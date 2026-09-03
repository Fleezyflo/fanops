# src/fanops/media_probe.py
"""Shared media probing: extension allowlist, PII name filter, content hash, ffprobe wrappers."""
from __future__ import annotations
import hashlib, re, subprocess
from pathlib import Path
from fanops.errors import ToolchainMissingError

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
# quarantine, so it must fail soft per file, fast.
_FFPROBE_TIMEOUT = 30.0

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
        # failure shape), has_video_stream -> None — the file stays in the inbox and is retried
        # next pass, bounded each time, never a crash or a dropped pass.
        return subprocess.CompletedProcess(["ffprobe", *args], returncode=124,
                                           stdout="", stderr="ffprobe timed out")

_FFPROBE_TIMEOUT_RC = 124

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

def has_video_stream(path: Path) -> bool | None:
    """True if the file carries a decodable video stream (a still image counts — it has a
    video-type stream). Audio-only files (.wav/.mp3/.m4a with no picture) return False. None when
    ffprobe timed out — the caller must leave the file in the inbox for retry, not archive it as
    audio-only. Used to keep audio-only drops out of the clip pipeline: ffmpeg's reframe -vf is
    silently ignored on an audio-only input, so without this guard the renderer emits a *videoless*
    'clip' (audio masquerading as a 9:16 post) — a real data-integrity bug confirmed on
    ffmpeg 8.0.1. Audio extensions stay in MEDIA_EXT for a future audiogram path; they just
    aren't catalogued as clip sources today. ffprobe ABSENT raises ToolchainMissingError (via
    _run_ffprobe) — we must NOT return False on a missing binary, which would silently DROP a
    real video as if it were audio-only."""
    r = _run_ffprobe(
        ["-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)])
    if r.returncode == _FFPROBE_TIMEOUT_RC:
        return None
    # `csv=p=0` emits "video," (trailing empty field) on some HEVC .mov muxings — exact `== "video"`
    # would then read it as audio-only and silently DROP a real clip. Token-match instead: True iff a
    # "video" codec_type appears among the comma/space-separated fields; empty stdout -> still False.
    return "video" in r.stdout.replace(",", " ").split()
