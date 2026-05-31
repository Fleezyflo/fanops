"""Render a Moment into platform-ready clips. Frame-accurate ffmpeg cut: -ss BEFORE -i
(fast seek) + -to AFTER -i (output-relative, version-stable — the v1 bug had -to before -i).
Reframe is chosen from the PROBED source dimensions so vertical/odd sources don't break.
render_aspects_for renders one clip per distinct aspect the active platforms need."""
from __future__ import annotations
import subprocess
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, MomentState, ClipState, Fmt
from fanops.ids import child_id

def reframe_filter(aspect: str, src_w: int, src_h: int) -> str:
    """Pick a safe ffmpeg -vf for the target aspect given the source dimensions."""
    targets = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
    tw, th = targets[aspect]
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
                    *, src_w: int = 0, src_h: int = 0) -> list[str]:
    # -ss before -i (fast seek) makes output-position -to a DURATION measured from the seek
    # point, so it must be (end - start), not the absolute end. Verified on ffmpeg 8.0.1:
    # `-ss 1.5 -to 6.5` yields a 6.5s clip; passing 8.0 here would yield 8.0s (the F39 bug).
    return ["ffmpeg", "-y", "-ss", str(start), "-i", src, "-to", str(end - start),
            "-vf", reframe_filter(aspect, src_w, src_h),
            "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", dst]

def render_moment(led: Ledger, cfg: Config, moment_id: str, *,
                  aspect: Fmt = Fmt.r9x16) -> tuple[Ledger, Clip]:
    m = led.moments[moment_id]
    src = led.sources[m.parent_id]
    cid = child_id("clip", moment_id, aspect.value)      # content-addressed by aspect
    cfg.clips.mkdir(parents=True, exist_ok=True)
    dst = cfg.clips / f"{cid}.mp4"
    r = subprocess.run(ffmpeg_clip_cmd(src.source_path, str(dst), m.start, m.end, aspect.value,
                                       src_w=src.width or 0, src_h=src.height or 0),
                       check=False, capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        # ffmpeg failed: record the clip as errored (dangling path would otherwise
        # masquerade as 'rendered' and blow up later in crosspost/media-upload).
        # Leave the moment un-clipped so a re-run retries. Mirrors transcribe.py's pattern.
        clip = Clip(id=cid, parent_id=moment_id, state=ClipState.error, path=str(dst),
                    aspect=aspect, error_reason=f"ffmpeg rc={r.returncode}: {(r.stderr or '')[:200]}")
        led.add_clip(clip)
        return led, clip
    clip = Clip(id=cid, parent_id=moment_id, state=ClipState.rendered, path=str(dst), aspect=aspect)
    led.add_clip(clip)
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
