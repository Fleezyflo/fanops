"""Postiz metrics + status read clients (GET analytics/post, GET posts)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import requests

from fanops.config import Config
from fanops.errors import PostizAuthError
from fanops.log import get_logger
from fanops.post.postiz import _base, _key
from fanops.post.metrics.common import _json_or_raise, _safe, poster_fail_reason

# ---- Postiz metrics (M2) — the FREE backend's read client. Postiz analytics is PER-POST
# (GET analytics/post/{id}), per-post (not a bulk list), so the client takes the published submission_ids
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
    if not isinstance(series, list):
        return None
    pts = [p for p in series if isinstance(p, dict) and p.get("date")]
    if not pts:
        return None
    latest = max(pts, key=lambda p: str(p.get("date")))
    try:
        return float(latest.get("total"))
    except (TypeError, ValueError):
        return None


def _map_analytics(arr) -> dict:
    # arr = the documented [{label, data:[{total,date}], percentageChange}] array. Map known labels
    # (case-insensitive) -> lift keys; drop unknown/uncollapsible. Defensive: skip non-dict entries.
    out: dict = {}
    if not isinstance(arr, list):
        return out
    for item in arr:
        if not isinstance(item, dict):
            continue
        key = _POSTIZ_LABEL_MAP.get(str(item.get("label", "")).strip().lower())
        if not key:
            continue
        val = _latest_total(item.get("data"))
        if val is not None:
            out[key] = val
    return out


class PostizMetricsClient:
    """Reads Postiz post analytics into the lift/learning loop. submission_ids=None -> list_posts()
    returns [] (no network), so cmd_track/cutover callers never crash. The POSTIZ_API_KEY is sent as
    the Authorization header and NEVER logged/echoed/returned (a 401 body is withheld — SENTINEL test)."""
    def __init__(self, cfg: Config, *, submission_ids: Optional[list[str]] = None):
        self.cfg = cfg
        self.base = _base(cfg)
        self.key = _key(cfg)  # _key raises PostizAuthError if the key is missing
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
        if not self.submission_ids:
            return []
        date = int(datetime.now(timezone.utc).timestamp() * 1000)
        rows = []
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
    """Reconcile READ for the Postiz backend — bulk window only. Postiz has NO per-post status
    endpoint and NO permalink in any response (Context7-verified) — the ONLY status signal is the
    `state` field on a row of GET /public/v1/posts. That list endpoint DEMANDS startDate/endDate
    ISO-8601 (the old `display`/`date` params are rejected with HTTP 400 — verified against the
    running instance 2026-06-21).

    `_fetch_posts` does the windowed GET and indexes the window by row id; `list_all` is the
    whole-corpus window the reconcile mirror consumes (`reconcile_due` → `PostizStatusClient(cfg).list_all()`).
    There is no per-post `get_status` — that method was deleted once the mirror became the sole Postiz
    path (MOL-820). Values carry mapped `status` plus the raw Postiz `state` token, `releaseURL`, and
    `releaseId`. 401 -> PostizAuthError (halt); 5xx -> RuntimeError."""
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base = _base(cfg)
        self.key = _key(cfg)  # _key raises PostizAuthError if missing

    def _fetch_posts(self, start: datetime, end: datetime) -> dict[str, dict]:
        """THE Postiz posts read — every reader of GET /public/v1/posts goes through here.

        WINDOW CONTRACT (verified live 2026-08-07): startDate/endDate are MANDATORY, not a curation
        knob — omitting them returns HTTP 400 "startDate must be a valid ISO 8601 date string" — and
        date-only ISO (YYYY-MM-DD) is the accepted form. The endpoint does NOT paginate: `page`,
        `limit` and `take` are all IGNORED (the response is byte-identical with and without them) and
        the body carries no envelope key whatsoever (`{"posts":[...]}` only — no page/total/nextCursor),
        so there is no page mechanism to be on the wrong side of. ONE call therefore returns every row
        in the window, and a row's ABSENCE is a sound observation rather than a missing page.

        Returns the window indexed by row id: {state (the RAW Postiz token, which the mirror persists),
        status (the backend-agnostic vocabulary via _POSTIZ_STATE_MAP), releaseURL, releaseId, raw}.
        Both body shapes parse ({"posts":[...]} and a bare list). First row wins on a duplicate id
        (`setdefault`)."""
        params = {"startDate": start.date().isoformat(), "endDate": end.date().isoformat()}
        resp = requests.get(f"{self.base}/public/v1/posts", headers={"Authorization": self.key},
                            params=params, timeout=30)
        if resp.status_code == 401:
            raise PostizAuthError("Postiz 401 on posts list — check POSTIZ_API_KEY (response body withheld)")
        if resp.status_code >= 300:
            raise RuntimeError(f"postiz posts {resp.status_code}: {_safe(self.cfg, resp.text)}")
        body = _json_or_raise(resp, "postiz posts", self.cfg)
        rows = body.get("posts", []) if isinstance(body, dict) else (body if isinstance(body, list) else [])
        out: dict[str, dict] = {}
        for r in rows:
            if not isinstance(r, dict) or not isinstance(r.get("id"), str):
                continue
            state = str(r.get("state", ""))
            rec = {"state": state, "status": _POSTIZ_STATE_MAP.get(state.upper(), "scheduled"),
                   "releaseURL": r.get("releaseURL"), "releaseId": r.get("releaseId"), "raw": r}
            if r.get("error") is not None:
                rec["error"] = r.get("error")
            if r.get("errorMessage") is not None:
                rec["errorMessage"] = r.get("errorMessage")
            out.setdefault(r["id"], rec)
        from fanops.postiz_lifecycle import local_postiz_errors
        extra = local_postiz_errors(self.cfg, list(out))
        for sid, raw in extra.items():
            rec = out.get(sid)
            if rec is None or rec.get("error") is not None or rec.get("errorMessage") is not None:
                continue
            rec["error"] = raw
            msg = poster_fail_reason(raw)
            if msg:
                rec["errorMessage"] = msg
        return out

    def list_all(self) -> dict[str, dict]:
        """The reconcile mirror's fetch layer: the WHOLE Postiz corpus in one call, indexed by row id.

        Takes NO caller-supplied window, deliberately. startDate/endDate are the API's mandatory shape
        (see _fetch_posts), and a NARROW window is the mechanism that manufactures a false "this post
        is absent" — so the window is maximal and internal. Measured against the live instance
        2026-08-07: the whole corpus is a single response of a few tens of KB, well under a second, so
        there is no cost argument for narrowing it. Values are _fetch_posts' rows."""
        return self._fetch_posts(datetime(2000, 1, 1, tzinfo=timezone.utc), datetime(2100, 12, 31, tzinfo=timezone.utc))
