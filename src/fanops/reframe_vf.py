"""Reframe video-filter geometry — crop/zoom math and ffmpeg -vf / filter_complex strings.

Target render sizes, face-fraction zoom, safe-area margins, active-speaker pan expressions,
per-segment concat graphs, and the vertical two-shot stack. ffmpeg command builders live in clip_ffmpeg.py."""
from __future__ import annotations

from statistics import median

from fanops import framing

# Target render size per aspect. The subtitle .ass PlayResX/Y must match the rendered frame so
# libass scales the caption to the clip — clip._build_ass_text reads this same table.
_TARGETS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}

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

# ---- E1b safe-area: the crop must CONTAIN the full detected face box with a margin on every edge and
# headroom above the head; when the box can't fit at the target zoom the crop WIDENS (reduce zoom), never
# cuts the face, bounded by the source. One implementation, shared by all four crop paths (_focus_crop,
# _track_crop, _already_aspect, _crop_box). A focus/segment with NO face height (a 2-tuple or saliency)
# leaves size + origin exactly as _place produced them -> byte-identical to the pre-E1b behaviour. ----
_SAFE_MARGIN_FRAC = 0.05     # min gap from the detected face box to every crop edge, as a fraction of the source dim

def _safe_dims(cw: int, ch: int, ch0: int, src_w: int, src_h: int, tw: int, th: int, fh, fw):
    """Widen the crop (reduce zoom) until the face box + a margin on every edge fits, bounded by the source
    baseline ch0 (never wider than the blind crop) and the source dims. fh/fw falsy on an axis -> that axis
    is unconstrained (a 2-tuple/legacy focus keeps today's size)."""
    if not fh and not fw:
        return cw, ch
    need = float(ch)
    if fh:
        need = max(need, (fh + 2 * _SAFE_MARGIN_FRAC) * src_h)
    if fw:
        need = max(need, ((fw + 2 * _SAFE_MARGIN_FRAC) * src_w) * th / tw)   # width need -> implied crop height
    ch = min(ch0, src_h, round(need))
    cw = min(round(ch * tw / th), src_w)
    return cw, ch

def _safe_origin(src_w: int, src_h: int, cw: int, ch: int, fx: float, fy: float, fh, ey, fw, eyeline: float):
    """Crop origin (x,y) that keeps the full face box inside with a margin (where the source allows) and
    protects the head-top, then clamps to source bounds. Reduces to _place when there is no face box."""
    if not fh:
        return _place(src_w, src_h, cw, ch, fx, ey if ey is not None else fy, eyeline if ey is not None else 0.5)
    mv, mw = _SAFE_MARGIN_FRAC * src_h, _SAFE_MARGIN_FRAC * src_w
    x = round(fx * src_w - cw / 2)                             # centre horizontally on the face
    if fw:                                                     # keep the face box's L/R edges inside with a margin
        lo, hi = round((fx + fw / 2) * src_w + mw - cw), round((fx - fw / 2) * src_w - mw)
        if lo <= hi:
            x = _clamp(x, lo, hi)
    x = _clamp(x, 0, max(0, src_w - cw))
    y = round((ey if ey is not None else fy) * src_h - eyeline * ch)   # eye-line composition
    lo = round((fy + fh / 2) * src_h + mv - ch)               # chin inside
    hi = round((fy - fh / 2) * src_h - mv)                    # head-top inside (headroom)
    if lo <= hi:
        y = _clamp(y, lo, hi)
    y = _clamp(y, 0, max(0, src_h - ch))
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

_SMALL_FACE_FRAC = 0.18      # below this source face height the subject is FAR (often profile + mic-occluded) —
                             # a tight punch-in just frames the foreground mic, so cap the zoom hard and show context
_ZOOM_MAX_FAR = 1.25         # the far-subject zoom cap: a wide, contextual shot (punch in on a near subject, hold
                             # wide on the far/turned one) — never a tight crop of an occlusion

def _adaptive_zoom_max(fh, base: float) -> float:
    """Face-size-adaptive zoom cap: a FAR/small subject (fh < _SMALL_FACE_FRAC, typically profile + mic-occluded)
    is held WIDE (_ZOOM_MAX_FAR) so its crop shows context, not a tight frame of the foreground mic; a near/well-
    sized subject keeps the `base` cap (punch-in). fh falsy -> base (no zoom applies anyway). The far cap only ever
    TIGHTENS: `min` keeps it from LOOSENING a base that is already gentler than _ZOOM_MAX_FAR (S3 passes
    _GENTLE_ZOOM_MAX 1.15, and a far subject must not zoom MORE than a near one). base=_ZOOM_MAX (1.6, the only
    pre-S3 caller) -> min(1.25, 1.6) == 1.25 -> byte-identical."""
    return min(_ZOOM_MAX_FAR, base) if (fh and 0 < fh < _SMALL_FACE_FRAC) else base

