"""Pure read-model builders for the Studio (no HTTP, no Flask). Each request re-loads the ledger
(lock-free) and assembles these dataclasses; templates render them. Mutations live in actions.py."""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.models import LIFT_SCORE, ClipState, PostState, StitchState
from fanops.bands import band_for                     # M3a: resolve a per-account clip_profile -> its length band (seconds)
from fanops.timeutil import parse_iso

IMMINENT_THRESHOLD_MINUTES = 5     # spec §4: a post within this of now (or past) is edit-disabled
RECENT_WINDOW_HOURS = 24           # spec §6: "what just shipped" read-only context window
GRID_PAGE_SIZE = 24                # max cards rendered per surface page — rendering all 164 <video> at
                                   # once is a real perf + usability problem (the black-box-wall report);
                                   # the total stays VISIBLE with a show-more link, never silent truncation


@dataclass
class GridPage:
    """A paginated slice of a card/row list for the Review/Publish grids. `items` is the visible page;
    `total` is the full count (shown so nothing is silently truncated); `next_offset` is the offset for
    the show-more link, or None when this is the last page."""
    items: list
    total: int
    offset: int
    next_offset: Optional[int]


def paginate(rows: list, offset: int, *, page_size: int = GRID_PAGE_SIZE) -> "GridPage":
    """Slice `rows` to one page. Clamps a negative/oversize offset into range; next_offset is None when
    the page reaches the end. Pure — no I/O, trivially testable."""
    total = len(rows)
    off = max(0, min(offset, total))
    page = rows[off:off + page_size]
    nxt = off + page_size if off + page_size < total else None
    return GridPage(items=page, total=total, offset=off, next_offset=nxt)
# A clip is "prepared" (produced, awaiting crosspost) when it has NO posts yet and isn't held — these
# post-less clips used to vanish from Review entirely (the 57-clips-0-posts bug). Only actionable
# in-flight states qualify; retired/error/terminal clips are not surfaced as prepare-able.
PREPARABLE_STATES = (ClipState.rendered, ClipState.captions_requested, ClipState.captioned, ClipState.queued)


def accounts_in(rows) -> list[str]:
    """Distinct, sorted account handles present in a built read-model list — the per-surface chip UNIVERSE,
    derived from the POSTS in that list (never Accounts.active(), so a retired account's history stays
    filterable). Dual-shape (P5 R4): dataclass rows expose `.account`; publish_queue returns plain dicts
    with `r["account"]`. Review CARDS are not rows (a card has a list of `surfaces`, no scalar account) — do
    NOT pass cards here; collect their surface accounts with `{s.account for c in cards for s in c.surfaces}`."""
    return sorted({(r["account"] if isinstance(r, dict) else r.account) for r in rows})


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
    variant_hook: Optional[str] = None     # persona-differentiation: the per-account on-screen hook burned into
                                           # this surface's media (crosspost burn_hook_only). None when creative_variation is OFF.
    # M3a — "review at scale": surface the per-account differentiation so the operator SEES it on the card.
    length_label: Optional[str] = None     # the clip LENGTH band as seconds, e.g. "28–45s", from Post.clip_profile
                                           # (M2b/M2c stamp). None when no profile (legacy/absent).
    is_account_cut: bool = False           # True iff this surface's Render is a REAL per-account CUT (its own band/
                                           # framing) — vs a hook-stamped shared clip. Read from Render.is_account_cut.
    framing: Optional[str] = None          # the account's PINNED vertical crop ("top"/"center"), or None when it
                                           # inherits the global. Shows the operator's deliberate per-account choice.


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
    affinities: list[str] = field(default_factory=list)      # C3: Moment.affinities (cast reach; [] == all accounts)


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
    suggested_time: Optional[str] = None   # P1: ONE deterministic strictly-future suggestion (surface_time
                                           # index=0), set ONLY for editable rows; read-only past rows carry None.
    batch_id: Optional[str] = None         # Face 5: denormalized Post.batch_id (None == ungrouped)
    batch_title: Optional[str] = None      # Batch.name via led.get_batch (None when unbatched/dangling)
    caption: str = ""                      # P5: the post's caption, shown as a Schedule column so the
                                           # operator reads WHAT each scheduled row ships without opening it
    variant_hook: Optional[str] = None     # Render foundation: the per-account on-screen hook (mirror of
                                           # Render.hook_text) so the operator SEES which hook each account ships


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
    tag_lean: Optional[str] = None   # persona-differentiation tag knob: tasteful|underground|bold (None -> none)


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
    cast_pick_budget: int = 3          # moments/account/run (FANOPS_CAST_PICK_BUDGET)
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


