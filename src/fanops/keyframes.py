# src/fanops/keyframes.py
"""Extract a few still frames from a SOURCE video inside a [start,end] window — the EYES of the
vision-grounded hook AUTHOR (moments gate) and the intro-tease matcher. The author writes each clip's
on-screen hook true to what is actually on screen, so it needs real frames; clips are not rendered yet
when the moments gate opens, so the frames come from the source. Bounded + fail-open exactly like
vocals.isolate_vocals (`vocals.py`): a missing/unspawnable ffmpeg, a timeout, or a per-frame failure
degrades to fewer (or zero) frames — the caller falls back to text-only — and NEVER crashes a pass."""
from __future__ import annotations
import subprocess
from pathlib import Path

_KF_TIMEOUT = 30.0   # one bounded ffmpeg per frame; a hung extract is reaped, never wedges the pass

def extract_keyframes(video_path: str, start: float, end: float, *, count: int = 3,
                      out_dir: str | Path, width: int = 480, timeout: float = _KF_TIMEOUT) -> list[str]:
    """Return up to `count` jpeg paths sampled evenly STRICTLY inside (start,end). A non-positive
    window → []. Absent/unspawnable ffmpeg or a timeout → [] (fail-open). A single frame that fails
    is skipped, not fatal, so a partial read still gives the editor something to look at."""
    if not (end > start):
        return []
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    dur = end - start
    times = [start + dur * (i + 1) / (count + 1) for i in range(count)]   # interior points, no edges
    written: list[str] = []
    try:
        for i, t in enumerate(times):
            dst = out / f"kf_{int(round(start * 100))}_{i}.jpg"
            r = subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", video_path, "-frames:v", "1",
                                "-vf", f"scale={width}:-1", str(dst)],
                               check=False, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0 and dst.exists():
                written.append(str(dst))
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []                                        # ffmpeg unusable -> degrade to text-only
    return written
