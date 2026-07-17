"""Zernio poster backend — a HOSTED scheduler (FANOPS_POSTER=zernio, or a per-account override). Lets
FanOps publish TikTok WITHOUT passing TikTok app review: Zernio owns the TikTok app/OAuth, so the
operator connects their TikTok accounts inside Zernio's dashboard and FanOps only needs an API key + the
resulting account _ids. Same swappable-poster slot as Postiz, SAME asymmetric-retry safety: a bad key
halts the queue by TYPE (ZernioAuthError); a 5xx / network drop after the body was sent parks
needs_reconcile rather than re-POST a possible live post (idempotency note below).

REST contract (OpenAPI 3.1.0 `Zernio API v1.0.4`, retrieved 2026-07-16; base https://zernio.com/api/v1):
Authorization: Bearer <sk_…>; POST /posts {content, publishNow:true, platforms:[{platform, accountId}],
**mediaItems:[{type, url},…]**}; GET /accounts -> {accounts:[{_id, platform, name}]}; media upload is
POST /media/presign + a signed PUT (zernio_upload_media). publishNow:true because FanOps already gated the
schedule (a post sits `queued` until due, then publish_due fires) — we don't hand Zernio the schedule.
accounts.json integrations[platform] carries the Zernio account _id for a zernio surface (which backend
that id belongs to lives in accounts.json `backends`).

IDEMPOTENCY — current bounded truth (report 09 §7):
  · Zernio DOES document an optional `x-request-id` header on POST /posts — same-attempt idempotency for
    ~5 minutes; a repeat returns HTTP 200 with the original post in `existingPost`. A separate 24h
    content-hash 409 carries `details.existingPostId`.
  · **FanOps does NOT send it yet.** `_extract_zernio_id` has no `existingPost` branch, so the header
    ALONE would misparse an idempotent replay as "no id" -> needs_reconcile, filing a SUCCESSFUL publish
    as ambiguous. Header and parser are inseparable.
  · FanOps therefore continues to rely on the **queued-only claim check** + **needs_reconcile** for
    cross-pass safety. That stays necessary regardless: x-request-id's ~5-minute window cannot cover
    cross-pass republication (daemon interval 600s), which is exactly what the invariant guards.
  · **REQUIRED separate follow-up before the first production requeue:** x-request-id + `existingPost`
    parsing + 409 handling, landed together.

The create-post RESPONSE id key stays an INTEGRATION CHECKPOINT — `existingPost` is prose-only in the spec
(never schematised) — so the offline tests lock the SHAPE and the operator verifies live at first publish."""
from __future__ import annotations
import logging
import random
import re
import time
from pathlib import Path
from typing import NamedTuple
import requests
from fanops.config import Config
from fanops.errors import ZernioAuthError, redact
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.text import safe_public_url
from fanops.post.compress import maybe_shrink_for_cap

_log = logging.getLogger("fanops.post.zernio")
_MAX_RETRIES = 4
_PUBLISH_TRANSIENT_MAX = _MAX_RETRIES   # MOL-115: connection/timeout retries before parking needs_reconcile


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


def _tiktok_settings() -> dict:
    # Zernio docs (2026-06-29): TikTok posts REQUIRE platformSpecificData.tiktokSettings with these
    # booleans + privacy_level — omitting them yields 400 "require media content" even when media is present.
    return {"privacy_level": "PUBLIC_TO_EVERYONE", "allow_comment": True, "allow_duet": True,
            "allow_stitch": True, "content_preview_confirmed": True, "express_consent_given": True}


def _zernio_media_url(u: str) -> str:
    # Postiz upload cache stores "id|https://…"; Zernio needs the bare hosted URL only.
    if "|" in u:
        _, rest = u.split("|", 1)
        if rest.startswith("http"):
            return rest
    return u

