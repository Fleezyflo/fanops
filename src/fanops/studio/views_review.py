"""Per-account Review read-models for the Studio: the moment cards (SurfacePost/ReviewCard with their
provenance chips + per-surface differentiation), the moment×account matrix, the by-account pivot, and the
bucketing/counting helpers the Review surface renders. Pure (no HTTP/Flask). Depends on views_common for the
shared time/pagination primitives; never on a sibling surface module (schedule/posted/cockpit) — acyclic."""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger, selection_index_for_source
from fanops.models import PostState, SelectionMethod, MomentState
from fanops.personas import casting_directive
from fanops.bands import band_for
from fanops.timeutil import parse_iso
from fanops.studio.views_common import PREPARABLE_STATES, RECENT_WINDOW_HOURS, _imminent, suggest_time
from fanops.studio.actions_common import RENDER_PENDING_REASON


def _handle_display_map(acct_by_handle: dict) -> dict[str, str]:
    return dict(acct_by_handle)


def _display_handle(handle: str, by_norm: dict[str, str]) -> str:
    return by_norm.get(handle, handle)


def _display_handles(handles: list[str], by_norm: dict[str, str]) -> list[str]:
    return sorted({_display_handle(h, by_norm) for h in handles})


@dataclass
class SurfacePost:
    post_id: str
    account: str
    platform: str
    persona: Optional[str]
    caption: str
    hashtags: list[str]
    scheduled_time: Optional[str]
    media_url: str
    state: str
    imminent: bool
    editable: bool
    suggested_time: Optional[str] = None   # P1: ONE deterministic strictly-future suggestion (surface_time
                                           # index=0), set ONLY for editable surfaces; read-only rows carry None.
    hook_preburn: bool = False             # variant_hook set but not yet burned (preview is the base clip)
    persona_hook_removed: Optional[str] = None  # Moment.hooks_by_persona_removed[account] — guard killed this hook
    variant_hook: Optional[str] = None     # persona-differentiation: the per-account on-screen hook burned into
                                           # this surface's media (crosspost burn_hook_only). None when creative_variation is OFF.
    # M3a — "review at scale": surface the per-account differentiation so the operator SEES it on the card.
    length_label: Optional[str] = None     # the clip LENGTH band as seconds, e.g. "28–45s", from Post.clip_profile
                                           # (M2b/M2c stamp). None when no profile (legacy/absent).
    is_account_cut: bool = False           # True iff this surface's Render is a REAL per-account CUT (its own band/
                                           # framing) — vs a hook-stamped shared clip. Read from Render.is_account_cut.
    framing: Optional[str] = None          # the account's PINNED vertical crop ("top"/"center"), or None when it
                                           # inherits the global. Shows the operator's deliberate per-account choice.
    # Phase 4 (pivot fallback badges): the per-account differentiation TRUTH, read FAIL-OPEN from the Render so a
    # surface that silently fell back to a shared cut/hook is flagged ⚠. is_account_cut (above) is the shared-cut
    # signal; hook_source is the shared-hook signal — its value is the HookSource enum string ("shared_fallback"
    # under a shared-hook fallback, "per_account" for its own, "none"/None when no hook / no Render).
    hook_source: Optional[str] = None      # Render.hook_source.value (P3); None when no Render (fail-open dark badge).
    # S2 provenance: the CAUSE of each derived value, stamped in _surface from the account/persona/affinities. Default
    # None → provenance_chips emits the value BARE (the OFF-firewall / legacy shape mints no attribution at all).
    length_cause: Optional[str] = None     # why this length ("persona long" | "@a long"); None = inherited global
    framing_cause: Optional[str] = None    # why this framing ("@a center"); None = inherited global
    cast_cause: Optional[str] = None       # why THIS account got it ("picked for @a"); None = uncast / fans to all
    day: Optional[str] = None              # Phase 4 pivot: the ingest day (clip -> moment -> source.created_at), set
                                           # only on the account-pivot flat rows for the running day header. None elsewhere.
    tag_sources: dict = field(default_factory=dict)   # per-tag provenance {tag: source} from the clip's meta_captions
                                           # (content|corpus|region|graph-reach|discovery|genre-floor). {} when absent
                                           # (legacy entry / no caption yet) -> the chip row simply doesn't render.
    thumb_url: Optional[str] = None        # lazy preview poster for the account-pivot row (/clip-thumb/{clip_id})
    ready: Optional[bool] = None           # publish_readiness advisory (editable surfaces only)
    ready_reason: Optional[str] = None   # WHY not ready (oversize cap, hook drift, …)


@dataclass
class ReviewCard:
    clip_id: str
    preview_url: str
    source_name: str
    label: str                  # operator-facing clip name (timecode-based), never the content-addressed id
    moment_window: str
    reason: str
    language: Optional[str]
    subtitles_burned: bool
    held: bool
    held_reason: Optional[str]
    transcript_excerpt: Optional[str]
    surfaces: list[SurfacePost]
    bucket: str
    clip_state: Optional[str] = None     # the clip's own state — shown on a post-less 'prepared' card
    day: Optional[str] = None            # content-lifecycle Phase 3: the ingest day (YYYY-MM-DD via source.
                                         # created_at) this card buckets under; only set on editable cards (the
                                         # day-sorted approve worklist). None elsewhere. 'undated' = broken lineage.
    hook_removed: Optional[str] = None   # Moment.hook_removed: the model's hook is_weak_hook stripped. Present ->
                                         # the clip is clean but a good hook was killed; Review badges it + offers
                                         # "approve with hook". None -> nothing was stripped.
    batch_id: Optional[str] = None       # Face 4: Post.batch_id (Face 1's denormalized Batch.id) — the REAL
                                         # Batch these posts belong to; None == unbatched (groups under 'Ungrouped').
    batch_title: Optional[str] = None    # the Batch.name (led.get_batch(batch_id).name); None when unbatched.
    # Face 4 follow-up — batch legibility (all default-safe; unbatched/empty -> byte-identical render):
    batch_targets: list[str] = field(default_factory=list)   # B3: Batch.target_accounts ([] == ALL / Ungrouped)
    batch_state: Optional[str] = None    # B3: Batch.state.value (None when unbatched/stale)
    batch_created: Optional[str] = None  # B3: Batch.created_at (None -> rendered '—')
    batch_excluded: int = 0              # B4: active accounts NOT in a non-empty target (0 == ALL/none excluded)
    batch_excluded_names: list[str] = field(default_factory=list)   # S4: the NAMED excluded handles (sorted) —
                                         # the operator reads WHO the batch target drops, not just how many. []
                                         # for an ALL-sentinel/none target (byte-identical to today's bare count).
    affinities: list[str] = field(default_factory=list)      # C3: Moment.affinities (cast reach; [] == all accounts)
    source_key: Optional[str] = None     # Phase 4: the STABLE source-scoping id (clip -> moment.parent_id = Source.id),
                                         # NOT the basename (two sources can share a filename). The ?source= filter
                                         # keys on this; the chip label is the basename. None == broken source lineage.


