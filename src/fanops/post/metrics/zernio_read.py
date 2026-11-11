"""Zernio metrics + status read clients and TikTok oEmbed permalink verification."""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote

import requests

from fanops.config import Config
from fanops.errors import ZernioAuthError
from fanops.log import get_logger
from fanops.post.zernio import _base as _zbase, _extract_zernio_permalink, _key as _zkey
from fanops.post.metrics.common import _json_or_raise, _safe, poster_fail_reason
from fanops.text import safe_public_url

# ---- Zernio metrics + status (Slice 5) — the FREE TikTok backend's read clients. Zernio reads PER-POST
# analytics (GET /analytics?postId= — docs 2026-06, NOT legacy /analytics/posts/{id}) AND has a true single-post status lookup
# (GET /posts/{id}). Both response SHAPES are INTEGRATION CHECKPOINTS: the maps below accept
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
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _map_zernio_analytics(body) -> dict:
    # INTEGRATION CHECKPOINT: accept a FLAT metric dict, a LABELED array (Postiz-style), or ONE nesting
    # level under metrics/insights/analytics/stats/data. Map known aliases -> canonical lift keys; drop
    # unknown/uncoercible. Flat mapping wins so a real metric key isn't mistaken for a wrapper.
    if isinstance(body, dict):
        out: dict = {}
        for k, v in body.items():
            key = _ZERNIO_LABEL_MAP.get(str(k).strip().lower())
            if not key:
                continue
            num = _zernio_num(v)
            if num is not None:
                out[key] = num
        if out:
            return out
        for wrap in _ZERNIO_WRAPS:
            inner = body.get(wrap)
            if isinstance(inner, (dict, list)):
                return _map_zernio_analytics(inner)
        return {}
    if isinstance(body, list):
        out = {}
        for item in body:
            if not isinstance(item, dict):
                continue
            label = item.get("label") or item.get("metric") or item.get("name") or ""
            key = _ZERNIO_LABEL_MAP.get(str(label).strip().lower())
            if not key:
                continue
            num = _zernio_num(item.get("value", item.get("count", item.get("total"))))
            if num is not None:
                out[key] = num
        return out
    return {}


def _zernio_platform_metric_payload(row: dict) -> object | None:
    # Live TikTok rows often carry lift keys FLAT on platformAnalytics[] (no analytics{} wrapper) or under
    # metrics/stats instead of analytics — both missed the pre-2026-07 extractor and starved every zernio post.
    for key in ("analytics", "metrics", "stats"):
        inner = row.get(key)
        if isinstance(inner, dict) and inner:
            return inner
    return row if _map_zernio_analytics(row) else None


def _zernio_analytics_payload(body) -> object:
    # Live GET /analytics?postId= shape: platformAnalytics[] FIRST (TikTok truth), else top-level analytics{}
    # when it maps to lift keys. A top-level analytics{} of platform-agnostic zeros (impressions/reach) must
    # NOT win over a platform row that carries the real likes/views — the 0/29 TikTok metrics gap.
    if not isinstance(body, dict):
        return body
    pa = body.get("platformAnalytics")
    if isinstance(pa, list):
        for row in pa:
            if not isinstance(row, dict):
                continue
            payload = _zernio_platform_metric_payload(row)
            if payload is not None:
                return payload
    ana = body.get("analytics")
    if isinstance(ana, dict) and ana and _map_zernio_analytics(ana):
        return ana
    return body


