"""Pure read-model builders for the Studio (no HTTP, no Flask). Each request re-loads the ledger
(lock-free) and assembles these dataclasses; templates render them. Mutations live in actions.py."""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fanops.config import Config
from fanops.accounts import Accounts
from fanops.errors import fail_open
from fanops.ledger import Ledger
from fanops.models import ClipState, PostState, StitchState, SourceState
from fanops.timeutil import parse_iso
# Facade re-exports: the names consumers reach via `fanops.studio.views` / `views.X` (templates / app.py /
# tests). Dead re-exports (no facade consumer AND no internal use here) were trimmed — every trimmed symbol
# still lives in its home submodule (views_common/_review/_results); this is just the public views surface.
# F401-silenced because each name is re-exported, not referenced within this file.
from fanops.studio import views_common   # module alias for build_system_strip's health/banner delegates (D13b)
from fanops.studio.views_common import (IMMINENT_THRESHOLD_MINUTES, GRID_PAGE_SIZE, paginate, TERM_DEFS, term_def, accounts_in, _imminent, suggest_time, lineage_maps, clip_source_of, source_universe_for_clips, account_color_hue)  # noqa: F401
from fanops.studio.views_review import (SurfacePost, ReviewCard, ProvChip, provenance_chips, _surface, source_choices, _empty_cell_reason, review_matrix, account_lanes, _STATE_TO_BUCKET, review_buckets, review_counts, review_progress, source_universe, account_pivot_rows, review_feed_rows, review_awaiting_by_account_led, review_scope_bucket_counts, group_review_by_source, surface_for_post, group_review_by_batch, awaiting_moment_count, review_awaiting_by_account)  # noqa: F401
from fanops.studio.views_results import (ScheduleRow, ScheduleLanes, LiftRow, publish_readiness, explain_suggested_time, schedule_rows, schedule_lanes, due_publish_plan, DuePublishPlan, schedule_cockpit, ScheduleCockpit, inflight_watch, InflightWatchRow, ScheduleChip, DayCell, CalendarMonth, schedule_calendar_month, schedule_bucket_split, PostedRow, posted_library, posted_archive_rows, posted_batch_rollup, lineage_stats, account_median_deltas, metric_peaks, bar_pct, group_posted_by_day, lift_rows, whats_working_panel, DimInsightRow, classify_post_delivery, failure_rollup, operator_error, failure_label, tag_exposure)  # noqa: F401
from fanops.studio.views_live import (LiveMediaRow, live_library, live_library_scope)  # noqa: F401  # MOL-27: the "viewed there, not authored here" Live library read-model (imported_media only, disjoint from Posted)
from fanops.studio.views_library import (STAGES, library_catalog, source_pipeline_map, source_progress)  # noqa: F401
from fanops.studio.views_home import (HomeStatus, HomeBatch, SpineStage, WorkflowSpine, home_status, home_batches, home_accounts_panel, home_source_gallery, home_week_calendar, account_work_counts, review_handoff, review_nav_params, zero_post_clips, metrics_stale_hint, build_spine, load_account_stats)  # noqa: F401


def led_for_request(cfg: Config) -> Ledger:
    """One Ledger.load per Studio HTTP request. Outside a request (CLI/tests): load normally."""
    with fail_open("studio.views.led_for_request"):
        from flask import g, has_request_context
        if has_request_context():
            led = getattr(g, "fanops_ledger", None)
            if led is None:
                led = Ledger.load(cfg)
                g.fanops_ledger = led
            return led
    return Ledger.load(cfg)


@dataclass
class GoLiveChannel:
    platform: str
    integration_id: str        # effective current id: the per-platform integrations[platform], else the
                               # shared account_id fallback, else "" (unmapped). NEVER a secret.
    backend: str = ""          # Zernio slice 4: the per-(handle, platform) backend OVERRIDE (e.g. "zernio");
                               # "" == no override -> the global FANOPS_POSTER. Surfaced so the operator sees
                               # WHICH scheduler each channel publishes through (IG postiz, TikTok zernio).


@dataclass
class ChannelReadiness:
    handle: str
    platform: str
    backend: str          # effective_provider resolved, "" if none
    mapped: bool
    creds: bool           # backend_has_creds(effective_provider) — publish creds NOT Meta
    persona: bool         # persona_id or inline persona (account-level)
    window: bool          # True always today (default-open window)
    ready: bool           # MUST agree with go_live
    first_blocker: str    # earliest failing check as action phrase; "" when ready


@dataclass
class GoLiveAccount:
    handle: str
    persona: Optional[str]
    channels: list[GoLiveChannel]    # one per platform this handle posts to
    persona_id: Optional[str] = None # S8: the linked first-class Persona record id (Account.persona_id) — None
                                     # when the account uses only inline text. Drives the linked/no-persona badge.
    ig_user_id: str = ""             # per-account Meta IG Business user id (accounts.json, non-secret) — safe to
                                     # render as the current value; "" == unset (falls back to global META_IG_USER_ID).
    meta_token_set: bool = False     # whether a per-handle Graph access token is set (a per-handle .env key). BOOL
                                     # only — the token value is a SECRET, NEVER carried in this read-model.


@dataclass
class GoLiveStatus:
    mode: str
    is_live: bool
    postiz_url: Optional[str]
    key_set: bool              # BOOL only — the POSTIZ_API_KEY value is NEVER carried in this read-model
    accounts: list[GoLiveAccount]
    checks: list[dict]
    notes: list[str]
    zernio_key_set: bool = False       # Zernio slice 4: BOOL only — ZERNIO_API_KEY present (connect-block state)
    learning_validated: bool = False   # M3: cutover.json metrics_confirmed — the loop is unfrozen on this backend
    account_casting: bool = False      # per-account moment casting ON (FANOPS_ACCOUNT_CASTING) — distinct moment sets per account
    clip_profile: str = "talk"         # clip-length band (FANOPS_CLIP_PROFILE): talk 12-22s / song 18-35s
    responder_mode: str = "llm"        # FANOPS_RESPONDER resolves to 'llm' (validate-or-refuse); gates answered by the LLM
    llm_transport: str = "claude"      # FANOPS_LLM_TRANSPORT: claude | cursor (which CLI shells for gates)
    llm_cli_binary: str = "claude"     # resolved binary name for operator copy (claude | cursor-agent)
    daemon: Optional[dict] = None      # launchd pipeline-driver health (verdict/loaded/interval/responder), None off-darwin
    paused: bool = False               # 00_control/paused — the operator brake on the unattended pump
    demoted: list = field(default_factory=list)   # Phase 3: planned/demoted accounts (promotable) — golive_accounts lists only active()
    # Phase 6: A/B learning-loop INTENT flags (default OFF). For the variant_* three, ON sets intent only —
    # their apply paths stay learning_validated-frozen (that gate auto-stamps on real metrics). The two
    # learn_* flags below carry NO such freeze: each is the whole gate on its learn-pass actuator.
    variant_learning: bool = False     # FANOPS_VARIANT_LEARNING — the loop master switch
    variant_amplify: bool = False      # FANOPS_VARIANT_AMPLIFY — a sustained winner auto-amplifies its source
    learn_amplify: bool = False        # FANOPS_LEARN_AMPLIFY — learn-pass winner mints new moments/clips/posts
    learn_retire: bool = False         # FANOPS_LEARN_RETIRE — learn-pass loser suppresses its clip/moment/unshipped posts
    variant_ucb: bool = False          # FANOPS_VARIANT_UCB — deterministic UCB1 explore/exploit rank
    variant_transfer: bool = False     # FANOPS_VARIANT_TRANSFER — seed a cold account from proven donors
    setup_state: str = "NOT_CONFIGURED"   # MOL-302: derived setup position (never persisted)
    setup_next: str = ""               # next operator action for the current setup_state
    half_live: bool = False            # D15/MOL-297: LIVE flag set but nothing routes live — warn, never solid-green LIVE
    half_live_hint: str = ""           # operator-facing explanation (names the ignored FANOPS_POSTER value)
    channels: list[ChannelReadiness] = field(default_factory=list)  # S04: per-(handle×platform) readiness matrix
    next_blocker: str = ""             # earliest first_blocker across channels; connect-first when none
    account_cards: list["AccountOnboardingCard"] = field(default_factory=list)  # U12: account-centric onboarding
                                       # cards — a display-only RE-PROJECTION of `channels` grouped by handle
                                       # (+ demoted). NEVER recomputes readiness; the template renders these.


