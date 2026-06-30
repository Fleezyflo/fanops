"""Real metrics-read client (FIX F05 — v1 had none). list_posts(window) returns rows keyed by
postSubmissionId with a metrics dict. The postSubmissionId key and the status enum
(in-progress|published|scheduled|failed, used by BlotatoStatusClient below) were VERIFIED against
the live Blotato MCP tool schemas 2026-06-02 (AUDIT D5). NOTE the live URL-key split: the published
URL is `publicUrl` on get_post_status (the single-post lookup) but `postUrl` on list_posts — this
client reads metrics rows by postSubmissionId and does NOT read a URL, so the split does not bite
here (a future reader of a list row's URL must use postUrl). Which METRICS fields Blotato exposes
remains an INTEGRATION CHECKPOINT: if saves/shares/retention are unavailable, redesign lift_score
(Task 21) on the available fields. This file also houses the Postiz (PostizMetricsClient/PostizStatusClient)
and Zernio (ZernioMetricsClient/ZernioStatusClient) per-post read clients, each emitting the same
{postSubmissionId, metrics} / {status, publicUrl} row contracts."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote
import requests
from fanops.config import Config
from fanops.errors import BlotatoAuthError, PostizAuthError, ZernioAuthError, redact
from fanops.post.blotato_base import BASE_URL
from fanops.post.postiz import _base, _key
from fanops.post.zernio import _base as _zbase, _key as _zkey
from fanops.timeutil import parse_iso
from fanops.log import get_logger

# A 401 on a metrics/status read is the SAME fatal auth condition as a 401 on publish — raise the
# TYPED error so reconcile's halt-on-auth guard fires (else a bad key grinds every parked post) and
# `track` halts cleanly. Body WITHHELD: the message reaches stdout/ledger/digest, so a 401 body
# echoing the key would leak it (the df85662 redaction closed media.py/blotato_rest.py but missed
# these two read clients — audit follow-up).
def _raise_for_auth(resp) -> None:
    if resp.status_code == 401:
        raise BlotatoAuthError("Blotato 401 unauthorized — check BLOTATO_API_KEY (response body withheld)")

def _safe(cfg, text, limit: int = 200) -> str:
    # Scrub EVERY provider key from an external body before it lands in error_reason/stderr/run.log
    # (stage-5 audit follow-up: the 401 paths withhold the body, but the non-401 echoes still embed it,
    # and a 5xx/proxy/WAF page can reflect the presented key). cfg may be None (legacy callers) -> no-op.
    if cfg is None:
        return (text or "")[:limit]
    return redact(text, cfg.blotato_api_key, cfg.postiz_api_key, cfg.zernio_api_key, limit=limit)

def _json_or_raise(resp, label: str, cfg=None):
    # ECC fix #4: a 200 with a non-JSON body (HTML error page from a misconfigured proxy) made
    # resp.json() raise a raw JSONDecodeError that propagated out of pull_metrics and aborted the
    # WHOLE pass — every post lost its metrics. Convert it to a diagnosable RuntimeError the callers
    # already handle as a per-step failure. requests' JSONDecodeError subclasses ValueError.
    try:
        return resp.json()
    except ValueError:
        raise RuntimeError(f"{label}: non-JSON {resp.status_code} response: {_safe(cfg, resp.text)}")

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
            raise RuntimeError(f"blotato metrics {resp.status_code}: {_safe(self.cfg, resp.text)}")
        data = _json_or_raise(resp, "blotato metrics", self.cfg)
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
            raise RuntimeError(f"blotato status {resp.status_code}: {_safe(self.cfg, resp.text)}")
        return _json_or_raise(resp, "blotato status", self.cfg)


# ---- Postiz metrics (M2) — the FREE backend's read client. Postiz analytics is PER-POST
# (GET analytics/post/{id}), not bulk like Blotato, so the client takes the published submission_ids
# and fetches each. It emits the SAME {postSubmissionId, metrics} row contract pull_metrics consumes,
# plus an inert _raw_labels list that M3's cutover reconcile reads (so it never re-fetches). ----

# VERIFIED-live Postiz analytics labels (Views/Reach/Saves/Likes/Comments/Shares, confirmed against the
# running instance 2026-06-21) -> lift_score key. The optimization-target weights live in tuning.json
# lift_weights (applied downstream by lift_score); unknown labels are DROPPED (lift_score whitelists keys
# anyway). `saves` (top _W weight) and `reach` (the learn_doctor gating key) are exactly the keys the old
# {"impressions":"reach"} map silently dropped — that froze the learning loop on live Postiz. NB:
# `comments`+`views` map but the default _W has no weight for them (present-but-unweighted until the
# operator weights them via tuning.json — intended). `retention` is genuinely absent from the live label
# set, so it stays unmapped (the one remaining _W gap learn_doctor reports).
_POSTIZ_LABEL_MAP = {"likes": "likes", "shares": "shares", "comments": "comments", "reach": "reach", "saves": "saves", "views": "views"}

def _latest_total(series) -> Optional[float]:
    # collapse a label's time-series [{total:str,date:str},...] to its latest `total`, coerced to num.
    # No datable point -> None (drop the label), NOT a positional series[-1] guess: the Postiz array's
    # order is unverified, so guessing could silently pick the OLDEST total (a wrong lift). Reconciled
    # against a real response at M3 cutover (the integration checkpoint).
    if not isinstance(series, list): return None
    pts = [p for p in series if isinstance(p, dict) and p.get("date")]
    if not pts: return None
    latest = max(pts, key=lambda p: str(p.get("date")))
    try: return float(latest.get("total"))
    except (TypeError, ValueError): return None

def _map_analytics(arr) -> dict:
    # arr = the documented [{label, data:[{total,date}], percentageChange}] array. Map known labels
    # (case-insensitive) -> lift keys; drop unknown/uncollapsible. Defensive: skip non-dict entries.
    out: dict = {}
    if not isinstance(arr, list): return out
    for item in arr:
        if not isinstance(item, dict): continue
        key = _POSTIZ_LABEL_MAP.get(str(item.get("label", "")).strip().lower())
        if not key: continue
        val = _latest_total(item.get("data"))
        if val is not None: out[key] = val
    return out

class PostizMetricsClient:
    """Reads Postiz post analytics into the lift/learning loop. submission_ids=None -> list_posts()
    returns [] (no network), so cmd_track/cutover callers never crash. The POSTIZ_API_KEY is sent as
    the Authorization header and NEVER logged/echoed/returned (a 401 body is withheld — SENTINEL test)."""
    def __init__(self, cfg: Config, *, submission_ids: Optional[list[str]] = None):
        self.cfg = cfg; self.base = _base(cfg); self.key = _key(cfg)  # _key raises PostizAuthError if the key is missing
        self.submission_ids = submission_ids

    def _fetch_one(self, submission_id: str, date: int) -> tuple[dict, list]:
        # returns (mapped-metrics, raw-label-strings). The raw labels ride along so M3's cutover
        # reconcile reads row["_raw_labels"] and never does a SECOND network fetch.
        url = f"{self.base}/public/v1/analytics/post/{quote(str(submission_id), safe='')}"  # encode the id so no path metachar can alter the request target
        resp = requests.get(url, headers={"Authorization": self.key}, params={"date": date}, timeout=30)
        if resp.status_code == 401:
            raise PostizAuthError("Postiz 401 on analytics — check POSTIZ_API_KEY (response body withheld)")
        if resp.status_code >= 300:
            raise RuntimeError(f"postiz analytics {resp.status_code}: {_safe(self.cfg, resp.text)}")
        arr = _json_or_raise(resp, "postiz analytics", self.cfg)
        labels = [str(it.get("label", "")) for it in arr if isinstance(it, dict)] if isinstance(arr, list) else []
        return _map_analytics(arr), labels

    def list_posts(self, window: str = "30d") -> list[dict]:
        # Postiz /analytics/post/{id} `date` is a Unix-MS TIMESTAMP (Context7-verified vs the public docs),
        # NOT a day count: we send NOW (ms-epoch) to retrieve the latest totals (_latest_total then collapses
        # the returned series to its newest point). INTEGRATION CHECKPOINT: whether `date=now` returns data
        # (vs the post's own publishDate-in-ms as the anchor) needs a live verify on a real published post —
        # but either conforms to the documented type, unlike the old day-count (7/30) which queried ~1970.
        # `window` is kept for the shared list_posts signature but is NOT a Postiz query param (single date).
        # submission_ids=None -> [] (nothing to fetch; never crashes cmd_track/cutover callers).
        if not self.submission_ids: return []
        date = int(datetime.now(timezone.utc).timestamp() * 1000); rows = []
        for sid in self.submission_ids:
            try:
                metrics, labels = self._fetch_one(sid, date)
            except PostizAuthError:
                raise                                       # a 401 is FATAL for every post — never swallow
            except Exception as e:
                # Per-post isolation: a single post's 5xx/transport failure must NOT abort the whole pass
                # and lose every OTHER post's metrics. SKIP this id entirely (no row) — an empty metrics={}
                # row would make record_metrics WHOLESALE-zero the post's already-captured metrics; skipping
                # preserves the prior snapshot and the post is simply re-polled next pass. Log it, keep going.
                get_logger(self.cfg)("postiz_metrics", str(sid), "fetch_failed", err=str(e)[:120])
                continue
            rows.append({"postSubmissionId": sid, "metrics": metrics, "_raw_labels": labels})
        return rows


# Postiz post `state` (GET /public/v1/posts) -> reconcile's backend-agnostic status. Case-insensitive.
# ONLY PUBLISHED->published and ERROR/FAILED->failed are terminal; EVERYTHING ELSE (QUEUE/DRAFT/unknown)
# -> scheduled (parked) so reconcile_posts leaves it alone. NEVER guess failed for an unknown state —
# that re-queues a possibly-live post (the C1 double-post hazard). Integration checkpoint: the exact
# enum is not pinned in the public docs (like _extract_postiz_id) — confirm against your Postiz version.
_POSTIZ_STATE_MAP = {"PUBLISHED": "published", "ERROR": "failed", "FAILED": "failed"}

class PostizStatusClient:
    """Reconcile READ for the Postiz backend (P2). Postiz has NO per-post status endpoint and NO
    permalink in any response (Context7-verified) — the ONLY status signal is the `state` field on a
    row of GET /public/v1/posts. That list endpoint DEMANDS startDate/endDate ISO-8601 (the old
    `display`/`date` params are rejected with HTTP 400 — verified against the running instance
    2026-06-21), so a post at a FUTURE operator-set time, an old post, or a 2099 cutover probe is found
    only when the query window covers its publishDate. So get_status anchors a ±35d ISO window on the
    post's own publishDate/scheduled_time (or now when unset). Emits the SAME {status, publicUrl} dict
    reconcile_posts consumes; publicUrl is the row's `releaseURL` (the real IG permalink, present on
    PUBLISHED rows). 401 -> PostizAuthError (halt, so
    reconcile's auth-halt fires); 5xx -> RuntimeError (per-post-isolated by reconcile_posts -> parked,
    never failed). A row absent from the page -> {"status":"unknown"} (parked, never guessed failed).

    The `GetStatus` seam (reconcile.py) is Callable[[str], dict]; the per-post `date` window rides in
    via the optional publish_date arg, supplied by the closure _default_get_status builds (which has
    the ledger in hand) — so the seam signature stays unchanged and the 30+ existing reconcile tests
    keep passing. A direct unit call without publish_date falls back to the default (week) window."""
    def __init__(self, cfg: Config):
        self.cfg = cfg; self.base = _base(cfg); self.key = _key(cfg)  # _key raises PostizAuthError if missing

    def get_status(self, submission_id: str, publish_date: Optional[str] = None) -> dict:
        # The live endpoint demands startDate/endDate ISO-8601 (display/date -> HTTP 400). Anchor a ±35d
        # window on the post's publishDate (or now when unset/unparseable) so a future operator-set time,
        # an old post, and a 2099 probe all fall inside the queried page. Date-only ISO is accepted.
        try: anchor = parse_iso(publish_date) if publish_date else datetime.now(timezone.utc)
        except (ValueError, TypeError, AttributeError): anchor = datetime.now(timezone.utc)
        params = {"startDate": (anchor - timedelta(days=35)).date().isoformat(),
                  "endDate": (anchor + timedelta(days=35)).date().isoformat()}
        resp = requests.get(f"{self.base}/public/v1/posts", headers={"Authorization": self.key},
                            params=params, timeout=30)
        if resp.status_code == 401:
            raise PostizAuthError("Postiz 401 on posts list — check POSTIZ_API_KEY (response body withheld)")
        if resp.status_code >= 300:
            raise RuntimeError(f"postiz posts {resp.status_code}: {_safe(self.cfg, resp.text)}")
        body = _json_or_raise(resp, "postiz posts", self.cfg)
        rows = body.get("posts", []) if isinstance(body, dict) else (body if isinstance(body, list) else [])
        row = next((r for r in rows if isinstance(r, dict) and r.get("id") == submission_id), None)
        if row is None:
            return {"status": "unknown"}                    # absent from the page -> left parked, never guessed
        status = _POSTIZ_STATE_MAP.get(str(row.get("state", "")).upper(), "scheduled")
        out = {"status": status}
        if status == "published":
            out["publicUrl"] = row.get("releaseURL") or None   # the real IG permalink (present only on PUBLISHED rows)
        return out


# ---- Zernio metrics + status (Slice 5) — the FREE TikTok backend's read clients. Zernio reads PER-POST
# analytics (GET /analytics/posts/{id}, like Postiz) AND has a true single-post status lookup
# (GET /posts/{id}, like Blotato). Both response SHAPES are INTEGRATION CHECKPOINTS: the maps below accept
# the documented aliases + common nestings (locked offline here), the operator verifies live at first
# publish. The ZERNIO_API_KEY rides the Bearer header and is NEVER logged/echoed (401 body withheld). ----

# Zernio/TikTok analytics label (case-insensitive) -> lift_score key. Includes TikTok's own field names
# (diggCount=likes, playCount=views, collectCount=saves, shareCount, commentCount). `impressions` is
# DELIBERATELY unmapped (the documented {"impressions":"reach"} mistake that froze Postiz learning — for
# TikTok reach != impressions). Unknown labels are dropped (lift_score whitelists keys anyway).
_ZERNIO_LABEL_MAP = {
    "likes": "likes", "like": "likes", "likecount": "likes", "like_count": "likes", "diggcount": "likes", "digg_count": "likes",
    "comments": "comments", "comment": "comments", "commentcount": "comments", "comment_count": "comments",
    "shares": "shares", "share": "shares", "sharecount": "shares", "share_count": "shares", "reposts": "shares",
    "saves": "saves", "save": "saves", "saved": "saves", "bookmarks": "saves", "favorites": "saves", "collectcount": "saves", "collect_count": "saves",
    "reach": "reach", "reachcount": "reach", "accountsreached": "reach", "accounts_reached": "reach",
    "views": "views", "view": "views", "viewcount": "views", "view_count": "views", "plays": "views", "playcount": "views", "play_count": "views", "videoviews": "views", "video_views": "views",
}
_ZERNIO_WRAPS = ("metrics", "insights", "analytics", "stats", "data")

def _zernio_num(v) -> Optional[float]:
    # a metric value may be a scalar OR a {value|count|total:…} object; coerce to float, else None (drop).
    if isinstance(v, dict):
        v = v.get("value", v.get("count", v.get("total")))
    try: return float(v)
    except (TypeError, ValueError): return None

def _map_zernio_analytics(body) -> dict:
    # INTEGRATION CHECKPOINT: accept a FLAT metric dict, a LABELED array (Postiz-style), or ONE nesting
    # level under metrics/insights/analytics/stats/data. Map known aliases -> canonical lift keys; drop
    # unknown/uncoercible. Flat mapping wins so a real metric key isn't mistaken for a wrapper.
    if isinstance(body, dict):
        out: dict = {}
        for k, v in body.items():
            key = _ZERNIO_LABEL_MAP.get(str(k).strip().lower())
            if not key: continue
            num = _zernio_num(v)
            if num is not None: out[key] = num
        if out: return out
        for wrap in _ZERNIO_WRAPS:
            inner = body.get(wrap)
            if isinstance(inner, (dict, list)):
                return _map_zernio_analytics(inner)
        return {}
    if isinstance(body, list):
        out = {}
        for item in body:
            if not isinstance(item, dict): continue
            label = item.get("label") or item.get("metric") or item.get("name") or ""
            key = _ZERNIO_LABEL_MAP.get(str(label).strip().lower())
            if not key: continue
            num = _zernio_num(item.get("value", item.get("count", item.get("total"))))
            if num is not None: out[key] = num
        return out
    return {}

def _zernio_raw_labels(body) -> list:
    # inert diagnostic parity with PostizMetricsClient's _raw_labels: the raw key/label names PRESENT at the
    # metric level (descend ONE wrapper only when the top dict carries no mapped key). Mirrors Postiz, which
    # returns EVERY label in the array (mapped or not) — so this returns every key at the resolved level,
    # never the partial mapped-only-vs-all asymmetry.
    if isinstance(body, dict):
        if not any(_ZERNIO_LABEL_MAP.get(str(k).strip().lower()) for k in body):
            for wrap in _ZERNIO_WRAPS:
                if isinstance(body.get(wrap), (dict, list)): return _zernio_raw_labels(body[wrap])
        return [str(k) for k in body]
    if isinstance(body, list):
        return [str(it.get("label") or it.get("metric") or it.get("name") or "") for it in body if isinstance(it, dict)]
    return []

class ZernioMetricsClient:
    """Reads Zernio per-post TikTok analytics into the lift/learning loop. Mirrors PostizMetricsClient:
    takes the published submission_ids and fetches each, emitting the SAME {postSubmissionId, metrics,
    _raw_labels} row contract pull_metrics consumes. submission_ids=None -> [] (no network). A 401 is FATAL
    (ZernioAuthError, halts the pass); a single post's 5xx/transport failure is isolated (empty row, the
    pass continues) so one bad id never loses every other post's metrics."""
    def __init__(self, cfg: Config, *, submission_ids: Optional[list[str]] = None):
        self.cfg = cfg; self.base = _zbase(cfg); self.key = _zkey(cfg)   # _zkey raises ZernioAuthError if missing
        self.submission_ids = submission_ids

    def _fetch_one(self, submission_id: str) -> tuple[dict, list]:
        url = f"{self.base}/analytics/posts/{quote(str(submission_id), safe='')}"   # encode the id (no path metachar can alter the target)
        resp = requests.get(url, headers={"Authorization": f"Bearer {self.key}"}, timeout=30)
        if resp.status_code == 401:
            raise ZernioAuthError("Zernio 401 on analytics — check ZERNIO_API_KEY (response body withheld)")
        if resp.status_code >= 300:
            raise RuntimeError(f"zernio analytics {resp.status_code}: {_safe(self.cfg, resp.text)}")
        body = _json_or_raise(resp, "zernio analytics", self.cfg)
        return _map_zernio_analytics(body), _zernio_raw_labels(body)

    def list_posts(self, window: str = "30d") -> list[dict]:
        if not self.submission_ids: return []
        rows = []
        for sid in self.submission_ids:
            try:
                metrics, labels = self._fetch_one(sid)
            except ZernioAuthError:
                raise                                       # 401 is FATAL for every post — never swallow
            except Exception as e:
                # SKIP this id (no row) — an empty metrics={} row would make record_metrics wholesale-zero
                # the post's already-captured metrics; skipping preserves the prior snapshot, re-polled next pass.
                get_logger(self.cfg)("zernio_metrics", str(sid), "fetch_failed", err=str(e)[:120])
                continue                                    # per-post isolation: keep going, don't abort the pass
            rows.append({"postSubmissionId": sid, "metrics": metrics, "_raw_labels": labels})
        return rows