@dataclass
class LiftView:
    variant_rows: list[LiftRow]
    variant_empty_reason: Optional[str]
    amplify_present: bool
    amplify_rows: list[LiftRow]
    amplify_empty_reason: Optional[str]


def _imminent(scheduled_time: Optional[str], now: datetime,
              threshold_min: int = IMMINENT_THRESHOLD_MINUTES) -> bool:
    """True (edit-disabled) when the time is missing, unparseable, naive, already due, or within
    `threshold_min` of `now`. Fail-safe: any doubt -> imminent (read-only), never editable. `now`
    must be timezone-aware UTC."""
    if not scheduled_time:
        return True
    try:
        dt = parse_iso(scheduled_time)
    except (ValueError, TypeError):
        return True
    if dt.tzinfo is None:
        return True
    return dt <= now + timedelta(minutes=threshold_min)


def suggest_time(cfg: Config, post, *, now: datetime) -> str:
    """ONE deterministic, strictly-future ISO-Z suggestion for a single post (P1). REUSES crosspost's
    proven surface_time with index=0 — a single anchored near-future time, NEVER a 40-min stagger (the
    stagger only appears at index>0, reachable only via operator Reschedule-all). Depends solely on
    account/platform/parent_id (all on the Post) + lead_minutes, so it never resolves a clip/moment and
    survives broken lineage. Pure, lock-free, no ledger write. Local import keeps views->crosspost acyclic
    (mirrors reschedule_bucket). Anti-degenerate: a raw value <= now (seed%50==0 && jitter==0 with lead 0)
    gets the smallest deterministic +1s nudge so the suggestion is never == now (which would re-open the
    publish-now hole) — NOT a cadence rule, just 'never equal now'."""
    from fanops.crosspost import surface_time
    from fanops.timeutil import iso_z
    raw = surface_time(now, post.account, post.platform.value, now.date().isoformat(), 0,
                       clip_id=post.parent_id or "", lead_minutes=cfg.publish_lead_minutes)
    if parse_iso(raw) <= now:
        return iso_z(now + timedelta(seconds=1))
    return raw


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

def _surface(post, *, persona, now: datetime, cfg: Config, led: Ledger, acct=None) -> SurfacePost:
    state = post.state.value
    # an awaiting_approval post is GATED — it cannot ship until approved, so it is never "imminent"
    # (no false "shipping now" badge) and is always editable (edit/regenerate/reschedule before approving).
    awaiting = post.state is PostState.awaiting_approval
    imm = False if awaiting else _imminent(post.scheduled_time, now)
    editable = awaiting or (state == PostState.queued.value and not imm)
    # M3a: the per-account differentiation, surfaced. is_account_cut is the TRUTH on the Render (a failed cut
    # fell back to a shared burn and stays False); framing is the account's own pinned crop (None = inherits global).
    r = led.renders.get(post.render_id) if getattr(post, "render_id", None) else None
    return SurfacePost(
        post_id=post.id, account=post.account, platform=post.platform.value, persona=persona,
        caption=post.caption, hashtags=list(post.hashtags or []),
        scheduled_time=post.scheduled_time, media_url=f"/media/{post.id}",
        state=state, imminent=imm, editable=editable,
        suggested_time=suggest_time(cfg, post, now=now) if editable else None,   # P1: only editable surfaces
        variant_hook=getattr(post, "variant_hook", None),   # persona on-screen hook (None when variation OFF)
        length_label=_length_label(getattr(post, "clip_profile", None)),
        is_account_cut=bool(r and getattr(r, "is_account_cut", False)),
        framing=(getattr(acct, "framing", None) or None))

