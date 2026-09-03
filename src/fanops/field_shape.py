# src/fanops/field_shape.py
"""Read-only learning-loop field-shape diagnostics. Answers ONE question: does the LIVE Postiz
analytics field shape carry the signal the learning loop optimizes? The loop weights track._W, and the
live Postiz backend delivers the labels _POSTIZ_LABEL_MAP maps (likes/shares/comments/reach/saves/views);
`retention` is genuinely absent from the live label set — a known gap, NOT a doctor failure. So the
verdict gates ONLY on `reach` (mapped from the live `reach` label), the one weighted key
reach-attribution consumes. Tri-state, so 0 posts is never a vacuous PASS:
  PASS    — sampled posts carry a reach signal (the reach label reconciles)
  FAIL    — sampled posts carry analytics labels but NONE yields `reach`
  NO-DATA — no shipped posts, or none with usable analytics yet
Genuinely read-only: pulls analytics, never writes the ledger / a control sidecar /
flips a flag / calls record_metrics."""
from __future__ import annotations

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.track import _W

# The ONE weighted key the verdict gates on: mapped from the live Postiz `reach` label and the field
# reach-attribution reads. `retention` is absent from the live label set (reported, never gated).
_GATING_KEY = "reach"


def _sampled_submission_ids(led: Ledger) -> list[str]:
    # Shipped posts carry a submission id; sample both published AND analyzed (a post stays shippable
    # evidence after a learn pass advances it to analyzed) so the doctor still has data to inspect.
    return [p.submission_id for p in led.posts.values()
            if p.submission_id and p.state in (PostState.published, PostState.analyzed)]


def _default_fetch(led: Ledger, cfg: Config):
    # Lazy import keeps requests/postiz off the dryrun/core path (mirrors track._default_list_posts).
    from fanops.post.metrics import PostizMetricsClient
    return PostizMetricsClient(cfg, submission_ids=_sampled_submission_ids(led)).list_posts


def _field_shape_report_core(led: Ledger, cfg: Config, *, window: str = "30d", list_posts=None) -> dict:
    """Pure read: pull sampled posts' live analytics and judge the `reach` field shape. `list_posts`
    is injectable for tests; None -> the per-post PostizMetricsClient over the shipped-post ids."""
    fetch = list_posts or _default_fetch(led, cfg)
    rows = fetch(window)
    posts_sampled = len(rows)
    labels_seen = sorted({lbl for r in rows for lbl in (r.get("_raw_labels") or [])})
    metric_keys = {k for r in rows for k in (r.get("metrics") or {})}
    mapped_lift_keys = _mapped_lift_keys()
    unmapped_weight_keys = sorted(k for k in _W if k not in mapped_lift_keys)   # retention (saves now maps)
    reach_present = _GATING_KEY in metric_keys
    if posts_sampled == 0 or not (labels_seen or metric_keys):
        verdict, detail = "NO-DATA", "no shipped posts with usable analytics yet — nothing to judge."
    elif reach_present:
        verdict, detail = "PASS", "the `reach` signal reconciles (the reach label is present)."
    else:
        verdict, detail = "FAIL", "`reach` absent from sampled analytics (the reach label did not reconcile)."
    return {"posts_sampled": posts_sampled, "labels_seen": labels_seen,
            "weight_keys": sorted(_W), "gating_key": _GATING_KEY, "reach_present": reach_present,
            "unmapped_weight_keys": unmapped_weight_keys, "verdict": verdict, "detail": detail}


def _mapped_lift_keys() -> set:
    # The lift keys the live Postiz backend can actually deliver (the label map's targets). Imported
    # lazily so the metrics module (requests) stays off the core path.
    from fanops.post.metrics import _POSTIZ_LABEL_MAP
    return set(_POSTIZ_LABEL_MAP.values())
