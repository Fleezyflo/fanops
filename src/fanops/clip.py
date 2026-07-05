"""Render a Moment into platform-ready clips. Frame-accurate ffmpeg cut: -ss BEFORE -i
(fast seek) + -to AFTER -i (output-relative, version-stable — the v1 bug had -to before -i).
Reframe is chosen from the PROBED source dimensions so vertical/odd sources don't break.
render_aspects_for renders one clip per distinct aspect the active platforms need."""
from __future__ import annotations
import contextlib, hashlib, json, os, subprocess
from pathlib import Path
from statistics import median
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, MomentState, ClipState, Fmt
from fanops.ids import child_id
from fanops.bands import band_for, TALK
from fanops import overlay, frames, framing
from fanops.log import get_logger

# Target render size per aspect. The subtitle .ass PlayResX/Y must match the rendered frame so
# libass scales the caption to the clip — so render_moment reads this same table the reframe uses.
_TARGETS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}

# Hard bound on one ffmpeg render (the llm.py timeout idiom). render_moment runs INSIDE
# advance()'s ledger transaction, so an UNBOUNDED hang on a corrupt input held the flock against
# every other pass and Studio write. 10min covers a multi-minute 1080p re-encode with headroom.
_FFMPEG_TIMEOUT = 600.0

# A real clip is watchable, not a 3-4s fragment. The model is asked for in-band windows
# (prompts.moment_prompt); this is the render-time SAFETY NET that guarantees it even when a pick
# comes back short (or long). The default band is TALK (12-22s); render_moment passes the per-source
# band (band_for(cfg.clip_profile)) so a song gets the wider 18-35s band. Sources below the floor
# render whole. The subtitle overlay uses the SAME fitted window. Band lives in fanops.bands (one home).
_MIN_CLIP_S, _MAX_CLIP_S = TALK.lo, TALK.hi

def realized_clip_seconds(clip: Clip | None, moment) -> float | None:
    """Playable duration for platform-cap checks: rendered cut_seconds when set, else moment envelope."""
    if clip is None: return None
    if clip.cut_seconds is not None: return clip.cut_seconds
    if moment is not None: return moment.end - moment.start
    return None

# How far (seconds) snap_window may move a cut edge to land on a transcript-line boundary. A small
# nudge: it polishes mid-word starts / mid-phrase ends without overriding the band (fit_window's job).
_SNAP_MAX_SHIFT_S = 1.5

def _nearest(value: float, candidates: list[float], max_shift: float) -> float | None:
    in_range = [c for c in candidates if abs(c - value) <= max_shift]
    return min(in_range, key=lambda c: abs(c - value)) if in_range else None

def snap_window(start: float, end: float, transcript: list[dict] | None,
                *, duration: float = 0.0, max_shift: float = _SNAP_MAX_SHIFT_S) -> tuple[float, float]:
    """Nudge [start,end] onto nearby transcript-line boundaries so a clip never begins mid-word or
    ends mid-phrase: start -> nearest line `start`, end -> nearest line `end`, each only if within
    `max_shift` seconds (else that edge is left as-is). Returns the window UNCHANGED when there is no
    transcript, or when snapping would invert/empty it (snapped start >= snapped end). Pure; applied
    AFTER fit_window so the band is enforced first, then the edges land on clean cuts. Lines missing
    a numeric start/end are skipped (semi-trusted whisper output). Re-applies fit_window's bounds
    invariants the snap could break — a whisper line `start` can be slightly negative and a line `end`
    can overshoot the real EOF — so the snapped start is floored at 0 and the end is clamped to
    `duration` when probed (duration<=0 means unprobed -> no EOF clamp)."""
    if not transcript:
        return start, end
    starts = [ln["start"] for ln in transcript if isinstance(ln.get("start"), (int, float))]
    ends = [ln["end"] for ln in transcript if isinstance(ln.get("end"), (int, float))]
    ns = _nearest(start, starts, max_shift); ne = _nearest(end, ends, max_shift)
    s = max(0.0, ns if ns is not None else start)
    e = ne if ne is not None else end
    if duration and e > duration: e = duration
    return (s, e) if s < e else (start, end)

def fit_window(start: float, end: float, duration: float,
               *, lo: float = _MIN_CLIP_S, hi: float = _MAX_CLIP_S) -> tuple[float, float]:
    """Fit a picked [start,end] to a lo..hi-second clip. In-band picks are returned unchanged. A
    short pick grows forward from `start` (borrowing lead-in only when it would overrun EOF); a long
    pick is trimmed to `hi` from `start`. A source shorter than `lo` yields the whole source. The
    start is floored at 0; the end is EOF-clamped to `duration` when probed (duration<=0 means
    unprobed -> grow/trim without an EOF clamp)."""
    length = end - start
    if lo <= length <= hi:
        return start, end
    if duration and duration <= lo:
        return 0.0, duration
    target = lo if length < lo else hi
    s, e = start, start + target
    if duration and e > duration:
        e = duration; s = e - target
    return max(0.0, s), e

# P1 T1 (strongest-frame cut start). How far the entry may shift to land on a stronger frame (a small
# nudge, like snap_window's max_shift — never overrides the band), how many candidate frames to probe,
# the per-frame probe bound (keyframes.py idiom), and the minimum move to count as a real visual pick.
_VSTART_MAX_SHIFT_S = 1.5
_VSTART_CANDIDATES = 5
_VSTART_PROBE_TIMEOUT = 30.0
_VSTART_MIN_MOVE_S = 0.05
# vstart sidecar schema version (C2/H2): Theme 3 added sharpness to the pick, so the cached DECISION
# can change. A pre-sharpness sidecar (no/lower `v`) is a cache miss -> re-probe, never served stale.
_VSTART_V = 2
_SCENE_NEAR_S = 0.3          # a scene-cut peak within this of a candidate counts as "on a cut" (tiebreak)

def _vstart_candidate_times(start: float, end: float) -> list[float]:
    """Evenly-spaced candidate entry times in [start, min(start+shift, end)], INCLUDING `start` itself
    (so 'no better frame than the current start' is always reachable -> no spurious move). Pure."""
    hi = min(start + _VSTART_MAX_SHIFT_S, end)
    if hi <= start:
        return [start]
    n = _VSTART_CANDIDATES
    return [start + (hi - start) * i / (n - 1) for i in range(n)]

def _signalstats_cmd(src: str, t: float) -> list[str]:
    # One bounded ffmpeg per candidate: seek, decode ONE frame, print its luma stats (YAVG/YMIN/YMAX)
    # via the signalstats+metadata filter. `-f null -` discards output (no jpg written) — we only parse
    # the printed text. info loglevel makes metadata=print emit the lavfi.signalstats.* lines.
    return ["ffmpeg", "-hide_banner", "-loglevel", "info", "-ss", f"{t:.3f}", "-i", src,
            "-frames:v", "1", "-vf", "signalstats,metadata=print", "-f", "null", "-"]

