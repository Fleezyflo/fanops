"""Lock-free preview for Review WYSIWYG — serves the owner-moment clip (hook burned at render_moment)."""
from __future__ import annotations
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger


def preview_media_path(cfg: Config, led: Ledger, post_id: str) -> str | None:
    post = led.posts.get(post_id)
    if post is None:
        return None
    if post.render_id:
        r = led.renders.get(post.render_id)
        if r and r.path and Path(r.path).exists():
            return r.path
    clip = led.clips.get(post.parent_id)
    if clip is None:
        return None
    if post.media_urls:
        raw = post.media_urls[0]
        if raw.startswith("file://"):
            lp = raw[7:]
            if Path(lp).exists():
                return lp
        elif not raw.startswith(("http://", "https://")) and Path(raw).exists():
            return raw
    return clip.path if clip.path and Path(clip.path).exists() else None