def _zernio_raw_labels(body) -> list:
    # inert diagnostic parity with PostizMetricsClient's _raw_labels: the raw key/label names PRESENT at the
    # metric level (descend ONE wrapper only when the top dict carries no mapped key). Mirrors Postiz, which
    # returns EVERY label in the array (mapped or not) — so this returns every key at the resolved level,
    # never the partial mapped-only-vs-all asymmetry.
    if isinstance(body, dict):
        if not any(_ZERNIO_LABEL_MAP.get(str(k).strip().lower()) for k in body):
            for wrap in _ZERNIO_WRAPS:
                if isinstance(body.get(wrap), (dict, list)):
                    return _zernio_raw_labels(body[wrap])
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
        self.cfg = cfg
        self.base = _zbase(cfg)
        self.key = _zkey(cfg)   # _zkey raises ZernioAuthError if missing
        self.submission_ids = submission_ids

    def _fetch_one(self, submission_id: str) -> tuple[dict, list]:
        url = f"{self.base}/analytics"
        resp = requests.get(url, headers={"Authorization": f"Bearer {self.key}"}, params={"postId": str(submission_id)}, timeout=30)
        if resp.status_code == 401:
            raise ZernioAuthError("Zernio 401 on analytics — check ZERNIO_API_KEY (response body withheld)")
        if resp.status_code == 202:
            raise RuntimeError("zernio analytics 202: sync pending")
        if resp.status_code >= 300:
            raise RuntimeError(f"zernio analytics {resp.status_code}: {_safe(self.cfg, resp.text)}")
        body = _json_or_raise(resp, "zernio analytics", self.cfg)
        payload = _zernio_analytics_payload(body)
        return _map_zernio_analytics(payload), _zernio_raw_labels(payload)

    def list_posts(self, window: str = "30d") -> list[dict]:
        if not self.submission_ids:
            return []
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
    live under post.platforms[], NOT at the top level — missing this stranded every TikTok reconcile as published-with-no-url.
    T8: ALSO scans platformAnalytics[] (the GET /analytics?postId= shape) so the same shape-tolerant permalink
    reader finds a url a Zernio analytics body carries (shareUrl/postUrl on the platform row) when the status
    endpoint returned none — the conditional capture fallback. A status body never carries platformAnalytics
    and vice-versa, so scanning both is additive and never crosses the two shapes."""
    if not isinstance(body, dict):
        return []
    out: list[dict] = []
    for node in (body, body.get("post"), body.get("data"), body.get("result")):
        if not isinstance(node, dict):
            continue
        for key in ("platforms", "platformAnalytics"):
            plats = node.get(key)
            if isinstance(plats, list):
                out.extend(p for p in plats if isinstance(p, dict))
    return out


def _extract_zernio_state(body) -> str:
    for p in _zernio_platform_rows(body):
        for k in ("status", "state", "postStatus", "publishStatus"):
            v = p.get(k)
            if isinstance(v, str) and v:
                return v
    if not isinstance(body, dict):
        return ""
    for k in ("status", "state", "postStatus", "publishStatus"):
        v = body.get(k)
        if isinstance(v, str) and v:
            return v
    for wrap in ("post", "data", "result"):
        nested = body.get(wrap)
        if isinstance(nested, dict):
            s = _extract_zernio_state(nested)
            if s:
                return s
    return ""


# ---- TikTok permalink liveness ------------------------------------------------------------------------
# tiktok_permalink_verdict returns "" only when the URL is proven, else a reason. Two proofs, both fail
# closed: (1) oEmbed author_unique_id / author_url matches expected_username, and embed_product_id, if
# present, equals the URL's /video/<id>. (2) oEmbed 200 with no author (TikTok dropped
# author_unique_id, author_url, and author_name): embed_product_id equals that video id AND the
# GET /analytics?postId= platformAnalytics row whose accountId._id == account_id carries the same id.
# A status-row username is not authorship. verify_tiktok_permalink is `not verdict` and does not pass
# analytics, so oembed_no_author stays False for current callers. A mismatched author and any non-200 do too.
_TIKTOK_OEMBED = "https://www.tiktok.com/oembed"
_TIKTOK_VIDEO_RE = re.compile(r"/video/(\d+)")


def _handle_key(handle: Optional[str]) -> str:
    # normalize a handle for comparison: strip a leading @, lowercase, trim. accounts.json stores "@mark";
    # TikTok oEmbed returns the bare "mark" — normalizing both sides makes the compare exact, case-insensitive.
    return (handle or "").strip().lstrip("@").lower()


def _oembed_author_key(body: dict) -> Optional[str]:
    # the author's unique username from an oEmbed body: author_unique_id when present, else the last path
    # segment of author_url (…/@mark -> mark). None when neither is usable -> the verify fails closed.
    if not isinstance(body, dict):
        return None
    uid = body.get("author_unique_id")
    if isinstance(uid, str) and uid.strip():
        return _handle_key(uid)
    au = body.get("author_url")
    if isinstance(au, str) and au.strip():
        seg = au.rstrip("/").rsplit("/", 1)[-1]                  # ".../@mark" -> "@mark"
        if seg:
            return _handle_key(seg)
    return None


def _id_str(v) -> Optional[str]:
    # TikTok video ids exceed 2^53; keep them as exact digit strings. bool is an int subclass — reject it.
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def _tiktok_video_id(url) -> Optional[str]:
    if not isinstance(url, str):
        return None
    m = _TIKTOK_VIDEO_RE.search(url)
    return m.group(1) if m else None


def _embed_product_id(body: dict) -> Optional[str]:
    if not isinstance(body, dict):
        return None
    return _id_str(body.get("embed_product_id"))


def _account_id_of(row: dict) -> Optional[str]:
    acct = row.get("accountId")
    if isinstance(acct, dict):
        return _id_str(acct.get("_id"))
    return _id_str(acct)


def _tiktok_row_username(row: dict) -> Optional[str]:
    acct = row.get("accountId") if isinstance(row.get("accountId"), dict) else {}
    psd = row.get("platformSpecificData") if isinstance(row.get("platformSpecificData"), dict) else {}
    for v in (acct.get("username"), psd.get("__usernameSnapshot"), psd.get("tiktokUsername"), acct.get("displayName")):
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _tiktok_platform_rows(body) -> list[dict]:
    return [r for r in _zernio_platform_rows(body) if str(r.get("platform", "")).strip().lower() == "tiktok"]


def _analytics_tiktok_rows(body) -> list[dict]:
    # Authorship reads platformAnalytics ONLY. A GET /posts/{id} status body carries platforms[] and must
    # not prove the video — the status username alone is not authorship.
    if not isinstance(body, dict):
        return []
    nodes = [body]
    for wrap in ("post", "data", "result"):
        inner = body.get(wrap)
        if isinstance(inner, dict):
            nodes.append(inner)
    out: list[dict] = []
    for node in nodes:
        pa = node.get("platformAnalytics")
        if not isinstance(pa, list):
            continue
        for row in pa:
            if isinstance(row, dict) and str(row.get("platform", "")).strip().lower() == "tiktok":
                out.append(row)
    return out


def _row_video_id(row: dict) -> Optional[str]:
    vid = _id_str(row.get("platformPostId"))
    if vid:
        return vid
    for key in ("platformPostUrl", "shareUrl", "postUrl", "url", "permalink"):
        found = _tiktok_video_id(row.get(key))
        if found:
            return found
    return None


def _analytics_video_match(body, account_id: Optional[str], embed_id: str) -> Optional[bool]:
    """True when this account's analytics row is this video. False when that row names a different video.
    None when there is no row for the account (or it carries no video id) — not authorship."""
    want = str(account_id or "").strip()
    if not want or not embed_id:
        return None
    saw_other = False
    for row in _analytics_tiktok_rows(body):
        if _account_id_of(row) != want:
            continue
        vid = _row_video_id(row)
        if not vid:
            continue
        if vid == embed_id:
            return True
        saw_other = True
    return False if saw_other else None


def _tiktok_status_accounts(body) -> list[dict]:
    """Each TikTok status row's identity. Omitted by get_status when there is no TikTok row."""
    out = []
    for row in _tiktok_platform_rows(body):
        pub = row.get("publishedAt")
        out.append({
            "accountId": _account_id_of(row),
            "username": _tiktok_row_username(row),
            "platformPostId": _id_str(row.get("platformPostId")),
            "publishedAt": pub.strip() if isinstance(pub, str) and pub.strip() else None,
        })
    return out


