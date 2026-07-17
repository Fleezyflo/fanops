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

IDEMPOTENCY (report 11, implemented 2026-07-17 — header + parser + 409, landed together because they are
inseparable: the header ALONE would misparse a replay as "no id" and file a SUCCESSFUL publish as ambiguous):
  · Every POST /posts carries `x-request-id` = _request_id(post): uuid5(_ZERNIO_REQ_NS, _request_name(post))
    where the name is the CANONICAL JSON array
    `json.dumps([ver, post.id, created_at, platform, account_id], ensure_ascii=False, separators=(",", ":"))`
    — NOT a delimiter join, which would ALIAS (["a|b","c"] and ["a","b|c"] flatten identically). STABLE per
    record INCARNATION x platform x RESOLVED Zernio account, so every retry of one attempt reuses it and
    Zernio replays instead of double-creating. NOT post.id alone: crosspost POPS a failed/rejected record and
    REMINTS it under the IDENTICAL post.id with a fresh created_at, and run.py refreshes account_id at
    publish — so one post.id denotes several distinct create operations, possibly to different Zernio
    accounts (report 11 §8).
  · A repeat inside Zernio's ~5-min window -> HTTP 200 + the original in `existingPost` -> IdempotentReplay
    -> `submitted`, the same ledger state as a first-time create (report 11 R-2).
  · Retries are bounded by _RETRY_DEADLINE_S on time.monotonic(), STRICTLY inside _IDEMPOTENCY_WINDOW_S:
    past the window the header is no longer honoured, so a late retry IS the double-post it exists to
    prevent. Past the deadline we never send again — we classify by whether anything may have landed.
  · The separate 24h content-hash 409 -> ReconciliationRequired + reconcile_candidate_id (EVIDENCE ONLY,
    never a submission_id). NEVER `failed`: Zernio is a SCHEDULER, so a 409 proves only that Zernio holds a
    MATCHING record — not platform publication, not ownership by this record, not completion (report 11 §3).
  · No request identity => NO network call. A fabricated discriminator would make two incarnations share
    one x-request-id — the exact collision the formula exists to prevent (report 11 §8.4).
  · The **queued-only claim check** + never downgrading **needs_reconcile** still carry CROSS-PASS safety
    and are not replaced: a ~5-minute window cannot span the 600s daemon interval. Idempotency closes the
    WITHIN-attempt hole; the claim invariant closes the CROSS-pass hole. Both are required.