def _personas(accounts: Accounts) -> dict:
    return {a.handle: a.persona for a in accounts.accounts}

def _timecode(seconds: float) -> str:
    """Whole-second m:ss timecode for an operator-facing clip label (e.g. 73 -> '1:13'). Non-finite
    (inf/nan) degrades to 0:00 — Moment's validator already rejects these, this is belt-and-suspenders."""
    import math
    s = max(0, int(seconds)) if math.isfinite(seconds) else 0
    return f"{s // 60}:{s % 60:02d}"

def _lineage_for_clip(led: Ledger, clip):
    """Return (source_name, label, moment_window, reason, language, transcript_excerpt) for a clip,
    walking clip -> moment -> source. Missing links degrade to safe '—'/None. `label` is the
    operator-facing clip name — a timecode window, never the content-addressed source/clip id."""
    mom = led.moments.get(clip.parent_id)
    src = led.sources.get(mom.parent_id) if mom is not None else None
    source_name = Path(src.source_path).name if (src and src.source_path) else "—"
    if mom is not None:
        moment_window = f"{int(mom.start)}–{int(mom.end)}"                      # en dash (raw seconds)
        label = f"{_timecode(mom.start)}–{_timecode(mom.end)} clip"            # human label
    else:
        moment_window = "—"; label = "Clip"
    reason = mom.reason if (mom and mom.reason) else "—"
    language = src.language if src else None
    excerpt = mom.transcript_excerpt if mom else None
    return source_name, label, moment_window, reason, language, excerpt

def _length_label(profile: Optional[str]) -> Optional[str]:
    """The clip-length band of `profile` as an operator-facing seconds string (e.g. "long" -> "28–45s").
    None for a missing/blank profile — band_for is NOT guessed from None (which would mislabel as the
    talk default). En dash mirrors moment_window."""
    if not (isinstance(profile, str) and profile.strip()):
        return None
    b = band_for(profile)
    return f"{int(b.lo)}–{int(b.hi)}s"

@dataclass
class ProvChip:                            # S2: one "value ← cause" chip (value=WHAT, cause=WHY|None, tone=''|'ok'|'warn')
    value: str
    cause: Optional[str]
    tone: str = ""


def provenance_chips(surface, *, creative_variation: bool = False) -> list[ProvChip]:
    """Pure projection: turn an already-built surface into ordered 'value ← cause' chips. Reads every field via
    getattr, so any surface works — but the CAUSE phrases come only from SurfacePost's length_cause/framing_cause/
    cast_cause; a surface lacking them (the MatrixCell, or any cause-less shape) yields BARE chips (value, no
    attribution), and an undifferentiated surface mints NO chips at all (the OFF-firewall / legacy shape). No
    ledger read; NEVER raises (a torn/odd surface degrades to whatever it could derive). Consumed by S4/S7/S8;
    `creative_variation` gates the shared-cut WARN (a shared cut under OFF is expected, not a fallback)."""
    chips: list[ProvChip] = []
    try:
        if getattr(surface, "length_label", None):
            chips.append(ProvChip(surface.length_label, getattr(surface, "length_cause", None), ""))
        if getattr(surface, "framing", None):
            chips.append(ProvChip(surface.framing, getattr(surface, "framing_cause", None), ""))
        if getattr(surface, "is_account_cut", False):
            chips.append(ProvChip("cut", f"{surface.account}'s own cut", "ok"))
        elif creative_variation:
            chips.append(ProvChip("shared-cut", "no per-account cut — fell back to shared", "warn"))
        if getattr(surface, "hook_source", None) == "shared_fallback":
            chips.append(ProvChip("shared-hook", "no per-account hook — fell back to shared", "warn"))
        if getattr(surface, "cast_cause", None):
            chips.append(ProvChip("cast", surface.cast_cause, ""))
    except Exception:
        return chips
    return chips


def _cast_cause(led: Ledger, post, affinities) -> Optional[str]:
    """RF1: name WHY this account got this moment, from the DURABLE AccountSelection (method-aware) instead of
    the non-durable affinities tag. A degraded provenance (fan_all_default / migrated) is flagged ⚠ so the
    operator SEES a labelled fan-to-all or a lifted-legacy pick rather than a silent gap. Falls back to the
    exact legacy affinities string ONLY for a pre-v9 source that wrote no selection. Pure read; fail-open."""
    clip = led.clips.get(post.parent_id)
    mom = led.moments.get(clip.parent_id) if clip else None
    if mom is None: return None
    sel = led.account_selection_for(mom.parent_id, post.account)
    if sel is None:
        if not led.selections_of_source(mom.parent_id):       # pre-v9 / casting-never-ran -> legacy fallback
            return f"picked for {post.account}" if (affinities and post.account in affinities) else None
        return None                                           # cast source, account not selected -> no cause
    if sel.method == SelectionMethod.fan_all_default: return f"⚠ fans to all ({post.account})"
    if mom.id in set(sel.moment_ids):
        return (f"⚠ picked for {post.account} (migrated)" if sel.method == SelectionMethod.migrated
                else f"picked for {post.account} ({sel.method.value})")
    return None

