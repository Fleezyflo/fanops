# src/fanops/ingest.py
"""Ingest Moh's OWN videos: drop (01_inbox), url (yt-dlp), local scan. Identity is the
CONTENT sha256 (FIX F35). Probe width/height/duration at ingest for safe reframe (FIX F68).
Exclude PII/legal/financial by name — necessary but NOT sufficient (FIX F46): a private file
misnamed slips through; a human still reviews held/odd clips before posting."""
from __future__ import annotations
import hashlib, re, shutil, subprocess
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.ids import make_id

MEDIA_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi",
             ".jpg", ".jpeg", ".png", ".heic", ".mp3", ".wav", ".m4a"}
_PII = re.compile(r"passport|\bid\b|\bvisa\b|licen[cs]e|agreement|contract|invoice|"
                  r"\bnda\b|tax|bank|ssn|emirates.?id|national.?id", re.IGNORECASE)

def is_excluded(name: str) -> bool:
    return bool(_PII.search(name))

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def probe_dimensions(path: Path) -> tuple[int, int, float]:
    """(width, height, duration_seconds) via ffprobe; zeros on failure."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True)
    vals = [x for x in r.stdout.split() if x]
    try:
        w = int(float(vals[0])); h = int(float(vals[1])); dur = float(vals[2])
        return w, h, dur
    except (IndexError, ValueError):
        return 0, 0, 0.0

def ingest_drops(led: Ledger, cfg: Config, *, origin: str = "drop") -> Ledger:
    cfg.sources.mkdir(parents=True, exist_ok=True)
    for f in sorted(cfg.inbox.rglob("*")):
        if not f.is_file() or f.name == ".gitkeep" or f.suffix.lower() not in MEDIA_EXT:
            continue
        if is_excluded(f.name):
            continue
        digest = sha256_of(f)
        if led.already_seen(sha256=digest):
            continue
        sid = make_id("src", digest)              # identity = content, not path
        dest = cfg.sources / f"{sid}{f.suffix.lower()}"
        if not dest.exists():
            shutil.copy2(f, dest)
        w, h, dur = probe_dimensions(dest)
        led.add_source(Source(id=sid, state=SourceState.catalogued, source_path=str(dest),
                              source_origin=origin, sha256=digest, width=w, height=h,
                              duration=dur or None,
                              meta={"original_name": f.name, "bytes": f.stat().st_size}))
    return led

def download_source(led: Ledger, cfg: Config, url: str) -> Ledger:
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    subprocess.run(["yt-dlp", "-o", str(cfg.inbox / "%(title).80s.%(ext)s"),
                    "--no-playlist", "--merge-output-format", "mp4", url],
                   check=False, capture_output=True, text=True)
    return ingest_drops(led, cfg, origin="url")

def scan_local(roots: list[Path]) -> list[str]:
    out: list[str] = []
    for root in roots:
        for f in Path(root).rglob("*"):
            if f.is_file() and f.suffix.lower() in MEDIA_EXT and not is_excluded(f.name):
                out.append(str(f))
    return sorted(out)
