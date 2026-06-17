# src/fanops/ingest.py
"""Ingest Moh's OWN videos: drop (01_inbox), url (yt-dlp), local scan. Identity is the
CONTENT sha256 (FIX F35). Probe width/height/duration at ingest for safe reframe (FIX F68).
Exclude PII/legal/financial by name — necessary but NOT sufficient (FIX F46): a private file
misnamed slips through; a human still reviews held/odd clips before posting."""
from __future__ import annotations
import hashlib, re, shutil, subprocess
from pathlib import Path
from typing import Literal
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.models import Source, SourceState
from fanops.ids import make_id
from fanops.errors import ToolchainMissingError, DownloadError

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

# Hard bounds (the llm.py timeout idiom). ffprobe is a sub-second metadata read — a hang means a
# corrupt file or stuck mount, and ingest runs INSIDE advance()'s transaction with no per-unit
# quarantine, so it must fail soft per file, fast. yt-dlp is a full network download (NO lock
# held — see download_url) but `fanops pull` still must not hang forever on a dead CDN.
_FFPROBE_TIMEOUT = 30.0
_YTDLP_TIMEOUT = 600.0

def _run_ffprobe(args: list[str]) -> subprocess.CompletedProcess:
    """Run ffprobe, translating a PRE-LAUNCH FileNotFoundError/OSError (ffprobe absent from PATH)
    into a typed, cli-catchable ToolchainMissingError. `check=False`-style: a nonzero ffprobe
    RETURNCODE is NOT an error here (callers interpret stdout, defaulting to 0/False) — only the
    binary being ABSENT is. This runs at ingest, OUTSIDE the pipeline's per-unit quarantine, so an
    uncaught raise would crash `fanops advance` with a traceback; the typed error -> clean exit 2."""
    try:
        return subprocess.run(["ffprobe", *args], capture_output=True, text=True,
                              timeout=_FFPROBE_TIMEOUT)
    except (FileNotFoundError, OSError) as e:
        raise ToolchainMissingError(
            "ffprobe not found on PATH — install ffmpeg (it provides ffprobe) to ingest media "
            f"({type(e).__name__})") from e
    except subprocess.TimeoutExpired:
        # PER-FILE hang (corrupt media, stuck mount) — NOT the binary-absent case: raising here
        # would abort the whole ingest pass and roll back the transaction over one bad file.
        # Fail SOFT with an empty result instead: probe_dimensions -> zeros (its documented
        # failure shape), has_video_stream -> False — the file stays in the inbox and is retried
        # next pass, bounded each time, never a crash or a dropped pass.
        return subprocess.CompletedProcess(["ffprobe", *args], returncode=124,
                                           stdout="", stderr="ffprobe timed out")

