"""Track stage: pull + record per-post performance. saves/shares/retention = algorithmic
lift; likes ~ noise. lift_score WHITELISTS keys (FIX F23/F42 — unknown Blotato fields are
ignored, never KeyError). pull_metrics binds to the real BlotatoMetricsClient by default but
stays injectable for tests; rows match published posts by submission_id (failed posts skipped)."""
from __future__ import annotations
from typing import Callable, Optional
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState

# DEFAULT lift weights: saves/shares are the real algorithmic signal; likes ~ noise (deweighted).
# NOTE: reach at 0.001 can dominate lift for very high-reach posts (reach=100k -> +100);
# this is a deliberate heuristic feeding a human-reviewed amplify decision (Task 22), not an
# autonomous trigger. Operator-overridable WITHOUT a code change via 00_control/tuning.json ->
# "lift_weights" (audit b): when present that map REPLACES this default wholesale (the map IS the
# full key set — a metric absent from it contributes 0), so tuning the optimization target is a
# config edit, not a deploy. Absent override -> these defaults stand.
_W = {"saves": 4.0, "shares": 4.0, "retention": 3.0, "reach": 0.001, "likes": 0.05}
ListPosts = Callable[[str], list[dict]]

def lift_score(metrics: dict, weights: Optional[dict] = None) -> float:
    # weights=None -> the in-code DEFAULT _W (existing callers/tests unchanged). A tuning.json
    # override (threaded in by pull_metrics) REPLACES the weight map. Each weight is coerced to
    # float so a JSON int override (e.g. {"likes": 10}) behaves like the float default.
    w = _W if weights is None else weights
    total = 0.0
    for k, v in metrics.items():
        if k in w and isinstance(v, (int, float)) and isinstance(w[k], (int, float)):
            total += float(w[k]) * float(v)
    return round(total, 4)

def record_metrics(led: Ledger, post_id: str, metrics: dict, *,
                   weights: Optional[dict] = None) -> Ledger:
    # Only a PUBLISHED post becomes analyzed — guard the public entry point so a direct
    # caller can't resurrect a failed/error/already-analyzed post into the winners pool that
    # adjust.py (Task 22) reads. pull_metrics already pre-filters, this makes it safe standalone.
    post = led.posts[post_id]
    if post.state is not PostState.published:
        return led
    # Wholesale replace: Blotato returns the full current metrics snapshot each pull, so
    # latest-snapshot-wins is correct (a merge could retain a metric Blotato later dropped).
    # weights is the resolved override (or None -> default _W) threaded from pull_metrics.
    post.metrics = {**metrics, "lift_score": lift_score(metrics, weights)}
    post.state = PostState.analyzed
    return led

def _default_list_posts(cfg: Config) -> ListPosts:
    from fanops.post.metrics import BlotatoMetricsClient
    return BlotatoMetricsClient(cfg).list_posts

def pull_metrics(led: Ledger, cfg: Config, *, list_posts: Optional[ListPosts] = None,
                 window: str = "30d") -> Ledger:
    fetch = list_posts or _default_list_posts(cfg)
    # Resolve the operator's lift-weight override ONCE per pull (audit b) and thread it down so the
    # real metrics path scores against the tuned optimization target; None -> the default _W.
    weights = cfg.tuning().get("lift_weights")
    by_sub = {p.submission_id: p for p in led.posts.values()
              if p.submission_id and p.state is PostState.published}
    for row in fetch(window):
        post = by_sub.get(row.get("postSubmissionId"))
        if post is not None:
            record_metrics(led, post.id, row.get("metrics", {}), weights=weights)
    return led
