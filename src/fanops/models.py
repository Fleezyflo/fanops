# src/fanops/models.py
"""Units (Source→Moment→Clip→Post) + agent-step request/response contracts.
Separate state enums per unit (no shared linear enum). failed (Post) is distinct from
analyzed. Every unit has an `error` state for per-unit quarantine."""
from __future__ import annotations
import json, math
from enum import Enum
from typing import Optional, Literal
from pydantic import BaseModel, Field, field_validator
from fanops.ids import content_id


class SourceState(str, Enum):
    catalogued = "catalogued"; transcribed = "transcribed"; signalled = "signalled"
    moments_requested = "moments_requested"
    picks_decided = "picks_decided"   # M1b (frame-seeing two-pass): pass-1 picks reconciled into `picked`
                                      # moments; the per-pick `moment_hooks` gates are now in flight. Pass-2
                                      # (ingest_moment_hooks) promotes the source to moments_decided once
                                      # every picked moment's hook has landed (or decided clean on a valid null).
    moments_decided = "moments_decided"
    moments_empty = "moments_empty"   # V2 M1/F8: the model returned [] (nothing worth posting) — VISIBLE
                                      # + re-runnable (retry-source), NOT a silent moments_decided. Non-
                                      # terminal: a prior good moment set is preserved (no cascade-delete).
    retired = "retired"; discovered = "discovered"   # M1: removed-but-kept / rebuild-orphan (inert until confirmed)
    error = "error"

class MomentState(str, Enum):
    picked = "picked"   # M1b: a moment is BORN here in pass-1 (window chosen, hook NOT yet authored). It is
                        # NOT renderable — the render loop (pipeline + prewarm) keys on `decided`, so a picked
                        # moment naturally waits for its frame-seeing hook. ingest_moment_hooks promotes
                        # picked -> decided once the per-pick moment_hooks gate lands (hook, or a valid clean null).
    decided = "decided"; clipped = "clipped"; retired = "retired"; error = "error"

class ClipState(str, Enum):
    rendered = "rendered"; captions_requested = "captions_requested"; captioned = "captioned"
    queued = "queued"; published = "published"; analyzed = "analyzed"
    held = "held"; retired = "retired"; error = "error"
    stitch_draft = "stitch_draft"   # M3: a stitched clip is BORN here — structurally unpostable (absent from
                                    # crosspost's captioned-selection AND _REUSABLE_CLIP_STATES) until an
                                    # operator approval transitions it to captioned. Reusing `held` is forbidden.

class RenderState(str, Enum):
    # Per-account Render lifecycle (mirrors the shippable arc of ClipState, minus the substrate-only
    # states). A Render is BORN `rendered`; it rides its Post's approve->publish->analyze arc and is
    # `retired` when GC sweeps it. Distinct enum (not ClipState) so the per-account artifact's lifecycle
    # never entangles with the shared substrate Clip's caption/stitch states.
    rendered = "rendered"; queued = "queued"; published = "published"; analyzed = "analyzed"; retired = "retired"

class PostState(str, Enum):
    # awaiting_approval: a crossposted post is BORN here (post-approval-lifecycle). It is NOT publishable
    # — publish_due/publish_now iterate only `queued`, so an unapproved post is structurally never
    # submitted (even on a live backend). The operator promotes it to `queued` via Ledger.approve_post
    # (the human gate, mirroring the M3/M4 stitch approve/release spine). `queued` thus means
    # "approved + scheduled", not merely "created".
    awaiting_approval = "awaiting_approval"
    queued = "queued"; submitting = "submitting"; submitted = "submitted"
    published = "published"; analyzed = "analyzed"; failed = "failed"; error = "error"
    # rejected: the operator discarded an awaiting_approval post (Ledger.reject_post). Terminal, never
    # fires, kept as a record. Distinct from `retired` (a queued base post superseded by a stitch) and
    # from `failed` (a publish attempt that didn't land, re-queueable).
    rejected = "rejected"
    # needs_reconcile: an ambiguous publish failure (5xx / network timeout AFTER the request body
    # was sent) — the post MAY already be live on the platform. Blotato has no idempotency key
    # (AUDIT C1), so it must NOT be blindly re-POSTed (double-publish risk). A human/poll step
    # checks GET /v2/posts/:id before resubmitting. Distinct from `failed` (definitely not posted,
    # safe to re-queue) for exactly that reason.
    needs_reconcile = "needs_reconcile"
    # retired: a queued base post superseded by an operator-approved stitch (M4). The stitch is a NEW
    # clip + NEW posts, so the bare post must NOT also publish (the feed would get both). publish_due
    # only iterates `queued`, so a retired post is structurally never submitted. Distinct from `failed`
    # (re-queueable) — retired is terminal + deliberate (the stitched version replaces it).
    retired = "retired"


