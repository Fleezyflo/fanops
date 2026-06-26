"""P3 — the post-publish metrics polling cadence. A pure, clock-injected selector: given a post's
publish time, the offsets already captured, and `now`, return the single cadence offset whose snapshot
is newly DUE (or None). The cadence is the FIXED operator spec — NOT a tunable knob, NOT a scheduler
(WHO triggers a poll is unchanged: `fanops track`/`run`; this module only decides WHICH offset a poll
captures). No I/O, no mutation, never raises on a None/naive/malformed published_at."""
from __future__ import annotations
from datetime import datetime
from typing import Iterable, Optional

# The fixed schedule: 4h,12h,24h,72h within the first days; then WEEKLY to one month (1w..4w); then
# MONTHLY (~4-week steps) to one year (8w..52w). 20 offsets total. Encoded once, here.
CADENCE_OFFSETS = ("4h", "12h", "24h", "72h", "1w", "2w", "3w", "4w",
                   "8w", "12w", "16w", "20w", "24w", "28w", "32w", "36w",
                   "40w", "44w", "48w", "52w")

_UNIT_SECONDS = {"h": 3600, "w": 604800}


def offset_seconds(offset: str) -> int:
    """'4h' -> 14400, '1w' -> 604800. Only ever called on CADENCE_OFFSETS members (the 'legacy'
    migration tag is never passed here — due_offset compares it as a plain string, never by seconds)."""
    return int(offset[:-1]) * _UNIT_SECONDS[offset[-1]]


def _parse_pub(published_at) -> Optional[datetime]:
    # Tolerant parse: a None / non-str / malformed / NAIVE (no tzinfo) published_at -> None, never a
    # raise and never a local-time guess (mirrors _migrate_v3_created_at / the pipeline heartbeat guard).
    if not published_at or not isinstance(published_at, str):
        return None
    from fanops.timeutil import parse_iso
    try:
        dt = parse_iso(published_at)
    except (ValueError, TypeError, AttributeError):
        return None
    return dt if dt.tzinfo is not None else None


def due_offset(published_at, captured: Iterable[str], now: datetime) -> Optional[str]:
    """The single cadence offset newly DUE for `published_at` at `now`, or None. Latest-due-wins: pick
    the LATEST offset whose elapsed time has passed; if it is already in `captured` -> None (nothing new
    is due — we NEVER backfill an earlier skipped offset). None published_at / nothing elapsed yet /
    full series captured -> None. Pure + clock-injected; `now` is supplied by the caller (tests inject)."""
    pub = _parse_pub(published_at)
    if pub is None:
        return None
    elapsed = (now - pub).total_seconds()
    captured = set(captured)
    due = None
    for off in CADENCE_OFFSETS:
        if offset_seconds(off) <= elapsed:
            due = off
        else:
            break                                    # offsets are increasing; nothing further is elapsed
    if due is None or due in captured:
        return None
    return due