# Zernio post status (GET /posts/{id}) -> reconcile's backend-agnostic status. Case-insensitive. Known
# terminal states -> published/failed; EVERYTHING ELSE (queued/processing/unknown) -> scheduled (parked) so
# reconcile_posts leaves it alone — NEVER guess failed for an unknown state (re-queues a possibly-live post,
# the double-post hazard). The status + permalink keys are INTEGRATION CHECKPOINTS.
_ZERNIO_STATE_MAP = {"published": "published", "posted": "published", "live": "published", "complete": "published",
                     "completed": "published", "success": "published", "succeeded": "published", "done": "published",
                     "failed": "failed", "error": "failed", "errored": "failed", "rejected": "failed",
                     "cancelled": "failed", "canceled": "failed"}

def _zernio_platform_rows(body) -> list[dict]:
    """Per-platform publish rows from the live GET /posts/{id} shape (verified 2026-06-30): status + platformPostUrl
    live under post.platforms[], NOT at the top level — missing this stranded every TikTok reconcile as published-with-no-url."""
    if not isinstance(body, dict): return []
    out: list[dict] = []
    for node in (body, body.get("post"), body.get("data"), body.get("result")):
        if not isinstance(node, dict): continue
        plats = node.get("platforms")
        if isinstance(plats, list):
            out.extend(p for p in plats if isinstance(p, dict))
    return out