def _surface(post, *, persona, now: datetime, cfg: Config, led: Ledger, acct=None, affinities=()) -> SurfacePost:
    state = post.state.value
    # an awaiting_approval post is GATED — it cannot ship until approved, so it is never "imminent"
    # (no false "shipping now" badge) and is always editable (edit/regenerate/reschedule before approving).
    awaiting = post.state is PostState.awaiting_approval
    imm = False if awaiting else _imminent(post.scheduled_time, now)
    editable = awaiting or (state == PostState.queued.value and not imm)
    # M3a: the per-account differentiation, surfaced. is_account_cut is the TRUTH on the Render (a failed cut
    # fell back to a shared burn and stays False); framing is the account's own pinned crop (None = inherits global).
    r = led.renders.get(post.render_id) if post.render_id else None
    # S2 provenance: NAME the cause of each derived value (pure, from the already-passed acct/affinities). length
    # attributes to the persona when the account is persona-linked, else the account's own pin, else None (global
    # inherited → value renders bare). framing names the account's pin. cast names the moment's pick for this account.
    prof = post.clip_profile
    # length attributes to the persona ONLY when the linked persona TRULY supplied the cut (persona_owns_profile,
    # stamped at hydration) — a persona_id alone proves nothing (the account's own pin may stand). Else name the
    # account ONLY when its pin actually EQUALS the post's stamped profile (a drifted pin must not be miscredited).
    if prof and getattr(acct, "persona_id", None) and getattr(acct, "persona_owns_profile", False): length_cause = f"persona {prof}"
    elif prof and getattr(acct, "clip_profile", None) == prof: length_cause = f"{post.account} {prof}"
    else: length_cause = None
    framing_cause = f"{post.account} {acct.framing}" if getattr(acct, "framing", None) else None
    cast_cause = _cast_cause(led, post, affinities)
    # per-tag provenance for the surface-edit chip row: read the clip's stored caption entry (fail-open to
    # {} for a legacy entry / no caption yet -> the chip row simply doesn't render).
    _clip = led.clips.get(post.parent_id)
    _mom = led.moments.get(post.parent_id) if _clip is not None else None
    _phr = ((_mom.hooks_by_persona_removed or {}).get(post.account) if _mom is not None else None)
    tag_sources = (_clip.meta_captions.get(f"{post.account}/{post.platform.value}", {}).get("tag_sources", {})
                   if _clip is not None else {})
    ready, ready_reason = (None, None)
    if editable:
        from fanops.studio.views_results import publish_readiness
        ready, ready_reason = publish_readiness(led, post, cfg)
    return SurfacePost(
        post_id=post.id, account=post.account, platform=post.platform.value, persona=persona,
        caption=post.caption, hashtags=list(post.hashtags or []),
        scheduled_time=post.scheduled_time, media_url=(f"/media-preview/{post.id}" if (cfg.creative_variation and (post.variant_hook or "").strip() and not post.render_id) else f"/media/{post.id}"),
        state=state, imminent=imm, editable=editable,
        suggested_time=suggest_time(cfg, post, now=now) if editable else None,   # P1: only editable surfaces
        variant_hook=post.variant_hook,
        persona_hook_removed=_phr,
        hook_preburn=bool((post.variant_hook or "").strip() and not post.render_id),
        length_label=_length_label(post.clip_profile),
        is_account_cut=bool(r and r.is_account_cut),
        framing=(getattr(acct, "framing", None) or None),
        # Phase 4: read Render.hook_source FAIL-OPEN (P3 provenance). A HookSource enum -> its .value string;
        # absent/None Render -> None (the ⚠ shared-hook badge stays dark, byte-identical). getattr-guarded so a
        # legacy Render with no hook_source field never raises.
        hook_source=(getattr(getattr(r, "hook_source", None), "value", None) if r else None),
        length_cause=length_cause, framing_cause=framing_cause, cast_cause=cast_cause,
        tag_sources=tag_sources, thumb_url=f"/clip-thumb/{post.parent_id}",
        ready=ready, ready_reason=ready_reason)

def _card(led: Ledger, clip, posts, bucket: str, cfg: Config, personas: dict, now: datetime,
          active_handles: frozenset = frozenset(), acct_by_handle: Optional[dict] = None) -> ReviewCard:
    source_name, label, window, reason, language, excerpt = _lineage_for_clip(led, clip)
    accts = acct_by_handle or {}
    mom = led.moments.get(clip.parent_id)                 # the moment carries hook_removed (clip -> moment)
    _by_norm = _handle_display_map(accts)
    _affs = _display_handles(led.cast_handles_for(mom.parent_id, mom.id), _by_norm) if mom is not None else []   # MOM-3: DERIVED from durable AccountSelection, not the stored tag
    surfaces = [_surface(p, persona=personas.get(p.account), now=now, cfg=cfg, led=led, acct=accts.get(p.account), affinities=_affs)
                for p in sorted(posts, key=lambda p: (p.account, p.platform.value))]
    src_key = mom.parent_id if mom is not None else None   # Phase 4: stable source id (clip -> moment.parent_id); the ?source= key
    # Face 4: the REAL Batch this card belongs to — Post.batch_id (all posts on one clip share the lineage,
    # so the same batch). Post-less cards (held/prepared, posts == []) carry None -> 'Ungrouped'. Title via
    # led.get_batch defensively (a stale/None batch_id -> None title -> 'Ungrouped' at the grouper).
    bid = next((p.batch_id for p in posts if getattr(p, "batch_id", None)), None)
    b = led.get_batch(bid) if bid else None
    tgts = (b.target_accounts if b is not None else [])
    # B4: how many ACTIVE accounts a non-empty target excludes (the enforcement signal; 0 for ALL-sentinel/none).
    # S4: also NAME them (sorted, deterministic) — the count alone never told the operator WHO got dropped.
    excluded_names = sorted(h for h in active_handles if h not in tgts) if tgts else []
    excluded = len(excluded_names)
    return ReviewCard(
        clip_id=clip.id, preview_url=f"/clips/{clip.id}", source_name=source_name, label=label,
        moment_window=window, reason=reason, language=language, subtitles_burned=cfg.burn_subs,
        held=bool(clip.held), held_reason=clip.held_reason, transcript_excerpt=excerpt,
        surfaces=surfaces, bucket=bucket, clip_state=clip.state.value,
        hook_removed=(mom.hook_removed if mom is not None else None),
        batch_id=bid, batch_title=(b.name if b is not None else None),
        batch_targets=tgts, batch_state=(b.state.value if b is not None else None),
        batch_created=(b.created_at if b is not None else None), batch_excluded=excluded,
        batch_excluded_names=excluded_names,
        affinities=_affs, source_key=src_key)   # MOM-3: derived view, not the stored tag

