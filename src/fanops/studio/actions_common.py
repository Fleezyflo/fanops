"""Shared primitives for the Studio mutation layer (no Flask): the ActionResult outcome type, the
now-or-utc-now helper, and the deep-copy caption inheritor. Imports nothing from fanops.* — a leaf module
the action surface modules (run/approve) and the actions.py facade all depend on without an import cycle."""
from __future__ import annotations
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


def _inherit_captions(meta: dict | None) -> dict:
    """DEEP-copy a sibling clip's meta_captions for an inheriting clip (release_stitches / approve_with_hook).
    A shallow dict()/model_copy shares the inner {caption,hashtags} dicts, so a later in-place edit to one
    clip's caption would silently corrupt the other — defended here (latent today; captions are replaced, not
    mutated in place — but this makes it structural)."""
    return copy.deepcopy(meta or {})


@dataclass(frozen=True)
class ActionResult:
    """The outcome of one Studio action — frozen so a result can't be mutated after construction (every
    action returns a fresh one; no call site reassigns ok/error/detail). Construct directly or via the
    success()/failure() factories."""
    ok: bool
    error: Optional[str] = None
    detail: Optional[dict] = None

    @classmethod
    def success(cls, detail: Optional[dict] = None) -> "ActionResult":
        return cls(ok=True, detail=detail)

    @classmethod
    def failure(cls, error: str) -> "ActionResult":
        return cls(ok=False, error=error)


def _now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)
