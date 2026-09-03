"""Schedule read-models for the Studio: approved-bucket rows, publish-readiness, suggested-time rationale,
cockpit summaries, inflight watch, and calendar/bucket views. Pure (no HTTP/Flask)."""
from __future__ import annotations
import calendar as _cal
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.models import PostState, RenderState
from fanops.timeutil import parse_iso, is_scheduled_due, schedule_utc
from fanops.variant_learning import _hook_for_post
from fanops.studio.views_common import RECENT_WINDOW_HOURS, _batch_title, _imminent, suggest_time, clip_source_of, account_color_hue
from fanops.studio.views_posted import _EXPOSURE_STATES, classify_post_delivery

logger = logging.getLogger(__name__)


def tag_exposure(led: Ledger) -> dict[str, list[tuple[str, int]]]:
    """Per-account hashtag exposure counts across in-flight + shipped posts (excludes rejected/failed/retired)."""
    counts: dict[str, dict[str, int]] = {}
    for p in led.posts.values():
        if p.state not in _EXPOSURE_STATES:
            continue
        bucket = counts.setdefault(p.account, {})
        for t in (p.hashtags or []):
            bucket[t] = bucket.get(t, 0) + 1
    return {h: sorted(tags.items(), key=lambda x: (-x[1], x[0])) for h, tags in counts.items()}


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
    bad_schedule: bool = False            # read-only: scheduled_time present but unparseable (M07 chip)
    inflight_headline: str = ""           # inflight lane: token-provenance copy, not a hardcoded waiting-for-link


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
                mp = media_path_for_post(cfg, led, post)
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
    except Exception as exc:
        logger.warning("publish readiness check failed (unverified): %s", exc)
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
        except Exception as exc:
            logger.warning("schedule_rows: backend resolve failed for %s (%s); defaulting", p.id, exc)
            backend = cfg.poster_backend or "dryrun"
        row = ScheduleRow(
            post_id=p.id, scheduled_time=p.scheduled_time, account=p.account,
            platform=p.platform.value, clip_id=p.parent_id, state=state, imminent=imm,
            editable=editable, integration_id=p.account_id, lane=lane,
            delivery=classify_post_delivery(p), submission_id=p.submission_id,
            backend=backend, error_reason=(p.error_reason or "")[:120] or None,
            suggested_time=suggest_time(cfg, p, now=now) if editable else None,
            batch_id=p.batch_id, batch_title=_batch_title(led, p.batch_id),
            caption=p.caption, variant_hook=_hook_for_post(led, p) or None,
            bad_schedule=bool((p.scheduled_time or "").strip()) and schedule_utc(p.scheduled_time) is None,
            inflight_headline=inflight_headline(p) if lane == "inflight" else "")
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
        if is_scheduled_due(p, now):
            return "due"
        return "upcoming"
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
    from fanops.post.run import _post_provider
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
        if not is_scheduled_due(p, now):
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
    # Report 11 §5: UNVERIFIED reconciliation evidence — the id a backend named when it rejected this post as
    # a duplicate. Rendered DISTINCTLY from submission_id and never as one: submission_id means "the backend
    # id OF this post", a candidate means "a record the backend holds that MIGHT be this post". Only the
    # operator can close that gap, so the UI must not let the two read alike.
    reconcile_candidate_id: Optional[str] = None
    inflight_headline: str = ""


def inflight_headline(post) -> str:
    from fanops.models import is_real_submission_id
    if is_real_submission_id(getattr(post, "submission_id", None)):
        return "Waiting for link"
    return "No backend id — cannot fetch a link"


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
                                    age_minutes=age, since_iso=since,
                                    reconcile_candidate_id=getattr(p, "reconcile_candidate_id", None),
                                    inflight_headline=inflight_headline(p)))
    out.sort(key=lambda r: (-r.age_minutes, r.post_id))
    return out


@dataclass
class ScheduleChip:
    post_id: str
    account: str
    clip_id: str
    platform: str
    caption: str
    variant_hook: Optional[str]
    time_hm: str              # operator-local HH:MM (drag keeps this on date change)
    hue: int
    editable: bool
    lane: str
    scheduled_time: str       # canonical ISO-Z for move forms


@dataclass
class DayCell:
    date: str                 # YYYY-MM-DD operator-local
    in_month: bool
    is_past: bool
    is_today: bool
    chips: list[ScheduleChip]


