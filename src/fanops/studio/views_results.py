"""Outcome read-models for the Studio: the approved-bucket Schedule (ScheduleRow + publish-readiness +
suggested-time rationale), the all-time Posted library (PostedRow + lineage stats + metric bars + day grouping)
and the cross-account Lift/learning view (LiftRow/LiftView). Pure (no HTTP/Flask). Depends on views_common for
the shared time/batch helpers; never on a sibling surface module (review/cockpit) — the import graph stays acyclic."""
from __future__ import annotations
import logging
import statistics
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.models import LIFT_SCORE, PostState, RenderState
from fanops.timeutil import parse_iso
from fanops.variant_learning import _hook_for_post
from fanops.studio.views_common import RECENT_WINDOW_HOURS, _batch_title, _imminent, suggest_time, clip_source_of

logger = logging.getLogger(__name__)


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


# non-terminal render states a shippable artifact can be in (mirrors crosspost._REUSABLE_CLIP_STATES philosophy;
# `queued` is currently dead but allowed so a future staged-render path can't trip a false warn — never `retired`).
_SHIPPABLE_RENDER = (RenderState.rendered, RenderState.queued, RenderState.published, RenderState.analyzed)

def publish_readiness(led: Ledger, post, cfg: Config | None = None) -> tuple[bool, str]:
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
            m = led.moments.get(led.clips[post.parent_id].parent_id) if post.parent_id in led.clips else None
            shown_hook = (m.hook or "").strip() if m is not None else ""
            if rid and (r.hook_text or "") != shown_hook:
                return (False, "hook drift — the burned hook differs from the one shown")
            ready, reason = True, "ready — its own cut"
        else:
            from fanops.crosspost import _REUSABLE_CLIP_STATES
            clip = led.clips.get(post.parent_id) if post.parent_id else None
            if clip is None: return (False, "source clip missing")
            if clip.state not in _REUSABLE_CLIP_STATES: return (False, f"clip not shippable ({clip.state.value})")
            if not (clip.path and Path(clip.path).exists()): return (False, "clip file missing from disk")
            ready, reason = True, "ready — shared clip"
        if cfg is not None and ready:
            from fanops.post.compress import publish_backend_for_post, upload_cap_bytes, media_path_for_post
            backend = publish_backend_for_post(cfg, post)
            cap = upload_cap_bytes(cfg, post, backend)
            if cap is not None:
                mp = media_path_for_post(led, post)
                if mp is not None:
                    try:
                        sz = mp.stat().st_size
                    except OSError:
                        sz = 0
                    if sz > cap:
                        mb = max(1, sz // (1024 * 1024))
                        cap_mb = max(1, cap // (1024 * 1024))
                        return (False, f"too large ({mb} MB > {cap_mb} MB cap) — auto-shrink on ship")
        return (ready, reason)
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
                  account: Optional[str] = None, batch: Optional[str] = None,
                  source: Optional[str] = None) -> list[ScheduleRow]:
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
            caption=p.caption, variant_hook=_hook_for_post(led, p) or None)
        if editable:
            row.ready, row.ready_reason = publish_readiness(led, p, cfg)
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
    if source is not None:
        rows = [r for r in rows if clip_source_of(led, r.clip_id) == source]
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
class DuePublishPlan:
    due: int = 0
    postiz_due: int = 0
    rate_per_min: int = 0
    est_minutes: int = 0


def due_publish_plan(cfg: Config, *, handle: Optional[str] = None, batch: Optional[str] = None,
                     now: Optional[datetime] = None) -> DuePublishPlan:
    """How many queued posts are due NOW in scope, and a Postiz throttle ETA (Sprint 6 guard)."""
    import math
    from fanops.post.run import _due_or_fail, _post_provider
    now = now or datetime.now(timezone.utc)
    led = Ledger.load(cfg)
    accounts = Accounts.load(cfg)
    due = postiz = 0
    for p in led.posts.values():
        if p.state is not PostState.queued:
            continue
        if handle and p.account != handle:
            continue
        if batch and p.batch_id != batch:
            continue
        if not _due_or_fail(cfg, p, now):
            continue
        due += 1
        if _post_provider(cfg, accounts, p) == "postiz":
            postiz += 1
    rate = cfg.postiz_publish_per_min if cfg.is_live else 0
    est = math.ceil(postiz / rate) if rate > 0 and postiz else (1 if due else 0)
    return DuePublishPlan(due=due, postiz_due=postiz, rate_per_min=rate, est_minutes=est)


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