def _sharpness_cmd(src: str, t: float) -> list[str]:
    # Theme 3: a SECOND tiny pass for a relative sharpness proxy — the discrete Laplacian convolution
    # (`0 -1 0 / -1 4 -1 / 0 -1 0`) on a gray frame, then signalstats YAVG = mean edge energy. ffmpeg-only
    # (zero new dep). NB this is mean-of-Laplacian (relative, in-clip ranking), NOT variance-of-Laplacian.
    return ["ffmpeg", "-hide_banner", "-loglevel", "info", "-ss", f"{t:.3f}", "-i", src,
            "-frames:v", "1", "-vf", "format=gray,convolution=0 -1 0 -1 4 -1 0 -1 0,signalstats,metadata=print",
            "-f", "null", "-"]

def _probe_frame_sharpness(src: str, t: float):
    """Run the Laplacian sharpness probe for ONE time and return the edge-energy proxy or None. FAIL-OPEN
    (any ffmpeg/parse failure -> None): sharpness is an ENHANCEMENT, so it degrades to contrast-only."""
    try:
        r = subprocess.run(_sharpness_cmd(src, t), check=False, capture_output=True, text=True,
                           timeout=_VSTART_PROBE_TIMEOUT)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    return frames.parse_sharpness((getattr(r, "stdout", "") or "") + (getattr(r, "stderr", "") or ""))

def _probe_frame_strength(src: str, t: float):
    """Probe ONE candidate time -> (luma, contrast, sharpness) or None. luma/contrast from signalstats;
    sharpness from a second Laplacian pass (fail-open to None -> contrast-only ranking). Fail-open
    (ffmpeg absent/hung/error -> None) exactly like keyframes.extract_keyframes — never raises."""
    try:
        r = subprocess.run(_signalstats_cmd(src, t), check=False, capture_output=True, text=True,
                           timeout=_VSTART_PROBE_TIMEOUT)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    # getattr-defensive: the probe is fail-open, so a result missing stdout/stderr -> no stats -> None
    # (a real capture_output run always has both as strings; this also tolerates minimal test fakes).
    lc = frames.parse_signalstats((getattr(r, "stdout", "") or "") + (getattr(r, "stderr", "") or ""))
    if lc is None:
        return None
    return lc[0], lc[1], _probe_frame_sharpness(src, t)      # sharpness fail-open -> None (contrast-only)

def _scene_score_near(scene_peaks, t: float) -> float:
    # signal_peaks is loaded from an unvalidated JSON sidecar, so a non-numeric t/score must not raise
    # out of the picker (fail-open contract) — a bad peak just contributes no tiebreak.
    best = 0.0
    for p in scene_peaks or []:
        if not isinstance(p, dict) or p.get("kind") != "scene_cut":
            continue
        try:
            pt = float(p.get("t", 0.0)); ps = float(p.get("score", 0.0))
        except (ValueError, TypeError):
            continue
        if abs(pt - t) <= _SCENE_NEAR_S:
            best = max(best, ps)
    return best

def pick_visual_start(src_path: str, start: float, end: float, *, scene_peaks, out_dir) -> tuple[float, str]:
    """Refine the cut entry onto the strongest opening frame within a bounded shift. Returns
    (new_start, kind): kind="visual" when a stronger frame moved the start, else "transcript" (the
    band/snap start is kept). The decision is CACHED in a per-(source,window) sidecar so the in-lock
    commit pass adopts it with NO ffmpeg (Phase D); the lock-free pre-warm pays the probe cost once.
    Fail-open: any probe failure leaves the start unchanged. PURE selection lives in frames.py."""
    out = Path(out_dir)
    key = hashlib.sha256(f"{src_path}|{round(start, 3)}|{round(end, 3)}".encode()).hexdigest()[:16]
    sidecar = out / f"vstart_{key}.json"
    if sidecar.exists():
        try:
            d = json.loads(sidecar.read_text())
            if d.get("v") != _VSTART_V:                    # C2/H2: stale (pre-sharpness) sidecar -> cache miss, re-probe
                raise KeyError("stale sidecar version")
            return float(d["start"]), str(d["kind"])      # cached -> no re-probe (commit stays lock-cheap)
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError):
            pass                                            # corrupt/stale sidecar -> fall through to a real probe
    cands = []
    for t in _vstart_candidate_times(start, end):
        ls = _probe_frame_strength(src_path, t)
        if ls is not None:
            cands.append({"t": t, "luma": ls[0], "contrast": ls[1], "sharpness": ls[2],
                          "scene": _scene_score_near(scene_peaks, t)})
    win = frames.pick_strongest(cands)
    if win is not None and abs(win["t"] - start) > _VSTART_MIN_MOVE_S:
        new_start, kind = float(win["t"]), "visual"
    else:
        new_start, kind = start, "transcript"
    try:
        out.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({"v": _VSTART_V, "start": new_start, "kind": kind}))
    except OSError:
        pass                                                # write failure just re-probes next time
    return new_start, kind

def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else (hi if v > hi else v)

# ---- Dynamic-reframe geometry (T5): zoom each subject to a consistent on-screen face fraction + place the
# eye-line, and pan SMOOTHLY (linear ramp) between active speakers — vs the old full-height no-zoom hard cut.
# ffmpeg evaluates crop x/y per-frame but w/h ONCE, so the crop box is constant per window (one zoom) and the
# pan lives in the x/y t-expression. A focus WITHOUT a face height (a 2-tuple, or saliency) never zooms ->
# byte-identical to the pre-zoom behaviour. ----
_FACE_FRAC_TALK = 0.42      # target on-screen face-box height for talk: a DELIBERATE head-and-shoulders short-form
                            # frame (the old 0.32 read as timid — output never left ~0.27). Bounded by _ZOOM_MAX.
_FACE_FRAC_MUSIC = 0.26     # ... for music/performance: wider, keeps stage/body context (still tighter than the old 0.22)
_EYELINE_FRAC = 0.40        # place the eyes at ~0.40 of the output height (eyes on the upper third)
_ZOOM_MAX = 1.6             # max zoom MAGNIFICATION for the STATIC single-subject crop (bounds upscale blur)
_ZOOM_MAX_TRACK = 1.7       # per-shot cap for a 2-shot NEAR speaker — sharp beats a big-but-blurry 2.4x upscale of
                            # a 1080p crop; the far speaker is held wide separately via _adaptive_zoom_max
_GENTLE_MIN_FACE_FRAC = 0.12   # an already-9:16 clip only gets a gentle zoom when the face is smaller than this
_GENTLE_ZOOM_MAX = 1.15        # ... and that gentle zoom never exceeds this magnification

def _target_frac(content_type: str | None) -> float:
    return _FACE_FRAC_MUSIC if content_type == "music" else _FACE_FRAC_TALK

def _zoom_h(src_h: int, ch0: int, fh, frac: float, zoom_max: float = _ZOOM_MAX) -> int:
    """Crop extent in the SCALED axis: shrink the baseline ch0 so a face of normalized height fh fills `frac`
    of the output, bounded so magnification (ch0/ch) never exceeds zoom_max (caps upscale blur). fh falsy
    (a 2-tuple focus / saliency) -> no zoom -> ch0 (today's full extent)."""
    if not fh or fh <= 0 or not frac:
        return ch0
    ch = round(src_h * fh / frac)
    return _clamp(ch, round(ch0 / zoom_max), ch0)