class Platform(str, Enum):
    instagram = "instagram"; tiktok = "tiktok"; youtube = "youtube"
    facebook = "facebook"; twitter = "twitter"

# THE Post.metrics key every scorer ranks by. Written in exactly one place (track.record_metrics);
# read by adjust/digest/variant_*/studio. One literal here — a key typo at any read site used to
# make that scorer silently treat every post as "no lift data" (learning loop goes quiet with no
# error, indistinguishable from "not enough data yet") — stage-6 audit.
LIFT_SCORE = "lift_score"

class Fmt(str, Enum):
    r9x16 = "9:16"; r1x1 = "1:1"; r16x9 = "16:9"

# Which aspect each platform wants (FIX F20 — was one-aspect-for-all).
PLATFORM_ASPECT = {
    Platform.tiktok: Fmt.r9x16, Platform.instagram: Fmt.r9x16, Platform.youtube: Fmt.r16x9,
    Platform.facebook: Fmt.r1x1, Platform.twitter: Fmt.r16x9,
}

# AUDIT (g): hard per-surface MAX clip length (seconds). A v1 version of this dict was REMOVED
# as a FALSE safety contract — it was declared but never enforced, so a 180s pick fanned out to
# YouTube/Twitter at full length, silently over their caps. This is the REAL enforcement: at
# crosspost time a clip whose PLAYABLE duration (its moment window, end - start — Clip has no
# duration field) exceeds the cap for a platform is SKIPPED for THAT surface only (it can still
# post to platforms whose cap it satisfies). Values are the real short-form ceilings:
#   instagram (Reels) 90s · tiktok 600s (10 min) · youtube (Shorts) 60s · twitter 140s ·
#   facebook (Reels) 90s.
# A platform with no meaningful short-form cap may be OMITTED here -> no clamp for it.
# Enforcement is FAIL-OPEN on unknown duration: if the window is 0/None/unmeasurable (or the
# moment is missing), the clip is NOT skipped — never silently drop a post over an unprobed
# length (the removed dict's sin was the opposite: pretending to guard while letting all through).
PLATFORM_MAX_SECONDS = {
    Platform.instagram: 90,
    Platform.tiktok: 600,
    Platform.youtube: 60,
    Platform.twitter: 140,
    Platform.facebook: 90,
}


# ---- units ----
class Source(BaseModel):
    id: str
    state: SourceState = SourceState.catalogued
    source_path: str
    source_origin: str = "drop"                 # drop | url | scan (HOW it arrived — intake channel)
    origin_kind: Literal["native", "third_party"] = "native"   # M1: WHOSE it is — a THIRD axis, distinct from
                                                # source_origin (channel) and P1 provenance (attribution). WRITE-ONCE
                                                # at catalogue (add_source setdefault); old ledgers load native.
    batch_id: Optional[str] = None              # Account-First Studio: the named ingest Batch this source belongs to.
                                                # WRITE-ONCE at _catalogue_file (mirrors origin_kind); None == ungrouped.
    sha256: Optional[str] = None
    duration: Optional[float] = None
    width: Optional[int] = None                 # FIX F68 — probed at ingest for safe reframe
    height: Optional[int] = None
    language: Optional[str] = None              # FIX F33 — Whisper-detected (en/ar/...)
    transcript: Optional[list[dict]] = None     # None = not transcribed; [] = ran, no speech
    signal_peaks: Optional[list[dict]] = None
    error_reason: Optional[str] = None
    meta: dict = Field(default_factory=dict)
    created_at: Optional[str] = None            # content-lifecycle: ISO-8601 UTC ingest day, set at
                                                # _catalogue_file / rebuild discovered. None on old ledgers ->
                                                # migration v2->v3 backfill (file mtime, else stamp). The Review
                                                # day-anchor ("clips I dropped in").

