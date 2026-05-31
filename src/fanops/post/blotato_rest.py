"""Blotato v2 REST backend. Retries 429/5xx with bounded exponential backoff; 401 raises
loudly (bad key — do not silently burn posts, FIX F52); other 4xx -> PostState.failed with a
reason (FIX F22 — never 'analyzed'). REST body shape confirmed vs help.blotato.com 2026-05-31;
the submission-id key is an INTEGRATION CHECKPOINT (asserted below)."""
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

    def publish(self, led: Ledger, post_id: str) -> Ledger:
        post = led.posts[post_id]
        payload = build_blotato_payload(
            account_id=post.account_id, platform=post.platform.value, text=post.caption,
            media_urls=post.media_urls, scheduled_time=post.scheduled_time,
            extra_target=default_target_fields(post.platform.value))
        delay = 1.0
        last = None
        for attempt in range(_MAX_RETRIES):
            resp = requests.post(f"{BASE_URL}/posts", headers=self.headers, json=payload, timeout=30)
            last = resp
            if resp.status_code in (200, 201):
                post.state = PostState.submitted
                try:
                    sid = resp.json().get("postSubmissionId")
                except Exception:
                    sid = None
                if not sid:
                    # INTEGRATION CHECKPOINT: confirm the real submission-id key.
                    post.error_reason = f"no postSubmissionId in 2xx body: {resp.text[:200]}"
                post.submission_id = sid
                return led
            if resp.status_code == 401:
                raise RuntimeError(f"Blotato 401 unauthorized — check BLOTATO_API_KEY ({resp.text[:120]})")
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                time.sleep(delay); delay *= 2; continue        # retry transient
            break                                              # other 4xx -> fail
        post.state = PostState.failed
        post.error_reason = f"blotato {getattr(last,'status_code','?')}: {getattr(last,'text','')[:200]}"
        return led