def _place(src_w: int, src_h: int, cw: int, ch: int, fx: float, ay: float, eyeline: float):
    """Clamped crop ORIGIN (x,y): x centres the box on fx; y puts the vertical anchor ay (eye-line or
    centroid) at `eyeline` of the crop. Both clamped so the window never runs off the frame."""
    x = _clamp(round(fx * src_w - cw / 2), 0, max(0, src_w - cw))
    y = _clamp(round(ay * src_h - eyeline * ch), 0, max(0, src_h - ch))
    return x, y

def _step_expr(bounds: list[float], vals: list[int]) -> str:
    """A per-frame ffmpeg crop-offset expression that HARD-CUTS through `vals` at the `bounds` switch times
    (instant reframe to the active speaker — the short-form standard, vs panning across the dead space
    between two seats). `vals` has one more entry than `bounds` (the final value is the else branch). Commas
    inside if() are escaped (\\,) so it survives filtergraph parsing as one option value. Single value -> the
    constant. A cut between distant speakers reads as energetic editing; a slow pan across the gap reads as a
    glitch (it shows the empty middle) — proven on real 2-shot footage."""
    if len(vals) <= 1:
        return str(vals[0]) if vals else "0"
    expr = str(vals[-1])
    for b, v in zip(reversed(bounds), reversed(vals[:-1])):
        expr = f"if(lt(t\\,{round(b, 2)})\\,{v}\\,{expr})"
    return expr

def _track_crop(track: list, src_w: int, src_h: int, tw: int, th: int, ch0: int, frac: float, *, axis: str) -> str:
    """Active-speaker crop: ONE zoom for the window (from the segments' median face height) + a SMOOTH pan
    of the crop origin between per-segment anchors. crop w/h constant (ffmpeg evals them once); x/y are the
    t-expressions. `axis` is just documentation — both x and y are emitted; a constant axis collapses to an int."""
    fhs = [s[4] for s in track if len(s) > 4 and s[4]]
    ch = _zoom_h(src_h, ch0, median(fhs) if fhs else None, frac)
    cw = min(round(ch * tw / th), src_w); ch = min(ch, src_h)
    bounds = [round(s[1], 2) for s in track[:-1]]
    xs, ys = [], []
    for s in track:
        ey = s[5] if len(s) > 5 else s[3]
        x, y = _place(src_w, src_h, cw, ch, s[2], ey, _EYELINE_FRAC if len(s) > 5 else 0.5)
        xs.append(x); ys.append(y)
    xexpr = _step_expr(bounds, xs)
    yexpr = _step_expr(bounds, ys)
    return f"crop=w={cw}:h={ch}:x={xexpr}:y={yexpr},scale={tw}:{th},setsar=1"

def _focus_crop(focus: tuple, src_w: int, src_h: int, tw: int, th: int, ch0: int, frac: float,
                *, symbolic_w: str, symbolic_full: bool) -> str:
    """Static subject-lock crop: zoom to the target face fraction + eye-line. When a 2-tuple focus produces
    NO zoom (full baseline extent), emit the legacy SYMBOLIC form so a pre-zoom focus clip is byte-identical
    (no needless re-render); otherwise a numeric zoomed crop."""
    fh = focus[2] if len(focus) > 2 else None
    ey = focus[3] if len(focus) > 3 else None
    ch = _zoom_h(src_h, ch0, fh, frac, zoom_max=_adaptive_zoom_max(fh, _ZOOM_MAX))
    cw = min(round(ch * tw / th), src_w); ch = min(ch, src_h)
    eyeline = _EYELINE_FRAC if ey is not None else 0.5
    x, y = _place(src_w, src_h, cw, ch, focus[0], ey if ey is not None else focus[1], eyeline)
    if symbolic_full and ch == ch0 and cw == round(ch0 * tw / th):
        # no zoom -> keep the exact pre-zoom string (byte-identical): width-crop "ih*tw/th:ih:x:y", height-crop "iw:iw*th/tw:x:y"
        return symbolic_w.format(x=x, y=y) + f",scale={tw}:{th},setsar=1"
    return f"crop={cw}:{ch}:{x}:{y},scale={tw}:{th},setsar=1"