def zernio_reported_tiktok_username(body, integration_id: Optional[str]) -> Optional[str]:
    """TikTok username on a Zernio status or analytics body.

    The row whose accountId._id == integration_id wins. A passed integration_id that matches no row
    returns None — never the sole TikTok row, which can belong to another account. When integration_id
    is None and there is exactly one TikTok row, that row is used (get_status has no post in scope).
    Username order: accountId.username, platformSpecificData.__usernameSnapshot, tiktokUsername,
    accountId.displayName. None when nothing usable is present.

    This is the username verify compares to an oEmbed author. It is not authorship by itself."""
    tt_rows = _tiktok_platform_rows(body)
    if not tt_rows:
        return None
    if integration_id is not None:
        want = str(integration_id).strip()
        if not want:
            return None
        for row in tt_rows:
            if _account_id_of(row) == want:
                return _tiktok_row_username(row)                 # exact account; no username -> None, not another row
        return None                                              # passed id matched nothing — do not bind the sole row
    if len(tt_rows) == 1:
        return _tiktok_row_username(tt_rows[0])
    return None                                                  # >1 row and no id -> can't disambiguate


def tiktok_permalink_verdict(cfg: Config, url: Optional[str], expected_username: Optional[str], *,
                             get=None, account_id: Optional[str] = None, analytics_body=None,
                             submission_id: Optional[str] = None) -> str:
    """'' when `url` is a live TikTok video this post authored, else a reason string.

    Reasons: bad_url, no_username, oembed_transport, oembed_status:<code>, oembed_no_author,
    video_id_mismatch, author_mismatch.

    Proven when the oEmbed author matches `expected_username` (embed_product_id, if present, must equal
    the URL video id), OR when oEmbed has no author and the analytics row proves it: pass `analytics_body`
    from GET /analytics?postId= (platformAnalytics, not the status body) whose accountId._id ==
    `account_id` and whose video id == embed_product_id == the URL video id. When `analytics_body` is
    omitted and both `account_id` and `submission_id` are set, this fetches that body with `get`.
    A present mismatched author is author_mismatch and is never overridden by analytics. A non-200
    (dead video) is oembed_status:<code>. `get` defaults to requests.get; tests inject it.

    Next unit, from `_tiktok_url_confirmed` (do not treat a reason as success):

        from fanops.post.metrics.zernio_read import tiktok_permalink_verdict
        reason = tiktok_permalink_verdict(
            cfg, url, reported_username, get=get,
            account_id=post.account_id, submission_id=sub)
        proven = reason == ""
    """
    ok = safe_public_url(url)
    if not ok:
        return "bad_url"
    want = _handle_key(expected_username)
    if not want:
        return "no_username"
    getter = get or requests.get
    try:
        resp = getter(_TIKTOK_OEMBED, params={"url": ok}, timeout=20)
    except requests.exceptions.RequestException:
        return "oembed_transport"
    code = getattr(resp, "status_code", None)
    if code != 200:
        return "oembed_transport" if code is None else f"oembed_status:{code}"
    try:
        body = resp.json()
    except ValueError:
        return "oembed_transport"
    if not isinstance(body, dict):
        return "oembed_no_author"
    embed_id = _embed_product_id(body)
    url_id = _tiktok_video_id(ok)
    if embed_id is not None and embed_id != url_id:
        return "video_id_mismatch"
    got = _oembed_author_key(body)
    if got is not None:
        return "" if got == want else "author_mismatch"
    if embed_id is None or url_id is None:
        return "oembed_no_author"                                # no author and no video id to match against analytics
    evidence = analytics_body
    acct = str(account_id).strip() if account_id is not None else ""
    if evidence is None and submission_id and acct:
        evidence = _fetch_zernio_analytics_body(cfg, str(submission_id), get=getter)
    matched = _analytics_video_match(evidence, acct, embed_id)
    if matched is True:
        return ""
    if matched is False:
        return "video_id_mismatch"
    return "oembed_no_author"


