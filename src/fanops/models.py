# src/fanops/models.py
"""Units (Source→Moment→Clip→Post) + agent-step request/response contracts.
Separate state enums per unit (no shared linear enum). failed (Post) is distinct from
analyzed. Every unit has an `error` state for per-unit quarantine."""
from __future__ import annotations
import math
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class SourceState(str, Enum):
    catalogued = "catalogued"; transcribed = "transcribed"; signalled = "signalled"
    moments_requested = "moments_requested"; moments_decided = "moments_decided"
    error = "error"

class MomentState(str, Enum):
    decided = "decided"; clipped = "clipped"; retired = "retired"; error = "error"

class ClipState(str, Enum):
    rendered = "rendered"; captions_requested = "captions_requested"; captioned = "captioned"
    queued = "queued"; published = "published"; analyzed = "analyzed"
    held = "held"; retired = "retired"; error = "error"

class PostState(str, Enum):
    queued = "queued"; submitting = "submitting"; submitted = "submitted"
    published = "published"; analyzed = "analyzed"; failed = "failed"; error = "error"
    # needs_reconcile: an ambiguous publish failure (5xx / network timeout AFTER the request body
    # was sent) — the post MAY already be live on the platform. Blotato has no idempotency key
    # (AUDIT C1), so it must NOT be blindly re-POSTed (double-publish risk). A human/poll step
    # checks GET /v2/posts/:id before resubmitting. Distinct from `failed` (definitely not posted,
    # safe to re-queue) for exactly that reason.
    needs_reconcile = "needs_reconcile"


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
    source_origin: str = "drop"                 # drop | url | scan
    sha256: Optional[str] = None
    duration: Optional[float] = None
    width: Optional[int] = None                 # FIX F68 — probed at ingest for safe reframe
    height: Optional[int] = None
    language: Optional[str] = None              # FIX F33 — Whisper-detected (en/ar/...)
    transcript: Optional[list[dict]] = None     # None = not transcribed; [] = ran, no speech
    signal_peaks: Optional[list[dict]] = None
    error_reason: Optional[str] = None
    meta: dict = Field(default_factory=dict)

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
    hook_edited: bool = False                   # the feed-aware hook editor (hookedit.py) has run on
                                                # this moment's hook; latches True so it never re-edits
                                                # (no loop). Default False -> old ledgers load + are
                                                # eligible for one edit pass.
    hook_judged: bool = False                   # the specificity critic (hookjudge.py) has judged this
                                                # hook against the rubric; latches True so it never
                                                # re-judges (no loop). Default False -> old ledgers
                                                # load + are eligible for one judge pass.
    signal_score: float = 0.0
    hook_pattern: Optional[str] = None          # P1 provenance: which of the 6 _hook_spec patterns the
                                                # responder/editor chose for this hook (open_loop|curiosity|
                                                # comment_bait|contrarian|pov|proof). None = unknown/clean.
                                                # The dim P4 ranks FIRST. One writer: moments/hookedit ingest.
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
    variant_key: Optional[str] = None   # creative-variation attribution: deterministic per-(account,platform,clip) key
    variant_hook: Optional[str] = None  # the burned-in hook text this account's variant used (observe-only)
    # P1 attribution key (one writer = crosspost): the creative dims P3 aggregates reach by and P4 ranks.
    # All None on old ledgers + when the upstream dim is unknown (validate-or-default; never crashes a load).
    hook_pattern: Optional[str] = None      # the moment's chosen _hook_spec pattern (P4 ranks this FIRST)
    first_frame_kind: Optional[str] = None  # "visual" | "transcript" — how the opening frame was chosen
    clip_profile: Optional[str] = None      # song | talk — the per-video-type group ("hook for which video type")
    cut_seconds: Optional[float] = None     # rendered clip length (observational; length not varied)
    variation_axis: Optional[str] = None    # P2 (one writer = crosspost): the cheap-text axis this variant moved


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

class MomentPick(BaseModel):
    start: float
    end: float
    reason: str
    transcript_excerpt: str = ""
    signal_score: float = 0.0
    hook: Optional[str] = None      # on-screen RETENTION hook (curiosity-gap, NOT a transcript quote); None -> derive a default
    hook_pattern: Optional[str] = None  # which of the 6 _hook_spec patterns this hook is; normalized at ingest

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

# Feed-aware hook editor (hookedit.py): a SINGLE gate over the WHOLE feed of decided hooks. The
# moment responder answers each clip in isolation, so it cannot avoid reusing a hook/template across
# clips; this gate hands the editor every hook at once to rewrite the weak/duplicated/templated ones
# into strong, DISTINCT hooks. Response = one item per moment_id; hook None -> no honest hook (clean clip).
class HookEditItem(BaseModel):
    moment_id: str
    hook: Optional[str] = None
    hook_pattern: Optional[str] = None  # the editor's pattern for its rewrite (normalized at ingest)

class HookEditDecision(BaseModel):
    request_id: str
    items: list[HookEditItem] = Field(default_factory=list)

# Specificity critic (hookjudge.py): the INDEPENDENT LLM judge the hookcheck floor references ("a later
# LLM critic") but that was never built. Runs AFTER the editor on each kept hook and applies the verified
# retention rubric (anchored to a concrete specific of THIS clip; passes the portability test; opens a
# loop). reject (keep=False) -> the hook is nulled to a clean clip (clean beats slop). One verdict per
# moment_id; keep defaults True so the judge's silence/omission NEVER strips a hook (fail-open).
class HookJudgeItem(BaseModel):
    moment_id: str
    keep: bool = True               # True = hook clears the rubric; False = reject to a clean clip
    why: str = ""                   # one line: the deciding rubric test (unanchored / generic / no loop)

class HookJudgeDecision(BaseModel):
    request_id: str
    items: list[HookJudgeItem] = Field(default_factory=list)