def _card(led: Ledger, clip, posts, bucket: str, cfg: Config, personas: dict, now: datetime,
          active_handles: frozenset = frozenset(), acct_by_handle: Optional[dict] = None) -> ReviewCard:
    source_name, label, window, reason, language, excerpt = _lineage_for_clip(led, clip)
    accts = acct_by_handle or {}
    surfaces = [_surface(p, persona=personas.get(p.account), now=now, cfg=cfg, led=led, acct=accts.get(p.account))
                for p in sorted(posts, key=lambda p: (p.account, p.platform.value))]
    mom = led.moments.get(clip.parent_id)                 # the moment carries hook_removed + affinities (clip -> moment)
    # Face 4: the REAL Batch this card belongs to — Post.batch_id (all posts on one clip share the lineage,
    # so the same batch). Post-less cards (held/prepared, posts == []) carry None -> 'Ungrouped'. Title via
    # led.get_batch defensively (a stale/None batch_id -> None title -> 'Ungrouped' at the grouper).
    bid = next((p.batch_id for p in posts if getattr(p, "batch_id", None)), None)
    b = led.get_batch(bid) if bid else None
    tgts = (b.target_accounts if b is not None else [])
    # B4: how many ACTIVE accounts a non-empty target excludes (the enforcement signal; 0 for ALL-sentinel/none).
    excluded = len([h for h in active_handles if h not in tgts]) if tgts else 0
    return ReviewCard(
        clip_id=clip.id, preview_url=f"/clips/{clip.id}", source_name=source_name, label=label,
        moment_window=window, reason=reason, language=language, subtitles_burned=cfg.burn_subs,
        held=bool(clip.held), held_reason=clip.held_reason, transcript_excerpt=excerpt,
        surfaces=surfaces, bucket=bucket, clip_state=clip.state.value,
        hook_removed=(mom.hook_removed if mom is not None else None),
        batch_id=bid, batch_title=(b.name if b is not None else None),
        batch_targets=tgts, batch_state=(b.state.value if b is not None else None),
        batch_created=(b.created_at if b is not None else None), batch_excluded=excluded,
        affinities=(getattr(mom, "affinities", None) or []))

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

def review_buckets(led: Ledger, accounts: Accounts, cfg: Config, *, now: datetime,
                   account: Optional[str] = None, batch: Optional[str] = None) -> list[ReviewCard]:
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
    return cards


def review_counts(cards: list[ReviewCard]) -> dict:
    """Bucket tallies for the Review tab's live auto-poller, computed from the SAME cards the worklist
    renders (no extra ledger read, no logic drift). awaiting=approve-worklist size (editable cards),
    prepared=post-less produced clips, held=brand-risk holds. 'recent' (already shipped) is not a
    waiting count and is excluded. Pure — trivially testable, single source of truth for the strip."""
    from collections import Counter
    c = Counter(card.bucket for card in cards)
    return {"awaiting": c.get("editable", 0), "prepared": c.get("prepared", 0), "held": c.get("held", 0)}


def surface_for_post(led: Ledger, accounts: Accounts, post_id: str, *, now: datetime, cfg: Config) -> Optional[SurfacePost]:
    """The single-surface read-model for ONE post — used by the Regenerate/Reschedule/Clear routes to
    re-render just that surface's editable field after a mutation. None if the post is gone. `cfg` is
    needed for the P1 suggested_time (surface_time)."""
    p = led.posts.get(post_id)
    if p is None:
        return None
    acct = next((a for a in accounts.accounts if a.handle == p.account), None)
    return _surface(p, persona=_personas(accounts).get(p.account), now=now, cfg=cfg, led=led, acct=acct)


def _batch_title(led: Ledger, bid: Optional[str]) -> Optional[str]:
    # Face 5: resolve a denormalized Post.batch_id to its Batch.name defensively — a dangling id (batch gone)
    # yields None (renders no label), never an AttributeError. Dict lookup, no I/O.
    b = led.get_batch(bid) if bid else None
    return b.name if b is not None else None


def schedule_rows(led: Ledger, cfg: Config, *, now: datetime,
                  account: Optional[str] = None, batch: Optional[str] = None) -> list[ScheduleRow]:
    """Queued posts (the editable timeline) plus recent published/analyzed posts (read-only past),
    sorted chronologically by scheduled_time. Rows with no/naive/unparseable time sort last. P5: an optional
    `account` filters AFTER the time-sort (the None default stays byte-identical); the per-account display
    GROUPING is a separate pure step (group_schedule_by_account) so the read-model order never changes."""
    recent_cutoff = now - timedelta(hours=RECENT_WINDOW_HOURS)
    rows: list[ScheduleRow] = []
    for p in led.posts.values():
        if p.state is PostState.queued:
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
        editable = (state == PostState.queued.value and not imm)
        rows.append(ScheduleRow(
            post_id=p.id, scheduled_time=p.scheduled_time, account=p.account,
            platform=p.platform.value, clip_id=p.parent_id, state=state, imminent=imm,
            editable=editable, integration_id=p.account_id,
            suggested_time=suggest_time(cfg, p, now=now) if editable else None,   # P1: only editable rows
            batch_id=p.batch_id, batch_title=_batch_title(led, p.batch_id),       # Face 5: batch legibility
            caption=p.caption,                                                    # P5: caption column
            variant_hook=getattr(p, "variant_hook", None)))                       # Render: per-account hook column

    def _key(r: ScheduleRow):
        if not r.scheduled_time:
            return (1, "")
        try:
            dt = parse_iso(r.scheduled_time)
            if dt.tzinfo is None:
                return (1, r.scheduled_time)
            return (0, dt.isoformat())
        except (ValueError, TypeError):
            return (1, r.scheduled_time)
    rows.sort(key=_key)
    if account is not None:        # P5: per-account filter, applied after the canonical time-sort
        rows = [r for r in rows if r.account == account]
    if batch is not None:          # Face 5: per-batch filter (follow a batch through to publish)
        rows = [r for r in rows if r.batch_id == batch]
    return rows


