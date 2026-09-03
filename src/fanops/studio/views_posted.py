"""Posted-library read-models for the Studio: shipped/in-flight/failed rows, delivery classification,
failure bucketing, lift/learning views, and metric breakdown helpers. Pure (no HTTP/Flask)."""
from __future__ import annotations
import json
import logging
import statistics
from dataclasses import dataclass, replace
from typing import Optional

from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.models import LIFT_SCORE, PostState
from fanops.timeutil import parse_iso
from fanops.variant_learning import _hook_for_post
from fanops.studio.views_common import _batch_title, clip_source_of

logger = logging.getLogger(__name__)

_EXPOSURE_STATES = frozenset({PostState.awaiting_approval, PostState.queued, PostState.published, PostState.analyzed, PostState.needs_reconcile, PostState.submitting, PostState.submitted})


@dataclass
class PostedRow:
    post_id: str
    clip_id: str
    account: str
    platform: str
    caption: str
    public_url: Optional[str]
    scheduled_time: Optional[str]
    lift_score: Optional[float]
    published_at: Optional[str] = None   # content-lifecycle Phase 3: the TRUE publish time; group_posted_by_day
                                         # keys on this (falls back to scheduled_time for pre-v3/in-flight rows).
    saves: Optional[float] = None        # P5: the raw whitelisted metric breakdown (track._W keys) for this
    shares: Optional[float] = None       # account's curve, read from post.metrics (the LATEST snapshot — NOT
    retention: Optional[float] = None    # metrics_series, which is P3's concern). Absent key -> None -> "—".
    reach: Optional[float] = None
    batch_id: Optional[str] = None       # Face 5: denormalized Post.batch_id (None == ungrouped)
    batch_title: Optional[str] = None    # Batch.name via led.get_batch (None when unbatched/dangling)
    variant_hook: Optional[str] = None   # Render foundation: the per-account on-screen hook (mirror of
                                         # Render.hook_text) so lift can be traced back to WHICH hook shipped
    # S6 lineage: additive, default-None, stamped by lineage_stats AFTER the rows are built (no extra I/O).
    sibling_count: Optional[int] = None  # how many shipped rows share this clip_id (the repost/crosspost lineage)
    rank: Optional[int] = None           # competition rank by lift within the lineage (1 = winner; ties share 1)
    delta_vs_best: Optional[float] = None  # lift_score - best-sibling lift (0.0 for the winner; negative otherwise)
    # M5: the delivery CHANNEL this row actually shipped through — derived from public_url, NEVER
    # from cfg.is_live (a row stamped published under dryrun must keep its 'dryrun' label even
    # after the operator flips live). Values: 'live' (https://... real provider permalink, only
    # reconcile.py writes these) | 'dryrun' (any non-http public_url — post dryrun-boundary a dryrun
    # post carries no url at all, and a legacy 'dryrun://' still reads as 'dryrun'). Pins the
    # operator's verbatim complaint: 'the system says posted when nothing is posted'.
    posted_via: str = "dryrun"
    submission_id: Optional[str] = None   # inflight rows: backend id awaiting permalink
    error_reason: Optional[str] = None      # inflight/failed: last reconcile error (truncated in UI)
    raw_state: Optional[str] = None         # ledger PostState.value for detail rows
    failure_kind: Optional[str] = None      # failed rows: rate_limit | oversize | bad_payload | transient | unknown
    is_archived: bool = False               # R2: row from 06_published/ supplement (read-only, no repost/crosspost)
    inflight_headline: str = ""


_FAILURE_KINDS = ("rate_limit", "oversize", "bad_payload", "transient", "unknown")
_RETRYABLE_FAILURES = frozenset({"rate_limit", "oversize", "bad_payload", "transient", "unknown"})


_FAILURE_LABELS = {"rate_limit": "Rate limited", "oversize": "Too large", "bad_payload": "Bad upload",
                   "transient": "Network blip", "unknown": "Failed"}


def failure_label(kind: str | None) -> str:
    return _FAILURE_LABELS.get(kind or "", "Failed")