def reframe_filter(aspect: str, src_w: int, src_h: int, *, top_bias: bool = False,
                   focus: tuple | None = None, track: list | None = None,
                   content_type: str | None = None) -> str:
    """A safe ffmpeg -vf for the target aspect given the source dims, content-adaptive and aspect-adaptive.
    `focus` ((fx,fy) or (fx,fy,fh,ey)) locks + zooms a static subject; `track` (6-tuples with face height +
    eye-line) follows the active speaker with a smooth pan; `content_type` tunes the zoom (music wider). A
    focus with no face height never zooms -> byte-identical to before; focus=None AND track=None AND
    top_bias=False is the exact centered crop of old. Every branch clamps in-bounds and falls open safely."""
    tw, th = _TARGETS[aspect]
    if not src_w or not src_h:
        # unknown source: scale to fit + pad to exact target (never an impossible crop)
        return (f"scale={tw}:{th}:force_original_aspect_ratio=decrease,"
                f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2,setsar=1")
    src_ar = src_w / src_h
    tgt_ar = tw / th
    frac = _target_frac(content_type)
    if abs(src_ar - tgt_ar) < 0.01:
        return _already_aspect(tw, th, src_w, src_h, focus, frac)   # passthrough or a bounded gentle zoom
    if src_ar > tgt_ar:
        # source wider than target -> crop width (full height kept). track/focus zoom + slide onto the subject.
        ch0 = src_h
        if track:
            return _track_crop(track, src_w, src_h, tw, th, ch0, frac, axis="x")
        if focus is not None:
            return _focus_crop(focus, src_w, src_h, tw, th, ch0, frac,
                               symbolic_w=f"crop=ih*{tw}/{th}:ih:{{x}}:{{y}}", symbolic_full=True)
        return f"crop=ih*{tw}/{th}:ih,scale={tw}:{th},setsar=1"
    # source taller/narrower than target -> crop height.
    ch0 = round(src_w * th / tw)
    if track:
        return _track_crop(track, src_w, src_h, tw, th, ch0, frac, axis="y")
    if focus is not None:
        return _focus_crop(focus, src_w, src_h, tw, th, ch0, frac,
                           symbolic_w=f"crop=iw:iw*{th}/{tw}:{{x}}:{{y}}", symbolic_full=True)
    if top_bias:
        return f"crop=iw:iw*{th}/{tw}:0:(ih-iw*{th}/{tw})/4,scale={tw}:{th},setsar=1"
    return f"crop=iw:iw*{th}/{tw},scale={tw}:{th},setsar=1"

def _already_aspect(tw: int, th: int, src_w: int, src_h: int, focus: tuple | None, frac: float) -> str:
    """Source ALREADY at the target aspect: scale-only by default (byte-identical to today). ONLY when a
    small face is detected (fh < _GENTLE_MIN_FACE_FRAC) apply a BOUNDED gentle zoom-in (still target AR) with
    eye-line recentre — never a destructive crop, never worse than passthrough."""
    fh = focus[2] if (focus is not None and len(focus) > 2) else None
    if not fh or fh >= _GENTLE_MIN_FACE_FRAC:
        return f"scale={tw}:{th},setsar=1"
    ch = _zoom_h(src_h, src_h, fh, frac, zoom_max=_GENTLE_ZOOM_MAX)
    cw = min(round(ch * tw / th), src_w); ch = min(ch, src_h)
    ey = focus[3] if len(focus) > 3 else focus[1]
    x, y = _place(src_w, src_h, cw, ch, focus[0], ey, _EYELINE_FRAC)
    return f"crop={cw}:{ch}:{x}:{y},scale={tw}:{th},setsar=1"

def ffmpeg_clip_cmd(src: str, dst: str, start: float, end: float, aspect: str,
                    *, src_w: int = 0, src_h: int = 0, extra_vf: str | None = None,
                    top_bias: bool = False, focus: tuple | None = None,
                    track: list | None = None, content_type: str | None = None) -> list[str]:
    # -ss before -i (fast seek) makes output-position -to a DURATION measured from the seek
    # point, so it must be (end - start), not the absolute end. Verified on ffmpeg 8.0.1:
    # `-ss 1.5 -to 6.5` yields a 6.5s clip; passing 8.0 here would yield 8.0s (the F39 bug).
    # extra_vf (e.g. the burned-subtitles `subtitles=...` token) is chained AFTER the reframe
    # with a comma so it operates on the already-reframed frame; default None == old behavior.
    vf = reframe_filter(aspect, src_w, src_h, top_bias=top_bias, focus=focus, track=track, content_type=content_type)
    if extra_vf:
        vf = f"{vf},{extra_vf}"
    return ["ffmpeg", "-y", "-ss", str(start), "-i", src, "-to", str(end - start),
            "-vf", vf,
            "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", dst]

# ---- Per-segment active-speaker render (the fix for "random sizes" in 2-shots) ----
# A single ffmpeg `crop` evaluates w/h ONCE per stream, so a 2-shot got ONE zoom for two speakers whose source
# face sizes differ >2x -> one of them was always wrong-sized (the operator's "cutting to random sizes"). The
# fix renders each active-speaker SEGMENT as its OWN correctly-sized crop and joins them with the concat filter
# in a SINGLE pass: N seeked inputs -> per-segment crop chains -> concat (sample-accurate, no container seams)
# -> optional subtitle burn -> one encode. Each speaker now lands at a consistent on-screen face size.
def _crop_box(fx: float, fy: float, fh, ey, src_w: int, src_h: int, tw: int, th: int,
              ch0: int, frac: float, zoom_max: float):
    """Numeric crop (cw, ch, x, y) that zooms a subject of normalized face-height fh to `frac` of the output
    (bounded by zoom_max) and anchors its eye-line ey at _EYELINE_FRAC. Shared sizing math so the static focus
    crop and the per-segment active-speaker crops are consistent. fh falsy -> no zoom (full ch0 extent).
    The zoom cap is face-size-adaptive: a far/small subject is held wide (context, not a tight mic crop)."""
    ch = _zoom_h(src_h, ch0, fh, frac, zoom_max=_adaptive_zoom_max(fh, zoom_max))
    cw = min(round(ch * tw / th), src_w); ch = min(ch, src_h)
    eyeline = _EYELINE_FRAC if (ey is not None and fh) else 0.5
    anchor = ey if (ey is not None and fh) else fy
    x, y = _place(src_w, src_h, cw, ch, fx, anchor, eyeline)
    return cw, ch, x, y

def _ch0_for(aspect_value: str, src_w: int, src_h: int):
    """Baseline crop extent in the scaled axis for source->target: full height for a wider source, full-width-
    derived height for a taller one. None when the source is ALREADY the target aspect (segment -> scale-only)."""
    tw, th = _TARGETS[aspect_value]
    if not src_w or not src_h:
        return None
    src_ar = src_w / src_h; tgt_ar = tw / th
    if abs(src_ar - tgt_ar) < 0.01:
        return None
    return src_h if src_ar > tgt_ar else round(src_w * th / tw)

def _segment_chain(idx: int, seg, src_w: int, src_h: int, tw: int, th: int, ch0, frac: float) -> str:
    """One concat input's video chain: crop the active speaker (this segment's own fx,fy,fh,ey -> own zoom +
    eye-line) then scale to the target, labeled [v{idx}]. ch0 None (already-aspect / unknown dims) -> scale-only."""
    if ch0 is None:
        return f"[{idx}:v]scale={tw}:{th},setsar=1[v{idx}]"
    fh = seg[4] if len(seg) > 4 else None
    ey = seg[5] if len(seg) > 5 else None
    cw, ch, x, y = _crop_box(seg[2], seg[3], fh, ey, src_w, src_h, tw, th, ch0, frac, _ZOOM_MAX_TRACK)
    return f"[{idx}:v]crop={cw}:{ch}:{x}:{y},scale={tw}:{th},setsar=1[v{idx}]"

def _segments_filter_complex(track: list, src_w: int, src_h: int, aspect_value: str,
                             content_type: str | None, *, sub_token: str | None = None) -> str:
    """The full -filter_complex: each segment's crop chain; a concat filter joining all (video+audio) in order;
    then the optional subtitle burn -> [vout],[aout]. The .ass timeline (0..clip-dur) aligns because concat
    rebuilds a continuous 0-based timeline from the contiguous segments."""
    tw, th = _TARGETS[aspect_value]
    frac = _target_frac(content_type)
    ch0 = _ch0_for(aspect_value, src_w, src_h)
    chains = [_segment_chain(i, seg, src_w, src_h, tw, th, ch0, frac) for i, seg in enumerate(track)]
    concat_in = "".join(f"[v{i}][{i}:a]" for i in range(len(track)))
    vlabel = "[vc]" if sub_token else "[vout]"
    parts = chains + [f"{concat_in}concat=n={len(track)}:v=1:a=1{vlabel}[aout]"]
    if sub_token:
        parts.append(f"[vc]{sub_token}[vout]")
    return ";".join(parts)

def ffmpeg_segments_cmd(src: str, dst: str, cs: float, ce: float, aspect_value: str, track: list,
                        *, src_w: int, src_h: int, content_type: str | None = None,
                        sub_token: str | None = None) -> list[str]:
    """ffmpeg command for the per-segment concat render: one seeked input per segment (`-ss`/`-t` before each
    `-i` = fast + accurate), a single -filter_complex (per-segment crop -> concat -> subtitles), one encode.
    Segment times are RELATIVE to the clip; each input window is (cs+t0) for (t1-t0) seconds."""
    cmd = ["ffmpeg", "-y"]
    for seg in track:
        seg_cs = cs + float(seg[0]); seg_dur = float(seg[1]) - float(seg[0])
        cmd += ["-ss", f"{seg_cs:.3f}", "-t", f"{seg_dur:.3f}", "-i", src]
    fc = _segments_filter_complex(track, src_w, src_h, aspect_value, content_type, sub_token=sub_token)
    cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", dst]
    return cmd

# ---- Stable render strategy: a LOCKED-OFF camera per shot (no per-frame motion = no jitter) ----
# A per-frame crop that CHASES the subject reads as a jittery hand-held cam — it tracks every detection wobble
# and the zoom "breathes" with per-frame face-height noise (the operator's "jittery cameraman" complaint).
# Seated podcast/interview footage wants the opposite: ONE fixed, correctly-sized crop held PERFECTLY STILL per
# shot, hard-cutting between speakers — a locked-off virtual camera, how real clippers cut it. So the render is a
# STATIC crop per active-speaker SEGMENT (the ffmpeg crop is constant within a segment -> zero camera motion),
# or a single static subject-lock for a one-person clip. Per-shot sizing fixes the cross-speaker "random sizes";
# the cut timing is the responsive ASD track. No per-frame motion = no jitter, by construction.
_SMALL_FACE_FRAC = 0.18      # below this source face height the subject is FAR (often profile + mic-occluded) — a
                             # tight punch-in just frames the foreground mic, so cap the zoom hard and show context
_ZOOM_MAX_FAR = 1.25         # the far-subject zoom cap: a wide, contextual shot (punch in on a near subject, hold
                             # wide on the far/turned one) — never a tight crop of an occlusion

def _adaptive_zoom_max(fh, base: float) -> float:
    """Face-size-adaptive zoom cap: a FAR/small subject (fh < _SMALL_FACE_FRAC, typically profile + mic-occluded)
    is held WIDE (_ZOOM_MAX_FAR) so its crop shows context, not a tight frame of the foreground mic; a near/well-
    sized subject keeps the `base` cap (punch-in). fh falsy -> base (no zoom applies anyway)."""
    return _ZOOM_MAX_FAR if (fh and 0 < fh < _SMALL_FACE_FRAC) else base

def render_reframed(src_path: str, dst: str, cs: float, ce: float, aspect_value: str, *,
                    src_w: int, src_h: int, extra_vf: str | None = None, top_bias: bool = False,
                    focus: tuple | None = None, track: list | None = None,
                    content_type: str | None = None, timeout: float = _FFMPEG_TIMEOUT):
    """Render the reframed clip to `dst` as a STABLE shot (no per-frame camera motion), fail-open ladder:
      1. segment-concat (a real 2-shot track) — each speaker its OWN static, correctly-sized crop, hard cuts;
      2. single-pass ffmpeg crop (single subject / centered) — one static subject-lock (or centered) crop.
    Both are LOCKED-OFF per shot (the crop is constant within a segment) so there is zero jitter. Returns the
    subprocess result for the caller's existing handling; FileNotFoundError/OSError/TimeoutExpired propagate
    exactly like the single-pass `subprocess.run` they replace.

    ATOMIC WRITE (MOL-78): ffmpeg renders to a `<dst>.part.mp4` temp SIBLING of `dst`, and the finished
    output is `os.replace`d onto `dst` ONLY after success (rc==0 + temp exists + size>0). So `dst` is never
    a torn file mid-write — a concurrent reader (preview fallback, ffprobe, upload) sees the OLD `dst` or
    nothing, never a half-muxed byte stream; on failure/timeout `dst` is left untouched (absent, or its
    prior good file), so the caller's rc/exists/size checks on `dst` still hold verbatim (an unreplaced
    failure leaves `dst` missing, matching the existing `not dst.exists()` error branch). The temp MUST keep
    a muxer-inferable `.mp4` suffix: `ffmpeg_clip_cmd`/`ffmpeg_segments_cmd` pass no `-f mp4`, so ffmpeg
    picks the container from the OUTPUT EXTENSION alone — a bare `.part` temp fails "Error initializing the
    muxer" and produces NO file (rc!=0), so os.replace would never run and `dst` would never be created
    (the MOL-78 CI E2E failure; the unit tests missed it because they stubbed ffmpeg). This also heals
    render_account_cut, which passes its OWN `<out>.part` as `dst` here: we render to `<dst>.part.mp4`
    (muxes fine) and publish to `dst` whatever ITS extension. The temp is swept on EVERY exit path —
    success, fail-through, or a raised exception — in the finally. Mirrors render_account_cut's proven
    atomic+os.replace pattern in this same file (and overlay.burn_hook_only)."""
    tmp = str(dst) + ".part.mp4"                              # keep a muxer-inferable .mp4 suffix (see ATOMIC WRITE)
    try:
        if track and len(track) > 1:
            seg_cmd = ffmpeg_segments_cmd(src_path, tmp, cs, ce, aspect_value, track,
                                          src_w=src_w, src_h=src_h, content_type=content_type, sub_token=extra_vf)
            r = subprocess.run(seg_cmd, check=False, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0 and Path(tmp).exists() and Path(tmp).stat().st_size > 0:
                os.replace(tmp, dst)                          # atomic publish — never a half-written clip at dst
                return r
            # a working ffmpeg rejected the segment graph -> fall through to the single-pass crop (fail-open);
            # the .part is overwritten by the single-pass output below, and swept in finally on any failure.
        cmd = ffmpeg_clip_cmd(src_path, tmp, cs, ce, aspect_value, src_w=src_w, src_h=src_h, extra_vf=extra_vf,
                              top_bias=top_bias, focus=focus, track=track, content_type=content_type)
        r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0 and Path(tmp).exists() and Path(tmp).stat().st_size > 0:
            os.replace(tmp, dst)                              # atomic publish (single-pass)
        # rc!=0 / empty / missing temp -> leave dst UNTOUCHED; the caller's rc+exists+size checks on dst fire.
        return r
    finally:
        # sweep the .part on EVERY exit path (success os.replace consumes it; failure/timeout/exception leave
        # it). suppress(OSError) so a sweep hiccup never MASKS a propagating TimeoutExpired/OSError from above.
        with contextlib.suppress(OSError):
            os.unlink(tmp)

def _subtitles_vf(led: Ledger, cfg: Config, moment_id: str, cid: str, aspect: Fmt,
                  *, clip_start: float, clip_end: float):
    """Build the burned-on-screen-text `-vf` fragment for this clip, or return None (reframe only).
    FAIL-OPEN by contract: a clip is NEVER blocked on its text. Two independent layers:
      • the RETENTION HOOK (m.hook) — the default on-screen text, a curiosity-gap line that drives
        watch-through (NOT a transcript). Burned whenever the moment has a hook. SUPPRESSED here when
        creative_variation is on: the per-account burn_hook_only pass burns a per-surface hook, and
        burning the moment hook too would STACK two hooks on one clip.
      • the TRANSCRIPT captions — OPT-IN via burn_subs (default OFF). Showing what the audio says is
        redundant (the viewer hears it) and only as good as the auto-transcription; useful for
        talking-head content, wrong for music — so it ships only when the operator asks.
    Returns (vf_fragment_or_None, hook_burn_failed). hook_burn_failed is True when on-screen text WAS
    wanted (a hook, or opted-in transcript) but could NOT be burned — ffmpeg lacks the text filter, or
    build_ass yielded empty — so render_moment flags the clip (F9) instead of shipping a fine-looking
    clip that silently lost its text. False when there was nothing to burn (clean clip) or it burned."""
    m = led.moments[moment_id]
    src = led.sources[m.parent_id]
    hook = None if cfg.creative_variation else ((m.hook or "").strip() or None)  # per-surface hook owns it under variation; blank -> None
    # Subtitle burn is opt-in via the GLOBAL cfg.burn_subs, with a PER-BATCH override (Batch.burn_subs): a music
    # batch can skip lyric subs (burn_subs=False) while talk stays on, or vice-versa. None override -> global.
    batch = led.get_batch(src.batch_id) if getattr(src, "batch_id", None) else None
    burn = batch.burn_subs if (batch is not None and batch.burn_subs is not None) else cfg.burn_subs
    segments = (src.transcript or []) if burn else []   # transcript is opt-in (global default, per-batch override)
    if not hook and not segments:                        # no hook, no opted-in transcript -> clean clip
        return None, False                               # nothing wanted -> not a failure
    if not overlay.ffmpeg_has_textfilter():
        # Text was asked for but the toolchain can't burn it. Don't block the clip — log once and
        # render plain. (One line per clip; ffmpeg_has_textfilter caches, so the probe runs once.)
        get_logger(cfg)("clip", cid, "subs_skipped",
                        reason="ffmpeg lacks the text filter — rendering without subtitles/hook")
        return None, True                                # WANTED but the toolchain can't burn it -> F9 flag
    tw, th = _TARGETS[aspect.value]
    if hook:                                             # P1 T2: fail-open legibility guard — warn once, never block
        warns = overlay.hook_legibility_warnings(hook, width=tw, height=th)
        if warns:
            get_logger(cfg)("clip", cid, "hook_legibility", warning="; ".join(warns))
    ass_text = overlay.build_ass(segments, hook=hook, clip_start=clip_start, clip_end=clip_end,
                                 width=tw, height=th, font=cfg.subtitle_font)
    if not ass_text or not ass_text.strip():
        return None, True                                # WANTED but produced no burnable text -> F9 flag
    ass_path = cfg.clips / f"{cid}.ass"
    overlay.write_ass(ass_text, ass_path)
    return overlay.subtitles_vf(ass_path), False

# Phase D: the clip's content-address (child_id of moment+aspect) does NOT include the burned hook or
# the cut window, so an mp4 on disk is NOT proof it matches the INTENDED render — a changed hook would
# leave a stale clip (the stale-render class of bug). The render fingerprint captures everything that
# determines the rendered bytes (source, window, aspect, source dims, the burned .ass text), so the
# lock-free pre-warm and the in-lock commit agree on when an existing mp4 may be reused. This is what
# lets the heavy ffmpeg run OUTSIDE the ledger lock and the commit pass skip it.
_REFRAME_GEOM_V = 4          # bump to force re-render of ZOOM/eyeline/dynamic clips after a geometry-math change
                             # (v4: STATIC locked-off crop per shot — no jitter; adaptive far-speaker zoom + min-shot merge)

def _render_fingerprint(src_path: str, cs: float, ce: float, aspect_value: str,
                        src_w: int, src_h: int, ass_text: str, *, top_bias: bool = False,
                        focus: tuple | None = None, track: list | None = None,
                        content_type: str | None = None) -> str:
    payload = {"src": src_path, "cs": round(cs, 3), "ce": round(ce, 3), "aspect": aspect_value,
               "w": src_w, "h": src_h, "ass": ass_text}
    if top_bias:                                          # additive: absent key -> byte-identical fp to today
        payload["top_bias"] = True
    if focus is not None:                                 # ALL elements: a 2-tuple hashes [fx,fy] (== old);
        payload["focus"] = [round(v, 3) for v in focus]  # a 4-tuple adds fh,ey -> zoom changes bytes -> re-render
    if track:                                             # full 6-tuple (fh,ey carried) -> dynamic crop re-renders
        payload["track"] = [[round(s[0], 2), round(s[1], 2)] + [round(v, 3) for v in s[2:]] for s in track]
    geom = bool(track) or (focus is not None and len(focus) > 2)   # zoom/eyeline/dynamic present?
    if content_type and geom:                            # content_type only alters bytes when a zoom applies
        payload["ct"] = content_type
    if geom:                                              # version the new geometry so a future change can bust it
        payload["geom"] = _REFRAME_GEOM_V
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()

def _resolve_framing(cfg: Config, src, cs: float, ce: float):
    """Pick the reframe strategy for this window: (focus, track, content_type). Classify the window once,
    then route — active-speaker TRACK only for real multi-speaker talk; subject-lock FOCUS (zoomed) for a
    single/music/silent subject; motion-SALIENCY (a no-zoom 2-tuple) for music/silent/no-people with no face;
    else centered (None,None,None). Gated entirely by cfg.smart_framing so OFF is byte-identical to today.
    Every framing call is fail-open (None), so any miss degrades to the centered crop."""
    if not cfg.smart_framing:
        return None, None, None
    stats = framing.detect_window(cfg, src, start=cs, end=ce)
    ct = framing.classify_window(cfg, src, start=cs, end=ce, stats=stats)
    if ct == framing.CT_MULTI:
        track = framing.speaker_track(cfg, src, start=cs, end=ce, src_w=src.width or 0, src_h=src.height or 0)
        if track:
            return None, track, ct
        ct = framing.CT_SINGLE                            # classed multi but not a real 2-shot -> single lock
    if ct in (framing.CT_SINGLE, framing.CT_MUSIC, framing.CT_SILENT):
        focus = framing.subject_focus(cfg, src, start=cs, end=ce)
        if focus is not None:
            return focus, None, ct
    if ct in (framing.CT_MUSIC, framing.CT_SILENT, framing.CT_NOPEOPLE):
        sal = framing.motion_saliency(cfg, src, start=cs, end=ce)   # follow the action when there's no face
        if sal is not None:
            return sal, None, None                       # 2-tuple -> pan only, NO zoom (no subject to size to)
    return None, None, None                              # centered crop (today)

def _fingerprint_matches(fp_path, fp: str) -> bool:
    try:
        return fp_path.exists() and json.loads(fp_path.read_text()).get("fp") == fp
    except (OSError, json.JSONDecodeError, ValueError):
        return False

# M4 (impact-cut): a stitched render's validity is DURATION-checked, not size-checked — a short/empty
# container that passes "size > 0" must still fail. Probe the rendered output's duration via ffprobe;
# None on any failure (the caller treats an unprobeable stitch as invalid -> error + bare-clip fallback).
# Module-level so tests can patch it without a real ffprobe (mirrors the subprocess.run patch pattern).
def _probe_duration(path: str) -> float | None:
    from fanops.ingest import probe_dimensions          # local: avoid an import cycle at module load
    from fanops.errors import ToolchainMissingError
    try:
        _, _, dur = probe_dimensions(Path(path))
        return dur or None
    except (ToolchainMissingError, OSError, ValueError):
        return None

def render_moment(led: Ledger, cfg: Config, moment_id: str, *,
                  aspect: Fmt = Fmt.r9x16, cut_window: tuple[float, float] | None = None,
                  clip_id: str | None = None, born_state: ClipState = ClipState.rendered) -> tuple[Ledger, Clip]:
    # M4 (impact-cut): when `cut_window` is given, render a STITCH — a new clip with the caller's distinct
    # `clip_id` (never the content-addressed bare cid, so it can't overwrite the bare clip — the supersede
    # rule), the peak-derived window verbatim (no band/snap/visual refine — the cut is already decided),
    # and `born_state` (stitch_draft, structurally unpostable). Its duration is validity-checked post-render.
    # The DEFAULT path (cut_window is None) is byte-identical to before. is_stitch guards every new branch.
    is_stitch = cut_window is not None
    m = led.moments[moment_id]
    src = led.sources[m.parent_id]
    cid = clip_id if is_stitch else child_id("clip", moment_id, aspect.value)  # content-addressed by aspect (bare)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    dst = cfg.clips / f"{cid}.mp4"
    first_frame_kind = None
    if is_stitch:
        cs, ce = float(cut_window[0]), float(cut_window[1])    # the impact-cut window, verbatim
    else:
        band = band_for(cfg.clip_profile)                          # talk 12-22s / song 18-35s
        cs, ce = fit_window(m.start, m.end, src.duration or 0.0, lo=band.lo, hi=band.hi)  # widen to a real clip
        cs, ce = snap_window(cs, ce, src.transcript, duration=src.duration or 0.0)  # land on clean phrase boundaries
        # P1 T1: refine the entry onto the strongest opening frame, applied LAST (after band + snap) so the
        # rendered cut and the first_frame_kind provenance AGREE — snap can't silently undo a visual pick and
        # leave the dim lying (it would poison P4, which ranks first_frame_kind). Both 1.5s shifts otherwise
        # overlap. Runs in the lock-free pre-warm + is sidecar-cached so the in-lock commit re-probes nothing.
        if cfg.visual_start:
            cs, first_frame_kind = pick_visual_start(src.source_path, cs, ce,
                                                     scene_peaks=src.signal_peaks, out_dir=cfg.clips)
    cut_seconds = round(ce - cs, 3)                            # P1 provenance (observational; length not varied)
    # Smart framing (default-on, fail-open): the subject's normalized centroid over THIS window slides the
    # crop onto the speaker/action instead of the blind top/center guess. None (no [framing] extra / no
    # detection) -> today's centered crop. Resolved here (window final) + cached, so the in-lock commit
    # re-probes nothing — and it feeds BOTH the fingerprint and the render so a fp-match can't reuse a stale crop.
    # Content-adaptive: classify the window (multi-speaker / single / music / silent / no-people) and route to
    # the right crop — active-speaker TRACK only for real talk 2-shots, subject-lock FOCUS (zoomed) for a
    # single/music/silent subject, motion SALIENCY for no-face music/silent, else centered. content_type tunes
    # the zoom (music wider). All fail-open -> centered. Resolved here (window final) + cached so the in-lock
    # commit re-probes nothing, and it feeds BOTH the fingerprint and the render (no stale-crop reuse).
    focus, track, content_type = _resolve_framing(cfg, src, cs, ce)
    extra_vf, hook_burn_failed = _subtitles_vf(led, cfg, moment_id, cid, aspect, clip_start=cs, clip_end=ce)
    # Phase D idempotent skip: if cid.mp4 already exists AND its fingerprint matches this exact intended
    # render (a pre-warm pass produced it), adopt it and SKIP ffmpeg — record the clip + advance the
    # moment. A changed hook/window yields a different fingerprint -> re-render (no stale clip reuse).
    ass_path = cfg.clips / f"{cid}.ass"
    ass_text = ass_path.read_text(encoding="utf-8") if (extra_vf and ass_path.exists()) else ""
    fp = _render_fingerprint(src.source_path, cs, ce, aspect.value, src.width or 0, src.height or 0,
                             ass_text, top_bias=cfg.aware_reframe, focus=focus, track=track, content_type=content_type)
    fp_path = cfg.clips / f"{cid}.render.json"
    if dst.exists() and dst.stat().st_size > 0 and _fingerprint_matches(fp_path, fp):
        # An fp-match means a prior render of THIS exact window already passed (the fp is stamped only
        # after a successful render + a passing duration check for stitches), so adopt it without re-probing.
        clip = Clip(id=cid, parent_id=moment_id, state=born_state, path=str(dst), aspect=aspect,
                    first_frame_kind=first_frame_kind, cut_seconds=cut_seconds,
                    hook_burn_failed=hook_burn_failed)
        led.clips[cid] = clip
        if not is_stitch:                                     # a stitch never advances the moment (the bare clip owns it)
            led.set_moment_state(moment_id, MomentState.clipped)
        return led, clip
    try:
        r = render_reframed(src.source_path, str(dst), cs, ce, aspect.value,
                            src_w=src.width or 0, src_h=src.height or 0, extra_vf=extra_vf,
                            top_bias=cfg.aware_reframe, focus=focus, track=track,
                            content_type=content_type, timeout=_FFMPEG_TIMEOUT)
    except (FileNotFoundError, OSError) as e:
        # ffmpeg ABSENT from PATH (or otherwise unspawnable): subprocess.run raises BEFORE the
        # process starts, so check=False (which only suppresses a nonzero RETURNCODE) does not
        # cover it. Treat it exactly like the nonzero-rc branch — record ClipState.error and
        # leave the moment at `decided` so a re-run retries when ffmpeg returns. Otherwise the
        # raise escapes to the pipeline's per-moment quarantine, parking the moment in the
        # TERMINAL MomentState.error (never re-rendered) — a transient PATH glitch would wedge
        # it permanently, contradicting this module's fail-safe philosophy.
        clip = Clip(id=cid, parent_id=moment_id, state=ClipState.error, path=str(dst),
                    aspect=aspect, error_reason=f"toolchain missing: ffmpeg ({type(e).__name__})")
        led.clips[cid] = clip
        return led, clip
    except subprocess.TimeoutExpired:
        # ffmpeg HUNG (corrupt input, stuck filesystem) and was killed at the bound. Same
        # fail-safe shape as the branches above/below: ClipState.error, moment stays `decided`
        # so a re-run retries — an unbounded hang here held the ledger flock forever.
        clip = Clip(id=cid, parent_id=moment_id, state=ClipState.error, path=str(dst),
                    aspect=aspect, error_reason=f"ffmpeg timed out after {_FFMPEG_TIMEOUT:.0f}s")
        led.clips[cid] = clip
        return led, clip
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        # ffmpeg RAN and failed OR produced a 0-byte output (truncated mux at rc=0): record the clip as
        # errored (a dangling/empty path would otherwise masquerade as 'rendered' and blow up later in
        # crosspost/media-upload). The st_size>0 guard mirrors the segment-concat (:447) + warm-skip (:619)
        # checks. Leave the moment un-clipped so a re-run retries. Mirrors transcribe.py's pattern.
        clip = Clip(id=cid, parent_id=moment_id, state=ClipState.error, path=str(dst),
                    aspect=aspect, error_reason=f"ffmpeg rc={r.returncode} out={dst.stat().st_size if dst.exists() else 'missing'}B: {(r.stderr or '')[:180]}")
        led.clips[cid] = clip
        return led, clip
    if is_stitch:
        # Output validity is DURATION-checked, not size-checked (PRD): a short/empty container that
        # passes "size > 0" must fail. expected = cut_end - cut_start; a render outside DURATION_TOLERANCE
        # is errored (bare clip already shipped upstream — fail-open + fail-visible), no skip-stamp so a
        # re-render retries. The moment is left alone (the bare clip owns its state).
        from fanops.impact_cut import DURATION_TOLERANCE
        expected = round(ce - cs, 3)
        actual = _probe_duration(str(dst))
        if actual is None or abs(actual - expected) > DURATION_TOLERANCE:
            clip = Clip(id=cid, parent_id=moment_id, state=ClipState.error, path=str(dst), aspect=aspect,
                        error_reason=f"duration {actual} vs {expected}")
            led.clips[cid] = clip
            return led, clip
    clip = Clip(id=cid, parent_id=moment_id, state=born_state, path=str(dst), aspect=aspect,
                first_frame_kind=first_frame_kind, cut_seconds=cut_seconds,
                hook_burn_failed=hook_burn_failed)
    # Overwrite any prior clip at this content-addressed id (e.g. a previous error-state
    # render) so a re-render self-heals; setdefault would pin the stale clip. id is unique
    # per (moment, aspect), so the latest successful render is authoritative.
    led.clips[cid] = clip
    if not is_stitch:                                         # a stitch never advances the moment (the bare clip owns it)
        led.set_moment_state(moment_id, MomentState.clipped)
    # Stamp the render fingerprint (Phase D) so a later pass — or the in-lock commit after a lock-free
    # pre-warm — can skip re-rendering an identical clip. Best-effort: a write failure just costs a
    # re-render, never a crash. Written ONLY on success, so a failed render never leaves a skip stamp.
    try:
        fp_path.write_text(json.dumps({"fp": fp}))
    except OSError:
        pass
    return led, clip

def render_aspects_for(led: Ledger, cfg: Config, moment_id: str, *,
                       aspects: set[Fmt]) -> tuple[Ledger, list[Clip]]:
    m = led.moments[moment_id]
    if m.state is MomentState.retired or led.is_retired_moment(moment_id):
        return led, []
    out: list[Clip] = []
    for asp in sorted(aspects, key=lambda a: a.value):
        led, clip = render_moment(led, cfg, moment_id, aspect=asp)
        out.append(clip)
    return led, out


def render_account_cut(led: Ledger, cfg: Config, moment_id: str, *, aspect: Fmt, profile: str,
                       hook: str, out_path: str, top_bias: bool = False) -> tuple[bool, float | None]:
    """M2: an override account's OWN per-account CUT. Cut the SOURCE at `profile`'s band (its own LENGTH —
    @short 8-15s, @long 28-45s off the SAME moment) and burn `hook` (top-third) in ONE ffmpeg pass, written
    ATOMICALLY to out_path. Returns (True, realized_seconds=ce-cs) on success, (False, None) FAIL-OPEN (any
    ffmpeg/parse failure) — the caller then falls back to burn_hook_only on the shared clip, so the
    Render.path file always exists (P3: the realized seconds is recorded on Render.cut_seconds). Unlike
    render_moment this writes to an ARBITRARY path with a SPECIFIC hook + band, mints NO Clip, and advances
    NO moment (the shared Clip owns the moment anchor — §4 of the per-account plan). Mirrors render_moment's
    window math (fit_window + snap + visual-start) so the per-account cut opens on the same strong frame the
    shared clip does. The hook .ass is 0-based (build_ass(clip_start=0) — the -ss output is 0-based)."""
    ass_path = None
    tmp = str(out_path) + ".part"
    try:
        m = led.moments[moment_id]
        src = led.sources[m.parent_id]
        band = band_for(profile)
        cs, ce = fit_window(m.start, m.end, src.duration or 0.0, lo=band.lo, hi=band.hi)   # the account's band
        cs, ce = snap_window(cs, ce, src.transcript, duration=src.duration or 0.0)
        if cfg.visual_start:                                  # same strong-frame entry the shared clip uses
            cs, _ = pick_visual_start(src.source_path, cs, ce, scene_peaks=src.signal_peaks, out_dir=cfg.clips)
        realized = ce - cs                                    # P3: the account cut's REALIZED window length (post snap+visual-start)
        focus, track, content_type = _resolve_framing(cfg, src, cs, ce)   # content-adaptive crop (fail-open -> centered)
        tw, th = _TARGETS[aspect.value]
        extra_vf = None
        if (hook or "").strip() and overlay.ffmpeg_has_textfilter():
            # hook-only .ass, 0-based over the cut output's first min(2.5, len) seconds (build_ass uses
            # clip_start/clip_end only for clip_len; the HOOK event is emitted at t=0 regardless).
            ass_text = overlay.build_ass([], hook=hook, clip_start=0.0, clip_end=ce - cs,
                                         width=tw, height=th, font=cfg.subtitle_font)
            if ass_text and ass_text.strip():
                ass_path = str(Path(out_path).with_suffix(".ass"))
                overlay.write_ass(ass_text, ass_path)
                extra_vf = overlay.subtitles_vf(ass_path)
        try:
            r = render_reframed(src.source_path, tmp, cs, ce, aspect.value,
                                src_w=src.width or 0, src_h=src.height or 0, extra_vf=extra_vf,
                                top_bias=top_bias, focus=focus, track=track,
                                content_type=content_type, timeout=_FFMPEG_TIMEOUT)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return False, None                                # ffmpeg absent/hung -> fail-open to the shared burn
        if r.returncode != 0 or not Path(tmp).exists():
            return False, None                                # ffmpeg failed -> fail-open (tmp swept in finally)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, out_path)                             # atomic publish — never a half-written per-account file
        return True, realized
    except Exception:
        return False, None                                    # fail-open by contract: a clip is never blocked on its variant
    finally:
        # sweep BOTH render artifacts on EVERY exit path (success, fail-open return, or a raise before the
        # subprocess) — the .ass is never an output, and the .part is consumed by os.replace on success (its
        # unlink then no-ops) but survives every failure. Mirrors overlay.burn_hook_only's atomic-temp finally.
        for _p in (ass_path, tmp):
            if _p:
                try: os.unlink(_p)
                except OSError: pass