def verify_tiktok_permalink(cfg: Config, url: Optional[str], expected_username: Optional[str], *, get=None) -> bool:
    """Bool for current callers: True only when tiktok_permalink_verdict returns "".

    Does not pass account_id, analytics_body, or submission_id, so a 200 with no author
    (oembed_no_author) stays False until reconcile calls tiktok_permalink_verdict. A mismatched
    author and any non-200 stay False too. `expected_username` is the Zernio-reported TikTok
    username, not the internal account handle."""
    return not tiktok_permalink_verdict(cfg, url, expected_username, get=get)


def _fetch_zernio_analytics_body(cfg: Config, submission_id: str, *, get=None):
    """The shared single-fetch core for the two /analytics?postId= fallback readers: returns the parsed body,
    or None on any 401/202/5xx/transport/JSON error (best-effort, FAIL-SOFT). One request; both the permalink
    and the reported-username readers below extract from what it returns, so the fallback never double-fetches."""
    base = _zbase(cfg)
    key = _zkey(cfg)                        # _zkey raises ZernioAuthError if missing (caller-guarded)
    get = get or requests.get
    try:
        resp = get(f"{base}/analytics", headers={"Authorization": f"Bearer {key}"},
                   params={"postId": str(submission_id)}, timeout=30)
    except requests.exceptions.RequestException:
        return None
    if getattr(resp, "status_code", None) != 200:
        return None                                             # 202 sync-pending / 5xx / 401 -> nothing to extract
    try:
        return resp.json()
    except ValueError:
        return None


