"""One-shot legacy Instagram `product_type` closure (MOL-792).

Every IG post whose `product_type` is None or "REELS" (Meta vocabulary) is set to the declared
service token "post". Zero network. Idempotent: a second run writes nothing. Unexpected non-None
values (FEED, STORY, AD, …) are left untouched and logged — no rescue machinery.
"""
from __future__ import annotations

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.models import Platform

_SERVICE = "post"
_LEGACY = (None, "REELS")


def close_legacy_product_type(cfg: Config) -> dict:
    """Close legacy IG product_type values once. One `Ledger.transaction`; IG only; model_copy only
    when the value changes. Returns `{closed, already_service, unexpected, skipped_non_ig}`."""
    log = get_logger(cfg)
    closed = already_service = unexpected = skipped_non_ig = 0
    with Ledger.transaction(cfg) as led:
        for pid, post in list(led.posts.items()):
            if post.platform is not Platform.instagram:
                skipped_non_ig += 1
                continue
            pt = post.product_type
            if pt == _SERVICE:
                already_service += 1
                continue
            if pt in _LEGACY:
                led.posts[pid] = post.model_copy(update={"product_type": _SERVICE})
                closed += 1
                continue
            unexpected += 1
            log("product_closure", pid, "unexpected_product_type", value=pt)
    return {
        "closed": closed,
        "already_service": already_service,
        "unexpected": unexpected,
        "skipped_non_ig": skipped_non_ig,
    }


def cmd_close_product_type(cfg: Config, args=None) -> int:
    """`fanops close-product-type` — headless one-shot. Counts via get_logger only (no print)."""
    counts = close_legacy_product_type(cfg)
    get_logger(cfg)("product_closure", "-", "report", **counts)
    return 0