@dataclass
class AccountOnboardingCard:
    """U12: one card per handle (active or demoted) for account-centric onboarding — a pure DISPLAY projection
    of channel_readiness re-grouped by account, so onboarding one account never means hopping stations. It
    NEVER recomputes readiness: `channels` is the subset of channel_readiness for this handle, `next_blocker`
    is that handle's WORST channel first_blocker (same _blocker_priority order the fleet next_blocker uses),
    `next_anchor` deep-links the CTA to the existing form that clears it, and `insights_rows` is the
    display-only IG-creds / TikTok-ceiling capability read (it must NOT feed ChannelReadiness.ready)."""
    account: GoLiveAccount            # active or demoted (the header + persona chip source)
    channels: list[ChannelReadiness]  # subset of channel_readiness for this handle ([] for a demoted account)
    next_blocker: str                 # worst-channel first_blocker (fleet order), "" when the account is ready
    next_anchor: str                  # in-page fragment id the single CTA deep-links to ("" when ready)
    insights_rows: list[dict]         # display-only: [{platform, ok, label}] — capability, NOT a readiness gate


def review_candidates(cfg: Config) -> list[dict]:
    """Track C: discover candidates awaiting approval — the top-level thumbnails `fanops discover`
    wrote into 00_review/ (the approved/ subdir is excluded; glob('*.jpg') matches top-level only).
    Lets the operator approve in the browser instead of dragging files in Finder; approving moves the
    thumbnail to 00_review/approved/ (actions.approve_candidate), then `fanops intake` copies the
    original into the inbox."""
    d = cfg.review
    if not d.exists():
        return []
    return [{"eid": p.stem} for p in sorted(d.glob("*.jpg"))]


# States the manual Publish tab surfaces — the by-hand-postable subset of actions._POSTABLE
# (queued is the norm; failed/error/needs_reconcile are recoverable posts the operator posts by hand).
# submitting/submitted are in-flight on a live backend, not a manual worklist item.
_MANUAL_QUEUE = {PostState.queued, PostState.needs_reconcile, PostState.failed, PostState.error}

def publish_queue(cfg: Config, *, now: Optional[datetime] = None,
                  account: Optional[str] = None) -> list[dict]:
    """Track B (manual / zero-dependency publishing): the worklist of `queued` posts the operator
    posts BY HAND. Each row carries the surface, caption, and the post id (Studio serves the clip at
    /media/<post_id>, marks it posted at /publish/posted/<post_id>). `due` = scheduled_time has
    passed. Due-first, then by schedule. Lock-free read; mutation is actions.mark_published. P5: an
    optional `account` filters the dict rows after the due-first sort (None default unchanged)."""
    now = now or datetime.now(timezone.utc)
    led = Ledger.load(cfg)
    rows = []
    for p in led.posts.values():
        if p.state not in _MANUAL_QUEUE:                 # every state mark_published accepts by hand
            continue
        due = False
        if p.scheduled_time:
            with fail_open("studio.views.publish_queue"):
                due = parse_iso(p.scheduled_time) <= now
        rows.append({"post_id": p.id, "clip_id": p.parent_id, "account": p.account,
                     "platform": p.platform.value, "caption": p.caption, "state": p.state.value,
                     "scheduled_time": p.scheduled_time, "due": due})
    # due-first; within a bucket by schedule. "9999" sentinel (not "") so a None/unscheduled post
    # sorts LAST, not as if it were the most urgent (ecc:python-review).
    rows.sort(key=lambda r: (not r["due"], r["scheduled_time"] or "9999"))
    if account is not None:        # P5: per-account filter on the dict rows
        rows = [r for r in rows if r["account"] == account]
    return rows


def pipeline_status(cfg: Config) -> dict:
    """Lock-free counts for the Run tab's status line: where the unit chain stands + how many gates
    are waiting + the active poster backend. Lets the operator see, in one glance, whether the next
    move is 'ingest', 'run a pass', or 'answer a gate'."""
    from collections import Counter
    from fanops.pipeline_status import PendingIndex, status_control_lines, source_backlog
    from fanops.pipeline_run import run_stage_snapshot
    led = led_for_request(cfg)
    idx = PendingIndex.build(cfg, led)
    run_line, wait_line = status_control_lines(cfg, led, idx)
    bl = source_backlog(led, cfg, idx)
    snap = run_stage_snapshot(cfg)
    by_kind = Counter(kind for _, kind, _ in idx.ordered)
    run_chip = f"{snap['stage']}:{snap['unit']}" if snap else None
    pending_unbound, queue_lines, held_pending = [], [], 0
    if cfg.queue_gate:
        for sid, s in sorted(led.sources.items()):
            if s.origin_kind != "native" or s.state is not SourceState.pending:
                continue
            if s.batch_id:
                held_pending += 1
            else:
                pending_unbound.append({"id": sid, "name": Path(s.source_path).name if s.source_path else sid,
                                        "duration": s.duration, "thumb_url": f"/source-thumb/{sid}"})
        by_batch: dict[str, list] = {}
        for sid, s in led.sources.items():
            if s.origin_kind == "native" and s.state is SourceState.pending and s.batch_id:
                by_batch.setdefault(s.batch_id, []).append(sid)
        for bid, sids in sorted(by_batch.items()):
            b = led.get_batch(bid)
            if not b:
                continue
            queue_lines.append({"batch_id": bid, "name": b.name, "sources": sorted(sids),
                                "target_accounts": b.target_accounts})
    # T2.3: machine-origin moment re-opens that request_moments parked instead of serving. Listed
    # unconditionally — the gate can be switched off AFTER a park, and a row nobody can release is
    # exactly the invisible backlog this ticket exists to end.
    pending_reopens = []
    for sid, s in sorted(led.sources.items()):
        parked = s.meta.get("pending_reopen")
        if isinstance(parked, dict):
            pending_reopens.append({"id": sid, "origin": parked.get("origin") or "",
                                    "requested_at": parked.get("requested_at") or ""})
    backlog_rows = []
    for r in bl.rows:
        row = {"id": r.id, "state": r.state, "bucket": r.bucket, "wait_line": r.wait_line,
               "block_reason": r.block_reason, "artifacts": r.artifacts}
        if snap and snap.get("unit") == r.id:
            row["active_stage"] = snap["stage"]
            row["stage_age"] = snap["stage_age"]
        backlog_rows.append(row)
    return {
        "sources": bl.actionable,   # in-progress pipeline work (NOT raw inventory — see source_backlog)
        "sources_blocked": bl.blocked_on_gates,
        "sources_recoverable": bl.recoverable,
        "sources_inventory": bl.inventory,
        "sources_held": bl.held,
        "native_total": bl.actionable + bl.blocked_on_gates + bl.recoverable + bl.inventory + bl.held,
        "pending_unbound": pending_unbound,
        "pending_unbound_count": len(pending_unbound),
        "queue_lines": queue_lines,
        "held_pending": held_pending,
        "pending_reopens": pending_reopens,
        "queue_gate": cfg.queue_gate,
        "backlog_rows": backlog_rows,
        "run_chip": run_chip,
        "third_party": sum(1 for s in led.sources.values() if s.origin_kind == "third_party"),
        "clips": len(led.clips), "posts": len(led.posts),
        "awaiting": awaiting_moment_count(led),   # S3: ACTIONABLE — MOMENTS (== Home/Review worklist), not raw posts
        "published": len(led.posts_in_state(PostState.published)),
        "holds": sum(1 for c in led.clips.values() if c.held),
        "pending_moments": by_kind.get("moments", 0),
        "pending_moment_hooks": by_kind.get("moment_hooks", 0),
        "pending_captions": by_kind.get("captions", 0),
        "run_line": run_line,
        "wait_line": wait_line,
        # R3-followup: the UI mode label MUST be the per-channel truth, not the legacy global. On a live
        # deployment with per-channel routing, cfg.poster_backend still reads 'dryrun' (the legacy
        # FANOPS_POSTER is the fallback bridge, not the per-channel source of truth) — surfacing it
        # printed 'dryrun' on a system that was actually publishing live, the UI lie that triggered this fix.
        # _publish_mode_label resolves to the distinct providers actually publishing (e.g. 'postiz, zernio'),
        # or 'dryrun' when cfg.is_live is False. ONE source for every status surface — no more divergence
        # between Home (which already used _publish_mode_label) and Make/Schedule/Publish (which used the
        # legacy global). hx-confirm gates that read `backend != 'dryrun'` still trigger when ANY channel
        # publishes live, which is the correct behavior (a live publish_now needs a confirm).
        "backend": _publish_mode_label(cfg),
        "accounts": [a.handle for a in Accounts.load(cfg).active()],   # Account-First: Run-form batch-target options
        "upload_max_bytes": cfg.upload_max_bytes,   # S02: chunked-upload JS intercept threshold (legacy single-shot uses the same cap)
        "errored": errored_sources(led),   # MOL-123: recoverable sources (error / moments_empty) for the Run-tab list
    }