def zernio_permalink_from_analytics(cfg: Config, submission_id: str, *, get=None) -> Optional[str]:
    """CONDITIONAL capture fallback (T8): when the status endpoint yields NO url, the Zernio
    GET /analytics?postId= body may still carry a permalink field the metrics mapper drops. Fetch it and
    extract the URL via _extract_zernio_permalink (the same shape-tolerant reader the status client uses).
    Best-effort + FAIL-SOFT: a 401/5xx/transport error or an absent url returns None (the post simply stays
    parked and surfaced, never crashes the pass). This is exercised only if Zernio never backfills the url on
    the status endpoint — the caller still oEmbed-verifies whatever this returns before accepting it."""
    body = _fetch_zernio_analytics_body(cfg, submission_id, get=get)
    if body is None:
        return None
    return safe_public_url(_extract_zernio_permalink(body))     # https-only; None if no url-shaped field present


def zernio_analytics_url_and_username(cfg: Config, submission_id: str, integration_id: Optional[str],
                                      *, get=None) -> tuple[Optional[str], Optional[str]]:
    """The url-less-status fallback that reconcile needs: from ONE GET /analytics?postId= fetch return BOTH the
    permalink AND the Zernio-reported tiktok username (keyed by integration_id == post.account_id). The live
    /analytics body carries platformAnalytics[] rows with the SAME accountId shape the status body does, so both
    the url and the username come out of a single request (no double-fetch when the status endpoint gave no url).
    FAIL-SOFT: (None, None) on any error / absent field — the caller then keeps the post parked."""
    body = _fetch_zernio_analytics_body(cfg, submission_id, get=get)
    if body is None:
        return None, None
    return safe_public_url(_extract_zernio_permalink(body)), zernio_reported_tiktok_username(body, integration_id)


def _zernio_http_get(cfg: Config, url: str, *, params: Optional[dict] = None):
    """Shared authenticated GET for Zernio read clients — one literal requests.get site."""
    return requests.get(url, headers={"Authorization": f"Bearer {_zkey(cfg)}"}, params=params, timeout=30)


def _zernio_extract_list_page(body) -> tuple[list[dict], dict]:
    """Shape-tolerant list-page parse — bare list, posts[], or data.posts[]."""
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)], {}
    if not isinstance(body, dict):
        return [], {}
    pag: dict = {}
    if isinstance(body.get("pagination"), dict):
        pag = dict(body["pagination"])
    elif isinstance(body.get("meta"), dict):
        pag = dict(body["meta"])
    else:
        for k in ("page", "totalPages", "total_pages", "limit", "total"):
            if k in body:
                pag[k] = body[k]
    for key in ("posts", "items", "results", "data"):
        items = body.get(key)
        if isinstance(items, list):
            return [r for r in items if isinstance(r, dict)], pag
        if isinstance(items, dict):
            inner = items.get("posts") or items.get("items") or items.get("results")
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)], pag
    return [], pag


