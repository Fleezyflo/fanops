# src/fanops/meta_graph.py
"""A thin, READ-ONLY Meta Graph client. Used by `hashtags refresh` (the hashtag half) and by the
metrics/insights paths; never on the publish path. Design rules:

  Hashtag LOOKUPS never swallow Meta's answer. A non-throttle error raises `GraphRefused` carrying
  Meta's own code/subcode/type/message; a transport failure raises `GraphUnreachable`. `resolve_hashtag`
  returns None ONLY when Meta answered HTTP 200 with an empty match list — that is the unambiguous
  "no such hashtag". The token is sent as the Graph `access_token` param and is NEVER logged/echoed
  (METRICS_CLIENT_AUTH_DISCIPLINE — mirrors post/metrics.py).

  META IS THE ONLY GOVERNOR of how much a hashtag pass gets through. There is no local budget /
  allowance model (a previous hard-capped local meter was deleted for cause — it starved the store
  while Meta was still serving searches). Throttle codes are absorbed with a jittered backoff and, if
  they persist, end the pass with whatever evidence accrued; non-throttle Meta errors raise
  `GraphRefused` so callers see Meta's own code/subcode/message. Nothing here predicts or meters an
  allowance.

  The hashtag REACH datum is Meta's own `like_count`, taken verbatim off one `top_media` item. Probed
  live 2026-07-26: the IG Hashtag node serves only `id` and `name` — `media_count` answers
  "(#100) Tried accessing nonexisting field" — so post volume is genuinely unavailable, and the earlier
  likes+comments SUM was a number we invented rather than one the platform published."""
from __future__ import annotations
import json
import random
import re
import socket
import time
from datetime import datetime, timezone
from typing import NamedTuple, Optional
import requests
from requests.adapters import HTTPAdapter
from fanops.config import Config
from fanops.errors import MetaInsightsScopeError
from fanops.log import get_logger
from fanops.hashtags import METRIC_FIELD, _norm


# Force AF_INET for Graph HTTP only — macOS often blackholes AAAA for graph.facebook.com (~20s then
# IPv4 fallback). Scoped to this Session/adapter; do NOT patch process-wide (daemon also talks Postiz).
class _IPv4HTTPAdapter(HTTPAdapter):
    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        import urllib3.util.connection as uc
        orig = uc.allowed_gai_family
        try:
            uc.allowed_gai_family = lambda: socket.AF_INET
            return super().send(request, stream=stream, timeout=timeout, verify=verify, cert=cert, proxies=proxies)
        finally:
            uc.allowed_gai_family = orig

_IPV4_SESSION: Optional[requests.Session] = None

def _ipv4_session() -> requests.Session:
    global _IPV4_SESSION
    if _IPV4_SESSION is None:
        s = requests.Session(); a = _IPv4HTTPAdapter()
        s.mount("https://", a); s.mount("http://", a); _IPV4_SESSION = s
    return _IPV4_SESSION

def _default_get(url, **kw):
    """Default Graph transport: Session whose adapter resolves AF_INET only (IPv6 blackhole tax)."""
    return _ipv4_session().get(url, **kw)


# ---- Per-account Meta credential resolution (the audit's per-handle-creds gap) --------------------------
# META_IG_USER_ID + META_GRAPH_TOKEN were a SINGLE GLOBAL credential, so every Graph read (list_user_media /
# insights / hashtag reads) enumerated ONE handle regardless of which account a post belonged to. A handle
# can now carry its OWN ig_user_id (accounts.json, non-secret) + its OWN access token (a per-handle .env key
# META_GRAPH_TOKEN__<SLUG>, a SECRET, never logged/echoed — mirrors the global META_GRAPH_TOKEN discipline).
# resolve_meta_creds is THE single source of truth: given a handle, resolve ITS creds, falling back per-field
# to the global env creds so a single-account setup (no per-account config) stays BYTE-IDENTICAL to today.
class MetaCreds(NamedTuple):
    ig_user_id: Optional[str]       # the IG Business user id (per-account ig_user_id, else global META_IG_USER_ID)
    token: Optional[str]            # the Graph access token (per-handle .env key, else global META_GRAPH_TOKEN) — SECRET

def _env_slug(handle: str) -> str:
    """A handle -> the UPPERCASE alphanumeric suffix of its per-handle token env key
    (META_GRAPH_TOKEN__<SLUG>), so '@markmakmouly' -> 'MARKMAKMOULY'. Strips '@'/punctuation/emoji (an env
    var name must be [A-Z0-9_]); a handle that normalizes to empty yields '' (no per-handle key -> global)."""
    return re.sub(r"[^A-Z0-9]", "", (handle or "").upper())