@dataclass
class ScheduleCockpit:
    """Per-account schedule summary for the operator cockpit."""
    handle: str
    due: int = 0
    upcoming: int = 0
    inflight: int = 0
    next_time: Optional[str] = None
    next_times: list = None
    off_suggestion: int = 0

    def __post_init__(self):
        if self.next_times is None:
            self.next_times = []


@dataclass
class InflightWatchRow:
    post_id: str
    account: str
    platform: str
    state: str
    submission_id: Optional[str] = None
    error_reason: Optional[str] = None
    age_minutes: int = 0
    since_iso: Optional[str] = None



def _schedule_needs_suggestion(scheduled_time: Optional[str], now: datetime) -> bool:
    """Queued post needs a fresh suggestion: no time, unparseable, or not strictly future."""
    if not scheduled_time:
        return True
    try:
        return parse_iso(scheduled_time) <= now
    except (ValueError, TypeError):
        return True


def schedule_cockpit(led: Ledger, cfg: Config, account: str, *, now: Optional[datetime] = None) -> ScheduleCockpit:
    """Per-account schedule cockpit: lane counts, next slots, how many differ from suggestion."""
    now = now or datetime.now(timezone.utc)
    rows = schedule_rows(led, cfg, now=now, account=account)
    due = sum(1 for r in rows if r.lane == "due" and r.editable)
    upcoming = sum(1 for r in rows if r.lane == "upcoming" and r.editable)
    inflight = sum(1 for r in rows if r.lane == "inflight")
    off = sum(1 for r in rows if r.editable and r.lane != "inflight" and _schedule_needs_suggestion(r.scheduled_time, now))
    times: list[str] = []
    for r in rows:
        if not r.editable or r.lane == "inflight" or not r.scheduled_time:
            continue
        times.append(r.scheduled_time)
    def _sort_key(t):
        try:
            return parse_iso(t)
        except (ValueError, TypeError):
            return now
    times.sort(key=_sort_key)
    return ScheduleCockpit(handle=account, due=due, upcoming=upcoming, inflight=inflight,
                           next_time=times[0] if times else None, next_times=times[:5], off_suggestion=off)


