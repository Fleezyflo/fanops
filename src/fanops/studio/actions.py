# src/fanops/studio/actions.py — CREATE
"""Lock-safe Studio mutations (no Flask). Each public action opens ONE Ledger.transaction and does
its existence + state(queued) + not-imminent guard + mutation INSIDE the lock, on the in-lock
freshly-loaded ledger — mirroring the CLI recovery verbs (cli.py:285,298) so it cannot lose-update
against a concurrent cron `fanops run`. Reads/normalization that can fail happen OUTSIDE the lock."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.timeutil import parse_iso, iso_z
from fanops.studio.views import _imminent

SNOOZE_DAYS = 365


@dataclass
class ActionResult:
    ok: bool
    error: Optional[str] = None
    detail: Optional[dict] = None


def _now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _normalize_z(new_time: str) -> str:
    """Parse an ISO time, COERCE naive -> UTC (iso_z would otherwise treat naive as LOCAL time),
    and re-emit the canonical ...Z aware form. Raises ValueError on unparseable input."""
    dt = parse_iso(new_time)                       # raises ValueError on garbage
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)       # explicit UTC coercion (never local-tz guess)
    return iso_z(dt)


def _guard_editable_post(led: Ledger, post_id: str, now: datetime):
    """Return (post, None) if post exists, is queued, and is not imminent; else (None, error)."""
    if post_id not in led.posts:
        return None, f"no such post: {post_id}"
    p = led.posts[post_id]
    if p.state is not PostState.queued:
        return None, f"post {post_id} is not queued (state={p.state.value}); only queued posts are editable"
    if _imminent(p.scheduled_time, now):
        return None, f"post {post_id} is imminent/already due — shipping now, cannot edit"
    return p, None


def reschedule_post(cfg: Config, post_id: str, new_time: str, *, now: Optional[datetime] = None) -> ActionResult:
    now = _now(now)
    try:
        z = _normalize_z(new_time)                 # OUTSIDE the lock: reject bad input early
    except (ValueError, TypeError) as exc:
        return ActionResult(ok=False, error=f"bad time {new_time!r}: {str(exc)[:120]}")
    with Ledger.transaction(cfg) as led:
        p, err = _guard_editable_post(led, post_id, now)
        if err:
            return ActionResult(ok=False, error=err)
        p.scheduled_time = z
    return ActionResult(ok=True, detail={"post_id": post_id, "scheduled_time": z})


def edit_caption(cfg: Config, post_id: str, caption: str, *, now: Optional[datetime] = None) -> ActionResult:
    now = _now(now)
    with Ledger.transaction(cfg) as led:
        p, err = _guard_editable_post(led, post_id, now)
        if err:
            return ActionResult(ok=False, error=err)
        p.caption = caption
    return ActionResult(ok=True, detail={"post_id": post_id, "caption": caption})


def snooze_clip(cfg: Config, clip_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """Push every non-imminent queued post of a clip ~SNOOZE_DAYS into the future, in ONE
    transaction (atomic — never a partial snooze). Inherits the same guard + normalization."""
    now = _now(now)
    z = iso_z(now + timedelta(days=SNOOZE_DAYS))
    with Ledger.transaction(cfg) as led:
        if clip_id not in led.clips:
            return ActionResult(ok=False, error=f"no such clip: {clip_id}")
        count = 0
        for p in led.posts.values():
            if p.parent_id == clip_id and p.state is PostState.queued and not _imminent(p.scheduled_time, now):
                p.scheduled_time = z
                count += 1
    return ActionResult(ok=True, detail={"clip_id": clip_id, "count": count, "scheduled_time": z})
