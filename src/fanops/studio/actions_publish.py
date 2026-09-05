"""Studio publish mutations (no Flask): ship, reconcile, preflight, mark-by-hand."""
from __future__ import annotations
from typing import Optional

from fanops.config import Config
from fanops.errors import AuthError, fail_open
from fanops.ledger import Ledger
from fanops.models import ErrorKind, PostState
from fanops.audit import write_audit
from fanops.log import get_logger
from fanops.studio.actions_common import ActionResult

# Non-terminal states an operator may mark "posted by hand". `error` is included (ecc:python-review):
# it is semantically a recoverable failure like `failed` (digest.py treats them alike), so the UI
# must not strand an error-state post. Excludes the terminal published/analyzed/retired.
_POSTABLE = {PostState.queued, PostState.needs_reconcile, PostState.submitting,
             PostState.submitted, PostState.failed, PostState.error}


def mark_published(cfg: Config, post_id: str, url: Optional[str] = None) -> ActionResult:
    """Track B: the operator posted this clip by hand — force the post to `published` (+ REQUIRED
    live URL). Like `fanops resolve <id> published` but STRICTER (ecc:python-review): resolve is the
    unguarded force-anything escape hatch, whereas this rejects an already-terminal
    (published/analyzed/retired) post so a double-click can't churn terminal state. Tight local
    transaction, no network.

    R1/D9: `url` is now REQUIRED (non-empty after strip). Saying "I posted by hand" MEANS the
    operator has a permalink they can paste — refusing the action without one closes the third door
    onto the ghost-row class (alongside D1: DryRunPoster, D2: _publish_one). Without this check the
    same operator-driven path produced Post(state=published, public_url='') — a row that says
    SHIPPED but the Posted tub can't render."""
    if not (url or "").strip():
        return ActionResult(ok=False, error=(
            "mark_published requires a non-empty url — you said you posted by hand, paste the "
            "permalink so the Posted tub has something to render (R1/D9)."))
    with Ledger.transaction(cfg) as led:
        if post_id not in led.posts:
            return ActionResult(ok=False, error=f"no such post: {post_id}")
        p = led.posts[post_id]
        if p.state not in _POSTABLE:
            return ActionResult(ok=False, error=f"post {post_id} is {p.state.value} — only an unpublished post can be marked posted")
        # R1: set the URL BEFORE the state flip so the @model_validator sees a consistent shape on
        # the next ledger save (Pydantic re-validates the modified instance on serialization).
        p.public_url = url.strip()
        led.set_post_state(post_id, PostState.published, error_kind=None, error_reason=None)
    # R3/D17: audit the SUCCESS — 'I posted by hand' is the most opaque action; the audit gives the operator a breadcrumb.
    write_audit(cfg, "mark_published", [post_id], reason="studio_mark_published", url=url.strip())
    return ActionResult(ok=True, detail={"post_id": post_id, "url": url})


def _studio_publish_guard(cfg: Config, post=None) -> Optional[str]:
    """Studio publish actions must not silently dryrun when the operator expects live — and must not submit
    into a DEAD backend. T10: once the per-post provider is resolved, exercise that provider's REAL health
    (Postiz's docker health-check is nginx-only and LIES while the Node backend crash-loops; Zernio auth can
    lapse) and FAIL FAST with an ops pointer BEFORE the poster runs, instead of submitting-then-parking the
    post in needs_reconcile. FAIL CLOSED: an unhealthy probe / failed auth blocks. The probe never echoes a key."""
    if not cfg.is_live:
        return "Not live — flip Go Live before publishing. Nothing reaches social in dryrun."
    if post is not None:
        from fanops.accounts import Accounts
        from fanops.post.run import _post_provider
        accts = Accounts.load(cfg)
        prov = _post_provider(cfg, accts, post)
        if prov == "dryrun":
            return (f"{post.account} on {post.platform.value} routes to dryrun — map the channel in Go Live → Accounts.")
        if prov is None:
            return (f"{post.account} on {post.platform.value} is not mapped — connect the channel in Go Live.")
        if prov == "postiz":
            from fanops.post import postiz as _postiz            # module ref so a test monkeypatch on the symbol applies
            health = _postiz.postiz_health_probe(cfg)
            if not health.healthy:
                from fanops.postiz_lifecycle import ensure_up
                ensure_up(cfg)                                 # self-heal: wake idle-stopped local stack once
                health = _postiz.postiz_health_probe(cfg)
            if not health.healthy:
                return (f"Postiz backend unhealthy ({health.status_code or 'unreachable'}) — its docker health-check "
                        f"is nginx-only and can lie while the Node backend crash-loops. Publishing now would submit "
                        f"then park in needs_reconcile. Fix Postiz first; see docs/POSTIZ_OPS.md.")
        elif prov == "zernio":
            from fanops.post import zernio as _zernio            # module ref so a test monkeypatch on the symbol applies
            from fanops.errors import ZernioAuthError
            try:
                if not _zernio.zernio_check_auth(cfg):
                    return ("Zernio unreachable — publishing now would submit then park. Check ZERNIO_API_KEY / the "
                            "Zernio API; see docs/POSTIZ_OPS.md.")
            except ZernioAuthError:
                return ("Zernio rejected the API key (401) — check ZERNIO_API_KEY in the Studio Go-Live tab; "
                        "see docs/POSTIZ_OPS.md.")
    return None


def preflight_publish_media(cfg: Config, post, led=None) -> str | None:
    """Return an error string when local media exceeds backend caps (fail BEFORE network). Shrinks when possible."""
    from fanops.post.compress import apply_shrink_to_post, media_path_for_post, publish_backend_for_post, upload_cap_bytes
    led = led if led is not None else Ledger.load(cfg)
    backend = publish_backend_for_post(cfg, post)
    cap = upload_cap_bytes(cfg, post, backend)
    if cap is None:
        return None
    if apply_shrink_to_post(cfg, led, post, backend=backend):
        return None
    path = media_path_for_post(cfg, led, post)
    size = path.stat().st_size if path else 0
    return f"oversize: {size} bytes > {cap} — re-render shorter"


