"""Subtle, NON-SYNCHRONIZED artist tagging. A minority of posts carry a buried @mohflow
(decided deterministically), and never two accounts within min_gap_minutes (tracked on
ledger.tag_log; writes are made durable by the ledger's atomic save). decide_tag() returns
whether THIS post may tag; crosspost (Task 16) appends the tag on its own line, never in the
hook. Wired into crosspost in Task 16 (v1 left this dead)."""
from __future__ import annotations
import hashlib
from datetime import datetime
from fanops.ledger import Ledger

ARTIST_HANDLE = "@mohflow"

def should_tag(clip_id: str, account: str, *, rate: float = 0.25) -> bool:
    h = int(hashlib.sha1(f"{clip_id}|{account}".encode()).hexdigest()[:8], 16)
    return (h % 1000) / 1000.0 < rate

def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

def decide_tag(led: Ledger, *, account: str, clip_id: str = "", when: datetime,
               rate: float = 0.25, min_gap_minutes: int = 120, force: bool = False) -> bool:
    # Per-(clip, account) probabilistic minority gate — NOT per-account constant: the clip_id
    # must vary so a buried @mention can appear on any account's posts over time (a fixed
    # per-account coin-flip would leave whole accounts permanently un-tagged). The time-window
    # below is the cross-account non-sync guard (an even tag cadence is itself a fingerprint).
    if not force and not should_tag(clip_id, account, rate=rate):
        return False
    for _, ts in led.tag_log.items():
        if abs((when - _parse(ts)).total_seconds()) / 60.0 < min_gap_minutes:
            return False
    led.tag_log[account] = when.isoformat().replace("+00:00", "Z")
    return True