def group_schedule_by_account(rows: list) -> list:
    """Group already-time-sorted ScheduleRows by account for a running per-account header (P5, decision 2:
    Schedule is a per-post <table>, so a header sits cleanly above its rows). Pure; account-sorted headers,
    within-account TIME order preserved (the input arrives time-sorted). Mirrors group_posted_by_day."""
    by_acct: dict[str, list] = {}
    for r in rows: by_acct.setdefault(r.account, []).append(r)
    return [(a, by_acct[a]) for a in sorted(by_acct)]


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


def posted_library(led: Ledger, cfg: Config, *, account: Optional[str] = None, batch: Optional[str] = None) -> list[PostedRow]:
    """The Posted library (post-approval-lifecycle): ALL-time shipped posts (published/analyzed), newest
    first, with the live URL + lift score. NOT a dead archive — each row also offers 'Post again' (a fresh
    awaiting_approval repost of the same clip). Unscheduled/naive/unparseable times sort last. Lock-free read.
    P5: an optional `account` filters the posts BEFORE rows/day-grouping are built (so the count + day buckets
    reflect the filtered set); each row carries the raw saves/shares/retention/reach breakdown from p.metrics."""
    posts = [p for p in led.posts.values() if p.state in (PostState.published, PostState.analyzed)]
    if account is not None:
        posts = [p for p in posts if p.account == account]
    if batch is not None:          # Face 5: per-batch filter
        posts = [p for p in posts if p.batch_id == batch]
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
                      variant_hook=getattr(p, "variant_hook", None)) for p in posts]


