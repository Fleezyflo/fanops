"""Reconcile stage (AUDIT H4). Resolves posts stranded in `submitting` (crash mid-publish, FIX
F11) or `needs_reconcile` (ambiguous 5xx / network timeout after the body was sent, AUDIT C1) by
polling Blotato `GET /v2/posts/{postSubmissionId}` — the ONLY lookup the API offers (status enum
in-progress|failed|published|scheduled + publicUrl/errorMessage, VERIFIED against the live Blotato
`get_post_status` MCP tool schema 2026-06-02, AUDIT D5). It REQUIRES the submission id.

Consequence (the honest boundary): AUDIT H1 (Phase D) stamps EVERY crossposted post with a client
idempotency token (submission_id="fanops_..."), so a post parked after a pure network timeout is no
longer id-less — it carries a fanops_ token and IS polled. But a fanops_ token is not a real Blotato
postSubmissionId, so that poll 404s; the per-post try/except below CONTAINS that error (leaves the
post parked, never failed — a poll error is not evidence it failed) so the pass continues. A post with
genuinely NO submission_id at all (older data) is still SKIPPED for human reconcile (the digest
surfaces it). A real postSubmissionId from an ambiguous-5xx body (blotato_rest.py) overwrites the
token, making that post cleanly auto-reconcilable. We never guess a post's fate — a wrong guess either
drops a live post (untrackable) or re-queues a live one (double-publish), the exact C1/cascade hazards.

Resolution:
  status 'published'        -> PostState.published (+ public_url) so track can later measure it
  status 'failed'           -> PostState.failed (definitely not live -> safe to re-queue)
  'in-progress'/'scheduled' -> leave as-is (not yet resolved; a later pass retries)

Backend-agnostic by design (P2). The poll is dispatched per backend in _default_get_status: Blotato
(rest/mcp) over GET /v2/posts/{id}; Postiz over the DATE-WINDOWED GET /public/v1/posts `state` field
(PostizStatusClient). The {status, publicUrl} dict and the state machine here are identical for both —
only honesty boundary differs: Postiz exposes NO permalink on any response, so a reconciled Postiz post
keeps public_url=None (the operator sets the real social URL via `fanops resolve <id> published --url`).
A FATAL auth failure from EITHER backend (the shared AuthError base) halts the pass; a single poll error
is contained per-post (parked, never guessed failed). dryrun never reaches here (gated upstream)."""
from __future__ import annotations
from typing import Callable, Optional
from fanops.config import Config
from fanops.errors import AuthError
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.models import PostState

# States whose true outcome is unknown and pollable: a publish was (or may have been) sent.
_RECONCILABLE = (PostState.submitting, PostState.submitted, PostState.needs_reconcile)
GetStatus = Callable[[str], dict]


def _default_get_status(cfg: Config, led: Optional[Ledger] = None) -> GetStatus:
    # Backend dispatch (P2). Blotato (rest/mcp) polls GET /v2/posts/{id} — a true per-post status
    # endpoint with a publicUrl. Postiz has NEITHER: its only status signal is the `state` field on a
    # row of the DATE-WINDOWED GET /public/v1/posts, and no response carries a permalink. So the Postiz
    # poll wraps PostizStatusClient in a closure that looks the parked post up in `led` to pass its own
    # scheduled_time as the date window (a future/old/2099 post is otherwise PERMANENTLY off the default
    # ~week page). The closure keeps the GetStatus seam Callable[[str], dict] unchanged — `cmd_reconcile`
    # and the 30+ existing reconcile tests are untouched.
    if cfg.poster_backend == "postiz":
        from fanops.post.metrics import PostizStatusClient
        client = PostizStatusClient(cfg)
        def poll(sid: str) -> dict:
            post = next((p for p in led.posts.values() if p.submission_id == sid), None) if led else None
            return client.get_status(sid, publish_date=post.scheduled_time if post else None)
        return poll
    from fanops.post.metrics import BlotatoStatusClient
    return BlotatoStatusClient(cfg).get_status


def reconcile_posts(led: Ledger, cfg: Config, *, get_status: Optional[GetStatus] = None) -> Ledger:
    poll = get_status or _default_get_status(cfg, led)
    log = get_logger(cfg)
    for post in [p for p in led.posts.values() if p.state in _RECONCILABLE]:
        if not post.submission_id:
            log("reconcile", post.id, "skipped: no submission_id")
            continue                       # no id -> cannot poll (API needs it) -> human reconcile
        # Per-post resilience (mirrors publish_due, run.py:70-76): one post's poll error must NOT
        # abort the whole pass. AUDIT H1 made this load-bearing — D1 stamps EVERY post with a CLIENT
        # idempotency token (submission_id = "fanops_..."), so a post parked after a pure network
        # timeout carries a fanops_ token that is NOT a real Blotato postSubmissionId. Polling it
        # 404s -> BlotatoStatusClient.get_status raises RuntimeError. Uncaught, that raise escapes
        # reconcile_posts and strands every genuinely-published post LATER in iteration order
        # (order-dependent availability bug). Contain it to THIS post instead.
        try:
            info = poll(post.submission_id) or {}
        except AuthError:
            raise                          # bad key/401 (Blotato OR Postiz): EVERY poll will fail ->
                                           # halt, don't grind. Widened from BlotatoAuthError to the
                                           # shared AuthError base (P2) so a Postiz 401 also halts.
                                           # the ledger recording a bogus error on every parked post
        except Exception as exc:
            # A single poll failure (e.g. a 404 on a not-yet-real fanops_ token) is NOT evidence the
            # post failed — it MAY be live. Honor the prime directive: never guess a post's fate.
            # Leave it parked (state untouched, NOT failed) and surface the reason for the digest;
            # a later pass retries. Then move on so the next post still gets reconciled.
            post.error_reason = f"reconcile poll error: {str(exc)[:200]}"
            log("reconcile", post.id, "poll-error")
            continue
        status = (info.get("status") or "").lower()
        if status == "published":
            post.state = PostState.published
            post.public_url = info.get("publicUrl") or post.public_url
            log("reconcile", post.id, "published")
        elif status == "failed":
            post.state = PostState.failed
            post.error_reason = f"reconciled: poster reports failed ({info.get('errorMessage', 'no detail')})"
            log("reconcile", post.id, "failed")
        else:
            # in-progress / scheduled / unknown -> leave parked; a later reconcile pass will retry.
            log("reconcile", post.id, f"left: {status or 'unknown'}")
    return led
