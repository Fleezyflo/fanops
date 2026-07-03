"""Track stage: pull + record per-post performance. saves/shares/retention = algorithmic
lift; likes ~ noise. lift_score WHITELISTS keys (FIX F23/F42 — unknown fields are
ignored, never KeyError). pull_metrics binds the metrics reader per-account through _default_list_posts
(postiz/zernio per-post analytics), stays injectable for tests; rows match
published/analyzed posts by submission_id."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Callable, Optional
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.metrics_schedule import due_offset
from fanops.models import LIFT_SCORE, Platform, PostState, is_real_submission_id
from fanops.timeutil import iso_z

# DEFAULT lift weights: saves/shares are the real algorithmic signal; likes ~ noise (deweighted).
# NOTE: reach at 0.001 can dominate lift for very high-reach posts (reach=100k -> +100);
# this is a deliberate heuristic feeding a human-reviewed amplify decision (Task 22), not an
# autonomous trigger. Operator-overridable WITHOUT a code change via 00_control/tuning.json ->
# "lift_weights" (audit b): when present that map REPLACES this default wholesale (the map IS the
# full key set — a metric absent from it contributes 0), so tuning the optimization target is a
# config edit, not a deploy. Absent override -> these defaults stand.

def _metrics_trackable(cfg: Config, sid: Optional[str]) -> bool:
    # dryrun-boundary M2: only a REAL backend id is trackable. The old `dryrun_` money-loop branch is
    # gone — post-M1 a dryrun post halts `queued` and never reaches a distribution state, so it is never
    # in the pollable set here anyway; there is no dryrun learning loop left to feed.
    return bool(sid) and is_real_submission_id(sid)

_W = {"saves": 4.0, "shares": 4.0, "retention": 3.0, "reach": 0.001, "likes": 0.05}
# T4 (honest lift): a weight at/above this is a PRIMARY signal (saves/shares/retention). When a primary
# key is ABSENT from a metrics row — e.g. Postiz cannot deliver saves/retention — the lift_score is a
# PARTIAL objective; record_metrics stamps lift_degraded so the operator sees it instead of trusting a
# reach/shares-dominated scalar. reach (0.001) / likes (0.05) are low-weight proxies, never "missing".
_HIGH_WEIGHT = 1.0

# Platform CAPABILITY: which lift keys a platform's analytics can STRUCTURALLY deliver — the ONE place
# this knowledge lives (MOL-16/17 audit 2026-07-02). Derived from the three read maps: IG reads the Meta
# Graph (meta_graph._MEDIA_METRICS/_GRAPH_INSIGHTS_MAP) which yields reach/views/saves/shares/likes/
# comments AND avg_watch_time -> a DERIVED `retention` (REELS only, and only when the clip duration is
# known); TikTok reads Zernio (post/metrics._ZERNIO_LABEL_MAP) and youtube/facebook/twitter publish via
# Postiz (post/metrics._POSTIZ_LABEL_MAP) — NEITHER map has a watch-time/retention field, so retention is
# absent BY CONSTRUCTION on every non-IG platform. Keyed to Platform (not "not TikTok") so youtube — a
# third Platform via Postiz with retention equally unavailable — is exempted too. A metric a platform
# CANNOT produce is NOT a REQUIRED primary when proving THAT platform's shape, and is NOT a "missing"
# degraded key for it. Reach-only / likes-only noise still fails everywhere (the proof floor is
# capability-independent). Stale-map guard: risks table — a mapped-available metric that stops appearing
# is the failure mode; keep this in lockstep with the three maps.
_PLATFORM_METRICS: dict[Platform, frozenset[str]] = {
    Platform.instagram: frozenset({"reach", "views", "saves", "shares", "likes", "comments", "retention"}),
    Platform.tiktok:    frozenset({"reach", "views", "saves", "shares", "likes", "comments"}),   # Zernio: no watch-time
    Platform.youtube:   frozenset({"reach", "views", "saves", "shares", "likes", "comments"}),   # via Postiz: no retention
    Platform.facebook:  frozenset({"reach", "views", "saves", "shares", "likes", "comments"}),   # via Postiz: no retention
    Platform.twitter:   frozenset({"reach", "views", "saves", "shares", "likes", "comments"}),   # via Postiz: no retention
}

def _platform_delivers(platform: Optional[Platform], key: str) -> bool:
    # True unless `platform` is a KNOWN Platform whose capability set provably EXCLUDES `key`. Fail-OPEN:
    # an unknown / None platform (a legacy or platform-less row) delivers everything, so it proves and
    # degrades EXACTLY as before the capability model — the model only ever WIDENS what a known-limited
    # platform may skip, never tightens the platform-less path.
    caps = _PLATFORM_METRICS.get(platform)
    return caps is None or key in caps

ListPosts = Callable[[str], list[dict]]

def _shape_proves_learning(metrics: dict, *, weights: Optional[dict] = None,
                           platform: Optional[Platform] = None, require_ig_retention: bool = False) -> bool:
    """True when a live analyzed row proves the metric field-shape for learning unfreeze. Broader than
    `not lift_degraded`: Postiz never delivers `retention`, so a Postiz-shaped row stays lift_degraded
    yet proves the shape once `reach` + a primary engagement key (saves|shares) reconcile — mirroring
    learn_doctor's reach gate, not an all-_W verdict. Still fails closed on present-but-null primaries
    (D1) and on reach-only noise (likes+reach with no saves/shares). A full primary set (Postiz-shaped)
    always proves. MOL-17: `platform` names the row's Platform so a metric the platform CANNOT deliver
    (retention on TikTok/youtube via _PLATFORM_METRICS) is not counted a missing primary. MOL-18c:
    `require_ig_retention` (default OFF, caller-gated on cfg.ig_retention_proof) tightens ONLY a platform
    that CAN deliver retention (IG) to require it present-numeric — fail-OPEN for platform None/unknown or
    a platform that structurally can't (prove exactly as today)."""
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
    if require_ig_retention and _platform_delivers(platform, "retention") and platform is not None:
        r = metrics.get("retention")                        # MOL-18c: IG must show retention to prove (flag ON)
        if not (isinstance(r, (int, float)) and not isinstance(r, bool)):
            return False                                    # a retention-capable platform without it -> unproven
    if not _missing_high_weight(metrics, weights, platform):
        return True                                         # full primary set (platform-available keys present)
    has_reach = isinstance(metrics.get("reach"), (int, float)) and not isinstance(metrics.get("reach"), bool)
    has_eng = any(isinstance(metrics.get(k), (int, float)) and not isinstance(metrics.get(k), bool)
                 for k in ("saves", "shares"))
    if has_reach and has_eng:
        return True                                         # Postiz-shaped proof
    if isinstance(metrics.get("saves"), (int, float)) and not isinstance(metrics.get("saves"), bool):
        return True                                         # Zernio-shaped: saves lands without reach
    return False

