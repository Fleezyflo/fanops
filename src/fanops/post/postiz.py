"""Postiz poster backend — the FREE, self-hosted alternative to Blotato (FANOPS_POSTER=postiz).

FanOps stays the clip+caption engine; a self-hosted Postiz instance (AGPL, github.com/gitroomhq/
postiz-app) is the distribution layer. Same swappable-poster slot as Blotato: build the post body,
POST it, map the response to the ledger's submit/reconcile/fail states with the SAME asymmetric-retry
safety (a bad key halts by type; a 5xx/timeout after the body was sent parks needs_reconcile, never
re-POSTs — Postiz, like Blotato, has no idempotency key).

REST contract (docs.postiz.com/public-api): Authorization: {apiKey} header; POST /public/v1/upload
(multipart) -> {id, path@uploads.postiz.com}; POST /public/v1/posts with
{type, date, shortLink, tags, posts:[{integration:{id}, value:[{content, image:[...]}], settings:{__type}}]}.
The created-post RESPONSE id key and the image-ref shape are INTEGRATION CHECKPOINTS — confirm against
your Postiz version's API; the offline tests lock the SHAPE. accounts.json `account_id` carries the
Postiz INTEGRATION id (from GET /public/v1/integrations) for a postiz deployment."""
from __future__ import annotations
import logging
import random
import time
from pathlib import Path
from typing import NamedTuple
import requests
from fanops.config import Config
from fanops.errors import PostizAuthError
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.text import safe_public_url

_log = logging.getLogger("fanops.post.postiz")
_MAX_RETRIES = 4
_PUBLIC = "/public/v1"


class PostizIntegration(NamedTuple):
    """One connected Postiz channel from GET /public/v1/integrations. `id` is what accounts.json's
    per-platform integrations[platform] (or the shared account_id fallback) carries for a postiz
    deployment. A typed row (W7) rather than a bare dict — consumers use .id/.name/.platform."""
    id: str
    name: str
    platform: str


def _base(cfg: Config) -> str:
    url = cfg.postiz_url
    if not url:
        raise RuntimeError("POSTIZ_URL missing — set it to your Postiz instance (e.g. https://api.postiz.com).")
    return url.rstrip("/")

def _key(cfg: Config) -> str:
    k = cfg.postiz_api_key
    if not k:
        raise PostizAuthError("POSTIZ_API_KEY missing — cannot use the Postiz backend.")
    return k


def _extract_postiz_id(body) -> str | None:
    # Postiz's create-post response id key isn't pinned in the public docs (integration checkpoint).
    # Accept the likely aliases + a nested posts[0].id, ignore non-str/empty; None when none present.
    if isinstance(body, list):
        body = body[0] if body else None
    if not isinstance(body, dict):
        return None
    for k in ("id", "postId", "submissionId"):
        v = body.get(k)
        if isinstance(v, str) and v:
            return v
    posts = body.get("posts")
    if isinstance(posts, list) and posts and isinstance(posts[0], dict):
        return _extract_postiz_id(posts[0])
    return None


def _postiz_permalink(cfg: Config, post_id: str | None) -> str | None:
    """The single chokepoint for "what PUBLIC URL do we record for a published Postiz post" (P2) —
    ships returning None, ALWAYS, today. Postiz's public API exposes NO social permalink and NO
    dashboard URL on any response (GET /public/v1/posts -> {id, publishDate, state, integration,
    content}, Context7-verified), and Postiz documents no stable public per-post page path. A *guessed*
    dashboard link (e.g. {POSTIZ_URL}/.../{post_id}) would 404 on the operator's self-hosted calendar
    UI — worse than None. So this stays None until/unless the route is VERIFIED against the operator's
    Postiz version (integration checkpoint); flipping it on is then a one-line change that BOTH the
    publish 2xx branch and the reconcile read pick up. NOT the IG/TikTok permalink. The true social URL
    stays operator-settable via `fanops resolve <id> published --url <url>` / Studio mark-published."""
    if not post_id:                                  # no confirmed id -> never a link
        return None
    return None                                      # route unverified -> None (build the URL here once verified)


