"""Studio schedule-tab mutations (no Flask)."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.audit import write_audit
from fanops.log import get_logger
from fanops.timeutil import parse_iso, iso_z
from fanops.studio.views import _imminent
from fanops.studio.actions_common import ActionResult, _now
from fanops.studio.actions_edit import _guard_editable_post

SNOOZE_DAYS = 365


def _normalize_z(new_time: str) -> str:
    """Parse an ISO time, COERCE naive -> UTC (iso_z would otherwise treat naive as LOCAL time),
    and re-emit the canonical ...Z aware form. Raises ValueError on unparseable input."""
    dt = parse_iso(new_time)                       # raises ValueError on garbage
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)       # explicit UTC coercion (never local-tz guess)
    return iso_z(dt)


def reschedule_post(cfg: Config, post_id: str, new_time: str, *, now: Optional[datetime] = None) -> ActionResult:
    now = _now(now)
    try:
        z = _normalize_z(new_time)                 # OUTSIDE the lock: reject bad input early
    except (ValueError, TypeError) as exc:
        return ActionResult(ok=False, error=f"bad time {new_time!r}: {str(exc)[:120]}")
    if parse_iso(z) <= now:
        return ActionResult(ok=False, error="scheduled time must be strictly in the future")
    with Ledger.transaction(cfg) as led:
        p, err = _guard_editable_post(led, post_id, now)
        if err:
            return ActionResult(ok=False, error=err)
        p.scheduled_time = z
    return ActionResult(ok=True, detail={"post_id": post_id, "scheduled_time": z})


def clear_time(cfg: Config, post_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """P1: deliberately DROP a post's scheduled_time. On an awaiting post just clears it. On a QUEUED post,
    FIRST sends it back to awaiting_approval (unapprove) THEN clears — both in ONE transaction, in that order,
    so the post is NEVER persisted as queued-and-timeless (which publish_due would publish-now). Reuses
    _guard_editable_post (rejects unknown/imminent/wrong-state), mirroring reschedule_post's shape. The
    unapprove uses the immutable model_copy (ledger layer); the scheduled_time=None is the in-place actions-
    layer edit (like reschedule_post's in-place p.scheduled_time = z) — consistent with both conventions."""
    now = _now(now)
    with Ledger.transaction(cfg) as led:
        p, err = _guard_editable_post(led, post_id, now)
        if err:
            return ActionResult(ok=False, error=err)
        if p.state is PostState.queued:
            led.unapprove_post(post_id)        # queued -> awaiting FIRST (model_copy), so it's never queued+None
        led.posts[post_id].scheduled_time = None
    return ActionResult(ok=True, detail={"post_id": post_id})


def accept_suggested_account(cfg: Config, handle: str, *, now: Optional[datetime] = None) -> ActionResult:
    """Apply batch spread suggestions to every queued post on one account."""
    from fanops.studio.actions_approve import _apply_schedule_for_account
    now = _now(now)
    try:
        with Ledger.transaction(cfg) as led:
            moved = _apply_schedule_for_account(led, cfg, handle, now=now)
    except Exception as exc:
        get_logger(cfg)("schedule", handle, "accept_suggestions_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"accept suggestions failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"rescheduled": moved, "outcome": "suggestions_accepted", "handle": handle})


