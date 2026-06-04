"""Blotato v2 REST backend.

Retry policy is asymmetric ON PURPOSE (AUDIT C1). Blotato's POST /v2/posts has NO idempotency
key (confirmed against help.blotato.com: the body accepts only post/scheduledTime/useNextFreeSlot)
and its own docs warn that a publish timeout produces a DUPLICATE live post. So a blind retry of
an ambiguous failure can post twice to the artist's real account, and there is no header that
would prevent it — sending a fake one would be a false-safety contract (worse than honest absence).

  - 200/201               -> submitted (+submission_id) if a recognizable id is present.
                             AUDIT B2: a 2xx with NO recognizable id -> needs_reconcile (it MAY be
                             live), NEVER failed (failed => re-queueable => double-post). The id is
                             extracted via _extract_submission_id (postSubmissionId | submissionId |
                             id, incl. nested data); the D1 client token is preserved as the handle.
  - 401                   -> raise loudly (bad key — never silently burn posts, FIX F52)
  - 429 (rate-limited)    -> RETRY with JITTERED bounded backoff (delay + random.uniform(0, delay),
                             then delay*=2 — AUDIT D4, avoids a thundering herd across surfaces). A
                             429 is rejected BEFORE Blotato processes it (user-level 30 req/min
                             limit), so the post was definitively NOT created — retrying cannot
                             double-post. Exhausted 429s -> failed.
  - 5xx / network timeout -> needs_reconcile, NO re-POST. The request body was already transmitted,
                             so the post MAY be live. A human/poll step resolves it via
                             GET /v2/posts/:id before any resubmit (don't blind-retry — Blotato's
                             own guidance: "do not retry the request"). NOT `failed` (which means
                             definitely-not-posted, safe to re-queue) — the distinction is the fix.
  - other 4xx             -> failed with a reason (FIX F22 — never 'analyzed')

REST body shape confirmed vs help.blotato.com 2026-05-31. The submission-id field (postSubmissionId),
status enum (in-progress|published|scheduled|failed), and publicUrl(get_post_status)/postUrl(list_posts)
URL-key split were verified against the live Blotato MCP tool schemas 2026-06-02 (AUDIT D5)."""
from __future__ import annotations
import random
import time
import requests
from fanops.config import Config
from fanops.errors import BlotatoAuthError
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.post.payload import build_blotato_payload, default_target_fields

BASE_URL = "https://backend.blotato.com/v2"
_MAX_RETRIES = 4


def _extract_submission_id(body) -> str | None:
    # AUDIT B2: Blotato's 2xx body shape varies (postSubmissionId | submissionId | id, sometimes
    # nested under "data"). Accept the known aliases, recurse into a nested dict, and ignore
    # non-str/empty values + non-dict bodies. Returns None when no recognizable id is present.
    if not isinstance(body, dict):
        return None
    for k in ("postSubmissionId", "submissionId", "id"):
        v = body.get(k)
        if isinstance(v, str) and v:
            return v
    data = body.get("data")
    if isinstance(data, dict):
        return _extract_submission_id(data)
    return None

class BlotatoRestPoster:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        key = cfg.blotato_api_key
        if not key:
            raise BlotatoAuthError("BLOTATO_API_KEY missing — cannot use REST backend.")
        self.headers = {"blotato-api-key": key, "Content-Type": "application/json"}

    def _reconcile(self, post, detail: str, resp=None) -> None:
        # Ambiguous failure after the body was sent — park for human/poll reconcile, never re-POST.
        # AUDIT H4: if the (5xx) body still carries a real submission id, CAPTURE it so the reconcile
        # step can later poll GET /v2/posts/:id and resolve this post automatically. Since D1 stamps
        # a CLIENT token at birth, post.submission_id is now ALWAYS set — so the guard fires on
        # `resp is not None` ALONE and a REAL Blotato id from the body OVERWRITES the client token
        # (the real id is the authoritative poll key). CODE-REVIEW (Minor #1): use the SAME
        # alias-aware _extract_submission_id as the 2xx path (postSubmissionId | submissionId | id +
        # nested data) — an error body may carry the id under an alias, and missing it would strand
        # the post on the un-pollable client token. No divergence between the two id-capture sites.
        if resp is not None:
            try:
                sid = _extract_submission_id(resp.json())
            except Exception:
                sid = None
            if sid:
                post.submission_id = sid
        post.state = PostState.needs_reconcile
        post.error_reason = f"ambiguous publish, may be live (reconcile via GET /v2/posts/:id): {detail}"

    def publish(self, led: Ledger, post_id: str) -> Ledger:
        post = led.posts[post_id]
        payload = build_blotato_payload(
            account_id=post.account_id, platform=post.platform.value, text=post.caption,
            media_urls=post.media_urls, scheduled_time=post.scheduled_time,
            extra_target=default_target_fields(post.platform.value, artist_name=self.cfg.artist_name))
        delay = 1.0
        last = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = requests.post(f"{BASE_URL}/posts", headers=self.headers, json=payload, timeout=30)
            except requests.exceptions.RequestException as exc:
                # Connection reset / read timeout MID-POST: the request may have landed on Blotato
                # (the response, not the request, was lost). Ambiguous -> reconcile, do not retry
                # into a possible second live post.
                self._reconcile(post, f"network error: {str(exc)[:160]}")
                return led
            last = resp
            if resp.status_code in (200, 201):
                try:
                    sid = _extract_submission_id(resp.json())
                except Exception:
                    sid = None
                if not sid:
                    # AUDIT B2: a 2xx with no RECOGNIZABLE submission id is MAY-BE-LIVE (the platform
                    # returned success) — PARK it as needs_reconcile, NEVER failed (failed =>
                    # re-queueable => double-post to a real fan account). The client token from D1 is
                    # PRESERVED (do NOT clear submission_id) so reconcile can still poll the post.
                    post.state = PostState.needs_reconcile
                    post.error_reason = f"2xx but no recognizable submission id: {resp.text[:200]}"
                    return led
                post.state = PostState.submitted
                post.submission_id = sid
                return led
            if resp.status_code == 401:
                raise BlotatoAuthError(f"Blotato 401 unauthorized — check BLOTATO_API_KEY ({resp.text[:120]})")
            if 500 <= resp.status_code < 600:
                # Ambiguous: Blotato may have created the post before the 5xx. No idempotency key
                # exists, so DO NOT re-POST (double-publish risk) — park for reconcile, capturing a
                # postSubmissionId from the body if present (AUDIT H4) so reconcile can poll it.
                self._reconcile(post, f"blotato {resp.status_code}: {resp.text[:160]}", resp=resp)
                return led
            if resp.status_code == 429:
                # Jitter the backoff so many surfaces rate-limited at once don't retry in lockstep
                # (thundering herd). Safe to retry (a 429 is rejected pre-processing — not posted).
                time.sleep(delay + random.uniform(0, delay)); delay *= 2; continue
            break                                              # other 4xx -> fail
        # Loop exhausted: only the 429 path reaches here (5xx/network return early). All attempts
        # were rate-limited -> the post was never created -> failed (re-queueable), not reconcile.
        post.state = PostState.failed
        post.error_reason = f"blotato {getattr(last,'status_code','?')}: {getattr(last,'text','')[:200]}"
        return led