def _extract_zernio_state(body) -> str:
    for p in _zernio_platform_rows(body):
        for k in ("status", "state", "postStatus", "publishStatus"):
            v = p.get(k)
            if isinstance(v, str) and v: return v
    if not isinstance(body, dict): return ""
    for k in ("status", "state", "postStatus", "publishStatus"):
        v = body.get(k)
        if isinstance(v, str) and v: return v
    for wrap in ("post", "data", "result"):
        nested = body.get(wrap)
        if isinstance(nested, dict):
            s = _extract_zernio_state(nested)
            if s: return s
    return ""

def _extract_zernio_permalink(body) -> Optional[str]:
    for p in _zernio_platform_rows(body):
        for k in ("platformPostUrl", "permalink", "postUrl", "publicUrl", "url", "link", "shareUrl", "share_url", "releaseURL"):
            v = p.get(k)
            if isinstance(v, str) and v.startswith("http"): return v
    if not isinstance(body, dict): return None
    for k in ("permalink", "postUrl", "publicUrl", "url", "link", "shareUrl", "share_url", "releaseURL", "platformPostUrl"):
        v = body.get(k)
        if isinstance(v, str) and v: return v
    for wrap in ("post", "data", "result"):
        nested = body.get(wrap)
        if isinstance(nested, dict):
            u = _extract_zernio_permalink(nested)
            if u: return u
    return None

