"""Real metrics-read client (FIX F05 — v1 had none). list_posts(window) returns rows keyed by
postSubmissionId with a metrics dict. The postSubmissionId key and the status enum
(in-progress|published|scheduled|failed, used by BlotatoStatusClient below) were VERIFIED against
the live Blotato MCP tool schemas 2026-06-02 (AUDIT D5). NOTE the live URL-key split: the published
URL is `publicUrl` on get_post_status (the single-post lookup) but `postUrl` on list_posts — this
client reads metrics rows by postSubmissionId and does NOT read a URL, so the split does not bite
here (a future reader of a list row's URL must use postUrl). Which METRICS fields Blotato exposes
remains an INTEGRATION CHECKPOINT: if saves/shares/retention are unavailable, redesign lift_score
(Task 21) on the available fields."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote
import requests
from fanops.config import Config
from fanops.errors import BlotatoAuthError, PostizAuthError
from fanops.post.blotato_base import BASE_URL
from fanops.post.postiz import _base, _key
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

def _json_or_raise(resp, label: str):
    # ECC fix #4: a 200 with a non-JSON body (HTML error page from a misconfigured proxy) made
    # resp.json() raise a raw JSONDecodeError that propagated out of pull_metrics and aborted the
    # WHOLE pass — every post lost its metrics. Convert it to a diagnosable RuntimeError the callers
    # already handle as a per-step failure. requests' JSONDecodeError subclasses ValueError.
    try:
        return resp.json()
    except ValueError:
        raise RuntimeError(f"{label}: non-JSON {resp.status_code} response: {(resp.text or '')[:200]}")

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
            raise RuntimeError(f"blotato metrics {resp.status_code}: {resp.text[:200]}")
        data = _json_or_raise(resp, "blotato metrics")
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
            raise RuntimeError(f"blotato status {resp.status_code}: {resp.text[:200]}")
        return _json_or_raise(resp, "blotato status")


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
            raise RuntimeError(f"postiz analytics {resp.status_code}: {(resp.text or '')[:200]}")
        arr = _json_or_raise(resp, "postiz analytics")
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
                # Per-post isolation: a single post's 5xx/transport failure must NOT abort the whole
                # pass and lose every OTHER post's metrics. Log + skip THIS id (empty row), keep going.
                get_logger(self.cfg)("postiz_metrics", str(sid), "fetch_failed", err=str(e)[:120])
                metrics, labels = {}, []
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
            raise RuntimeError(f"postiz posts {resp.status_code}: {(resp.text or '')[:200]}")
        body = _json_or_raise(resp, "postiz posts")
        rows = body.get("posts", []) if isinstance(body, dict) else (body if isinstance(body, list) else [])
        row = next((r for r in rows if isinstance(r, dict) and r.get("id") == submission_id), None)
        if row is None:
            return {"status": "unknown"}                    # absent from the page -> left parked, never guessed
        status = _POSTIZ_STATE_MAP.get(str(row.get("state", "")).upper(), "scheduled")
        out = {"status": status}
        if status == "published":
            out["publicUrl"] = row.get("releaseURL") or None   # the real IG permalink (present only on PUBLISHED rows)
        return out
