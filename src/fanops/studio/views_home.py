"""Home tab read-models for the Studio: status headline, batch list, account tiles, source gallery,
week calendar, workflow spine, and the review-handoff helpers the Home surface renders. Pure (no HTTP/Flask).
Depends on views_common for lineage_maps; lazy-imports golive_accounts/led_for_request/_publish_mode_label from
the views facade to avoid circular imports."""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from fanops.config import Config
from fanops.errors import fail_open
from fanops.ledger import Ledger
from fanops.models import ClipState, PostState
from fanops.timeutil import parse_iso
from fanops.studio.views_common import lineage_maps

if TYPE_CHECKING:
    from fanops.studio.views import GoLiveAccount


@dataclass
class HomeStatus:                      # Face 2: the GET / status-home read-model (read-only, no secret, no flag)
    mode: str
    is_live: bool
    counts: dict                       # {sources, batches(int|None on fail-open), awaiting, scheduled, posted}
    accounts: list[GoLiveAccount]      # via the shared golive_accounts helper (NEVER golive_status -> no build_health_report on /)
    by_account: dict                   # Face 2 fu (D2): per-account post counts for #home-metrics (on-disk facts, never lift)


@dataclass
class HomeBatch:                       # Face 2 fu: one batch row for the Home entry point (deep-links ?batch=<id>)
    id: str
    name: str
    targets: list[str]
    state: str
    created_at: Optional[str]
    posts_born: int
    sources_in_batch: int = 0
    is_emptied: bool = False             # 0 sources AND 0 posts — post-reset shell, not a silent-fail run
    is_zero_result: bool = False         # sources > 0 AND 0 posts — a run that birthed nothing


def _post_is_due(p, now: datetime) -> bool:
    from fanops.timeutil import is_scheduled_due
    return p.state is PostState.queued and is_scheduled_due(p, now)


def _post_live_today(p, now: datetime) -> bool:
    from fanops.studio.views_results import _classify_channel
    if _classify_channel(p.public_url) != "live":
        return False
    t = p.published_at or p.scheduled_time
    if not t:
        return False
    with fail_open("studio.views_home._post_live_today"):
        dt = parse_iso(t)
        if dt.tzinfo is None:
            return False
        return dt >= now - timedelta(hours=24)
    return False


def _queued_has_future_schedule(p, now: datetime) -> bool:
    """True when a queued post has a strictly-future scheduled_time (not timeless / past-due)."""
    if not p.scheduled_time:
        return False
    try:
        return parse_iso(p.scheduled_time) > now
    except (ValueError, TypeError):
        return False


def review_handoff(cfg: Config) -> dict:
    """Account with the most awaiting posts — Make → Review entry."""
    from fanops.studio import views as _views
    wc = account_work_counts(cfg)
    best_h, best_n = None, 0
    for h, c in wc.items():
        n = int(c.get("awaiting") or 0)
        if n > best_n:
            best_h, best_n = h, n
    if not best_h or not best_n:
        return {}
    out = {"account": best_h, "awaiting": best_n}
    with fail_open("studio.views_home.review_handoff"):
        led = _views.led_for_request(cfg)
        by_batch: dict[str, int] = {}
        # T2.5: pick the dominant batch off the OWNED worklist. Off a raw state tally this handoff link could
        # point the operator at a batch made entirely of dead lineage — a Review page that then shows nothing.
        for p in led.review_posts():
            if p.account == best_h and p.batch_id:
                by_batch[p.batch_id] = by_batch.get(p.batch_id, 0) + 1
        if by_batch:
            out["batch"] = max(by_batch, key=by_batch.get)
    return out


def zero_post_clips(cfg: Config) -> list[dict]:
    """Captioned/queued clips with no Post born — the silent crosspost drop surfaced for Home."""
    with fail_open("studio.views_home.zero_post_clips"):
        led = Ledger.load(cfg)
        out = []
        for clip in led.clips.values():
            if clip.state not in (ClipState.queued, ClipState.captioned):
                continue
            if any(p.parent_id == clip.id for p in led.posts.values()):
                continue
            mom = led.moments.get(clip.parent_id)
            out.append({"clip_id": clip.id, "moment_id": clip.parent_id,
                        "window": f"{int(mom.start)}–{int(mom.end)}" if mom else "—"})
        return out[:5]
    return []