class Moment(BaseModel):
    id: str
    parent_id: str                              # source id
    state: MomentState = MomentState.decided
    content_token: str = ""                     # the stable token its id was built from
                                                # (reconcile always sets it; default "" lets a
                                                #  hand-built Moment omit it harmlessly)
    start: float
    end: float
    reason: str                                 # WHY worth posting (required)
    transcript_excerpt: str = ""
    hook: Optional[str] = None                  # punchy top-third line for the clip; deterministic
                                                # first-clause default (overlay.derive_hook), an LLM
                                                # may overwrite. Optional/None -> old ledgers load fine.
    hook_removed: Optional[str] = None          # the model's hook that is_weak_hook STRIPPED (dup/opening-
                                                # template cluster) instead of discarding it. Preserved so
                                                # Studio Review can show "hook removed" + let the operator
                                                # restore it (the 29% blank rate is mostly good hooks the
                                                # mechanical guard killed, not dead footage). None = nothing
                                                # was stripped (old ledgers load fine).
    signal_score: float = 0.0
    hooks_by_persona: dict[str, str] = Field(default_factory=dict)   # handle -> that account's own frame-grounded on-screen hook (the moment author writes these); {} -> every surface uses `hook` (old ledgers load fine)
    hook_strategy: Optional[str] = None         # M2 router: text | clean_final | clean_awaiting_strategy:<key>
                                                # | stitch:<format>. Observe-only annotation; None = unrouted
                                                # (router off / old ledgers load). One writer: router.route_moments.
    intro_matches: Optional[list[dict]] = None  # M6 intro-tease: the LLM-vision matcher's ranked pairings for
                                                # this moment — [{asset_id, fit_score, rationale, tease_text}, ...],
                                                # best-fit first. None = unmatched (matcher off / no answer / old
                                                # ledgers load). One writer: intro_match.ingest_intro_match.
    affinities: list[str] = Field(default_factory=list)   # Account-First: handles this moment was CAST to
                                                # (sole writer casting.cast_moments, default-OFF). [] = uncast =
                                                # ALL active accounts (byte-identical). SUBSET of the batch target;
                                                # NON-DURABLE across a re-decision (re-derived each gated pass).
    error_reason: Optional[str] = None

class Clip(BaseModel):
    id: str
    parent_id: str                              # moment id
    state: ClipState = ClipState.rendered
    path: str
    aspect: Fmt = Fmt.r9x16
    first_frame_kind: Optional[str] = None      # P1 provenance: "visual" if pick_visual_start moved the cut
                                                # start onto a stronger opening frame, else "transcript".
    cut_seconds: Optional[float] = None         # P1 provenance: the rendered window length (ce-cs).
                                                # OBSERVATIONAL only — length is not varied, so not P4-ranked.
    held: bool = False
    held_reason: Optional[str] = None
    tagged_artist: bool = False
    media_url: Optional[str] = None             # FIX F44 — cached Blotato URL, uploaded once
    meta_captions: dict = Field(default_factory=dict)   # surface -> {caption, hashtags}
    error_reason: Optional[str] = None
    hook_burn_failed: bool = False              # V2 M1/F9: a hook was WANTED but couldn't burn (ffmpeg
                                                # lacks the text filter, or build_ass yielded empty) — the
                                                # clip still rendered fail-open but lost its hook. Surfaced
                                                # in the run summary so the silent drop is never invisible.