def operator_error(msg: str | None, *, kind: str | None = None) -> str:
    """Plain-language error for Studio surfaces — no backend names, ids, or status dumps.

    Failure bucketing is NOT done here (MOL-781): pass `kind=` from Post.error_kind / classify_failure.
    Without kind, this is display-only cleanup of raw prose (strip backend tags, truncate)."""
    if kind:
        return failure_label(kind)
    if not msg:
        return ""
    clean = msg.strip()
    low = clean.lower()
    if "published_no_url" in low:
        return "Published — waiting for link."
    if "not live" in low or "dryrun" in low:
        return "Publishing is off until you go live."
    for tag in ("postiz", "zernio"):
        if low.startswith(tag + " "):
            rest = clean.split(None, 1)[-1] if " " in clean else ""
            if rest[:3].isdigit():
                tail = rest.split(None, 1)[-1] if " " in rest else ""
                return operator_error(tail) if tail else "Platform error."
            return operator_error(rest) if rest else "Platform error."
    return (clean[:97] + "…") if len(clean) > 100 else clean



def classify_failure(post) -> str:
    """Bucket a failed/error post from its typed error_kind (MOL-781). Untyped rows → unknown."""
    from fanops.models import ErrorKind
    kind = getattr(post, "error_kind", None)
    if kind is None:
        return "unknown"
    val = kind.value if isinstance(kind, ErrorKind) else str(kind)
    return val if val in _FAILURE_KINDS else "unknown"


def failure_rollup(led: Ledger, *, account: Optional[str] = None, batch: Optional[str] = None,
                   source: Optional[str] = None) -> dict:
    """Read-only counts of failed/error posts by classify_failure bucket.

    T2.7: the scope kwargs mirror `posted_library`'s account/batch/source filters EXACTLY (same fields, same
    comparisons) so the Posted page's failure chips count the SAME rows the library beside them lists — the
    rollup was whole-ledger next to a filtered list, an unlabelled global posing as the page's total. All three
    default None -> whole-ledger, so a kwarg-less call is byte-identical (home status, delivery_audit)."""
    buckets = {k: 0 for k in _FAILURE_KINDS}
    for p in led.posts.values():
        if p.state not in (PostState.failed, PostState.error):
            continue
        if account is not None and p.account != account:
            continue
        if batch is not None and p.batch_id != batch:
            continue
        if source is not None and clip_source_of(led, p.parent_id) != source:
            continue
        buckets[classify_failure(p)] += 1
    return {"total": sum(buckets.values()), "buckets": buckets}


def delivery_audit(led: Ledger) -> dict:
    """Read-only ops snapshot: live trackable, inflight, queued, failed bucket counts."""
    inflight = sum(1 for p in led.posts.values()
                   if p.state in (PostState.needs_reconcile, PostState.submitting, PostState.submitted))
    live = sum(1 for p in led.posts.values()
               if p.state in (PostState.published, PostState.analyzed)
               and _classify_channel(getattr(p, "public_url", None)) == "live")
    queued = len(led.posts_in_state(PostState.queued))
    roll = failure_rollup(led)
    return {"live_trackable": live, "inflight": inflight, "queued": queued,
            "failed": roll["total"], "buckets": roll["buckets"]}


def classify_post_delivery(post) -> str:
    """Unified delivery label for Schedule, Posted, Home, spine: live | inflight | dryrun | failed |
    queued | awaiting. Maps 1:1 to ledger + backend reality — never 'published' when nothing shipped."""
    st = post.state if isinstance(post.state, PostState) else PostState(post.state)
    if st is PostState.awaiting_approval:
        return "awaiting"
    if st in (PostState.failed, PostState.error):
        return "failed"
    if st in (PostState.needs_reconcile, PostState.submitting, PostState.submitted):
        return "inflight"
    if st is PostState.queued:
        return "queued"
    if st in (PostState.published, PostState.analyzed, PostState.retired):
        return "live" if _classify_channel(getattr(post, "public_url", None)) == "live" else "dryrun"
    return "queued"


def _classify_channel(public_url: Optional[str]) -> str:
    """Return the delivery channel for a published row: 'live' for an https/http permalink (only
    reconcile.py from a real provider writes these), else 'dryrun'. Pure — no I/O, deterministic on
    the post's on-disk state. An empty/unrecognized public_url classifies as 'dryrun' (the fail-safe
    default): post dryrun-boundary a dryrun post carries NO public_url, and a legacy 'dryrun://' value
    still reads as 'dryrun' through this same fall-through — so the Posted chip is unchanged."""
    if not public_url:
        return "dryrun"
    if public_url.strip().lower().startswith(("https://", "http://")):
        return "live"
    return "dryrun"   # empty / dryrun:// / any non-http scheme is NOT a live URL — fail safe to dryrun


