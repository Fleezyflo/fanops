"""Postiz poster backend — the FREE, self-hosted poster backend (FANOPS_POSTER=postiz).

FanOps stays the clip+caption engine; a self-hosted Postiz instance (AGPL, github.com/gitroomhq/
postiz-app) is the distribution layer. A swappable-poster slot: build the post body,
POST it, map the response to the ledger's submit/reconcile/fail states with the SAME asymmetric-retry
safety (a bad key halts by type; a 5xx/timeout after the body was sent parks needs_reconcile, never
re-POSTs — Postiz has no idempotency key).

REST contract (docs.postiz.com/public-api): Authorization: {apiKey} header; POST /public/v1/upload
(multipart) -> {id, path@uploads.postiz.com}; POST /public/v1/posts with
{type, date, shortLink, tags, posts:[{integration:{id}, value:[{content, image:[...]}], settings:{__type}}]}.
The created-post RESPONSE id key and the image-ref shape are INTEGRATION CHECKPOINTS — confirm against
your Postiz version's API; the offline tests lock the SHAPE. accounts.json `account_id` carries the
Postiz INTEGRATION id (from GET /public/v1/integrations) for a postiz deployment."""
from __future__ import annotations
import hashlib
import hmac
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote, urlparse
import requests
from fanops.config import Config
from fanops.errors import PostizAuthError, redact
from fanops.ledger import Ledger
from fanops.models import ErrorKind, Platform, PostState, error_kind_for_http_status
from fanops.text import safe_public_url

_log = logging.getLogger("fanops.post.postiz")
_MAX_RETRIES = 4
_PUBLISH_TRANSIENT_MAX = _MAX_RETRIES   # MOL-115: connection/timeout retries before parking needs_reconcile
_PUBLIC = "/public/v1"
_YOUTUBE_TITLE_FLOOR = "New clip"   # YouTube REQUIRES a 2-100 char title; last-resort so no caller ever emits an invalid one
_POSTIZ_POST_TYPES = ("post", "story")   # the only tokens the vendor's non-YouTube settings DTO accepts (@IsDefined post_type)
_REELS_MEDIA_EXT = ".mp4"                # the extension the vendor's single-media REELS branch keys on (see _is_video_media)


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


def _postiz_permalink_from_body(body) -> str | None:
    """Extract a real https social permalink from a Postiz publish 2xx body when present — never invent.

    Only known social permalink fields are accepted (`releaseURL`, `permalink`, `publicUrl`,
    `platformPostUrl`). Generic `url`/`link` keys are ignored — they may be a Postiz dashboard or CDN URL."""
    if not isinstance(body, dict):
        return None
    for k in ("releaseURL", "permalink", "publicUrl", "platformPostUrl"):
        u = safe_public_url(body.get(k))
        if u:
            return u
    posts = body.get("posts")
    if isinstance(posts, list) and posts and isinstance(posts[0], dict):
        return _postiz_permalink_from_body(posts[0])
    return None


def _postiz_permalink(cfg: Config, post_id: str | None, body=None) -> str | None:
    """The single chokepoint for "what PUBLIC URL do we record for a published Postiz post" (P2).
    At publish time the immediate 2xx may carry no social permalink yet; when the response body DOES
    include a verified releaseURL (or equivalent), persist it here. Otherwise None — reconcile READ
    (PostizStatusClient) back-fills from a PUBLISHED row's releaseURL later. Never fabricate a
    dashboard guess link."""
    if body is not None:
        return _postiz_permalink_from_body(body)
    if not post_id:
        return None
    return None


def _postiz_image(u: str) -> dict:
    # postiz_upload_media returns "id|path"; this Postiz version requires BOTH on image[] (it validates
    # id as a string AND the path's file extension). Split them back out; defensively fall back to
    # id-only / path-only for any legacy single-value entry.
    if "|" in u:
        mid, mpath = u.split("|", 1); return {"id": mid, "path": mpath}
    return {"path": u} if u.startswith("http") else {"id": u}