_RECOVERABLE_SOURCE_STATES = (SourceState.error, SourceState.moments_empty)
def errored_sources(led: Ledger) -> list[dict]:
    """MOL-123: the recoverable-source rows for the Run tab — every source in error / moments_empty, with the
    FULL error_reason (the operator needs the exact failure, not a truncation) + filename + batch. Pure read;
    fail-open to [] on a torn row so it never 500s the panel."""
    out: list[dict] = []
    for s in led.sources.values():
        if s.state not in _RECOVERABLE_SOURCE_STATES:
            continue
        with fail_open("studio.views.errored_sources"):
            out.append({"id": s.id, "state": s.state.value, "error_reason": s.error_reason or "",
                        "batch_id": s.batch_id, "created_at": s.created_at,
                        "name": Path(s.source_path).name if s.source_path else s.id})
    return out


def run_next_step(status: dict) -> dict:
    """S3: the Make tab's ONE 'do this next' affordance, derived PURELY from pipeline_status counts (no ledger
    read; fail-open via .get so a torn/partial dict never raises). Gate ON: add → queue → make. Gate OFF:
    add → gate → prepare → review (gates PRECEDE review because a pending decision is BLOCKING mid-pipeline
    clips). Returns {key, label, hint}."""
    s = status if isinstance(status, dict) else {}
    def _n(k):
        try: return int(s.get(k, 0) or 0)
        except (TypeError, ValueError): return 0
    footage = _n("native_total") + _n("third_party")
    gates = _n("pending_moments") + _n("pending_moment_hooks") + _n("pending_captions")
    awaiting = _n("awaiting")
    if footage == 0:
        return {"key": "add", "label": "Add a video to begin",
                "hint": "Choose a file above, or paste a link under More — then tick accounts and Make clips."}
    recoverable = _n("sources_recoverable")
    if recoverable:
        label = f"{recoverable} source{'s' if recoverable != 1 else ''} need attention"
        return {"key": "recover", "label": label,
                "hint": "Open the source in Library to read the failure and resume or reset from there."}
    if s.get("queue_gate"):
        if _n("pending_unbound_count"):
            return {"key": "queue", "label": f"Make clips for {_n('pending_unbound_count')} pending file(s)",
                    "hint": "Tick the accounts you want, then Make clips."}
        if _n("held_pending"):
            return {"key": "make", "label": f"Make clips for {_n('held_pending')} queued file(s)",
                    "hint": "Make clips releases the queued file(s) and cuts until Review."}
        if _n("sources"):
            return {"key": "prepare", "label": "Run a pass",
                    "hint": "Cut clips and write captions for every released source — they'll land in Review."}
        return {"key": "add", "label": "Add more footage",
                "hint": "Upload or drop another file when you're ready."}
    if gates:
        hint = "Some clips are paused waiting on a decision. Answer them, then run Prepare again to finish those clips."
        if awaiting: hint += f" ({awaiting} clip(s) are also waiting in Review.)"
        return {"key": "gate", "label": f"Answer {gates} processing decision(s)", "hint": hint}
    if awaiting:
        return {"key": "review", "label": f"{awaiting} clip(s) ready",
                "hint": "Review and approve them in the Review tab — nothing ships until you do."}
    return {"key": "prepare", "label": "Run a pass",
            "hint": "Cut clips and write captions for every account — they'll land in Review."}


def asset_catalog(cfg: Config) -> dict:
    """Lock-free read-model for the Library tab (M1): every remembered Source split by origin_kind, with
    just-enough metadata to recognize it. Fail-open — a torn/absent ledger yields empty lists, never a
    500 (the Studio invariant)."""
    try:                                             # whole body guarded: a torn row must not 500 either
        led = Ledger.load(cfg)
        from fanops.pipeline_status import source_backlog
        bl = source_backlog(led, cfg)
        from fanops.pipeline_run import run_stage_snapshot
        run_active = run_stage_snapshot(cfg) is not None
        by_id = {r.id: r for r in bl.rows}
        from fanops.models import SourceState
        rows = [{"id": s.id, "origin_kind": s.origin_kind, "state": s.state.value,
                 "bucket": by_id[s.id].bucket if s.id in by_id else "inventory",
                 "wait_line": by_id[s.id].wait_line if s.id in by_id else None,
                 "block_reason": by_id[s.id].block_reason if s.id in by_id else None,
                 "artifacts": by_id[s.id].artifacts if s.id in by_id else None,
                 "retire_preview": (led.preview_retire_cascade(s.id) if s.origin_kind == "native"
                                    and s.state is not SourceState.retired else None),
                 "name": Path(s.source_path).name if s.source_path else s.id,   # P6: human filename, not the opaque id
                 "duration": s.duration, "width": s.width, "height": s.height,
                 "degraded_reason": s.degraded_reason} for s in led.sources.values()]   # RF1: the VISIBLE-degradation channel (probe_failed) -> a Library marker, else a 0×0 source silently renders a mangled clip
        return {"native": [r for r in rows if r["origin_kind"] == "native"],
                "third_party": [r for r in rows if r["origin_kind"] == "third_party"],
                "backlog": {"actionable": bl.actionable, "blocked_on_gates": bl.blocked_on_gates,
                            "recoverable": bl.recoverable, "inventory": bl.inventory},
                "run_active": run_active}
    except Exception as exc:                          # invariant: the Library tab must never 500 — but
        from fanops.log import get_logger             # a read-fail is RECORDED, never silently shown as "empty"
        get_logger(cfg)("library", "-", "error", err=str(exc)[:160])
        return {"native": [], "third_party": [], "backlog": {"actionable": 0, "blocked_on_gates": 0,
                                                            "recoverable": 0, "inventory": 0}, "run_active": False}