def _postiz_image(u: str) -> dict:
    # postiz_upload_media returns "id|path"; this Postiz version requires BOTH on image[] (it validates
    # id as a string AND the path's file extension). Split them back out; defensively fall back to
    # id-only / path-only for any legacy single-value entry.
    if "|" in u:
        mid, mpath = u.split("|", 1); return {"id": mid, "path": mpath}
    return {"path": u} if u.startswith("http") else {"id": u}

def build_postiz_payload(*, integration_id: str, platform: str, content: str,
                         media_urls: list[str], scheduled_time: str | None) -> dict:
    # image[] references media ALREADY uploaded to Postiz — this version requires BOTH the upload's
    # `id` AND its public `path`. postiz_upload_media returns them joined "id|path"; _postiz_image
    # splits them. type=schedule with the post's own date — Postiz schedules it (a past date posts
    # ~now). __type names the platform; post_type is REQUIRED, one of "post"/"story" (feed/reel vs story).
    images = [_postiz_image(u) for u in (media_urls or []) if u]
    return {"type": "schedule", "date": scheduled_time, "shortLink": False, "tags": [],
            "posts": [{"integration": {"id": integration_id},
                       "value": [{"content": content, "image": images}],
                       "settings": {"__type": platform, "post_type": "post"}}]}


def postiz_upload_media(cfg: Config, path: Path) -> str:
    """Upload a local file to Postiz (multipart POST /public/v1/upload) -> "id|path": the upload's
    media id AND its public URL, joined (this Postiz version's image[] requires BOTH). 401 -> typed
    PostizAuthError (halt)."""
    headers = {"Authorization": _key(cfg)}
    with open(path, "rb") as fh:
        resp = requests.post(f"{_base(cfg)}{_PUBLIC}/upload", headers=headers,
                             files={"file": (Path(path).name, fh)}, timeout=120)
    if resp.status_code == 401:
        raise PostizAuthError("Postiz 401 on media upload — check POSTIZ_API_KEY (response body withheld)")
    if resp.status_code >= 300:
        raise RuntimeError(f"Postiz upload failed ({resp.status_code}) — body withheld")   # body may echo the auth header (reaches error_reason via _submit_one)
    body = resp.json()
    media_id = body.get("id") if isinstance(body, dict) else None
    media_path = body.get("path") if isinstance(body, dict) else None
    if not (media_id and media_path):
        raise RuntimeError(f"Postiz upload response missing id/path; got keys {sorted(body) if isinstance(body, dict) else type(body)}")
    return f"{media_id}|{media_path}"


def postiz_list_integrations(cfg: Config) -> list[PostizIntegration]:
    """List the channels connected to the operator's Postiz instance (GET /public/v1/integrations) so
    the Studio Go-Live tab can map each FanOps channel to a Postiz integration id WITHOUT the operator
    hand-pasting it into accounts.json. Returns [PostizIntegration(id, name, platform)] — `id` is what
    accounts.json carries per-platform for a postiz deployment. 401 -> typed PostizAuthError (halt); any
    other non-2xx -> RuntimeError. The response SHAPE is an INTEGRATION CHECKPOINT (not pinned in the
    public docs): accept a bare list OR {"integrations":[...]}, pull id + a display name + platform per
    item, and SKIP a malformed entry (no usable id / not a dict) rather than raise — a live verify happens
    when the operator clicks Refresh, and a manual id paste stays available as the fallback."""
    headers = {"Authorization": _key(cfg)}
    resp = requests.get(f"{_base(cfg)}{_PUBLIC}/integrations", headers=headers, timeout=30)
    if resp.status_code == 401:
        raise PostizAuthError("Postiz 401 on integrations — check POSTIZ_API_KEY (response body withheld)")
    if resp.status_code >= 300:
        raise RuntimeError(f"Postiz integrations failed ({resp.status_code}): {(resp.text or '')[:200]}")
    body = resp.json()
    items = body.get("integrations") if isinstance(body, dict) else body
    if not isinstance(items, list):
        return []
    out: list[PostizIntegration] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        iid = it.get("id")
        if isinstance(iid, bool):                        # bool is an int subclass — never a valid id
            continue
        if isinstance(iid, int):
            iid = str(iid)                               # coerce a numeric id to the string accounts.json stores
        if not (isinstance(iid, str) and iid):
            continue
        platform = it.get("identifier") or it.get("platform") or ""
        name = it.get("name") or it.get("displayName") or platform or iid
        out.append(PostizIntegration(id=iid, name=str(name), platform=str(platform)))
    return out