def _track_crop(track: list, src_w: int, src_h: int, tw: int, th: int, ch0: int, frac: float, *, axis: str) -> str:
    """Active-speaker crop: ONE zoom for the window (from the segments' median face height) + a SMOOTH pan
    of the crop origin between per-segment anchors. crop w/h constant (ffmpeg evals them once); x/y are the
    t-expressions. `axis` is just documentation — both x and y are emitted; a constant axis collapses to an int."""
    fhs = [s[4] for s in track if len(s) > 4 and s[4]]
    fws = [s[6] for s in track if len(s) > 6 and s[6]]
    ch = _zoom_h(src_h, ch0, median(fhs) if fhs else None, frac)
    cw = min(round(ch * tw / th), src_w); ch = min(ch, src_h)
    # E1b: ONE window crop that fits the WIDEST/TALLEST speaker across segments; each origin keeps ITS face safe.
    cw, ch = _safe_dims(cw, ch, ch0, src_w, src_h, tw, th, max(fhs) if fhs else None, max(fws) if fws else None)
    bounds = [round(s[1], 2) for s in track[:-1]]
    xs, ys = [], []
    for s in track:
        fh = s[4] if len(s) > 4 else None
        ey = s[5] if len(s) > 5 else s[3]
        fw = s[6] if len(s) > 6 else None
        x, y = _safe_origin(src_w, src_h, cw, ch, s[2], s[3], fh, ey, fw, _EYELINE_FRAC if len(s) > 5 else 0.5)
        xs.append(x); ys.append(y)
    xexpr = _step_expr(bounds, xs)
    yexpr = _step_expr(bounds, ys)
    return f"crop=w={cw}:h={ch}:x={xexpr}:y={yexpr},scale={tw}:{th},setsar=1"