# MOL-84 half 1 (13e-2=A): keys to CARRY FORWARD from a prior snapshot when a poll cycle DROPS them. The
# high-weight primaries (saves/shares/retention) were already protected via _missing_high_weight, but reach
# (weight 0.001) sat BELOW the _HIGH_WEIGHT floor, so it was silently EXEMPTED from carry-forward — a
# dropped reach regressed the stored snapshot to a reach-less row and aggregate_by_dim then read reach as a
# literal 0.0, corrupting the p4_dim_bias/timing_bias ranking that ranks EXCLUSIVELY on reach_mean. Removing
# that exemption: reach joins the always-carry set uniformly with the primaries. This is a CARRY concern
# ONLY — it does NOT touch _missing_high_weight, so the degraded marker and the learning-proof shape gate
# (_shape_proves_learning) are byte-identical: reach stays a low-weight proxy there, never a "missing"
# primary, never a proof requirement. Platform-aware like the primaries: a platform that structurally
# cannot emit reach never carries it (fail-OPEN for platform None/unknown — carries as before).
def _carry_forward_keys(metrics: dict, weights: Optional[dict], platform: Optional[Platform] = None) -> list[str]:
    keys = set(_missing_high_weight(metrics, weights, platform))
    if _platform_delivers(platform, "reach") and metrics.get("reach") is None:
        keys.add("reach")                                  # reach: no longer exempt from carry-forward
    return sorted(keys)

