"""Zernio poster backend — a HOSTED scheduler (FANOPS_POSTER=zernio, or a per-account override). Lets
FanOps publish TikTok WITHOUT passing TikTok app review: Zernio owns the TikTok app/OAuth, so the
operator connects their TikTok accounts inside Zernio's dashboard and FanOps only needs an API key + the
resulting account _ids. Same swappable-poster slot as Postiz/Blotato, SAME asymmetric-retry safety: a bad
key halts the queue by TYPE (ZernioAuthError); a 5xx / network drop after the body was sent parks
needs_reconcile (Zernio's create-post has no idempotency key, so we NEVER re-POST a possible live post).

REST contract (operator-pasted docs, https://zernio.com/api/v1): Authorization: Bearer <sk_…>;
POST /posts {content, publishNow:true, platforms:[{platform, accountId}], media:[url,…]}; GET /accounts
-> {accounts:[{_id, platform, name}]}. publishNow:true because FanOps already gated the schedule (a post
sits `queued` until due, then publish_due fires) — we don't hand Zernio the schedule. The create-post
RESPONSE id key and the media field shape are INTEGRATION CHECKPOINTS — the offline tests lock the SHAPE;
the operator verifies live at first publish. accounts.json integrations[platform] carries the Zernio
account _id for a zernio surface (which backend that id belongs to lives in accounts.json `backends`)."""
from __future__ import annotations
import logging
import random
import time
from typing import NamedTuple
import requests
from fanops.config import Config
from fanops.errors import ZernioAuthError
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.text import safe_public_url

_log = logging.getLogger("fanops.post.zernio")
_MAX_RETRIES = 4


class ZernioAccount(NamedTuple):
    """One connected Zernio account from GET /accounts. `id` (the doc's `_id`) is what accounts.json's
    per-platform integrations[platform] carries for a zernio surface. A typed row, not a bare dict."""
    id: str
    name: str
    platform: str


def _base(cfg: Config) -> str:
    return (cfg.zernio_url or "https://zernio.com/api/v1").rstrip("/")

def _key(cfg: Config) -> str:
    k = cfg.zernio_api_key
    if not k:
        raise ZernioAuthError("ZERNIO_API_KEY missing — cannot use the Zernio backend.")
    return k


def _extract_zernio_id(body) -> str | None:
    # Zernio's create-post response id key isn't pinned (integration checkpoint). Accept the likely
    # aliases + a nested post.{_id,id}; ignore non-str/empty; None when none present.
    if isinstance(body, list):
        body = body[0] if body else None
    if not isinstance(body, dict):
        return None
    for k in ("_id", "id", "postId"):
        v = body.get(k)
        if isinstance(v, str) and v:
            return v
    nested = body.get("post")
    if isinstance(nested, dict):
        return _extract_zernio_id(nested)
    return None


def build_zernio_payload(*, account_id: str, platform: str, content: str,
                         media_urls: list[str], scheduled_time: str | None) -> dict:
    # publishNow:true — FanOps owns the schedule (publish_due fired this post because it's due), so we do
    # NOT pass scheduledFor/timezone (kept in the signature for parity / future use). platforms[] targets
    # ONE Zernio account (a FanOps Post is one surface). media[] references already-uploaded URLs (the
    # file->URL upload is slice 3); the field name is an INTEGRATION CHECKPOINT — omitted when empty.
    payload: dict = {"content": content, "publishNow": True,
                     "platforms": [{"platform": platform, "accountId": account_id}]}
    media = [u for u in (media_urls or []) if u]
    if media:
        payload["media"] = media
    return payload