def _focus_crop(focus: tuple, src_w: int, src_h: int, tw: int, th: int, ch0: int, frac: float,
                *, symbolic_w: str, symbolic_full: bool, zoom_base: float = _ZOOM_MAX) -> str:
    """Static subject-lock crop: zoom to the target face fraction + eye-line. When a 2-tuple focus produces
    NO zoom (full baseline extent), emit the legacy SYMBOLIC form so a pre-zoom focus clip is byte-identical
    (no needless re-render); otherwise a numeric zoomed crop. `zoom_base` is the magnification ceiling before
    the far-subject adaptation; it defaults to _ZOOM_MAX so every pre-S3 caller is byte-identical, and S3's
    subject-lock passes _GENTLE_ZOOM_MAX to re-anchor a dominant host without punching in (F6/ADR-0103)."""
    fh = focus[2] if len(focus) > 2 else None
    ey = focus[3] if len(focus) > 3 else None
    fw = focus[4] if len(focus) > 4 else None
    ch = _zoom_h(src_h, ch0, fh, frac, zoom_max=_adaptive_zoom_max(fh, zoom_base))
    cw = min(round(ch * tw / th), src_w); ch = min(ch, src_h)
    cw, ch = _safe_dims(cw, ch, ch0, src_w, src_h, tw, th, fh, fw)   # E1b: widen if the face box won't fit
    x, y = _safe_origin(src_w, src_h, cw, ch, focus[0], focus[1], fh, ey, fw, _EYELINE_FRAC)
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
    if content_type == framing.RENDER_STACK_PAIR:
        focus, content_type = None, None     # the stack renders via render_reframed's filter_complex, not here — centre defensively
    tw, th = _TARGETS[aspect]
    if not src_w or not src_h:
        # unknown source: scale to fit + pad to exact target (never an impossible crop)
        return (f"scale={tw}:{th}:force_original_aspect_ratio=decrease,"
                f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2,setsar=1")
    src_ar = src_w / src_h
    tgt_ar = tw / th
    frac = _target_frac(content_type)
    # S3/D1-B: the subject-lock re-anchors the crop onto the ONE dominant host. Its defect is POSITIONAL, so it
    # is capped at the GENTLE magnification — the crop moves onto the host rather than punching in on him (spec
    # F6 "widest crop that satisfies F1-F3; zoom only to remove dead space, never for emphasis"; ADR-0103's
    # binding minimal-zoom requirement). Any other content_type keeps _ZOOM_MAX -> byte-identical.
    zoom_base = _GENTLE_ZOOM_MAX if content_type == framing.RENDER_SUBJECT_LOCK else _ZOOM_MAX
    if abs(src_ar - tgt_ar) < 0.01:
        return _already_aspect(tw, th, src_w, src_h, focus, frac)   # passthrough or a bounded gentle zoom
    if src_ar > tgt_ar:
        # source wider than target -> crop width (full height kept). track/focus zoom + slide onto the subject.
        ch0 = src_h
        if track:
            return _track_crop(track, src_w, src_h, tw, th, ch0, frac, axis="x")
        if focus is not None:
            return _focus_crop(focus, src_w, src_h, tw, th, ch0, frac, zoom_base=zoom_base,
                               symbolic_w=f"crop=ih*{tw}/{th}:ih:{{x}}:{{y}}", symbolic_full=True)
        return f"crop=ih*{tw}/{th}:ih,scale={tw}:{th},setsar=1"
    # source taller/narrower than target -> crop height.
    ch0 = round(src_w * th / tw)
    if track:
        return _track_crop(track, src_w, src_h, tw, th, ch0, frac, axis="y")
    if focus is not None:
        return _focus_crop(focus, src_w, src_h, tw, th, ch0, frac, zoom_base=zoom_base,
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
    ey = focus[3] if len(focus) > 3 else focus[1]
    fw = focus[4] if len(focus) > 4 else None
    ch = _zoom_h(src_h, src_h, fh, frac, zoom_max=_GENTLE_ZOOM_MAX)
    cw = min(round(ch * tw / th), src_w); ch = min(ch, src_h)
    cw, ch = _safe_dims(cw, ch, src_h, src_w, src_h, tw, th, fh, fw)   # E1b (ch0 = src_h for an already-aspect source)
    x, y = _safe_origin(src_w, src_h, cw, ch, focus[0], focus[1], fh, ey, fw, _EYELINE_FRAC)
    return f"crop={cw}:{ch}:{x}:{y},scale={tw}:{th},setsar=1"

def _crop_box(fx: float, fy: float, fh, ey, src_w: int, src_h: int, tw: int, th: int,
              ch0: int, frac: float, zoom_max: float, fw=None):
    """Numeric crop (cw, ch, x, y) that zooms a subject of normalized face-height fh to `frac` of the output
    (bounded by zoom_max) and anchors its eye-line ey at _EYELINE_FRAC. Shared sizing math so the static focus
    crop and the per-segment active-speaker crops are consistent. fh falsy -> no zoom (full ch0 extent).
    The zoom cap is face-size-adaptive: a far/small subject is held wide (context, not a tight mic crop).
    E1b: `fw` (face width, when known) drives the horizontal safe-area — the crop widens rather than cut it."""
    ch = _zoom_h(src_h, ch0, fh, frac, zoom_max=_adaptive_zoom_max(fh, zoom_max))
    cw = min(round(ch * tw / th), src_w); ch = min(ch, src_h)
    cw, ch = _safe_dims(cw, ch, ch0, src_w, src_h, tw, th, fh, fw)
    x, y = _safe_origin(src_w, src_h, cw, ch, fx, fy, fh, ey, fw, _EYELINE_FRAC)
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
    fw = seg[6] if len(seg) > 6 else None
    cw, ch, x, y = _crop_box(seg[2], seg[3], fh, ey, src_w, src_h, tw, th, ch0, frac, _ZOOM_MAX_TRACK, fw)
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

def _stack_filter_complex(focus: tuple, src_w: int, src_h: int, aspect_value: str,
                          *, sub_token: str | None = None) -> str:
    """filter_complex for the vertical stack: the LEFT host anchor (focus[0:5] = cx,cy,fh,ey,fw) cropped into
    the TOP half and the RIGHT host anchor (focus[5:10]) into the BOTTOM half, each scaled to tw×(th//2), then
    vstacked; an optional subtitle burn runs on the stacked frame. Each half reuses the proven _crop_box sizing
    at a GENTLE zoom cap (minimal punch-in). ch0 None (unknown dims) -> scale-only halves (fail-open, no crop)."""
    tw, th = _TARGETS[aspect_value]
    half = th // 2
    frac = _target_frac(None)
    ch0 = None
    if src_w and src_h:
        ch0 = src_h if (src_w / src_h) > (tw / half) else round(src_w * half / tw)
    def _half(anchor: tuple, label: str) -> str:
        if ch0 is None:
            return f"[0:v]scale={tw}:{half},setsar=1[{label}]"
        cx, cy, fh, ey = anchor[0], anchor[1], anchor[2], anchor[3]
        fw = anchor[4] if len(anchor) > 4 else None
        cw, ch, x, y = _crop_box(cx, cy, fh, ey, src_w, src_h, tw, half, ch0, frac, _GENTLE_ZOOM_MAX, fw)
        return f"[0:v]crop={cw}:{ch}:{x}:{y},scale={tw}:{half},setsar=1[{label}]"
    parts = [_half(tuple(focus[0:5]), "sptop"), _half(tuple(focus[5:10]), "spbot")]
    vlabel = "[vc]" if sub_token else "[vout]"
    parts.append(f"[sptop][spbot]vstack=inputs=2{vlabel}")
    if sub_token:
        parts.append(f"[vc]{sub_token}[vout]")
    return ";".join(parts)