def metrics_stale_hint(cfg: Config) -> bool:
    """True when live trackable posts exist but most lack analyzed metrics."""
    if not cfg.is_live:
        return False
    with fail_open("studio.views_home.metrics_stale_hint"):
        from fanops.studio.views_results import _classify_channel
        led = Ledger.load(cfg)
        live = [p for p in led.posts.values()
                if p.state in (PostState.published, PostState.analyzed)
                and _classify_channel(getattr(p, "public_url", None)) == "live"]
        if len(live) < 2:
            return False
        thin = sum(1 for p in live if (p.metrics or {}).get("lift_score") is None)
        return thin >= max(1, len(live) // 2)
    return False


def review_nav_params(cfg: Config, account: str | None = None) -> dict:
    """Review focus entry — account + dominant batch for handoff links."""
    from fanops.studio import views as _views
    out: dict = {"view": "account", "focus": 1}
    h = account
    batch = None
    if not h:
        handoff = review_handoff(cfg)
        h = handoff.get("account")
        batch = handoff.get("batch")
    if h:
        out["account"] = h
        if batch is None:
            with fail_open("studio.views_home.review_nav_params"):
                led = _views.led_for_request(cfg)
                by_batch: dict[str, int] = {}
                for p in led.review_posts():           # T2.5: same owned worklist as review_handoff above
                    if p.account == h and p.batch_id:
                        by_batch[p.batch_id] = by_batch.get(p.batch_id, 0) + 1
                if by_batch:
                    batch = max(by_batch, key=by_batch.get)
    if batch:
        out["batch"] = batch
    return out


def account_work_counts(cfg: Config) -> dict[str, dict]:
    """Per-handle work queue counts for Home rows and the account session bar."""
    from collections import defaultdict
    from fanops.studio import views as _views
    out: dict[str, dict] = defaultdict(lambda: {"awaiting": 0, "scheduled": 0, "failed": 0, "inflight": 0, "review_batch": None})
    with fail_open("studio.views_home.account_work_counts"):
        led = _views.led_for_request(cfg)
        now = datetime.now(timezone.utc)
        # awaiting stays the owned worklist predicate (can_promote); scheduled is a time predicate on
        # queued rows — neither is a pure PostState census. inflight/failed read Ledger.state_histogram.
        for p in led.posts.values():
            h = p.account
            if p.state is PostState.awaiting_approval and led.can_promote(p):
                out[h]["awaiting"] += 1
            elif p.state is PostState.queued and _queued_has_future_schedule(p, now):
                out[h]["scheduled"] += 1
        for h in {p.account for p in led.posts.values()}:
            hist = led.state_histogram(account=h)
            inflight = (hist[PostState.needs_reconcile] + hist[PostState.submitting]
                        + hist[PostState.submitted])
            failed = hist[PostState.failed] + hist[PostState.error]
            if inflight:
                out[h]["inflight"] = inflight
            if failed:
                out[h]["failed"] = failed
    for h in out:
        if out[h]["awaiting"]:
            out[h]["review_batch"] = review_nav_params(cfg, h).get("batch")
    return dict(out)


def home_status(cfg: Config) -> HomeStatus:
    """Lock-free, fail-open read-model for GET / (the status home): connection state per account (via the
    shared golive_accounts helper — NEVER golive_status, which also runs build_health_report on every load) +
    headline counts + per-account post counts, all from ONE Ledger.load. A torn ledger -> zeroed counts +
    batches=None + empty by_account, never a 500."""
    from fanops.studio import views as _views
    accounts = _views.golive_accounts(cfg)                   # once-bound, already fail-open (no build_health_report on /)
    mode = _views._publish_mode_label(cfg)                    # provider-aware (M3); 'dryrun' when not live
    try:
        from collections import Counter
        led = _views.led_for_request(cfg)
        att = led.attention_counts()        # T2.5: the OWNED worklist, loaded ONCE for this view — never per row
        st = led.state_histogram()
        inflight = (st[PostState.needs_reconcile] + st[PostState.submitting] + st[PostState.submitted])
        due_soon = sum(1 for p in led.posts.values()
                       if p.state is PostState.queued and _post_is_due(p, datetime.now(timezone.utc)))
        live_today = sum(1 for p in led.posts.values()
                         if p.state in (PostState.published, PostState.analyzed)
                         and _post_live_today(p, datetime.now(timezone.utc)))
        from fanops.studio.views_results import _classify_channel
        live_trackable = sum(1 for p in led.posts.values()
                             if p.state in (PostState.published, PostState.analyzed)
                             and _classify_channel(getattr(p, "public_url", None)) == "live")
        failed = st[PostState.failed]
        from fanops.studio.views_results import failure_rollup
        fb = failure_rollup(led)["buckets"]
        counts = {"sources": sum(1 for s in led.sources.values() if s.origin_kind == "native"),
                  "batches": len(getattr(led, "batches", {})),
                  "awaiting": att["moments"],
                  "awaiting_posts": st[PostState.awaiting_approval],
                  "scheduled": st[PostState.queued],
                  "inflight": inflight,
                  "due_soon": due_soon,
                  "live_today": live_today,
                  "live_trackable": live_trackable,
                  "failed": failed, "failed_rate_limit": fb.get("rate_limit", 0),
                  "failed_oversize": fb.get("oversize", 0),
                  "posted": st[PostState.published] + st[PostState.analyzed]}
        by_account = dict(Counter(p.account for p in led.posts.values()))
    except Exception as exc:                          # the first page an operator sees must never 500
        from fanops.log import get_logger
        get_logger(cfg)("home", "-", "error", err=str(exc)[:160])
        counts = {"sources": 0, "batches": None, "awaiting": 0, "awaiting_posts": 0, "scheduled": 0,
                  "inflight": 0, "due_soon": 0, "live_today": 0, "live_trackable": 0, "failed": 0, "posted": 0}
        by_account = {}
    return HomeStatus(mode=mode, is_live=cfg.is_live, counts=counts, accounts=accounts, by_account=by_account)


def home_batches(cfg: Config) -> list[HomeBatch]:
    """Lock-free, fail-open batch list for the Home entry point — each row deep-links ?batch=<id> into Review
    and carries posts_born + a zero-result flag (a non-empty target that birthed NO post — the silent
    crosspost batch_target_skip outcome, surfaced). Newest-first by created_at (None sinks last), tie-broken
    by id. Torn ledger -> [] + logged, never a 500. Surfaces the outcome; computes no skip logic."""
    try:
        led = Ledger.load(cfg)
        out = []
        for b in getattr(led, "batches", {}).values():
            sources = sum(1 for s in led.sources.values() if getattr(s, "batch_id", None) == b.id)
            born = sum(1 for p in led.posts.values() if p.batch_id == b.id)
            is_emptied = sources == 0 and born == 0
            is_zero_result = sources > 0 and born == 0
            out.append(HomeBatch(id=b.id, name=b.name, targets=list(b.target_accounts), state=b.state.value,
                                 created_at=b.created_at, posts_born=born, sources_in_batch=sources,
                                 is_emptied=is_emptied, is_zero_result=is_zero_result))
        out.sort(key=lambda h: (h.created_at or "", h.id), reverse=True)
        return out
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("home_batches", "-", "error", err=str(exc)[:160])
        return []


def load_account_stats(cfg: Config) -> dict[str, dict]:
    """Read account_stats.json; corrupt/missing -> {}."""
    try:
        p = cfg.account_stats_path
        if not p.exists():
            return {}
        raw = json.loads(p.read_text())
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("home", "-", "account_stats_read_error", err=str(exc)[:120])
        return {}


_HOME_CAL_COLORS = ("#4a9e8e", "#c85a7a", "#5888dd", "#e6a23c", "#6c8ebf", "#d74a4a", "#8bc34a", "#f90")


def home_accounts_panel(cfg: Config) -> list[dict]:
    """Compact account tiles for Home: handle, platforms, persona, followers, posted total, awaiting badge."""
    from fanops.studio import views as _views
    accounts = _views.golive_accounts(cfg)
    wc = account_work_counts(cfg)
    stats = load_account_stats(cfg)
    personas = None
    try:
        from fanops.personas import Personas
        personas = Personas.load(cfg)
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("home", "-", "personas_load_error", err=str(exc)[:120])
    posted_by: dict[str, int] = {}
    try:
        led = Ledger.load(cfg)
        for p in led.posts.values():
            if p.state in (PostState.published, PostState.analyzed):
                posted_by[p.account] = posted_by.get(p.account, 0) + 1
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("home", "-", "posted_total_error", err=str(exc)[:120])
    out = []
    for a in accounts:
        persona_name = "no persona"
        if personas and a.persona_id:
            pr = personas.get(a.persona_id)
            if pr and pr.name:
                persona_name = pr.name
        elif a.persona:
            persona_name = a.persona
        snap = stats.get(a.handle) or {}
        followers = snap.get("followers") if isinstance(snap.get("followers"), int) else "—"
        w = wc.get(a.handle, {})
        out.append({"handle": a.handle, "channels": [ch.platform for ch in a.channels],
                    "persona": persona_name, "followers": followers,
                    "posted_total": posted_by.get(a.handle, 0), "awaiting": int(w.get("awaiting") or 0)})
    return out


def home_source_gallery(cfg: Config, *, page: int = 1, per_page: int = 12) -> dict:
    """Native sources newest-first with clip counts and thumb URLs."""
    try:
        led = Ledger.load(cfg)
        moms, clips_bm, _posts_bc = lineage_maps(led)
        rows = []
        for sid, src in led.sources.items():
            if getattr(src, "origin_kind", None) == "third_party":
                continue
            moments = moms.get(sid, [])
            clips_n = sum(len(clips_bm.get(m.id, [])) for m in moments)
            title = Path(src.source_path).name if src.source_path else sid
            rows.append({"id": sid, "title": title, "clips": clips_n, "created_at": src.created_at or "",
                         "thumb_url": f"/thumb/source/{sid}", "url": f"/library/{sid}"})
        rows.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
        total = len(rows)
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, pages))
        start = (page - 1) * per_page
        items = rows[start:start + per_page]
        return {"entries": items, "page": page, "pages": pages, "total": total}
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("home", "-", "source_gallery_error", err=str(exc)[:120])
        return {"entries": [], "page": 1, "pages": 1, "total": 0}


