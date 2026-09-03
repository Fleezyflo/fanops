"""Studio recovery mutations (no Flask): retry, resolve, bulk revert."""
from __future__ import annotations
from datetime import timedelta
from typing import Optional

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ErrorKind, PostState, _REVIEW_REVERT_BLOCKED
from fanops.audit import write_audit
from fanops.log import get_logger
from fanops.timeutil import iso_z
from fanops.studio.actions_common import ActionResult, _now


def _refuse_retired(cfg: Config, led: Ledger, p) -> bool:
    """True when a re-arm must be REFUSED: suppressed lineage never moves forward. The five Studio re-arm
    verbs all ask this one question of one row, so the Ledger's derived-disposition predicate owns it — this
    is the SOLE caller here, never a hand-copied lineage walk. Refuse BEFORE the write (a re-armed retired
    post used to be silently un-done by the per-tick sweep 600s later); every refusal is logged, never
    swallowed. A BACKWARD move (recover_posts `discard`) is NOT gated — it re-arms nothing.
    MOL-818: both failed/error oversize paths call this BEFORE apply_shrink_to_post, so a suppressed
    clip's file is unreachable by shrink — cmd_gc may reclaim it without orphaning a live retry."""
    if led.can_promote(p): return False
    get_logger(cfg)("review", p.id, "skipped_retired_lineage", account=p.account); return True


def _rearm_to_queued(led: Ledger, pid: str) -> None:
    """Sole owner of the failed→queued re-arm write (MOL-817). Clears submission_id + error_reason and
    routes state through Ledger.set_post_state. Callers stamp scheduled_time / media_urls on led.posts[pid]
    first when their verb requires it; _refuse_retired stays outside so oversize shrink runs only after
    an admitted (can_promote) re-arm — never on suppressed lineage (MOL-818 / Branch A)."""
    led.posts[pid].submission_id = None
    led.set_post_state(pid, PostState.queued, error_reason=None, error_kind=None)


def _apply_oversize_shrink(cfg: Config, led: Ledger, p) -> bool:
    """Shrink local media under the publish cap. Returns True when within cap after shrink."""
    from fanops.post.compress import apply_shrink_to_post, publish_backend_for_post
    backend = publish_backend_for_post(cfg, p)
    return apply_shrink_to_post(cfg, led, p, backend=backend)


def _strip_remote_media_urls(p) -> None:
    p.media_urls = [u for u in (p.media_urls or []) if not u.startswith("http")]


def _shrink_oversize_for_retry(cfg: Config, led: Ledger, p, *, require_cap: bool = False) -> bool:
    """Shrink oversize media in-place and strip remote media_urls. Returns False when shrink cannot proceed."""
    if require_cap:
        from fanops.post.compress import upload_cap_bytes, publish_backend_for_post
        backend = publish_backend_for_post(cfg, p)
        if upload_cap_bytes(cfg, p, backend) is None:
            return False
    if not _apply_oversize_shrink(cfg, led, p):
        return False
    _strip_remote_media_urls(p)
    return True


