"""Ingest source collection for Trial Reels."""
from __future__ import annotations

import argparse
from pathlib import Path

FANOPS_CLIPS_ROOT = Path("/Users/molhamhomsi/FanOps/MohFlow-FanOps/03_clips")

MEDIA_SUFFIXES = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".m4v",
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
}


def _is_media(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES


def _collect_from_dir(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p.resolve() for p in directory.iterdir() if _is_media(p))


def _resolve_fanops_clip(source_id: str) -> Path | None:
    if not FANOPS_CLIPS_ROOT.is_dir():
        return None
    for candidate in FANOPS_CLIPS_ROOT.rglob(f"*{source_id}*"):
        if _is_media(candidate):
            return candidate.resolve()
    direct = FANOPS_CLIPS_ROOT / source_id
    if _is_media(direct):
        return direct.resolve()
    return None


def collect_sources(args: argparse.Namespace) -> list[Path]:
    """Collect media paths from ingest CLI shapes.

    Shapes:
    - ``--file`` single media file
    - ``--folder`` all media in a directory
    - no positional/file/folder/from_fanops → drain ``in/`` (``args.in_dir``)
    - ``--from-fanops`` id under ``FANOPS_CLIPS_ROOT``
    """
    sources: list[Path] = []

    file_arg = getattr(args, "file", None)
    folder_arg = getattr(args, "folder", None)
    from_fanops = getattr(args, "from_fanops", None)
    in_dir = Path(getattr(args, "in_dir", "in"))

    if file_arg:
        path = Path(file_arg)
        if _is_media(path):
            sources.append(path.resolve())

    if folder_arg:
        sources.extend(_collect_from_dir(Path(folder_arg)))

    if from_fanops:
        resolved = _resolve_fanops_clip(str(from_fanops))
        if resolved is not None:
            sources.append(resolved)

    if not file_arg and not folder_arg and not from_fanops:
        sources.extend(_collect_from_dir(in_dir))

    seen: set[Path] = set()
    unique: list[Path] = []
    for src in sources:
        if src not in seen:
            seen.add(src)
            unique.append(src)
    return unique
