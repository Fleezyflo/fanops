"""Live-library read-model for the Studio (ledger-rebuild MOL-27): the "viewed there, not authored here"
surface over led.imported_media. An ImportedMedia is a live IG post PROBED from the platform (M2 projection)
with NO clip lineage — it is NOT a Post, so it must NOT appear in the Posted library (authored, shipped). This
module is the DISJOINT peer read: it reads imported_media ONLY, never posts. Metrics (M3) are shown when
present, else '—'. Pure (no HTTP/Flask) — a lock-free ledger read; the projection/insights fill the data."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import LIFT_SCORE


@dataclass
class LiveMediaRow:
    media_id: str
    permalink: Optional[str]
    product_type: Optional[str]          # AD | FEED | STORY | REELS (None until resolved on the live media)
    timestamp: Optional[str]             # the media's live publish time (Graph `timestamp`) — display/order
    caption: Optional[str]               # the live caption text (display-only), when the probe returns it
    account: Optional[str]               # the credentialed handle this media was enumerated under (scope label)
    imported_at: Optional[str]           # wall-clock ISO-Z when first mirrored into the ledger (audit)
    error_reason: Optional[str]          # breadcrumb for an unresolved product_type / a transient insights miss
    # the M3 metric breakdown (track._W keys), read from the LATEST snapshot (ImportedMedia.metrics). Absent
    # key -> None -> the template renders "—". A row before insights ran carries NO metrics (all None).
    lift_score: Optional[float] = None
    saves: Optional[float] = None
    shares: Optional[float] = None
    retention: Optional[float] = None
    reach: Optional[float] = None


def live_library(led: Ledger, cfg: Config) -> list[LiveMediaRow]:
    """The Live library: every ImportedMedia row (a live-only IG post mirrored by the M2 projection),
    newest live-timestamp first (unstamped rows last). Lock-free read over led.imported_media ONLY — an
    authored Post is NEVER here (that is the Posted library). Metrics are the M3 snapshot when filled."""
    rows = [LiveMediaRow(media_id=im.media_id, permalink=im.permalink, product_type=im.product_type,
                         timestamp=im.timestamp, caption=im.caption, account=im.account,
                         imported_at=im.imported_at, error_reason=im.error_reason,
                         lift_score=im.metrics.get(LIFT_SCORE), saves=im.metrics.get("saves"),
                         shares=im.metrics.get("shares"), retention=im.metrics.get("retention"),
                         reach=im.metrics.get("reach")) for im in led.imported_media.values()]
    # newest live-media first; a media with no timestamp sorts last (key[0]=0), stable within each bucket.
    rows.sort(key=lambda r: (1, r.timestamp) if r.timestamp else (0, ""), reverse=True)
    return rows


def live_library_scope(cfg: Config) -> str:
    """The single-credential scope label stated on the surface (PRD credential-scope note): the projection
    enumerates ONE credentialed handle's media (META_IG_USER_ID). Never blank — a no-creds label still
    renders so the surface always tells the operator WHOSE live media this is (or that none is scoped)."""
    handle = cfg.meta_ig_user_id
    if handle:
        return f"Scoped to the credentialed Instagram account ({handle})."
    return "No Instagram account is connected — connect one on the Go Live tab to mirror its live media."
