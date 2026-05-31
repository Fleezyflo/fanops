"""Poster interface + factory. Backends: dryrun (default), rest, mcp."""
from __future__ import annotations
from typing import Protocol
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
    from fanops.post.dryrun import DryRunPoster
    return DryRunPoster(cfg)