def posted_library(led: Ledger, cfg: Config, *, account: Optional[str] = None, batch: Optional[str] = None,
                   delivery: Optional[str] = None, failure_kind: Optional[str] = None,
                   source: Optional[str] = None) -> list[PostedRow]:
    """The Posted library: shipped + in-flight + failed rows, filterable by delivery class (live /
    inflight / dryrun / failed). Default (delivery=None) shows terminal shipped rows only — inflight and
    failed are opt-in via the tab filters. Lock-free read."""
    from fanops.studio.views_schedule import inflight_headline
    if delivery == "inflight":
        posts = [p for p in led.posts.values()
                 if p.state in (PostState.needs_reconcile, PostState.submitting, PostState.submitted)]
    elif delivery == "failed":
        posts = [p for p in led.posts.values() if p.state in (PostState.failed, PostState.error)]
    elif delivery in ("live", "dryrun"):
        posts = [p for p in led.posts.values()
                 if p.state in (PostState.published, PostState.analyzed)
                 and classify_post_delivery(p) == delivery]
    elif delivery == "all":
        posts = [p for p in led.posts.values()
                 if p.state in (PostState.published, PostState.analyzed, PostState.needs_reconcile,
                                PostState.submitting, PostState.submitted, PostState.failed, PostState.error)]
    else:
        posts = [p for p in led.posts.values() if p.state in (PostState.published, PostState.analyzed)]
    if account is not None:
        posts = [p for p in posts if p.account == account]
    if batch is not None:          # Face 5: per-batch filter
        posts = [p for p in posts if p.batch_id == batch]
    if failure_kind:
        posts = [p for p in posts if classify_failure(p) == failure_kind]
    if source is not None:
        posts = [p for p in posts if clip_source_of(led, p.parent_id) == source]
    def _key(p):
        if not p.scheduled_time: return (0, "")
        try:
            dt = parse_iso(p.scheduled_time)
            return (1, dt.isoformat()) if dt.tzinfo is not None else (0, "")
        except (ValueError, TypeError): return (0, "")
    posts.sort(key=_key, reverse=True)              # reverse: latest aware time first; unscheduled (key[0]=0) last
    return [PostedRow(post_id=p.id, clip_id=p.parent_id, account=p.account, platform=p.platform.value,
                      caption=p.caption, public_url=p.public_url, scheduled_time=p.scheduled_time,
                      lift_score=p.metrics.get(LIFT_SCORE), published_at=p.published_at,
                      saves=p.metrics.get("saves"), shares=p.metrics.get("shares"),
                      retention=p.metrics.get("retention"), reach=p.metrics.get("reach"),
                      batch_id=p.batch_id, batch_title=_batch_title(led, p.batch_id),
                      variant_hook=_hook_for_post(led, p) or None,
                      posted_via=classify_post_delivery(p), submission_id=p.submission_id,
                      error_reason=(p.error_reason or "")[:120] or None, raw_state=p.state.value,
                      failure_kind=classify_failure(p) if p.state in (PostState.failed, PostState.error) else None,
                      inflight_headline=inflight_headline(p)) for p in posts]


def posted_archive_rows(cfg: Config, *, ledger_ids: set[str] | None = None) -> list[PostedRow]:
    """Read-only supplement: day-bucketed 06_published/*.json records not already in the ledger. FAIL-OPEN."""
    skip = ledger_ids or set()
    out: list[PostedRow] = []
    root = cfg.published
    try:
        if not root.is_dir(): return []
        paths = sorted(root.rglob("*.json"))
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("posted_archive", "-", "glob_error", err=str(exc)[:160])
        return []
    for ap in paths:
        try:
            rec = json.loads(ap.read_text(encoding="utf-8"))
        except Exception as exc:
            from fanops.log import get_logger
            get_logger(cfg)("posted_archive", "-", "parse_error", path=str(ap)[-80:], err=str(exc)[:120])
            continue
        pid = rec.get("post_id") or ap.stem
        if pid in skip: continue
        url = rec.get("public_url")
        out.append(PostedRow(post_id=pid, clip_id=rec.get("clip_id") or "", account=rec.get("account") or "",
                             platform=rec.get("platform") or "", caption=rec.get("caption") or "",
                             public_url=url, scheduled_time=rec.get("scheduled_time"),
                             lift_score=None, published_at=rec.get("published_at"),
                             posted_via=_classify_channel(url), is_archived=True))
    return out


