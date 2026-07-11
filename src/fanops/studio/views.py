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
from fanops.studio.views_common import (IMMINENT_THRESHOLD_MINUTES, GRID_PAGE_SIZE, paginate, TERM_DEFS, term_def, accounts_in, _imminent, suggest_time, lineage_maps, clip_source_of, source_universe_for_clips)  # noqa: F401
from fanops.studio.views_review import (SurfacePost, ReviewCard, ProvChip, provenance_chips, _surface, source_choices, _empty_cell_reason, review_matrix, account_lanes, _STATE_TO_BUCKET, review_buckets, review_counts, review_progress, source_universe, account_pivot_rows, group_review_by_account_surface, surface_for_post, group_review_by_batch, awaiting_moment_count, review_awaiting_by_account)  # noqa: F401
from fanops.studio.views_results import (ScheduleRow, ScheduleLanes, LiftRow, publish_readiness, explain_suggested_time, schedule_rows, schedule_lanes, due_publish_plan, DuePublishPlan, schedule_cockpit, ScheduleCockpit, inflight_watch, InflightWatchRow, group_schedule_by_account, PostedRow, posted_library, posted_batch_rollup, lineage_stats, account_median_deltas, metric_peaks, bar_pct, group_posted_by_day, lift_rows, classify_post_delivery, failure_rollup, operator_error, failure_label)  # noqa: F401
from fanops.studio.views_live import (LiveMediaRow, live_library, live_library_scope)  # noqa: F401  # MOL-27: the "viewed there, not authored here" Live library read-model (imported_media only, disjoint from Posted)
from fanops.studio.views_library import (STAGES, library_catalog, source_pipeline_map)  # noqa: F401


@dataclass
class GoLiveChannel:
    platform: str
    integration_id: str        # effective current id: the per-platform integrations[platform], else the
                               # shared account_id fallback, else "" (unmapped). NEVER a secret.
    backend: str = ""          # Zernio slice 4: the per-(handle, platform) backend OVERRIDE (e.g. "zernio");
                               # "" == no override -> the global FANOPS_POSTER. Surfaced so the operator sees
                               # WHICH scheduler each channel publishes through (IG postiz, TikTok zernio).


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
    creative_variation: bool = False   # per-account on-screen hooks ON (FANOPS_CREATIVE_VARIATION) — persona diff
    account_casting: bool = False      # per-account moment casting ON (FANOPS_ACCOUNT_CASTING) — distinct moment sets per account
    clip_profile: str = "talk"         # clip-length band (FANOPS_CLIP_PROFILE): talk 12-22s / song 18-35s
    responder_mode: str = "manual"     # THE AI switch (FANOPS_RESPONDER): 'llm' = pipeline answers gates via claude, 'manual' = human/pending
    daemon: Optional[dict] = None      # launchd pipeline-driver health (verdict/loaded/interval/responder), None off-darwin
    demoted: list = field(default_factory=list)   # Phase 3: planned/demoted accounts (promotable) — golive_accounts lists only active()
    # Phase 6: A/B learning-loop INTENT flags (default OFF). ON sets intent only — the apply paths stay
    # learning_validated-frozen, so a flag here NEVER unfreezes learning (that gate auto-stamps on real metrics).
    variant_learning: bool = False     # FANOPS_VARIANT_LEARNING — the loop master switch
    variant_amplify: bool = False      # FANOPS_VARIANT_AMPLIFY — a sustained winner auto-amplifies its source
    variant_ucb: bool = False          # FANOPS_VARIANT_UCB — deterministic UCB1 explore/exploit rank
    variant_transfer: bool = False     # FANOPS_VARIANT_TRANSFER — seed a cold account from proven donors
    setup_state: str = "NOT_CONFIGURED"   # MOL-302: derived setup position (never persisted)
    setup_next: str = ""               # next operator action for the current setup_state
    half_live: bool = False            # D15/MOL-297: LIVE flag set but nothing routes live — warn, never solid-green LIVE
    half_live_hint: str = ""           # operator-facing explanation (names the ignored FANOPS_POSTER value)