def per_account_token_env_key(handle: str) -> Optional[str]:
    """The .env key holding THIS handle's Graph access token, or None when the handle has no env-safe slug
    (falls back to the global token). The dual-write surface and the resolver agree on this ONE derivation."""
    slug = _env_slug(handle)
    return f"META_GRAPH_TOKEN__{slug}" if slug else None

def resolve_meta_creds(cfg: Config, *, handle: Optional[str] = None) -> MetaCreds:
    """Resolve the Meta creds for `handle`: its per-account ig_user_id (accounts.json) + its per-handle token
    (.env META_GRAPH_TOKEN__<SLUG>), each falling back to the GLOBAL env cred (cfg.meta_ig_user_id /
    cfg.meta_graph_token) when unset. `handle=None` (a niche-wide call with no account in context — hashtag
    discovery) returns the global creds exactly as today. NEVER raises: a corrupt accounts.json degrades to
    the global creds (mirrors load_accounts_safe), so a read path can't be crashed by config. The token is a
    SECRET — this returns it for use as the access_token param; the caller must never log/echo it."""
    ig = cfg.meta_ig_user_id                                     # global fallback (per-field)
    tok = cfg.meta_token_for(handle)
    if handle:
        from fanops.accounts import load_accounts_safe          # lazy: accounts imports config, not meta_graph
        accts, _err = load_accounts_safe(cfg)                    # never raises -> global fallback on a torn file
        acc = next((a for a in accts.accounts if a.handle == handle), None)
        if acc is not None and (acc.ig_user_id or "").strip():
            ig = acc.ig_user_id.strip()                          # per-account IG Business id wins
    return MetaCreds(ig_user_id=ig, token=tok)


def resolvable_meta_tokens(cfg: Config) -> list[tuple[str, str]]:
    """T9: every DISTINCT Meta Graph access token the deployment can resolve, as (label, token) — the GLOBAL
    META_GRAPH_TOKEN (label 'global') plus each active IG-carrying account's per-handle META_GRAPH_TOKEN__<SLUG>
    (label the handle). Deduped by token VALUE so a handle inheriting the global (no per-handle key) is not
    introspected twice. NEVER raises: a torn accounts.json degrades to just the global (mirrors
    load_accounts_safe). The token is a SECRET — callers pass it straight to debug_token, never log it; this
    returns it only so the expiry preflight can introspect each distinct credential exactly once."""
    from fanops.accounts import load_accounts_safe
    from fanops.models import Platform
    out: list[tuple[str, str]] = []; seen: set[str] = set()
    g = cfg.meta_graph_token
    if g and g not in seen: seen.add(g); out.append(("global", g))
    accts, _err = load_accounts_safe(cfg)
    for a in accts.active():
        if Platform.instagram not in a.platforms: continue
        v = cfg._per_handle_meta_token(a.handle)
        if v and v not in seen: seen.add(v); out.append((a.handle, v))
    return out


def debug_token_expiry(cfg: Config, token: str, *, get=None) -> tuple[str, object]:
    """T9: introspect ONE Meta access token via the Graph debug_token endpoint and return (status, detail):
      ("ok",       expires_at_epoch_int)   - a valid future expiry (or 0 == a never-expiring long-lived token)
      ("expired",  expires_at_epoch_int)   - expires_at is in the PAST
      ("unknown",  reason_str)             - introspection FAILED (no creds/transport/non-200/bad shape/invalid)
    FAIL-CLOSED by construction: any failure returns 'unknown' (the caller treats it as a check FAILURE, never a
    silent pass). The token rides the debug_token params (input_token + access_token) exactly like every other
    Graph read here; it is NEVER placed in a logged/returned string (only the epoch or a generic reason).
    debug_token is Meta's own token-introspection edge; expires_at=0 is Meta's sentinel for 'does not expire'."""
    get = get or _default_get
    if not token:
        return ("unknown", "no token")
    try:
        resp = get(f"{cfg.meta_graph_url}/debug_token",
                   params={"input_token": token, "access_token": token}, timeout=20)
    except requests.exceptions.RequestException:
        return ("unknown", "transport error")
    if getattr(resp, "status_code", None) != 200:
        return ("unknown", f"debug_token HTTP {getattr(resp, 'status_code', '?')}")
    try:
        data = resp.json().get("data")
    except (ValueError, AttributeError):
        return ("unknown", "non-JSON debug_token response")
    if not isinstance(data, dict):
        return ("unknown", "debug_token response missing data")
    if data.get("is_valid") is False:
        return ("expired", data.get("expires_at") if isinstance(data.get("expires_at"), (int, float)) else 0)
    exp = data.get("expires_at")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        return ("unknown", "debug_token response missing expires_at")
    exp = int(exp)
    if exp == 0:
        return ("ok", 0)                                 # Meta sentinel: a long-lived token that does not expire
    now = int(_now().timestamp())
    return (("expired", exp) if exp <= now else ("ok", exp))