def zernio_list_accounts(cfg: Config) -> list[ZernioAccount]:
    """List the accounts connected to the operator's Zernio workspace (GET /accounts) so the Go-Live tab
    can map each FanOps channel to a Zernio account _id WITHOUT hand-editing accounts.json. Returns
    [ZernioAccount(id, name, platform)]. 401 -> typed ZernioAuthError (halt); other non-2xx -> RuntimeError.
    Response SHAPE is an INTEGRATION CHECKPOINT: accept a bare list OR {"accounts":[...]}, pull _id +
    platform + a display name per item, SKIP a malformed entry (no usable id / not a dict) rather than raise."""
    headers = {"Authorization": f"Bearer {_key(cfg)}"}
    resp = requests.get(f"{_base(cfg)}/accounts", headers=headers, timeout=30)
    if resp.status_code == 401:
        raise ZernioAuthError("Zernio 401 on accounts — check ZERNIO_API_KEY (response body withheld)")
    if resp.status_code >= 300:
        raise RuntimeError(f"Zernio accounts failed ({resp.status_code}): {(resp.text or '')[:200]}")
    body = resp.json()
    items = body.get("accounts") if isinstance(body, dict) else body
    if not isinstance(items, list):
        return []
    out: list[ZernioAccount] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        aid = it.get("_id") or it.get("id")
        if not (isinstance(aid, str) and aid):
            continue
        platform = it.get("platform") or ""
        name = it.get("name") or it.get("displayName") or platform or aid
        out.append(ZernioAccount(id=aid, name=str(name), platform=str(platform)))
    return out


def zernio_check_auth(cfg: Config) -> bool:
    """Cheap auth probe for the Go-Live 'Save & test' button: hit GET /accounts and report whether the
    key works. True on success, raise ZernioAuthError on 401 (so the surface can name the key), False on
    any other failure (bad URL, 5xx, network) — the test must never crash the request handler. The
    swallowed (non-401) failure is LOGGED with its type + truncated message; never logs the key."""
    try:
        zernio_list_accounts(cfg)
        return True
    except ZernioAuthError:
        raise
    except Exception as exc:
        _log.warning("Zernio auth probe failed (treating as unreachable): %s: %s",
                     type(exc).__name__, str(exc)[:140])
        return False


class ZernioPoster:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base = _base(cfg)
        self.headers = {"Authorization": f"Bearer {_key(cfg)}", "Content-Type": "application/json"}

    def publish(self, led: Ledger, post_id: str) -> Ledger:
        post = led.posts[post_id]
        payload = build_zernio_payload(account_id=post.account_id, platform=post.platform.value,
                                       content=post.caption, media_urls=post.media_urls,
                                       scheduled_time=post.scheduled_time)
        delay, last = 1.0, None
        for _ in range(_MAX_RETRIES):
            try:
                resp = requests.post(f"{self.base}/posts", headers=self.headers, json=payload, timeout=30)
            except requests.exceptions.RequestException as exc:
                # Body may have landed on Zernio (the response, not the request, was lost) — ambiguous,
                # park for reconcile, never re-POST into a possible second live post (no idempotency key).
                post.state = PostState.needs_reconcile
                post.error_reason = f"zernio network error, may be live: {str(exc)[:160]}"
                return led
            last = resp
            if resp.status_code in (200, 201):
                sid = None
                try:
                    sid = _extract_zernio_id(resp.json())
                except Exception:
                    sid = None
                if not sid:
                    post.state = PostState.needs_reconcile
                    post.error_reason = "zernio 2xx but no recognizable post id (body withheld)"
                    return led
                post.state = PostState.submitted
                post.submission_id = sid
                post.public_url = safe_public_url(None) or post.public_url   # no permalink in the API yet
                return led
            if resp.status_code == 401:
                raise ZernioAuthError("Zernio 401 unauthorized — check ZERNIO_API_KEY (response body withheld)")
            if 500 <= resp.status_code < 600:
                # Ambiguous after the body was sent (no idempotency key) — park, do NOT re-POST.
                post.state = PostState.needs_reconcile
                post.error_reason = f"zernio {resp.status_code}, may be live (reconcile by hand) — body withheld"
                return led
            if resp.status_code == 429:
                time.sleep(delay + random.uniform(0, delay)); delay *= 2; continue
            break                                            # other 4xx -> fail
        # Never downgrade an ambiguous-live park to `failed` (failed is re-queueable -> double-post risk).
        if post.state is not PostState.needs_reconcile:
            post.state = PostState.failed
            post.error_reason = f"zernio {getattr(last, 'status_code', '?')} (body withheld)"
        return led