def snooze_clip(cfg: Config, clip_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """Push every non-imminent queued post of a clip ~SNOOZE_DAYS into the future, in ONE
    transaction (atomic — never a partial snooze). Spreads via suggest_times_for_batch (lead base =
    now+SNOOZE_DAYS) so same-account multi-platform posts don't lockstep. Inline imminence + state
    check — does not use _guard_editable_post/_normalize_z (many posts of a clip, not one)."""
    from fanops.studio.views_common import suggest_times_for_batch
    now = _now(now)
    horizon = now + timedelta(days=SNOOZE_DAYS)
    with Ledger.transaction(cfg) as led:
        if clip_id not in led.clips:
            return ActionResult(ok=False, error=f"no such clip: {clip_id}")
        # bump both approved (queued) and pre-approval (awaiting_approval) posts — Review shows the
        # latter, so a Review-card snooze must actually move something (not a silent 0-count no-op).
        posts = [p for p in led.posts.values()
                 if p.parent_id == clip_id and p.state in (PostState.queued, PostState.awaiting_approval)
                 and not _imminent(p.scheduled_time, now)]
        sched = suggest_times_for_batch(cfg, posts, now=horizon)
        for p in posts:
            p.scheduled_time = sched[p.id]
    z = min(sched.values()) if sched else iso_z(horizon)   # UI banner (_result.html) still wants one time
    return ActionResult(ok=True, detail={"clip_id": clip_id, "count": len(posts), "scheduled_time": z})


def _seconds_away(scheduled_time: Optional[str], now: datetime, *, window_s: int = 60) -> bool:
    """M3: a tight protect-window for reschedule — TRUE only when the post fires in the next
    `window_s` seconds (default 60). PAST-DUE posts are NOT protected (the operator's complaint:
    'Reschedule all silently reschedules nothing' is exactly the bug where past-due was treated
    as imminent). Distinct from `_imminent` (5 min, used for the EDIT-DISABLED UI guard — a
    different concern; editing a 4-min-out post races the publisher, but RESPREADING it doesn't)."""
    if not scheduled_time:
        return False                                # missing time -> respread, never protect
    try:
        dt = parse_iso(scheduled_time)
    except (ValueError, TypeError):
        return False                                # unparseable -> respread, never protect
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return now <= dt <= now + timedelta(seconds=window_s)


def reschedule_bucket(cfg: Config, *, now: Optional[datetime] = None, handle: Optional[str] = None) -> ActionResult:
    """Routine re-spread of the APPROVED bucket: re-stagger every queued (approved) post onto a fresh
    cadence starting from `now`, reusing the M4 batch-aware spread engine (suggest_times_for_batch) so
    Approve and Reschedule share ONE cadence story — a single source of truth, no drift. Past-due posts
    ARE respread (M3 fix: today's broad `_imminent` 5-min gate silently no-op'd the bucket the operator
    cares about); only TRULY about-to-fire posts (seconds away) are protected, via `_seconds_away`.
    Never touches awaiting/published/etc. One transaction, idempotent-by-`now`, never a 500. The
    Schedule-tab 'reschedule all' control. An optional `handle` scopes the respread to ONE account
    (the per-account M3 control); None = the whole bucket."""
    from fanops.studio.views_common import suggest_times_for_batch
    now = _now(now)
    due: list = []
    try:
        with Ledger.transaction(cfg) as led:
            due = [p for p in led.posts.values()
                   if p.state is PostState.queued
                   and not _seconds_away(p.scheduled_time, now)
                   and (handle is None or p.account == handle)]
            due.sort(key=lambda p: (p.scheduled_time or "", p.account, p.platform.value, p.id))  # stable order in
            sched = suggest_times_for_batch(cfg, due, now=now)
            for p in due:
                p.scheduled_time = sched[p.id]
    except Exception as exc:
        get_logger(cfg)("schedule", handle or "-", "reschedule_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"reschedule failed: {str(exc)[:160]}")
    # R3/D17: audit which posts moved + the handle scope (None = whole bucket).
    if due:
        write_audit(cfg, "reschedule_bucket", [p.id for p in due],
                    reason="studio_reschedule_bucket", handle=handle, rescheduled=len(due))
    return ActionResult(ok=True, detail={"rescheduled": len(due), "handle": handle})


def shift_account_schedule(cfg: Config, handle: str, hours: float | str, *, now: Optional[datetime] = None) -> ActionResult:
    """Nudge every queued post for one account by a fixed offset — preserves relative spacing.
    MOL-726: `hours` arrives raw off the Schedule form, so parse AND range-check it here — before the
    lock, mirroring reschedule_post's `_normalize_z`. `timedelta`'s own constructor IS the
    representable-range oracle (nan -> ValueError, ±inf / out-of-range -> OverflowError); a constant
    derived from `timedelta.max.total_seconds()/3600` would be WRONG at the edge — that exact value
    still raises. Previously this ran outside the try below, so bad input 500'd the route."""
    try:
        hours = float(hours); delta = timedelta(hours=hours)
    except (TypeError, ValueError, OverflowError) as exc:
        return ActionResult(ok=False, error=f"bad shift {hours!r} hours: {str(exc)[:120]}")
    handle = (handle or "").strip()
    if not handle:
        return ActionResult(ok=True, detail={"shifted": 0, "handle": None, "hours": hours})
    now = _now(now)
    moved = 0
    try:
        with Ledger.transaction(cfg) as led:
            for p in led.posts.values():
                if p.state is not PostState.queued or p.account != handle:
                    continue
                if _seconds_away(p.scheduled_time, now) or not p.scheduled_time:
                    continue
                try:
                    dt = parse_iso(p.scheduled_time)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    p.scheduled_time = iso_z(dt + delta)
                    moved += 1
                except (ValueError, TypeError):
                    continue
    except Exception as exc:
        get_logger(cfg)("schedule", handle, "shift_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"shift failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"shifted": moved, "handle": handle, "hours": hours})


def reschedule_account(cfg: Config, handle: str, *, now: Optional[datetime] = None) -> ActionResult:
    """M3 per-account respread: re-stagger one account's queued posts on a fresh cadence (past-due
    included), leaving every other account untouched. Thin wrapper over `reschedule_bucket` so the
    M3 PRD outcome — a 'Reschedule this account' control that respreads exactly one account — has a
    named entry point; the per-account scoping is enforced inside the transaction (no race)."""
    return reschedule_bucket(cfg, now=now, handle=handle)


def randomize_account_schedule(cfg: Config, handle: str, *, days: int = 7, source_id: Optional[str] = None,
                               seed: Optional[int] = None, now: Optional[datetime] = None) -> ActionResult:
    """U7: scatter one account's queued posts across the next `days` operator-local days with 30-min min-gap
    and account-window clamp. Optional source_id scopes to one clip source. Seeded RNG for tests."""
    import random
    from fanops.errors import fail_open
    from fanops.studio.views_common import _roll_into_window, clip_source_of
    from fanops.timeutil import _operator_zone
    handle = (handle or "").strip()
    if not handle:
        return ActionResult(ok=True, detail={"rescheduled": 0, "handle": None})
    now = _now(now)
    rng = random.Random(seed)
    min_gap = timedelta(minutes=30)
    zone = _operator_zone(cfg) or timezone.utc
    window = cfg.account_window(handle)
    moved = 0
    try:
        with Ledger.transaction(cfg) as led:
            posts = [p for p in led.posts.values()
                     if p.state is PostState.queued and p.account == handle
                     and not _seconds_away(p.scheduled_time, now)]
            if source_id is not None:
                posts = [p for p in posts if clip_source_of(led, p.parent_id) == source_id]
            posts.sort(key=lambda p: p.id)
            slots: list[datetime] = []
            for p in posts:
                placed = False
                for _ in range(300):
                    day_off = rng.randint(0, max(0, days - 1))
                    local_base = now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=day_off)
                    open_h = 0 if window is None else window[0]
                    close_h = 24 if window is None else window[1]
                    span = max(1, close_h - open_h) if close_h > open_h else 1
                    hour = open_h + rng.randint(0, span - 1) if span > 1 else open_h
                    minute = rng.randint(0, 59)
                    local = local_base.replace(hour=hour % 24, minute=minute)
                    t = _roll_into_window(local.astimezone(timezone.utc), window, cfg)
                    if t <= now:
                        t = now + timedelta(minutes=max(1, cfg.publish_lead_minutes))
                        t = _roll_into_window(t, window, cfg)
                    if t <= now:
                        continue
                    if all(abs((t - s).total_seconds()) >= min_gap.total_seconds() for s in slots):
                        slots.append(t)
                        p.scheduled_time = iso_z(t)
                        moved += 1
                        placed = True
                        break
                if not placed:
                    t = (slots[-1] + min_gap) if slots else now + timedelta(minutes=max(1, cfg.publish_lead_minutes))
                    t = _roll_into_window(t, window, cfg)
                    if t <= now:
                        t = now + timedelta(seconds=1)
                    slots.append(t)
                    p.scheduled_time = iso_z(t)
                    moved += 1
    except Exception as exc:
        with fail_open("studio.actions.randomize_account_schedule"):
            raise exc
        return ActionResult(ok=False, error=f"randomize failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"rescheduled": moved, "handle": handle, "source_id": source_id})


def publish_due_bucket(cfg: Config, *, handle: Optional[str] = None, batch: Optional[str] = None,
                       confirmed: bool = True, now: Optional[datetime] = None) -> ActionResult:
    """Publish every DUE queued post in scope (Schedule 'Publish all due'). LIVE requires confirm + shows rate."""
    from fanops.errors import AuthError
    from fanops.post.run import publish_due
    from fanops.studio.actions_publish import _studio_publish_guard
    from fanops.studio.views_results import due_publish_plan
    plan = due_publish_plan(cfg, handle=handle, batch=batch, now=_now(now))
    if plan.due == 0:
        return ActionResult(ok=True, detail={"due": 0, "published": 0, "plan": plan.__dict__})
    if (err := _studio_publish_guard(cfg)):
        return ActionResult(ok=False, error=err)
    if cfg.is_live and not confirmed:
        tail = f", est. {plan.est_minutes} min" if plan.est_minutes and plan.postiz_due else ""
        rate = f"~{plan.rate_per_min}/min Postiz cap" if plan.rate_per_min else "live backends"
        return ActionResult(ok=False, error=(f"LIVE: Publish all due ships {plan.due} post(s) ({rate}{tail}) — "
                            "tick the confirm box, then click again."))
    try:
        summary = publish_due(cfg, now=iso_z(_now(now)), account=handle, batch_id=batch)
    except AuthError as exc:
        key = Config.auth_key_name_from_error(exc)
        return ActionResult(ok=False, error=f"FATAL auth failure — check {key}: {str(exc)[:160]}")
    except Exception as exc:
        get_logger(cfg)("publish", handle or "-", "publish_due_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"publish due failed: {str(exc)[:160]}")
    write_audit(cfg, "publish_due_bucket", [], reason="studio_publish_due_bucket", handle=handle, batch=batch, **summary)
    return ActionResult(ok=True, detail={**summary, "plan": plan.__dict__})