def pending_stitches(cfg: Config) -> list:
    """Lock-free read-model for the Stitches tab (M3): the SUGGESTED stitch_plans awaiting operator
    approval. Fail-open — a torn/absent ledger yields [] (and logs), never a 500 (the Studio invariant)."""
    try:
        led = Ledger.load(cfg)
        rows = [{"id": p.id, "clip_id": p.clip_id, "strategy_key": p.strategy_key,
                 "asset_ids": p.asset_ids, "state": p.state.value,
                 "rank_score": p.rank_score, "rationale": p.rationale}      # M5: the routine-loop's WHY + fit
                for p in led.stitch_plans.values() if p.state is StitchState.suggested]
        # best-fit first (highest rank_score); a None rank sinks to the bottom; tie -> stable by id
        rows.sort(key=lambda r: (-(r["rank_score"] or 0.0), r["id"]))
        return rows
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("stitches", "-", "error", err=str(exc)[:160])
        return []


def pending_stitch_drafts(cfg: Config) -> list:
    """Lock-free read-model for the Stitches tab (M4): rendered `stitch_draft` clips awaiting the operator's
    RELEASE (the second gate — approved plans render into these unpostable drafts; releasing one makes it
    crosspost-eligible). Fail-open — a torn/absent ledger yields [], never a 500 (the Studio invariant)."""
    try:
        led = Ledger.load(cfg)
        return [{"id": c.id, "parent_id": c.parent_id, "aspect": c.aspect.value}
                for c in led.clips.values() if c.state is ClipState.stitch_draft]
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("stitches", "-", "error", err=str(exc)[:160])
        return []


@dataclass
class PersonaCard:
    """A2: one first-class Persona for the Personas page — editable fields + linked accounts.
    Posted hashtags are the source lock, not `corpus`. NO secret."""
    id: str
    name: str
    voice: str
    corpus: list                       # leftover Persona.hashtag_corpus field; not the caption menu
    niche: list                        # declared territory (identity lever; not caption tags)
    linked_handles: list               # accounts whose persona_id points at this persona
    discovery_roots: list = field(default_factory=list)  # unused; vocab expander is deleted
    reach_tags: list = field(default_factory=list)   # leftover cache overlay on corpus; not membership
    reach_means: dict = field(default_factory=dict)  # leftover {corpus tag -> media_count}
    # Lever engine: the per-characteristic levers + the COMPOSED instruction the pipeline will read
    # ("what the AI will read") — so the operator sees their config's exact downstream effect on the card.
    content_focus: Optional[str] = None              # MOL-523: free-text editorial focus (was the token multi-select)
    cut_policy: list = field(default_factory=list)    # MOL-523: the moment-kind tokens that DERIVE cut length+framing
    selection_scope: Optional[str] = None
    hook_angle: Optional[str] = None
    intensity: Optional[str] = None
    clip_profile: Optional[str] = None
    framing: Optional[str] = None
    instruction: str = ""              # the COMPILED casting directive (the headline "AI reads ->")
    # TRANSPARENCY facts (length band + lead tags) derived from the REAL resolvers — so the operator sees, on
    # the card, exactly what the config produces.
    length_band: str = ""
    lead_tags: list = field(default_factory=list)
    # M3 DIRECTIVE ENGINE: the COMPILED per-dimension directive the LLM actually reads (so the operator sees
    # exactly what each lever produces). (M3e: the freeform OVERRIDE text fields were retired with the levers.)
    hook_text: str = ""
    caption_text: str = ""
    # M4: the LEVER MANIFEST — per editable lever {key,label,channels,value,produces,source,health}, derived
    # from the registry + the SAME resolvers the pipeline runs (no-drift). The drawer renders it as a health row.
    lever_manifest: list = field(default_factory=list)
    # S05: drawer-only effective-persona read projection (fail-open defaults).
    account_provenance: list = field(default_factory=list)   # [{handle, fields:[{name, value, source}]}]
    lever_detail: list = field(default_factory=list)         # [{key, label, value, catalog_does, option_effect, produces, health, crosswalk_note}]
    corpus_tags: list = field(default_factory=list)          # unused; templates do not render corpus chips
    corpus_refreshed_at: str = ""                            # unused; Layer B derive is deleted


def _account_provenance(cfg: Config, persona, handles: list) -> list:
    """Per linked account, mirror accounts._hydrate_from_personas field sourcing for the drawer provenance table."""
    if not handles:
        return []
    try:
        from fanops.accounts import Account
        from fanops.models import validate_account_handle
        from fanops.personas import resolved_cut_spec
        raw = json.loads(cfg.accounts_path.read_text(encoding="utf-8"))
        rows = {}
        for r in raw.get("accounts", []):
            if not isinstance(r, dict): continue
            try: rows[validate_account_handle(r.get("handle") or "")] = r
            except ValueError: continue
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("personas", getattr(persona, "id", "-"), "provenance_error", err=str(exc)[:160])
        return []
    _prof, _fr = resolved_cut_spec(persona)
    out: list = []
    for h in handles:
        row = rows.get(h) or rows.get("@" + h.lstrip("@"))
        if not row: continue
        acc = Account(**row)
        fields: list = []
        if (persona.voice or "").strip():
            fields.append({"name": "voice", "value": persona.voice.strip(), "source": "persona"})
        else:
            fields.append({"name": "voice", "value": acc.persona or "", "source": "account"})
        fields.append({"name": "hashtag_corpus", "value": list(persona.hashtag_corpus), "source": "persona"})
        # MOL-523: content_focus is free TEXT; the token list that derives the cut moved to cut_policy.
        fields.append({"name": "content_focus", "value": persona.content_focus or "", "source": "persona"})
        fields.append({"name": "cut_policy", "value": list(persona.cut_policy or []), "source": "persona"})
        fields.append({"name": "intensity", "value": persona.intensity or "", "source": "persona"})
        fields.append({"name": "selection_scope", "value": persona.selection_scope or "", "source": "persona"})
        fields.append({"name": "hook_angle", "value": persona.hook_angle or "", "source": "persona"})
        if _prof:
            fields.append({"name": "clip_profile", "value": _prof, "source": "persona"})
        else:
            fields.append({"name": "clip_profile", "value": acc.clip_profile or "", "source": "account"})
        if _fr:
            fields.append({"name": "framing", "value": _fr, "source": "persona"})
        else:
            fields.append({"name": "framing", "value": acc.framing or "", "source": "account"})
        out.append({"handle": h, "fields": fields})
    return out


