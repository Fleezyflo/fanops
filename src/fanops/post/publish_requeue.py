"""Daemon prep: re-queue failed transient and rate-limited posts before publish_due."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ErrorKind, Post, PostState, is_real_submission_id
from fanops.timeutil import iso_z
from fanops.log import get_logger

_DAEMON_TRANSIENT_MAX = 3    # MOL-125: daemon re-queue cycles for failed-but-transient (no submission_id)


def _requeue_transient_failed_for_daemon(cfg: Config) -> int:
    """MOL-125: before publish_due, re-queue failed transient posts (no real submission_id) for another
    daemon attempt. Bounded by _DAEMON_TRANSIENT_MAX — after that they stay terminal failed."""
    from fanops.studio.views_common import is_transient_failure
    requeued = 0
    led = Ledger.load(cfg)
    candidates = [p for p in led.posts_in_state(PostState.failed)
                  if not is_real_submission_id(p.submission_id)
                  and is_transient_failure(p)
                  and int(getattr(p, "daemon_transient_retry", 0) or 0) < _DAEMON_TRANSIENT_MAX]
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
                n = int(getattr(cur, "daemon_transient_retry", 0) or 0) + 1
                if n > _DAEMON_TRANSIENT_MAX:
                    continue
                cur.submission_id = None
                if not (cur.scheduled_time or "").strip():
                    cur.scheduled_time = iso_z(now)
                # MOL-812: counter is a field; clear the old counter-only prose so Studio never shows it.
                lg.set_post_state(cur.id, PostState.queued, error_kind=None, error_reason=None,
                                  daemon_transient_retry=n)
                requeued += 1
    except Exception as exc:                             # a re-queue txn hiccup must not sink the publish pass (fail-open)
        get_logger(cfg)("publish", "-", "requeue_transient_failed", err=str(exc)[:120], requeued=requeued)
        return requeued
    return requeued


def _requeue_rate_limited_for_daemon(cfg: Config) -> int:
    """Re-queue failed 429 rows (no real id), at most one per account_id per pass, spaced by the Postiz throttle."""
    requeued = 0
    led = Ledger.load(cfg)
    by_acct: dict[str, Post] = {}
    for p in led.posts_in_state(PostState.failed):
        if is_real_submission_id(p.submission_id):
            continue
        if getattr(p, "error_kind", None) is not ErrorKind.rate_limit:
            continue
        if int(getattr(p, "daemon_transient_retry", 0) or 0) >= _DAEMON_TRANSIENT_MAX:
            continue
        if not led.can_promote(p):
            continue
        aid = (p.account_id or p.account or "").strip() or "_"
        prev = by_acct.get(aid)
        if prev is None or (p.scheduled_time or "") < (prev.scheduled_time or ""):
            by_acct[aid] = p
    if not by_acct:
        return 0
    now = datetime.now(timezone.utc)
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
                if not lg.can_promote(cur):
                    continue
                n = int(getattr(cur, "daemon_transient_retry", 0) or 0) + 1
                if n > _DAEMON_TRANSIENT_MAX:
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


def _requeue_failed_posts(cfg: Config) -> None:
    """Daemon prep before publish_due: bounded re-queue for transient and rate-limited failures."""
    _requeue_transient_failed_for_daemon(cfg)
    _requeue_rate_limited_for_daemon(cfg)
