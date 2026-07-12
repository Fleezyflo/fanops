"""Thumbnail routes for Studio — source + clip posters (fail-open GIF fallback)."""
from __future__ import annotations
import logging
import os
import re
from pathlib import Path

from flask import Response, send_file

from fanops.discover import make_thumbnail
from fanops.ingest import probe_dimensions
from fanops.ledger import Ledger

_log = logging.getLogger("fanops.studio.thumb_media")

# 1×1 transparent GIF (43 bytes) — fail-open placeholder when extraction fails.
_TRANSPARENT_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
    b"\x00\x02\x02D\x01\x00;"
)


def _bounded(cfg, candidate) -> Path | None:
    if not candidate:
        return None
    p = Path(candidate).resolve()
    return p if p.is_relative_to(cfg.base.resolve()) else None


def _fallback_response(reason: str) -> Response:
    _log.warning("thumb fallback: %s", reason)
    return Response(_TRANSPARENT_GIF, mimetype="image/gif",
                    headers={"Cache-Control": "public, max-age=3600"})


def _serve_cached_jpg(cache: Path) -> Response:
    return send_file(cache, mimetype="image/jpeg")


def _extract_and_serve(src: Path, cache: Path, *, at_seconds: float) -> Response:
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not make_thumbnail(src, cache, at_seconds=at_seconds) or not cache.exists() or cache.stat().st_size == 0:
        return _fallback_response(f"extract failed for {src.name}")
    return _serve_cached_jpg(cache)


def resolve_source_thumb(cfg, source_id: str) -> Response:
    if "/" in source_id or "\\" in source_id or ".." in source_id or not re.fullmatch(r"[\w.-]+", source_id):
        return _fallback_response(f"bad source id: {source_id!r}")
    try:
        src_row = Ledger.load(cfg).sources.get(source_id)
        if src_row is None or not src_row.source_path:
            return _fallback_response(f"unknown source: {source_id}")
        from fanops.post.media import resolve_media_path
        raw = src_row.source_path
        resolved = resolve_media_path(cfg, raw, "source")
        video = _bounded(cfg, str(resolved) if resolved else None)
        if video is None or not video.exists():
            return _fallback_response(f"no video for source {source_id}")
        cache = _bounded(cfg, cfg.agent_io / "thumbs" / f"{source_id}.jpg")
        if cache is None:
            return _fallback_response(f"cache path escapes base for {source_id}")
        if cache.exists() and cache.stat().st_size > 0:
            return _serve_cached_jpg(cache)
        try:
            _pw, _ph, dur = probe_dimensions(video)
        except Exception:
            dur = None
        at = max(0.5, (dur or 0) * 0.1)
        return _extract_and_serve(video, cache, at_seconds=at)
    except Exception as exc:
        return _fallback_response(str(exc)[:120])


def resolve_clip_thumb(cfg, clip_id: str) -> Response:
    if "/" in clip_id or "\\" in clip_id or ".." in clip_id:
        return _fallback_response(f"bad clip id: {clip_id!r}")
    try:
        clip = Ledger.load(cfg).clips.get(clip_id)
        src = _bounded(cfg, clip.path if clip else None)
        if not src or not os.path.exists(src):
            return _fallback_response(f"no clip file for {clip_id}")
        cache = _bounded(cfg, cfg.clips / f"{clip_id}.jpg")
        if cache is None:
            return _fallback_response(f"cache path escapes base for {clip_id}")
        fresh = (cache.exists() and cache.stat().st_size > 0
                 and cache.stat().st_mtime >= os.path.getmtime(src))
        if fresh:
            return _serve_cached_jpg(cache)
        return _extract_and_serve(src, cache, at_seconds=0.5)
    except Exception as exc:
        return _fallback_response(str(exc)[:120])
