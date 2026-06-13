"""Real metrics-read client (FIX F05 — v1 had none). list_posts(window) returns rows keyed by
postSubmissionId with a metrics dict. The postSubmissionId key and the status enum
(in-progress|published|scheduled|failed, used by BlotatoStatusClient below) were VERIFIED against
the live Blotato MCP tool schemas 2026-06-02 (AUDIT D5). NOTE the live URL-key split: the published
URL is `publicUrl` on get_post_status (the single-post lookup) but `postUrl` on list_posts — this
client reads metrics rows by postSubmissionId and does NOT read a URL, so the split does not bite
here (a future reader of a list row's URL must use postUrl). Which METRICS fields Blotato exposes
remains an INTEGRATION CHECKPOINT: if saves/shares/retention are unavailable, redesign lift_score
(Task 21) on the available fields."""
from __future__ import annotations
import requests
from fanops.config import Config
from fanops.errors import BlotatoAuthError
from fanops.post.blotato_base import BASE_URL

# A 401 on a metrics/status read is the SAME fatal auth condition as a 401 on publish — raise the
# TYPED error so reconcile's halt-on-auth guard fires (else a bad key grinds every parked post) and
# `track` halts cleanly. Body WITHHELD: the message reaches stdout/ledger/digest, so a 401 body
# echoing the key would leak it (the df85662 redaction closed media.py/blotato_rest.py but missed
# these two read clients — audit follow-up).
def _raise_for_auth(resp) -> None:
    if resp.status_code == 401:
        raise BlotatoAuthError("Blotato 401 unauthorized — check BLOTATO_API_KEY (response body withheld)")

class BlotatoMetricsClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        key = cfg.blotato_api_key
        if not key:
            raise RuntimeError("BLOTATO_API_KEY missing — cannot read metrics.")
        self.headers = {"blotato-api-key": key}

    def list_posts(self, window: str = "30d") -> list[dict]:
        resp = requests.get(f"{BASE_URL}/posts", headers=self.headers,
                            params={"window": window}, timeout=30)
        _raise_for_auth(resp)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"blotato metrics {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("items", [])


class BlotatoStatusClient:
    """Single-post status lookup for the reconcile stage (AUDIT H4): GET /v2/posts/{id} ->
    {status: in-progress|failed|published|scheduled, publicUrl, errorMessage}. Verified against
    help.blotato.com. Rate-limited by Blotato to 60 req/min, so reconcile polls only stranded
    posts that HAVE a submission id, not the whole ledger."""
    def __init__(self, cfg: Config):
        self.cfg = cfg
        key = cfg.blotato_api_key
        if not key:
            raise RuntimeError("BLOTATO_API_KEY missing — cannot reconcile posts.")
        self.headers = {"blotato-api-key": key}

    def get_status(self, submission_id: str) -> dict:
        resp = requests.get(f"{BASE_URL}/posts/{submission_id}", headers=self.headers, timeout=30)
        _raise_for_auth(resp)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"blotato status {resp.status_code}: {resp.text[:200]}")
        return resp.json()
