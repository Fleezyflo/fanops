"""Daemon prep: re-queue failed transient posts before publish_due.

A rate_limit row is not sent back to queued. Postiz has no idempotency key, and a 429 may
already have created the post — re-queueing a row with no real submission id is a second create.
"""
from __future__ import annotations
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ErrorKind, PostState, is_real_submission_id
from fanops.timeutil import iso_z
from fanops.log import get_logger

_DAEMON_TRANSIENT_MAX = 3    # MOL-125: daemon re-queue cycles for failed-but-transient (no submission_id)


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


def _requeue_rate_limited_for_daemon(cfg: Config) -> int:
    """Do not send a rate_limit row back to queued.

    A row with a real submission id is already a live create. A row with no real id may still
    be one: whether Postiz applied the body before HTTP 429 is unproven, and there is no
    idempotency key. Either way this writer must not construct a second create.
    """
    return 0


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
    """Daemon prep before publish_due: bounded re-queue for transient failures.

    Rate-limited rows stay where they are — `_requeue_rate_limited_for_daemon` does not
    promote them to queued.
    """
    _requeue_transient_failed_for_daemon(cfg)
    _heal_obsolete_failed_for_daemon(cfg)
    _requeue_rate_limited_for_daemon(cfg)
