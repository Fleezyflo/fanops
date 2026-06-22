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

def get_poster(cfg: Config, backend: str | None = None) -> "Poster":
    # `backend` defaults to the global cfg.poster_backend (back-compat: existing callers pass nothing ->
    # byte-identical). An explicit per-account backend (Zernio slice 2) lets one publish_due run send IG
    # through Postiz and TikTok through Zernio at once. M1: resolved through the provider registry; an
    # UNKNOWN backend still falls back to DryRunPoster (the exact old behavior — note the uploader's
    # unknown-fallback is Blotato, a deliberate asymmetry preserved below).
    from fanops.post.providers import get_provider
    provider = get_provider(cfg, backend or cfg.poster_backend)
    if provider is not None:
        return provider.make_poster(cfg)
    from fanops.post.dryrun import DryRunPoster
    return DryRunPoster(cfg)

def get_media_uploader(cfg: Config, backend: str | None = None) -> Callable[[Config, Path], str]:
    """Return the (cfg, Path) -> hosted-URL function for `backend` (defaults to the global
    cfg.poster_backend — back-compat). dryrun -> file:// (no network); postiz -> Postiz upload; zernio ->
    Zernio upload; rest/mcp -> Blotato presign. M1: resolved through the provider registry. An UNKNOWN
    backend falls back to the Blotato presign uploader (the exact old else-branch — NOT dryrun; this
    differs from get_poster's dryrun fallback, an asymmetry the registry preserves)."""
    from fanops.post.providers import get_provider
    provider = get_provider(cfg, backend or cfg.poster_backend)
    if provider is not None:
        return provider.make_uploader(cfg)
    from fanops.post.media import upload_media
    return upload_media