def posted_batch_rollup(rows) -> Optional[dict]:
    """Read-only per-batch summary over the already-built PostedRow list (zero extra I/O, no metrics_series,
    no write, no learning unfreeze): {posted, with_lift, mean_lift}. mean_lift is over rows that CARRY a
    lift_score (None when none do -> renders '—'); never fabricates. None for an empty list."""
    if not rows: return None
    lifts = [r.lift_score for r in rows if r.lift_score is not None]
    return {"posted": len(rows), "with_lift": len(lifts),
            "mean_lift": (sum(lifts) / len(lifts)) if lifts else None}


_BAR_METRICS = ("saves", "shares", "retention", "reach")


def lineage_stats(rows) -> list:
    """S6 — return a NEW list of rows (PostedRow/LiftRow) annotated with sibling_count / rank / delta_vs_best
    so the operator reads 'this hook BEAT that hook'. Never mutates the caller's rows: an annotated row is a
    dataclasses.replace copy; a skipped row passes through as the same object. Groups by clip_id (the durable
    key a repost/crosspost shares with its origin) and ranks by lift_score desc within the group (COMPETITION
    ranking — tied bests both read rank 1). A falsy clip_id is skipped (no join key -> passed through). An
    unmeasured sibling (lift None) still counts toward sibling_count but keeps rank/delta None (can't rank
    what wasn't measured). Pure over the already-built list — NO ledger read, reads ONLY clip_id+lift (so it
    is per-account hook rendering-independent: a shared clip across accounts is a real lineage in either mode).
    Fail-open: any error returns the input rows unchanged (additive fields stay at their None defaults).
    Ranks within whatever filtered set is passed in. Same order and length as the input."""
    try:
        groups: dict = {}
        for r in rows:
            cid = getattr(r, "clip_id", None)
            if cid: groups.setdefault(cid, []).append(r)
        ann: dict = {}                                   # id(row) -> the fields to stamp on its copy
        for sibs in groups.values():
            n = len(sibs)
            for r in sibs: ann[id(r)] = {"sibling_count": n}
            measured = [r for r in sibs if isinstance(getattr(r, "lift_score", None), (int, float))
                        and not isinstance(r.lift_score, bool)]
            if not measured: continue
            best = max(r.lift_score for r in measured)
            for r in measured:
                ann[id(r)].update(rank=1 + sum(1 for o in measured if o.lift_score > r.lift_score),
                                  delta_vs_best=round(r.lift_score - best, 4))
        return [replace(r, **ann[id(r)]) if id(r) in ann else r for r in rows]
    except Exception:
        logger.warning("lineage sibling-ranking skipped (fail-open, additive fields stay None)", exc_info=True)
        return rows


def account_median_deltas(rows) -> list:
    """T-15 — return a NEW list of rows annotated with delta_vs_account_median = round(lift_score - account_median, 4),
    so the operator reads 'this variant beat/trailed its ACCOUNT's typical lift'. Never mutates the caller's rows:
    an annotated row is a dataclasses.replace copy; an unstamped row passes through as the same object. This is a
    DIFFERENT statistic from lineage_stats' delta_vs_best (best-in-clip-lineage): here the baseline is
    statistics.median over the account's MEASURED lift scores. Groups by `account`; a group with <2 measured rows
    is degenerate (a median vs a single point) and left at None — mirroring lineage_stats' judgment of only
    ranking within `measured`. An unmeasured row (lift None/non-numeric) is excluded from the median AND never
    stamped. Pure over the already-built list — NO ledger read. Fail-open: any error returns the input rows
    unchanged (additive fields stay at their None defaults). Same order and length as the input."""
    try:
        groups: dict = {}
        for r in rows:
            acct = getattr(r, "account", None)
            if acct: groups.setdefault(acct, []).append(r)
        ann: dict = {}
        for grp in groups.values():
            measured = [r for r in grp if isinstance(getattr(r, "lift_score", None), (int, float))
                        and not isinstance(r.lift_score, bool)]
            if len(measured) < 2: continue     # a median vs a single data point is degenerate
            med = statistics.median(r.lift_score for r in measured)
            for r in measured:
                ann[id(r)] = {"delta_vs_account_median": round(r.lift_score - med, 4)}
        return [replace(r, **ann[id(r)]) if id(r) in ann else r for r in rows]
    except Exception as exc:
        logger.warning("delta_vs_account_median: stats pass failed (%s)", exc)
        return rows   # fail-open (mirrors lineage_stats): additive field stays at its None default, never a raise


