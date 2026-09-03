"""Zernio metrics + status read clients and TikTok oEmbed permalink verification."""
from __future__ import annotations

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


# ---- T8: TikTok permalink LIVE-VERIFY (symmetric with IG's matched media_id) --------------------------
# A captured TikTok URL must be PROVEN a real live post FOR THAT HANDLE before a post rests published — else
# Zernio handing back a dead/wrong URL passes on paper (the same silent-failure class as a phantom IG reel).
# TikTok oEmbed (https://www.tiktok.com/oembed?url=…) is the Graph-native proof: a 200 returns the post's
# author (author_unique_id / author_url), a 404 means the video is dead/removed. No token needed (oEmbed is
# public), so nothing is logged/echoed beyond the pass/fail. FAIL CLOSED at every step (bad url, non-200,
# transport error, author mismatch -> False), so an unverifiable URL never lets a post rest.
_TIKTOK_OEMBED = "https://www.tiktok.com/oembed"


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


def zernio_reported_tiktok_username(body, integration_id: Optional[str]) -> Optional[str]:
    """The TikTok username ZERNIO reports a post went to, read from a GET /posts/{id} status body — the SOLE
    authority for 'which TikTok account did this post publish to'. A post's TikTok video is authored by the
    TikTok USERNAME on the Zernio integration (e.g. our internal @hrmny-blog publishes to tiktok.com/@wahed_bared),
    NOT by our internal account name; verify_tiktok_permalink must therefore compare the live oEmbed author to
    THIS, never to our handle (the shipped bug). Scans the SAME platform rows _zernio_platform_rows yields,
    restricted to tiktok rows, and returns the username of the row whose accountId._id == `integration_id`
    (accounts.json integrations.tiktok == post.account_id). FALLBACK: the SOLE tiktok row when the id matches
    nothing / is None (the bound-method dispatch has no post in scope to pass an id, but a single-account post
    has exactly one tiktok row). Per-row username source order: accountId.username -> platformSpecificData
    __usernameSnapshot -> tiktokUsername -> accountId.displayName. FAIL-CLOSED: None when no tiktok row carries
    a usable username (the caller must then reject — a post with no derivable Zernio username stays parked)."""
    tt_rows = [r for r in _zernio_platform_rows(body) if str(r.get("platform", "")).strip().lower() == "tiktok"]
    if not tt_rows:
        return None

    def _row_username(r: dict) -> Optional[str]:
        acct = r.get("accountId") if isinstance(r.get("accountId"), dict) else {}
        psd = r.get("platformSpecificData") if isinstance(r.get("platformSpecificData"), dict) else {}
        for v in (acct.get("username"), psd.get("__usernameSnapshot"), psd.get("tiktokUsername"), acct.get("displayName")):
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    if integration_id:
        for r in tt_rows:
            acct = r.get("accountId") if isinstance(r.get("accountId"), dict) else {}
            if str(acct.get("_id") or "") == str(integration_id):
                return _row_username(r)                          # the EXACT account this post went to
    if len(tt_rows) == 1:
        return _row_username(tt_rows[0])                         # sole tiktok row -> unambiguous even with no id
    return None                                                  # >1 row and no id match -> can't disambiguate, fail closed


def verify_tiktok_permalink(cfg: Config, url: Optional[str], expected_username: Optional[str], *, get=None) -> bool:
    """True iff `url` is a live TikTok post whose oEmbed author == `expected_username`, proven via TikTok oEmbed.
    `expected_username` is the username ZERNIO reports for this post's own account (zernio_reported_tiktok_username)
    — the real TikTok username the post published to — NOT our internal handle (comparing to the internal handle
    was the shipped bug: @hrmny-blog's live post is tiktok.com/@wahed_bared, so oEmbed author 'wahed_bared' never
    equaled 'hrmny-blog' and a genuinely-live post fail-closed). Injectable `get` (defaults to requests.get) so
    tests never hit the network. FAIL CLOSED: a non-https/empty url (rejected by safe_public_url before any
    request), a missing expected_username, a non-200 oEmbed (404 = dead video), a transport error, or an author
    that doesn't match all return False — an unverifiable URL is never accepted. oEmbed is public (no token)."""
    ok = safe_public_url(url)
    if not ok:
        return False                                            # malformed/non-https -> never reaches the network
    want = _handle_key(expected_username)
    if not want:
        return False                                            # no authoritative username to compare against -> fail closed
    get = get or requests.get
    try:
        resp = get(_TIKTOK_OEMBED, params={"url": ok}, timeout=20)
    except requests.exceptions.RequestException:
        return False                                            # transport error is not proof it is live
    if getattr(resp, "status_code", None) != 200:
        return False                                            # 404 (removed) / any non-200 -> unverified
    try:
        body = resp.json()
    except ValueError:
        return False
    got = _oembed_author_key(body if isinstance(body, dict) else {})
    return got == want                                          # exact, normalized author == Zernio-reported username


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
        if status == "failed":
            msg = poster_fail_reason(body.get("errorMessage"), body.get("error"),
                                     body.get("message"))
            if msg:
                out["errorMessage"] = msg
        if status == "published":
            out["publicUrl"] = _extract_zernio_permalink(body) or None
            # T-VERIFY: carry the Zernio-REPORTED TikTok username out of the SAME body (no second fetch) so
            # reconcile's TikTok REST-gate can verify the live oEmbed author against the username Zernio actually
            # published to — NOT our internal handle. integration_id is not in scope here (bound-method dispatch),
            # so this resolves the SOLE tiktok row; reconcile disambiguates a multi-account body via post.account_id.
            # ADDITIVE + fail-closed: the key is present ONLY when a username is derivable (a status body with no
            # accountId.username adds nothing -> the {status, publicUrl} shape is byte-identical for those bodies).
            uname = zernio_reported_tiktok_username(body, None)
            if uname:
                out["tiktokUsername"] = uname
        return out