def _card_day(led: Ledger, card: ReviewCard) -> str:
    """The ingest day (YYYY-MM-DD) a Review card buckets under: clip -> moment -> source.created_at.
    'undated' when the lineage is broken or the source predates the day-anchor. Pure (content-lifecycle Phase 3)."""
    clip = led.clips.get(card.clip_id)
    mom = led.moments.get(clip.parent_id) if clip is not None else None
    src = led.sources.get(mom.parent_id) if mom is not None else None
    ca = src.created_at if src is not None else None
    if not ca: return "undated"
    try: return parse_iso(ca).date().isoformat()
    except (ValueError, TypeError, AttributeError): return "undated"

# ── Slice 2: the moment × account MATRIX (per source) ─────────────────────────
# Rows = a source's moments; columns = the (handle, platform) CHANNELS that actually have posts; a cell =
# that channel's lead post for that moment. The cell EXISTS iff a post exists — "cast" is POST existence,
# NOT live Moment.affinities (which reset on re-decision, so they'd make the grid disagree with reality).
@dataclass
class MatrixCell:
    channel: str; account: str; platform: str
    post_ids: list                 # ALL posts on this channel for this moment (reposts stack) — row/col approve uses these
    lead_post_id: str              # the collapsed representative (prefer awaiting, then most-recent created_at, then id)
    state: str; hook: Optional[str]; length_label: Optional[str]; framing: Optional[str]
    is_account_cut: bool; hook_source: Optional[str]
    preview_url: str; thumb_url: str; multiplicity: int
    length_cause: Optional[str] = None    # S4: WHY this length (persona/account), shown as the chip's hover title
    framing_cause: Optional[str] = None   # S4: WHY this framing (the account's pin), as the chip's hover title
    render_pending: bool = False          # #4: an approve was attempted but the per-account render couldn't be
                                          # made off-lock (warm-miss) — the cell shows a 'render pending' flag

@dataclass
class MatrixRow:
    moment_id: str; window: str; reason: Optional[str]; hook: Optional[str]
    affinities: list               # advisory context ONLY — never drives the "—" cells
    cells: dict                    # channel_key -> MatrixCell | None (None = uncast → renders "—")
    empty_reasons: dict = field(default_factory=dict)   # S4: channel_key -> WHY this cell is empty
                                   # (off-target | budget | no <platform>); absent key == no derivable reason.

@dataclass
class MatrixView:
    source_id: str; source_name: str
    columns: list                  # [(channel_key, handle, platform)] in account-then-platform order
    rows: list                     # [MatrixRow]

_CH = "\x1f"                       # channel-key separator (handle\x1fplatform); unit-sep, never appears in a handle

def _source_label(src) -> str:
    if src is None: return ""
    name = Path(src.source_path).name if getattr(src, "source_path", None) else ""
    return name or src.id

def source_choices(led: Ledger) -> list:
    """Sources that HAVE moments (a catalogued-but-unclipped source has no grid), newest first (created_at
    desc, id asc tiebreak). From led.sources/moments — NOT review cards — so a moments-but-no-cards source stays pickable."""
    have = {m.parent_id for m in led.moments.values()}
    srcs = sorted((s for s in led.sources.values() if s.id in have), key=lambda s: s.id)
    srcs = sorted(srcs, key=lambda s: (s.created_at or ""), reverse=True)   # stable → created_at desc, id asc
    return [(s.id, _source_label(s)) for s in srcs]

def _pick_lead(posts: list):
    """Deterministic cell representative over reposts/ties: prefer awaiting, then most-recent created_at, then highest id."""
    return max(posts, key=lambda p: (p.state is PostState.awaiting_approval, p.created_at or "", p.id))

def _state_matches(post, state: Optional[str]) -> bool:
    if not state: return True
    return post.state.value == state or (state == "awaiting" and post.state is PostState.awaiting_approval)

def _empty_cell_reason(handle: str, platform: str, *, targets, affinities, acct) -> Optional[str]:
    """S4: WHY a (moment × channel) matrix cell is empty — deterministic precedence off-target > budget >
    no-platform. off-target: a non-empty batch target excludes the handle (the enforcement SKIP births no post).
    budget: the moment is cast (non-empty affinities) and this handle wasn't picked. no-platform: the account
    isn't configured for this platform. None: in-scope, cast, on-platform → genuinely just not posted. Pure;
    fail-open (a None/odd acct yields None, never raises) so a torn surface can't 500 the grid."""
    if targets and handle not in targets: return "off-target"
    if affinities and handle not in affinities: return "budget"
    plats = {getattr(p, "value", p) for p in (getattr(acct, "platforms", None) or [])}
    if plats and platform not in plats: return f"no {platform}"
    return None