def probe_dimensions(path: Path) -> tuple[int, int, float]:
    """(width, height, duration_seconds) via ffprobe; zeros on failure (ffprobe ABSENT raises
    ToolchainMissingError — see _run_ffprobe — rather than masquerading as a 0×0 source)."""
    r = _run_ffprobe(
        ["-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    vals = [x for x in r.stdout.split() if x]
    try:
        w = int(float(vals[0])); h = int(float(vals[1])); dur = float(vals[2])
        return w, h, dur
    except (IndexError, ValueError):
        return 0, 0, 0.0

def has_video_stream(path: Path) -> bool:
    """True if the file carries a decodable video stream (a still image counts — it has a
    video-type stream). Audio-only files (.wav/.mp3/.m4a with no picture) return False. Used
    to keep audio-only drops out of the clip pipeline: ffmpeg's reframe -vf is silently
    ignored on an audio-only input, so without this guard the renderer emits a *videoless*
    'clip' (audio masquerading as a 9:16 post) — a real data-integrity bug confirmed on
    ffmpeg 8.0.1. Audio extensions stay in MEDIA_EXT for a future audiogram path; they just
    aren't catalogued as clip sources today. ffprobe ABSENT raises ToolchainMissingError (via
    _run_ffprobe) — we must NOT return False on a missing binary, which would silently DROP a
    real video as if it were audio-only."""
    r = _run_ffprobe(
        ["-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)])
    # `csv=p=0` emits "video," (trailing empty field) on some HEVC .mov muxings — exact `== "video"`
    # would then read it as audio-only and silently DROP a real clip. Token-match instead: True iff a
    # "video" codec_type appears among the comma/space-separated fields; empty stdout -> still False.
    return "video" in r.stdout.replace(",", " ").split()

def _catalogue_file(led: Ledger, cfg: Config, f: Path, *, origin: str,
                    origin_kind: Literal["native", "third_party"] = "native") -> None:
    """Catalogue ONE file as a Source (content-addressed, deduped, probed) — the single spine shared by
    the native drop/url scan and the third-party intake; the caller sets origin_kind. Same bytes already
    seen under a DIFFERENT origin_kind = a conflict: keep the first (origin_kind is WRITE-ONCE), surface
    it via a visible log line, never silently flip native<->third_party."""
    digest = sha256_of(f)
    if led.already_seen(sha256=digest):
        prior = next((s for s in led.sources.values() if s.sha256 == digest), None)
        if prior is not None and prior.origin_kind != origin_kind:   # dedup-suppressed an upload — make it visible
            get_logger(cfg)("ingest", prior.id, "origin_conflict", want=origin_kind, have=prior.origin_kind)
        return
    sid = make_id("src", digest)                  # identity = content, not path
    dest = cfg.sources / f"{sid}{f.suffix.lower()}"
    if not dest.exists():
        shutil.copy2(f, dest)
    w, h, dur = probe_dimensions(dest)
    led.add_source(Source(id=sid, state=SourceState.catalogued, source_path=str(dest),
                          source_origin=origin, origin_kind=origin_kind, sha256=digest, width=w, height=h,
                          duration=dur or None,
                          meta={"bytes": f.stat().st_size}))   # AUDIT: no original_name (PII)

def ingest_drops(led: Ledger, cfg: Config, *, origin: str = "drop",
                 origin_kind: Literal["native", "third_party"] = "native",
                 inbox: Path | None = None) -> Ledger:
    cfg.sources.mkdir(parents=True, exist_ok=True)
    for f in sorted((inbox or cfg.inbox).rglob("*")):     # inbox= lets third-party scan its own staging dir
        if not f.is_file() or f.name == ".gitkeep" or f.suffix.lower() not in MEDIA_EXT:
            continue
        if is_excluded(f.name):
            continue
        if not has_video_stream(f):
            continue                              # audio-only (no video stream): not a clip source (FIX)
        _catalogue_file(led, cfg, f, origin=origin, origin_kind=origin_kind)
    return led

def download_url(cfg: Config, url: str) -> None:
    """Network-only half of a URL pull: shell yt-dlp to drop the media into the inbox. Holds NO
    ledger lock — the slow download must run OUTSIDE the ledger transaction (cmd_pull then ingests
    what landed inside a tight transaction), so a download never serializes behind the ledger flock
    (the Phase-B-followup lost-update + no-network-under-lock rule). yt-dlp ABSENT from PATH:
    subprocess.run raises before the process starts (check=False covers only a nonzero RETURNCODE) —
    surface the typed, cli-catchable ToolchainMissingError (-> clean exit 2 + 'install yt-dlp').
    A HUNG download is killed at _YTDLP_TIMEOUT; the TimeoutExpired propagates BY DESIGN to
    cli.main's guard (one clean stderr line + exit 2). yt-dlp's partial *.part file is ignored by
    ingest (not in MEDIA_EXT), so a killed download never catalogues a truncated source."""
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(["yt-dlp", "-o", str(cfg.inbox / "%(title).80s.%(ext)s"),
                            "--no-playlist", "--merge-output-format", "mp4", url],
                           check=False, capture_output=True, text=True, timeout=_YTDLP_TIMEOUT)
    except (FileNotFoundError, OSError) as e:
        raise ToolchainMissingError(
            f"yt-dlp not found on PATH — install yt-dlp to pull from a URL ({type(e).__name__})") from e
    if r.returncode != 0:
        # yt-dlp RAN but failed (dead/geoblocked URL, format gone). check=False only covers the
        # binary-absent case above; without this the rc+stderr were discarded and `pull` silently
        # ingested nothing, printing "pulled -> 0 sources" as success (audit silent-failure). Surface
        # the typed DownloadError with the stderr tail -> cli.main: one clean line + exit 2.
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        why = tail[-1][:200] if tail else f"exit {r.returncode}"
        raise DownloadError(f"yt-dlp failed (exit {r.returncode}): {why}")


def download_source(led: Ledger, cfg: Config, url: str) -> Ledger:
    """Download + ingest in one call (kept for any direct caller/test). The CLI's `pull` command
    splits these (download outside the lock, ingest inside a transaction) — see cli.cmd_pull."""
    download_url(cfg, url)
    return ingest_drops(led, cfg, origin="url")

def scan_local(roots: list[Path]) -> list[str]:
    out: list[str] = []
    for root in roots:
        for f in Path(root).rglob("*"):
            if f.is_file() and f.suffix.lower() in MEDIA_EXT and not is_excluded(f.name):
                out.append(str(f))
    return sorted(out)
