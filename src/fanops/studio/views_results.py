"""Outcome read-models for the Studio: the approved-bucket Schedule (ScheduleRow + publish-readiness +
suggested-time rationale), the all-time Posted library (PostedRow + lineage stats + metric bars + day grouping)
and the cross-account Lift/learning view (LiftRow/LiftView). Pure (no HTTP/Flask). Depends on views_common for
the shared time/batch helpers; never on a sibling surface module (review/cockpit) — the import graph stays acyclic."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.models import LIFT_SCORE, PostState, RenderState
from fanops.timeutil import parse_iso
from fanops.studio.views_common import RECENT_WINDOW_HOURS, _batch_title, _imminent, suggest_time


@dataclass
class ScheduleRow:
    post_id: str
    scheduled_time: Optional[str]
    account: str
    platform: str
    clip_id: str
    state: str
    imminent: bool
    editable: bool
    integration_id: str = ""        # the Postiz channel this post will hit (post.account_id) — surfaced so
                                    # the operator sees WHICH integration each approved post publishes to.
    lane: str = ""                  # due | upcoming | inflight | recent — Schedule three-lane bucket
    delivery: str = ""              # classify_post_delivery — unified state-honesty label for badges
    submission_id: Optional[str] = None  # inflight: backend id the reconciler polls
    backend: str = ""               # per-channel effective provider (not the legacy global)
    error_reason: Optional[str] = None   # inflight/failed: last reconcile or publish error (truncated in UI)
    suggested_time: Optional[str] = None   # P1: ONE deterministic strictly-future suggestion (surface_time
                                           # index=0), set ONLY for editable rows; read-only past rows carry None.
    batch_id: Optional[str] = None         # Face 5: denormalized Post.batch_id (None == ungrouped)
    batch_title: Optional[str] = None      # Batch.name via led.get_batch (None when unbatched/dangling)
    caption: str = ""                      # P5: the post's caption, shown as a Schedule column so the
                                           # operator reads WHAT each scheduled row ships without opening it
    variant_hook: Optional[str] = None     # Render foundation: the per-account on-screen hook (mirror of
                                           # Render.hook_text) so the operator SEES which hook each account ships
    # S5: advisory publish-readiness + the suggested-time rationale, set ONLY on editable rows (read-only past
    # rows carry None). NEVER gates publish — a warn is information, the operator can still ship.
    ready: Optional[bool] = None           # True = the shippable artifact exists + coheres; False = a reason below
    ready_reason: Optional[str] = None     # WHY (e.g. "ready — its own cut" | "hook drift …" | "render not finished")
    why_suggested: Optional[str] = None    # one plain sentence explaining the suggested time (account/platform/lead)


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


@dataclass
class LiftView:
    variant_rows: list[LiftRow]
    variant_empty_reason: Optional[str]
    amplify_present: bool
    amplify_rows: list[LiftRow]
    amplify_empty_reason: Optional[str]


# non-terminal render states a shippable artifact can be in (mirrors crosspost._REUSABLE_CLIP_STATES philosophy;
# `queued` is currently dead but allowed so a future staged-render path can't trip a false warn — never `retired`).
_SHIPPABLE_RENDER = (RenderState.rendered, RenderState.queued, RenderState.published, RenderState.analyzed)

def publish_readiness(led: Ledger, post) -> tuple[bool, str]:
    """S5: ADVISORY (ready, reason) for a single post, from already-loaded objects — NEVER a ledger write, NEVER
    a publish gate. A post with a render ships that render: it must exist, be shippable, its file must be on disk,
    and its BURNED hook must match the hook the operator sees (else 'drift'). A post with no render ships the
    shared clip: it must exist, be in a reusable state (the SAME allowlist crosspost ships from — single source of
    truth), and have its file on disk. Fail-open: any torn/odd shape -> (False, 'unverified'), never raises."""
    try:
        rid = post.render_id
        if rid:
            r = led.renders.get(rid)
            if r is None: return (False, "render record missing")
            if r.state not in _SHIPPABLE_RENDER: return (False, "render not finished")
            if not (r.path and Path(r.path).exists()): return (False, "render file missing from disk")
            if (r.hook_text or "") != (post.variant_hook or ""):
                return (False, "hook drift — the burned hook differs from the one shown")
            return (True, "ready — its own cut")
        from fanops.crosspost import _REUSABLE_CLIP_STATES        # the EXACT states crosspost will reuse a clip from
        clip = led.clips.get(post.parent_id) if post.parent_id else None
        if clip is None: return (False, "source clip missing")
        if clip.state not in _REUSABLE_CLIP_STATES: return (False, f"clip not shippable ({clip.state.value})")
        if not (clip.path and Path(clip.path).exists()): return (False, "clip file missing from disk")
        return (True, "ready — shared clip")
    except Exception:
        return (False, "unverified")


def explain_suggested_time(cfg: Config, row) -> str:
    """S5: one plain sentence for WHY the suggested time is what it is — the suggestion is fully deterministic
    (suggest_time = the earliest strictly-future slot honoring the per-account/platform cadence + the lead
    window), but it was printed bare. Pure; names the account, platform, and lead so the operator trusts it."""
    lead = getattr(cfg, "publish_lead_minutes", 0)
    return (f"The earliest safe slot for {getattr(row, 'account', '?')} on {getattr(row, 'platform', '?')} — "
            f"a {lead}-minute lead from now, paced to its cadence so posts don't cluster.")


def schedule_rows(led: Ledger, cfg: Config, *, now: datetime,
                  account: Optional[str] = None, batch: Optional[str] = None) -> list[ScheduleRow]:
    """Approved-bucket rows in three lanes (due / upcoming / in-flight) plus optional recent shipped.
    In-flight (needs_reconcile, submitting, submitted) is NOW visible — the operator no longer has to
    open Posted or the CLI to see reconciling posts. P5: optional account/batch filters after sort."""
    recent_cutoff = now - timedelta(hours=RECENT_WINDOW_HOURS)
    accts = Accounts.load(cfg)
    rows: list[ScheduleRow] = []
    for p in led.posts.values():
        if p.state is PostState.queued:
            include = True
        elif p.state in (PostState.needs_reconcile, PostState.submitting, PostState.submitted):
            include = True
        elif p.state in (PostState.published, PostState.analyzed):
            include = True
            if p.scheduled_time:
                try:
                    dt = parse_iso(p.scheduled_time)
                    include = dt.tzinfo is not None and dt >= recent_cutoff
                except (ValueError, TypeError):
                    include = True
        else:
            include = False
        if not include:
            continue
        imm = _imminent(p.scheduled_time, now)
        state = p.state.value
        lane = _schedule_lane(p, now)
        editable = (p.state is PostState.queued and lane != "inflight" and not imm)
        try:
            backend = accts.effective_provider(p.account, p.platform) or cfg.poster_backend or "dryrun"
        except Exception:
            backend = cfg.poster_backend or "dryrun"
        row = ScheduleRow(
            post_id=p.id, scheduled_time=p.scheduled_time, account=p.account,
            platform=p.platform.value, clip_id=p.parent_id, state=state, imminent=imm,
            editable=editable, integration_id=p.account_id, lane=lane,
            delivery=classify_post_delivery(p), submission_id=p.submission_id,
            backend=backend, error_reason=(p.error_reason or "")[:120] or None,
            suggested_time=suggest_time(cfg, p, now=now) if editable else None,
            batch_id=p.batch_id, batch_title=_batch_title(led, p.batch_id),
            caption=p.caption, variant_hook=p.variant_hook)
        if editable:
            row.ready, row.ready_reason = publish_readiness(led, p)
            row.why_suggested = explain_suggested_time(cfg, row)
        rows.append(row)

    def _key(r: ScheduleRow):
        if r.lane == "inflight":
            return (0, r.post_id)
        if not r.scheduled_time:
            return (2, "")
        try:
            dt = parse_iso(r.scheduled_time)
            if dt.tzinfo is None:
                return (2, r.scheduled_time)
            return (1, dt.isoformat())
        except (ValueError, TypeError):
            return (2, r.scheduled_time)
    rows.sort(key=_key)
    if account is not None:
        rows = [r for r in rows if r.account == account]
    if batch is not None:
        rows = [r for r in rows if r.batch_id == batch]
    return rows


def _schedule_lane(p, now: datetime) -> str:
    """Bucket one post into due | upcoming | inflight | recent for the Schedule panel."""
    if p.state in (PostState.needs_reconcile, PostState.submitting, PostState.submitted):
        return "inflight"
    if p.state in (PostState.published, PostState.analyzed):
        return "recent"
    if p.state is PostState.queued:
        if not p.scheduled_time:
            return "due"
        try:
            return "due" if parse_iso(p.scheduled_time) <= now else "upcoming"
        except (ValueError, TypeError):
            return "due"
    return "upcoming"


@dataclass
class ScheduleLanes:
    due: list[ScheduleRow]
    upcoming: list[ScheduleRow]
    inflight: list[ScheduleRow]


def schedule_lanes(rows: list[ScheduleRow]) -> ScheduleLanes:
    """Split already-built ScheduleRows into the three operator-facing lanes (recent rows excluded)."""
    due, upcoming, inflight = [], [], []
    for r in rows:
        if r.lane == "inflight":
            inflight.append(r)
        elif r.lane == "due":
            due.append(r)
        elif r.lane == "upcoming":
            upcoming.append(r)
    return ScheduleLanes(due=due, upcoming=upcoming, inflight=inflight)


def group_schedule_by_account(rows: list) -> list:
    """Group already-time-sorted ScheduleRows by account for a running per-account header (P5, decision 2:
    Schedule is a per-post <table>, so a header sits cleanly above its rows). Pure; account-sorted headers,
    within-account TIME order preserved (the input arrives time-sorted). Mirrors group_posted_by_day."""
    by_acct: dict[str, list] = {}
    for r in rows: by_acct.setdefault(r.account, []).append(r)
    return [(a, by_acct[a]) for a in sorted(by_acct)]


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
    # reconcile.py writes these) | 'dryrun' (public_url is None OR has the dryrun:// scheme — the
    # DryRunPoster->publish_post transition never sets public_url). Pins the operator's verbatim
    # complaint: 'the system says posted when nothing is posted'.
    posted_via: str = "dryrun"
    submission_id: Optional[str] = None   # inflight rows: backend id awaiting permalink
    error_reason: Optional[str] = None      # inflight/failed: last reconcile error (truncated in UI)
    raw_state: Optional[str] = None         # ledger PostState.value for detail rows
    failure_kind: Optional[str] = None      # failed rows: rate_limit | oversize | bad_payload | poll_error | unknown


_FAILURE_KINDS = ("rate_limit", "oversize", "bad_payload", "poll_error", "unknown")
_RETRYABLE_FAILURES = frozenset({"rate_limit", "bad_payload", "unknown"})


def classify_failure(post) -> str:
    """Bucket a failed/error post's error_reason for the Posted recovery cockpit."""
    er = (getattr(post, "error_reason", None) or "").lower()
    if not er:
        return "unknown"
    if "429" in er or "rate limit" in er or "too many requests" in er:
        return "rate_limit"
    if "413" in er or "oversize" in er or "too large" in er or "entity too large" in er:
        return "oversize"
    if "poll error" in er or "reconcile poll" in er:
        return "poll_error"
    if "400" in er or "bad request" in er or "bad media" in er or "invalid" in er:
        return "bad_payload"
    return "unknown"