def _lever_detail_rows(cfg: Config, persona, manifest_rows: list, catalog: list, effects: dict) -> list:
    """Join manifest rows with lever_catalog does + _LEVER_EFFECTS option effects + optional archetype crosswalk."""
    xnote = ""
    try:
        from fanops.lever_docs import archetype_crosswalk_rows
        xw = next((r for r in archetype_crosswalk_rows() if r["id"] == persona.id), None)
        if xw:
            foc = ", ".join(xw["content_focus"]) if xw["content_focus"] else "—"
            xnote = f"{xw['name']}: {foc} · {xw['hook_angle']} · {xw['selection_scope']} · {xw['intensity']}"
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("personas", getattr(persona, "id", "-"), "crosswalk_error", err=str(exc)[:160])
        xnote = ""
    cat_by = {lv["key"]: lv for lv in (catalog or [])}
    out: list = []
    for i, row in enumerate(manifest_rows or []):
        try:
            key = row.get("key") or ""
            cat = cat_by.get(key, {})
            val = row.get("value")
            eff_map = (effects or {}).get(key) or {}
            if isinstance(val, list):    # MOL-523: the multi-value lever is cut_policy now, not content_focus
                parts = [eff_map[v] for v in val if v in eff_map]
                opt_eff = " · ".join(parts) if parts else "—"
            elif isinstance(val, str) and val.strip():
                opt_eff = eff_map.get(val.strip()) or "—"
            else:
                opt_eff = "—"
            produces = row.get("produces")
            if isinstance(produces, list):
                prod = " ".join(str(x) for x in produces if x) or "—"
            else:
                prod = produces or "—"
            out.append({"key": key, "label": row.get("label") or key, "value": val,
                          "catalog_does": cat.get("does") or "—", "option_effect": opt_eff,
                          "produces": prod, "health": row.get("health") or "—",
                          "crosswalk_note": xnote if i == 0 else ""})
        except Exception as exc:
            from fanops.log import get_logger
            get_logger(cfg)("personas", getattr(persona, "id", "-"), "lever_detail_error",
                           key=row.get("key", ""), err=str(exc)[:160])
            out.append({"key": row.get("key", ""), "label": row.get("label", ""), "value": "—",
                          "catalog_does": "—", "option_effect": "—", "produces": "—", "health": "—",
                          "crosswalk_note": ""})
    return out


@dataclass
class PersonaAccountLink:
    """A2: one account row for the Personas "connect" section — its current persona link (or None), so
    the operator can connect/disconnect each account to a persona from a dropdown."""
    handle: str
    persona_id: Optional[str]


@dataclass
class PersonasPage:
    personas: list                     # PersonaCard
    accounts: list                     # PersonaAccountLink


def personas_page(cfg: Config, *, led: Optional[Ledger] = None) -> "PersonasPage":
    """The Personas-page read-model: every persona as a card (linked account handles + levers)
    + every account's current persona link (connect dropdown). Posted hashtags are the source lock.
    Fail-open: a corrupt personas.json / accounts.json -> an EMPTY page (the surface never 500s),
    mirroring golive_accounts. `led` is accepted for call-compat; the surface reads no ledger."""
    try:
        from fanops.personas import (Personas, compose_persona_instruction, persona_facts,   # lazy: personas imports accounts (in migrate) -> avoid a load cycle
                                     hook_directive, caption_directive, resolved_cut_spec, manifest)
        reg = Personas.load(cfg)
        accts = Accounts.load(cfg).accounts
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("personas", "-", "read_error", err=str(exc)[:160])
        return PersonasPage(personas=[], accounts=[])
    by_pid: dict = {}
    for a in accts:
        if getattr(a, "persona_id", None):
            by_pid.setdefault(a.persona_id, []).append(a.handle)
    # Surface each corpus BIGGEST FIRST (`ranked_tags` is size-first — MOL-692) and flag the tags that
    # actually carry a measurement. A corpus tag should always be measured — it can only have entered by
    # being measured — but a cache entry can expire between derivations, so the ★ still gates on a present
    # measurement rather than assuming it.
    from fanops.hashtags import load_measurements, ranked_tags, _norm, tag_size
    cache = load_measurements(cfg)
    store = ranked_tags(cache) or None
    rank = {t: i for i, t in enumerate(store or [])}
    means = {t: tag_size(r) for t, r in cache.items() if tag_size(r) is not None}
    def _ranked(corpus):
        return sorted((_norm(t) for t in corpus), key=lambda n: rank.get(n, 10 ** 6))
    from fanops.personas import lever_catalog
    _cat = lever_catalog()
    _fx = {lv["key"]: {o["value"]: o["effect"] for o in lv["options"]} for lv in _cat}
    cards: list = []
    for p in reg.all():
        facts = persona_facts(cfg, p)
        mf = manifest(cfg, p)
        try:
            acct_prov = _account_provenance(cfg, p, by_pid.get(p.id, []))
        except Exception as exc:
            from fanops.log import get_logger
            get_logger(cfg)("personas", p.id, "provenance_error", err=str(exc)[:160])
            acct_prov = []
        lev_detail = _lever_detail_rows(cfg, p, mf, _cat, _fx)
        cards.append(PersonaCard(id=p.id, name=p.name, voice=p.voice,
                         corpus=_ranked(p.hashtag_corpus), niche=list(p.niche),
                         discovery_roots=[],
                         linked_handles=by_pid.get(p.id, []),
                         reach_tags=[_norm(t) for t in p.hashtag_corpus if _norm(t) in means],
                         reach_means={_norm(t): means[_norm(t)] for t in p.hashtag_corpus if _norm(t) in means},
                         content_focus=p.content_focus, cut_policy=list(p.cut_policy or []),
                         selection_scope=p.selection_scope, hook_angle=p.hook_angle,
                         intensity=p.intensity,
                         clip_profile=resolved_cut_spec(p)[0], framing=facts["framing"],
                         instruction=compose_persona_instruction(p),
                         length_band=facts["length_band"], lead_tags=facts["lead_tags"],
                         hook_text=str(hook_directive(p)), caption_text=caption_directive(p),
                         lever_manifest=mf, account_provenance=acct_prov, lever_detail=lev_detail))
    links = [PersonaAccountLink(handle=a.handle, persona_id=getattr(a, "persona_id", None)) for a in accts]
    return PersonasPage(personas=cards, accounts=links)


def golive_accounts(cfg: Config) -> list[GoLiveAccount]:
    """The active accounts as a per-channel read-model, SHARED by golive_status + home_status so the two
    surfaces never drift on what "connected" means. One GoLiveChannel per platform; integration_id is the
    effective per-platform id (integrations[platform] -> account_id fallback -> "" unmapped). Fail-open: a
    malformed accounts.json logs accounts_error and degrades to [] (the surface never 500s). NO secret."""
    try:
        return [GoLiveAccount(
            handle=a.handle, persona=a.persona,
            persona_id=getattr(a, "persona_id", None),     # S8: the linked first-class Persona (badge), additive
            ig_user_id=(a.ig_user_id or ""),               # per-account Meta id (non-secret) — render current value
            meta_token_set=cfg.meta_token_set_for(a.handle),  # BOOL only; token is SECRET
            channels=[GoLiveChannel(platform=p.value,
                                    integration_id=a.integrations.get(p.value) or a.account_id or "",
                                    backend=a.backends.get(p.value) or "")
                      for p in a.platforms])
            for a in Accounts.load(cfg).active()]
    except Exception as exc:
        from fanops.log import get_logger             # ECC fix #5: a disk/parse error was invisible
        get_logger(cfg)("golive", "-", "accounts_error", err=str(exc)[:160])
        return []                                     # malformed accounts.json — doctor's readiness check names it


