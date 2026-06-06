"""Pure read-model builders for the Studio (no HTTP, no Flask). Each request re-loads the ledger
(lock-free) and assembles these dataclasses; templates render them. Mutations live in actions.py."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.timeutil import parse_iso

IMMINENT_THRESHOLD_MINUTES = 5     # spec §4: a post within this of now (or past) is edit-disabled
RECENT_WINDOW_HOURS = 24           # spec §6: "what just shipped" read-only context window


@dataclass
class SurfacePost:
    post_id: str
    account: str
    platform: str
    persona: Optional[str]
    caption: str
    hashtags: list[str]
    scheduled_time: Optional[str]
    media_url: str
    state: str
    imminent: bool
    editable: bool


@dataclass
class ReviewCard:
    clip_id: str
    preview_url: str
    source_name: str
    moment_window: str
    reason: str
    language: Optional[str]
    subtitles_burned: bool
    held: bool
    held_reason: Optional[str]
    transcript_excerpt: Optional[str]
    surfaces: list[SurfacePost]
    bucket: str


@dataclass
class ScheduleRow:
    post_id: str
    scheduled_time: Optional[str]
    account: str
    platform: str
    clip_id: str
    state: str
    imminent: bool
    editable: bool


@dataclass
class LiftRow:
    variant_hook: Optional[str]
    account: str
    platform: str
    lift_score: float
    loop_state: str
    amplify_state: Optional[str] = None


@dataclass
class LiftView:
    variant_rows: list[LiftRow]
    variant_empty_reason: Optional[str]
    amplify_present: bool
    amplify_rows: list[LiftRow]
    amplify_empty_reason: Optional[str]


def _imminent(scheduled_time: Optional[str], now: datetime,
              threshold_min: int = IMMINENT_THRESHOLD_MINUTES) -> bool:
    """True (edit-disabled) when the time is missing, unparseable, naive, already due, or within
    `threshold_min` of `now`. Fail-safe: any doubt -> imminent (read-only), never editable. `now`
    must be timezone-aware UTC."""
    if not scheduled_time:
        return True
    try:
        dt = parse_iso(scheduled_time)
    except (ValueError, TypeError):
        return True
    if dt.tzinfo is None:
        return True
    return dt <= now + timedelta(minutes=threshold_min)
