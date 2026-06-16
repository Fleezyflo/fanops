"""Render a Moment into platform-ready clips. Frame-accurate ffmpeg cut: -ss BEFORE -i
(fast seek) + -to AFTER -i (output-relative, version-stable — the v1 bug had -to before -i).
Reframe is chosen from the PROBED source dimensions so vertical/odd sources don't break.
render_aspects_for renders one clip per distinct aspect the active platforms need."""
from __future__ import annotations
import hashlib, json, subprocess
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, MomentState, ClipState, Fmt
from fanops.ids import child_id
from fanops.bands import band_for, TALK
from fanops import overlay
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

def reframe_filter(aspect: str, src_w: int, src_h: int) -> str:
    """Pick a safe ffmpeg -vf for the target aspect given the source dimensions."""
    tw, th = _TARGETS[aspect]
    if not src_w or not src_h:
        # unknown source: scale to fit + pad to exact target (never an impossible crop)
        return (f"scale={tw}:{th}:force_original_aspect_ratio=decrease,"
                f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2,setsar=1")
    src_ar = src_w / src_h
    tgt_ar = tw / th
    if abs(src_ar - tgt_ar) < 0.01:
        return f"scale={tw}:{th},setsar=1"
    if src_ar > tgt_ar:
        # source wider than target -> crop width
        return f"crop=ih*{tw}/{th}:ih,scale={tw}:{th},setsar=1"
    # source taller/narrower than target -> crop height
    return f"crop=iw:iw*{th}/{tw},scale={tw}:{th},setsar=1"

def ffmpeg_clip_cmd(src: str, dst: str, start: float, end: float, aspect: str,
                    *, src_w: int = 0, src_h: int = 0, extra_vf: str | None = None) -> list[str]:
    # -ss before -i (fast seek) makes output-position -to a DURATION measured from the seek
    # point, so it must be (end - start), not the absolute end. Verified on ffmpeg 8.0.1:
    # `-ss 1.5 -to 6.5` yields a 6.5s clip; passing 8.0 here would yield 8.0s (the F39 bug).
    # extra_vf (e.g. the burned-subtitles `subtitles=...` token) is chained AFTER the reframe
    # with a comma so it operates on the already-reframed frame; default None == old behavior.
    vf = reframe_filter(aspect, src_w, src_h)
    if extra_vf:
        vf = f"{vf},{extra_vf}"
    return ["ffmpeg", "-y", "-ss", str(start), "-i", src, "-to", str(end - start),
            "-vf", vf,
            "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", dst]

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
    Returns None when there's nothing to burn, or (logged once) when ffmpeg lacks the text filter."""
    m = led.moments[moment_id]
    src = led.sources[m.parent_id]
    hook = None if cfg.creative_variation else ((m.hook or "").strip() or None)  # per-surface hook owns it under variation; blank -> None
    segments = (src.transcript or []) if cfg.burn_subs else []   # transcript is opt-in
    if not hook and not segments:                        # no hook, no opted-in transcript -> clean clip
        return None
    if not overlay.ffmpeg_has_textfilter():
        # Text was asked for but the toolchain can't burn it. Don't block the clip — log once and
        # render plain. (One line per clip; ffmpeg_has_textfilter caches, so the probe runs once.)
        get_logger(cfg)("clip", cid, "subs_skipped",
                        reason="ffmpeg lacks the text filter — rendering without subtitles/hook")
        return None
    tw, th = _TARGETS[aspect.value]
    ass_text = overlay.build_ass(segments, hook=hook, clip_start=clip_start, clip_end=clip_end,
                                 width=tw, height=th, font=cfg.subtitle_font)
    if not ass_text or not ass_text.strip():
        return None
    ass_path = cfg.clips / f"{cid}.ass"
    overlay.write_ass(ass_text, ass_path)
    return overlay.subtitles_vf(ass_path)

# Phase D: the clip's content-address (child_id of moment+aspect) does NOT include the burned hook or
# the cut window, so an mp4 on disk is NOT proof it matches the INTENDED render — a changed hook would
# leave a stale clip (the stale-render class of bug). The render fingerprint captures everything that
# determines the rendered bytes (source, window, aspect, source dims, the burned .ass text), so the
# lock-free pre-warm and the in-lock commit agree on when an existing mp4 may be reused. This is what
# lets the heavy ffmpeg run OUTSIDE the ledger lock and the commit pass skip it.
def _render_fingerprint(src_path: str, cs: float, ce: float, aspect_value: str,
                        src_w: int, src_h: int, ass_text: str) -> str:
    payload = {"src": src_path, "cs": round(cs, 3), "ce": round(ce, 3), "aspect": aspect_value,
               "w": src_w, "h": src_h, "ass": ass_text}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()

def _fingerprint_matches(fp_path, fp: str) -> bool:
    try:
        return fp_path.exists() and json.loads(fp_path.read_text()).get("fp") == fp
    except (OSError, json.JSONDecodeError, ValueError):
        return False

def render_moment(led: Ledger, cfg: Config, moment_id: str, *,
                  aspect: Fmt = Fmt.r9x16) -> tuple[Ledger, Clip]:
    m = led.moments[moment_id]
    src = led.sources[m.parent_id]
    cid = child_id("clip", moment_id, aspect.value)      # content-addressed by aspect
    cfg.clips.mkdir(parents=True, exist_ok=True)
    dst = cfg.clips / f"{cid}.mp4"
    band = band_for(cfg.clip_profile)                          # talk 12-22s / song 18-35s
    cs, ce = fit_window(m.start, m.end, src.duration or 0.0, lo=band.lo, hi=band.hi)  # widen to a real clip
    cs, ce = snap_window(cs, ce, src.transcript, duration=src.duration or 0.0)  # land on clean phrase boundaries
    extra_vf = _subtitles_vf(led, cfg, moment_id, cid, aspect, clip_start=cs, clip_end=ce)
    # Phase D idempotent skip: if cid.mp4 already exists AND its fingerprint matches this exact intended
    # render (a pre-warm pass produced it), adopt it and SKIP ffmpeg — record the clip + advance the
    # moment. A changed hook/window yields a different fingerprint -> re-render (no stale clip reuse).
    ass_path = cfg.clips / f"{cid}.ass"
    ass_text = ass_path.read_text(encoding="utf-8") if (extra_vf and ass_path.exists()) else ""
    fp = _render_fingerprint(src.source_path, cs, ce, aspect.value, src.width or 0, src.height or 0, ass_text)
    fp_path = cfg.clips / f"{cid}.render.json"
    if dst.exists() and dst.stat().st_size > 0 and _fingerprint_matches(fp_path, fp):
        clip = Clip(id=cid, parent_id=moment_id, state=ClipState.rendered, path=str(dst), aspect=aspect)
        led.clips[cid] = clip
        led.set_moment_state(moment_id, MomentState.clipped)
        return led, clip
    cmd = ffmpeg_clip_cmd(src.source_path, str(dst), cs, ce, aspect.value,
                          src_w=src.width or 0, src_h=src.height or 0, extra_vf=extra_vf)
    try:
        r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
    except (FileNotFoundError, OSError) as e:
        # ffmpeg ABSENT from PATH (or otherwise unspawnable): subprocess.run raises BEFORE the
        # process starts, so check=False (which only suppresses a nonzero RETURNCODE) does not
        # cover it. Treat it exactly like the nonzero-rc branch — record ClipState.error and
        # leave the moment at `decided` so a re-run retries when ffmpeg returns. Otherwise the
        # raise escapes to the pipeline's per-moment quarantine, parking the moment in the
        # TERMINAL MomentState.error (never re-rendered) — a transient PATH glitch would wedge
        # it permanently, contradicting this module's fail-safe philosophy.
        clip = Clip(id=cid, parent_id=moment_id, state=ClipState.error, path=str(dst),
                    aspect=aspect, error_reason=f"toolchain missing: {cmd[0]} ({type(e).__name__})")
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
    if r.returncode != 0 or not dst.exists():
        # ffmpeg RAN and failed: record the clip as errored (dangling path would otherwise
        # masquerade as 'rendered' and blow up later in crosspost/media-upload).
        # Leave the moment un-clipped so a re-run retries. Mirrors transcribe.py's pattern.
        clip = Clip(id=cid, parent_id=moment_id, state=ClipState.error, path=str(dst),
                    aspect=aspect, error_reason=f"ffmpeg rc={r.returncode}: {(r.stderr or '')[:200]}")
        led.clips[cid] = clip
        return led, clip
    clip = Clip(id=cid, parent_id=moment_id, state=ClipState.rendered, path=str(dst), aspect=aspect)
    # Overwrite any prior clip at this content-addressed id (e.g. a previous error-state
    # render) so a re-render self-heals; setdefault would pin the stale clip. id is unique
    # per (moment, aspect), so the latest successful render is authoritative.
    led.clips[cid] = clip
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