def reconcile_inflight(cfg: Config) -> ActionResult:
    """Poll backends for permalinks on in-flight posts (Studio reconcile strip)."""
    if not cfg.is_live:
        return ActionResult(ok=False, error="Publishing is off — turn on Go Live before checking for links.")
    from fanops.reconcile import reconcile_due
    try:
        summary = reconcile_due(cfg)
    except Exception as exc:
        get_logger(cfg)("reconcile", "-", "reconcile_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"reconcile failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"outcome": "reconciled", **summary})


def publish_now(cfg: Config, post_id: str, *, confirmed: bool = True) -> ActionResult:
    """Ship ONE reviewed post IMMEDIATELY from the Studio (milestone 5: publish in the UI) via the
    SAME poster path the pipeline uses (post.run.publish_post) — a real post on a live backend, a
    dryrun no-op->published locally — IGNORING the post's (future) schedule, so the occasional-batch
    operator can review then ship without waiting for the schedule or touching the CLI. Same
    live-publish confirm + fatal-auth surfacing as run_advance; queued-only; scoped to THIS post
    (other scheduled posts are untouched). Distinct from mark_published (Track B: 'I posted by hand')
    — this actually drives the poster."""
    from fanops.post.run import publish_post
    if cfg.is_live and not confirmed:
        # UI-LIE-FIX: per-channel truth, not the legacy global.
        return ActionResult(ok=False, error=f"LIVE backend ({cfg.effective_publish_mode()}): this "
                            "PUBLISHES the post to a real account — tick the confirm box, then click again.")
    # Short lock-free guard read for a friendly message; publish_post's own CLAIM transaction is the
    # authoritative queued-only gate (a state change in the gap is re-validated there -> a clean no-op).
    led = Ledger.load(cfg)
    if post_id not in led.posts:
        return ActionResult(ok=False, error=f"no such post: {post_id}")
    post = led.posts[post_id]
    st = post.state
    if st is not PostState.queued:
        return ActionResult(ok=False, error=f"post {post_id} is {st.value} — only a queued post can be published")
    if (err := _studio_publish_guard(cfg, post)):
        return ActionResult(ok=False, error=err)
    if (pf := preflight_publish_media(cfg, post, led=led)):
        with fail_open("studio.actions.publish_now"):
            with Ledger.transaction(cfg) as led:
                p = led.posts.get(post_id)
                if p is not None:
                    led.set_post_state(post_id, PostState.failed, error_kind=ErrorKind.bad_payload,
                                       error_reason=pf)
        return ActionResult(ok=False, error=pf)
    from fanops.post.compress import persist_post_shrink
    persist_post_shrink(cfg, led, post_id)
    try:
        # network runs OUTSIDE the ledger lock (per-post claim->network->finalize) — the Studio no longer
        # holds the flock across the publish round-trip, so a concurrent daemon pass isn't starved.
        state = publish_post(cfg, post_id)
    except AuthError as exc:
        # UI-LIE-FIX: the auth-key name comes from the EXCEPTION CLASS, not a backend guess
        # (PostizAuthError -> POSTIZ_API_KEY, etc). This is unambiguous: the backend that raised
        # owns the key. Replaces the old `if cfg.poster_backend == 'postiz'` ternary that lied on
        # per-channel deployments and didn't even know zernio existed.
        key = Config.auth_key_name_from_error(exc)
        return ActionResult(ok=False, error=f"FATAL auth failure — check {key}: {str(exc)[:160]}")
    except Exception as exc:
        # A non-auth failure (media upload RuntimeError, corrupt clip.path, etc.) must NOT escape to
        # Flask as a 500 — the cockpit surfaces it cleanly (mirrors run_advance's broad catch).
        get_logger(cfg)("publish", post_id, "publish_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"publish failed: {str(exc)[:160]}")
    # ONLY 'published' is success: _publish_one advances submitted -> published on a clean poster return,
    # so any other terminal state means the post did NOT fully ship. A None return means the CLAIM gate
    # found it no longer queued (e.g. a concurrent daemon pass just claimed it between the guard read and
    # the claim) — tell the operator to retry rather than print a confusing "post is None".
    if state in ("published", "needs_reconcile", "submitted"):
        pub = Ledger.load(cfg).posts.get(post_id)
        # dryrun-boundary M3: the "LIVE publish accidentally ran dryrun -> dryrun:// url" guard is gone —
        # nothing writes a dryrun:// url any more, and a dryrun provider is boundary-skipped before it can
        # reach _publish_one on a live system. The row can't be constructed, so there's nothing to catch.
        from fanops.studio.views_results import classify_post_delivery
        delivery = classify_post_delivery(pub) if pub else "dryrun"
        outcome = {"live": "live_shipped", "inflight": "inflight_submitted", "dryrun": "dryrun_local"}.get(delivery, "live_shipped")
        write_audit(cfg, "publish_now", [post_id], reason="studio_publish_now",
                    backend=cfg.effective_publish_mode())
        return ActionResult(ok=True, detail={"post_id": post_id, "state": state, "outcome": outcome,
                                             "submission_id": getattr(pub, "submission_id", None),
                                             "public_url": getattr(pub, "public_url", None),
                                             "backend": cfg.effective_publish_mode()})
    if state is None:
        return ActionResult(ok=False, error="post was not claimable (it may be publishing already) — refresh and try again")
    return ActionResult(ok=False, error=f"publish did not complete (post is {state}) — see the run log")