def postiz_check_auth(cfg: Config) -> bool:
    """Cheap auth probe for the Go-Live 'Save & test' button: hit the integrations endpoint and report
    whether the key works. True on success, raise PostizAuthError on 401 (so the surface can name the
    key), False on any other failure (bad URL, 5xx, network) — the test must never crash the request
    handler. The swallowed (non-401) failure is LOGGED with its type + truncated message so a silent
    'auth failed' is diagnosable (W8); the message carries response text / a network error, never the
    key (the Authorization header is never echoed into an exception). NEVER returns or logs the key."""
    try:
        postiz_list_integrations(cfg)
        return True
    except PostizAuthError:
        raise
    except Exception as exc:
        _log.warning("Postiz auth probe failed (treating as unreachable): %s: %s",
                     type(exc).__name__, str(exc)[:140])
        return False


class PostizPoster:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base = _base(cfg)
        self.headers = {"Authorization": _key(cfg), "Content-Type": "application/json"}

    def publish(self, led: Ledger, post_id: str) -> Ledger:
        post = led.posts[post_id]
        payload = build_postiz_payload(integration_id=post.account_id, platform=post.platform.value,
                                       content=post.caption, media_urls=post.media_urls,
                                       scheduled_time=post.scheduled_time)
        delay, last = 1.0, None
        for _ in range(_MAX_RETRIES):
            try:
                resp = requests.post(f"{self.base}{_PUBLIC}/posts", headers=self.headers, json=payload, timeout=30)
            except requests.exceptions.RequestException as exc:
                # Body may have landed on Postiz (the response, not the request, was lost) — ambiguous,
                # park for reconcile, never re-POST into a possible second live post.
                post.state = PostState.needs_reconcile
                post.error_reason = f"postiz network error, may be live: {str(exc)[:160]}"
                return led
            last = resp
            if resp.status_code in (200, 201):
                sid = None
                try:
                    sid = _extract_postiz_id(resp.json())
                except Exception:
                    sid = None
                if not sid:
                    post.state = PostState.needs_reconcile
                    post.error_reason = "postiz 2xx but no recognizable post id (body withheld)"   # body may echo the auth header -> never persist it
                    return led
                post.state = PostState.submitted
                post.submission_id = sid
                # P2: record a public URL ONLY on the confirmed-submitted branch (no confirmed id ->
                # no link). _postiz_permalink is None today (no URL in the API); `or post.public_url`
                # keeps any operator-set link and makes this a no-op until the route is verified. M2:
                # safe_public_url guards a future permalink to https-only (never persist a dead link).
                post.public_url = safe_public_url(_postiz_permalink(self.cfg, sid)) or post.public_url
                return led
            if resp.status_code == 401:
                raise PostizAuthError("Postiz 401 unauthorized — check POSTIZ_API_KEY (response body withheld)")
            if 500 <= resp.status_code < 600:
                # Ambiguous after the body was sent (no idempotency key) — park, do NOT re-POST.
                post.state = PostState.needs_reconcile
                post.error_reason = f"postiz {resp.status_code}, may be live (reconcile by hand) — body withheld"   # body may echo the auth header
                return led
            if resp.status_code == 429:
                time.sleep(delay + random.uniform(0, delay)); delay *= 2; continue
            break                                            # other 4xx -> fail
        # ECC fix #17 (defensive): never downgrade an ambiguous-live post to `failed` (failed is
        # re-queueable -> double-post risk). Today the 5xx branch returns before here, but guard it
        # so a future edit to the retry/return flow can't strand a needs_reconcile post as failed.
        if post.state is not PostState.needs_reconcile:
            post.state = PostState.failed
            post.error_reason = f"postiz {getattr(last, 'status_code', '?')} (body withheld)"   # body may echo the auth header -> never persist it
        return led