def build_zernio_payload(*, account_id: str, platform: str, content: str,
                         media_urls: list[str], scheduled_time: str | None) -> dict:
    # publishNow:true — FanOps owns the schedule (publish_due fired this post because it's due), so we do
    # NOT pass scheduledFor/timezone (kept in the signature for parity / future use). platforms[] targets
    # ONE Zernio account (a FanOps Post is one surface). mediaItems[] is the CURRENT documented media key
    # (OpenAPI v1.0.4), never the legacy `media`; it carries the presign publicUrl from zernio_upload_media.
    # TikTok surfaces also need platformSpecificData.tiktokSettings (privacy + consent flags).
    # IDEMPOTENCY: Zernio documents an optional x-request-id (~5-min same-attempt) but FanOps does NOT send
    # it yet, and the header without an `existingPost` parser is worse than neither (module docstring) — so
    # a re-POST would still DOUBLE-publish. Cross-pass safety therefore rests on the queued-only claim
    # check (run.py publish_due iterates PostState.queued + the under-lock claim re-checks `queued`) AND on
    # never downgrading needs_reconcile — a submitting/submitted/needs_reconcile post is structurally never
    # re-submitted. See test_needs_reconcile_post_is_never_republished (tests/test_channel_provider.py).
    plat = {"platform": platform, "accountId": account_id}
    if platform == "tiktok":
        plat["platformSpecificData"] = {"tiktokSettings": _tiktok_settings()}
    payload: dict = {"content": content, "publishNow": True, "platforms": [plat]}
    media = [_zernio_media_url(u) for u in (media_urls or []) if u]
    if media:
        payload["mediaItems"] = [{"type": "video", "url": u} for u in media]
    return payload


_SIGNED_Q = re.compile(r"([?&](?:X-Amz-Signature|X-Amz-Credential|X-Amz-Security-Token|Signature|sig)=)[^&\s\"']+",
                       re.I)


def _scrub_signed(s: str) -> str:
    """Blank the credential VALUES in a signed-storage URL, keeping the param NAME as a breadcrumb. redact()
    knows only the API key, but a presigned uploadUrl carries its OWN upload credential (report 09 §8.4)."""
    return _SIGNED_Q.sub(r"\1<redacted>", s)


def _evidence(cfg: Config, resp) -> str:
    """Bounded, redacted RESPONSE evidence — closes the sibling-parity gap with zernio_list_accounts, which
    has always redact()ed its body while this path withheld it entirely. Carries `Allow` on a 405: RFC 9110
    REQUIRES the server to name the permitted methods there, so dropping it is why four burned posts yielded
    exactly one integer (report 09 §6.5). Signatures are scrubbed BEFORE the length cap, so a value straddling
    the cut can't survive it — the same redact-then-truncate rule redact() applies to keys."""
    allow = (getattr(resp, "headers", None) or {}).get("Allow")
    body = redact(_scrub_signed(resp.text or ""), cfg.zernio_api_key, limit=400)
    return (f"Allow={allow!r} " if allow else "") + f"body={body!r}"


def _scrubbed_transport(exc: Exception, stage: str, cfg: Config | None = None) -> BaseException:
    """A requests exception raised off a SIGNED url is not safe to propagate: str(exc) embeds the full URL
    ("Max retries exceeded with url: /t/v.mp4?X-Amz-Signature=…") and exc.request.url holds a second copy,
    while run.py's publish handler redacts only the two API KEYS — so it would land verbatim in the ledger's
    error_reason, the Studio UI, and the log (report 09 §8.5).

    Re-raise the SAME CLASS, never RuntimeError: _is_transient_publish_error classifies a RequestException by
    TYPE (ConnectionError/Timeout -> transient, retried) but a RuntimeError by MESSAGE SUBSTRING — so a
    RuntimeError wrap would silently make a ConnectionError terminal and burn the post on the first network
    blip, while a Timeout, whose class name happens to contain "timeout", stayed transient (report 09 §8.4.1).
    The fresh instance also carries no request/response, so exc.request.url cannot leak either. A class that
    won't take a bare message degrades to RequestException — non-transient, the safe direction, and only an
    exotic non-transport error can land there."""
    name = type(exc).__name__
    if stage == "signed-put":
        msg = f"Zernio signed upload transport failed ({name})"      # class + stage ONLY: no str/repr, no host, no url
    else:
        detail = redact(_scrub_signed(str(exc)), cfg.zernio_api_key if cfg else "", limit=200)
        msg = f"Zernio {stage} transport failed ({name}): {detail}"  # the presign url is not a credential
    try:
        return type(exc)(msg)
    except Exception:
        return requests.exceptions.RequestException(msg)


def _put_signed(upload_url: str, path: Path, ctype: str):
    """The signed PUT: raw bytes, matching Content-Type, and NO Authorization header — the URL carries the
    signature, and presenting the key would hand it to third-party storage. Transport errors are scrubbed."""
    with open(path, "rb") as fh:
        try:
            return requests.put(upload_url, data=fh, headers={"Content-Type": ctype}, timeout=300)
        except requests.exceptions.RequestException as exc:
            raise _scrubbed_transport(exc, "signed-put") from None