class Post(BaseModel):
    id: str
    parent_id: str                              # clip id
    state: PostState = PostState.queued
    account: str                                # human handle, e.g. "@a"
    account_id: str                             # Blotato NUMERIC id (FIX F06)
    platform: Platform
    caption: str
    hashtags: list[str] = Field(default_factory=list)
    media_urls: list[str] = Field(default_factory=list)
    aspect: Fmt = Fmt.r9x16
    scheduled_time: Optional[str] = None
    submission_id: Optional[str] = None         # set BEFORE network return is confirmed (dedupe)
    public_url: Optional[str] = None
    error_reason: Optional[str] = None
    metrics: dict = Field(default_factory=dict)
    # P3 append-only metrics time-series: one sparse row per captured cadence offset, each a superset of
    # a `metrics` snapshot + {"offset","captured_at"} provenance. `metrics` above stays EXACTLY the LATEST
    # snapshot (byte-identical back-compat: every existing reader stays on it). An old ledger row lacking
    # this key defaults to [] (Pydantic default_factory; independent of extra="ignore").
    metrics_series: list[dict] = Field(default_factory=list)
    render_id: Optional[str] = None     # per-account Render foundation: THE single authoritative pointer to the
                                        # rendered file this account ships (Render owns the bytes + the hook).
                                        # None == no per-account render (creative_variation OFF / no hook) -> the
                                        # serve route falls to the shared Clip.path (byte-identical to old ledgers).
    variant_key: Optional[str] = None   # creative-variation attribution: deterministic per-(account,platform,clip) key
    variant_hook: Optional[str] = None  # the burned-in hook text this account's variant used (observe-only; a
                                        # READ-ONLY mirror of Render.hook_text — Render is the single source of truth)
    # P1 attribution key (one writer = crosspost): the creative dims P3 aggregates reach by and P4 ranks.
    # All None on old ledgers + when the upstream dim is unknown (validate-or-default; never crashes a load).
    first_frame_kind: Optional[str] = None  # "visual" | "transcript" — how the opening frame was chosen
    clip_profile: Optional[str] = None      # song | talk — the per-video-type group ("hook for which video type")
    cut_seconds: Optional[float] = None     # rendered clip length (observational; length not varied)
    variation_axis: Optional[str] = None    # P2 (one writer = crosspost): the cheap-text axis this variant moved
    batch_id: Optional[str] = None      # Account-First Studio: DENORMALIZED from the source at crosspost (carried by
                                        # repost_post); the single join key the Studio surfaces group by. None == ungrouped.
    created_at: Optional[str] = None    # content-lifecycle: ISO-8601 UTC BIRTH day (wall-clock), set at crosspost
                                        # add_post / repost / crosspost_to_account. NOT part of the content-
                                        # addressed pid. None on old ledgers -> migration backfill (scheduled_time
                                        # else stamp).
    published_at: Optional[str] = None  # content-lifecycle: ISO-8601 UTC TRUE publish time, stamped at the
                                        # run.py published transition. The Posted-archive day-anchor ("what shipped
                                        # Tuesday") — scheduled_time is INTENT day, not publish day. None until
                                        # published; old/in-flight rows fall back to scheduled_time in the grouper.


