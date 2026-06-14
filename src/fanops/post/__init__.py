"""Poster interface + factory. Backends: dryrun (default), rest, mcp (Blotato), postiz (free,
self-hosted). get_media_uploader dispatches the file->hosted-URL step per backend so publish_due
uploads to the right place (Blotato presign vs Postiz upload vs dryrun file://)."""
from __future__ import annotations
from pathlib import Path
from typing import Protocol, Callable
from fanops.config import Config
from fanops.ledger import Ledger

class Poster(Protocol):
    def publish(self, led: Ledger, post_id: str) -> Ledger: ...

def get_poster(cfg: Config) -> "Poster":
    backend = cfg.poster_backend
    if backend == "rest":
        from fanops.post.blotato_rest import BlotatoRestPoster
        return BlotatoRestPoster(cfg)
    if backend == "mcp":
        from fanops.post.blotato_mcp import BlotatoMcpPoster
        return BlotatoMcpPoster(cfg)
    if backend == "postiz":
        from fanops.post.postiz import PostizPoster
        return PostizPoster(cfg)
    from fanops.post.dryrun import DryRunPoster
    return DryRunPoster(cfg)

def get_media_uploader(cfg: Config) -> Callable[[Config, Path], str]:
    """Return the (cfg, Path) -> hosted-URL function for the active backend. dryrun -> file:// (no
    network); postiz -> Postiz upload (uploads.postiz.com); rest/mcp -> Blotato presign. Lazy imports
    keep the core importable without optional deps and avoid an import cycle with media.py."""
    backend = cfg.poster_backend
    if backend == "postiz":
        from fanops.post.postiz import postiz_upload_media
        return postiz_upload_media
    if backend == "dryrun":
        from fanops.post.media import dryrun_media_url
        return lambda c, p: dryrun_media_url(p)
    from fanops.post.media import upload_media
    return upload_media