def home_week_calendar(cfg: Config) -> dict:
    """Rolling 7-day columns of future-scheduled queued posts in operator_tz."""
    from fanops.timeutil import _operator_zone
    zone = _operator_zone(cfg)
    now = datetime.now(timezone.utc)
    today = datetime.now(zone).date()
    day_keys = [(today + timedelta(days=i)).isoformat() for i in range(7)]
    buckets: dict[str, list] = {d: [] for d in day_keys}
    try:
        led = Ledger.load(cfg)
        for p in led.posts.values():
            if p.state is not PostState.queued or not p.scheduled_time:
                continue
            try:
                dt = parse_iso(p.scheduled_time)
                if dt.tzinfo is None or dt <= now:
                    continue
                local = dt.astimezone(zone)
                dkey = local.date().isoformat()
                if dkey not in buckets:
                    continue
                plat = getattr(p.platform, "value", p.platform) if p.platform else "?"
                color = _HOME_CAL_COLORS[hash(p.account) % len(_HOME_CAL_COLORS)]
                buckets[dkey].append({"time": local.strftime("%H:%M"), "account": p.account,
                                      "platform": plat, "color": color,
                                      "title": f"{local.strftime('%H:%M')} · {p.account} · {plat}"})
            except (ValueError, TypeError):
                continue
        for posts in buckets.values():
            posts.sort(key=lambda x: x["time"])
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("home", "-", "week_calendar_error", err=str(exc)[:120])
    return {"days": [{"date": d, "posts": buckets[d]} for d in day_keys]}


