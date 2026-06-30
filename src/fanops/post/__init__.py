"""Poster interface + factory. Backends: dryrun (default), postiz (free, self-hosted), zernio (hosted TikTok), rest, mcp (Blotato, being retired).
get_media_uploader dispatches the file->hosted-URL step per backend so publish_due
uploads to the right place (Blotato presign vs Postiz upload vs dryrun file://)."""
from __future__ import annotations
from pathlib import Path
from typing import Protocol, Callable
from fanops.config import Config
from fanops.ledger import Ledger

class Poster(Protocol):
    def publish(self, led: Ledger, post_id: str) -> Ledger: ...

def get_poster(cfg: Config, backend: str | None = None) -> "Poster":
    # ROOT FIX: a LIVE system asking for the dryrun poster is the bug that wrote 7 fake-published rows.
    # Refuse the bad construction. Live + backend='dryrun' (or fall-through to the legacy global which IS
    # 'dryrun') now RAISES — the publisher catches it and parks the post in needs_reconcile, the operator
    # sees the breadcrumb, no row is stamped published with a 'dryrun://' URL.
    resolved = backend or cfg.poster_backend
    if cfg.is_live and (resolved or "").lower() == "dryrun":
        raise RuntimeError(
            f"get_poster: refused to construct DryRunPoster on a LIVE system "
            f"(cfg.is_live=True, backend={resolved!r}). A per-channel provider must resolve to "
            f"postiz/zernio/etc., NOT dryrun. Fix the account's backends mapping in accounts.json.")
    from fanops.post.providers import get_provider
    provider = get_provider(cfg, resolved)
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