def zernio_upload_media(cfg: Config, path: Path, *, account_id: str | None = None) -> str:
    """Upload a local file to Zernio via the OFFICIAL presigned flow and return the public URL to reference
    in mediaItems[] (OpenAPI 3.1.0 `Zernio API v1.0.4`, paths./v1/media/presign, retrieved 2026-07-16):
      1) POST {base}/media/presign {"filename","contentType","size"} -> {uploadUrl, publicUrl, key, expiresIn}
      2) PUT <uploadUrl> raw bytes — Content-Type MUST match presign's contentType, and NO Authorization
      3) the caller puts publicUrl in mediaItems[]; the PUT body is never parsed for it

    Supersedes the reverse-engineered /media/upload-token + POST /media/upload pair ("DISCOVERED LIVE
    2026-06-29") — an END-USER-FLOW endpoint Zernio never published a contract for, which now answers 405 and
    burned four posts on 2026-07-16. There is deliberately NO fallback to it: it is not a published path, the
    spec scopes it away from programmatic use, and it can now only fail (report 09 §6, §8.6).

    account_id is retained for call-site compatibility (media._uploader_kwargs passes it) and is UNUSED —
    presign is account-agnostic, unlike the per-account token mint it replaces. 401 -> typed ZernioAuthError;
    other non-2xx -> RuntimeError carrying bounded, redacted evidence."""
    ctype = "video/mp4"                                  # a contentType enum member (presign schema)
    cap = cfg.zernio_max_upload_bytes                    # UNCHANGED: the 4 MB legacy cap is its own fix (report 09 §4.5)
    path = maybe_shrink_for_cap(cfg, path, cap, label="zernio")
    size = path.stat().st_size                           # POST-shrink — pre-validation is only meaningful for the bytes we PUT
    if size > cap:
        raise RuntimeError(f"zernio oversize: {size} bytes > {cap} — re-render short")
    # Step 1 — presign. Bearer REQUIRED here (unlike the PUT). `size` is optional but documented for
    # pre-validation (max 5 GB), so a mismatch fails on this cheap call instead of after a multi-MB PUT.
    try:
        r = requests.post(f"{_base(cfg)}/media/presign",
                          headers={"Authorization": f"Bearer {_key(cfg)}", "Content-Type": "application/json"},
                          json={"filename": path.name, "contentType": ctype, "size": size}, timeout=30)
    except requests.exceptions.RequestException as exc:
        raise _scrubbed_transport(exc, "presign", cfg) from None
    if r.status_code == 401:
        raise ZernioAuthError("Zernio 401 on media presign — check ZERNIO_API_KEY (response body withheld)")
    if r.status_code >= 300:
        raise RuntimeError(f"Zernio presign failed ({r.status_code}): {_evidence(cfg, r)}")
    try:
        body = r.json(); upload_url = body.get("uploadUrl"); public_url = body.get("publicUrl")
    except Exception:
        upload_url = public_url = None
    if not upload_url or not public_url:
        raise RuntimeError("Zernio presign 2xx but no uploadUrl/publicUrl (body withheld)")
    # Step 2 — signed PUT. A 401 HERE is an upload failure, not a key problem: the signed url carries no
    # Bearer, so it must NOT raise ZernioAuthError (that would halt the whole run over one bad signature).
    resp = _put_signed(upload_url, path, ctype)
    if resp.status_code >= 300:
        raise RuntimeError(f"Zernio signed upload failed ({resp.status_code}): {_evidence(cfg, resp)}")
    return public_url


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
        raise RuntimeError(f"Zernio accounts failed ({resp.status_code}): {redact(resp.text, cfg.zernio_api_key)}")
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
        for attempt in range(_MAX_RETRIES):
            try:
                resp = requests.post(f"{self.base}/posts", headers=self.headers, json=payload, timeout=30)
            except requests.exceptions.RequestException as exc:
                # Pre-send ConnectTimeout blips are safe to retry; ConnectionError may mean the body landed — park immediately (H01).
                if isinstance(exc, requests.exceptions.ConnectTimeout) and attempt < _MAX_RETRIES - 1:
                    time.sleep(delay + random.uniform(0, delay)); delay *= 2; continue
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
                post.public_url = safe_public_url(None) or post.public_url   # permalink captured later by ZernioStatusClient (reconcile); none on the publish 2xx — placeholder mirrors postiz.py
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