class Render(BaseModel):
    # The per-account SHIPPABLE artifact — a first-class child of the shared substrate Clip (the audit
    # foundation: nothing owned the per-account render, so "which file does @a ship" was smeared across
    # Post.parent_id + Post.media_urls + a loose orphan mp4, and the serve route GUESSED). A Render owns:
    # the rendered bytes (`path`), the burned on-screen hook (`hook_text` — THE single home; Post.variant_hook
    # is a read-only mirror), the upload cache (`media_url`, FIX-F44 parity), its lifecycle (`state`), and its
    # lineage (`batch_id`/`source_id`, for batch-scoped filing + the durable archive). CONTENT-ADDRESSED by
    # (clip_id, hook_text): two surfaces with the SAME hook compute the same id -> ONE render, ONE file (the
    # anti-explosion dedup). Exists ONLY under creative_variation; a hookless surface has Post.render_id None
    # and serves the shared Clip.path. Captions are NOT here — they stay surface-keyed on the shared Clip
    # (the caption pipeline is intentionally untouched).
    id: str                                     # child_id("render", clip_id, hook_text or "NO_HOOK")
    clip_id: str                                # parent shared Clip (the substrate this render burned onto)
    account: str                                # the handle this render belongs to (UI attribution)
    surface_key: str                            # surface_key(account, platform) — UI attribution / grouping
    hook_text: Optional[str] = None             # THE single source of truth for the per-account on-screen hook
    path: str                                   # the rendered mp4 (filed under clips/{batch}/{source}/…)
    media_url: Optional[str] = None             # per-render cached upload URL (FIX-F44 parity; uploaded once)
    state: RenderState = RenderState.rendered
    batch_id: Optional[str] = None              # denormalized from the source at mint (filing + archive lineage)
    source_id: Optional[str] = None             # denormalized from the moment's source at mint (filing path)
    is_account_cut: bool = False                # M2: True iff this render is a REAL per-account CUT at the
                                                # account's own length band (render_account_cut succeeded), vs a
                                                # hook burned onto the global-band shared clip. The truthful
                                                # source for Post.clip_profile provenance + the M3/M4 "this is a
                                                # per-account 28-45s cut" UI label. Additive (False on every
                                                # legacy render — they reload fine, no migration).


class SelectionMethod(str, Enum):
    heuristic = "heuristic"   # persona_fit_score (token overlap + signal) picked it
    llm = "llm"               # the moment_casting LLM gate selected it


class SelectionFact(BaseModel):
    # M4: the DURABLE audit record of the selector — WHICH account got WHICH moment and WHY. Casting writes
    # only Moment.affinities (handles), which is NON-durable (reset to [] on each re-decision) and carries no
    # "why" — the persona_fit_score (overlap, signal) and the LLM choice were computed-and-discarded. A
    # SelectionFact persists that decision + its reasoning. CONTENT-ADDRESSED one-per-(moment, account):
    # child_id("selfact", moment_id, account) so a re-cast OVERWRITES (the CURRENT durable selection, not a
    # growing history). Additive top-level `selection_facts` map (v6->v7); old ledgers load with {} — nothing
    # writes facts until casting does (M4b), so the OFF/baseline shape is byte-identical.
    id: str                                     # child_id("selfact", moment_id, account)
    moment_id: str
    account: str                                # the handle this moment was selected FOR
    method: SelectionMethod = SelectionMethod.heuristic   # how it was chosen (validated enum: heuristic | llm)
    reason: str = ""                            # human-readable WHY — the moment's editorial reason (audit context)
    overlap: Optional[int] = None               # heuristic: persona-token ∩ moment-corpus count (the fit signal); None for llm
    signal: Optional[float] = None              # the moment's signal_score at selection time
    rank: Optional[int] = None                  # rank within the account's selected set (0 = best fit); None for llm
    source_id: Optional[str] = None             # lineage (the moment's source) — direct per-account/source audit
    batch_id: Optional[str] = None              # lineage (the ingest batch) — direct per-account/batch audit
    created_at: Optional[str] = None            # wall-clock ISO-Z when cast (audit timestamp); None when untimed/legacy


# ---- M3 (structural-hooks): the stitch_plan entity — the operator-approval spine ----
class StitchState(str, Enum):
    suggested = "suggested"; approved = "approved"; in_use = "in_use"   # lifecycle
    dismissed = "dismissed"; error = "error"                            # terminal

