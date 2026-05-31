"""Track stage: pull + record per-post performance. saves/shares/retention = algorithmic
lift; likes ~ noise. lift_score WHITELISTS keys (FIX F23/F42 — unknown Blotato fields are
ignored, never KeyError). pull_metrics binds to the real BlotatoMetricsClient by default but
stays injectable for tests; rows match published posts by submission_id (failed posts skipped)."""
from __future__ import annotations
from typing import Callable, Optional
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState

_W = {"saves": 4.0, "shares": 4.0, "retention": 3.0, "reach": 0.001, "likes": 0.05}
ListPosts = Callable[[str], list[dict]]

def lift_score(metrics: dict) -> float:
    total = 0.0
    for k, v in metrics.items():
        if k in _W and isinstance(v, (int, float)):
            total += _W[k] * float(v)
    return round(total, 4)

def record_metrics(led: Ledger, post_id: str, metrics: dict) -> Ledger:
    post = led.posts[post_id]
    post.metrics = {**metrics, "lift_score": lift_score(metrics)}
    post.state = PostState.analyzed
    return led

def _default_list_posts(cfg: Config) -> ListPosts:
    from fanops.post.metrics import BlotatoMetricsClient
    return BlotatoMetricsClient(cfg).list_posts

def pull_metrics(led: Ledger, cfg: Config, *, list_posts: Optional[ListPosts] = None,
                 window: str = "30d") -> Ledger:
    fetch = list_posts or _default_list_posts(cfg)
    by_sub = {p.submission_id: p for p in led.posts.values()
              if p.submission_id and p.state is PostState.published}
    for row in fetch(window):
        post = by_sub.get(row.get("postSubmissionId"))
        if post is not None:
            record_metrics(led, post.id, row.get("metrics", {}))
    return led