def _is_video_media(u: str) -> bool:
    # Read the extension off the SAME `path` Postiz itself validates (_postiz_image splits the uploader's
    # "id|path"). An id-only legacy entry carries no path -> unprovable -> not a video, by design: the
    # single-video invariant below must be PROVEN, never assumed.
    path = _postiz_image(u).get("path") or ""
    return urlparse(path).path.lower().endswith(_REELS_MEDIA_EXT)


def _validate_ledger_media(post, post_type: str, media_urls: list[str]) -> None:
    """Pre-network refusal for a LEDGER post, at the publish boundary. The row-less cutover probe is
    exempt (it builds a deliberate text-only "post" through the builder), so this is the one place where
    every empty-media payload is a defect. Two rules, both raising ValueError BEFORE any POST:

    1. A ledger post always carries media — an empty set is a defect whatever the declared token.
    2. Single-video invariant: an Instagram post declared "post" MUST carry exactly one .mp4. Postiz maps
       post_type 'post' to media_type=REELS only for a single .mp4 (instagram.provider.js); >1 media ->
       VIDEO, an image -> FEED. This is what makes the insights boundary's post->REELS correspondence
       TRUE rather than lucky — the derived metric set would otherwise ask a FEED media for a reels-only
       metric (which Meta 400s). Do not relax it without a vendor citation.

    `_publish_one` contains the raise per-post (`failed` + ErrorKind.bad_payload), so this is loud and
    operator-recoverable, never a crash of the pass. Classification is typed at the write site (MOL-781)."""
    if not media_urls:
        raise ValueError(f"{post.platform.value} post {post.id} reached publish with no media — refusing to submit an empty post")
    if post.platform is Platform.instagram and post_type == "post" and not (
            len(media_urls) == 1 and _is_video_media(media_urls[0])):
        raise ValueError(f"instagram post {post.id} declares post_type 'post' but its media set is not a single video — "
                         f"Postiz maps post_type 'post' to REELS only for exactly one .mp4")


def publisher_refuses(post) -> str | None:
    """Pre-network refuse reason for this post, or None if publish would send. Dual of heal."""
    declared = (post.post_type or "").strip()
    if post.platform is not Platform.youtube and not declared:
        return (f"{post.platform.value} post {post.id} reached publish with undeclared post_type "
                f"— refusing to guess post|story")
    try:
        media_urls = list(post.media_urls or [])
        _validate_ledger_media(post, declared, media_urls)
    except ValueError as exc:
        return str(exc)
    return None


def rewrite_media_base(url: str, cfg: Config) -> str:
    """Rewrite loopback / private Postiz upload paths to FANOPS_MEDIA_PUBLIC_BASE so hosted backends
    (Postiz upload-from-url, Instagram pull-from-URL) can fetch the asset. Foreign https URLs and unset
    public base pass through unchanged."""
    base = cfg.media_public_base
    if not base or not url:
        return url
    if url.startswith(base + "/") or url == base:
        return url
    if url.startswith("file://"):
        return f"{base}/fanops/{Path(url.removeprefix('file://')).name}"
    low = url.lower()
    for prefix in ("http://127.0.0.1", "http://localhost", "https://127.0.0.1", "https://localhost"):
        if low.startswith(prefix):
            tail = urlparse(url).path.lstrip("/")
            return f"{base}/{tail}" if tail else url
    return url


def _r2_configured(cfg: Config) -> bool:
    return bool(cfg.media_public_base and cfg.r2_bucket and cfg.r2_access_key_id
                and cfg.r2_secret_access_key and cfg.r2_account_id)