class StitchPlan(BaseModel):
    id: str                                     # content-addressed (stitch_plan_id) — the durable dedup key
    clip_id: str                                # the base clip this stitch wraps / re-cuts
    strategy_key: str                           # a router.STRATEGY_KEYS member (impact_cut, intro_tease, ...)
    asset_ids: list[str] = Field(default_factory=list)   # native/third-party assets the stitch pairs in
    plan_params: dict = Field(default_factory=dict)      # format-specific params (cut window, intro id, ...)
    state: StitchState = StitchState.suggested  # born suggested -> operator approval -> approved -> in_use
    base_fingerprint: Optional[str] = None      # base clip's render fingerprint, PINNED at approval (stale -> dismiss)
    error_reason: Optional[str] = None
    rank_score: Optional[float] = None          # M5: fit score the routine loop ranks suggestions by (higher first)
    rationale: Optional[str] = None             # M5: one-line human-readable WHY, shown in Studio (operator-facing)
    render_attempts: int = 0                    # M6: failed in-lock commit passes for a flaky (clip, asset) pair;
                                                # at the cap the plan is PARKED (error) instead of retried forever.
                                                # Resets implicitly when the clip/asset changes (a new plan id).

def stitch_plan_id(clip_id: str, asset_ids: list[str], strategy_key: str, plan_params: dict) -> str:
    """Content-addressed id keyed on the CLIP id + the sorted pairing inputs (NOT the render
    fingerprint), so re-emitting the same pairing yields the same id (dedup) while re-rendering the
    base clip never re-mints it. Deterministic across processes (ids.content_id)."""
    token = json.dumps({"assets": sorted(asset_ids), "strategy": strategy_key, "params": plan_params},
                       sort_keys=True, default=str)
    return content_id("stitch", clip_id, token)


# ---- Account-First Studio: the Batch entity — a named, account-targeted ingest grouping ----
class BatchState(str, Enum):
    open = "open"; closed = "closed"; error = "error"   # born open; this build only ever sets open (StitchState parity)

class Batch(BaseModel):
    id: str                                              # content-addressed (batch_id) — the durable key
    name: str                                            # operator label, required non-blank (validated in create_batch)
    target_accounts: list[str] = Field(default_factory=list)   # [] == ALL-ACTIVE-ACCOUNTS sentinel; else exact HANDLES
    state: BatchState = BatchState.open                  # (Account.handle == Surface.account == Post.account)
    created_at: Optional[str] = None                     # ISO-8601 UTC birth (microsecond); None on a hand-built Batch
    error_reason: Optional[str] = None

def batch_id(name: str, created_at: str) -> str:
    """Content-addressed id keyed on (name, microsecond-precision created_at): a re-submit of the same
    (name, birth) yields the same id (idempotent), two distinct create_batch calls cannot collide.
    Deterministic across processes (ids.content_id)."""
    return content_id("batch", name, created_at)


# ---- agent-step contracts (all carry request_id for correlation — FIX F21) ----
class MomentRequest(BaseModel):
    source_id: str
    request_id: str
    duration: float
    transcript: list[dict] = Field(default_factory=list)
    signal_peaks: list[dict] = Field(default_factory=list)
    language: Optional[str] = None
    guidance: str = ""
    clip_profile: str = "talk"      # content-type band selector (bands.band_for); "talk" -> today's behavior
    frames: list[str] = Field(default_factory=list)   # Phase 1: source stills the vision author SEES while picking + hooking (fail-open [] when no source)
    personas: list[dict] = Field(default_factory=list)   # [{handle, persona}] active fan accounts -> per-handle hooks_by_persona. Absent/[] -> no per-account hooks (byte-identical to today).

class MomentPick(BaseModel):
    # M1b (frame-seeing two-pass): the PICK pass chooses WINDOWS only. Hook authoring moved to the
    # per-pick `moment_hooks` gate (MomentHookDecision), which sees the picked WINDOW's frames — the
    # author can no longer write a hook for footage it never saw. (Pydantic ignores any vestigial `hook`
    # field an old response still carries, so a stale answer never breaks the load.)
    start: float
    end: float
    reason: str
    transcript_excerpt: str = ""
    signal_score: float = 0.0

    @field_validator("start", "end")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("timestamp must be a finite number (no NaN/Infinity)")
        return v

class MomentDecision(BaseModel):
    source_id: str
    request_id: str
    picks: list[MomentPick] = Field(default_factory=list)

