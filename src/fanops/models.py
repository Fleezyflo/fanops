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

class Fmt(str, Enum):
    r9x16 = "9:16"; r1x1 = "1:1"; r16x9 = "16:9"

# Which aspect each platform wants (FIX F20 — was one-aspect-for-all).
PLATFORM_ASPECT = {
    Platform.tiktok: Fmt.r9x16, Platform.instagram: Fmt.r9x16, Platform.youtube: Fmt.r16x9,
    Platform.facebook: Fmt.r1x1, Platform.twitter: Fmt.r16x9,
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
    signal_score: float = 0.0
    error_reason: Optional[str] = None

class Clip(BaseModel):
    id: str
    parent_id: str                              # moment id
    state: ClipState = ClipState.rendered
    path: str
    aspect: Fmt = Fmt.r9x16
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


# ---- agent-step contracts (all carry request_id for correlation — FIX F21) ----
class MomentRequest(BaseModel):
    source_id: str
    request_id: str
    duration: float
    transcript: list[dict] = Field(default_factory=list)
    signal_peaks: list[dict] = Field(default_factory=list)
    language: Optional[str] = None
    guidance: str = ""

class MomentPick(BaseModel):
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

class CaptionSet(BaseModel):
    request_id: str
    items: list[CaptionItem] = Field(default_factory=list)
