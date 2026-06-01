"""Blotato v2 REST backend.

Retry policy is asymmetric ON PURPOSE (AUDIT C1). Blotato's POST /v2/posts has NO idempotency
key (confirmed against help.blotato.com: the body accepts only post/scheduledTime/useNextFreeSlot)
and its own docs warn that a publish timeout produces a DUPLICATE live post. So a blind retry of
an ambiguous failure can post twice to the artist's real account, and there is no header that
would prevent it — sending a fake one would be a false-safety contract (worse than honest absence).

  - 200/201               -> submitted (+submission_id) | failed if no postSubmissionId (untrackable)
  - 401                   -> raise loudly (bad key — never silently burn posts, FIX F52)
  - 429 (rate-limited)    -> RETRY with bounded backoff. A 429 is rejected BEFORE Blotato processes
                             it (user-level 30 req/min limit), so the post was definitively NOT
                             created — retrying cannot double-post. Exhausted 429s -> failed.
  - 5xx / network timeout -> needs_reconcile, NO re-POST. The request body was already transmitted,
                             so the post MAY be live. A human/poll step resolves it via
                             GET /v2/posts/:id before any resubmit (don't blind-retry — Blotato's
                             own guidance: "do not retry the request"). NOT `failed` (which means
                             definitely-not-posted, safe to re-queue) — the distinction is the fix.
  - other 4xx             -> failed with a reason (FIX F22 — never 'analyzed')

REST body shape confirmed vs help.blotato.com 2026-05-31."""
from __future__ import annotations
import time
import requests
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.post.payload import build_blotato_payload, default_target_fields

BASE_URL = "https://backend.blotato.com/v2"
_MAX_RETRIES = 4

class BlotatoRestPoster:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        key = cfg.blotato_api_key
        if not key:
            raise RuntimeError("BLOTATO_API_KEY missing — cannot use REST backend.")
        self.headers = {"blotato-api-key": key, "Content-Type": "application/json"}

    def _reconcile(self, post, detail: str) -> None:
        # Ambiguous failure after the body was sent — park for human/poll reconcile, never re-POST.
        post.state = PostState.needs_reconcile
        post.error_reason = f"ambiguous publish, may be live (reconcile via GET /v2/posts/:id): {detail}"

    def publish(self, led: Ledger, post_id: str) -> Ledger:
        post = led.posts[post_id]
        payload = build_blotato_payload(
            account_id=post.account_id, platform=post.platform.value, text=post.caption,
            media_urls=post.media_urls, scheduled_time=post.scheduled_time,
            extra_target=default_target_fields(post.platform.value))
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
                    sid = resp.json().get("postSubmissionId")
                except Exception:
                    sid = None
                if not sid:
                    # INTEGRATION CHECKPOINT: a 2xx with no submission id can't be tracked
                    # by track.py — fail it (don't park it in 'submitted'), so it surfaces.
                    post.state = PostState.failed
                    post.error_reason = f"2xx but no postSubmissionId: {resp.text[:200]}"
                    return led
                post.state = PostState.submitted
                post.submission_id = sid
                return led
            if resp.status_code == 401:
                raise RuntimeError(f"Blotato 401 unauthorized — check BLOTATO_API_KEY ({resp.text[:120]})")
            if 500 <= resp.status_code < 600:
                # Ambiguous: Blotato may have created the post before the 5xx. No idempotency key
                # exists, so DO NOT re-POST (double-publish risk) — park for reconcile.
                self._reconcile(post, f"blotato {resp.status_code}: {resp.text[:160]}")
                return led
            if resp.status_code == 429:
                time.sleep(delay); delay *= 2; continue        # safe to retry (rejected pre-processing)
            break                                              # other 4xx -> fail
        # Loop exhausted: only the 429 path reaches here (5xx/network return early). All attempts
        # were rate-limited -> the post was never created -> failed (re-queueable), not reconcile.
        post.state = PostState.failed
        post.error_reason = f"blotato {getattr(last,'status_code','?')}: {getattr(last,'text','')[:200]}"
        return led