def golive_demoted_accounts(cfg: Config) -> list:
    """Phase 3: the PLANNED (demoted / never-activated) accounts as a read-model so Go-Live can render them with
    a Promote button — golive_accounts lists only active(), so a demote was a silent one-way door. Fail-open -> []
    on a malformed accounts.json (mirrors golive_accounts)."""
    try:
        return [GoLiveAccount(
            handle=a.handle, persona=a.persona,
            persona_id=getattr(a, "persona_id", None),     # S8: the linked first-class Persona (badge), additive
            ig_user_id=(a.ig_user_id or ""),               # U12: parity with golive_accounts so a demoted IG card
            meta_token_set=cfg.meta_token_set_for(a.handle),  # can still show its insights row (BOOL only; SECRET)
            channels=[GoLiveChannel(platform=p.value,
                                    integration_id=a.integrations.get(p.value) or a.account_id or "",
                                    backend=a.backends.get(p.value) or "")
                      for p in a.platforms])
            for a in Accounts.load(cfg).accounts if a.status.value == "planned"]
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("golive", "-", "accounts_error", err=str(exc)[:160])
        return []


def _publish_mode_label(cfg: Config) -> str:
    """Thin delegate to cfg.effective_publish_mode (UI-LIE-FIX root: the truth lives on Config so
    every caller — display, hx-confirm, friendly error — reads the SAME source). Kept as the
    historical helper name for the call sites that already use it."""
    return cfg.effective_publish_mode()


def _postiz_down_on_helper_raise(cfg: Config) -> dict:
    """Outer except for postiz_health_for_banner: show unknown if a channel routes to postiz OR the
    route-check failed; hide only when we can prove no channel routes to postiz. Lives here so we
    never re-touch publish-hot views_common for a strip-only raise shield (MOL-963 R2d)."""
    unknown = {"show": True, "danger": False, "status": None,
               "hint": "Postiz health unknown (read failed)"}
    try:
        from fanops.accounts import load_accounts_safe
        accounts, err = load_accounts_safe(cfg)
        if err:
            return {**unknown, "hint": "Postiz health unknown (route check failed)"}
        for a in accounts.active():
            for plat in a.platforms:
                if accounts.effective_provider(a.handle, plat) == "postiz":
                    return unknown
        return {"show": False, "danger": False, "status": None, "hint": ""}
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("system_strip", "-", "postiz_route_check_error", err=str(exc)[:160])
        return {**unknown, "hint": "Postiz health unknown (route check failed)"}


def build_system_strip(cfg: Config) -> dict:
    """Global system strip read-model: LIVE/DRYRUN mode + blocked gate count + failed-post alert. Health dots lazy-load via htmx."""
    from fanops.log import get_logger                     # a strip sub-read failure is RECORDED, never a silently-zeroed badge
    from fanops.health import SnapshotFreshness, read_strip_metrics
    strip_metrics_unknown = False
    try:
        sr = read_strip_metrics(cfg)
        if sr.freshness is SnapshotFreshness.FRESH and isinstance(sr.data, dict):
            m = sr.data
            blocked = int(m.get("blocked_gates") or 0)
            recoverable = int(m.get("recoverable_sources") or 0)
            errored_first_id = m.get("errored_first_id")
            failed = int(m.get("failed") or 0)
        else:
            # Missing/stale/unreadable → unknown, never calm zero. Do not Ledger.load on the strip path.
            strip_metrics_unknown = True
            blocked = None; recoverable = 0; errored_first_id = None; failed = 0
    except Exception as exc:
        get_logger(cfg)("system_strip", "-", "pipeline_status_error", err=str(exc)[:160])
        strip_metrics_unknown = True
        blocked = None; recoverable = 0; errored_first_id = None; failed = 0
    errored = recoverable
    # Leg 2 (Insight): the one external gate — a persisted breadcrumb means Graph media-insights was refused
    # for lack of instagram_manage_insights, so IG performance is frozen at its last snapshot until granted.
    try:
        from fanops.meta_graph import insights_blocked_signal
        insights_blocked = insights_blocked_signal(cfg)
    except Exception as exc:
        get_logger(cfg)("system_strip", "-", "insights_blocked_error", err=str(exc)[:160])
        insights_blocked = False
    try:
        from fanops.health_model import half_live_state
        hl = half_live_state(cfg)
        half_live, half_live_hint = hl.is_half_live, (hl.hint or "")
    except Exception as exc:
        get_logger(cfg)("system_strip", "-", "half_live_error", err=str(exc)[:160])
        half_live, half_live_hint = True, "readiness unavailable — not confirmed LIVE"
    # D13b: Postiz-down banner — snapshot-only (deps_health.json).
    # Helper raise → unknown when a channel routes to postiz OR the route-check itself failed; else hide.
    try:
        postiz_down = views_common.postiz_health_for_banner(cfg)
    except Exception as exc:
        get_logger(cfg)("system_strip", "-", "postiz_down_error", err=str(exc)[:160])
        postiz_down = _postiz_down_on_helper_raise(cfg)
    return {"is_live": cfg.is_live, "mode": _publish_mode_label(cfg), "blocked_gates": blocked,
            "strip_metrics_unknown": strip_metrics_unknown,
            "recoverable_sources": recoverable, "failed": failed, "insights_blocked": insights_blocked,
            "errored_sources": errored, "errored_first_id": errored_first_id,
            "half_live": half_live, "half_live_hint": half_live_hint,
            "postiz_down": postiz_down}




def resolve_account_handle(raw: str, cfg: Config) -> str:
    """Map ?account= to the canonical ledger/accounts handle (@-agnostic)."""
    raw = (raw or "").strip()
    if not raw:
        return raw
    from fanops.models import validate_account_handle
    try:
        bare = validate_account_handle(raw)
    except ValueError:
        bare = raw.lstrip("@").lower()
    with fail_open("studio.views.resolve_account_handle"):
        for a in Accounts.load(cfg).active():
            if a.handle == bare:
                return a.handle
    return raw  # unknown handle — preserve operator input for empty-state copy


def schedule_auto_ship(cfg: Config) -> bool:
    """Live + daemon alive — Schedule is read-only; posts ship on the clock."""
    if not cfg.is_live:
        return False
    dh = daemon_health(cfg)
    return bool(dh and dh.get("verdict") == "alive")

def daemon_health(cfg: Config) -> Optional[dict]:
    """Fail-open liveness of the launchd PIPELINE DRIVER for the Home banner. Returns daemon.status()'s
    verdict dict (loaded/pid/last_exit/heartbeat_age_s/verdict), or None when it can't be judged — non-darwin,
    launchctl absent, or any error — so Home never 500s and a non-mac dev box shows no false alarm. The
    detection already exists in daemon.status(); this only SURFACES it where the operator looks. Lazy import
    keeps the launchd/subprocess dependency off the core view path; htmx-loaded on-demand (mirrors
    /golive/health) so it never runs a subprocess on the spine's every-surface home_status read.

    Enriched with `interval`/`pending_gates` so the banner can frame a NOT-INSTALLED driver as OPT-IN
    rather than a fault; gates are always answered by the LLM, so there is no AI on/off to disclose."""
    with fail_open("studio.views.daemon_health"):
        from fanops import daemon
        from fanops import pipeline
        interval = daemon.installed_interval(cfg) or 600
        rep = daemon.status(cfg, interval=interval)
        pending_gates = None                                   # never let a torn agent_io dir 500 the banner
        with fail_open("studio.views.daemon_health.pending_gates"):
            pending_gates = pipeline.pending_gate_count(cfg)   # need-aware truth: claude runs ONLY to answer these
        siblings = daemon.sibling_agents_status()
        from fanops.pipeline_run import run_status_line
        out = {**rep, "interval": interval,
               "pending_gates": pending_gates, "siblings": siblings,
               "responder": daemon.resolve_responder(cfg)}
        run_line = run_status_line(cfg)
        if run_line != "run=idle":
            out["run_line"] = run_line
        return out
    return None