def metric_peaks(rows) -> dict:
    """S6 — the column max of each breakdown metric (saves/shares/retention/reach) across the row list, so a
    per-row micro-bar can be drawn PROPORTIONAL to the visible peak. A metric absent on every row -> None (no
    bar). Pure, fail-open (non-numeric values are ignored, never raise)."""
    peaks: dict = {}
    for k in _BAR_METRICS:
        vals = [v for v in (getattr(r, k, None) for r in rows)
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
        peaks[k] = max(vals) if vals else None
    return peaks


def bar_pct(value, peak) -> int:
    """S6 — a 0..100 bar width for `value` against the column `peak` (from metric_peaks). 0 when either is
    missing or peak<=0; clamped to [0,100]. Fail-safe — never raises into a template."""
    try:
        if value is None or peak is None or peak <= 0: return 0
        return max(0, min(100, round(float(value) / float(peak) * 100)))
    except (TypeError, ValueError): return 0


def group_posted_by_day(rows: list, cfg=None) -> list:
    """Group Posted rows by PUBLISH day (published_at — the TRUE shipped day; falls back to scheduled_time for
    pre-v3/in-flight rows), newest day first, 'undated' last. Pure; preserves within-day order (content-
    lifecycle Phase 3). A naive/None/unparseable time -> 'undated' (never a local-tz guess). MOL-83: with cfg,
    the aware ts is converted to the operator zone (cfg.operator_tz, via the same _operator_zone helper
    publish_buckets uses) BEFORE .date() — so a 23:30Z post lands on the operator's calendar day. cfg omitted
    -> UTC day (unchanged)."""
    zone = None
    if cfg is not None:
        from fanops.timeutil import _operator_zone
        zone = _operator_zone(cfg)
    def _day(r) -> str:
        ts = getattr(r, "published_at", None) or r.scheduled_time
        if not ts: return "undated"
        try:
            dt = parse_iso(ts)
            if dt.tzinfo is None: return "undated"
            if zone is not None: dt = dt.astimezone(zone)
            return dt.date().isoformat()
        except (ValueError, TypeError): return "undated"
    by_day: dict[str, list] = {}
    for r in rows: by_day.setdefault(_day(r), []).append(r)
    days = sorted((d for d in by_day if d != "undated"), reverse=True)
    if "undated" in by_day: days.append("undated")
    return [(d, by_day[d]) for d in days]


def _loop_state(led: Ledger, cfg: Config, accounts: Optional[Accounts], post,
                cache: Optional[dict] = None) -> str:
    """Per-surface learning-loop annotation, reusing the digest's fail-open gate computation.
    `cache` memoises per (account, platform) across one request — without it every variant post
    re-ran the full posts scan inside the scorer (stage-6 audit: digest had the cache, Lift lost it)."""
    try:
        from fanops.digest import gate_state
        return gate_state(led, cfg, post.account, post.platform, cache, accounts=accounts)
    except Exception as exc:
        # ECC fix #5: was a SILENT fail-open — a broken gate_state (refactor/schema drift) looked
        # identical to "no data yet". Log ONE breadcrumb per request (dedup via the per-request cache)
        # so the operator can tell a real break from genuine emptiness, without per-post spam.
        if cache is None or not cache.get("_loop_state_logged"):
            from fanops.log import get_logger
            get_logger(cfg)("lift", "-", "loop_state_error", err=str(exc)[:160])
            if cache is not None: cache["_loop_state_logged"] = True
        return "gathering data"


@dataclass
class LiftRow:
    variant_hook: Optional[str]
    account: str
    platform: str
    lift_score: float
    loop_state: str
    amplify_state: Optional[str] = None
    lift_degraded: bool = False             # T4: the lift scalar is partial (a primary metric was absent from the row)
    lift_missing: Optional[list] = None     # which primary keys were missing (e.g. ["saves", "retention"])
    scheduled_time: Optional[str] = None    # P5: P1's operator-set time, shown as the Results 'When' column
    saves: Optional[float] = None           # P5: the raw whitelisted metric breakdown (track._W keys) from
    shares: Optional[float] = None          # post.metrics (LATEST snapshot — NOT metrics_series). Absent -> None.
    retention: Optional[float] = None
    reach: Optional[float] = None
    clip_id: Optional[str] = None           # S6: the parent clip — the join key lineage_stats groups variants on
    sibling_count: Optional[int] = None     # S6 lineage (see PostedRow): stamped by lineage_stats, additive/None.
    rank: Optional[int] = None
    delta_vs_best: Optional[float] = None
    delta_vs_account_median: Optional[float] = None   # T-15: Δ vs the ACCOUNT's median lift (additive to delta_vs_best,


@dataclass
class LiftView:
    variant_rows: list[LiftRow]
    variant_empty_reason: Optional[str]
    amplify_present: bool
    amplify_rows: list[LiftRow]
    amplify_empty_reason: Optional[str]
    # MOL-50: uniform DEGRADED is a TABLE-level fact, not a per-row one. When most rows are degraded the
    # badge stops being a signal and becomes red noise, drowning the Lift number it annotates. These
    # summary fields let the template surface it ONCE (a table-level note) + shrink the per-row badge to
    # a quiet marker; a MINORITY (<=50%) keeps the loud per-row badge as the exception-signal it is.
    degraded_count: int = 0
    degraded_total: int = 0
    degraded_mostly: bool = False


def lift_rows(led: Ledger, cfg: Config, accounts: Optional[Accounts] = None, *,
             account: Optional[str] = None) -> LiftView:
    """Per-hook lift: analyzed posts with a moment hook + lift_score, ranked desc."""
    posts_view = [p for p in led.posts.values() if account is None or p.account == account]
    variant_posts = [p for p in posts_view
                     if _hook_for_post(led, p) and p.state is PostState.analyzed and LIFT_SCORE in p.metrics]
    variant_rows: list[LiftRow] = []
    variant_empty_reason: Optional[str] = None
    if not variant_posts:
        any_analyzed = any(p.state is PostState.analyzed for p in posts_view)
        if not any_analyzed:
            variant_empty_reason = ("No results yet — connect Postiz (Go Live) so posts come back "
                                    "with analytics. (Needs a POSTIZ_API_KEY.)")
        else:
            variant_empty_reason = ("No analyzed posts with a burned hook and lift_score yet.")
    else:
        gate_cache: dict = {}                       # one scorer pass per surface per request
        for p in sorted(variant_posts, key=lambda p: p.metrics.get(LIFT_SCORE, 0.0), reverse=True):
            variant_rows.append(LiftRow(
                variant_hook=_hook_for_post(led, p), account=p.account,
                platform=p.platform.value, lift_score=float(p.metrics.get(LIFT_SCORE, 0.0)),
                loop_state=_loop_state(led, cfg, accounts, p, gate_cache),
                lift_degraded=bool(p.metrics.get("lift_degraded")),
                lift_missing=p.metrics.get("lift_missing_keys") or None,
                scheduled_time=p.scheduled_time, saves=p.metrics.get("saves"),
                shares=p.metrics.get("shares"), retention=p.metrics.get("retention"),
                reach=p.metrics.get("reach"), clip_id=p.parent_id))

    amplify_present = cfg.variant_amplify
    amplify_rows: list[LiftRow] = []
    amplify_empty_reason: Optional[str] = None
    if amplify_present:
        try:
            from fanops.variant_amplify import amplify_candidates
            cands = amplify_candidates(led, cfg)
            for c in cands:
                p = led.posts.get(c.get("post_id"))
                if p is None or (account is not None and p.account != account):    # P5: drop off-account candidates
                    continue
                amplify_rows.append(LiftRow(
                    variant_hook=c.get("winning_hook"), account=p.account,
                    platform=p.platform.value, lift_score=float(p.metrics.get(LIFT_SCORE, 0.0)),
                    loop_state="amplify candidate", amplify_state=str(c.get("evidence", "")),
                    scheduled_time=p.scheduled_time))     # When column for parity; breakdown out of scope (has evidence)
            if not amplify_rows:
                amplify_empty_reason = "No sustained amplification streaks yet."
        except Exception as exc:
            from fanops.log import get_logger     # ECC fix #5: log the real cause, not just "unavailable"
            get_logger(cfg)("lift", "-", "amplify_error", err=str(exc)[:160])
            amplify_empty_reason = "Amplify state unavailable (fail-open)."
    # MOL-50: fold the per-row degraded flags into a table-level summary. "Mostly" = strictly MORE than
    # half the shown rows are degraded (>50%) — the point past which the repeated badge is noise, not signal.
    deg_count = sum(1 for r in variant_rows if r.lift_degraded)
    deg_total = len(variant_rows)
    deg_mostly = deg_total > 0 and deg_count * 2 > deg_total
    return LiftView(variant_rows=variant_rows, variant_empty_reason=variant_empty_reason,
                    amplify_present=amplify_present, amplify_rows=amplify_rows,
                    amplify_empty_reason=amplify_empty_reason,
                    degraded_count=deg_count, degraded_total=deg_total, degraded_mostly=deg_mostly)


@dataclass
class DimInsightRow:
    dim: str                          # the stamped Post attribute (clip_profile | first_frame_kind | top_bias | publish_hour | publish_dow)
    label: str                        # "length" | "opening" | "framing" | "timing (hour)" | "timing (dow)"
    state: str                        # "frozen" | "collecting" | "ranked"
    progress: Optional[str] = None    # collecting only: "N of 8 attributed posts" (honest numerator)
    values: list = None               # ranked only: [(value, aggregate_by_dim row)] sorted reach_mean desc

    def __post_init__(self):
        if self.values is None:
            self.values = []


# U10: the "What's working" dims, in the panel's fixed order — the P4 creative three, then the two timing
# axes. Labels are the panel's OWN (the plan's DimInsightRow contract: length/opening/framing + timing
# hour/dow); a stamped dim reads getattr(p, dim) exactly as aggregate_by_dim / the actuators do.
_WHATS_WORKING_DIMS = (("clip_profile", "length"), ("first_frame_kind", "opening"),
                       ("top_bias", "framing"), ("publish_hour", "timing (hour)"),
                       ("publish_dow", "timing (dow)"))


def _fmt_dim_value(dim: str, value: str) -> str:
    """Render one dim value for the operator. top_bias is a bool dim (aggregate_by_dim keys it as the
    stringified bool) -> the natural phrasing p4_dim_bias uses; every other dim shows its raw value."""
    if dim == "top_bias":
        return "top-anchored" if value == "True" else "centered"
    return value


def whats_working_panel(led: Ledger, cfg: Config) -> list:
    """U10 — the gate-honest 'What's working' read-model for the Results page. Per creative/timing dim it
    reports one of three honest states, reusing ONLY the existing gate + aggregator (no new learning code,
    no second ranking path):
      frozen     — learning_validated(cfg) is False (no proven live-metric shape); nothing is ranked.
      collecting — validated but the dim is under the P4 signal threshold (p4_unlocked False); shows the
                   honest 'N of 8' progress from validation_gate.dim_collecting_progress.
      ranked     — validated AND unlocked; values are the FULL per-value reach ranking, i.e.
                   sorted(aggregate_by_dim(led, dim).items(), reach_mean desc). This is the ranking the
                   actuators read BEFORE the p4_min_reach_gap winner selection — the panel shows every
                   value's reach, not just the picked winner (so it never disagrees by hiding runners-up).
    Cross-account rollup (aggregate_by_dim spans accounts), matching the learning actuators. Pure read.
    FAIL-OPEN: any exception returns [] so the shipped library still renders."""
    try:
        from fanops.digest import aggregate_by_dim
        from fanops.validation_gate import learning_validated, p4_unlocked, dim_collecting_progress
        validated = learning_validated(cfg)
        out: list = []
        for dim, label in _WHATS_WORKING_DIMS:
            if not validated:
                out.append(DimInsightRow(dim=dim, label=label, state="frozen",
                                         progress="frozen until live metrics validate"))
                continue
            if not p4_unlocked(led, cfg, dim):
                n, need = dim_collecting_progress(led, dim)
                out.append(DimInsightRow(dim=dim, label=label, state="collecting",
                                         progress=f"{n} of {need} attributed posts"))
                continue
            ranked = sorted(aggregate_by_dim(led, dim).items(), key=lambda kv: -kv[1]["reach_mean"])
            out.append(DimInsightRow(dim=dim, label=label, state="ranked",
                                     values=[(_fmt_dim_value(dim, v), row) for v, row in ranked]))
        return out
    except Exception:
        logger.warning("whats_working panel degraded (fail-open, empty)", exc_info=True)
        return []