# M1b pass-2: ONE per-pick frame-seeing hook gate. The request carries the PICKED WINDOW + frames
# extracted over that window (clip.fit_window), so the author writes a hook grounded in the exact
# footage the clip opens on — the operator's #1 ask. Gate key = moment_hooks__{source_id}.{token},
# so N picks -> N independent gates; correlation is by the gate KEY (filename), not a body field.
class MomentHookRequest(BaseModel):
    source_id: str
    moment_id: str
    token: str                                      # the pick's content token (start-end), echoes the gate key
    request_id: str
    start: float
    end: float
    reason: str = ""
    transcript_excerpt: str = ""
    signal_score: float = 0.0
    language: Optional[str] = None
    guidance: str = ""
    clip_profile: str = "talk"
    frames: list[str] = Field(default_factory=list)        # stills over the PICKED WINDOW (the author's eyes); [] -> text-only
    signal_peaks: list[dict] = Field(default_factory=list)  # window-scoped energy transients (the _hook_decision AUDIO step)
    personas: list[dict] = Field(default_factory=list)      # [{handle, persona}] -> hooks_by_persona; [] -> no per-account hooks

class MomentHookDecision(BaseModel):
    request_id: str
    hook: Optional[str] = None      # the window-grounded on-screen RETENTION hook; None/"" -> this pick ships CLEAN (valid)
    hooks_by_persona: dict[str, str] = Field(default_factory=dict)   # handle -> that account's own window-grounded hook

# M1 (Option C — per-account moment SELECTION): an agent gate that, seeing the source's DECIDED moments +
# each active account's persona, chooses per account that account's OWN set of moments. The decision writes
# Moment.affinities, which the EXISTING crosspost affinity gate already honors (a cast moment fans ONLY to
# its accounts). GENEROUS — no count cap (unlike the heuristic cast_moments budget); overlap allowed (a
# moment can suit several personas). Gate key = source_id (one selection gate per source, like the moments gate).
class MomentCastingRequest(BaseModel):
    source_id: str
    request_id: str
    moments: list[dict] = Field(default_factory=list)   # [{moment_id, reason, hook, transcript_excerpt, signal_score, start, end}]
    personas: list[dict] = Field(default_factory=list)  # [{handle, persona}] active fan accounts to cast for
    language: Optional[str] = None
    guidance: str = ""

class MomentCastingDecision(BaseModel):
    request_id: str
    selections: dict[str, list[str]] = Field(default_factory=dict)   # handle -> [moment_id,...] that account's OWN moments

class CaptionRequest(BaseModel):
    clip_id: str
    request_id: str
    surfaces: list[dict] = Field(default_factory=list)   # [{surface, platform}]
    transcript_excerpt: str = ""
    language: Optional[str] = None
    guidance: str = ""

class CaptionItem(BaseModel):
    surface: str
    caption: str
    hashtags: list[str] = Field(default_factory=list)
    language: Optional[str] = None      # AUDIT H5: the LLM declares the caption's language
    hook: Optional[str] = None          # per-surface on-screen hook (creative variation); None -> use moment default
    axis: Optional[str] = None          # P2: the ONE cheap-text axis this variant moves (normalized at ingest)
    rationale: Optional[str] = None     # P2: one-line WHY this variant is a coherent, justified difference

class CaptionSet(BaseModel):
    request_id: str
    items: list[CaptionItem] = Field(default_factory=list)

# M6 intro-tease: the LLM-vision pairing matcher (intro_match.py). The matcher sees a clean clip's context
# (keyframes, router reason, transcript, hook) against candidate intro assets (thumbnail, origin_kind) and
# returns RANKED pairings, each a {asset_id, fit_score, rationale, tease_text}. fit_score becomes the plan's
# rank_score; tease_text is the "wait for it / [X] incoming" line the prepend burns. One+ items per moment_id;
# ingest filters to real candidate asset_ids and orders best-fit first. Fail-open: no response -> no pairing.
class IntroMatchItem(BaseModel):
    moment_id: str
    asset_id: str
    fit_score: float = 0.0
    rationale: str = ""
    tease_text: str = ""

class IntroMatchDecision(BaseModel):
    request_id: str
    items: list[IntroMatchItem] = Field(default_factory=list)