def daemon_health_strip(cfg: Config) -> Optional[dict]:
    """Home daemon partial — snapshot facts + live heartbeat/activity via project_daemon_strip."""
    from fanops.health import SnapshotFreshness, read_daemon_strip_snapshot
    from fanops.health_model import heartbeat_stale, project_daemon_strip, daemon_progress
    from fanops import pipeline
    from fanops.pipeline_run import run_status_line
    sr = read_daemon_strip_snapshot(cfg)
    run_line = run_status_line(cfg)
    alive_mid, _, _ = daemon_progress(cfg)
    live_activity = bool(alive_mid) or bool(run_line and run_line != "run=idle")
    if sr.freshness in (SnapshotFreshness.MISSING, SnapshotFreshness.UNREADABLE) and not live_activity:
        return {"verdict": "unknown", "installed": False, "loaded": False,
                "hint": f"daemon strip snapshot {sr.freshness.value}"}
    snap = dict(sr.data) if isinstance(sr.data, dict) else {}
    pending_gates = None
    with fail_open("studio.views.daemon_health_strip.pending_gates"):
        pending_gates = pipeline.pending_gate_count(cfg)
    age, stale, _iv = heartbeat_stale(cfg, interval=snap.get("interval") or 600)
    return project_daemon_strip(
        snap, age=age, stale=stale, pending_gates=pending_gates,
        run_line=run_line, alive_mid=alive_mid)



def _blocker_priority(msg: str) -> int:
    if msg == "connect Postiz or Zernio first": return 0
    if msg == "map an integration id": return 1
    if msg == "route to a scheduler backend": return 2
    if msg.startswith("connect ") and "first (set " in msg: return 3
    if msg == "link a persona": return 4
    return 99


def _channel_first_blocker(cfg: Config, *, mapped: bool, has_integ: bool, has_backend: bool,
                           backend: str, persona_ok: bool) -> str:
    if not cfg.postiz_api_key and not cfg.zernio_api_key:
        return "connect Postiz or Zernio first"
    if not mapped:
        return "map an integration id"
    if has_integ and not has_backend:
        return "route to a scheduler backend"
    if has_backend and not has_integ:
        return "map an integration id"
    if backend:
        from fanops.config import _LIVE_BACKENDS
        if backend in _LIVE_BACKENDS and not cfg.backend_has_creds(backend):
            need = {"zernio": "ZERNIO_API_KEY", "postiz": "POSTIZ_API_KEY"}.get(backend, "the backend's API key")
            return f"connect {backend} first (set {need})"
    if cfg.account_casting and not persona_ok:
        return "link a persona"
    return ""


def channel_readiness(cfg: Config) -> list[ChannelReadiness]:
    """S04: per-(handle×platform) readiness for the Go-Live matrix — mirrors go_live gates without mutating."""
    from fanops.models import Platform
    try:
        accounts = Accounts.load(cfg)
        live_ready = set(accounts.live_ready_channels())
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("golive", "-", "channel_readiness_error", err=str(exc)[:160])
        return []
    out: list[ChannelReadiness] = []
    for a in accounts.active():
        persona_ok = bool((a.persona_id or "").strip() or (a.persona or "").strip())
        for p in a.platforms:
            pv = p.value
            mapped = bool(a.integrations.get(pv) or a.account_id)
            has_integ = bool(a.integrations.get(pv))
            has_backend = bool(a.backends.get(pv))
            backend = accounts.effective_provider(a.handle, Platform(p)) or ""
            creds = bool(backend and cfg.backend_has_creds(backend))
            r2 = (has_integ and not has_backend) or (has_backend and not has_integ)
            in_live = (a.handle, pv, backend) in live_ready if backend else False
            ready = in_live and not r2 and (not cfg.account_casting or persona_ok)
            blocker = _channel_first_blocker(cfg, mapped=mapped, has_integ=has_integ, has_backend=has_backend,
                                             backend=backend, persona_ok=persona_ok)
            if blocker:
                ready = False
            out.append(ChannelReadiness(handle=a.handle, platform=pv, backend=backend, mapped=mapped, creds=creds,
                                        persona=persona_ok, window=True, ready=ready, first_blocker=blocker))
    return out


def _next_blocker(channels: list[ChannelReadiness]) -> str:
    if not channels:
        return "connect Postiz or Zernio first"
    blockers = [c.first_blocker for c in channels if c.first_blocker]
    if not blockers:
        return ""
    return min(blockers, key=_blocker_priority)


def _card_worst_blocker(channels: list[ChannelReadiness]) -> str:
    """The WORST (highest-priority) first_blocker across ONE handle's channels — the card's single CTA. Uses the
    SAME _blocker_priority ordering the fleet _next_blocker uses (never a new order). "" when every channel is
    ready. An empty ladder (a demoted account has no channel_readiness rows) has no publish blocker -> ""."""
    blockers = [c.first_blocker for c in channels if c.first_blocker]
    return min(blockers, key=_blocker_priority) if blockers else ""


def _card_next_anchor(handle: str, blocker: str, channels: list[ChannelReadiness]) -> str:
    """Map a card's worst blocker to the in-page fragment id of the EXISTING form that clears it (NO new route).
    Matched on the exact strings _channel_first_blocker returns; the map/backend anchors point at the FIRST
    channel of this handle whose own first_blocker is that phrase (the deep-linked form id in the card body)."""
    if not blocker:
        return ""
    if blocker == "connect Postiz or Zernio first" or (blocker.startswith("connect ") and "first (set " in blocker):
        return "#golive-connect"
    if blocker == "map an integration id":
        ch = next((c for c in channels if c.first_blocker == blocker), None)
        return f"#map-{handle}-{ch.platform}" if ch else "#golive-connect"
    if blocker == "route to a scheduler backend":
        ch = next((c for c in channels if c.first_blocker == blocker), None)
        return f"#backend-{handle}-{ch.platform}" if ch else "#golive-connect"
    if blocker == "link a persona":
        return f"#persona-{handle}"
    return "#golive-connect"


def _card_insights_rows(account: "GoLiveAccount") -> list[dict]:
    """DISPLAY-ONLY per-platform insights capability for a card — it MUST NOT feed ChannelReadiness.ready (the
    live-arming gate is publish-only). instagram: ✓ when the handle carries BOTH its own ig_user_id and a
    per-handle Graph token (meta_token_set); ✗ names the Meta-creds fix (MOL-112 blindness made visible).
    tiktok: always ✗ — Zernio exposes publish but no per-account insights parity (red-flag 3). youtube/other:
    OMITTED entirely (no false parity). Order follows the account's channels."""
    rows: list[dict] = []
    for ch in account.channels:
        p = ch.platform
        if p == "instagram":
            ok = bool((account.ig_user_id or "").strip() and account.meta_token_set)
            label = ("Instagram insights connected" if ok
                     else "Connect Meta creds for Instagram insights (metrics + followers blind otherwise)")
            rows.append({"platform": p, "ok": ok, "label": label})
        elif p == "tiktok":
            rows.append({"platform": p, "ok": False, "label": "Insights not available via Zernio"})
        # youtube / any other platform: no insights row (no false IG-parity)
    return rows