def zernio_list_posts(cfg: Config, *, account_id: str, search: Optional[str] = None,
                      date_from: Optional[str] = None, date_to: Optional[str] = None,
                      status: str = "published", page: int = 1, limit: int = 50) -> tuple[list[dict], dict]:
    """GET /posts list — account-scoped discovery for reconcile vendor lookup. Shape-tolerant like
    zernio_list_accounts; returns (posts, pagination) with page/totalPages when present."""
    params: dict = {"accountId": account_id, "status": status, "page": page, "limit": limit}
    if search:
        params["search"] = search
    if date_from:
        params["dateFrom"] = date_from
    if date_to:
        params["dateTo"] = date_to
    resp = _zernio_http_get(cfg, f"{_zbase(cfg)}/posts", params=params)
    if resp.status_code == 401:
        raise ZernioAuthError("Zernio 401 on posts list — check ZERNIO_API_KEY (response body withheld)")
    if resp.status_code >= 300:
        raise RuntimeError(f"zernio posts list {resp.status_code}: {_safe(cfg, resp.text)}")
    posts, pag = _zernio_extract_list_page(_json_or_raise(resp, "zernio posts list", cfg))
    if "page" not in pag:
        pag["page"] = page
    if "totalPages" not in pag and "total_pages" not in pag:
        pag["totalPages"] = 1
    return posts, pag


class ZernioStatusClient:
    """Reconcile READ for the Zernio backend. GET /posts/{id} -> a per-post status + TikTok permalink.
    Unlike Postiz, Zernio HAS a real single-post lookup, so this is a bound single-post get_status (a bound
    get_status, no date window). Emits the SAME {status, publicUrl} dict reconcile_posts consumes. 401 ->
    ZernioAuthError (halt); 5xx -> RuntimeError (per-post-isolated by reconcile_posts -> parked, never
    failed). An unrecognized state -> {"status":"scheduled"} (parked, never guessed failed)."""
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base = _zbase(cfg)
        self.key = _zkey(cfg)   # _zkey raises ZernioAuthError if missing

    def fetch_body(self, submission_id: str) -> dict:
        """GET /posts/{id} raw body — used when integration-scoped identity checks need platform rows."""
        url = f"{self.base}/posts/{quote(str(submission_id), safe='')}"
        resp = _zernio_http_get(self.cfg, url)
        if resp.status_code == 401:
            raise ZernioAuthError("Zernio 401 on post status — check ZERNIO_API_KEY (response body withheld)")
        if resp.status_code >= 300:
            raise RuntimeError(f"zernio status {resp.status_code}: {_safe(self.cfg, resp.text)}")
        return _json_or_raise(resp, "zernio status", self.cfg)

    def get_status(self, submission_id: str) -> dict:
        body = self.fetch_body(submission_id)
        status = _ZERNIO_STATE_MAP.get(_extract_zernio_state(body).strip().lower(), "scheduled")
        out = {"status": status}
        if status == "failed":
            plat = _zernio_platform_rows(body)
            msg = poster_fail_reason(
                body.get("errorMessage"), body.get("error"), body.get("message"),
                *[p.get(k) for p in plat for k in ("errorMessage", "error", "message")])
            if msg:
                out["errorMessage"] = msg
        if status == "published":
            out["publicUrl"] = _extract_zernio_permalink(body) or None
            # integration_id is None here (bound get_status): sole TikTok row only. A multi-row body adds
            # no tiktokUsername; callers disambiguate with tiktokAccounts[].accountId == post.account_id.
            # Key stays absent when no username is derivable, so bodies with no TikTok row keep the old shape.
            uname = zernio_reported_tiktok_username(body, None)
            if uname:
                out["tiktokUsername"] = uname
        accounts = _tiktok_status_accounts(body)
        if accounts:
            out["tiktokAccounts"] = accounts
        return out