def review_matrix(led: Ledger, accounts: Accounts, cfg: Config, *, source_id: str, now: datetime,
                  state: Optional[str] = None) -> MatrixView:
    """Per-source grid built from ONE-PASS bucket maps (O(M+C+P), never the nested-accessor quadratic).
    Reuses _surface() for the hook/length/framing/is_account_cut/hook_source truth (which already guards
    render_id=None). A 0-moment source short-circuits to an empty view (the guided empty state)."""
    src = led.sources.get(source_id)
    moments = sorted([m for m in led.moments.values() if m.parent_id == source_id], key=lambda m: m.start)
    if not moments: return MatrixView(source_id=source_id, source_name=_source_label(src), columns=[], rows=[])
    clips_by_moment: dict = {}
    for c in led.clips.values(): clips_by_moment.setdefault(c.parent_id, []).append(c)
    posts_by_clip: dict = {}
    for p in led.posts.values(): posts_by_clip.setdefault(p.parent_id, []).append(p)
    acct_by_handle = {a.handle: a for a in accounts.accounts}
    col_rank = {a.handle: i for i, a in enumerate(accounts.accounts)}
    # S4: the source-level batch target (denormalized on Source.batch_id) — fans an off-target reason to every
    # empty cell of an excluded channel. Read fail-open; [] (ALL-sentinel / no batch) -> off-target never fires.
    _batch = led.get_batch(getattr(src, "batch_id", None)) if getattr(src, "batch_id", None) else None
    targets = list(_batch.target_accounts) if _batch is not None else []
    # MOL-82: SCOPE the cast-handles lookup to this source — build the moment->handles index ONCE (one scan of
    # the source's selections) instead of cast_handles_for rescanning the whole ledger-wide map per moment.
    _cast_idx = selection_index_for_source(led, source_id)
    _by_norm = _handle_display_map(acct_by_handle)
    channels: dict = {}; rows = []
    for m in moments:
        mposts = [p for c in clips_by_moment.get(m.id, []) for p in posts_by_clip.get(c.id, [])]
        mposts = [p for p in mposts if _state_matches(p, state)]
        by_channel: dict = {}
        for p in mposts:
            key = f"{p.account}{_CH}{p.platform.value}"
            by_channel.setdefault(key, []).append(p); channels[key] = (p.account, p.platform.value)
        cells: dict = {}
        _aff = _display_handles(_cast_idx.get(m.id, []), _by_norm)   # MOM-3: DERIVED from the durable AccountSelection (operator overrides included), not the legacy Moment.affinities tag; MOL-82: O(1) scoped-index lookup, not a per-moment whole-map rescan
        for key, plist in by_channel.items():
            lead = _pick_lead(plist)
            sp = _surface(lead, persona=None, now=now, cfg=cfg, led=led, acct=acct_by_handle.get(lead.account), affinities=_aff)
            cells[key] = MatrixCell(channel=key, account=lead.account, platform=lead.platform.value,
                                    post_ids=[p.id for p in plist], lead_post_id=lead.id, state=sp.state,
                                    hook=sp.variant_hook, length_label=sp.length_label, framing=sp.framing,
                                    is_account_cut=sp.is_account_cut, hook_source=sp.hook_source,
                                    preview_url=f"/clips/{lead.parent_id}", thumb_url=f"/clip-thumb/{lead.parent_id}",
                                    multiplicity=len(plist), length_cause=sp.length_cause, framing_cause=sp.framing_cause,
                                    render_pending=(lead.error_reason == RENDER_PENDING_REASON))   # #4: warm-miss flag
        rows.append(MatrixRow(moment_id=m.id, window=f"{int(m.start)}–{int(m.end)}", reason=m.reason,
                              hook=m.hook, affinities=_aff, cells=cells))   # MOM-3: derived view, not the stored tag
    cols = sorted(channels.items(), key=lambda kv: (col_rank.get(kv[1][0], 999), kv[1][1]))
    columns = [(k, h, pf) for k, (h, pf) in cols]
    # S4: now the column set is known, give every empty "—" cell a reason (off-target > budget > no-platform).
    for row in rows:
        for key, handle, platform in columns:
            if row.cells.get(key) is None:
                reason = _empty_cell_reason(handle, platform, targets=targets,
                                            affinities=row.affinities, acct=acct_by_handle.get(handle))
                if reason: row.empty_reasons[key] = reason
    return MatrixView(source_id=source_id, source_name=_source_label(src), columns=columns, rows=rows)

# ── RF6: the per-account LANES (account-first Review) ─────────────────────────
# A LANE is ONE account's view of a source: every DECIDED moment as a row, with whether THIS account is cast
# on it — read from the DURABLE AccountSelection, NOT post existence (the matrix's rule). That inversion is the
# whole point: a lane can show a cast moment with no post yet, AND a targeted account with ZERO posts (a column
# the matrix structurally cannot draw). Data-only (no string formatting — the header chips live in the template,
# matching MatrixCell). cast state truth = led.moment_ids_selected_for; fans-to-all = no record OR fan_all_default.
@dataclass
class LaneRow:
    moment_id: str; window: str; reason: Optional[str]; hook: Optional[str]
    is_cast: bool                       # m.id ∈ this account's AccountSelection.moment_ids (durable truth, not a post)
    preview_url: str                    # the MASTER clip player (clip -> source); '' when this moment has no clip yet
    post: Optional[SurfacePost] = None  # this account's LEAD post for the moment (matrix collapse: awaiting>newest>id); None = no post

@dataclass
class AccountLane:
    account: str
    rows: list                          # [LaneRow] — the source's decided moments, this account's cast/uncast per row
    method: Optional[str]               # AccountSelection.method.value (provenance: llm/operator/migrated/fan_all_default); None = no record
    cast_count: int                     # how many of THESE rows (decided moments) the account is cast on
    moment_count: int                   # the row count (decided moments) — the "N of M" denominator
    fans_all: bool                      # sel is None OR fan_all_default — every row uncast, header reads "fans to all"
    zero_cast: bool = False             # MOM-2: persona-bearing candidate with NO record on a CAST source -> posts NOTHING (operator must cast manually)