@dataclass
class SpineStage:                      # Slice 1: one node of the workflow stepper
    key: str                           # 'make' | 'review' | 'schedule' | 'posted'
    label: str
    endpoint: str                      # the rail endpoint this stage links to
    count: int                         # the stage's headline number (sources/awaiting/scheduled/posted)
    state: str                         # 'active' (you-are-here) | 'done' | 'todo'
    severity: Optional[str] = None     # warn | info | danger — stage badge emphasis


@dataclass
class WorkflowSpine:                    # the whole through-line: the ordered path + the single next move
    stages: list[SpineStage]           # always Make→Review→Schedule→Posted
    next_label: Optional[str]          # the one next-action sentence ("Review 4 clips")
    next_endpoint: Optional[str]       # where it points; None == "caught up", no CTA
    here: Optional[str]                # the current stage key (from the active tab), else None
    inflight: int = 0                  # needs_reconcile + submitting (Schedule severity)
    blocked_gates: Optional[int] = 0   # pending agent gates; None == metrics unknown (never calm-zero)
    strip_metrics_unknown: bool = False
    next_params: dict = field(default_factory=dict)  # extra url_for kwargs for the next CTA


_SPINE_ORDER = (("make", "Make", "run_panel"), ("review", "Review", "review"),
                ("schedule", "Schedule", "schedule"), ("posted", "Posted", "posted"))


