# src/fanops/ingest_shard.py
"""Long-source sharding: silence-snapped cut points + stream-copy segment files."""
from __future__ import annotations
import os
import re
import subprocess
from pathlib import Path
from fanops.config import Config
from fanops.errors import ToolchainMissingError
from fanops.log import get_logger
from fanops.media_probe import probe_dimensions

_SHARD_TARGET_S = 1500.0      # ~25 min segment target (internal — not env)
_SHARD_SNAP_WINDOW_S = 90.0   # silence snap radius around nominal window end
_SHARD_SIL_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SHARD_SIL_END = re.compile(r"silence_end:\s*([0-9.]+)")

def _shard_silence_cmd(path: Path) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-vn", "-i", str(path), "-af",
            "silencedetect=noise=-35dB:d=1.5", "-f", "null", "-"]

def _run_ffmpeg(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, OSError) as e:
        raise ToolchainMissingError(
            f"ffmpeg not found on PATH — install ffmpeg to shard sources ({type(e).__name__})") from e

def _parse_silence_intervals(stderr: str) -> list[tuple[float, float]]:
    starts = [float(m) for m in _SHARD_SIL_START.findall(stderr)]
    ends = [float(m) for m in _SHARD_SIL_END.findall(stderr)]
    return [(s, e) for s, e in zip(starts, ends) if e > s]

def _shard_ffmpeg_timeout(duration: float) -> float:
    return max(60.0, duration * 0.1 + 60.0)

def shard_points(path: Path, duration: float, *, target_s: float = _SHARD_TARGET_S) -> list[float]:
    """Interior cut timestamps for stream-copy sharding."""
    if duration <= 0:
        return []
    intervals: list[tuple[float, float]] = []
    try:
        r = _run_ffmpeg(_shard_silence_cmd(path), timeout=_shard_ffmpeg_timeout(duration))
        intervals = _parse_silence_intervals(r.stderr or "")
    except (ToolchainMissingError, subprocess.TimeoutExpired):
        intervals = []
    def _pick_cut(nominal: float) -> float:
        best = None; best_len = -1.0
        for start, end in intervals:
            mid = (start + end) / 2.0
            if abs(mid - nominal) <= _SHARD_SNAP_WINDOW_S:
                seg_len = end - start
                if seg_len > best_len:
                    best_len = seg_len; best = mid
        return best if best is not None else min(nominal, duration - 0.001)
    points: list[float] = []
    if duration <= target_s:
        cut = _pick_cut(duration / 2.0)
        if 0 < cut < duration:
            points.append(cut)
        return sorted(set(points))
    k = 0
    while True:
        nominal = (k + 1) * target_s
        if nominal >= duration:
            break
        cut = _pick_cut(nominal)
        if 0 < cut < duration:
            points.append(cut)
        k += 1
    return sorted(set(points))

def _stem_is_shard_part(stem: str) -> bool:
    return bool(re.search(r"-p\d{2}$", stem))

def shard_file(cfg: Config, f: Path, points: list[float]) -> list[Path] | None:
    """Stream-copy `f` into inbox part files beside it. None on any segment failure (parts cleaned up)."""
    _w, _h, dur = probe_dimensions(f)
    duration = dur or 0.0
    bounds = [0.0, *sorted(points), duration]
    parts: list[Path] = []
    temps: list[Path] = []
    try:
        for i in range(len(bounds) - 1):
            start = bounds[i]; seg_len = bounds[i + 1] - start
            if seg_len <= 0:
                continue
            dest = f.parent / f"{f.stem}-p{i + 1:02d}{f.suffix}"
            tmp = dest.with_name(dest.name + ".part.mp4")
            temps.append(tmp)
            cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(f), "-to", f"{seg_len:.3f}",
                   "-c", "copy", "-avoid_negative_ts", "make_zero", str(tmp)]
            r = _run_ffmpeg(cmd, timeout=_shard_ffmpeg_timeout(duration))
            if r.returncode != 0:
                raise RuntimeError("segment failed")
            os.replace(tmp, dest)
            temps.remove(tmp)
            parts.append(dest)
        return parts
    except (ToolchainMissingError, subprocess.TimeoutExpired, OSError, RuntimeError):
        pass
    for p in parts + temps:
        try:
            if p.exists(): p.unlink()
        except OSError as e:
            get_logger(cfg)("ingest", f.name, "shard_cleanup_failed", part=p.name, why=str(e)[:120])
    return None
