"""Media/thumb serve route group for the Studio: clip/source/post previews and thumbnails.

register_media_routes(app, cfg) registers them under their ORIGINAL endpoint names
(url_for byte-identical); create_app calls it."""
from __future__ import annotations

import os
import re

from flask import abort, send_file

from fanops.ledger import Ledger
from fanops.studio.app_request import _bounded
from fanops.studio.preview_media import media_path_for_post


def register_media_routes(app, cfg):
    @app.get("/review-thumb/<eid>")
    def review_thumb(eid):
        if "/" in eid or "\\" in eid or ".." in eid:     # bare stem only — no traversal
            abort(404)
        path = _bounded(cfg, cfg.review / f"{eid}.jpg")  # must resolve inside cfg.base
        if not path or not os.path.exists(path):
            abort(404)
        return send_file(path)

    # Media serve is state-agnostic: missing file → 404. After cmd_gc reclaims a suppressed clip's .mp4
    # (incl. under a failed/error post — MOL-818), that 404 is expected; do not special-case post state here.
    @app.get("/media/<post_id>")
    def media(post_id):
        path = _bounded(cfg, media_path_for_post(cfg, Ledger.load(cfg), post_id))
        if not path or not os.path.exists(path):
            abort(404)
        return send_file(path)

    @app.get("/media-preview/<post_id>")
    def media_preview(post_id):
        path = _bounded(cfg, media_path_for_post(cfg, Ledger.load(cfg), post_id))
        if not path or not os.path.exists(path):
            abort(404)
        return send_file(path)

    @app.get("/clips/<clip_id>")
    def clip_media(clip_id):
        from fanops.post.media import resolve_media_path
        clip = Ledger.load(cfg).clips.get(clip_id)
        raw = clip.path if clip else None
        resolved = resolve_media_path(cfg, raw, "clip") if raw else None
        path = _bounded(cfg, str(resolved) if resolved else None)
        if not path or not os.path.exists(path):
            abort(404)
        return send_file(path)

    @app.get("/source-media/<source_id>")
    def source_media(source_id):
        if "/" in source_id or "\\" in source_id or ".." in source_id or not re.fullmatch(r"[\w.-]+", source_id):
            abort(404)
        src = Ledger.load(cfg).sources.get(source_id)
        from fanops.post.media import resolve_media_path
        raw = src.source_path if src else None
        resolved = resolve_media_path(cfg, raw, "source") if raw else None
        path = _bounded(cfg, str(resolved) if resolved else None)
        if not path or not os.path.exists(path):
            abort(404)
        return send_file(path)

    @app.get("/keyframe/<source_id>/<name>")
    @app.get("/keyframe/<source_id>/<whash>/<name>")
    def keyframe(source_id, name, whash=None):
        if "/" in source_id or "\\" in source_id or ".." in source_id or not re.fullmatch(r"[\w.-]+", source_id):
            abort(404)
        if not re.fullmatch(r"(grid|kf)_[\w-]+\.jpg", name):
            abort(404)
        if whash is not None and not re.fullmatch(r"[0-9a-f]{64}", whash):
            abort(404)
        base = cfg.agent_io / "keyframes" / source_id
        candidate = base / whash / name if whash else base / name
        path = _bounded(cfg, candidate)
        if not path or not path.exists():
            abort(404)
        return send_file(path, mimetype="image/jpeg")

    @app.get("/thumb/source/<source_id>")
    def thumb_source(source_id):
        from fanops.studio.thumb_media import resolve_source_thumb
        return resolve_source_thumb(cfg, source_id)

    @app.get("/thumb/clip/<clip_id>")
    def thumb_clip(clip_id):
        from fanops.studio.thumb_media import resolve_clip_thumb
        return resolve_clip_thumb(cfg, clip_id)

    @app.get("/clip-thumb/<clip_id>")
    def clip_thumb(clip_id):
        from fanops.studio.thumb_media import resolve_clip_thumb
        return resolve_clip_thumb(cfg, clip_id)
