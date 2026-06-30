"""Track stage: pull + record per-post performance. saves/shares/retention = algorithmic
lift; likes ~ noise. lift_score WHITELISTS keys (FIX F23/F42 — unknown Blotato fields are
ignored, never KeyError). pull_metrics binds the metrics reader per-account through _default_list_posts
(postiz/zernio per-post analytics, else the Blotato bulk list), stays injectable for tests; rows match
published/analyzed posts by submission_id."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Callable, Optional
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.metrics_schedule import due_offset
from fanops.models import LIFT_SCORE, PostState, is_real_submission_id
from fanops.timeutil import iso_z

# DEFAULT lift weights: saves/shares are the real algorithmic signal; likes ~ noise (deweighted).
# NOTE: reach at 0.001 can dominate lift for very high-reach posts (reach=100k -> +100);
# this is a deliberate heuristic feeding a human-reviewed amplify decision (Task 22), not an
# autonomous trigger. Operator-overridable WITHOUT a code change via 00_control/tuning.json ->
# "lift_weights" (audit b): when present that map REPLACES this default wholesale (the map IS the
# full key set — a metric absent from it contributes 0), so tuning the optimization target is a
# config edit, not a deploy. Absent override -> these defaults stand.

def _metrics_trackable(cfg: Config, sid: Optional[str]) -> bool:
    # C4 dryrun money loop: dryrun_ ids bind injected metrics when not live; live/reconcile still skip them (R1/D16).
    if not sid: return False
    if is_real_submission_id(sid): return True
    return not cfg.is_live and sid.startswith("dryrun_")

_W = {"saves": 4.0, "shares": 4.0, "retention": 3.0, "reach": 0.001, "likes": 0.05}
# T4 (honest lift): a weight at/above this is a PRIMARY signal (saves/shares/retention). When a primary
# key is ABSENT from a metrics row — e.g. Postiz cannot deliver saves/retention — the lift_score is a
# PARTIAL objective; record_metrics stamps lift_degraded so the operator sees it instead of trusting a
# reach/shares-dominated scalar. reach (0.001) / likes (0.05) are low-weight proxies, never "missing".
_HIGH_WEIGHT = 1.0
ListPosts = Callable[[str], list[dict]]

def _shape_proves_learning(metrics: dict, *, weights: Optional[dict] = None) -> bool:
    """True when a live analyzed row proves the metric field-shape for learning unfreeze. Broader than
    `not lift_degraded`: Postiz never delivers `retention`, so a Postiz-shaped row stays lift_degraded
    yet proves the shape once `reach` + a primary engagement key (saves|shares) reconcile — mirroring
    learn_doctor's reach gate, not an all-_W verdict. Still fails closed on present-but-null primaries
    (D1) and on reach-only noise (likes+reach with no saves/shares). A full primary set (Blotato-shaped)
    always proves."""
    if LIFT_SCORE not in metrics:
        return False
    w = _W if weights is None else weights
    raw = {k: v for k, v in metrics.items()
           if k not in (LIFT_SCORE, "lift_degraded", "lift_missing_keys")}
    for k, wt in w.items():
        if not (isinstance(wt, (int, float)) and not isinstance(wt, bool) and wt >= _HIGH_WEIGHT):
            continue
        if k in raw and (raw[k] is None or not isinstance(raw[k], (int, float)) or isinstance(raw[k], bool)):
            return False                                    # D1: explicit null/non-numeric in the live row
    if not _missing_high_weight(metrics, weights):
        return True                                         # full primary set (e.g. Blotato)
    has_reach = isinstance(metrics.get("reach"), (int, float)) and not isinstance(metrics.get("reach"), bool)
    has_eng = any(isinstance(metrics.get(k), (int, float)) and not isinstance(metrics.get(k), bool)
                 for k in ("saves", "shares"))
    if has_reach and has_eng:
        return True                                         # Postiz-shaped proof
    if isinstance(metrics.get("saves"), (int, float)) and not isinstance(metrics.get("saves"), bool):
        return True                                         # Zernio-shaped: saves lands without reach
    return False

def _missing_high_weight(metrics: dict, weights: Optional[dict]) -> list[str]:
    """The ACTIVE high-weight keys absent from this row (sorted). Judged against the ACTIVE weight map
    (a tuning override REPLACES _W), so 'degraded' tracks whatever objective is configured. NEVER
    recalibrates _W — purely observational (audit H3). D1: a key PRESENT-BUT-NULL counts as missing —
    lift_score drops a non-numeric value (isinstance guard) so a null saves contributes nothing, exactly
    like an absent saves; treating it as present would stamp a partial objective 'complete' and could
    auto-unfreeze learning on an unproven shape."""
    w = _W if weights is None else weights
    return sorted(k for k, wt in w.items()
                  if isinstance(wt, (int, float)) and not isinstance(wt, bool) and wt >= _HIGH_WEIGHT
                  and metrics.get(k) is None)    # absent OR present-but-null (.get -> None for both)

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

def _captured_offsets(post) -> set[str]:
    # The cadence offsets already present in a post's series (P3). 'legacy' (the migration tag) rides
    # along harmlessly — it is not a CADENCE_OFFSETS member, so it never blocks a real future poll.
    return {r.get("offset") for r in post.metrics_series if isinstance(r, dict)}

def record_metrics(led: Ledger, post_id: str, metrics: dict, *,
                   weights: Optional[dict] = None, offset: Optional[str] = None,
                   captured_at: Optional[str] = None) -> Ledger:
    # P3: a PUBLISHED post flips to analyzed on the first matched poll (terminal UNCHANGED); an
    # already-ANALYZED post stays analyzed but remains RE-POLLABLE so its metrics_series accumulates later
    # cadence offsets across the year. A non-(published|analyzed) post — failed/error/rejected/
    # needs_reconcile — is an absolute no-op (never resurrected into the winners pool adjust.py reads).
    post = led.posts[post_id]
    prior = post.state
    if prior not in (PostState.published, PostState.analyzed):
        return led
    # CULM-6: a transiently-partial pull must not REGRESS a complete snapshot. Carry forward any PRIMARY
    # weighted key the new row DROPS but the stored snapshot still had (a non-null value), then score the
    # MERGED row so lift reflects the richest known truth, never a mid-cadence regression. The append-only
    # metrics_series keeps every RAW row for forensics; this protects only the latest-wins `metrics`.
    # Non-primary keys still follow latest-wins. A backend dropping a metric FOREVER keeps the carried value
    # (the series shows the drop; latest-wins must not regress lift). weights is the resolved override
    # (None -> default _W) threaded from pull_metrics; Post.metrics stays the LATEST snapshot (no offset/
    # captured_at keys) so every existing reader is byte-identical.
    prior_metrics = {k: v for k, v in (post.metrics or {}).items()
                     if k not in (LIFT_SCORE, "lift_degraded", "lift_missing_keys")}
    recovered = {k: prior_metrics[k] for k in _missing_high_weight(metrics, weights)
                 if prior_metrics.get(k) is not None}
    merged = {**metrics, **recovered}
    post.metrics = {**merged, LIFT_SCORE: lift_score(merged, weights)}
    # T4: ADDITIVE honest-lift marker (NOT a scoring change — lift_score is untouched). When a primary
    # weighted metric is absent from the MERGED row, the objective is partial; surface it so the operator
    # does not trust a degraded lift as a full one. Marker keys are not weights, so a later lift_score
    # ignores them. Absent any missing primary key -> no marker -> byte-identical to today.
    missing = _missing_high_weight(merged, weights)
    if missing:
        post.metrics["lift_degraded"] = True
        post.metrics["lift_missing_keys"] = missing
    # P3 append-only time-series: one SPARSE row per cadence offset, a superset of the LATEST snapshot +
    # {offset, captured_at} provenance (so it carries the same degraded markers). Append iff an offset is
    # supplied AND not already captured — never duplicate, never interpolate, never rewrite an earlier
    # row. Immutable list update (project immutability rule). offset=None (legacy direct call / not-yet-due
    # poll) updates the LATEST snapshot but adds no row.
    if offset is not None and offset not in _captured_offsets(post):
        post.metrics_series = [*post.metrics_series, {**post.metrics, "offset": offset, "captured_at": captured_at}]
    if prior is PostState.published:
        post.state = PostState.analyzed
    return led

def _metrics_client_for(cfg: Config, backend: str, submission_ids: Optional[list[str]]) -> ListPosts:
    # One backend's metrics fetcher. postiz/zernio read PER-POST analytics (need the published ids);
    # rest/mcp reads the Blotato BULK list (ignores ids — UNCHANGED). Lazy imports keep requests/postiz/
    # zernio off the dryrun/core path.
    if backend == "postiz":
        from fanops.post.metrics import PostizMetricsClient
        return PostizMetricsClient(cfg, submission_ids=submission_ids).list_posts
    if backend == "zernio":
        from fanops.post.metrics import ZernioMetricsClient
        return ZernioMetricsClient(cfg, submission_ids=submission_ids).list_posts
    from fanops.post.metrics import BlotatoMetricsClient
    return BlotatoMetricsClient(cfg).list_posts

def _default_list_posts(cfg: Config, *, submission_ids: Optional[list[str]] = None,
                        posts: Optional[list] = None) -> ListPosts:
    # Backend-polymorphic. `posts` (per-post routing, zernio): group the pollable posts by RESOLVED backend
    # (an accounts.json `backends` override -> else the global FANOPS_POSTER) and fetch each group from its
    # own client, concatenating the rows — so IG-via-Postiz and TikTok-via-Zernio metrics pull in ONE pass.
    # When every post resolves to the global backend (no overrides), this is byte-identical to a single
    # client. `submission_ids` (back-compat / a true single-backend deployment): ALL ids -> the global
    # backend's client (UNCHANGED). Lazy imports keep deps off the dryrun/core path.
    if posts is None:
        return _metrics_client_for(cfg, cfg.poster_backend, submission_ids)
    from fanops.accounts import load_accounts_safe
    accounts, err = load_accounts_safe(cfg)
    if err: get_logger(cfg)("backend_route", "accounts", "load_failed_global_fallback", err=err)
    groups: dict[str, list[str]] = {}
    for p in posts:
        if not p.submission_id: continue
        backend = accounts.effective_provider(p.account, p.platform)   # H1: per-channel provider, NOT the global fallback
        if backend is None: continue                                   # no provider -> don't dryrun-default a live post's metrics
        groups.setdefault(backend, []).append(p.submission_id)
    fetchers = [_metrics_client_for(cfg, b, ids) for b, ids in groups.items()]
    def fetch(window: str = "30d") -> list[dict]:
        rows: list[dict] = []
        for f in fetchers:
            rows.extend(f(window))
        return rows
    return fetch

def pull_metrics(led: Ledger, cfg: Config, *, list_posts: Optional[ListPosts] = None,
                 window: str = "30d", now: Optional[datetime] = None) -> Ledger:
    # Clock injected (tests pass `now`; real callers default to UTC now — mirrors approve_post's
    # now_iso). The fetch id-set + match-set are PUBLISHED OR ANALYZED (P3): an analyzed post stays
    # re-pollable so its series accumulates later cadence offsets. due_offset returns None once a post's
    # series is complete (or the post predates published_at), so a finished/timeline-less post is still
    # fetched + flipped/updated but records no new row. Inert id-thread for Blotato (it ignores it).
    now = now or datetime.now(timezone.utc)
    pollable = (PostState.published, PostState.analyzed)
    fetch = list_posts or _default_list_posts(
        cfg, posts=[p for p in led.posts.values()
                    if p.submission_id and p.state in pollable])      # per-post backend routing (zernio)
    # Resolve the operator's lift-weight override ONCE per pull (audit b) and thread it down so the
    # real metrics path scores against the tuned optimization target; None -> the default _W.
    weights = cfg.tuning().get("lift_weights")
    log = get_logger(cfg)
    by_sub = {}
    for p in led.posts.values():
        if not (p.submission_id and p.state in pollable): continue
        if not _metrics_trackable(cfg, p.submission_id):
            log("track", p.id, "skip_unreal_submission_id", sub=p.submission_id)   # CULM-3: fanops_ token -> can't attribute
            continue
        by_sub[p.submission_id] = p
    for row in fetch(window):
        post = by_sub.get(row.get("postSubmissionId"))
        if post is not None:
            # ALWAYS record on a match (preserves the first-poll published->analyzed flip + the LATEST
            # snapshot, R1); the due offset gates ONLY whether a new time-series ROW is appended.
            off = due_offset(post.published_at, _captured_offsets(post), now)
            record_metrics(led, post.id, row.get("metrics", {}), weights=weights,
                           offset=off, captured_at=(iso_z(now) if off else None))
    _auto_validate_metrics_shape(led, cfg)
    return led


def _auto_validate_metrics_shape(led: Ledger, cfg: Config) -> None:
    """De-gated learning (the operator's `fanops cutover metrics` step is removed): the FIRST real,
    non-degraded analyzed metric pulled from a LIVE backend PROVES the metric field-shape against _W —
    exactly what the manual cutover reconciled by hand. Postiz-shaped rows stay lift_degraded (retention
    is absent) yet still prove the shape once reach + saves|shares reconcile. Auto-stamp cutover.json `metrics_confirmed` so
    `learning_validated` unfreezes with NO operator probe. dryrun never reaches a real analytics row, so it
    never falsely unfreezes; a DEGRADED row (a primary weighted key absent) is the unproven/mis-keyed case
    the gate exists for and never stamps. Idempotent (skips once confirmed); the manual cutover still works."""
    log = get_logger(cfg)
    if not cfg.is_live:
        log("learning", "auto_validate", "frozen_not_live"); return   # dryrun never proves a real shape
    from fanops.validation_gate import learning_validated
    if learning_validated(cfg):
        return                                                   # already proven -> not frozen, nothing to explain
    weights = cfg.tuning().get("lift_weights")
    analyzed = [p for p in led.posts.values() if p.state is PostState.analyzed and LIFT_SCORE in p.metrics]
    proven = next((p for p in analyzed if _shape_proves_learning(p.metrics, weights=weights)), None)
    if proven is None:                                           # XC-4: name WHY learning stays frozen, per branch
        if not analyzed:
            log("learning", "auto_validate", "frozen_no_analyzed_metric")    # no analyzed row yet (waiting on a pull)
        else:
            log("learning", "auto_validate", "frozen_all_degraded", n=len(analyzed))   # rows exist, all missing a primary key
        return
    from fanops import cutover
    cutover._save_state(cfg, {"metrics_confirmed": True, "metrics_confirmed_auto": True})  # real data proved it
    log("learning", "auto_validate", "unfrozen_on_real_metric", post=proven.id)   # the proof landed