@dataclass
class HomeStatus:                      # Face 2: the GET / status-home read-model (read-only, no secret, no flag)
    mode: str
    is_live: bool
    counts: dict                       # {sources, batches(int|None on fail-open), awaiting, scheduled, posted}
    accounts: list[GoLiveAccount]      # via the shared golive_accounts helper (NEVER golive_status -> no doctor_report on /)
    by_account: dict                   # Face 2 fu (D2): per-account post counts for #home-metrics (on-disk facts, never lift)


@dataclass
class HomeBatch:                       # Face 2 fu: one batch row for the Home entry point (deep-links ?batch=<id>)
    id: str
    name: str
    targets: list[str]
    state: str
    created_at: Optional[str]
    posts_born: int
    is_zero_result: bool               # bool(targets) and posts_born == 0 — a mis-targeted batch that birthed nothing


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
            try:
                due = parse_iso(p.scheduled_time) <= now
            except Exception:
                due = False
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
    from fanops.agentstep import pending
    from fanops.pipeline_status import status_control_lines, source_backlog
    led = Ledger.load(cfg)
    run_line, wait_line = status_control_lines(cfg, led)
    bl = source_backlog(led, cfg)
    return {
        "sources": bl.actionable,   # in-progress pipeline work (NOT raw inventory — see source_backlog)
        "sources_blocked": bl.blocked_on_gates,
        "sources_recoverable": bl.recoverable,
        "sources_inventory": bl.inventory,
        "native_total": bl.actionable + bl.blocked_on_gates + bl.recoverable + bl.inventory,
        "backlog_rows": [{"id": r.id, "state": r.state, "bucket": r.bucket, "wait_line": r.wait_line,
                          "block_reason": r.block_reason, "artifacts": r.artifacts} for r in bl.rows],
        "third_party": sum(1 for s in led.sources.values() if s.origin_kind == "third_party"),
        "clips": len(led.clips), "posts": len(led.posts),
        "awaiting": awaiting_moment_count(led),   # S3: ACTIONABLE — MOMENTS (== Home/Review worklist), not raw posts
        "published": len(led.posts_in_state(PostState.published)),
        "holds": sum(1 for c in led.clips.values() if c.held),
        "pending_moments": len(pending(cfg, kind="moments")),
        "pending_moment_hooks": len(pending(cfg, kind="moment_hooks")),
        "pending_captions": len(pending(cfg, kind="captions")),
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
        try:
            out.append({"id": s.id, "state": s.state.value, "error_reason": s.error_reason or "",
                        "batch_id": s.batch_id, "created_at": s.created_at,
                        "name": Path(s.source_path).name if s.source_path else s.id})
        except Exception:
            continue
    return out


def run_next_step(status: dict) -> dict:
    """S3: the Make tab's ONE 'do this next' affordance, derived PURELY from pipeline_status counts (no ledger
    read; fail-open via .get so a torn/partial dict never raises). The ladder mirrors the real pipeline:
    add footage → answer gates → run a pass → review. Gates PRECEDE review because a pending decision is
    BLOCKING mid-pipeline clips (the operator can't finish them until they answer). Returns {key, label, hint};
    the gate step spells out the gate→clip link ('answer, then Prepare again')."""
    s = status if isinstance(status, dict) else {}
    def _n(k):
        try: return int(s.get(k, 0) or 0)
        except (TypeError, ValueError): return 0
    footage = _n("native_total") + _n("third_party")
    gates = _n("pending_moments") + _n("pending_moment_hooks") + _n("pending_captions")
    awaiting = _n("awaiting")               # ACTIONABLE clips awaiting review (moments) — the SAME unit Home/Review
                                            # show, so the Make banner agrees with them (was raw posts: "57" vs "17").
    if footage == 0:
        return {"key": "add", "label": "Add a video to begin",
                "hint": "Choose a file above, or paste a link under More — then ingest it."}
    if gates:
        hint = "Some clips are paused waiting on a decision. Answer them, then run Prepare again to finish those clips."
        if awaiting: hint += f" ({awaiting} clip(s) are also waiting in Review.)"
        return {"key": "gate", "label": f"Answer {gates} processing decision(s)", "hint": hint}
    if awaiting:
        return {"key": "review", "label": f"{awaiting} clip(s) ready",
                "hint": "Review and approve them in the Review tab — nothing ships until you do."}
    # footage exists, no gates, nothing awaiting review -> run a pass (cut the new footage, or produce more).
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
                            "recoverable": bl.recoverable, "inventory": bl.inventory}}
    except Exception as exc:                          # invariant: the Library tab must never 500 — but
        from fanops.log import get_logger             # a read-fail is RECORDED, never silently shown as "empty"
        get_logger(cfg)("library", "-", "error", err=str(exc)[:160])
        return {"native": [], "third_party": [], "backlog": {"actionable": 0, "blocked_on_gates": 0,
                                                            "recoverable": 0, "inventory": 0}}


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
    """A2: one first-class Persona for the Personas page — its editable fields + curated corpus + the
    accounts currently linked to it (so the operator sees a persona's blast radius). NO secret."""
    id: str
    name: str
    voice: str
    corpus: list                       # the per-persona reach-vetted hashtag pool (B1) — the SOLE hashtag differentiator (M3), DISPLAYED reach-first (B3)
    intake: dict                       # genre/language/reference_accounts/notes (seeds B3 research)
    linked_handles: list               # accounts whose persona_id points at this persona
    reach_tags: list = field(default_factory=list)   # corpus tags present in the LIVE Graph-reach store -> flag as currently most-active
    reach_means: dict = field(default_factory=dict)  # {corpus tag -> LIVE Graph reach} from the store (the honest 'why this tag' signal)
    # Lever engine: the per-characteristic levers + the COMPOSED instruction the pipeline will read
    # ("what the AI will read") — so the operator sees their config's exact downstream effect on the card.
    content_focus: list = field(default_factory=list)
    selection_scope: Optional[str] = None
    hook_angle: Optional[str] = None
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
    """The Personas-page read-model: every persona as a card (with its linked account handles + corpus
    ranked by LIVE Graph reach + each curated tag's Graph reach) + every account's current persona link (for
    the connect dropdown). Fail-open: a corrupt personas.json / accounts.json -> an EMPTY page (the surface
    never 500s), mirroring golive_accounts. `led` is accepted for call-compat; the surface reads no ledger."""
    try:
        from fanops.personas import (Personas, compose_persona_instruction, persona_facts,   # lazy: personas imports accounts (in migrate) -> avoid a load cycle
                                     hook_directive, caption_directive, resolved_cut_spec, manifest,
                                     ensure_baked_personas)
        ensure_baked_personas(cfg)
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
    # Surface each corpus REACH-RANKED by the LIVE Graph-reach store and flag the currently-most-active tags.
    # MOL-59: "most-active" (★) is gated on a MEASURED Graph reach value, NOT mere store presence — a seed tag
    # with no measurement asserts no live-reach fact, so it never stars. No measurements -> reach_tags empty.
    from fanops.hashtags import vetted_menu, load_store, load_store_reach, _norm
    store = load_store(cfg)
    rank = {t: i for i, t in enumerate(vetted_menu(store))}
    # The numeric reach annotation per tag is the tag's LIVE Graph reach, persisted in the store by
    # refresh_store — NOT own-post reach (the own-reach judge was deleted; a tag's worth is its platform
    # reach, never a post that used it). Absent store / no creds -> {} (the number simply doesn't render).
    # MOL-59: `means` is also the ★ gate — a tag is "most-active" iff it has a measured reach here.
    means = load_store_reach(cfg)
    def _ranked(corpus):
        return sorted((_norm(t) for t in corpus), key=lambda n: rank.get(n, 10 ** 6))
    cards = [PersonaCard(id=p.id, name=p.name, voice=p.voice,
                         corpus=_ranked(p.hashtag_corpus), intake=dict(p.intake),
                         linked_handles=by_pid.get(p.id, []),
                         reach_tags=[_norm(t) for t in p.hashtag_corpus if _norm(t) in means],   # MOL-59: measured-reach-gated, not store-present
                         reach_means={_norm(t): means[_norm(t)] for t in p.hashtag_corpus if _norm(t) in means},
                         content_focus=list(p.content_focus), selection_scope=p.selection_scope, hook_angle=p.hook_angle,
                         clip_profile=resolved_cut_spec(p)[0], framing=facts["framing"],   # M3: the DERIVED tier (the per-persona pin is retired)
                         instruction=compose_persona_instruction(p),
                         length_band=facts["length_band"], lead_tags=facts["lead_tags"],
                         hook_text=hook_directive(p), caption_text=caption_directive(p),
                         lever_manifest=manifest(cfg, p))                  # M4: the per-lever produces + health read
             for p in reg.all() for facts in (persona_facts(cfg, p),)]
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


