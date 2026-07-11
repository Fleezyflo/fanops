# src/fanops/keyframes.py
"""Extract a few still frames from a SOURCE video inside a [start,end] window — the EYES of the
vision-grounded hook AUTHOR (moments gate) and the intro-tease matcher. The author writes each clip's
on-screen hook true to what is actually on screen, so it needs real frames; clips are not rendered yet
when the moments gate opens, so the frames come from the source. Bounded + fail-open exactly like
vocals.isolate_vocals (`vocals.py`): a missing/unspawnable ffmpeg, a timeout, or a per-frame failure
degrades to fewer (or zero) frames — the caller falls back to text-only — and NEVER crashes a pass.

M2 — extract_frames_grid grew a content-addressed CACHE (`<agent_io>/keyframes/<source_id>/<hash>/`)
plus a per-(framing, source_id) stage_lock so two concurrent callers for the same window run ONE
ffmpeg, not N. The cache is OPT-IN via the `source_id=` kwarg: a caller that passes it gets the
cache + lock; a caller that doesn't gets today's behaviour byte-for-byte (back-compat for any
non-framing use that doesn't have a source identity). Mirrors clip._render_fingerprint's pattern:
the cache key is sha256((source_id, start, end, fps, width)), a hex digest as the dir name."""
from __future__ import annotations
import hashlib
import json
import subprocess
from pathlib import Path

from fanops.config import Config

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

_GRID_TIMEOUT = 60.0   # one bounded ffmpeg for the WHOLE window; a hung grid is reaped, never wedges the pass


def _window_cache_key(*, source_id: str, start: float, end: float, fps: float, width: int) -> str:
    """Content-addressed key for the extracted grid: sha256 over the inputs that determine the
    output bytes. Stable across processes (no salt, deterministic JSON). Hex digest so it's safe as
    a directory name on every fs."""
    payload = {"src": source_id, "s": round(start, 3), "e": round(end, 3),
               "fps": round(fps, 3), "w": width}
    blob = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _cache_dir_for(cfg: Config, *, source_id: str, window_hash: str) -> Path:
    """The content-addressed cache directory for one (source, window) grid extract. Mirrors
    clip._render_fingerprint's on-disk layout — content-addressed paths live under agent_io."""
    return cfg.agent_io / "keyframes" / source_id / window_hash


def _existing_cached_frames(cache_dir: Path) -> list[str]:
    """Return SORTED jpg paths already on disk in `cache_dir` (the cache-hit short-circuit). An
    empty list means there's no cached extract yet (or a partial one that was wiped); the caller
    runs ffmpeg."""
    if not cache_dir.exists():
        return []
    return [str(p) for p in sorted(cache_dir.glob("grid_*.jpg"))]


def extract_frames_grid(video_path: str, start: float, end: float, *, fps: float,
                        out_dir: str | Path, width: int = 960, timeout: float = _GRID_TIMEOUT,
                        source_id: str | None = None,
                        cfg: Config | None = None) -> list[str]:
    """SINGLE-PASS frame sampler: ONE ffmpeg `-vf fps=N,scale=W:-2` pass writing numbered jpgs across the
    whole [start,end) window, vs extract_keyframes' one -ss spawn PER frame. This is what makes fine-grained
    (sub-second) face/speaker detection affordable — fps=4 over a 20s window is 1 ffmpeg call (~80 frames),
    not 80. Returns the jpg paths SORTED (so list index == time order; frame i ~= start + i/fps). Same
    fail-open contract as extract_keyframes: non-positive window / absent-unspawnable ffmpeg / timeout /
    nonzero exit -> [] (caller degrades to the static keyframe path or centered crop). NEVER raises.
    scale uses -2 (even dim, AR-preserving) so the encoder never rejects an odd dimension.

    M2 cache (opt-in via source_id=): when source_id is provided, the output dir becomes the content-
    addressed cache_dir <agent_io>/keyframes/<source_id>/<hash>/ AND the call is bracketed by the per-
    (framing, source_id) stage_lock. A second concurrent caller blocks on the lock, finds the frames
    inside the lock, short-circuits. Without source_id (callers that don't have one — intro_match,
    casting), the function falls back to the legacy out_dir+stamp behaviour byte-for-byte.

    The cfg= kwarg is for the cache-path resolution (cfg.agent_io); when omitted but source_id is
    present, a Config() with default root is used."""
    if not (end > start):
        return []

    # M2 cache path — opt-in. The non-source_id branch is byte-identical to pre-M2 (back-compat).
    if source_id is not None:
        if cfg is None:
            cfg = Config()
        whash = _window_cache_key(source_id=source_id, start=start, end=end, fps=fps, width=width)
        cache_dir = _cache_dir_for(cfg, source_id=source_id, window_hash=whash)
        # Fast path: cache already populated -> return without acquiring the lock.
        cached = _existing_cached_frames(cache_dir)
        if cached:
            return cached
        # Slow path: bracket the ffmpeg by a per-(keyframes, window_hash) lock so two concurrent
        # callers for the same window don't both shell out. The lock is keyed on the WINDOW HASH,
        # NOT source_id — keying on source_id would self-deadlock when extract_frames_grid is
        # called from inside framing.detect_window's own (framing, source_id) lock (the same
        # process opens a second fd against the same path and waits forever). window_hash already
        # includes source_id, so per-(source, window) exclusion still holds. Re-check inside the
        # lock — the first acquirer fills the cache, the second enters, sees the frames, returns.
        from fanops.stage_lock import stage_lock
        with stage_lock(cfg, stage="keyframes", key=whash):
            cached = _existing_cached_frames(cache_dir)
            if cached:
                return cached
            result = _run_grid_extract(video_path, start, end, fps=fps, out_dir=cache_dir,
                                     width=width, timeout=timeout)
            if result and cfg is not None:
                from fanops.artifacts import stamp_stage
                stamp_stage(cfg, source_id, "keyframes", cache_dir, 1)
            return result

    # Legacy path — no cache, no lock; byte-identical to pre-M2 for callers without a source_id.
    return _run_grid_extract(video_path, start, end, fps=fps, out_dir=Path(out_dir), width=width,
                             timeout=timeout)


def _run_grid_extract(video_path: str, start: float, end: float, *, fps: float, out_dir: Path,
                      width: int, timeout: float) -> list[str]:
    """Run the one bounded ffmpeg grid pass + return the sorted jpgs. Shared between the cached
    and legacy paths; fail-open exactly like the pre-M2 body. NEVER raises."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(round(start * 100))                       # window-keyed prefix so concurrent windows don't collide
    pattern = out_dir / f"grid_{stamp}_%05d.jpg"
    try:
        r = subprocess.run(["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", video_path,
                            "-t", f"{end - start:.3f}", "-vf", f"fps={fps},scale={width}:-2", str(pattern)],
                           check=False, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []                                        # ffmpeg unusable -> degrade
    if r.returncode != 0:
        return []                                        # encode failed -> degrade (no partial grid)
    return [str(p) for p in sorted(out_dir.glob(f"grid_{stamp}_*.jpg"))]