def resolve_post(cfg: Config, post_id: str, status: str, *, url: Optional[str] = None) -> ActionResult:
    """Studio twin of cmd_resolve — operator forces ground truth on stuck inflight posts."""
    from fanops.models import _POST_TERMINAL_REQUIRES_URL
    if post_id not in (Ledger.load(cfg).posts):
        return ActionResult(ok=False, error=f"no such post: {post_id}")
    try:
        st = PostState(status)
    except ValueError:
        st = PostState.published if status == "published" else PostState.failed
    if st not in (PostState.published, PostState.failed):
        return ActionResult(ok=False, error=f"resolve only supports published or failed, not {st.value!r}")
    if st in _POST_TERMINAL_REQUIRES_URL and not (url or "").strip():
        return ActionResult(ok=False, error="Paste the live permalink to mark this post published.")
    try:
        with Ledger.transaction(cfg) as led:
            if post_id not in led.posts:
                return ActionResult(ok=False, error=f"no such post: {post_id}")
            p = led.posts[post_id]
            if (url or "").strip():
                p.public_url = url.strip()
            if st is PostState.failed:
                led.set_post_state(post_id, st, error_kind=ErrorKind.unknown,
                                  error_reason=p.error_reason or "marked failed by operator")
            else:
                led.set_post_state(post_id, st, error_kind=None)
    except Exception as exc:
        get_logger(cfg)("resolve", post_id, "resolve_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"resolve failed: {str(exc)[:160]}")
    write_audit(cfg, "resolve_post", [post_id], reason="studio_resolve", status=st.value, url=(url or "").strip())
    outcome = "live_shipped" if st is PostState.published else "failed"
    return ActionResult(ok=True, detail={"post_id": post_id, "outcome": outcome, "state": st.value,
                                          "public_url": (url or "").strip() or None})


def bulk_send_to_review(cfg: Config, post_ids: list[str], *, reason: str) -> ActionResult:
    """R3/D7: the operator's wipe-and-revert flow as a first-class API. For each id move
    state -> awaiting_approval and clear the post-publish telemetry (scheduled_time,
    public_url, metrics, published_at) AND the failure latch (error_reason — RC-8: it is a
    status/suppression field, not lineage, so a reverted post never carries a stale one into
    Review). The session's hand-edited 67-post revert becomes
    one atomic call. Best-effort: known ids are moved; unknown ids surface in the result
    (operator typo never passes for success). Atomic per id (one transaction holding the
    flock for the whole batch). The reason field is the operator's intent — pinned in the
    audit so 'why this batch went back to Review' is in the log."""
    ids = [str(i) for i in (post_ids or []) if i]
    moved: list[str] = []
    skipped: list[str] = []
    skipped_retired: list[str] = []
    unknown: list[str] = []
    try:
        with Ledger.transaction(cfg) as led:
            for pid in ids:
                if pid not in led.posts:
                    unknown.append(pid); continue
                p = led.posts[pid]
                if p.state in _REVIEW_REVERT_BLOCKED:
                    skipped.append(pid); continue
                if _refuse_retired(cfg, led, p):
                    skipped_retired.append(pid); continue
                p.scheduled_time = None
                p.public_url = ""
                p.metrics = {}
                p.published_at = None
                led.set_post_state(pid, PostState.awaiting_approval, error_reason=None, error_kind=None)  # RC-8: clear failure latch on revert
                # Don't touch submission_id / batch_id — keep the lineage so the operator can
                # see "this post was once part of batch X" in the audit / Posted history.
                moved.append(pid)
    except Exception as exc:
        get_logger(cfg)("review", "-", "bulk_send_to_review_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"bulk_send_to_review failed: {str(exc)[:160]}")
    # R3/D17: audit the bulk revert — the most operator-impactful action in the system.
    if moved:
        write_audit(cfg, "bulk_send_to_review", moved, reason=reason,
                    unknown=unknown, moved=len(moved))
    return ActionResult(ok=True, detail={"moved": len(moved), "skipped": len(skipped),
                                          "skipped_retired": len(skipped_retired), "unknown": unknown,
                                          "post_ids": moved})


def retry_rate_limited_failures(cfg: Config, *, reason: str = "studio_retry_rate_limit", stagger_min: int = 2) -> ActionResult:
    """Pace 429 retries through the same one-per-integration daemon helper. stagger_min is ignored."""
    from fanops.post.run import _requeue_rate_limited_for_daemon
    from fanops.studio.views_results import classify_failure
    led = Ledger.load(cfg)
    skipped_retired = [pid for pid, p in led.posts.items()
                       if p.state in (PostState.failed, PostState.error)
                       and classify_failure(p) == "rate_limit"
                       and _refuse_retired(cfg, led, p)]
    candidates = [pid for pid, p in led.posts.items()
                  if p.state in (PostState.failed, PostState.error) and classify_failure(p) == "rate_limit"]
    try:
        n = _requeue_rate_limited_for_daemon(cfg)
    except Exception as exc:
        get_logger(cfg)("recover", "-", "retry_rate_limit_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"retry_rate_limited failed: {str(exc)[:160]}")
    after = Ledger.load(cfg)
    retried = [pid for pid in candidates if after.posts[pid].state is PostState.queued]
    if retried:
        write_audit(cfg, "recover_posts", retried, reason=reason, recover_action="retry", retried=n)
    return ActionResult(ok=True, detail={"retried": n, "skipped_retired": len(skipped_retired),
                                          "post_ids": retried, "outcome": "retried_rate_limit"})


def retry_oversize_failures(cfg: Config, *, reason: str = "studio_retry_oversize", stagger_min: int = 2) -> ActionResult:
    """Re-shrink and re-queue failed oversize (413) posts for daemon publish."""
    from fanops.studio.views_results import classify_failure
    ids = [pid for pid, p in Ledger.load(cfg).posts.items()
           if p.state in (PostState.failed, PostState.error) and classify_failure(p) == "oversize"]
    if not ids:
        return ActionResult(ok=True, detail={"retried": 0, "post_ids": [], "skipped": 0, "outcome": "retried_oversize"})
    retried: list[str] = []; skipped: list[str] = []; skipped_retired: list[str] = []
    now = _now(None)
    try:
        with Ledger.transaction(cfg) as led:
            for i, pid in enumerate(ids):
                p = led.posts.get(pid)
                if p is None or p.state not in (PostState.failed, PostState.error):
                    continue
                if _refuse_retired(cfg, led, p):   # BEFORE the shrink: apply_shrink_to_post transcodes and rewrites media_urls + the render row, so guarding after it would commit a write on a REFUSED re-arm
                    skipped_retired.append(pid); continue
                if not _shrink_oversize_for_retry(cfg, led, p):
                    skipped.append(pid); continue
                p.scheduled_time = iso_z(now + timedelta(minutes=stagger_min * i))
                _rearm_to_queued(led, pid)
                retried.append(pid)
    except Exception as exc:
        get_logger(cfg)("recover", "-", "retry_oversize_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"retry_oversize failed: {str(exc)[:160]}")
    if retried:
        write_audit(cfg, "recover_posts", retried, reason=reason, recover_action="retry", retried=len(retried))
    return ActionResult(ok=True, detail={"retried": len(retried), "skipped": len(skipped),
                                          "skipped_retired": len(skipped_retired), "post_ids": retried,
                                          "outcome": "retried_oversize", "stagger_min": stagger_min})


def retry_transient_failures(cfg: Config, *, reason: str = "studio_retry_transient", stagger_min: int = 2) -> ActionResult:
    """Queue all failed posts stamped ErrorKind.transient for daemon retry."""
    from fanops.studio.views_common import is_transient_failure
    ids = [pid for pid, p in Ledger.load(cfg).posts.items()
           if p.state in (PostState.failed, PostState.error) and is_transient_failure(p)]
    if not ids:
        return ActionResult(ok=True, detail={"retried": 0, "post_ids": []})
    retried: list[str] = []; skipped_retired: list[str] = []
    now = _now(None)
    try:
        with Ledger.transaction(cfg) as led:
            for i, pid in enumerate(ids):
                p = led.posts.get(pid)
                if p is None or p.state not in (PostState.failed, PostState.error):
                    continue
                if not is_transient_failure(p):
                    continue
                if _refuse_retired(cfg, led, p):
                    skipped_retired.append(pid); continue
                p.scheduled_time = iso_z(now + timedelta(minutes=stagger_min * i))
                _rearm_to_queued(led, pid)
                retried.append(pid)
    except Exception as exc:
        get_logger(cfg)("recover", "-", "retry_transient_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"retry_transient failed: {str(exc)[:160]}")
    if retried:
        write_audit(cfg, "recover_posts", retried, reason=reason, recover_action="retry", retried=len(retried))
    return ActionResult(ok=True, detail={"retried": len(retried), "skipped_retired": len(skipped_retired),
                                          "post_ids": retried, "outcome": "retried_transient",
                                          "stagger_min": stagger_min})


def recover_posts(cfg: Config, post_ids: list[str], *, action: str, reason: str = "") -> ActionResult:
    """S1 recovery cockpit: retry (failed→queued, retryable buckets only), review (→awaiting_approval),
    or discard (failed→rejected). Atomic per batch; unknown ids reported; oversize retried after auto-shrink."""
    from fanops.studio.views_results import classify_failure, _RETRYABLE_FAILURES
    ids = [str(i) for i in (post_ids or []) if i]
    if not ids:
        return ActionResult(ok=True, detail={"retried": 0, "discarded": 0, "reviewed": 0, "skipped": 0, "unknown": []})
    action = (action or "").strip().lower()
    if action == "review":
        return bulk_send_to_review(cfg, ids, reason=reason or "studio_recover_review")
    retried: list[str] = []; discarded: list[str] = []; skipped: list[str] = []
    skipped_retired: list[str] = []; unknown: list[str] = []
    try:
        with Ledger.transaction(cfg) as led:
            for pid in ids:
                if pid not in led.posts:
                    unknown.append(pid); continue
                p = led.posts[pid]
                if p.state not in (PostState.failed, PostState.error):
                    skipped.append(pid); continue
                if action == "retry":
                    if classify_failure(p) not in _RETRYABLE_FAILURES:
                        skipped.append(pid); continue
                    if _refuse_retired(cfg, led, p):   # BEFORE the oversize shrink below, which transcodes and rewrites media_urls + the render row — a refused re-arm must leave NO write behind
                        skipped_retired.append(pid); continue
                    if classify_failure(p) == "oversize":
                        if not _shrink_oversize_for_retry(cfg, led, p, require_cap=True):
                            skipped.append(pid); continue
                    if not (p.scheduled_time or "").strip():   # timeless-queued: a recovered post with no schedule (cleared/corrupt) parks FOREVER in _due_or_fail (silent). Land a time so the daemon publishes it, never never.
                        p.scheduled_time = iso_z(_now(None) + timedelta(minutes=cfg.publish_lead_minutes))
                    _rearm_to_queued(led, pid)
                    retried.append(pid)
                elif action == "discard":
                    led.set_post_state(pid, PostState.rejected)
                    discarded.append(pid)
                else:
                    return ActionResult(ok=False, error=f"unknown recover action: {action}")
    except Exception as exc:
        get_logger(cfg)("recover", "-", "recover_posts_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"recover_posts failed: {str(exc)[:160]}")
    if retried or discarded:
        write_audit(cfg, "recover_posts", retried or discarded, reason=reason,
                    recover_action=action, retried=len(retried), discarded=len(discarded),
                    skipped=len(skipped), unknown=unknown)
    detail = {"retried": len(retried), "discarded": len(discarded), "reviewed": 0,
              "skipped": len(skipped), "skipped_retired": len(skipped_retired),
              "unknown": unknown, "post_ids": retried or discarded}
    return ActionResult(ok=True, detail=detail)