def _post_is_due(p, now: datetime) -> bool:
    if p.state is not PostState.queued:
        return False
    if not p.scheduled_time:
        return True
    try:
        return parse_iso(p.scheduled_time) <= now
    except Exception:
        return False


def _post_live_today(p, now: datetime) -> bool:
    from fanops.studio.views_results import _classify_channel
    from datetime import timedelta
    if _classify_channel(p.public_url) != "live":
        return False
    t = p.published_at or p.scheduled_time
    if not t:
        return False
    try:
        dt = parse_iso(t)
        if dt.tzinfo is None:
            return False
        return dt >= now - timedelta(hours=24)
    except Exception:
        return False


def _half_live_state(cfg: Config) -> tuple[bool, str]:
    """D15: FANOPS_LIVE=1 but nothing routes live (typo'd FANOPS_POSTER, no live per-channel backend).
    Shared by build_system_strip + golive_status so every LIVE surface reads the same truth. Fail-open."""
    from fanops.log import get_logger
    try:
        if cfg.is_live and not cfg.live_route_exists:
            raw = cfg.poster_backend_raw or "(unset)"
            return True, (f"LIVE flag is set but nothing routes live — FANOPS_POSTER={raw} is ignored "
                            "(it's a legacy bridge, not the switch). Check .env / the Go-Live tab: route a "
                            "channel to a provider with creds, or flip back to dryrun.")
    except Exception as exc:
        get_logger(cfg)("half_live", "-", "half_live_error", err=str(exc)[:160])
    return False, ""


