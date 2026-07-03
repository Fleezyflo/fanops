# src/fanops/meta_graph.py
"""M4 live half — a thin, budget-aware, READ-ONLY Meta Graph client that samples hashtag TREND signal
(finding #7: hashtags update on what is trending in our niche). Used ONLY by `hashtags refresh`, never
on the publish path. Two design rules, both load-bearing:

  1. ENHANCEMENT -> the TRANSPORT fails SOFT. Any per-tag fetch failure (no creds, 401, 5xx, timeout,
     non-JSON, unresolved hashtag) returns None and is skipped — a missing trend never blocks a refresh
     (the frozen reach floor still stands). The token is sent as the Graph `access_token` param and is NEVER
     logged/echoed (METRICS_CLIENT_AUTH_DISCIPLINE — mirrors post/metrics.py).

  2. The 30-unique-hashtags / rolling-7-day `ig_hashtag_search` cap is a HARD Meta limit, so the BUDGET
     fails CLOSED + LOUD: if the persisted counter (00_control/hashtag_budget.json) is unreadable/corrupt,
     budget_remaining returns None and sample_trends queries NOTHING (better a stale store than a banned
     app). Meta deprecated hashtag media_count, so the trend signal is engagement summed over top_media."""
from __future__ import annotations
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional
import requests
from fanops.config import Config
from fanops.errors import MetaInsightsScopeError
from fanops.log import get_logger
from fanops.hashtags import _norm


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
    tok = cfg.meta_graph_token
    if handle:
        from fanops.accounts import load_accounts_safe          # lazy: accounts imports config, not meta_graph
        accts, _err = load_accounts_safe(cfg)                    # never raises -> global fallback on a torn file
        acc = next((a for a in accts.accounts if a.handle == handle), None)
        if acc is not None and (acc.ig_user_id or "").strip():
            ig = acc.ig_user_id.strip()                          # per-account IG Business id wins
        key = per_account_token_env_key(handle)
        if key:
            v = os.getenv(key)
            if v and v.strip():
                tok = v.strip()                                  # per-handle access token wins
    return MetaCreds(ig_user_id=ig, token=tok)

_BUDGET_LIMIT = 30                  # Meta: 30 UNIQUE hashtags per IG user per rolling 7 days
_BUDGET_WINDOW_DAYS = 7
_TAG_RE = re.compile(r"#[0-9A-Za-z_؀-ۿ]+")   # a hashtag in a caption: Latin + Arabic-block letters
_HARVEST_CAP = 5000                 # upper bound on distinct co-tags per harvest — a guard against a pathological/mocked top_media response (untrusted UGC); unreachable under Meta's own caption+page limits

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _graph_get(cfg: Config, path: str, params: dict, *, get=None, token: Optional[str] = None):
    """Read-only Graph GET -> parsed JSON dict, or None on ANY failure (fail-soft enhancement). The
    token rides in the `access_token` param; it is never placed in a logged string. `token` overrides the
    global cfg.meta_graph_token (per-account creds threading); None keeps the global (byte-identical)."""
    get = get or requests.get
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

def hashtag_id(cfg: Config, tag: str, *, get=None):
    """Resolve a '#tag' to its Graph hashtag-node id via ig_hashtag_search (q has no leading '#'), or
    None if it does not resolve / the call fails.
    Meta permissions (docs/instagram-platform/.../hashtag-search): instagram_basic is REQUIRED but NOT
    sufficient on its own — the separate 'Instagram Public Content Access' FEATURE (its OWN App Review
    submission, distinct from the permission) is ALSO mandatory. An operator granting only instagram_basic
    hits an opaque rejection; the missing piece is that App Review feature, not another scope."""
    body = _graph_get(cfg, "ig_hashtag_search",
                      {"user_id": cfg.meta_ig_user_id, "q": tag.lstrip("#")}, get=get)
    try:
        data = body.get("data") if body else None
        return data[0]["id"] if data else None
    except (KeyError, IndexError, TypeError):
        return None

