"""Media probing and vertical prep filters (ffmpeg only)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

_FMT = "format=yuv420p"
_PROBE_TIMEOUT = 30.0


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration_s: float
    width: int
    height: int
    has_audio: bool


def probe_media(path: str | Path) -> MediaInfo:
    """Return duration, dimensions, and audio presence for *path*."""
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(src)

    fmt_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(src),
    ]
    fmt = json.loads(
        subprocess.run(fmt_cmd, check=True, capture_output=True, text=True, timeout=_PROBE_TIMEOUT).stdout
    )
    duration_s = float(fmt.get("format", {}).get("duration", 0.0) or 0.0)

    stream_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(src),
    ]
    streams = json.loads(
        subprocess.run(stream_cmd, check=True, capture_output=True, text=True, timeout=_PROBE_TIMEOUT).stdout
    )
    video = (streams.get("streams") or [{}])[0]
    width = int(video.get("width") or 1080)
    height = int(video.get("height") or 1920)

    audio_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(src),
    ]
    audio = subprocess.run(audio_cmd, capture_output=True, text=True, timeout=_PROBE_TIMEOUT)
    has_audio = bool((audio.stdout or "").strip())

    return MediaInfo(
        path=src.resolve(),
        duration_s=duration_s,
        width=width,
        height=height,
        has_audio=has_audio,
    )


def vertical_filter_chain(
    video_in: str,
    *,
    width: int = 1080,
    height: int = 1920,
    out_label: str = "vprep",
) -> str:
    """Scale/crop to vertical 9:16 and normalise pixel format."""
    return (
        f"[{video_in}]{_FMT},scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1[{out_label}]"
    )