def build_system_strip(cfg: Config) -> dict:
    """Global system strip read-model: LIVE/DRYRUN mode + blocked gate count + failed-post alert. Health dots lazy-load via htmx."""
    from fanops.log import get_logger                     # a strip sub-read failure is RECORDED, never a silently-zeroed badge
    try:
        ps = pipeline_status(cfg)
        blocked = ps.get("sources_blocked", 0) or (ps.get("pending_moments", 0) + ps.get("pending_moment_hooks", 0)
                                                   + ps.get("pending_captions", 0))
        recoverable = ps.get("sources_recoverable", 0)
    except Exception as exc:
        get_logger(cfg)("system_strip", "-", "pipeline_status_error", err=str(exc)[:160])
        blocked = 0; recoverable = 0
    failed = 0
    try:
        failed = sum(1 for p in Ledger.load(cfg).posts.values() if p.state is PostState.failed)
    except Exception as exc:
        get_logger(cfg)("system_strip", "-", "failed_scan_error", err=str(exc)[:160])
        failed = 0
    # MOL-123: errored sources must be LOUD — a source parked in error/moments_empty means clip production
    # silently stalled (the 2026-07-05 TimeoutExpired sat invisible while the strip read "idle"). Same
    # fail-open-with-breadcrumb discipline as the failed-post scan; the count links to the Run tab's list.
    errored = recoverable if recoverable else 0
    if not errored:
        try:
            errored = sum(1 for s in Ledger.load(cfg).sources.values() if s.state in _RECOVERABLE_SOURCE_STATES)
        except Exception as exc:
            get_logger(cfg)("system_strip", "-", "errored_scan_error", err=str(exc)[:160])
            errored = 0
    # Leg 2 (Insight): the one external gate — a persisted breadcrumb means Graph media-insights was refused
    # for lack of instagram_manage_insights, so IG performance is frozen at its last snapshot until granted.
    try:
        from fanops.meta_graph import insights_blocked_signal
        insights_blocked = insights_blocked_signal(cfg)
    except Exception as exc:
        get_logger(cfg)("system_strip", "-", "insights_blocked_error", err=str(exc)[:160])
        insights_blocked = False
    half_live, half_live_hint = _half_live_state(cfg)
    # D13b: Postiz-down banner — the backend health probe (past the nginx-only container check) is unhealthy
    # AND at least one channel routes to postiz. Delegated to views_common (30s-cached) so a Studio render
    # doesn't slam Postiz every hit; fail-open to not-shown so a probe hiccup never blocks the page.
    try:
        postiz_down = views_common.postiz_health_for_banner(cfg)
    except Exception as exc:
        get_logger(cfg)("system_strip", "-", "postiz_down_error", err=str(exc)[:160])
        postiz_down = {"show": False}
    return {"is_live": cfg.is_live, "mode": _publish_mode_label(cfg), "blocked_gates": blocked,
            "recoverable_sources": recoverable, "failed": failed, "insights_blocked": insights_blocked,
            "errored_sources": errored, "half_live": half_live, "half_live_hint": half_live_hint,
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


def _queued_has_future_schedule(p, now: datetime) -> bool:
    """True when a queued post has a strictly-future scheduled_time (not timeless / past-due)."""
    if not p.scheduled_time:
        return False
    try:
        return parse_iso(p.scheduled_time) > now
    except (ValueError, TypeError):
        return False

def schedule_auto_ship(cfg: Config) -> bool:
    """Live + daemon alive — Schedule is read-only; posts ship on the clock."""
    if not cfg.is_live:
        return False
    dh = daemon_health(cfg)
    return bool(dh and dh.get("verdict") == "alive")


def review_handoff(cfg: Config) -> dict:
    """Account with the most awaiting posts — Make → Review entry."""
    wc = account_work_counts(cfg)
    best_h, best_n = None, 0
    for h, c in wc.items():
        n = int(c.get("awaiting") or 0)
        if n > best_n:
            best_h, best_n = h, n
    if not best_h or not best_n:
        return {}
    out = {"account": best_h, "awaiting": best_n}
    try:
        led = Ledger.load(cfg)
        by_batch: dict[str, int] = {}
        for p in led.posts.values():
            if p.state is PostState.awaiting_approval and p.account == best_h and p.batch_id:
                by_batch[p.batch_id] = by_batch.get(p.batch_id, 0) + 1
        if by_batch:
            out["batch"] = max(by_batch, key=by_batch.get)
    except Exception:
        pass
    return out


def zero_post_clips(cfg: Config) -> list[dict]:
    """Captioned/queued clips with no Post born — the silent crosspost drop surfaced for Home."""
    from fanops.models import ClipState
    try:
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
    except Exception:
        return []


def metrics_stale_hint(cfg: Config) -> bool:
    """True when live trackable posts exist but most lack analyzed metrics."""
    if not cfg.is_live:
        return False
    try:
        from fanops.studio.views_results import _classify_channel
        led = Ledger.load(cfg)
        live = [p for p in led.posts.values()
                if p.state in (PostState.published, PostState.analyzed)
                and _classify_channel(getattr(p, "public_url", None)) == "live"]
        if len(live) < 2:
            return False
        thin = sum(1 for p in live if (p.metrics or {}).get("lift_score") is None)
        return thin >= max(1, len(live) // 2)
    except Exception:
        return False


def review_nav_params(cfg: Config, account: str | None = None) -> dict:
    """Review focus entry — account + dominant batch for handoff links."""
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
            try:
                led = Ledger.load(cfg)
                by_batch: dict[str, int] = {}
                for p in led.posts.values():
                    if p.state is PostState.awaiting_approval and p.account == h and p.batch_id:
                        by_batch[p.batch_id] = by_batch.get(p.batch_id, 0) + 1
                if by_batch:
                    batch = max(by_batch, key=by_batch.get)
            except Exception:
                pass
    if batch:
        out["batch"] = batch
    return out


def account_work_counts(cfg: Config) -> dict[str, dict]:
    """Per-handle work queue counts for Home rows and the account session bar."""
    from collections import defaultdict
    out: dict[str, dict] = defaultdict(lambda: {"awaiting": 0, "scheduled": 0, "failed": 0, "inflight": 0, "review_batch": None})
    try:
        led = Ledger.load(cfg)
        for p in led.posts.values():
            h = p.account
            if p.state is PostState.awaiting_approval:
                out[h]["awaiting"] += 1
            elif p.state is PostState.queued:
                if _queued_has_future_schedule(p, datetime.now(timezone.utc)):
                    out[h]["scheduled"] += 1
            elif p.state in (PostState.needs_reconcile, PostState.submitting, PostState.submitted):
                out[h]["inflight"] += 1
            elif p.state in (PostState.failed, PostState.error):
                out[h]["failed"] += 1
    except Exception:
        pass
    for h in out:
        if out[h]["awaiting"]:
            out[h]["review_batch"] = review_nav_params(cfg, h).get("batch")
    return dict(out)


def home_status(cfg: Config) -> HomeStatus:
    """Lock-free, fail-open read-model for GET / (the status home): connection state per account (via the
    shared golive_accounts helper — NEVER golive_status, which also runs doctor_report on every load) +
    headline counts + per-account post counts, all from ONE Ledger.load. A torn ledger -> zeroed counts +
    batches=None + empty by_account, never a 500."""
    accounts = golive_accounts(cfg)                   # once-bound, already fail-open (no doctor_report on /)
    mode = _publish_mode_label(cfg)                    # provider-aware (M3); 'dryrun' when not live
    try:
        from collections import Counter
        led = Ledger.load(cfg)
        st = Counter(p.state for p in led.posts.values())
        inflight = (st.get(PostState.needs_reconcile, 0) + st.get(PostState.submitting, 0)
                    + st.get(PostState.submitted, 0))
        due_soon = sum(1 for p in led.posts.values()
                       if p.state is PostState.queued and _post_is_due(p, datetime.now(timezone.utc)))
        live_today = sum(1 for p in led.posts.values()
                         if p.state in (PostState.published, PostState.analyzed)
                         and _post_live_today(p, datetime.now(timezone.utc)))
        from fanops.studio.views_results import _classify_channel
        live_trackable = sum(1 for p in led.posts.values()
                             if p.state in (PostState.published, PostState.analyzed)
                             and _classify_channel(getattr(p, "public_url", None)) == "live")
        failed = st.get(PostState.failed, 0)
        from fanops.studio.views_results import failure_rollup
        fb = failure_rollup(led)["buckets"]
        counts = {"sources": sum(1 for s in led.sources.values() if s.origin_kind == "native"),
                  "batches": len(getattr(led, "batches", {})),
                  "awaiting": awaiting_moment_count(led),
                  "awaiting_posts": st.get(PostState.awaiting_approval, 0),
                  "scheduled": st.get(PostState.queued, 0),
                  "inflight": inflight,
                  "due_soon": due_soon,
                  "live_today": live_today,
                  "live_trackable": live_trackable,
                  "failed": failed, "failed_rate_limit": fb.get("rate_limit", 0),
                  "failed_oversize": fb.get("oversize", 0),
                  "posted": st.get(PostState.published, 0) + st.get(PostState.analyzed, 0)}
        by_account = dict(Counter(p.account for p in led.posts.values()))
    except Exception as exc:                          # the first page an operator sees must never 500
        from fanops.log import get_logger
        get_logger(cfg)("home", "-", "error", err=str(exc)[:160])
        counts = {"sources": 0, "batches": None, "awaiting": 0, "awaiting_posts": 0, "scheduled": 0,
                  "inflight": 0, "due_soon": 0, "live_today": 0, "live_trackable": 0, "failed": 0, "posted": 0}
        by_account = {}
    return HomeStatus(mode=mode, is_live=cfg.is_live, counts=counts, accounts=accounts, by_account=by_account)


def daemon_health(cfg: Config) -> Optional[dict]:
    """Fail-open liveness of the launchd PIPELINE DRIVER for the Home banner. Returns daemon.status()'s
    verdict dict (loaded/pid/last_exit/heartbeat_age_s/verdict), or None when it can't be judged — non-darwin,
    launchctl absent, or any error — so Home never 500s and a non-mac dev box shows no false alarm. The
    detection already exists in daemon.status(); this only SURFACES it where the operator looks. Lazy import
    keeps the launchd/subprocess dependency off the core view path; htmx-loaded on-demand (mirrors
    /golive/health) so it never runs a subprocess on the spine's every-surface home_status read.

    Enriched with `interval`/`responder`/`discloses_llm` so the banner can (a) frame a NOT-INSTALLED driver
    as OPT-IN rather than a fault and (b) DISCLOSE the recurring-LLM cost when hands-off would run llm."""
    try:
        from fanops import daemon
        from fanops import pipeline
        interval = daemon.installed_interval(cfg) or 600
        rep = daemon.status(cfg, interval=interval)
        responder = daemon.resolve_responder(cfg)
        try:
            pending_gates = pipeline.pending_gate_count(cfg)   # need-aware truth: claude runs ONLY to answer these
        except Exception:
            pending_gates = None                               # never let a torn agent_io dir 500 the banner
        siblings = daemon.sibling_agents_status()
        return {**rep, "interval": interval, "responder": responder, "discloses_llm": responder == "llm",
                "pending_gates": pending_gates, "siblings": siblings}
    except Exception:
        return None


def home_batches(cfg: Config) -> list[HomeBatch]:
    """Lock-free, fail-open batch list for the Home entry point — each row deep-links ?batch=<id> into Review
    and carries posts_born + a zero-result flag (a non-empty target that birthed NO post — the silent
    crosspost batch_target_skip outcome, surfaced). Newest-first by created_at (None sinks last), tie-broken
    by id. Torn ledger -> [] + logged, never a 500. Surfaces the outcome; computes no skip logic."""
    try:
        led = Ledger.load(cfg)
        out = []
        for b in getattr(led, "batches", {}).values():
            born = sum(1 for p in led.posts.values() if p.batch_id == b.id)
            out.append(HomeBatch(id=b.id, name=b.name, targets=list(b.target_accounts), state=b.state.value,
                                 created_at=b.created_at, posts_born=born,
                                 is_zero_result=bool(b.target_accounts) and born == 0))   # [] ALL-sentinel is NEVER zero-result
        out.sort(key=lambda h: (h.created_at or "", h.id), reverse=True)
        return out
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("home_batches", "-", "error", err=str(exc)[:160])
        return []


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
    blocked_gates: int = 0             # pending agent gates (Make severity dot)
    next_params: dict = field(default_factory=dict)  # extra url_for kwargs for the next CTA


_SPINE_ORDER = (("make", "Make", "run_panel"), ("review", "Review", "review"),
                ("schedule", "Schedule", "schedule"), ("posted", "Posted", "posted"))


def build_spine(*, counts: dict, has_accounts: bool, here: Optional[str],
                inflight: int = 0, blocked_gates: int = 0, next_params: Optional[dict] = None) -> WorkflowSpine:
    """Pure: turn the Home counts into the Make→Review→Schedule→Posted stepper. Stage badges carry
    severity when blocked (Make), awaiting>20 (Review), or inflight>0 (Schedule)."""
    src = int(counts.get("sources", 0)); awaiting = int(counts.get("awaiting", 0))
    queued = int(counts.get("scheduled", 0)); posted = int(counts.get("posted", 0))
    failed = int(counts.get("failed", 0)); live_trackable = int(counts.get("live_trackable", 0))
    inflight = int(inflight); blocked_gates = int(blocked_gates)
    done = {"make": src > 0, "review": awaiting == 0 and (queued > 0 or posted > 0), "schedule": posted > 0, "posted": live_trackable > 0}
    sched_count = queued + inflight
    nums = {"make": src, "review": awaiting, "schedule": sched_count, "posted": live_trackable}
    sev = {"make": "danger" if blocked_gates else None,
           "review": "warn" if awaiting > 20 else None,
           "schedule": "info" if inflight else None,
           "posted": "danger" if failed else None}
    stages = [SpineStage(key=k, label=lbl, endpoint=ep, count=nums[k],
                         state=("active" if k == here else ("done" if done[k] else "todo")),
                         severity=sev[k])
              for k, lbl, ep in _SPINE_ORDER]
    if not has_accounts:               n = ("Connect an account to begin", "golive_view")
    elif src == 0:                     n = ("Add footage to get started", "run_panel")
    elif blocked_gates:                n = (f"Answer {blocked_gates} processing decision{'s' if blocked_gates != 1 else ''}", "gates")
    elif awaiting > 0:                 n = (f"Review {awaiting} clip{'s' if awaiting != 1 else ''}", "review")
    elif queued > 0 or inflight:       n = (f"Schedule {queued} post{'s' if queued != 1 else ''}" + (f" · {inflight} in flight" if inflight else ""), "schedule")
    elif failed > 0:                   n = (f"{failed} post{'s' if failed != 1 else ''} failed — open recovery", "posted")
    elif live_trackable > 0:           n = ("You're all caught up", None)
    else:                              n = ("Run a pass in Make", "run_panel")
    return WorkflowSpine(stages=stages, next_label=n[0], next_endpoint=n[1], here=here,
                         inflight=inflight, blocked_gates=blocked_gates, next_params=next_params or {})


def golive_status(cfg: Config) -> GoLiveStatus:
    """Lock-free read-model for the Go-Live tab: the publish mode (dryrun/live), whether Postiz is
    configured (postiz_url is shown — it is NON-secret; key_set is a BOOL only, the key itself is never
    exposed), the ACTIVE accounts to map, and the doctor readiness checks/notes.

    Accounts are listed PER-CHANNEL: each active handle carries one GoLiveChannel per platform, because a
    handle's Instagram and TikTok are DIFFERENT Postiz integrations (M1). Each channel's integration_id is
    the effective current id — the per-platform integrations[platform], else the shared account_id
    fallback, else "" (unmapped). Tolerates a malformed accounts.json (falls back to an empty list) so the
    tab never 500s."""
    from fanops.doctor import doctor_report
    accts = golive_accounts(cfg)                      # shared helper (single source of truth for the accounts read-model)
    try:
        report = doctor_report(cfg)
    except Exception as exc:                          # invariant: the Go-Live tab must never 500 (ecc:python-review)
        from fanops.log import get_logger             # ECC fix #5: log why readiness is unavailable
        get_logger(cfg)("golive", "-", "doctor_error", err=str(exc)[:160])
        report = {"checks": [], "notes": ["readiness check unavailable"]}
    from fanops.validation_gate import learning_validated
    from fanops.doctor import setup_state, setup_next_action
    half_live, half_live_hint = _half_live_state(cfg)
    return GoLiveStatus(
        mode=_publish_mode_label(cfg),               # provider-aware (M3); 'dryrun' when not live
        is_live=cfg.is_live,
        half_live=half_live, half_live_hint=half_live_hint,
        postiz_url=cfg.postiz_url,                    # non-secret; shown so the operator can confirm config
        key_set=cfg.postiz_api_key is not None,       # BOOL only — the API key value is NEVER exposed
        zernio_key_set=cfg.zernio_api_key is not None,  # Zernio slice 4: BOOL only (connect-block state)
        accounts=accts,
        checks=report["checks"],
        notes=report["notes"],
        learning_validated=learning_validated(cfg),    # M3: shows whether the loop is unfrozen (cutover done)
        creative_variation=False,
        account_casting=cfg.account_casting,           # per-account moment casting toggle state (persona diff)
        clip_profile=cfg.clip_profile,                 # clip-length band (talk/song)
        responder_mode=cfg.responder_mode,             # THE AI switch state (llm/manual) — surfaced for the toggle
        daemon=daemon_health(cfg),                     # launchd driver health for the Go-Live daemon control (None off-darwin)
        demoted=golive_demoted_accounts(cfg),          # Phase 3: promotable planned accounts
        variant_learning=cfg.variant_learning,         # Phase 6: A/B learning-loop intent flags (default OFF)
        variant_amplify=cfg.variant_amplify, variant_ucb=cfg.variant_ucb, variant_transfer=cfg.variant_transfer,
        setup_state=setup_state(cfg), setup_next=setup_next_action(cfg))


def gate_rows(cfg: Config) -> list[dict]:
    """Lock-free read-model for the Gates tab (Phase 3a): every PENDING moment/caption agent gate
    with the request context the operator needs to answer it (transcript/signals for moments, the
    surface list for captions). Corrupt request files surface as dismiss-only rows (corrupt=True).
    Same enumeration `fanops respond` uses, surfaced for the browser."""
    from fanops.agentstep import pending, request_path
    from fanops.pipeline_status import _gate_is_corrupt
    rows: list[dict] = []
    for kind in ("moments", "moment_hooks", "captions"):
        for key in pending(cfg, kind=kind):
            if _gate_is_corrupt(cfg, kind, key):
                rows.append({"kind": kind, "key": key, "corrupt": True})
                continue
            try:
                payload = json.loads(request_path(cfg, kind, key).read_text())
            except Exception:
                continue                               # torn/unreadable request file: SKIP it (match the
                                                       # docstring) rather than render an empty, unanswerable
                                                       # gate form whose blank submit could write a bad answer
                                                       # (ecc audit). The corruption is already logged by
                                                       # latest_request_id during pending().
            rows.append({"kind": kind, "key": key, **payload})
    return rows
