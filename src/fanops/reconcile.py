"""Reconcile stage (AUDIT H4). Resolves posts stranded in `submitting` (crash mid-publish, FIX
F11) or `needs_reconcile` (ambiguous 5xx / network timeout after the body was sent, AUDIT C1) by
polling Blotato `GET /v2/posts/{postSubmissionId}` — the ONLY lookup the API offers (verified
against help.blotato.com: returns status in-progress|failed|published|scheduled + publicUrl/
errorMessage). It REQUIRES the submission id.

Consequence (the honest boundary): a stranded post WITHOUT a submission_id cannot be looked up —
the API has no content/account search — so it is SKIPPED here and left for human reconcile (the
digest surfaces it). The REST poster now captures a postSubmissionId from an ambiguous-5xx body
when one is present (blotato_rest.py) precisely so those posts BECOME auto-reconcilable; a pure
network timeout still yields no id and remains human-only. We never guess a post's fate — a wrong
guess either drops a live post (untrackable) or re-queues a live one (double-publish), the exact
C1/cascade hazards.

Resolution:
  status 'published'        -> PostState.published (+ public_url) so track can later measure it
  status 'failed'           -> PostState.failed (definitely not live -> safe to re-queue)
  'in-progress'/'scheduled' -> leave as-is (not yet resolved; a later pass retries)
"""
from __future__ import annotations
from typing import Callable, Optional
from fanops.config import Config
from fanops.errors import BlotatoAuthError
from fanops.ledger import Ledger
from fanops.models import PostState

# States whose true outcome is unknown and pollable: a publish was (or may have been) sent.
_RECONCILABLE = (PostState.submitting, PostState.submitted, PostState.needs_reconcile)
GetStatus = Callable[[str], dict]


def _default_get_status(cfg: Config) -> GetStatus:
    from fanops.post.metrics import BlotatoStatusClient
    return BlotatoStatusClient(cfg).get_status


def reconcile_posts(led: Ledger, cfg: Config, *, get_status: Optional[GetStatus] = None) -> Ledger:
    poll = get_status or _default_get_status(cfg)
    for post in [p for p in led.posts.values() if p.state in _RECONCILABLE]:
        if not post.submission_id:
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
        except BlotatoAuthError:
            raise                          # bad key/401: EVERY poll will fail -> halt, don't grind
                                           # the ledger recording a bogus error on every parked post
        except Exception as exc:
            # A single poll failure (e.g. a 404 on a not-yet-real fanops_ token) is NOT evidence the
            # post failed — it MAY be live. Honor the prime directive: never guess a post's fate.
            # Leave it parked (state untouched, NOT failed) and surface the reason for the digest;
            # a later pass retries. Then move on so the next post still gets reconciled.
            post.error_reason = f"reconcile poll error: {str(exc)[:200]}"
            continue
        status = (info.get("status") or "").lower()
        if status == "published":
            post.state = PostState.published
            post.public_url = info.get("publicUrl") or post.public_url
        elif status == "failed":
            post.state = PostState.failed
            post.error_reason = f"reconciled: blotato reports failed ({info.get('errorMessage', 'no detail')})"
        # in-progress / scheduled / unknown -> leave parked; a later reconcile pass will retry.
    return led
