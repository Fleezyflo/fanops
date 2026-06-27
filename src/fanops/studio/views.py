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
from fanops.ledger import Ledger
from fanops.models import ClipState, PostState, StitchState
from fanops.timeutil import parse_iso
# Facade re-exports: the names consumers reach via `fanops.studio.views` / `views.X` (templates / app.py /
# tests). Dead re-exports (no facade consumer AND no internal use here) were trimmed — every trimmed symbol
# still lives in its home submodule (views_common/_review/_results); this is just the public views surface.
# F401-silenced because each name is re-exported, not referenced within this file.
from fanops.studio.views_common import (IMMINENT_THRESHOLD_MINUTES, GRID_PAGE_SIZE, paginate, TERM_DEFS, term_def, accounts_in, _imminent, suggest_time)  # noqa: F401
from fanops.studio.views_review import (SurfacePost, ReviewCard, ProvChip, provenance_chips, _surface, source_choices, _empty_cell_reason, review_matrix, account_lanes, _STATE_TO_BUCKET, review_buckets, review_counts, review_progress, source_universe, account_pivot_rows, group_review_by_account_surface, surface_for_post, group_review_by_batch, awaiting_moment_count)  # noqa: F401
from fanops.studio.views_results import (ScheduleRow, LiftRow, publish_readiness, explain_suggested_time, schedule_rows, group_schedule_by_account, PostedRow, posted_library, posted_batch_rollup, lineage_stats, metric_peaks, bar_pct, group_posted_by_day, lift_rows)  # noqa: F401


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
    demoted: list = field(default_factory=list)   # Phase 3: planned/demoted accounts (promotable) — golive_accounts lists only active()
    # Phase 6: A/B learning-loop INTENT flags (default OFF). ON sets intent only — the apply paths stay
    # learning_validated-frozen, so a flag here NEVER unfreezes learning (that gate auto-stamps on real metrics).
    variant_learning: bool = False     # FANOPS_VARIANT_LEARNING — the loop master switch
    variant_amplify: bool = False      # FANOPS_VARIANT_AMPLIFY — a sustained winner auto-amplifies its source
    variant_ucb: bool = False          # FANOPS_VARIANT_UCB — deterministic UCB1 explore/exploit rank
    variant_transfer: bool = False     # FANOPS_VARIANT_TRANSFER — seed a cold account from proven donors


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
    led = Ledger.load(cfg)
    return {
        "sources": sum(1 for s in led.sources.values() if s.origin_kind == "native"),  # M1: chain count = native only
        "third_party": sum(1 for s in led.sources.values() if s.origin_kind == "third_party"),
        "clips": len(led.clips), "posts": len(led.posts),
        "awaiting": awaiting_moment_count(led),   # S3: ACTIONABLE — MOMENTS (== Home/Review worklist), not raw posts
        "published": len(led.posts_in_state(PostState.published)),
        "holds": sum(1 for c in led.clips.values() if c.held),
        "pending_moments": len(pending(cfg, kind="moments")),
        "pending_moment_hooks": len(pending(cfg, kind="moment_hooks")),
        "pending_captions": len(pending(cfg, kind="captions")),
        "backend": cfg.poster_backend,
        "accounts": [a.handle for a in Accounts.load(cfg).active()],   # Account-First: Run-form batch-target options
    }


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
    footage = _n("sources") + _n("third_party")
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
        rows = [{"id": s.id, "origin_kind": s.origin_kind, "state": s.state.value,
                 "name": Path(s.source_path).name if s.source_path else s.id,   # P6: human filename, not the opaque id
                 "duration": s.duration, "width": s.width, "height": s.height} for s in led.sources.values()]
        return {"native": [r for r in rows if r["origin_kind"] == "native"],
                "third_party": [r for r in rows if r["origin_kind"] == "third_party"]}
    except Exception as exc:                          # invariant: the Library tab must never 500 — but
        from fanops.log import get_logger             # a read-fail is RECORDED, never silently shown as "empty"
        get_logger(cfg)("library", "-", "error", err=str(exc)[:160])
        return {"native": [], "third_party": []}


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
    energy: Optional[str] = None
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
    # Surface each corpus REACH-RANKED by the LIVE Graph-reach store and flag the currently-most-active
    # (store-present) tags. No store -> insertion order preserved, reach_tags empty (no signal yet).
    from fanops.hashtags import vetted_menu, load_store, load_store_reach, _norm
    store = load_store(cfg)
    rank = {t: i for i, t in enumerate(vetted_menu(store))}
    store_set = {_norm(t) for t in (store or [])}
    # The numeric reach annotation per tag is the tag's LIVE Graph reach, persisted in the store by
    # refresh_store — NOT own-post reach (the own-reach judge was deleted; a tag's worth is its platform
    # reach, never a post that used it). Absent store / no creds -> {} (the number simply doesn't render).
    means = load_store_reach(cfg)
    def _ranked(corpus):
        return sorted((_norm(t) for t in corpus), key=lambda n: rank.get(n, 10 ** 6))
    cards = [PersonaCard(id=p.id, name=p.name, voice=p.voice,
                         corpus=_ranked(p.hashtag_corpus), intake=dict(p.intake),
                         linked_handles=by_pid.get(p.id, []),
                         reach_tags=[_norm(t) for t in p.hashtag_corpus if _norm(t) in store_set],
                         reach_means={_norm(t): means[_norm(t)] for t in p.hashtag_corpus if _norm(t) in means},
                         content_focus=list(p.content_focus), energy=p.energy, hook_angle=p.hook_angle,
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
    """The publish-mode label for the status banner under the provider model (M3): 'dryrun' when the system
    is not live, else the distinct providers that would ACTUALLY publish (e.g. 'postiz' / 'postiz, zernio'),
    else 'live' (live but no resolved channel yet). Replaces the old cfg.poster_backend, which now reads
    'dryrun' on a per-channel-provider deployment even when live — a contradictory 'LIVE (dryrun)' banner.
    Fail-open: any accounts read error degrades to 'live' (the is_live truth is already shown separately)."""
    if not cfg.is_live:
        return "dryrun"
    try:
        provs = sorted({p for _, _, p in Accounts.load(cfg).live_ready_channels()})
        return ", ".join(provs) if provs else "live"
    except Exception:
        return "live"


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
        counts = {"sources": sum(1 for s in led.sources.values() if s.origin_kind == "native"),
                  "batches": len(getattr(led, "batches", {})),
                  "awaiting": awaiting_moment_count(led),    # MOMENTS (== Review worklist), not raw surface posts
                  "awaiting_posts": st.get(PostState.awaiting_approval, 0),  # raw surface count for the tooltip
                  "scheduled": st.get(PostState.queued, 0),
                  "posted": st.get(PostState.published, 0) + st.get(PostState.analyzed, 0)}
        by_account = dict(Counter(p.account for p in led.posts.values()))
    except Exception as exc:                          # the first page an operator sees must never 500
        from fanops.log import get_logger
        get_logger(cfg)("home", "-", "error", err=str(exc)[:160])
        counts = {"sources": 0, "batches": None, "awaiting": 0, "awaiting_posts": 0, "scheduled": 0, "posted": 0}
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


@dataclass
class WorkflowSpine:                    # the whole through-line: the ordered path + the single next move
    stages: list[SpineStage]           # always Make→Review→Schedule→Posted
    next_label: Optional[str]          # the one next-action sentence ("Review 4 clips")
    next_endpoint: Optional[str]       # where it points; None == "caught up", no CTA
    here: Optional[str]                # the current stage key (from the active tab), else None


_SPINE_ORDER = (("make", "Make", "run_panel"), ("review", "Review", "review"),
                ("schedule", "Schedule", "schedule"), ("posted", "Posted", "posted"))


def build_spine(*, counts: dict, has_accounts: bool, here: Optional[str]) -> WorkflowSpine:
    """Pure: turn the Home counts into the Make→Review→Schedule→Posted stepper. A stage is 'active' when it is
    the current tab (`here`), else 'done' once there is output downstream of it, else 'todo'. The next-action
    ladder is strict precondition→pending order: connect an account, then add footage, then clear the awaiting
    worklist, then schedule the approved bucket, else caught-up (no CTA). No ledger, no I/O — trivially tested."""
    src = int(counts.get("sources", 0)); awaiting = int(counts.get("awaiting", 0))
    queued = int(counts.get("scheduled", 0)); posted = int(counts.get("posted", 0))
    done = {"make": src > 0, "review": awaiting == 0 and (queued > 0 or posted > 0), "schedule": posted > 0, "posted": posted > 0}
    nums = {"make": src, "review": awaiting, "schedule": queued, "posted": posted}
    stages = [SpineStage(key=k, label=lbl, endpoint=ep, count=nums[k],
                         state=("active" if k == here else ("done" if done[k] else "todo")))
              for k, lbl, ep in _SPINE_ORDER]
    if not has_accounts:               n = ("Connect an account to begin", "golive_view")
    elif src == 0:                     n = ("Add footage to get started", "run_panel")
    elif awaiting > 0:                 n = (f"Review {awaiting} clip{'s' if awaiting != 1 else ''}", "review")
    elif queued > 0:                   n = (f"Schedule {queued} post{'s' if queued != 1 else ''}", "schedule")
    elif posted > 0:                   n = ("You're all caught up", None)
    else:                              n = ("Run a pass in Make", "run_panel")
    return WorkflowSpine(stages=stages, next_label=n[0], next_endpoint=n[1], here=here)


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
    return GoLiveStatus(
        mode=_publish_mode_label(cfg),               # provider-aware (M3); 'dryrun' when not live
        is_live=cfg.is_live,
        postiz_url=cfg.postiz_url,                    # non-secret; shown so the operator can confirm config
        key_set=cfg.postiz_api_key is not None,       # BOOL only — the API key value is NEVER exposed
        zernio_key_set=cfg.zernio_api_key is not None,  # Zernio slice 4: BOOL only (connect-block state)
        accounts=accts,
        checks=report["checks"],
        notes=report["notes"],
        learning_validated=learning_validated(cfg),    # M3: shows whether the loop is unfrozen (cutover done)
        creative_variation=cfg.creative_variation,     # per-account on-screen hooks toggle state (persona diff)
        account_casting=cfg.account_casting,           # per-account moment casting toggle state (persona diff)
        clip_profile=cfg.clip_profile,                 # clip-length band (talk/song)
        demoted=golive_demoted_accounts(cfg),          # Phase 3: promotable planned accounts
        variant_learning=cfg.variant_learning,         # Phase 6: A/B learning-loop intent flags (default OFF)
        variant_amplify=cfg.variant_amplify, variant_ucb=cfg.variant_ucb, variant_transfer=cfg.variant_transfer)


def gate_rows(cfg: Config) -> list[dict]:
    """Lock-free read-model for the Gates tab (Phase 3a): every PENDING moment/caption agent gate
    with the request context the operator needs to answer it (transcript/signals for moments, the
    surface list for captions). Same enumeration `fanops respond` uses, surfaced for the browser.
    A torn/unreadable request file is skipped (fail-open) rather than 500-ing the tab."""
    from fanops.agentstep import pending, request_path
    rows: list[dict] = []
    for kind in ("moments", "moment_hooks", "captions"):
        for key in pending(cfg, kind=kind):
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