def build_spine(*, counts: dict, has_accounts: bool, here: Optional[str],
                inflight: int = 0, blocked_gates: Optional[int] = 0,
                strip_metrics_unknown: bool = False,
                next_params: Optional[dict] = None) -> WorkflowSpine:
    """Pure: turn the Home counts into the Make→Review→Schedule→Posted stepper. Stage badges carry
    severity when blocked (Make), awaiting>20 (Review), or inflight>0 (Schedule).
    strip_metrics_unknown → Make danger + gates CTA; blocked_gates stays None (never int-cast to 0)."""
    src = int(counts.get("sources", 0)); awaiting = int(counts.get("awaiting", 0))
    queued = int(counts.get("scheduled", 0)); posted = int(counts.get("posted", 0))
    failed = int(counts.get("failed", 0)); live_trackable = int(counts.get("live_trackable", 0))
    inflight = int(inflight)
    if strip_metrics_unknown:
        blocked_gates = None
    else:
        blocked_gates = int(blocked_gates or 0)
    done = {"make": src > 0, "review": awaiting == 0 and (queued > 0 or posted > 0), "schedule": posted > 0, "posted": live_trackable > 0}
    sched_count = queued + inflight
    nums = {"make": src, "review": awaiting, "schedule": sched_count, "posted": live_trackable}
    sev = {"make": "danger" if (strip_metrics_unknown or blocked_gates) else None,
           "review": "warn" if awaiting > 20 else None,
           "schedule": "info" if inflight else None,
           "posted": "danger" if failed else None}
    stages = [SpineStage(key=k, label=lbl, endpoint=ep, count=nums[k],
                         state=("active" if k == here else ("done" if done[k] else "todo")),
                         severity=sev[k])
              for k, lbl, ep in _SPINE_ORDER]
    if not has_accounts:               n = ("Connect an account to begin", "golive_view")
    elif src == 0:                     n = ("Add footage to get started", "run_panel")
    elif strip_metrics_unknown:        n = ("Gate metrics unknown — open processing decisions", "gates")
    elif blocked_gates:                n = (f"Answer {blocked_gates} processing decision{'s' if blocked_gates != 1 else ''}", "gates")
    elif awaiting > 0:                 n = (f"Review {awaiting} clip{'s' if awaiting != 1 else ''}", "review")
    elif queued > 0 or inflight:       n = (f"Schedule {queued} post{'s' if queued != 1 else ''}" + (f" · {inflight} in flight" if inflight else ""), "schedule")
    elif failed > 0:                   n = (f"{failed} post{'s' if failed != 1 else ''} failed — open recovery", "posted")
    elif live_trackable > 0:           n = ("You're all caught up", None)
    else:                              n = ("Run a pass in Make", "run_panel")
    return WorkflowSpine(stages=stages, next_label=n[0], next_endpoint=n[1], here=here,
                         inflight=inflight, blocked_gates=blocked_gates,
                         strip_metrics_unknown=strip_metrics_unknown,
                         next_params=next_params or {})