@dataclass
class CalendarMonth:
    year: int
    month: int
    label: str
    weeks: list[list[DayCell]]
    prev_ym: str              # YYYY-MM navigation
    next_ym: str
    month_param: str
    # MOL-730: the chips this month does NOT show. The calendar is month-scoped, so an operator whose
    # approved posts all sit in a neighbouring month saw an empty grid with nothing saying why (at a
    # month boundary "now + 3h" is next month — every chip vanished). These are FACTS, not UI policy:
    # the template decides when to surface them (today: only when `shown == 0`, so an in-month render
    # stays byte-identical). Counted AFTER the account filter, so they match the scope on screen.
    shown: int = 0                      # chips placed inside this month
    offscreen: int = 0                  # chips dropped because their operator-local month differs
    offscreen_ym: Optional[str] = None  # YYYY-MM of the EARLIEST offscreen chip (where the queue starts)


def _schedule_chip(row: ScheduleRow, cfg: Config, *, zone) -> Optional[ScheduleChip]:
    if not row.scheduled_time:
        return None
    try:
        dt = parse_iso(row.scheduled_time)
        if dt.tzinfo is None:
            return None
        local = dt.astimezone(zone)
    except (ValueError, TypeError):
        return None
    return ScheduleChip(post_id=row.post_id, account=row.account, clip_id=row.clip_id,
                        platform=row.platform, caption=row.caption, variant_hook=row.variant_hook,
                        time_hm=local.strftime("%H:%M"), hue=account_color_hue(row.account),
                        editable=row.editable, lane=row.lane, scheduled_time=row.scheduled_time)


def schedule_calendar_month(rows: list[ScheduleRow], cfg: Config, *, year: int, month: int,
                            account: Optional[str] = None, now: Optional[datetime] = None) -> CalendarMonth:
    """U7: month grid of scheduled chips in operator-local days. Optional account filters chips only.
    MOL-730: also reports shown/offscreen/offscreen_ym — the chips this month drops — so a month with
    none of them is legible instead of blank. Placement itself is unchanged."""
    from fanops.timeutil import _operator_zone
    now = now or datetime.now(timezone.utc)
    zone = _operator_zone(cfg) or timezone.utc
    today = now.astimezone(zone).date()
    chips_by_day: dict[str, list[ScheduleChip]] = {}
    shown = 0; offscreen = 0; offscreen_first: Optional[datetime] = None
    for r in rows:
        if account is not None and r.account != account:
            continue
        chip = _schedule_chip(r, cfg, zone=zone)
        if chip is None:
            continue
        try:
            dt = parse_iso(r.scheduled_time)
            local = dt.astimezone(zone)
            if local.year != year or local.month != month:
                # MOL-730: a real, schedulable chip — just not in THIS month. Count it (and remember the
                # earliest) so the surface can say where the queue actually is instead of rendering blank.
                offscreen += 1
                if offscreen_first is None or local < offscreen_first: offscreen_first = local
                continue
            dkey = local.date().isoformat()
        except (ValueError, TypeError):
            continue
        shown += 1
        chips_by_day.setdefault(dkey, []).append(chip)
    for day_chips in chips_by_day.values():
        day_chips.sort(key=lambda c: c.time_hm)
    weeks_raw = _cal.monthcalendar(year, month)
    weeks: list[list[DayCell]] = []
    for week in weeks_raw:
        row_cells: list[DayCell] = []
        for day in week:
            if day == 0:
                row_cells.append(DayCell(date="", in_month=False, is_past=False, is_today=False, chips=[]))
                continue
            d = datetime(year, month, day, tzinfo=zone).date()
            dkey = d.isoformat()
            row_cells.append(DayCell(date=dkey, in_month=True, is_past=d < today, is_today=d == today,
                                       chips=chips_by_day.get(dkey, [])))
        weeks.append(row_cells)
    prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)
    label = datetime(year, month, 1).strftime("%B %Y")
    off_ym = f"{offscreen_first.year:04d}-{offscreen_first.month:02d}" if offscreen_first is not None else None
    return CalendarMonth(year=year, month=month, label=label, weeks=weeks,
                         prev_ym=f"{prev_y:04d}-{prev_m:02d}", next_ym=f"{next_y:04d}-{next_m:02d}",
                         month_param=f"{year:04d}-{month:02d}",
                         shown=shown, offscreen=offscreen, offscreen_ym=off_ym)


def schedule_bucket_split(led: Ledger, rows: list[ScheduleRow]) -> dict[str, dict[str, list[ScheduleRow]]]:
    """U7: split account-scoped rows into untimed vs timed buckets, each grouped by clip source."""
    out: dict[str, dict[str, list[ScheduleRow]]] = {"untimed": {}, "timed": {}}
    for r in rows:
        bucket = "untimed" if not (r.scheduled_time or "").strip() else "timed"
        sid = clip_source_of(led, r.clip_id) or ""
        out[bucket].setdefault(sid, []).append(r)
    for b in out.values():
        for sid in b:
            b[sid].sort(key=lambda x: (x.scheduled_time or "", x.post_id))
    return out