class ZernioStatusClient:
    """Reconcile READ for the Zernio backend. GET /posts/{id} -> a per-post status + TikTok permalink.
    Unlike Postiz, Zernio HAS a real single-post lookup, so this mirrors BlotatoStatusClient (a bound
    get_status, no date window). Emits the SAME {status, publicUrl} dict reconcile_posts consumes. 401 ->
    ZernioAuthError (halt); 5xx -> RuntimeError (per-post-isolated by reconcile_posts -> parked, never
    failed). An unrecognized state -> {"status":"scheduled"} (parked, never guessed failed)."""
    def __init__(self, cfg: Config):
        self.cfg = cfg; self.base = _zbase(cfg); self.key = _zkey(cfg)   # _zkey raises ZernioAuthError if missing

    def get_status(self, submission_id: str) -> dict:
        url = f"{self.base}/posts/{quote(str(submission_id), safe='')}"
        resp = requests.get(url, headers={"Authorization": f"Bearer {self.key}"}, timeout=30)
        if resp.status_code == 401:
            raise ZernioAuthError("Zernio 401 on post status — check ZERNIO_API_KEY (response body withheld)")
        if resp.status_code >= 300:
            raise RuntimeError(f"zernio status {resp.status_code}: {_safe(self.cfg, resp.text)}")
        body = _json_or_raise(resp, "zernio status", self.cfg)
        status = _ZERNIO_STATE_MAP.get(_extract_zernio_state(body).strip().lower(), "scheduled")
        out = {"status": status}
        if status == "published":
            out["publicUrl"] = _extract_zernio_permalink(body) or None
        return out