@dataclass
class LaneView:
    source_id: str; source_name: str
    lanes: list                         # [AccountLane] in active-first then-alpha account order

def account_lanes(led: Ledger, accounts: Accounts, cfg: Config, *, source_id: str, now: datetime,
                  state: Optional[str] = None) -> LaneView:
    """One LANE per account for ONE source: rows = the source's DECIDED moments, each flagged is_cast from the
    account's durable AccountSelection (NOT a post). Reuses the matrix's lead-post collapse for the post side and
    _surface() for the surface shape. The lane UNIVERSE = active accounts ∪ accounts-with-a-selection ∪ accounts-
    with-a-post (so a zero-post targeted account, or a selection-only handle, still gets a lane — the matrix can't).
    fan-to-all (sel is None OR fan_all_default) -> every row is_cast=False (NEVER misread as "cast on everything";
    the gate semantics are unchanged). A 0-moment source yields lanes with empty rows (no crash)."""
    src = led.sources.get(source_id)
    moments = sorted([m for m in led.moments.values()
                      if m.parent_id == source_id and m.state == MomentState.decided], key=lambda m: m.start)
    moment_ids = {m.id for m in moments}
    # one-pass lineage maps scoped to this source's moments (mirror review_matrix) for the post side of each row.
    clips_by_moment: dict = {}
    for c in led.clips.values():
        if c.parent_id in moment_ids: clips_by_moment.setdefault(c.parent_id, []).append(c)
    posts_by_clip: dict = {}
    clip_ids = {c.id for cs in clips_by_moment.values() for c in cs}
    for p in led.posts.values():
        if p.parent_id in clip_ids: posts_by_clip.setdefault(p.parent_id, []).append(p)
    acct_by_handle = {a.handle: a for a in accounts.accounts}
    personas = _personas(accounts)
    # MOL-82: SCOPE the cast-handles lookup to this source — build the moment->handles index ONCE from a single
    # scan of the source's selections, reused for every lane row below (was cast_handles_for rescanning the whole
    # ledger-wide map per (account × moment)). _source_sels is that same one scan, reused for the universe + has-chosen.
    _source_sels = led.selections_of_source(source_id)
    _cast_idx = selection_index_for_source(led, source_id)
    # lane universe: active-first (in accounts.json order), then any has-selection / has-post handle, alpha.
    active_order = [a.handle for a in accounts.accounts]
    _by_norm = _handle_display_map(acct_by_handle)
    extra = {_display_handle(s.account, _by_norm) for s in _source_sels} | {_display_handle(p.account, _by_norm) for p in led.posts.values() if p.parent_id in clip_ids}
    handles = active_order + sorted(h for h in extra if h not in set(active_order))
    # first-clip per moment owns the MASTER preview (clip -> source player), matching the cards/matrix.
    preview_by_moment = {mid: f"/clips/{cs[0].id}" for mid, cs in clips_by_moment.items() if cs}
    # MOM-2: who was a casting CANDIDATE (active + persona-bearing, the SAME predicate request_moment_casting
    # filters on) and is this a CAST source (any chosen selection — moment_ids non-empty per the sum-type)? A
    # candidate with NO record on a cast source posts nothing -> the lane shows a "0 cast" badge (visibility only).
    candidates = {a.handle for a in accounts.active() if casting_directive(a)}
    source_has_chosen = any(s.moment_ids for s in _source_sels)   # MOL-82: reuse the one scan, no rescan
    lanes: list = []
    for handle in handles:
        sel = led.account_selection_for(source_id, handle)
        cast_ids = led.moment_ids_selected_for(source_id, handle)    # set() for BOTH no-record AND fan_all_default (read-model only)
        fans_all = sel is None or sel.method == SelectionMethod.fan_all_default
        acct = acct_by_handle.get(handle); persona = personas.get(handle)
        rows: list = []
        for m in moments:
            mposts = [p for c in clips_by_moment.get(m.id, []) for p in posts_by_clip.get(c.id, [])
                      if p.account == handle and _state_matches(p, state)]
            sp = _surface(_pick_lead(mposts), persona=persona, now=now, cfg=cfg, led=led, acct=acct,
                          affinities=_display_handles(_cast_idx.get(m.id, []), _by_norm)) if mposts else None   # MOM-3: derived view, not the stored tag; MOL-82: O(1) scoped-index lookup, not a per-(account×moment) whole-map rescan
            rows.append(LaneRow(moment_id=m.id, window=f"{int(m.start)}–{int(m.end)}", reason=m.reason,
                                hook=m.hook, is_cast=m.id in cast_ids,
                                preview_url=preview_by_moment.get(m.id, ""), post=sp))
        # MOM-2: a candidate with NO durable record (sel is None -> not a fan_all_default/pending row either) on
        # a cast source is denied everywhere -> posts nothing. Flag it; the operator casts it manually (no auto-fan).
        zero_cast = sel is None and source_has_chosen and handle in candidates
        lanes.append(AccountLane(account=handle, rows=rows, method=(sel.method.value if sel else None),
                                 cast_count=len(cast_ids & moment_ids), moment_count=len(moments),
                                 fans_all=fans_all, zero_cast=zero_cast))
    return LaneView(source_id=source_id, source_name=_source_label(src), lanes=lanes)

# Phase 4: the ?state= filter maps an operator-facing state word to its ReviewCard.bucket. 'awaiting' is the
# primary worklist state (editable cards); 'approved' surfaces have LEFT Review for the Schedule, so it maps to
# the read-only 'recent' (already-shipped) bucket that Review still shows. An unknown word never reaches here
# (the arg reader maps it to None upstream); this map is the single source of truth for the legal set.
_STATE_TO_BUCKET = {"awaiting": "editable", "approved": "recent", "held": "held", "prepared": "prepared"}


