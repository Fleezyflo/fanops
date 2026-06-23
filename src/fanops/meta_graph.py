# src/fanops/meta_graph.py
"""M4 live half — a thin, budget-aware, READ-ONLY Meta Graph client that samples hashtag TREND signal
(finding #7: hashtags update on what is trending in our niche). Used ONLY by `hashtags refresh`, never
on the publish path. Two design rules, both load-bearing:

  1. ENHANCEMENT -> the TRANSPORT fails SOFT. Any per-tag fetch failure (no creds, 401, 5xx, timeout,
     non-JSON, unresolved hashtag) returns None and is skipped — a missing trend never blocks a refresh
     (own-reach ranking still stands). The token is sent as the Graph `access_token` param and is NEVER
     logged/echoed (METRICS_CLIENT_AUTH_DISCIPLINE — mirrors post/metrics.py).

  2. The 30-unique-hashtags / rolling-7-day `ig_hashtag_search` cap is a HARD Meta limit, so the BUDGET
     fails CLOSED + LOUD: if the persisted counter (00_control/hashtag_budget.json) is unreadable/corrupt,
     budget_remaining returns None and sample_trends queries NOTHING (better a stale store than a banned
     app). Meta deprecated hashtag media_count, so the trend signal is engagement summed over top_media."""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
import requests
from fanops.config import Config
from fanops.log import get_logger

_BUDGET_LIMIT = 30                  # Meta: 30 UNIQUE hashtags per IG user per rolling 7 days
_BUDGET_WINDOW_DAYS = 7

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _graph_get(cfg: Config, path: str, params: dict, *, get=None):
    """Read-only Graph GET -> parsed JSON dict, or None on ANY failure (fail-soft enhancement). The
    token rides in the `access_token` param; it is never placed in a logged string."""
    get = get or requests.get
    try:
        resp = get(f"{cfg.meta_graph_url}/{path}",
                   params={**params, "access_token": cfg.meta_graph_token}, timeout=20)
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
    None if it does not resolve / the call fails."""
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
    small. Best-effort persist (an OSError just means the next read sees fewer entries — conservative)."""
    now = now or _now()
    cutoff = now - timedelta(days=_BUDGET_WINDOW_DAYS)
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
    try:
        cfg.hashtag_budget_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.hashtag_budget_path.write_text(json.dumps({"queries": kept}, indent=2))
    except OSError:
        pass

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