def onboarding_account_cards(cfg: Config) -> list[AccountOnboardingCard]:
    """U12: the account-centric onboarding read-model — one AccountOnboardingCard per handle (active first,
    then demoted). A pure DISPLAY projection: it RE-GROUPS channel_readiness(cfg) by handle (never a second
    readiness computation), picks that handle's worst-channel blocker as the single CTA (fleet order), maps it
    to the existing form's anchor, and attaches the display-only insights rows. Demoted accounts have no
    channel_readiness rows (only active() is projected) -> empty ladder + a promote CTA in the template."""
    chans = channel_readiness(cfg)
    by_handle: dict[str, list[ChannelReadiness]] = {}
    for c in chans:
        by_handle.setdefault(c.handle, []).append(c)
    cards: list[AccountOnboardingCard] = []
    for acct in golive_accounts(cfg):                     # active accounts, in accounts.json order
        ladder = by_handle.get(acct.handle, [])
        blocker = _card_worst_blocker(ladder)
        cards.append(AccountOnboardingCard(
            account=acct, channels=ladder, next_blocker=blocker,
            next_anchor=_card_next_anchor(acct.handle, blocker, ladder),
            insights_rows=_card_insights_rows(acct)))
    for acct in golive_demoted_accounts(cfg):             # demoted (planned) accounts — promotable, empty ladder
        cards.append(AccountOnboardingCard(
            account=acct, channels=[], next_blocker="", next_anchor="",
            insights_rows=_card_insights_rows(acct)))
    return cards


def golive_status(cfg: Config) -> GoLiveStatus:
    """Lock-free read-model for the Go-Live tab: the publish mode (dryrun/live), whether Postiz is
    configured (postiz_url is shown — it is NON-secret; key_set is a BOOL only, the key itself is never
    exposed), the ACTIVE accounts to map, and doctor readiness via health_model projectors (MOL-965 WP2).

    Accounts are listed PER-CHANNEL: each active handle carries one GoLiveChannel per platform, because a
    handle's Instagram and TikTok are DIFFERENT Postiz integrations (M1). Each channel's integration_id is
    the effective current id — the per-platform integrations[platform], else the shared account_id
    fallback, else "" (unmapped). Tolerates a malformed accounts.json (falls back to an empty list) so the
    tab never 500s. Readiness checks/notes/half-live come from build_health_report → project_golive_readiness
    (no second doctor assembly, no parallel half-live compute)."""
    from fanops.health_model import build_health_report, project_golive_readiness
    accts = golive_accounts(cfg)                      # shared helper (single source of truth for the accounts read-model)
    try:
        ready = project_golive_readiness(build_health_report(cfg))
    except Exception as exc:                          # invariant: the Go-Live tab must never 500 (ecc:python-review)
        from fanops.log import get_logger             # ECC fix #5: log why readiness is unavailable
        get_logger(cfg)("golive", "-", "doctor_error", err=str(exc)[:160])
        # Never fail-open to calm LIVE-looking half_live=False (MOL-965 WP2-fix).
        ready = {"checks": [], "notes": ["readiness check unavailable"],
                 "half_live": True,
                 "half_live_hint": "readiness unavailable — not confirmed LIVE"}
    from fanops.validation_gate import learning_validated
    from fanops.doctor import setup_state, setup_next_action
    from fanops.pipeline_run import paused as _paused
    chans = channel_readiness(cfg)
    return GoLiveStatus(
        mode=_publish_mode_label(cfg),               # provider-aware (M3); 'dryrun' when not live
        is_live=cfg.is_live,
        half_live=ready["half_live"], half_live_hint=ready["half_live_hint"],
        postiz_url=cfg.postiz_url,                    # non-secret; shown so the operator can confirm config
        key_set=cfg.postiz_api_key is not None,       # BOOL only — the API key value is NEVER exposed
        zernio_key_set=cfg.zernio_api_key is not None,  # Zernio slice 4: BOOL only (connect-block state)
        accounts=accts,
        channels=chans, next_blocker=_next_blocker(chans),
        account_cards=onboarding_account_cards(cfg),   # U12: account-centric cards (display re-projection of channels)
        checks=ready["checks"],
        notes=ready["notes"],
        learning_validated=learning_validated(cfg),    # M3: shows whether the loop is unfrozen (cutover done)
        account_casting=cfg.account_casting,           # per-account moment casting toggle state (persona diff)
        clip_profile=cfg.clip_profile,                 # clip-length band (talk/song)
        responder_mode=cfg.responder_mode,             # always 'llm' now (validate-or-refuse); gates answered by the LLM
        llm_transport=cfg.llm_transport, llm_cli_binary=cfg.llm_cli_binary,
        daemon=daemon_health(cfg),                     # launchd driver health for the Go-Live daemon control (None off-darwin)
        paused=_paused(cfg),                           # 00_control/paused — operator brake on the unattended pump
        demoted=golive_demoted_accounts(cfg),          # Phase 3: promotable planned accounts
        variant_learning=cfg.variant_learning,         # Phase 6: A/B learning-loop intent flags (default OFF)
        variant_amplify=cfg.variant_amplify, variant_ucb=cfg.variant_ucb, variant_transfer=cfg.variant_transfer,
        learn_amplify=cfg.learn_amplify,               # the learn-pass amplify intent flag (unattended minter)
        learn_retire=cfg.learn_retire,                 # the learn-pass retire intent flag (unattended destroyer)
        setup_state=setup_state(cfg), setup_next=setup_next_action(cfg))


def gate_rows(cfg: Config) -> list[dict]:
    """Lock-free read-model for the Gates tab (Phase 3a): every PENDING moment/caption agent gate
    with the request context the operator needs to answer it (transcript/signals for moments, the
    surface list for captions). Corrupt request files surface as dismiss-only rows (corrupt=True).
    Same enumeration `fanops respond` uses, surfaced for the browser."""
    from fanops.agentstep import pending, request_path
    from fanops.pipeline_status import _gate_is_corrupt
    from fanops.transcribe import _trust_tier
    rows: list[dict] = []
    for kind in ("moments", "moment_hooks", "captions"):
        for key in pending(cfg, kind=kind):
            if _gate_is_corrupt(cfg, kind, key):
                rows.append({"kind": kind, "key": key, "corrupt": True})
                continue
            try:
                payload = json.loads(request_path(cfg, kind, key).read_text())
            except Exception as exc:
                from fanops.log import get_logger
                get_logger(cfg)("gates", key, "request_read_failed", kind=kind, err=str(exc)[:160])
                continue                               # torn/unreadable request file: SKIP it (match the
                                                       # docstring) rather than render an empty, unanswerable
                                                       # gate form whose blank submit could write a bad answer
                                                       # (ecc audit). The corruption is also logged by
                                                       # latest_request_id during pending().
            if kind == "moments" and payload.get("transcript"):
                lang = payload.get("language")
                tr = []
                for seg in payload["transcript"]:
                    if isinstance(seg, dict):
                        s = dict(seg)
                        s["trust_tier"] = _trust_tier(s, src_lang=lang)
                        tr.append(s)
                    else:
                        tr.append(seg)
                payload = {**payload, "transcript": tr}
            rows.append({"kind": kind, "key": key, **payload})
    return rows
