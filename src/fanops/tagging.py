"""Subtle, NON-SYNCHRONIZED artist tagging. A minority of posts carry a buried @mohflow
(decided deterministically), and never two accounts within min_gap_minutes (tracked on
ledger.tag_log, keyed per (account,clip) so a re-tag can't overwrite a time the window still
needs — AUDIT H3; writes are made durable by the ledger's atomic save). decide_tag() returns
whether THIS post may tag; crosspost (Task 16)
appends the tag on its own line, never in the hook. Wired into crosspost in Task 16 (v1 left
this dead)."""
from __future__ import annotations
import hashlib
from datetime import datetime
from fanops.ledger import Ledger
from fanops.timeutil import parse_iso as _parse

ARTIST_HANDLE = "@mohflow"

def should_tag(clip_id: str, account: str, *, rate: float = 0.25) -> bool:
    h = int(hashlib.sha1(f"{clip_id}|{account}".encode()).hexdigest()[:8], 16)
    return (h % 1000) / 1000.0 < rate

def decide_tag(led: Ledger, *, account: str, clip_id: str = "", when: datetime,
               rate: float = 0.25, min_gap_minutes: int = 120, force: bool = False) -> bool:
    # Per-(clip, account) probabilistic minority gate — NOT per-account constant: the clip_id
    # must vary so a buried @mention can appear on any account's posts over time (a fixed
    # per-account coin-flip would leave whole accounts permanently un-tagged). The time-window
    # below is the cross-account non-sync guard (an even tag cadence is itself a fingerprint).
    if not force and not should_tag(clip_id, account, rate=rate):
        return False
    # De-cluster against EVERY recorded tag time. AUDIT H3: tag_log was keyed per account, so an
    # account re-tagging OVERWROTE its earlier time — erasing a timestamp another account should
    # still be de-clustered against (a hole in the cross-account window). Key per (account,clip)
    # instead: each accepted tag keeps its own entry, nothing is overwritten.
    #
    # NOTE: we deliberately do NOT prune entries here by `when`. crosspost evaluates surfaces in
    # account/platform order, NOT chronologically, so a later call can carry an EARLIER `when` that
    # still needs to be de-clustered against a tag time a `when`-relative prune would have dropped
    # (that exact race produced a false "allow" in testing). Growth is bounded elsewhere (gc /
    # ledger lifecycle), never by discarding a time a future out-of-order decision may need.
    for ts in led.tag_log.values():
        if abs((when - _parse(ts)).total_seconds()) / 60.0 < min_gap_minutes:
            return False
    led.tag_log[f"{account}|{clip_id}"] = when.isoformat().replace("+00:00", "Z")
    return True