def review_buckets(led: Ledger, accounts: Accounts, cfg: Config, *, now: datetime,
                   account: Optional[str] = None, batch: Optional[str] = None,
                   source: Optional[str] = None, state: Optional[str] = None) -> list[ReviewCard]:
    """Three buckets (spec §6): editable (awaiting_approval posts grouped by clip — the approve worklist),
    recent (published/analyzed within RECENT_WINDOW_HOURS), held (clips with held=True, no posts). A clip
    may appear in both editable and recent (different posts). Approved (`queued`) posts have left Review for
    the Schedule bucket — they are NOT shown here (post-approval-lifecycle).

    P5: when `account` is set, keep a card iff ANY of its surfaces is on that account (filter on
    SurfacePost.account — a fan-out card is one card with N surfaces, so it survives if any surface matches).
    The filter runs AFTER the cards list + sort are built, so it cannot perturb order or review_counts; a
    post-less card (prepared/held, surfaces == []) has no surface on any account -> excluded under any
    non-None filter, present under None (byte-identical default)."""
    personas = _personas(accounts)
    acct_by_handle = {a.handle: a for a in accounts.accounts}         # M3a: per-surface framing lookup by handle
    active_handles = frozenset(a.handle for a in accounts.active())   # B4: active universe for the excluded-count
    cards: list[ReviewCard] = []
    queued_by_clip: dict[str, list] = {}
    recent_by_clip: dict[str, list] = {}
    recent_cutoff = now - timedelta(hours=RECENT_WINDOW_HOURS)
    for p in led.posts.values():
        if p.state is PostState.awaiting_approval:
            queued_by_clip.setdefault(p.parent_id, []).append(p)
        elif p.state in (PostState.published, PostState.analyzed):
            keep = True
            if p.scheduled_time:
                try:
                    dt = parse_iso(p.scheduled_time)
                    keep = dt.tzinfo is not None and dt >= recent_cutoff
                except (ValueError, TypeError):
                    keep = True   # unparseable but shipped -> still show it
            if keep:
                recent_by_clip.setdefault(p.parent_id, []).append(p)
    editable_cards: list[ReviewCard] = []
    for clip_id, posts in queued_by_clip.items():
        clip = led.clips.get(clip_id)
        if clip is not None and not clip.held:        # a held clip belongs ONLY in the held bucket
            editable_cards.append(_card(led, clip, posts, "editable", cfg, personas, now, active_handles, acct_by_handle))
    # editable cards: day-sorted (newest ingest day first, 'undated' last) so _review_body.html can emit a
    # running day-header across the paginated slice WITHOUT touching pagination (content-lifecycle Phase 3 H8).
    for c in editable_cards: c.day = _card_day(led, c)
    editable_cards.sort(key=lambda c: (c.day != "undated", c.day), reverse=True)   # undated (False) sorts last under reverse
    cards.extend(editable_cards)
    editable_clip_ids = {c.clip_id for c in editable_cards}   # Face 4: dedup — a clip already in the approve
    for clip_id, posts in recent_by_clip.items():             # worklist must not ALSO render a 'recent' card
        if clip_id in editable_clip_ids:                      # (two <video> for one clip — the volume fix)
            continue
        clip = led.clips.get(clip_id)
        if clip is not None and not clip.held:        # (same rule for the recent/shipped bucket)
            cards.append(_card(led, clip, posts, "recent", cfg, personas, now, active_handles, acct_by_handle))
    clips_with_posts = {p.parent_id for p in led.posts.values()}
    for clip in led.clips.values():
        if clip.held:
            cards.append(_card(led, clip, [], "held", cfg, personas, now, active_handles, acct_by_handle))
        elif clip.id not in clips_with_posts and clip.state in PREPARABLE_STATES:
            cards.append(_card(led, clip, [], "prepared", cfg, personas, now, active_handles, acct_by_handle))
    if account is not None:        # P5: keep a card iff a SURFACE matches (post-less cards have none -> dropped)
        cards = [c for c in cards if any(s.account == account for s in c.surfaces)]
    if batch is not None:          # B2: drill into ONE batch — keep cards whose denormalized Batch.id matches
        cards = [c for c in cards if c.batch_id == batch]
    if source is not None:         # Phase 4: drill into ONE source — keep cards on that stable source id (NOT basename)
        cards = [c for c in cards if c.source_key == source]
    if state is not None:          # Phase 4: keep one state's cards (awaiting/approved/held/prepared -> bucket)
        target = _STATE_TO_BUCKET.get(state)
        cards = [c for c in cards if c.bucket == target] if target else cards
    return cards


def review_counts(cards: list[ReviewCard]) -> dict:
    """Bucket tallies for the Review tab's live auto-poller, computed from the SAME cards the worklist
    renders (no extra ledger read, no logic drift). awaiting=approve-worklist size (editable cards),
    prepared=post-less produced clips, held=brand-risk holds. 'recent' (already shipped) is not a
    waiting count and is excluded. Pure — trivially testable, single source of truth for the strip."""
    from collections import Counter
    c = Counter(card.bucket for card in cards)
    return {"awaiting": c.get("editable", 0), "prepared": c.get("prepared", 0), "held": c.get("held", 0)}


def awaiting_moment_count(led: Ledger) -> int:
    """Single source of truth for the 'awaiting' headline shared by the Review tab and the status Home: the
    number of MOMENTS (distinct non-held clips) with >=1 awaiting_approval post — i.e. the size of the Review
    approve-worklist (editable cards), NOT the raw awaiting-POST count. A clip fans out to many per-account
    surface posts, so counting posts overstates the worklist (the 'Home 57 vs Review 17' bug). Mirrors the
    editable-bucket rule in review_buckets exactly (non-held existing clip with an awaiting post) so the Home
    headline and the Review worklist can never drift. Pure, lock-free read."""
    seen: set[str] = set()
    for p in led.posts.values():
        if p.state is PostState.awaiting_approval:
            clip = led.clips.get(p.parent_id)
            if clip is not None and not clip.held:
                seen.add(p.parent_id)
    return len(seen)


