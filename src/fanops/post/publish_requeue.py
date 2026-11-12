"""Daemon prep: re-queue failed transient and rate-limited posts before publish_due."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ErrorKind, Post, PostState, is_real_submission_id
from fanops.timeutil import iso_z, schedule_utc
from fanops.log import get_logger

_DAEMON_TRANSIENT_MAX = 3    # MOL-125: daemon re-queue cycles for failed-but-transient (no submission_id)
# A Postiz 429 means the create-post endpoint's per-hour API_LIMIT is spent (Postiz public API docs, "Rate
# Limits"; self-host reference, API_LIMIT). One `fanops run` pass calls publish_due once per advance, so the
# daemon waits out that hour from the claim that took the 429 — the pass that took it never sends it again.
_RATE_LIMIT_COOL_OFF = timedelta(hours=1)


def _requeue_transient_failed_for_daemon(cfg: Config) -> int:
    """MOL-125: before publish_due, re-queue failed transient posts (no real submission_id) for another
    daemon attempt. No retry cap — never-sent vendor-down is not a terminal failed budget."""
    from fanops.studio.views_common import is_transient_failure
    requeued = 0
    led = Ledger.load(cfg)
    candidates = [p for p in led.posts_in_state(PostState.failed)
                  if not is_real_submission_id(p.submission_id)
                  and is_transient_failure(p)]
    if not candidates:
        return 0
    now = datetime.now(timezone.utc)
    try:
        with Ledger.transaction(cfg) as lg:
            for p in candidates:
                cur = lg.posts.get(p.id)
                if cur is None or cur.state is not PostState.failed:
                    continue
                if is_real_submission_id(cur.submission_id):
                    continue
                if not is_transient_failure(cur):
                    continue
                cur.submission_id = None
                if not (cur.scheduled_time or "").strip():
                    cur.scheduled_time = iso_z(now)
                lg.set_post_state(cur.id, PostState.queued, error_kind=None, error_reason=None)
                requeued += 1
    except Exception as exc:                             # a re-queue txn hiccup must not sink the publish pass (fail-open)
        get_logger(cfg)("publish", "-", "requeue_transient_failed", err=str(exc)[:120], requeued=requeued)
        return requeued
    return requeued


def _cooling_off(p: Post, now: datetime, cool_off: timedelta | None) -> bool:
    """True while the claim that took the 429 (`submission_started_at`, re-stamped by every claim) is younger
    than cool_off. No stamp means the attempt predates MOL-709, so there is nothing left to wait out."""
    if cool_off is None:
        return False
    started = schedule_utc(p.submission_started_at)
    return started is not None and now - started < cool_off


def _rate_limit_held(p: Post, now: datetime, cool_off: timedelta | None) -> bool:
    """True when this unsent 429 must stay failed.

    Inside the Postiz hour it stays failed. At the retry cap with no cool-off (the Studio retry
    passes none) it stays failed. Once that hour has elapsed, the cap does not bury it: the hour
    is what stops a same-pass resend."""
    if _cooling_off(p, now, cool_off):
        return True
    if int(getattr(p, "daemon_transient_retry", 0) or 0) < _DAEMON_TRANSIENT_MAX:
        return False
    return cool_off is None


def _requeue_rate_limited_for_daemon(cfg: Config, *, cool_off: timedelta | None = None) -> int:
    """Re-queue failed 429 rows (no real id), at most one per account_id per pass, spaced by the Postiz throttle.
    The daemon prep passes `_RATE_LIMIT_COOL_OFF`; the operator's Studio retry passes none and re-queues at once."""
    requeued = 0
    now = datetime.now(timezone.utc)
    led = Ledger.load(cfg)
    by_acct: dict[str, Post] = {}
    for p in led.posts_in_state(PostState.failed):
        if is_real_submission_id(p.submission_id):
            continue
        if getattr(p, "error_kind", None) is not ErrorKind.rate_limit:
            continue
        if _rate_limit_held(p, now, cool_off):
            continue
        if not led.can_promote(p):
            continue
        aid = (p.account_id or p.account or "").strip() or "_"
        prev = by_acct.get(aid)
        if prev is None or (p.scheduled_time or "") < (prev.scheduled_time or ""):
            by_acct[aid] = p
    if not by_acct:
        return 0
    per_min = cfg.postiz_publish_per_min
    gap = timedelta(seconds=(60.0 / per_min) if per_min > 0 else 0)
    try:
        with Ledger.transaction(cfg) as lg:
            for p in by_acct.values():
                cur = lg.posts.get(p.id)
                if cur is None or cur.state is not PostState.failed:
                    continue
                if is_real_submission_id(cur.submission_id):
                    continue
                if getattr(cur, "error_kind", None) is not ErrorKind.rate_limit:
                    continue
                if _rate_limit_held(cur, now, cool_off):
                    continue
                if not lg.can_promote(cur):
                    continue
                n = int(getattr(cur, "daemon_transient_retry", 0) or 0) + 1
                if cool_off is None and n > _DAEMON_TRANSIENT_MAX:
                    continue
                cur.submission_id = None
                cur.scheduled_time = iso_z(now + gap)
                lg.set_post_state(cur.id, PostState.queued, error_kind=None, error_reason=None,
                                  daemon_transient_retry=n)
                requeued += 1
    except Exception as exc:
        get_logger(cfg)("publish", "-", "requeue_rate_limited_failed", err=str(exc)[:120], requeued=requeued)
        return requeued
    return requeued


def _heal_obsolete_failed_for_daemon(cfg: Config) -> int:
    """Re-arm failed rows the current publisher would now accept (I8). Dual of publisher_refuses."""
    from fanops.post.postiz import publisher_refuses
    healed = 0
    led = Ledger.load(cfg)
    candidates = [p for p in led.posts_in_state(PostState.failed)
                  if not is_real_submission_id(p.submission_id)
                  and getattr(p, "error_kind", None) is ErrorKind.bad_payload
                  and int(getattr(p, "daemon_transient_retry", 0) or 0) < _DAEMON_TRANSIENT_MAX
                  and publisher_refuses(p) is None]
    if not candidates:
        return 0
    now = datetime.now(timezone.utc)
    try:
        with Ledger.transaction(cfg) as lg:
            for p in candidates:
                cur = lg.posts.get(p.id)
                if cur is None or cur.state is not PostState.failed:
                    continue
                if is_real_submission_id(cur.submission_id):
                    continue
                if getattr(cur, "error_kind", None) is not ErrorKind.bad_payload:
                    continue
                if publisher_refuses(cur) is not None:
                    continue
                if not lg.can_promote(cur):
                    continue
                n = int(getattr(cur, "daemon_transient_retry", 0) or 0) + 1
                if n > _DAEMON_TRANSIENT_MAX:
                    continue
                cur.submission_id = None
                if not (cur.scheduled_time or "").strip():
                    cur.scheduled_time = iso_z(now)
                lg.set_post_state(cur.id, PostState.queued, error_kind=None, error_reason=None,
                                  daemon_transient_retry=n)
                healed += 1
    except Exception as exc:
        get_logger(cfg)("publish", "-", "heal_obsolete_failed", err=str(exc)[:120], healed=healed)
        return healed
    return healed


def _requeue_failed_posts(cfg: Config) -> None:
    """Daemon prep before publish_due: bounded re-queue for transient and rate-limited failures."""
    _requeue_transient_failed_for_daemon(cfg)
    _heal_obsolete_failed_for_daemon(cfg)
    _requeue_rate_limited_for_daemon(cfg, cool_off=_RATE_LIMIT_COOL_OFF)