def inflight_watch(led: Ledger, cfg: Config, *, account: Optional[str] = None,
                   now: Optional[datetime] = None) -> list[InflightWatchRow]:
    """Posts waiting for a permalink — age in minutes for the reconcile strip."""
    now = now or datetime.now(timezone.utc)
    out: list[InflightWatchRow] = []
    for p in led.posts.values():
        if p.state not in (PostState.needs_reconcile, PostState.submitting, PostState.submitted):
            continue
        if account and p.account != account:
            continue
        ts = getattr(p, "published_at", None) or p.scheduled_time
        age, since = 0, None
        if ts:
            try:
                dt = parse_iso(ts)
                if dt.tzinfo is not None:
                    age = max(0, int((now - dt).total_seconds() // 60))
                    since = ts
            except (ValueError, TypeError):
                pass
        out.append(InflightWatchRow(post_id=p.id, account=p.account, platform=p.platform.value,
                                    state=p.state.value, submission_id=p.submission_id,
                                    error_reason=(p.error_reason or "")[:80] or None,
                                    age_minutes=age, since_iso=since))
    out.sort(key=lambda r: (-r.age_minutes, r.post_id))
    return out

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
    # reconcile.py writes these) | 'dryrun' (any non-http public_url — post dryrun-boundary a dryrun
    # post carries no url at all, and a legacy 'dryrun://' still reads as 'dryrun'). Pins the
    # operator's verbatim complaint: 'the system says posted when nothing is posted'.
    posted_via: str = "dryrun"
    submission_id: Optional[str] = None   # inflight rows: backend id awaiting permalink
    error_reason: Optional[str] = None      # inflight/failed: last reconcile error (truncated in UI)
    raw_state: Optional[str] = None         # ledger PostState.value for detail rows
    failure_kind: Optional[str] = None      # failed rows: rate_limit | oversize | bad_payload | poll_error | unknown


_FAILURE_KINDS = ("rate_limit", "oversize", "bad_payload", "poll_error", "transient", "unknown")
_RETRYABLE_FAILURES = frozenset({"rate_limit", "oversize", "bad_payload", "transient", "unknown"})


_FAILURE_LABELS = {"rate_limit": "Rate limited", "oversize": "Too large", "bad_payload": "Bad upload",
                   "poll_error": "Link pending", "transient": "Network blip", "unknown": "Failed"}


def failure_label(kind: str | None) -> str:
    return _FAILURE_LABELS.get(kind or "", "Failed")


def operator_error(msg: str | None, *, kind: str | None = None) -> str:
    """Plain-language error for Studio surfaces — no backend names, ids, or status dumps."""
    if kind:
        return failure_label(kind)
    if not msg:
        return ""
    er = msg.lower()
    if "429" in er or "rate limit" in er or "too many requests" in er:
        return "Rate limited — wait and retry."
    if "413" in er or "oversize" in er or "too large" in er or "entity too large" in er:
        return "Video too large for this platform."
    if "401" in er or "403" in er or "unauthorized" in er or "auth" in er:
        return "Credentials rejected — check Go Live."
    if "400" in er or "bad media" in er or "bad request" in er or "invalid" in er:
        return "Platform rejected the upload."
    if "connection" in er or "refused" in er or "unreachable" in er or "timed out" in er:
        return "Could not reach the publisher — try again."
    if "published_no_url" in er or "no permalink" in er or "no_url" in er:
        return "Published — waiting for link."
    if "not live" in er or "dryrun" in er:
        return "Publishing is off until you go live."
    clean = msg.strip()
    for tag in ("postiz", "zernio"):
        if clean.lower().startswith(tag + " "):
            rest = clean.split(None, 1)[-1] if " " in clean else ""
            if rest[:3].isdigit():
                tail = rest.split(None, 1)[-1] if " " in rest else ""
                return operator_error(tail) if tail else "Platform error."
    return (clean[:97] + "…") if len(clean) > 100 else clean



def classify_failure(post) -> str:
    """Bucket a failed/error post's error_reason for the Posted recovery cockpit."""
    from fanops.studio.views_common import is_transient_failure_reason
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
    if is_transient_failure_reason(getattr(post, "error_reason", None)):
        return "transient"
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


def lineage_stats(rows) -> list:
    """S6 — return a NEW list of rows (PostedRow/LiftRow) annotated with sibling_count / rank / delta_vs_best
    so the operator reads 'this hook BEAT that hook'. Never mutates the caller's rows: an annotated row is a
    dataclasses.replace copy; a skipped row passes through as the same object. Groups by clip_id (the durable
    key a repost/crosspost shares with its origin) and ranks by lift_score desc within the group (COMPETITION
    ranking — tied bests both read rank 1). A falsy clip_id is skipped (no join key -> passed through). An
    unmeasured sibling (lift None) still counts toward sibling_count but keeps rank/delta None (can't rank
    what wasn't measured). Pure over the already-built list — NO ledger read, reads ONLY clip_id+lift (so it
    is FANOPS_CREATIVE_VARIATION-independent: a shared clip across accounts is a real lineage in either mode).
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


def account_median_deltas(rows) -> None:
    """T-15 — IN-PLACE annotate each row with delta_vs_account_median = round(lift_score - account_median, 4),
    so the operator reads 'this variant beat/trailed its ACCOUNT's typical lift'. This is a DIFFERENT statistic
    from lineage_stats' delta_vs_best (best-in-clip-lineage): here the baseline is statistics.median over the
    account's MEASURED lift scores. Groups by `account`; a group with <2 measured rows is degenerate (a median
    vs a single point) and left at None — mirroring lineage_stats' judgment of only ranking within `measured`.
    An unmeasured row (lift None/non-numeric) is excluded from the median AND never stamped. Pure over the
    already-built list — NO ledger read. Fail-open: any error leaves the additive field at its None default."""
    try:
        groups: dict = {}
        for r in rows:
            acct = getattr(r, "account", None)
            if acct: groups.setdefault(acct, []).append(r)
        for grp in groups.values():
            measured = [r for r in grp if isinstance(getattr(r, "lift_score", None), (int, float))
                        and not isinstance(r.lift_score, bool)]
            if len(measured) < 2: continue     # a median vs a single data point is degenerate
            med = statistics.median(r.lift_score for r in measured)
            for r in measured: r.delta_vs_account_median = round(r.lift_score - med, 4)
    except Exception:
        return   # fail-open (mirrors lineage_stats): additive field stays at its None default, never a raise


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
