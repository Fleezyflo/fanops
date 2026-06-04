"""Pre-ingest content discovery + folder-review intake. CHEAP by design: a filesystem scan +
ONE ffprobe + ONE thumbnail frame per candidate — NO transcription, NO LLM, NO signal detection
(that expensive pipeline work happens only AFTER the operator approves, on approved items). The
operator reviews 00_review/ in Finder and moves keepers into 00_review/approved/; `intake` then
copies the approved originals into 01_inbox/ for the existing pipeline. Rejects never enter the
pipeline (no wasted clip/claude cost)."""
from __future__ import annotations
import json, os, shutil, subprocess
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.ingest import scan_local, probe_dimensions, sha256_of

def candidate_meta(path: Path) -> dict:
    """Cheap metadata for one candidate: bytes + mtime always (from os.stat); width/height/duration
    via ffprobe (fail-soft — if ffprobe chokes, return them None so the candidate is still listed)."""
    st = os.stat(path)
    w = h = dur = None
    try:
        pw, ph, pdur = probe_dimensions(path)
        w, h, dur = (pw or None), (ph or None), (pdur or None)
    except Exception:
        pass                                   # fail-soft: list it anyway, dims/duration unknown
    return {"bytes": st.st_size, "mtime": st.st_mtime, "width": w, "height": h, "duration": dur}

def make_thumbnail(path: Path, out_jpg: Path, *, at_seconds: float = 1.0) -> bool:
    """One cheap thumbnail frame (320px wide). Fail-open: returns False (no raise, no file) if
    ffmpeg is absent or errors — the candidate is still listed from metadata, just without a thumb."""
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-ss", str(at_seconds), "-i", str(path),
           "-frames:v", "1", "-vf", "scale=320:-1", str(out_jpg)]
    try:
        r = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        return False
    if r.returncode != 0 or not out_jpg.exists():
        # a 1s seek can overshoot a <1s clip; one retry at t=0 before giving up
        cmd0 = ["ffmpeg", "-y", "-i", str(path), "-frames:v", "1", "-vf", "scale=320:-1", str(out_jpg)]
        try:
            r0 = subprocess.run(cmd0, check=False, capture_output=True, text=True)
        except (FileNotFoundError, OSError):
            return False
        return r0.returncode == 0 and out_jpg.exists()
    return True
