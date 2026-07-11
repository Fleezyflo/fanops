"""Lock-free preview for Review WYSIWYG — serves the owner-moment clip (hook burned at render_moment)."""
from __future__ import annotations
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.post.media import resolve_media_path


def preview_media_path(cfg: Config, led: Ledger, post_id: str) -> str | None:
    post = led.posts.get(post_id)
    if post is None:
        return None
    if post.render_id:
        r = led.renders.get(post.render_id)
        if r and r.path:
            p = resolve_media_path(cfg, r.path, "render")
            if p:
                return str(p)
    clip = led.clips.get(post.parent_id)
    if clip is None:
        return None
    if post.media_urls:
        raw = post.media_urls[0]
        if raw.startswith("file://"):
            stored = raw[7:]
        elif not raw.startswith(("http://", "https://")):
            stored = raw
        else:
            stored = None
        if stored:
            kind = "render" if post.render_id else "clip"
            p = resolve_media_path(cfg, stored, kind)
            if p:
                return str(p)
    if clip.path:
        p = resolve_media_path(cfg, clip.path, "clip")
        return str(p) if p else None
    return None