def _r2_put_object(cfg: Config, *, key: str, body, content_type: str, payload_hash: str | None = None) -> None:
    """S3-compatible PUT to Cloudflare R2 (no boto3 — requests + SigV4 only). body may be bytes or a readable."""
    host = f"{cfg.r2_account_id}.r2.cloudflarestorage.com"
    region, service = "auto", "s3"
    t = datetime.now(timezone.utc)
    amz_date, date_stamp = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
    if payload_hash is None:
        payload_hash = hashlib.sha256(body if isinstance(body, (bytes, bytearray)) else body.read()).hexdigest()
        if hasattr(body, "seek"):
            body.seek(0)
    enc_key = quote(key, safe="/")
    canonical_uri = f"/{cfg.r2_bucket}/{enc_key}"
    canonical_headers = (f"content-type:{content_type}\nhost:{host}\n"
                           f"x-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n")
    signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"
    canonical_request = f"PUT\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    algorithm, scope = "AWS4-HMAC-SHA256", f"{date_stamp}/{region}/{service}/aws4_request"
    sts = (f"{algorithm}\n{amz_date}\n{scope}\n"
           f"{hashlib.sha256(canonical_request.encode()).hexdigest()}")
    def _hmac(k: bytes, msg: str) -> bytes:
        return hmac.new(k, msg.encode(), hashlib.sha256).digest()
    sk = ("AWS4" + cfg.r2_secret_access_key).encode()
    sig_key = _hmac(_hmac(_hmac(_hmac(sk, date_stamp), region), service), "aws4_request")
    signature = hmac.new(sig_key, sts.encode(), hashlib.sha256).hexdigest()
    auth = (f"{algorithm} Credential={cfg.r2_access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}")
    resp = requests.put(f"https://{host}/{cfg.r2_bucket}/{enc_key}", data=body, headers={
        "Content-Type": content_type, "Host": host, "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date, "Authorization": auth}, timeout=120)
    if resp.status_code >= 300:
        raise RuntimeError(f"R2 upload failed ({resp.status_code}) — body withheld")


def _mirror_media_to_r2(cfg: Config, path: Path) -> str:
    """Upload local media to R2 and return its public HTTPS URL under FANOPS_MEDIA_PUBLIC_BASE."""
    if not _r2_configured(cfg):
        raise RuntimeError("R2 mirroring not configured — set FANOPS_MEDIA_PUBLIC_BASE and R2_*")
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk: break
            h.update(chunk)
    digest = h.hexdigest()
    suffix = path.suffix if path.suffix else ".mp4"
    key = f"fanops/{digest[:32]}{suffix}"
    ctype = "video/mp4" if suffix.lower() == ".mp4" else "application/octet-stream"
    with open(path, "rb") as fh:
        _r2_put_object(cfg, key=key, body=fh, content_type=ctype, payload_hash=digest)
    return f"{cfg.media_public_base}/{key}"


def _postiz_upload_from_url(cfg: Config, url: str) -> str:
    """POST /public/v1/upload-from-url — fetch a public HTTPS asset into Postiz media storage."""
    headers = {"Authorization": _key(cfg), "Content-Type": "application/json"}
    pub = rewrite_media_base(url, cfg)
    resp = requests.post(f"{_base(cfg)}{_PUBLIC}/upload-from-url", headers=headers,
                         json={"url": pub}, timeout=120)
    if resp.status_code == 401:
        raise PostizAuthError("Postiz 401 on upload-from-url — check POSTIZ_API_KEY (response body withheld)")
    if resp.status_code >= 300:
        raise RuntimeError(f"Postiz upload-from-url failed ({resp.status_code}) — body withheld")
    body = resp.json()
    media_id = body.get("id") if isinstance(body, dict) else None
    media_path = body.get("path") if isinstance(body, dict) else None
    if not (media_id and media_path):
        raise RuntimeError(f"Postiz upload-from-url response missing id/path; got keys {sorted(body) if isinstance(body, dict) else type(body)}")
    return f"{media_id}|{rewrite_media_base(media_path, cfg)}"


def _youtube_tags(hashtags) -> list[dict]:
    # YouTube tags are bare keywords — strip a leading '#', dedupe, drop empties. Postiz's
    # @IsYoutubeTagsLength caps the total label length (~500); stop before it so a long hashtag list
    # can't 422 the whole post. Best-effort SEO, never load-bearing.
    out, used = [], 0
    for h in (hashtags or []):
        t = (h or "").lstrip("#").strip()
        if not t or any(t == o["label"] for o in out): continue
        if used + len(t) > 480: break
        out.append({"value": t, "label": t}); used += len(t)
    return out

def build_postiz_payload(*, integration_id: str, platform: str, content: str,
                         media_urls: list[str], scheduled_time: str | None, post_type: str,
                         title: str | None = None, hashtags: list[str] | None = None) -> dict:
    # image[] references media ALREADY uploaded to Postiz — this version requires BOTH the upload's
    # `id` AND its public `path`. postiz_upload_media returns them joined "id|path"; _postiz_image splits
    # them. type=schedule with the post's own date — Postiz schedules it (a past date posts ~now).
    # settings is Postiz's per-platform discriminated union (keyed on __type). Most platforms REQUIRE
    # post_type ("post"/"story"); YOUTUBE is the exception — YoutubeSettingsDto has NO post_type and
    # REQUIRES title (2-100) + type (privacy). The post content becomes the video DESCRIPTION; the title
    # is the per-account hook (the caller passes it, clamped to 100 here); hashtags map to tags.
    # `post_type` is REQUIRED and RENDERED (never hardcoded): it mirrors the vendor DTO's @IsDefined
    # post_type, so the caller declares the product and this builder never guesses one. Validated here,
    # BEFORE any network. Empty media is NOT rejected for "post": the cutover probe (cutover_postiz.py)
    # deliberately builds a row-less text-only "post". A LEDGER post's empty-media refusal lives at the
    # publish boundary (_validate_ledger_media), where it is unconditionally a defect.
    images = [_postiz_image(u) for u in (media_urls or []) if u]
    if platform == "youtube":
        yt_title = (title or "").strip()[:100]
        if len(yt_title) < 2: yt_title = _YOUTUBE_TITLE_FLOOR   # never emit a title Postiz's @MinLength(2) would 422
        settings = {"__type": "youtube", "title": yt_title, "type": "public",
                    "selfDeclaredMadeForKids": "no"}
        tags = _youtube_tags(hashtags)
        if tags: settings["tags"] = tags
    else:
        if post_type not in _POSTIZ_POST_TYPES:
            raise ValueError(f"invalid declared post_type {post_type!r} for {platform}: expected post|story")
        if post_type == "story" and not images:
            raise ValueError(f"declared post_type 'story' for {platform} carries no media — a story must carry media")
        settings = {"__type": platform, "post_type": post_type}
    return {"type": "schedule", "date": scheduled_time, "shortLink": False, "tags": [],
            "posts": [{"integration": {"id": integration_id},
                       "value": [{"content": content, "image": images}],
                       "settings": settings}]}


def postiz_upload_media(cfg: Config, path: Path, **kw) -> str:
    """Upload a local file to Postiz. When R2 mirroring is configured, mirror bytes to the public CDN
    first then POST /upload-from-url (Postiz SSRF-blocks localhost; IG needs internet-reachable URLs).
    Otherwise multipart POST /upload -> "id|path". 401 -> typed PostizAuthError (halt)."""
    if _r2_configured(cfg):
        return _postiz_upload_from_url(cfg, _mirror_media_to_r2(cfg, path))
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
    return f"{media_id}|{rewrite_media_base(media_path, cfg)}"


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
        raise RuntimeError(f"Postiz integrations failed ({resp.status_code}): {redact(resp.text, cfg.postiz_api_key)}")
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


class PostizHealth(NamedTuple):
    """Typed result of postiz_health_probe (R5/D13). Unlike the bare bool postiz_check_auth returned,
    this carries the WHY so a UI surface can act on it: `healthy` (does the BACKEND answer, not just
    nginx), `status_code` (the HTTP code, or None on a network/URL failure), and `hint` (an operator-
    facing one-liner — the status + a pointer, NEVER the key). The Postiz container's own health check
    is nginx-only, so a crash-looping Node backend still reports 'healthy' to Docker while returning
    502 here — this probe goes PAST nginx by exercising the real /integrations endpoint."""
    healthy: bool
    status_code: int | None
    hint: str


def postiz_health_probe(cfg: Config) -> PostizHealth:
    """Exercise the Postiz BACKEND (GET /integrations) and report typed health (R5/D13). This is the
    honest health read the operator surfaces need: the container's Docker health is nginx-only and LIES
    when the Node backend crash-loops (docs/POSTIZ_OPS.md), so a 502 here means 'up at the proxy, dead at
    the app'. NEVER raises (a banner render must never 500) and NEVER echoes the key: a 401 is reported as
    unhealthy-with-status (the auth-fault answer), a 5xx as unhealthy-with-status (backend down), a bad
    URL / network error as unhealthy with status_code=None. The swallowed failure is LOGGED with its type
    + truncated message (W8) so a silent 'unhealthy' is diagnosable; the message carries response text / a
    network error, never the Authorization header. `postiz_check_auth` is the back-compat bool wrapper."""
    try:
        postiz_list_integrations(cfg)
        return PostizHealth(True, 200, "")
    except PostizAuthError:
        # 401 is a real backend answer (bad key), not a crash — report it, don't raise (the banner
        # surface must not 500). postiz_check_auth still re-raises for the Go-Live 'Save & test' callsite.
        return PostizHealth(False, 401, "Postiz rejected the API key (401) — check POSTIZ_API_KEY (see docs/POSTIZ_OPS.md).")
    except Exception as exc:
        code = _status_of(exc)
        _log.warning("Postiz health probe failed (treating as unhealthy): %s: %s",
                     type(exc).__name__, str(exc)[:140])
        where = f" ({code})" if code is not None else ""
        return PostizHealth(False, code, f"Postiz backend unreachable{where} — the container's health "
                                         "check is nginx-only and can lie; see docs/POSTIZ_OPS.md.")


def _status_of(exc: Exception) -> int | None:
    # postiz_list_integrations raises RuntimeError("... failed (<code>): ...") on a non-2xx; pull the
    # code back out for the health hint. Best-effort — a network error (no code) yields None.
    import re as _re
    m = _re.search(r"\((\d{3})\)", str(exc))
    return int(m.group(1)) if m else None


def postiz_check_auth(cfg: Config) -> bool:
    """Cheap auth probe for the Go-Live 'Save & test' button: hit the integrations endpoint and report
    whether the key works. True on success, raise PostizAuthError on 401 (so the surface can name the
    key), False on any other failure (bad URL, 5xx, network) — the test must never crash the request
    handler. NOW a thin bool wrapper over postiz_health_probe (R5/D13): the typed probe is the single
    home of the /integrations exercise, this keeps the historical bool contract (INCLUDING the raise-on-
    401 the Go-Live save flow relies on to name the key). NEVER returns or logs the key."""
    # The probe never raises; re-derive the legacy raise-on-401 from the typed status so the Go-Live
    # save surface keeps naming the key. A non-401 unhealthy stays a quiet False (bad URL/5xx/network).
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

    def _youtube_title(self, post, led: Ledger | None = None) -> str:
        # YouTube REQUIRES a 2-100 char title. Use the owner-moment hook; floor to artist name when absent.
        t = ""
        if led is not None:
            clip = led.clips.get(post.parent_id)
            if clip is not None:
                m = led.moments.get(clip.parent_id)
                if m is not None:
                    t = (m.hook or "").strip()
        return t if len(t) >= 2 else self.cfg.artist_name

    def publish(self, led: Ledger, post_id: str) -> Ledger:
        post = led.posts[post_id]
        title = self._youtube_title(post, led) if post.platform is Platform.youtube else None
        # Postiz requires `date` to be a valid ISO 8601 string. A publish_now-claimed post (or any
        # untimed approval) carries scheduled_time=None; pass `now` so Postiz schedules it ~immediately
        # (per build_postiz_payload's comment, a past/now date posts ~now). Without this, Postiz 400s
        # with "date should not be null or undefined".
        from datetime import datetime, timezone
        from fanops.timeutil import iso_z
        sched = post.scheduled_time or iso_z(datetime.now(timezone.utc))
        media_urls = [rewrite_media_base(u, self.cfg) for u in (post.media_urls or [])]
        # The payload's post_type is RENDERED from the post's own declaration — never guessed here.
        # An undeclared IG row (post_type None/blank) is refused BEFORE any network; `_publish_one`
        # lands it `failed` with ErrorKind.bad_payload (MOL-781). YoutubeSettingsDto has no post_type
        # (mint leaves None); the IG refuse does not apply to YouTube — do not invent "post".
        declared = (post.post_type or "").strip()
        reason = publisher_refuses(post)
        if reason:
            raise ValueError(reason)
        # IG/TT: compose sentence + lock tags at send. YouTube keeps the sentence as description
        # and ships tags via settings.tags (`_youtube_tags`) — never dump the IG composed string.
        from fanops.caption import posted_text_for
        content = post.caption if post.platform is Platform.youtube else posted_text_for(self.cfg, led, post)
        payload = build_postiz_payload(integration_id=post.account_id, platform=post.platform.value,
                                       content=content, media_urls=media_urls,
                                       scheduled_time=sched, post_type=declared,
                                       title=title, hashtags=post.hashtags)
        delay, last = 1.0, None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = requests.post(f"{self.base}{_PUBLIC}/posts", headers=self.headers, json=payload, timeout=30)
            except requests.exceptions.RequestException as exc:
                if isinstance(exc, requests.exceptions.ConnectTimeout) and attempt < _MAX_RETRIES - 1:
                    time.sleep(delay + random.uniform(0, delay)); delay *= 2; continue
                # Body may have landed on Postiz (the response, not the request, was lost) — ambiguous,
                # park for reconcile, never re-POST into a possible second live post.
                led.set_post_state(post_id, PostState.needs_reconcile,
                                   error_reason=f"postiz network error, may be live: {str(exc)[:160]}")
                return led
            last = resp
            if resp.status_code in (200, 201):
                body = None
                sid = None
                try:
                    body = resp.json()
                    sid = _extract_postiz_id(body)
                except Exception as exc:
                    _log.warning("postiz publish: could not parse 2xx body for %s (%s)", post_id, exc)
                    body = None
                    sid = None
                if not sid:
                    led.set_post_state(post_id, PostState.needs_reconcile,
                                       error_reason="postiz 2xx but no recognizable post id (body withheld)")
                    return led
                led.set_post_state(post_id, PostState.submitted)
                post = led.posts[post_id]
                post.submission_id = sid
                post.public_url = safe_public_url(_postiz_permalink(self.cfg, sid, body)) or post.public_url
                return led
            if resp.status_code == 401:
                raise PostizAuthError("Postiz 401 unauthorized — check POSTIZ_API_KEY (response body withheld)")
            if 500 <= resp.status_code < 600:
                # Ambiguous after the body was sent (no idempotency key) — park, do NOT re-POST.
                led.set_post_state(post_id, PostState.needs_reconcile,
                                   error_reason=f"postiz {resp.status_code}, may be live (reconcile by hand) — body withheld")  # body may echo the auth header
                return led
            if resp.status_code == 429:
                time.sleep(delay + random.uniform(0, delay)); delay *= 2; continue
            break                                            # other 4xx -> fail
        # ECC fix #17 (defensive): never downgrade an ambiguous-live post to `failed` (failed is
        # re-queueable -> double-post risk). Today the 5xx branch returns before here, but guard it
        # so a future edit to the retry/return flow can't strand a needs_reconcile post as failed.
        if led.posts[post_id].state is not PostState.needs_reconcile:
            code = getattr(last, "status_code", None)
            kind = error_kind_for_http_status(code) if isinstance(code, int) else ErrorKind.unknown
            led.set_post_state(post_id, PostState.failed, error_kind=kind,
                               error_reason=f"postiz {code if code is not None else '?'} (body withheld)")  # body may echo the auth header -> never persist it
        return led