def _missing_high_weight(metrics: dict, weights: Optional[dict], platform: Optional[Platform] = None) -> list[str]:
    """The ACTIVE high-weight keys absent from this row (sorted). Judged against the ACTIVE weight map
    (a tuning override REPLACES _W), so 'degraded' tracks whatever objective is configured. NEVER
    recalibrates _W — purely observational (audit H3). D1: a key PRESENT-BUT-NULL counts as missing —
    lift_score drops a non-numeric value (isinstance guard) so a null saves contributes nothing, exactly
    like an absent saves; treating it as present would stamp a partial objective 'complete' and could
    auto-unfreeze learning on an unproven shape. MOL-18b: a high-weight key the row's PLATFORM cannot
    structurally deliver (retention on TikTok/youtube/facebook/twitter) is NOT counted missing — else the
    lift_degraded marker stays PERMANENT for a metric the platform can never emit. Fail-OPEN: platform
    None/unknown counts every absent key exactly as before (the platform-less path is byte-identical)."""
    w = _W if weights is None else weights
    return sorted(k for k, wt in w.items()
                  if isinstance(wt, (int, float)) and not isinstance(wt, bool) and wt >= _HIGH_WEIGHT
                  and _platform_delivers(platform, k)   # skip a key this platform structurally can't emit
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
    recovered = {k: prior_metrics[k] for k in _carry_forward_keys(metrics, weights, post.platform)
                 if prior_metrics.get(k) is not None}     # MOL-84: reach now carried like the primaries
    merged = {**metrics, **recovered}
    post.metrics = {**merged, LIFT_SCORE: lift_score(merged, weights)}
    # T4: ADDITIVE honest-lift marker (NOT a scoring change — lift_score is untouched). When a primary
    # weighted metric is absent from the MERGED row, the objective is partial; surface it so the operator
    # does not trust a degraded lift as a full one. Marker keys are not weights, so a later lift_score
    # ignores them. Absent any missing primary key -> no marker -> byte-identical to today.
    missing = _missing_high_weight(merged, weights, post.platform)   # MOL-18b: platform-aware, not permanent
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


def pull_imported_insights(led: Ledger, cfg: Config, *, get=None,
                           now: Optional[datetime] = None) -> Ledger:
    """ledger-rebuild M3: fill metrics for every ImportedMedia row (a live-only IG post) by its media_id,
    via the SOLE-SOURCE Graph media-insights read. Mirrors the Post metrics path — merge (carrying forward a
    dropped primary key so a partial pull never regresses the snapshot) + lift_score + the append-only
    metrics_series row. CONSUMES the empty-metric guard: meta_graph.media_insights DERIVES the request from
    product_type and refuses PRE-FLIGHT (returns None, NO HTTP, NO scope-block) when the type is
    unresolved/None — so an ImportedMedia with unknown product_type NEVER builds an empty `metric=` request
    (the row stays re-resolvable). FAIL-OPEN per row: a None (transient: no creds / 5xx / unresolved type)
    preserves the prior metrics, never crashes; a LOUD scope refusal raises out of media_insights (the one
    external gate), same as the Post path. Injectable `get` for hermetic tests."""
    from fanops import meta_graph
    now = now or datetime.now(timezone.utc)
    weights = cfg.tuning().get("lift_weights")
    log = get_logger(cfg)
    for mid, im in list(led.imported_media.items()):
        # per-account creds (the per-handle-creds gap): read this media's insights with ITS handle's token,
        # so a live-only media under a non-global handle is measured with the RIGHT creds. im.account is the
        # enumerating handle; a global-scope label (an ig id, no matching account) resolves to the global
        # creds — byte-identical to before.
        creds = meta_graph.resolve_meta_creds(cfg, handle=im.account)
        vals = meta_graph.media_insights(cfg, mid, im.product_type, get=get, creds=creds)   # None on transient/unresolved; refuses empty-metric pre-flight
        if not vals:
            continue                                             # transient / unresolved product_type -> preserve prior, re-poll next pass
        prior_metrics = {k: v for k, v in (im.metrics or {}).items()
                         if k not in (LIFT_SCORE, "lift_degraded", "lift_missing_keys")}
        # MOL-84 half 2 (13e-1=YES): thread the platform into the weighting calls EXACTLY as record_metrics
        # threads post.platform. This path is IG-only by construction (its sole caller enumerates
        # credentialed IG handles via Graph media), so Platform.instagram is the row's true platform — the
        # capability model then applies here as it does on the Post path, not the fail-open platform-None
        # default that silently skipped it. reach carry-forward (half 1) rides the same _carry_forward_keys.
        recovered = {k: prior_metrics[k] for k in _carry_forward_keys(vals, weights, Platform.instagram)
                     if prior_metrics.get(k) is not None}         # carry a dropped primary/reach key forward (no regression)
        merged = {**vals, **recovered}
        new_metrics = {**merged, LIFT_SCORE: lift_score(merged, weights)}
        missing = _missing_high_weight(merged, weights, Platform.instagram)
        if missing:
            new_metrics["lift_degraded"] = True; new_metrics["lift_missing_keys"] = missing
        # append-only series: one SPARSE row per pull, keyed by a monotonic offset (ImportedMedia has no
        # published_at cadence — the count-of-rows is a stable, never-duplicated tick).
        offset = f"pull{len(im.metrics_series)}"
        series = [*im.metrics_series, {**new_metrics, "offset": offset, "captured_at": iso_z(now)}]
        led.imported_media[mid] = im.model_copy(update={"metrics": new_metrics, "metrics_series": series})
        log("imported_insights", mid, "filled", reach=new_metrics.get("reach"), degraded=bool(missing))
    return led


def _metrics_client_for(cfg: Config, backend: str, submission_ids: Optional[list[str]]) -> ListPosts:
    # One backend's metrics fetcher. postiz/zernio read PER-POST analytics (need the published ids).
    # An unknown backend FAILS CLOSED + legibly (mirrors #251-#263): a stale FANOPS_POSTER already
    # degrades to dryrun at cfg (W4), so an unrecognized backend reaching here is a real routing bug,
    # not a silent fallback. Lazy imports keep requests/postiz/zernio off the dryrun/core path.
    if backend == "postiz":
        from fanops.post.metrics import PostizMetricsClient
        return PostizMetricsClient(cfg, submission_ids=submission_ids).list_posts
    if backend == "zernio":
        from fanops.post.metrics import ZernioMetricsClient
        return ZernioMetricsClient(cfg, submission_ids=submission_ids).list_posts
    raise ValueError(f"unknown backend {backend!r}: no metrics client (expected postiz/zernio)")

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
    from fanops.models import Platform
    accounts, err = load_accounts_safe(cfg)
    if err: get_logger(cfg)("backend_route", "accounts", "load_failed_global_fallback", err=err)
    # Leg 2 (Insight): Instagram metrics come from Meta Graph (the SOLE IG source) regardless of the
    # PUBLISH backend (Postiz publishes IG, but Graph MEASURES it). Split IG posts to GraphInsightsClient
    # (it needs the Post objects for media_id + cut_seconds), leave every non-IG post on its provider's
    # reader UNCHANGED (TikTok -> Zernio). Both fetchers' rows concat into ONE pass.
    ig_posts = [p for p in posts if p.platform is Platform.instagram and p.submission_id]
    groups: dict[str, list[str]] = {}
    for p in posts:
        if p.platform is Platform.instagram: continue                  # Graph owns IG metrics now
        if not p.submission_id: continue
        backend = accounts.effective_provider(p.account, p.platform)   # H1: per-channel provider, NOT the global fallback
        if backend is None: continue                                   # no provider -> don't dryrun-default a live post's metrics
        groups.setdefault(backend, []).append(p.submission_id)
    fetchers = [_metrics_client_for(cfg, b, ids) for b, ids in groups.items()]
    graph = None
    if ig_posts:
        from fanops.post.metrics import GraphInsightsClient
        graph = GraphInsightsClient(cfg, posts=ig_posts)
    def fetch(window: str = "30d") -> list[dict]:
        rows: list[dict] = []
        if graph is not None:
            rows.extend(graph.list_posts(window))
        for f in fetchers:
            rows.extend(f(window))
        return rows
    return fetch

def pull_metrics(led: Ledger, cfg: Config, *, list_posts: Optional[ListPosts] = None,
                 window: str = "30d", now: Optional[datetime] = None,
                 resolve_media: Optional[Callable[[Ledger, Config], object]] = None) -> Ledger:
    # Clock injected (tests pass `now`; real callers default to UTC now — mirrors approve_post's
    # now_iso). The fetch id-set + match-set are PUBLISHED OR ANALYZED (P3): an analyzed post stays
    # re-pollable so its series accumulates later cadence offsets. due_offset returns None once a post's
    # series is complete (or the post predates published_at), so a finished/timeline-less post is still
    # fetched + flipped/updated but records no new row. Inert id-thread for any non-postiz backend (ignored).
    now = now or datetime.now(timezone.utc)
    # Leg 2 (Insight): resolve each new IG post's Graph media_id AS PART of the automatic pull so the
    # unattended daemon self-resolves — the sole-source insights read keys on media_id. FAIL-OPEN: a resolve
    # failure (creds/transport) must never block the metrics pull (resolve_media_ids itself returns [] silently
    # when it can't enumerate). Injectable for hermetic tests; default = the real reconcile resolver.
    if resolve_media is None:
        from fanops.reconcile import resolve_media_ids as resolve_media
    try:
        resolve_media(led, cfg)
    except Exception as exc:
        get_logger(cfg)("track", "resolve_media", "error", err=str(exc)[:160])   # fail-open, breadcrumb
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
    require_ig_ret = cfg.ig_retention_proof                       # MOL-18c: default OFF; fail-open per-row inside the proof
    analyzed = [p for p in led.posts.values() if p.state is PostState.analyzed and LIFT_SCORE in p.metrics]
    proven = next((p for p in analyzed if _shape_proves_learning(
        p.metrics, weights=weights, platform=p.platform, require_ig_retention=require_ig_ret)), None)
    if proven is None:                                           # XC-4: name WHY learning stays frozen, per branch
        if not analyzed:
            log("learning", "auto_validate", "frozen_no_analyzed_metric")    # no analyzed row yet (waiting on a pull)
        else:
            log("learning", "auto_validate", "frozen_all_degraded", n=len(analyzed))   # rows exist, all missing a primary key
        return
    from fanops import cutover
    cutover._save_state(cfg, {"metrics_confirmed": True, "metrics_confirmed_auto": True})  # real data proved it
    log("learning", "auto_validate", "unfrozen_on_real_metric", post=proven.id)   # the proof landed