def review_awaiting_by_account(cards: list[ReviewCard]) -> dict[str, int]:
    """Editable awaiting surface count per account — powers the per-account approve strip."""
    from collections import Counter
    c = Counter()
    for card in cards:
        if card.bucket != "editable":
            continue
        for s in card.surfaces:
            if s.editable:
                c[s.account] += 1
    return dict(c)

def review_progress(cards: list[ReviewCard]) -> dict:
    """Phase 4 progress header: per-scope counts (awaiting/approved/held/prepared) over the SAME cards the
    worklist renders — a pure read, re-derived each htmx swap so the count rides the URL (mirrors review_counts).
    'approved' counts the read-only 'recent' (already-shipped) bucket; Review is the AWAITING worklist, so
    'awaiting' leads. Single source of truth for the pivot/per-account progress line. Trivially testable."""
    from collections import Counter
    c = Counter(card.bucket for card in cards)
    return {"awaiting": c.get("editable", 0), "approved": c.get("recent", 0),
            "held": c.get("held", 0), "prepared": c.get("prepared", 0)}


def source_universe(cards: list[ReviewCard]) -> list:
    """Phase 4 source-filter chips: the distinct sources present in THIS (unfiltered) card list, as
    [(source_key, basename)] in FIRST-APPEARANCE order. Keyed on the stable Source.id (ReviewCard.source_key) so
    two sources sharing a filename never collide; labelled with the basename (already on card.source_name) for the
    operator. Cards with broken source lineage (source_key None) are skipped. Pure — mirrors the chip-universe
    helpers; the template renders a GET link per entry like _account_filter.html."""
    seen: dict = {}                                    # source_key -> basename; dict preserves first-appearance order
    for c in cards:
        if c.source_key is not None and c.source_key not in seen:
            seen[c.source_key] = c.source_name or c.source_key
    return list(seen.items())


def account_pivot_rows(led: Ledger, accounts: Accounts, cfg: Config, *, now: datetime, account: Optional[str],
                       batch: Optional[str] = None, source: Optional[str] = None,
                       state: Optional[str] = None) -> list[SurfacePost]:
    """Phase 4 account-first PIVOT: ONE account's entire run as a flat SurfacePost list (NOT moment cards), in
    the upstream day-sort order, ready to paginate. Reuses review_buckets (already account/batch/source/state
    filtered) + the surfaces it built — flatten every card's surfaces, keeping ONLY the chosen account (a fan-out
    card carries N surfaces; we want @x's row, not @y's). A blank/None account -> [] (the pivot is meaningless
    without an account; the route falls back to moment-first). Each row carries its card's `day` for the running
    day header (group_review_by_account_surface). Pure-ish (one lock-free ledger read); never raises."""
    handle = (account or "").strip()
    if not handle:
        return []
    cards = review_buckets(led, accounts, cfg, now=now, account=handle, batch=batch, source=source, state=state)
    rows: list[SurfacePost] = []
    for c in cards:                                    # cards arrive day-sorted; preserve that order across surfaces
        for s in c.surfaces:
            if s.account == handle:
                rows.append(replace(s, day=c.day))     # immutable copy: stamp the card's ingest day for the header (no shared-ref mutation)
    return rows


def group_review_by_account_surface(rows: list) -> list:
    """Phase 4 pivot grouper: group the flat account-pivot SurfacePost rows by their ingest `day` for a running
    day header, FIRST-APPEARANCE order (preserves the upstream day-sort), within-day INPUT order. Pure — mirrors
    group_review_by_batch / group_schedule_by_account. Returns [(day, [SurfacePost])]; a None day renders 'undated'."""
    groups: dict = {}                                  # day -> [rows]; dict preserves first-appearance order
    for r in rows:
        groups.setdefault(getattr(r, "day", None), []).append(r)
    return [(d if d is not None else "undated", rs) for d, rs in groups.items()]


def surface_for_post(led: Ledger, accounts: Accounts, post_id: str, *, now: datetime, cfg: Config) -> Optional[SurfacePost]:
    """The single-surface read-model for ONE post — used by the Regenerate/Reschedule/Clear routes to
    re-render just that surface's editable field after a mutation. None if the post is gone. `cfg` is
    needed for the P1 suggested_time (surface_time)."""
    p = led.posts.get(post_id)
    if p is None:
        return None
    acct = next((a for a in accounts.accounts if a.handle == p.account), None)
    return _surface(p, persona=_personas(accounts).get(p.account), now=now, cfg=cfg, led=led, acct=acct)


def group_review_by_batch(cards: list) -> list:
    """Group editable ReviewCards by the REAL Batch (Post.batch_id) for collapsible per-batch <details>
    sections. Pure; FIRST-APPEARANCE batch order (preserves the upstream day-sort), within-batch INPUT order.
    Unbatched cards (batch_id is None) collect under ONE (None, 'Ungrouped', [...]) group that sorts LAST.
    Mirrors group_schedule_by_account but first-appearance (NOT sorted), so the day-sort survives. Returns
    [(batch_id, batch_title, [ReviewCard])]; a None/stale batch_title renders as 'Ungrouped'."""
    groups: dict = {}                                  # batch_id -> [cards]; dict preserves first-appearance order
    titles: dict = {}
    for c in cards:
        groups.setdefault(c.batch_id, []).append(c)
        titles.setdefault(c.batch_id, c.batch_title or "Ungrouped")
    out = [(bid, titles[bid], cs) for bid, cs in groups.items() if bid is not None]
    if None in groups:
        out.append((None, "Ungrouped", groups[None]))  # the unbatched group ALWAYS sorts LAST
    return out
