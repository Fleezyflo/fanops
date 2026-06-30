"""Fail-open video shrink for upload caps (Zernio TikTok 413)."""
from __future__ import annotations
import subprocess
import tempfile
from pathlib import Path
from fanops.config import Config
from fanops.log import get_logger


def maybe_shrink_for_cap(cfg: Config, path: Path, cap: int, *, label: str = "upload") -> Path:
    """Return `path` if within cap, else a re-encoded temp file under cap. Fail-open to `path` on error."""
    try:
        size = path.stat().st_size
    except OSError:
        return path
    if size <= cap:
        return path
    log = get_logger(cfg)
    shrink_root = cfg.base / "04_agent_io"
    shrink_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="fanops-shrink-", dir=str(shrink_root)))
    for crf in (28, 32, 36, 40):
        out = tmp / f"{path.stem}.crf{crf}.mp4"
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
               "-c:v", "libx264", "-preset", "fast", "-crf", str(crf),
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=600)
        except Exception as exc:
            log(label, path.stem, "shrink_failed", err=str(exc)[:120])
            return path
        if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            continue
        if out.stat().st_size <= cap:
            log(label, path.stem, "shrink_ok", was=size, now=out.stat().st_size, crf=crf)
            return out
    log(label, path.stem, "shrink_still_oversize", was=size, cap=cap)
    return path
