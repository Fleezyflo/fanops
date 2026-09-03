"""Lock-free media resolution for Studio serve + Review WYSIWYG preview."""
from __future__ import annotations

from fanops.config import Config
from fanops.errors import fail_open
from fanops.ledger import Ledger
from fanops.post.media import resolve_media_path


def media_path_for_post(cfg: Config, led: Ledger, post_id: str) -> str | None:
    """Resolve the local file to serve for a post — a pure lookup, no guessing (the Render foundation
    killed the old 3-way heuristic that silently served a textless base):
      1. post.render_id -> the per-account Render's path (THE authoritative per-account artifact);
      2. else media_urls[0] when it is a local file:// / bare path (legacy pre-Render rows; resilient if
         a Render entity was swept but its file remains — media_urls still points at the same path);
      3. else the shared base clip.path (a hookless surface legitimately ships the base).
    An http(s) media_urls (an already-published URL) is NOT locally servable -> fall through. The id is a
    dict-key lookup and every path comes from the trusted ledger (never the URL), so no path traversal.
    The route 404s when the resolved path does not exist (a missing render surfaces, never a silent swap)."""
    with fail_open("studio.preview_media.media_path_for_post"):
        post = led.posts.get(post_id)
        if post is None:
            return None
        if post.render_id:
            r = led.renders.get(post.render_id)
            if r is not None and r.path:
                p = resolve_media_path(cfg, r.path, "render")
                return str(p) if p else None
        candidate = None
        kind = "render" if post.render_id else "clip"
        if post.media_urls:
            raw = post.media_urls[0]
            if raw.startswith("file://"):
                candidate = raw[len("file://"):]
            elif not raw.startswith(("http://", "https://")):
                candidate = raw            # a bare local path
            # http(s) publicUrl -> not locally servable; fall through to base clip
        if candidate is not None:
            p = resolve_media_path(cfg, candidate, kind)
            return str(p) if p else None
        clip = led.clips.get(post.parent_id)
        if clip and clip.path:
            p = resolve_media_path(cfg, clip.path, "clip")
            return str(p) if p else None
    return None


preview_media_path = media_path_for_post