def posted_batch_rollup(rows) -> Optional[dict]:
    """Read-only per-batch summary over the already-built PostedRow list (zero extra I/O, no metrics_series,
    no write, no learning unfreeze): {posted, with_lift, mean_lift}. mean_lift is over rows that CARRY a
    lift_score (None when none do -> renders '—'); never fabricates. None for an empty list."""
    if not rows: return None
    lifts = [r.lift_score for r in rows if r.lift_score is not None]
    return {"posted": len(rows), "with_lift": len(lifts),
            "mean_lift": (sum(lifts) / len(lifts)) if lifts else None}


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
                reach=p.metrics.get("reach")))

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
        "published": len(led.posts_in_state(PostState.published)),
        "holds": sum(1 for c in led.clips.values() if c.held),
        "pending_moments": len(pending(cfg, kind="moments")),
        "pending_moment_hooks": len(pending(cfg, kind="moment_hooks")),
        "pending_captions": len(pending(cfg, kind="captions")),
        "backend": cfg.poster_backend,
        "accounts": [a.handle for a in Accounts.load(cfg).active()],   # Account-First: Run-form batch-target options
    }


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
    tag_lean: Optional[str]
    corpus: list                       # the per-persona reach-vetted hashtag pool (B1), DISPLAYED reach-first (B3)
    intake: dict                       # genre/language/reference_accounts/notes (seeds B3 research)
    linked_handles: list               # accounts whose persona_id points at this persona
    reach_tags: list = field(default_factory=list)   # B3: corpus tags present in the reach store (own-reach+trends) -> flag high-reach
    reach_means: dict = field(default_factory=dict)  # B4 (closed loop): {corpus tag -> measured mean reach} over analyzed posts
    # Lever engine: the per-characteristic levers + the COMPOSED instruction the pipeline will read
    # ("what the AI will read") — so the operator sees their config's exact downstream effect on the card.
    content_focus: list = field(default_factory=list)
    energy: Optional[str] = None
    hook_angle: Optional[str] = None
    hook_tone: Optional[str] = None
    clip_profile: Optional[str] = None
    framing: Optional[str] = None
    instruction: str = ""
    # M2: the LOCKED brief + the TRANSPARENCY facts (length band + lead tags) derived from the REAL resolvers
    # — so the operator sees, on the card, exactly what the config produces and what definition is frozen.
    brief: str = ""
    length_band: str = ""
    lead_tags: list = field(default_factory=list)


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
    reach-ranked + each curated tag's MEASURED reach) + every account's current persona link (for the
    connect dropdown). Fail-open: a corrupt personas.json / accounts.json -> an EMPTY page (the surface
    never 500s), mirroring golive_accounts. `led` is injectable (tests); else loaded lock-free."""
    try:
        from fanops.personas import Personas, compose_persona_instruction, persona_facts   # lazy: personas imports accounts (in migrate) -> avoid a load cycle
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
    # B3: surface each corpus REACH-RANKED (the store blends own-reach + Graph trends) and flag the
    # high-reach (store-present) tags. No store -> insertion order preserved, reach_tags empty (no signal yet).
    from fanops.hashtags import vetted_menu, load_store, _norm
    store = load_store(cfg)
    rank = {t: i for i, t in enumerate(vetted_menu(store))}
    store_set = {_norm(t) for t in (store or [])}
    # B4 (closed loop): the MEASURED mean reach per tag over analyzed posts, shown next to each curated
    # tag. Fail-open — a missing/torn ledger leaves reach_means empty (the page still renders).
    means: dict = {}
    try:
        from fanops.fanops_hashtags import tag_reach_means
        means = tag_reach_means(led if led is not None else Ledger.load(cfg))
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("personas", "-", "reach_means_error", err=str(exc)[:160])   # observable, still fail-open
        means = {}
    def _ranked(corpus):
        return sorted((_norm(t) for t in corpus), key=lambda n: rank.get(n, 10 ** 6))
    cards = [PersonaCard(id=p.id, name=p.name, voice=p.voice, tag_lean=p.tag_lean,
                         corpus=_ranked(p.hashtag_corpus), intake=dict(p.intake),
                         linked_handles=by_pid.get(p.id, []),
                         reach_tags=[_norm(t) for t in p.hashtag_corpus if _norm(t) in store_set],
                         reach_means={_norm(t): means[_norm(t)] for t in p.hashtag_corpus if _norm(t) in means},
                         content_focus=list(p.content_focus), energy=p.energy, hook_angle=p.hook_angle,
                         hook_tone=p.hook_tone, clip_profile=p.clip_profile, framing=p.framing,
                         instruction=compose_persona_instruction(p), brief=getattr(p, "brief", "") or "",
                         length_band=(facts := persona_facts(cfg, p))["length_band"], lead_tags=facts["lead_tags"])
             for p in reg.all()]
    links = [PersonaAccountLink(handle=a.handle, persona_id=getattr(a, "persona_id", None)) for a in accts]
    return PersonasPage(personas=cards, accounts=links)


def golive_accounts(cfg: Config) -> list[GoLiveAccount]:
    """The active accounts as a per-channel read-model, SHARED by golive_status + home_status so the two
    surfaces never drift on what "connected" means. One GoLiveChannel per platform; integration_id is the
    effective per-platform id (integrations[platform] -> account_id fallback -> "" unmapped). Fail-open: a
    malformed accounts.json logs accounts_error and degrades to [] (the surface never 500s). NO secret."""
    try:
        return [GoLiveAccount(
            handle=a.handle, persona=a.persona, tag_lean=a.tag_lean,
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
            handle=a.handle, persona=a.persona, tag_lean=a.tag_lean,
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
                  "awaiting": st.get(PostState.awaiting_approval, 0),
                  "scheduled": st.get(PostState.queued, 0),
                  "posted": st.get(PostState.published, 0) + st.get(PostState.analyzed, 0)}
        by_account = dict(Counter(p.account for p in led.posts.values()))
    except Exception as exc:                          # the first page an operator sees must never 500
        from fanops.log import get_logger
        get_logger(cfg)("home", "-", "error", err=str(exc)[:160])
        counts = {"sources": 0, "batches": None, "awaiting": 0, "scheduled": 0, "posted": 0}
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
        cast_pick_budget=cfg.cast_pick_budget,         # moments per account per run
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