def trend_score(cfg: Config, tag: str, *, get=None):
    """A RELATIVE trend signal for one hashtag = total engagement (likes + comments) over its top_media.
    Meta gives no media_count, so engagement on the top posts is the available visibility proxy. None on
    any failure (unresolved tag, no media, transport error)."""
    hid = hashtag_id(cfg, tag, get=get)
    if hid is None:
        return None
    body = _graph_get(cfg, f"{hid}/top_media",
                      {"user_id": cfg.meta_ig_user_id, "fields": "like_count,comments_count"}, get=get)
    data = body.get("data") if body else None
    if not isinstance(data, list):
        return None
    total = 0.0
    for m in data:
        if not isinstance(m, dict):
            continue
        for k in ("like_count", "comments_count"):
            v = m.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                total += float(v)
    return total

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
    transient failure. Meta signals it as an OAuthException (code 10 / 200) or a 'permission'-worded message.
    Conservative: only a clear permission signal trips the loud path; anything else stays transient (None)."""
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return False
    if err.get("type") == "OAuthException" or err.get("code") in (10, 200, 803):
        return True
    return "permission" in str(err.get("message", "")).lower()

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
    get = get or requests.get
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

def _read_queries(cfg: Config):
    """The recorded search queries, or None if the file is corrupt/unreadable -> the caller treats None
    as FAIL-CLOSED (budget unknown == exhausted). Absent file == clean state (nothing spent) -> []."""
    p = cfg.hashtag_budget_path
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text())
        q = d.get("queries") if isinstance(d, dict) else None
        return q if isinstance(q, list) else None
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None

def budget_remaining(cfg: Config, *, now: datetime | None = None):
    """Remaining ig_hashtag_search budget = 30 - (UNIQUE tags queried in the last 7 days). None means
    FAIL-CLOSED (the counter is unreadable -> refuse all queries). Pure read."""
    now = now or _now()
    q = _read_queries(cfg)
    if q is None:
        return None
    cutoff = now - timedelta(days=_BUDGET_WINDOW_DAYS)
    recent: set[str] = set()
    for e in q:
        try:
            ts = datetime.fromisoformat(e["ts"]); tag = e["tag"]
        except (KeyError, TypeError, ValueError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff and isinstance(tag, str):
            recent.add(tag)
    return max(0, _BUDGET_LIMIT - len(recent))

def record_query(cfg: Config, tag: str, *, now: datetime | None = None) -> None:
    """Append a (tag, ts) to the budget counter, pruning entries older than the window so the file stays
    small. SERIALIZED under an fcntl flock: the read-filter-append-write is a lost-update window — two
    concurrent Studio calls (tag_metrics / discover_corpus) each re-read, filter, and write back, so the
    second overwrote the first and the budget under-counted, over-spending the Meta Graph 30/7-day quota.
    Best-effort persist: on a write/lock failure the next read just sees fewer entries (conservative)."""
    now = now or _now()
    cutoff = now - timedelta(days=_BUDGET_WINDOW_DAYS)
    from fanops.ledger import _file_lock       # lazy: reuse the proven fcntl flock (accounts.py pattern) without a top-level cycle
    from fanops.errors import LockBusyError
    try:
        with _file_lock(cfg.hashtag_budget_lock):
            q = _read_queries(cfg) or []
            kept = []
            for e in q:
                try:
                    ts = datetime.fromisoformat(e["ts"])
                except (KeyError, TypeError, ValueError):
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    kept.append(e)
            kept.append({"tag": tag, "ts": now.isoformat()})
            cfg.hashtag_budget_path.parent.mkdir(parents=True, exist_ok=True)
            cfg.hashtag_budget_path.write_text(json.dumps({"queries": kept}, indent=2))
    except (OSError, LockBusyError) as e:
        get_logger(cfg)("hashtags", tag, "budget_write_failed", err=str(e)[:120])   # best-effort, but no longer silent

def tag_metrics(cfg: Config, tag: str, *, get=None, now: datetime | None = None) -> dict:
    """B2: ON-DEMAND live Graph metrics for ONE hashtag the operator wants to RECOMMEND into a persona's
    corpus — the evidence behind a curation decision. Resolves the hashtag node + sums top_media engagement
    (the same signal sample_trends uses), spending ONE ig_hashtag_search budget slot. Returns a plain dict
    the Studio renders: {tag, resolved, engagement?, sampled_at?, error?}. SAME discipline as sample_trends:
    FAIL-OPEN on creds/transport (resolved False + a reason, never raises); FAIL-CLOSED + LOUD on an
    unreadable budget (resolved False); refuses when the 30/7-day budget is exhausted. The token is never
    echoed. Operator-initiated, so it is NOT gated by FANOPS_HASHTAG_TRENDS (that gates the background
    refresh sampling) — only by creds + budget."""
    now = now or _now()
    h = tag if (tag or "").startswith("#") else f"#{(tag or '').strip()}"
    h = h.strip().lower()
    if not h.lstrip("#"):                                # a bare "#" / blank -> reject BEFORE spending a budget slot
        return {"tag": h, "resolved": False, "error": "enter a valid hashtag"}
    if not (cfg.meta_graph_token and cfg.meta_ig_user_id):
        return {"tag": h, "resolved": False, "error": "Graph not configured — set META_GRAPH_TOKEN + META_IG_USER_ID"}
    remaining = budget_remaining(cfg, now=now)
    if remaining is None:
        return {"tag": h, "resolved": False, "error": "trend budget unreadable — refusing the query (fail-closed)"}
    if remaining <= 0:
        return {"tag": h, "resolved": False, "error": "trend budget exhausted (Meta's 30-searches / 7-day cap) — retry later"}
    score = trend_score(cfg, h, get=get)                 # resolves the node + sums top_media engagement
    record_query(cfg, h, now=now)                        # spend one slot (Meta counts unique searches per 7-day window)
    if score is None:
        return {"tag": h, "resolved": False, "error": "did not resolve on Instagram — no such hashtag, or no recent public media"}
    return {"tag": h, "resolved": True, "engagement": score, "sampled_at": now.isoformat()}


def sample_trends(cfg: Config, candidates: list[str], *, get=None, now: datetime | None = None) -> dict:
    """Spend the 30/7-day budget sampling trend scores for `candidates` (in order). Returns {tag: score}
    for the tags actually sampled. FAIL-OPEN on creds/transport (no token -> {}; a per-tag failure is
    skipped); FAIL-CLOSED + LOUD on the budget (unknown counter -> query nothing). A tag already queried
    in the window is skipped (Meta counts unique searches; re-asking wastes a slot)."""
    now = now or _now()
    log = get_logger(cfg)
    if not (cfg.meta_graph_token and cfg.meta_ig_user_id):
        return {}
    remaining = budget_remaining(cfg, now=now)
    if remaining is None:
        log("hashtags", "trends", "budget_unreadable", note="refusing trend queries (fail-closed)")
        return {}
    cutoff = now - timedelta(days=_BUDGET_WINDOW_DAYS)
    already: set[str] = set()                       # tags queried WITHIN the window only (an expired one is re-queryable)
    for e in (_read_queries(cfg) or []):
        if not isinstance(e, dict):
            continue
        try:
            ts = datetime.fromisoformat(e["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff and isinstance(e.get("tag"), str):
            already.add(e["tag"])
    scores: dict[str, float] = {}
    deferred = 0
    for tag in candidates:
        if remaining <= 0:
            deferred += 1
            continue
        if tag in already:
            continue                                # recent unique search -> free but not re-sampled
        s = trend_score(cfg, tag, get=get)
        record_query(cfg, tag, now=now); remaining -= 1; already.add(tag)   # a duplicate candidate must not spend the budget twice
        if s is not None:
            scores[tag] = s
    if deferred:
        log("hashtags", "trends", "budget_exhausted", sampled=len(scores), deferred=deferred)
    return scores


def harvest_cooccurring(cfg: Config, seed_tags: list[str], *, get=None, now: datetime | None = None) -> dict[str, dict]:
    """M1 (live discovery): resolve each category SEED tag, read its live top_media, and tally the hashtags
    those currently-winning posts use ALONGSIDE the seed — the only Graph-native way to DISCOVER tags we have
    never named (IG has no trending-by-topic endpoint). Returns {co_tag: {"count": int, "host_engagement":
    float}} with the seeds themselves EXCLUDED. SAME discipline as sample_trends: FAIL-OPEN on no creds ({});
    FAIL-CLOSED + LOUD on an unreadable budget; a per-seed transport/resolve failure is skipped. The seed
    RESOLUTION spends one ig_hashtag_search slot (top_media reads are free); a duplicate normalized seed
    resolves once. Re-resolving a within-window seed is budget-NEUTRAL (Meta counts UNIQUE searches) and
    yields fresh top_media — which is what a periodic discovery run wants, so in-window seeds are NOT skipped."""
    now = now or _now()
    log = get_logger(cfg)
    if not (cfg.meta_graph_token and cfg.meta_ig_user_id):
        return {}
    remaining = budget_remaining(cfg, now=now)
    if remaining is None:
        log("hashtags", "discover", "budget_unreadable", note="refusing harvest (fail-closed)")
        return {}
    seeds: list[str] = []; sseen: set[str] = set()
    for s in seed_tags:                                  # normalize + dedupe the seeds (so a seed resolves once)
        n = _norm(s) if isinstance(s, str) else ""
        if n and n not in sseen: sseen.add(n); seeds.append(n)
    out: dict[str, dict] = {}; deferred = 0
    for seed in seeds:
        if remaining <= 0:
            deferred += 1; continue
        hid = hashtag_id(cfg, seed, get=get)
        record_query(cfg, seed, now=now); remaining -= 1     # the ONE budget cost (per unique seed ATTEMPTED — Meta charges the search whether or not the tag resolves)
        if hid is None:
            continue
        body = _graph_get(cfg, f"{hid}/top_media",
                          {"user_id": cfg.meta_ig_user_id, "fields": "caption,like_count,comments_count"}, get=get)
        data = body.get("data") if body else None
        if not isinstance(data, list):
            continue
        for m in data:
            if not isinstance(m, dict):
                continue
            eng = 0.0
            for k in ("like_count", "comments_count"):
                v = m.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    eng += float(v)
            for raw in _TAG_RE.findall(m.get("caption") or ""):
                t = _norm(raw)
                if not t or t in sseen:                       # exclude the seeds themselves
                    continue
                if t not in out and len(out) >= _HARVEST_CAP:
                    continue                                  # cap DISTINCT tags (untrusted-UGC guard); already-seen tags still tally
                agg = out.setdefault(t, {"count": 0, "host_engagement": 0.0})
                agg["count"] += 1; agg["host_engagement"] += eng
    if deferred:
        log("hashtags", "discover", "budget_exhausted", harvested=len(out), deferred=deferred)
    return out


def discover_candidates(cfg: Config, seeds: list[str], *, known=(), measure_k: int = 0,
                        get=None, now: datetime | None = None) -> list[dict]:
    """M2: rank the co-occurrence harvest, DROP the tags we already know (VETTED ∪ store ∪ corpus, passed
    in `known`), and OPTIONALLY measure the top `measure_k` novel tags' live Graph reach within budget. Returns
    ordered proposals [{"tag","count","host_engagement","measured_engagement"?,"sampled_at"?}], most-relevant
    first (by co-occurrence count, then host engagement). The FREE harvest is the primary signal; measurement
    is the only extra budget cost beyond seed resolution, hard-capped by BOTH measure_k AND a live
    budget_remaining re-check, so it never overspends Meta's 30/7-day window. No creds -> [] (harvest no-ops)."""
    now = now or _now()
    known_n = {_norm(t) for t in known if isinstance(t, str)}
    harvested = harvest_cooccurring(cfg, seeds, get=get, now=now)
    ranked = sorted(((t, d) for t, d in harvested.items() if t not in known_n),
                    key=lambda kv: (kv[1]["count"], kv[1]["host_engagement"]), reverse=True)
    out = [{"tag": t, "count": d["count"], "host_engagement": d["host_engagement"]} for t, d in ranked]
    for cand in out[:max(0, measure_k)]:                 # measure only the top-K, budget permitting
        if (budget_remaining(cfg, now=now) or 0) <= 0:
            break
        s = trend_score(cfg, cand["tag"], get=get)       # resolve + sum top_media engagement (spends 1 slot)
        record_query(cfg, cand["tag"], now=now)
        if s is not None:
            cand["measured_engagement"] = s; cand["sampled_at"] = now.isoformat()
    return out
