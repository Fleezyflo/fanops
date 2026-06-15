"""Render a Moment into platform-ready clips. Frame-accurate ffmpeg cut: -ss BEFORE -i
(fast seek) + -to AFTER -i (output-relative, version-stable — the v1 bug had -to before -i).
Reframe is chosen from the PROBED source dimensions so vertical/odd sources don't break.
render_aspects_for renders one clip per distinct aspect the active platforms need."""
from __future__ import annotations
import subprocess
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, MomentState, ClipState, Fmt
from fanops.ids import child_id
from fanops import overlay
from fanops.log import get_logger

# Target render size per aspect. The subtitle .ass PlayResX/Y must match the rendered frame so
# libass scales the caption to the clip — so render_moment reads this same table the reframe uses.
_TARGETS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}

# Hard bound on one ffmpeg render (the llm.py timeout idiom). render_moment runs INSIDE
# advance()'s ledger transaction, so an UNBOUNDED hang on a corrupt input held the flock against
# every other pass and Studio write. 10min covers a multi-minute 1080p re-encode with headroom.
_FFMPEG_TIMEOUT = 600.0

# A real clip is watchable, not a 3-4s fragment. The model is asked for 12-22s windows
# (prompts.moment_prompt); this is the render-time SAFETY NET that guarantees it even when a pick
# comes back short (or long). The 12s floor lets short sources (and the model's tighter picks)
# qualify; sources below the floor render whole. The subtitle overlay uses the SAME fitted window.
_MIN_CLIP_S = 12.0
_MAX_CLIP_S = 22.0

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
    """Build the burned-subtitles `-vf` fragment for this clip, or return None (render with the
    reframe only). FAIL-OPEN by contract: a clip is NEVER blocked on subtitles. Returns None when
    burn_subs is off, the source has no transcript, or build_ass yields nothing. When subtitles are
    REQUESTED (burn_subs on, transcript present) but this ffmpeg lacks the text filter, log ONE
    warning and return None. Writes the .ass adjacent to the clip (cfg.clips/<cid>.ass) on success."""
    if not cfg.burn_subs:
        return None
    m = led.moments[moment_id]
    src = led.sources[m.parent_id]
    transcript = src.transcript
    if not transcript:                                   # None or [] -> nothing to burn
        return None
    if not overlay.ffmpeg_has_textfilter():
        # Subtitles were asked for but the toolchain can't burn them. Don't block the clip — log
        # once and render plain. (One line per clip; the cache in ffmpeg_has_textfilter means the
        # probe itself runs at most once per process.)
        get_logger(cfg)("clip", cid, "subs_skipped",
                        reason="ffmpeg lacks the text filter — rendering without subtitles")
        return None
    tw, th = _TARGETS[aspect.value]
    ass_text = overlay.build_ass(transcript, hook=m.hook, clip_start=clip_start, clip_end=clip_end,
                                 width=tw, height=th, font=cfg.subtitle_font)
    if not ass_text or not ass_text.strip():
        return None
    ass_path = cfg.clips / f"{cid}.ass"
    overlay.write_ass(ass_text, ass_path)
    return overlay.subtitles_vf(ass_path)

def render_moment(led: Ledger, cfg: Config, moment_id: str, *,
                  aspect: Fmt = Fmt.r9x16) -> tuple[Ledger, Clip]:
    m = led.moments[moment_id]
    src = led.sources[m.parent_id]
    cid = child_id("clip", moment_id, aspect.value)      # content-addressed by aspect
    cfg.clips.mkdir(parents=True, exist_ok=True)
    dst = cfg.clips / f"{cid}.mp4"
    cs, ce = fit_window(m.start, m.end, src.duration or 0.0)   # widen a short pick to a real 15-20s clip
    extra_vf = _subtitles_vf(led, cfg, moment_id, cid, aspect, clip_start=cs, clip_end=ce)
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