def failure_rollup(led: Ledger) -> dict:
    """Read-only counts of failed/error posts by classify_failure bucket."""
    buckets = {k: 0 for k in _FAILURE_KINDS}
    for p in led.posts.values():
        if p.state not in (PostState.failed, PostState.error):
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
    reconcile.py from a real provider writes these), else 'dryrun'. Pure — no I/O, deterministic
    on the post's on-disk state. NB: an empty public_url IS the dryrun signature today (the
    DryRunPoster->publish_post transition never sets public_url; only reconcile.py does, and only
    on a real provider response)."""
    if not public_url:
        return "dryrun"
    p = public_url.strip().lower()
    if p.startswith("dryrun://"):
        return "dryrun"
    if p.startswith(("https://", "http://")):
        return "live"
    return "dryrun"   # an unrecognized scheme is NOT a live URL — fail safe to dryrun


def posted_library(led: Ledger, cfg: Config, *, account: Optional[str] = None, batch: Optional[str] = None,
                   delivery: Optional[str] = None, failure_kind: Optional[str] = None) -> list[PostedRow]:
    """The Posted library: shipped + in-flight + failed rows, filterable by delivery class (live /
    inflight / dryrun / failed). Default (delivery=None) shows terminal shipped rows only — inflight and
    failed are opt-in via the tab filters. Lock-free read."""
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
                      variant_hook=p.variant_hook,
                      posted_via=classify_post_delivery(p), submission_id=p.submission_id,
                      error_reason=(p.error_reason or "")[:120] or None, raw_state=p.state.value,
                      failure_kind=classify_failure(p) if p.state in (PostState.failed, PostState.error) else None) for p in posts]


def posted_batch_rollup(rows) -> Optional[dict]:
    """Read-only per-batch summary over the already-built PostedRow list (zero extra I/O, no metrics_series,
    no write, no learning unfreeze): {posted, with_lift, mean_lift}. mean_lift is over rows that CARRY a
    lift_score (None when none do -> renders '—'); never fabricates. None for an empty list."""
    if not rows: return None
    lifts = [r.lift_score for r in rows if r.lift_score is not None]
    return {"posted": len(rows), "with_lift": len(lifts),
            "mean_lift": (sum(lifts) / len(lifts)) if lifts else None}


_BAR_METRICS = ("saves", "shares", "retention", "reach")


def lineage_stats(rows) -> None:
    """S6 — IN-PLACE annotate each row (PostedRow/LiftRow) with sibling_count / rank / delta_vs_best so the
    operator reads 'this hook BEAT that hook'. Groups by clip_id (the durable key a repost/crosspost shares
    with its origin) and ranks by lift_score desc within the group (COMPETITION ranking — tied bests both
    read rank 1). A falsy clip_id is skipped (no join key -> untouched). An unmeasured sibling (lift None)
    still counts toward sibling_count but keeps rank/delta None (can't rank what wasn't measured). Pure over
    the already-built list — NO ledger read, reads ONLY clip_id+lift (so it is FANOPS_CREATIVE_VARIATION-
    independent: a shared clip across accounts is a real lineage in either mode). Fail-open: any error leaves
    the additive fields at their None defaults. Ranks within whatever filtered set is passed in."""
    try:
        groups: dict = {}
        for r in rows:
            cid = getattr(r, "clip_id", None)
            if cid: groups.setdefault(cid, []).append(r)
        for sibs in groups.values():
            n = len(sibs)
            for r in sibs: r.sibling_count = n
            measured = [r for r in sibs if isinstance(getattr(r, "lift_score", None), (int, float))
                        and not isinstance(r.lift_score, bool)]
            if not measured: continue
            best = max(r.lift_score for r in measured)
            for r in measured:
                r.rank = 1 + sum(1 for o in measured if o.lift_score > r.lift_score)
                r.delta_vs_best = round(r.lift_score - best, 4)
    except Exception:
        pass


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


def group_posted_by_day(rows: list) -> list:
    """Group Posted rows by PUBLISH day (published_at — the TRUE shipped day; falls back to scheduled_time for
    pre-v3/in-flight rows), newest day first, 'undated' last. Pure; preserves within-day order (content-
    lifecycle Phase 3). A naive/None/unparseable time -> 'undated' (never a local-tz guess)."""
    def _day(r) -> str:
        ts = getattr(r, "published_at", None) or r.scheduled_time
        if not ts: return "undated"
        try:
            dt = parse_iso(ts)
            return dt.date().isoformat() if dt.tzinfo is not None else "undated"
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

def lift_rows(led: Ledger, cfg: Config, accounts: Optional[Accounts] = None, *,
             account: Optional[str] = None) -> LiftView:
    """Per-variant lift (spec §8): analyzed posts carrying a variant_key + lift_score, ranked desc.
    Honest, reason-bearing empty states per sub-view; amplify section mirrors digest's
    `if cfg.variant_amplify:` gate (absent, not blank, when off). P5: an optional `account` scopes the post
    universe (variant_posts AND the any_analyzed empty-reason probe) BEFORE the empty branch, so a
    filtered-to-empty view still gets an honest reason (R6); the amplify candidates are filtered by their
    resolved post's account too. Each variant row carries P1's scheduled_time + the P3 metric breakdown."""
    posts_view = [p for p in led.posts.values() if account is None or p.account == account]
    variant_posts = [p for p in posts_view
                     if p.variant_key and p.state is PostState.analyzed and LIFT_SCORE in p.metrics]
    variant_rows: list[LiftRow] = []
    variant_empty_reason: Optional[str] = None
    if not variant_posts:
        any_analyzed = any(p.state is PostState.analyzed for p in posts_view)
        if not any_analyzed:
            variant_empty_reason = ("No results yet — connect Postiz (Go Live) so posts come back "
                                    "with analytics. (Needs a POSTIZ_API_KEY, or a Blotato backend.)")
        else:
            variant_empty_reason = ("Creative variation (FANOPS_CREATIVE_VARIATION) was off when "
                                    "these posts were crossposted — no per-variant lift.")
    else:
        gate_cache: dict = {}                       # one scorer pass per surface per request
        for p in sorted(variant_posts, key=lambda p: p.metrics.get(LIFT_SCORE, 0.0), reverse=True):
            variant_rows.append(LiftRow(
                variant_hook=p.variant_hook or p.variant_key, account=p.account,
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
    return LiftView(variant_rows=variant_rows, variant_empty_reason=variant_empty_reason,
                    amplify_present=amplify_present, amplify_rows=amplify_rows,
                    amplify_empty_reason=amplify_empty_reason)