_TAG_RE = re.compile(r"#[0-9A-Za-z_؀-ۿ]+")   # a hashtag in a caption: Latin + Arabic-block letters
_HARVEST_CAP = 5000                 # upper bound on distinct co-tags per harvest — a guard against a pathological/mocked top_media response (untrusted UGC); unreachable under Meta's own caption+page limits

def _now() -> datetime:
    return datetime.now(timezone.utc)

def account_overview(cfg: Config, handle: str, *, get=None) -> Optional[dict]:
    """Per-handle IG follower snapshot via Graph GET /{ig_user_id}?fields=followers_count. Returns
    {"followers": int, "fetched_at": iso} or None when creds/token/ig_user_id are absent or the call fails.
    FAIL-OPEN: logs once on error, never echoes the token."""
    creds = resolve_meta_creds(cfg, handle=handle)
    if not (creds.ig_user_id and creds.token):
        return None
    body = _graph_get(cfg, creds.ig_user_id, {"fields": "followers_count"}, get=get, token=creds.token)
    if not body:
        get_logger(cfg)("account_stats", handle, "overview_fail", err="graph_none")
        return None
    fc = body.get("followers_count")
    if not isinstance(fc, (int, float)) or isinstance(fc, bool):
        get_logger(cfg)("account_stats", handle, "overview_fail", err="bad_shape")
        return None
    return {"followers": int(fc), "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}


def _graph_get(cfg: Config, path: str, params: dict, *, get=None, token: Optional[str] = None):
    """Read-only Graph GET -> parsed JSON dict, or None on ANY failure (fail-soft enhancement). The
    token rides in the `access_token` param; it is never placed in a logged string. `token` overrides the
    global cfg.meta_graph_token (per-account creds threading); None keeps the global (byte-identical).
    Default transport is IPv4-only (_default_get) — macOS IPv6 blackhole to graph.facebook.com."""
    get = get or _default_get
    try:
        resp = get(f"{cfg.meta_graph_url}/{path}",
                   params={**params, "access_token": token if token is not None else cfg.meta_graph_token}, timeout=20)
    except requests.exceptions.RequestException:
        return None
    if getattr(resp, "status_code", None) != 200:
        return None
    try:
        body = resp.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None

class GraphThrottled(Exception):
    """Meta answered with a THROTTLE code and kept answering with one after the backoff ladder. The
    caller ends its pass and keeps the evidence it already accrued. This exception is the ONLY thing
    that bounds a pass — there is no local allowance model to consult."""


class GraphRefused(Exception):
    """Meta answered with a non-throttle error object. Carries Meta's own fields so a caller can tell
    'refused' from 'no such hashtag' — never collapse either into a bare None."""
    def __init__(self, path: str, *, code=None, subcode=None, type=None, message: str = ""):
        self.path = path
        self.code = code if isinstance(code, int) and not isinstance(code, bool) else None
        self.subcode = subcode if isinstance(subcode, int) and not isinstance(subcode, bool) else None
        self.type = type if isinstance(type, str) else None
        self.message = (message or "")[:160]                  # never carry a long body / token echo
        super().__init__(f"Meta refused {path}: code={self.code} subcode={self.subcode} "
                         f"type={self.type} msg={self.message}")


class GraphUnreachable(Exception):
    """The request never reached Meta (transport / non-JSON). Carries a short reason; never the token."""
    def __init__(self, path: str, *, reason: str = ""):
        self.path = path
        self.reason = (reason or "")[:160]
        super().__init__(f"Meta unreachable {path}: {self.reason}")


_THROTTLE_CODES = frozenset({4, 17, 32, 613})   # Meta's own rate-limit codes: app / user / page / custom
_MAX_RL_RETRIES = 3                             # total attempts = retries + 1 (mirrors llm.py's ladder)
_RL_BASE_DELAY = 2.0                            # seconds; doubled per attempt + jittered
_sleep = time.sleep                             # indirection so tests can stub the backoff wait


def _error_code(body) -> Optional[int]:
    """Meta's numeric error code from a response body, or None when the body carries no error object."""
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    code = err.get("code") if isinstance(err, dict) else None
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def _refused_from_body(path: str, body) -> GraphRefused:
    """Build GraphRefused from Meta's error object (or an empty shell when the body carried none)."""
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return GraphRefused(path)
    msg = err.get("message")
    typ = err.get("type")
    return GraphRefused(path, code=err.get("code"), subcode=err.get("error_subcode"),
                        type=typ if isinstance(typ, str) else None,
                        message=msg if isinstance(msg, str) else "")


def _hashtag_get(cfg: Config, path: str, params: dict, *, get=None):
    """One hashtag-endpoint GET, with Meta's own refusals as the only control loop.

    Returns the parsed body on HTTP 200. Raises GraphThrottled when a throttle code survives the
    jittered-backoff ladder (end the pass). Raises GraphRefused for any other Meta error object — the
    caller MUST see Meta's code/subcode/message. Raises GraphUnreachable on transport / non-JSON.
    The token rides the `access_token` param and never enters a logged or exception string."""
    get = get or _default_get
    delay = _RL_BASE_DELAY
    for attempt in range(_MAX_RL_RETRIES + 1):
        try:
            resp = get(f"{cfg.meta_graph_url}/{path}",
                       params={**params, "access_token": cfg.meta_graph_token}, timeout=20)
        except requests.exceptions.RequestException as exc:
            raise GraphUnreachable(path, reason=f"{type(exc).__name__}: {exc}") from exc
        if getattr(resp, "status_code", None) == 200:
            try:
                body = resp.json()
            except ValueError as exc:
                raise GraphUnreachable(path, reason=f"non_json: {exc}") from exc
            if not isinstance(body, dict):
                raise GraphUnreachable(path, reason="non_object_body")
            return body
        try:
            body = resp.json()
        except (ValueError, AttributeError):
            body = None
        if _error_code(body) not in _THROTTLE_CODES:
            raise _refused_from_body(path, body)             # Meta's own words — never swallow to None
        if attempt < _MAX_RL_RETRIES:
            _sleep(delay + random.uniform(0, delay))     # jitter so retries don't land in lockstep
            delay *= 2
    raise GraphThrottled(f"Meta throttled {path} after {_MAX_RL_RETRIES} retries")


def resolve_hashtag(cfg: Config, tag: str, *, get=None) -> Optional[str]:
    """Resolve '#tag' to its Graph hashtag-node id via ig_hashtag_search (`q` carries no leading '#').

    Returns None ONLY when Meta answered and there is no such hashtag (HTTP 200, empty `data`).
    Raises GraphRefused / GraphUnreachable / GraphThrottled for every other failure — a bare None must
    never mean "Meta errored" or "the socket dropped".

    Node ids are STABLE and global, which is why callers cache them: a tag we have already resolved never
    spends another search, so the search endpoint funds novel discovery only.

    Meta permissions (docs/instagram-platform/.../hashtag-search): instagram_basic is REQUIRED but NOT
    sufficient on its own — the separate 'Instagram Public Content Access' FEATURE (its OWN App Review
    submission, distinct from the permission) is ALSO mandatory. An operator granting only instagram_basic
    hits an opaque rejection; the missing piece is that App Review feature, not another scope."""
    if not (cfg.meta_graph_token and cfg.meta_ig_user_id):
        return None
    q = _norm(tag).lstrip("#")
    if not q:
        return None
    body = _hashtag_get(cfg, "ig_hashtag_search", {"user_id": cfg.meta_ig_user_id, "q": q}, get=get)
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    hid = data[0].get("id")
    return hid if isinstance(hid, str) and hid else None


def measure_and_harvest(cfg: Config, hid: str, *, get=None) -> tuple[Optional[float], dict[str, int]]:
    """ONE `top_media` fetch serving BOTH jobs — the measurement and the discovery harvest.

    metric  = the FIRST item in Meta's own top_media ordering that carries a `like_count`, verbatim. Meta
              ranks the media and Meta supplies the number; this selects, it never computes. An item with
              likes hidden is SKIPPED rather than read as zero (probed live: real top media do hide them).
              No item carries one -> None -> the tag is UNMEASURED, and unmeasured is inadmissible.
    harvest = every hashtag those same captions carry (`_TAG_RE`), tallied. Co-occurrence is the only
              Graph-native way to DISCOVER tags nobody has named — IG has no trending-by-topic endpoint —
              and it is also where versatility comes from: the posts winning in a niche right now carry
              both the niche tags and the broad ones.

    Raises GraphThrottled / GraphRefused / GraphUnreachable. A 200 with empty `data` is unmeasured
    `(None, {})` — that is Meta answering, not Meta failing."""
    body = _hashtag_get(cfg, f"{hid}/top_media",
                        {"user_id": cfg.meta_ig_user_id, "fields": f"caption,{METRIC_FIELD}"}, get=get)
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return None, {}
    metric: Optional[float] = None
    cotags: dict[str, int] = {}
    for m in data:
        if not isinstance(m, dict):
            continue
        v = m.get(METRIC_FIELD)
        if metric is None and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
            metric = float(v)
        for raw in _TAG_RE.findall(m.get("caption") or ""):
            t = _norm(raw)
            if not t:
                continue
            if t not in cotags and len(cotags) >= _HARVEST_CAP:
                continue                                  # cap DISTINCT co-tags (untrusted-UGC guard)
            cotags[t] = cotags.get(t, 0) + 1
    return metric, cotags

_MEDIA_FIELDS = "id,permalink,media_product_type,timestamp,caption"   # caption added (ledger-rebuild): the inverse projection mirrors a live-only media's caption (display-only); resolve ignores the extra field
_MEDIA_PAGE_CAP = 50            # defensive: >50 pages of the IG user's OWN media is a pathological/mocked paging loop

def list_user_media(cfg: Config, *, get=None, creds: Optional[MetaCreds] = None):
    """Leg 2 identify-half: the live list of THIS IG user's media (id + permalink + product_type + timestamp),
    walking `paging.next` to completion. READ-ONLY, spends NO hashtag budget (a separate high-limit endpoint).
    FAIL-OPEN -> [] on any transport/shape failure or absent creds (mirrors trend_score) so an insights pull
    that can't enumerate media simply resolves no new media_ids rather than crashing the daemon tick.
    `creds` scopes the read to a specific handle's ig_user_id + token (per-account creds threading); None
    resolves the GLOBAL creds (byte-identical to a single-account setup).
    Meta permissions for the /media edge (docs/instagram-platform/.../instagram-media) — TWO valid auth paths:
    EITHER instagram_business_basic ALONE (the newer Instagram Login flow), OR instagram_basic +
    pages_read_engagement (the Facebook Login flow), plus ads_management or ads_read ONLY when the token's
    Page role came from Business Manager. instagram_manage_insights is a DIFFERENT permission (it governs the
    separate insights edge — see media_insights) and is NOT required here."""
    creds = creds or resolve_meta_creds(cfg)
    if not (creds.token and creds.ig_user_id):
        return []                                                # no creds -> nothing to enumerate (fail-open)
    out: list[dict] = []
    params = {"fields": _MEDIA_FIELDS, "limit": 100}
    path = f"{creds.ig_user_id}/media"
    for _ in range(_MEDIA_PAGE_CAP):
        body = _graph_get(cfg, path, params, get=get, token=creds.token)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            break                                                # transport/shape failure -> stop, return what we have ([] first pass)
        out.extend(m for m in data if isinstance(m, dict) and m.get("id"))
        nxt = (body.get("paging") or {}).get("next") if isinstance(body.get("paging"), dict) else None
        if not nxt:
            break
        # `next` is a fully-formed absolute URL (host + querystring); pass it as the path with empty params
        # so _graph_get GETs it verbatim (+ the access_token). Strip the base so we don't double it.
        path, params = _next_path(cfg, nxt), {}
    return out

def _next_path(cfg: Config, next_url: str) -> str:
    """The Graph `paging.next` is an absolute URL; _graph_get prepends `{meta_graph_url}/`. Strip that base
    (and any leading slash) so the verbatim cursor URL is GET as-is, not concatenated onto the base twice."""
    base = cfg.meta_graph_url.rstrip("/") + "/"
    return next_url[len(base):] if next_url.startswith(base) else next_url.lstrip("/")


_IG_OBJECT_FIELDS = "id,permalink,media_type,timestamp,username"   # MOL-113: the per-object liveness projection

def resolve_ig_media(cfg: Config, media_id, *, handle: Optional[str] = None, get=None) -> Optional[dict]:
    """MOL-113 — ask the Graph about ONE specific IG object: GET /{media_id}?fields=id,permalink,media_type,
    timestamp,username with the resolved per-account creds. THE direct liveness SOURCE — no feed enumeration,
    no permalink string-match (which is capped at one global credential and breaks on a re-share). Returns
    {"exists": True, "permalink":…, "media_type":…, "username":…} on a 200 that carries the object id back
    (proof it resolved), else None. FAIL-CLOSED at every step (empty/None id, no creds, non-200, transport
    error, a 200 whose body lacks the id) -> None, NEVER a fabricated exists:True. Mirrors _graph_get's
    fail-soft + resolve_meta_creds threading exactly (the token rides access_token, never logged/echoed).
    `handle` scopes the read to that account's ig_user_id+token (the object belongs to one account); None
    resolves the GLOBAL creds. This is the confirming primitive MOL-117's gate consumes."""
    mid = (str(media_id).strip() if media_id is not None else "")
    if not mid:
        return None                                              # no id -> nothing to resolve (no HTTP)
    creds = resolve_meta_creds(cfg, handle=handle)
    if not creds.token:
        return None                                              # no token -> can't ask the platform (fail-closed, no HTTP)
    body = _graph_get(cfg, mid, {"fields": _IG_OBJECT_FIELDS}, get=get, token=creds.token)
    if not isinstance(body, dict) or not body.get("id"):
        return None                                              # non-200 / transport / error-shaped 200 -> unconfirmed
    return {"exists": True, "permalink": body.get("permalink"),
            "media_type": body.get("media_type"), "username": body.get("username")}


def confirm_post_live(cfg: Config, post, *, reported_username: Optional[str] = None, get=None) -> dict:
    """MOL-113 — the ONE seam that confirms a post against ITS platform's own API and returns
    {"confirmed": bool, "owner": Optional[str]}. Routes by platform, NEVER guesses:
      IG     -> resolve_ig_media on the captured media_id (Post.media_id, stamped from the Postiz releaseId at
                reconcile — the stable per-object input). Confirmed iff the object resolves; owner is the
                Graph-reported username. No media_id / a removed object -> {confirmed:False, owner:None}.
      TikTok -> the EXISTING oEmbed verifier (post.metrics.verify_tiktok_permalink) — unchanged, just routed
                here. Confirmed iff the live oEmbed author == `reported_username` (the Zernio-reported username
                the post published to); owner is that username on a pass.
    FAIL-CLOSED: any surface with no confirmable signal returns {confirmed:False, owner:None}. Read-only —
    no ledger write, no publish. The injectable `get` keeps every call mockable (no live network in tests)."""
    from fanops.models import Platform
    if post.platform is Platform.instagram:
        res = resolve_ig_media(cfg, post.media_id, handle=post.account, get=get)
        if res:
            return {"confirmed": True, "owner": res.get("username")}
        return {"confirmed": False, "owner": None}
    if post.platform is Platform.tiktok:
        from fanops.post.metrics import verify_tiktok_permalink
        ok = bool(verify_tiktok_permalink(cfg, post.public_url, reported_username, get=get))
        return {"confirmed": ok, "owner": (reported_username if ok else None)}
    return {"confirmed": False, "owner": None}                   # no per-object liveness notion on other surfaces


def credentialed_ig_handles(cfg: Config) -> list[str]:
    """The active IG-carrying account handles that have their OWN per-account ig_user_id configured — the
    set of handles reconcile must enumerate media for (the per-handle-creds gap: live-linking capped at the
    single global handle). EMPTY when no account is per-account-credentialed -> the caller falls back to the
    single global enumeration (byte-identical to before). NEVER raises: a torn accounts.json degrades to []
    (mirrors load_accounts_safe), so a read path is never crashed by config."""
    from fanops.accounts import load_accounts_safe
    from fanops.models import Platform
    accts, _err = load_accounts_safe(cfg)
    return [a.handle for a in accts.active()
            if Platform.instagram in a.platforms and (a.ig_user_id or "").strip()]


def enumerate_scoped_media(cfg: Config, handles, *, get=None) -> list[tuple]:
    """Enumerate each handle's live IG media with THAT handle's resolved creds, returning a flat
    [(handle, media_dict), ...] across all handles. `handles` is the list of handles to enumerate; pass
    [None] for the single GLOBAL enumeration (byte-identical to a bare list_user_media). FAIL-OPEN per
    handle (list_user_media returns [] on a per-handle creds/transport failure) so one dark handle never
    blocks the others. A handle with no resolvable creds simply contributes nothing."""
    out: list[tuple] = []
    for h in (handles or [None]):
        creds = resolve_meta_creds(cfg, handle=h)
        for m in list_user_media(cfg, get=get, creds=creds):
            out.append((h, m))
    return out

# Leg 2 (Insight): the SINGLE Meta-derived source of which insights metric is valid for which media type.
# Transcribed ONCE from Meta's official ig-media/insights reference (each metric -> the product types Meta
# declares it valid on; `media_product_type` is one of AD|FEED|STORY|REELS). This REPLACES the old
# hand-curated per-type lists — a human-synced list is how `plays` (deprecated 2025-04-21) rotted in and how
# a feed video got asked for a reels-only metric. A metric invalid for a type simply is NOT in the derived
# set, so it is UNCONSTRUCTABLE in the request; deprecated names are absent by design, never requestable.
# Scoped to the metrics FanOps consumes.
# Coverage note (docs/instagram-platform/.../instagram-media/insights): two real, NON-deprecated metrics
# are valid but NOT collected here — total_interactions (a FEED/REELS/STORY aggregate of likes+saves+
# comments+shares, and the ONLY aggregate engagement metric that works on STORY, where the individual
# ones don't apply) and ig_reels_video_view_total_time (REELS-only, total watch incl. replays, a
# complement to ig_reels_avg_watch_time). Wiring them into this table + the track.py/digest.py consumers
# is a separate product decision (its own ticket) — noted here for awareness, deliberately NOT added.
_MEDIA_METRICS: dict[str, frozenset[str]] = {
    "reach": frozenset({"FEED", "REELS", "STORY"}),
    "views": frozenset({"FEED", "REELS", "STORY"}),
    "likes": frozenset({"FEED", "REELS"}),
    "comments": frozenset({"FEED", "REELS"}),
    "saved": frozenset({"FEED", "REELS"}),
    "shares": frozenset({"FEED", "REELS", "STORY"}),
    "ig_reels_avg_watch_time": frozenset({"REELS"}),             # REELS-only (asking it on FEED 400s)
}

def insights_metrics_for(product_type: str | None) -> list[str]:
    """The metrics Meta declares valid for this media's `product_type`, derived from `_MEDIA_METRICS` — the
    SOLE builder of the insights request `metric=` list. An unknown/None type intersects nothing -> [], so
    the caller must resolve the real type first (the client skips an unresolved one, never guesses). Order
    follows the table for a stable request string."""
    pt = (product_type or "").upper()
    return [m for m, types in _MEDIA_METRICS.items() if pt in types]

# Graph metric name -> our lift/row key. `saved` is our `saves`; ig_reels_avg_watch_time lands as raw
# `avg_watch_ms` (retention as a [0,1] fraction is derived downstream in GraphInsightsClient from the clip
# duration — kept out of here so this stays duration-free). Deprecated names (plays/impressions) are NOT
# mapped: once the request stops sending them (see _MEDIA_METRICS), Meta never returns them.
_GRAPH_INSIGHTS_MAP = {
    "reach": "reach", "views": "views",
    "saved": "saves", "saves": "saves", "shares": "shares",
    "likes": "likes", "like_count": "likes", "comments": "comments", "comments_count": "comments",
    "ig_reels_avg_watch_time": "avg_watch_ms",
}

def _is_scope_error(body) -> bool:
    """True iff a Graph error body is a PERMISSION/scope refusal (missing instagram_manage_insights) vs a
    transient/data failure. Meta signals a genuine scope refusal as an `OAuthException` (permission codes
    10 / 200 / 803). It is DELIBERATELY NOT keyed on the word 'permission' in the message alone: Meta's
    `GraphMethodException` for a bad object id (code 100 / error_subcode 33) reads "...cannot be loaded due
    to missing permissions, or does not support this operation..." — a DATA error (wrong/non-IG id), not a
    scope refusal. Substring-matching 'permission' there is what false-blocked IG insights when a Postiz id
    reached the insights edge (see tests/test_meta_graph_contract.py + its recorded cassette). Contract is
    pinned to the REAL Graph error shapes, not a guess: a scope refusal is an OAuthException; a code-100 /
    GraphMethodException is transient (None), so the row re-resolves its real IG media id next pass."""
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return False
    # A GraphMethodException (code 100) is a bad-request/does-not-exist DATA error — never a scope refusal,
    # even though its message contains the word "permissions".
    if err.get("type") == "GraphMethodException" or err.get("code") == 100:
        return False
    # A genuine scope refusal: OAuthException with a Meta permission code.
    if err.get("type") == "OAuthException" and err.get("code") in (10, 200, 803):
        return True
    # Fallback: an explicit permission-worded OAuth-typed error (message text alone is insufficient).
    return err.get("type") == "OAuthException" and "permission" in str(err.get("message", "")).lower()

def media_insights(cfg: Config, media_id: str, product_type: str | None, *, get=None, creds: Optional[MetaCreds] = None):
    """Leg 2 read-half: THE complete performance of one live IG media from Graph media-insights — the SOLE
    IG analytics source (no Postiz fallback). The requested metric list is DERIVED from `product_type` via
    `insights_metrics_for` (the one Meta table) — reels get avg-watch, feed cannot (Meta: REELS-only), and a
    deprecated metric is unrequestable. Returns a normalized dict {reach,views,saves,shares,likes,comments
    [,avg_watch_ms]} on success; None on a TRANSIENT failure (5xx / network / no creds / an UNRESOLVED
    product_type — re-poll/re-resolve next pass); raises MetaInsightsScopeError on a PERMISSION refusal
    (LOUD, fail-closed — the one external gate). The token rides the access_token param, never a logged
    string. `creds` scopes the read to a handle's token (per-account creds threading); None resolves the
    GLOBAL token (byte-identical). media_id is per-media so only the token varies by account here."""
    creds = creds or resolve_meta_creds(cfg)
    if not (creds.token and creds.ig_user_id):
        return None                                              # no creds -> transient-shaped (keep prior snapshot)
    metrics = insights_metrics_for(product_type)                 # SOLE source: Meta's per-type valid set
    if not metrics:                                              # unresolved/unknown product_type -> empty set:
        # honor the docstring above — SKIP an unresolved one, never build a request with an empty `metric=`.
        # Meta 400s an empty metric list as an OAuthException, which the scope classifier would misread as a
        # permission refusal and false-block. Refuse PRE-FLIGHT (no HTTP): transient-shaped so the row re-
        # resolves its product_type next reconcile pass, then lands real metrics. NO request is ever built.
        get_logger(cfg)("graph_insights", str(media_id), "unresolved_type_skip", product_type=str(product_type))
        return None
    get = get or _default_get
    try:
        resp = get(f"{cfg.meta_graph_url}/{media_id}/insights",
                   params={"metric": ",".join(metrics), "access_token": creds.token}, timeout=20)
    except requests.exceptions.RequestException:
        return None                                              # transport blip -> transient
    if getattr(resp, "status_code", None) != 200:
        try: body = resp.json()
        except (ValueError, AttributeError): body = None
        if _is_scope_error(body):
            raise MetaInsightsScopeError(                        # LOUD: the insights scope is missing (body WITHHELD)
                "Meta Graph media-insights refused: grant the instagram_manage_insights token scope")
        return None                                              # non-permission non-200 -> transient
    try:
        data = resp.json().get("data")
    except (ValueError, AttributeError):
        return None
    if not isinstance(data, list):
        return None
    out: dict = {}
    for item in data:
        if not isinstance(item, dict): continue
        key = _GRAPH_INSIGHTS_MAP.get(item.get("name"))
        if key is None: continue                                 # unknown metric name -> dropped (mirrors _map_analytics)
        vals = item.get("values")
        v = vals[0].get("value") if isinstance(vals, list) and vals and isinstance(vals[0], dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[key] = v
    return out

# The one-external-gate breadcrumb (Leg 2): a scope refusal during a pull persists here so a SEPARATE
# doctor/Home read surfaces it (the block happens on a daemon tick; the operator looks later). Written
# LOUD, cleared automatically the next time insights flow — a self-healing signal, no manual reset.
def insights_blocked_signal(cfg: Config) -> bool:
    """True iff the persisted insights-scope-blocked breadcrumb is present + set. Fail-open: any read error
    -> False, but LOGGED (a torn/absent file must not itself raise a false alarm, yet a real read failure is
    still surfaced on the log stream, never silently swallowed)."""
    p = cfg.insights_blocked_path
    if not p.exists():
        return False
    try:
        d = json.loads(p.read_text())
        return bool(d.get("blocked")) if isinstance(d, dict) else False
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
        get_logger(cfg)("graph_insights", "signal", "read_failed", err=str(e)[:120]); return False

def _set_insights_blocked(cfg: Config) -> None:
    """Persist the LOUD scope-blocked breadcrumb (idempotent). A write error is LOGGED (not silent): the
    in-pass insights_blocked flag + the scope log line already fired, so a missing breadcrumb degrades the
    doctor/Home surfacing only, and the failure is visible on the log stream."""
    try:
        cfg.insights_blocked_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.insights_blocked_path.write_text(json.dumps({"blocked": True}))
    except OSError as e:
        get_logger(cfg)("graph_insights", "signal", "write_failed", err=str(e)[:120])

def _clear_insights_blocked(cfg: Config) -> None:
    """Clear the breadcrumb once insights flow again (scope granted) — self-healing + idempotent (absent file
    is already 'clear'). A clear failure is LOGGED, never silently swallowed."""
    try:
        cfg.insights_blocked_path.unlink(missing_ok=True)
    except OSError as e:
        get_logger(cfg)("graph_insights", "signal", "clear_failed", err=str(e)[:120])
