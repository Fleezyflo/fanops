"""Poster interface + factory. Backends: dryrun (default), postiz (free, self-hosted IG/YouTube),
zernio (hosted TikTok). get_media_uploader dispatches the file->hosted-URL step per backend so
publish_due uploads to the right place (Postiz upload vs Zernio upload vs dryrun file://)."""
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
    Zernio upload. Resolved through the provider registry. An UNKNOWN backend falls back to the dryrun
    uploader (a fail-safe file:// URL, not a crash) — no live account routes to an unknown backend."""
    from fanops.post.providers import get_provider
    provider = get_provider(cfg, backend or cfg.poster_backend)
    if provider is not None:
        return provider.make_uploader(cfg)
    from fanops.post.providers import _dryrun_uploader
    return _dryrun_uploader(cfg)