The create-post RESPONSE id key stays an INTEGRATION CHECKPOINT — `existingPost` is prose-only in the spec
(never schematised, and 200 is not even in its responses map) — so the offline tests lock the SHAPE, the
parser is tolerant and fails to needs_reconcile (never to `failed`), and the operator verifies live at
first publish."""
from __future__ import annotations
import json
import logging
import random
import re
import time
import uuid
from pathlib import Path
from typing import NamedTuple
import requests
from fanops.config import Config
from fanops.errors import ZernioAuthError, fail_open, redact
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.models import PostState
from fanops.text import safe_public_url
from fanops.post.compress import maybe_shrink_for_cap
from fanops.post.zernio_outcome import (Created, IdempotentReplay, ReconciliationRequired, TerminalFailure,
                                        ZernioCreateResult)

_log = logging.getLogger("fanops.post.zernio")
_MAX_RETRIES = 4
_PUBLISH_TRANSIENT_MAX = _MAX_RETRIES   # MOL-115: connection/timeout retries before parking needs_reconcile

# Zernio's documented same-attempt idempotency window for x-request-id (~5 minutes, report 09 §7).
_IDEMPOTENCY_WINDOW_S = 300.0
# Our retry budget, STRICTLY inside that window: past it the header is no longer honoured, so a "retry"
# would be a fresh create — the exact double-post x-request-id exists to prevent. Measured on
# time.monotonic() (immune to a wall-clock step / NTP correction / DST), per publish call, and checked
# BEFORE each sleep so the next SEND — not the sleep — lands inside the deadline.
_RETRY_DEADLINE_S = 240.0
# PERMANENT constants. Changing either silently re-opens the double-post hole for every in-flight post
# (a retry would derive a DIFFERENT x-request-id than the send it is retrying, so Zernio would create a
# second post instead of replaying the first). Chosen once, 2026-07-17. Never regenerate, never "clean up".
_ZERNIO_REQ_NS = uuid.UUID("09105245-a8e0-4d28-ba02-c85ebab84cb3")
_REQ_NAME_V = "1"                       # formula version — bump ONLY with a migration story for in-flight posts


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


def _request_id(post) -> str:
    """The x-request-id for ONE create attempt: stable per (record incarnation x platform x RESOLVED Zernio
    account), so every retry of that attempt reuses it and Zernio replays rather than creating a second post.

    uuid5(ns, post.id) ALONE is INSUFFICIENT and was a false claim in report 11 Rev 2 (§0 D6): crosspost
    POPS a `failed`/`rejected` record and REMINTS it under the IDENTICAL post.id with a fresh created_at
    (crosspost.py `led.posts.pop(pid)` -> `add_post(Post(id=pid, ..., created_at=now))`), so one post.id
    denotes SEVERAL distinct create operations — a post.id-only name would hand a NEW incarnation the OLD
    incarnation's request identity, and Zernio would replay the old post instead of creating the new one.
    Hence the four-part name:
      · post.id       — the logical surface (content-addressed clip x account x platform)
      · created_at    — the per-INCARNATION discriminator: written at BIRTH only, never mutated on an
                        existing record, and NOT in _NET_POST_FIELDS (so finalize cannot overwrite it).
                        The remint stamps a fresh one, which is exactly what makes a remint a new identity.
      · platform      — already hashed into post.id via surface_key(), so this is redundant-but-explicit:
                        the name stays correct if pid's derivation ever changes.
      · account_id    — the resolved Zernio account ACTUALLY receiving this request. A genuine addition:
                        post.id carries the HANDLE, not the Zernio integration id, and run.py refreshes
                        account_id at publish (a Go-Live remap) BEFORE poster.publish — the same field
                        build_zernio_payload reads, so the id and the payload's accountId cannot disagree.

    The caller MUST have passed _require_request_identity(post) first: this function never invents a
    missing component."""
    return str(uuid.uuid5(_ZERNIO_REQ_NS, _request_name(post)))


def _request_name(post) -> str:
    """The canonical UUIDv5 name — a JSON array with fixed separators, NOT a delimiter join.

    `"|".join(...)` was WRONG and is corrected here: a raw delimiter join ALIASES, because the delimiter
    can appear inside a component. `["a|b", "c"]` and `["a", "b|c"]` both flatten to `a|b|c` — two DIFFERENT
    identities collapsing onto ONE x-request-id, which makes Zernio replay the wrong post. That is not
    theoretical: `account_id` is an operator-supplied Zernio integration id from accounts.json and is
    unconstrained, and `post.id` is only conventionally `post_<hex>`.

    JSON quotes and escapes every component, so the encoding is INJECTIVE: distinct tuples cannot produce
    the same name (a `"` or `|` inside a value is escaped/quoted, never confused with structure).
    `separators=(",", ":")` pins the bytes (no whitespace drift); `ensure_ascii=False` keeps Unicode literal,
    which `uuid.uuid5` then encodes as UTF-8 — deterministic either way, but fixed so it cannot drift.
    A list (not a dict) so ORDER is the schema and no key-sorting question exists."""
    return json.dumps([_REQ_NAME_V, post.id, post.created_at, post.platform.value, post.account_id],
                      ensure_ascii=False, separators=(",", ":"))


def _require_request_identity(post) -> TerminalFailure | None:
    """Refuse to POST when a request identity cannot be derived — returns TerminalFailure, or None to
    proceed. Called BEFORE the first send, so a refusal costs ZERO network calls (invariant I-10).

    Post.created_at is Optional[str] (models.py) — every row carries one in practice (all three mint sites
    stamp it; the v3 migration backfills any older row) but the TYPE permits None, and this design does not
    rest on an unenforced observation. Defaulting a missing component to "", to post.id, or to a fresh stamp
    is NOT an option: the first two make two different incarnations collide on ONE x-request-id, the third
    makes every attempt unique and silently disables idempotency altogether. Failing loudly is the only
    correct behavior — the reason is precise, the operator sees it in Review, and it is deliberately NOT
    phrased as a transient (is_transient_failure_reason must not match it, or the daemon would re-queue a
    row that cannot possibly succeed until its data is repaired). Pinned by the reason-classifier test."""
    missing = [n for n, v in (("created_at", post.created_at), ("account_id", post.account_id),
                              ("platform", getattr(post.platform, "value", None))) if not (v or "").strip()]
    if missing:
        return TerminalFailure("missing_request_identity",
                               f"cannot derive x-request-id: {','.join(missing)} absent — refusing to POST")
    return None


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


def _parse_create_body(body) -> ZernioCreateResult:
    """Classify a 2xx create body into Created / IdempotentReplay / ReconciliationRequired. NEVER returns
    TerminalFailure: a 2xx means Zernio accepted something, so "we can't read it" is ambiguous, never
    provably-not-accepted. `existingPost` is prose-only in the spec (never schematised, and 200 isn't even
    in its responses map), so every unreadable shape parks for reconcile — over-parking is free
    (needs_reconcile is structurally never re-POSTed and reconcile heals it), under-parking double-posts.

    An `existingPost` KEY present but unreadable is therefore NOT downgraded to Created off a sibling `id`:
    the key is Zernio saying "this is a replay", and which post that direct id then denotes is exactly what
    we cannot assume."""
    if isinstance(body, list):
        body = body[0] if body else None
    if not isinstance(body, dict):
        return ReconciliationRequired("success_no_id", "2xx body is not an object (body withheld)")
    direct = _extract_zernio_id(body)
    has_existing = "existingPost" in body
    ep = body.get("existingPost")
    replay = _extract_zernio_id(ep) if isinstance(ep, dict) else (ep if isinstance(ep, str) and ep else None)
    if direct and replay and direct != replay:
        # Two DIFFERENT ids means the response contract is not what we modelled. Adopt neither, and carry
        # no candidate: a candidate is a pointer we'd hand the operator as evidence, and we cannot say which
        # of the two it would even be.
        return ReconciliationRequired("conflicting_ids", f"id={direct!r} != existingPost id={replay!r}")
    if replay:
        return IdempotentReplay(replay)
    if has_existing:
        return ReconciliationRequired("replay_no_id", "existingPost present but carries no usable id (body withheld)")
    if direct:
        return Created(direct)
    return ReconciliationRequired("success_no_id", "2xx but no recognizable post id (body withheld)")


def _extract_409_candidate(body) -> str | None:
    """The 409's details.existingPostId — an UNPROVEN pointer, never an identity (report 11 §3). Absent /
    malformed -> None, never a raise: a 409 must reach needs_reconcile whatever its body looks like."""
    if not isinstance(body, dict):
        return None
    details = body.get("details")
    if not isinstance(details, dict):
        return None
    v = details.get("existingPostId")
    return v if isinstance(v, str) and v else None


def _retry_after_s(resp) -> float | None:
    """Retry-After in seconds, or None when absent/unparseable (the HTTP-date form is deliberately not
    parsed — the caller falls back to bounded backoff, which the deadline gates identically)."""
    v = (getattr(resp, "headers", None) or {}).get("Retry-After")
    if v is None:
        return None
    try:
        return max(0.0, float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _fits_deadline(started: float, wait: float) -> bool:
    """True when sleeping `wait` still leaves the NEXT SEND inside _RETRY_DEADLINE_S. monotonic-based, so a
    wall-clock jump can neither extend nor collapse the budget."""
    return (time.monotonic() - started) + wait < _RETRY_DEADLINE_S


def _breadcrumb(cfg: Config, post_id: str, outcome: str):
    """Adapt `errors.fail_open`'s printf-style `log` onto the HOUSE run.log channel.

    fail_open defaults to `_log.warning`, i.e. stderr — but run.log is where the operator actually looks, so
    a parse failure that only reaches stderr is a breadcrumb nobody reads. fail_open calls
    `log(fmt, site, type(exc).__name__, str(exc)[:200], exc_info=True)`; we keep the exception TYPE plus a
    bounded, redacted message and drop the traceback (run.log is single-line JSON). The message is redacted
    even though a JSONDecodeError carries only a position, never document content — the sink is the ledger's
    neighbour and the rule is redact-then-truncate, not "reason about whether this one can leak"."""
    def _log(_fmt="", site="", exc_type="", exc_str="", **_kw):
        get_logger(cfg)("publish", post_id, outcome, site=str(site)[:60],
                        err=f"{exc_type}: {redact(str(exc_str), cfg.zernio_api_key, limit=120)}")
    return _log


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
    # IDEMPOTENCY: the x-request-id is a HEADER, deliberately not built here — this function returns the
    # BODY, and the id is derived from the record's identity (post.id/created_at/platform/account_id), not
    # from the content. Two posts with byte-identical bodies are different creates; one record retried
    # twice is the SAME create. See _request_id + ZernioPoster._create (module docstring).
    # CROSS-PASS safety still rests on the queued-only claim check (run.py publish_due iterates
    # PostState.queued + the under-lock claim re-checks `queued`) AND on never downgrading needs_reconcile —
    # a submitting/submitted/needs_reconcile post is structurally never re-submitted. The ~5-min
    # idempotency window cannot span the 600s daemon interval, so it does NOT replace that invariant.
    # See test_needs_reconcile_post_is_never_republished (tests/test_channel_provider.py).
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

    Supersedes the /media/upload-token + POST /media/upload pair ("DISCOVERED LIVE 2026-06-29"): that
    upload-token flow is documented for END-USER workflows, and FanOps used it PROGRAMMATICALLY. The supported
    programmatic contract is /media/presign + the signed PUT above. The old route began answering 405 on
    2026-07-16, burning four posts. There is deliberately NO fallback to it: it is the wrong contract for this
    integration and is no longer operationally reliable (report 09 §6, §8.6).

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

    def _create(self, post) -> ZernioCreateResult:
        """ONE create attempt (with its bounded, in-window retries) -> a typed result. PRIVATE: the result
        never leaves this class. Raises ONLY ZernioAuthError, which must halt the whole run rather than burn
        one post (a bad key fails every post).

        Every send carries the SAME x-request-id, so a retry after a lost response, a 429, or a crash is a
        REPLAY (HTTP 200 + existingPost), not a second post. Every boundary where the request MAY have
        reached Zernio returns ReconciliationRequired — never TerminalFailure, because `failed` is
        re-queueable and re-queueing a landed post is the double-post this exists to prevent."""
        bad = _require_request_identity(post)
        if bad is not None:
            return bad                                   # ZERO network calls (I-10) — never fabricate an id
        rid = _request_id(post)
        payload = build_zernio_payload(account_id=post.account_id, platform=post.platform.value,
                                       content=post.caption, media_urls=post.media_urls,
                                       scheduled_time=post.scheduled_time)
        headers = dict(self.headers); headers["x-request-id"] = rid
        started = time.monotonic()
        delay, sent_any = 1.0, False
        for attempt in range(_MAX_RETRIES):
            try:
                resp = requests.post(f"{self.base}/posts", headers=headers, json=payload, timeout=30)
            except requests.exceptions.RequestException as exc:
                if isinstance(exc, requests.exceptions.ConnectTimeout):
                    # The connection was never established, so THIS attempt sent nothing. Retry inside the
                    # deadline; past it the verdict depends on whether an EARLIER attempt reached Zernio.
                    wait = delay + random.uniform(0, delay)
                    if attempt < _MAX_RETRIES - 1 and _fits_deadline(started, wait):
                        time.sleep(wait); delay *= 2; continue
                    if not sent_any:
                        return TerminalFailure("connect_timeout",
                                               f"could not connect after {attempt + 1} attempt(s); nothing was sent")
                    return ReconciliationRequired("connect_timeout_after_send",
                                                  f"an earlier attempt reached Zernio and this one could not re-check "
                                                  f"within {_RETRY_DEADLINE_S:.0f}s — may be live")
                # The body may have landed (the response, not the request, was lost) — ambiguous. Park; do
                # NOT re-POST. The retry that WOULD be safe is the in-deadline loop above; this exception
                # class is no evidence that a fresh send lands inside the window.
                sent_any = True
                return ReconciliationRequired("network_error_may_be_live",
                                              f"{type(exc).__name__}: {redact(str(exc), self.cfg.zernio_api_key, limit=160)}")
            sent_any = True                              # a RESPONSE proves the request reached Zernio
            if resp.status_code in (200, 201):
                parsed: ZernioCreateResult | None = None
                with fail_open("zernio.create.parse", log=_breadcrumb(self.cfg, post.id, "zernio_2xx_body_unparsed")):
                    parsed = _parse_create_body(resp.json())
                if parsed is not None:
                    return parsed
                # Non-JSON / unreadable 2xx: Zernio accepted SOMETHING we cannot identify. NEVER terminal.
                return ReconciliationRequired("success_unreadable_body", "2xx but the body did not parse — body withheld")
            if resp.status_code == 401:
                raise ZernioAuthError("Zernio 401 unauthorized — check ZERNIO_API_KEY (response body withheld)")
            if resp.status_code == 409:
                # R-3: a 409 is DUPLICATE-CONTENT and is NOT `failed`. Zernio is a hosted SCHEDULER, so this
                # proves only that Zernio holds a matching record within its 24h window — not platform
                # publication, not ownership by THIS post, not completion. The candidate is evidence for the
                # operator, never an identity (report 11 §3/§5).
                # A 409 must park whatever its body looks like — but the parse failure is NOT swallowed:
                # "Zernio named no post" and "Zernio may have named one we could not read" are DIFFERENT
                # facts, and only the second means the operator is missing a pointer that actually exists.
                # `read` is the sentinel that keeps them apart: fail_open swallows, so cand=None alone is
                # ambiguous between the two.
                cand, read = None, False
                with fail_open("zernio.409.parse", log=_breadcrumb(self.cfg, post.id, "zernio_409_body_unparsed")):
                    cand = _extract_409_candidate(resp.json())
                    read = True
                unread = "" if read else " (409 body unreadable — a candidate may exist but could not be read)"
                return ReconciliationRequired("duplicate_content_409",
                                              "Zernio reports duplicate content in its 24h window — identity UNPROVEN, "
                                              f"reconcile by hand{unread}", candidate_post_id=cand)
            if 500 <= resp.status_code < 600:
                return ReconciliationRequired("http_5xx", f"zernio {resp.status_code}, may be live (reconcile by hand) — body withheld")
            if resp.status_code == 429:
                # The request REACHED Zernio, so the create may already have landed — this is exactly the
                # R-1 branch that used to re-POST bare. Retry only if the wait still lands the next SEND
                # inside the deadline (past the window the header is dead and a "retry" is a second post).
                wait = _retry_after_s(resp)
                if wait is None:
                    wait = delay + random.uniform(0, delay)
                if attempt < _MAX_RETRIES - 1 and _fits_deadline(started, wait):
                    time.sleep(wait); delay *= 2; continue
                return ReconciliationRequired("rate_limited_may_be_live",
                                              f"429 and the retry budget ({_RETRY_DEADLINE_S:.0f}s) is spent — the create "
                                              f"may already have landed; body withheld")
            # Other 4xx: a verdict re-sending cannot change. The body stays WITHHELD (as before this fix) —
            # this error_reason is scanned by is_transient_failure_reason for the daemon re-queue, and a
            # response body echoing "timeout" or "(503)" would flip a terminal 4xx into a re-queue loop.
            return TerminalFailure(f"http_{resp.status_code}", f"({resp.status_code}) body withheld")
        # Unreachable: every branch returns or continues, and the last iteration cannot continue
        # (attempt < _MAX_RETRIES - 1 is False there). Belt-and-braces, never the re-queueable direction.
        return ReconciliationRequired("retries_exhausted", f"no verdict after {_MAX_RETRIES} attempts — may be live")

    def publish(self, led: Ledger, post_id: str) -> Ledger:
        """Poster protocol — signature UNCHANGED and shared with postiz/dryrun (invariant I-11). The SOLE
        site mapping a Zernio create result onto the ledger, so each rule ("a 409 is never failed", "a
        candidate is never a submission_id") has exactly one owner to review."""
        post = led.posts[post_id]
        result = self._create(post)                      # ZernioAuthError propagates: halts the run (run.py H8)
        if isinstance(result, (Created, IdempotentReplay)):
            # Both are the SAME logical submission, so both take the SAME ledger state: `submitted` + a real
            # id. Zernio returns no permalink at create, so run.py's public_url gate parks both in
            # needs_reconcile and reconcile back-fills the URL — identical to the pre-fix success path.
            post.state = PostState.submitted
            post.submission_id = result.post_id
            post.public_url = safe_public_url(None) or post.public_url   # permalink captured later by ZernioStatusClient (reconcile); none on the publish 2xx — placeholder mirrors postiz.py
            if isinstance(result, IdempotentReplay):
                # The ONLY behavioral difference from Created: an audit trail. A replay means a send DID
                # land and we recovered it instead of creating a second post — the whole point of the
                # header, so it must be visible. Unguarded, exactly like every sibling log on this path.
                get_logger(self.cfg)("publish", post.id, "idempotent_replay", sub=result.post_id, request_id=_request_id(post))
        elif isinstance(result, ReconciliationRequired):
            post.state = PostState.needs_reconcile
            post.reconcile_candidate_id = result.candidate_post_id   # NEVER submission_id (report 11 §5)
            # Mirror the candidate into error_reason too: models.py is extra="ignore" (deliberate, pinned
            # forward-compat), so an OLDER binary loading this ledger drops the new key entirely — the
            # mirror is then the only surviving copy.
            cand = f" candidate={result.candidate_post_id}" if result.candidate_post_id else ""
            post.error_reason = f"zernio {result.reason}:{cand} {result.evidence}"[:400]
        else:                                            # TerminalFailure — the ONLY re-queueable verdict
            post.state = PostState.failed
            post.error_reason = f"zernio {result.reason}: {result.evidence}"[:400]
        return led
