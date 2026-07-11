# src/fanops/models.py
"""Units (Source→Moment→Clip→Post) + agent-step request/response contracts.
Separate state enums per unit (no shared linear enum). failed (Post) is distinct from
analyzed. Every unit has an `error` state for per-unit quarantine."""
from __future__ import annotations
import json, math, re
from enum import Enum
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator
from fanops.ids import content_id

# Same threshold as moments._MIN_MOMENT_S — a segment shorter than this is noise.
_MIN_MOMENT_S = 0.5

def _validate_segments(segments: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Finite, each ≥ _MIN_MOMENT_S, strictly ascending + non-overlapping (same-source-order rule)."""
    out: list[tuple[float, float]] = []
    prev_end = -math.inf
    for seg in segments:
        if not (isinstance(seg, (tuple, list)) and len(seg) == 2):
            raise ValueError(f"segment must be a (start, end) pair, got {seg!r}")
        s, e = float(seg[0]), float(seg[1])
        if not (math.isfinite(s) and math.isfinite(e)):
            raise ValueError(f"segment timestamps must be finite, got ({s}, {e})")
        if e <= s:
            raise ValueError(f"segment end<=start ({s}->{e})")
        if (e - s) < _MIN_MOMENT_S:
            raise ValueError(f"segment too short ({e - s:.2f}s)")
        if s < prev_end:
            raise ValueError(f"segments must be ascending and non-overlapping ({s} < {prev_end})")
        out.append((s, e))
        prev_end = e
    return out


def _canon_affinity_list(handles) -> list[str]:
    """Canonical account-handle list for Moment.affinities (strip '@', lowercase)."""
    if not handles: return []
    out = []
    for h in handles:
        s = str(h or "").strip().lstrip("@").lower()
        if s: out.append(s)
    return sorted(set(out))


def _canon_account_str(h) -> str:
    return str(h or "").strip().lstrip("@").lower()


def _canon_affinity_list(handles) -> list[str]:
    """Canonical account-handle list for Moment.affinities (strip '@', lowercase)."""
    if not handles: return []
    out = []
    for h in handles:
        s = str(h or "").strip().lstrip("@").lower()
        if s: out.append(s)
    return sorted(set(out))


def _canon_account_str(h) -> str:
    return str(h or "").strip().lstrip("@").lower()


def _segments_dump(segs: list[tuple[float, float]]) -> list[list[float]]:
    return [[s, e] for s, e in segs]

def realized_seconds(pick: "MomentPick | Moment") -> float:
    """Playable duration: sum of segment spans when segments present, else envelope width."""
    segs = getattr(pick, "segments", None) or []
    if segs:
        return sum(e - s for s, e in segs)
    return pick.end - pick.start


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
    # Per-account Render lifecycle. CULM-9 (decided 2026-06: RESERVE, not wire): a Render is BORN `rendered`
    # and NO driver advances it today — nothing moves it to queued/published/analyzed, and no GC retires it
    # (the Post carries the publish/analyze arc; the Render is just the artifact pointer). The members are
    # KEPT (not dropped) because views_results._SHIPPABLE_RENDER reads the enum by name (queued/published/
    # analyzed count as shippable; only `retired` would gate) — so the reader's guard is currently a no-op
    # (a render is always `rendered`), an HONEST reserved-for-future-lifecycle surface, NOT an active arc.
    # Wiring an advancer + a retire-GC is a deferred lifecycle decision (YAGNI — don't build a speculative
    # GC for an enum). Pinned by test_render_model. Distinct enum (not ClipState) so the per-account
    # artifact never entangles with the shared substrate Clip's caption/stitch states.
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
    # was sent) — the post MAY already be live on the platform. The backend has no idempotency key
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
    Platform.tiktok: Fmt.r9x16, Platform.instagram: Fmt.r9x16, Platform.youtube: Fmt.r9x16,  # youtube=Shorts (9:16); was 16:9 long-form
    Platform.facebook: Fmt.r1x1, Platform.twitter: Fmt.r16x9,
}

# AUDIT (g): hard per-surface MAX clip length (seconds). A v1 version of this dict was REMOVED
# as a FALSE safety contract — it was declared but never enforced, so a 180s pick fanned out to
# YouTube/Twitter at full length, silently over their caps. This is the REAL enforcement: at
# crosspost time a clip whose PLAYABLE duration (its moment window, end - start — Clip has no
# duration field) exceeds the cap for a platform is SKIPPED for THAT surface only (it can still
# post to platforms whose cap it satisfies). Values are the real short-form ceilings:
#   instagram (Reels) 90s · tiktok 600s (10 min) · youtube (Shorts) 180s · twitter 140s ·
#   facebook (Reels) 90s.
# A platform with no meaningful short-form cap may be OMITTED here -> no clamp for it.
# Enforcement is FAIL-OPEN on unknown duration: if the window is 0/None/unmeasurable (or the
# moment is missing), the clip is NOT skipped — never silently drop a post over an unprobed
# length (the removed dict's sin was the opposite: pretending to guard while letting all through).
PLATFORM_MAX_SECONDS = {
    Platform.instagram: 90,
    Platform.tiktok: 600,
    Platform.youtube: 180,   # Shorts ceiling (3 min); was 60s (pre-Oct-2024 rule)
    Platform.twitter: 140,
    Platform.facebook: 90,
}


# ---- units ----
# LEDGER FORWARD-COMPAT (audit x-f4): the ledger models do NOT set model_config extra=... — they rely on
# pydantic v2's DEFAULT, which is extra="ignore" (unknown fields are silently DROPPED on load, not an error).
# This is load-bearing: an OLDER binary loading a ledger written by a NEWER schema (extra fields present) must
# parse it, dropping the keys it doesn't know, never crash. Do NOT switch any ledger model to extra="forbid"
# — it would turn a forward-rolled ledger into a hard ControlFileError on the old binary. Pinned by
# tests/test_models_extra_ignore.py::test_unknown_field_is_ignored.
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
    degraded_reason: Optional[str] = None       # RF1: the single VISIBLE-degradation channel. Set when a
                                                # fail-open path (casting probe error) lands the source on the
                                                # fan-to-all fallback — so a silent collapse is never invisible.
    meta: dict = Field(default_factory=dict)
    created_at: Optional[str] = None            # content-lifecycle: ISO-8601 UTC ingest day, set at
                                                # _catalogue_file / rebuild discovered. None on old ledgers ->
                                                # migration v2->v3 backfill (file mtime, else stamp). The Review
                                                # day-anchor ("clips I dropped in").

class Moment(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
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
    hook: Optional[str] = None                  # punchy top-third line for the clip, AUTHORED by the
                                                # frame-seeing hook gate (viewer-POV). None = a clean
                                                # (hookless) clip. Optional/None -> old ledgers load fine.
    hook_removed: Optional[str] = None          # the model's hook that is_weak_hook STRIPPED (dup/opening-
                                                # template cluster) instead of discarding it. Preserved so
                                                # Studio Review can show "hook removed" + let the operator
                                                # restore it (the 29% blank rate is mostly good hooks the
                                                # mechanical guard killed, not dead footage). None = nothing
                                                # was stripped (old ledgers load fine).
    signal_score: float = 0.0
    hook_strategy: Optional[str] = None         # M2 router: text | clean_final | clean_awaiting_strategy:<key>
                                                # | stitch:<format>. Observe-only annotation; None = unrouted
                                                # (router off / old ledgers load). One writer: router.route_moments.
    intro_matches: Optional[list[dict]] = None  # M6 intro-tease: the LLM-vision matcher's ranked pairings for
                                                # this moment — [{asset_id, fit_score, rationale, tease_text}, ...],
                                                # best-fit first. None = unmatched (matcher off / no answer / old
                                                # ledgers load). One writer: intro_match.ingest_intro_match.
    affinities: list[str] = Field(default_factory=list)   # Account-First: handles this moment was CAST to.
                                                # the single-owner crosspost gate input (P5 pick + P13 operator
                                                # cast_add/cast_remove); [] = persona-blind -> fans to all.
                                                # SUBSET of the batch target; stamped at pick, operator-mutable.
    hook_frames_unread: bool = False            # AGENT-9: True when this pick's hook was authored with frames
                                                # ATTACHED but UNREAD (the model answered single-shot, the granted
                                                # Read never fired) -> a text-grounded, NOT frame-grounded hook.
                                                # Additive (default False; old ledgers load fine); counted in
                                                # RunSummary.frames_unread so the degraded hook is VISIBLE.
    error_reason: Optional[str] = None
    segments: list[tuple[float, float]] = Field(default_factory=list)   # S1 supercut: ordered non-overlapping spans; [] = single-window (old ledgers load fine)
    clip_profile: Optional[str] = None          # P5: owner persona's resolved length band at pick birth
                                                # (config.resolve_clip_profile(owner)); None = persona-blind

    @field_validator("affinities", mode="before")
    @classmethod
    def _canon_affinities(cls, v):
        return _canon_affinity_list(v or [])
                                                # -> P9 falls back to global (byte-identical).
    framing: Optional[str] = None               # P5: owner persona's crop bias at pick birth ("top"/"center");
                                                # None = persona-blind -> P9 falls back to global.

    @model_validator(mode="after")
    def _apply_segments_envelope(self) -> "Moment":
        if self.segments:
            segs = _validate_segments(self.segments)
            object.__setattr__(self, "segments", segs)
            object.__setattr__(self, "start", segs[0][0])
            object.__setattr__(self, "end", segs[-1][1])
        return self

    @field_serializer("segments")
    def _dump_segments(self, segs: list[tuple[float, float]]) -> list[list[float]]:
        return _segments_dump(segs)

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
    media_url: Optional[str] = None             # FIX F44 — cached hosted URL, uploaded once
    meta_captions: dict = Field(default_factory=dict)   # surface -> {caption, hashtags}
    error_reason: Optional[str] = None
    hook_burn_failed: bool = False              # V2 M1/F9: a hook was WANTED but couldn't burn (ffmpeg
                                                # lacks the text filter, or build_ass yielded empty) — the
                                                # clip still rendered fail-open but lost its hook. Surfaced
                                                # in the run summary so the silent drop is never invisible.

class Post(BaseModel):
    id: str
    parent_id: str                              # clip id
    state: PostState = PostState.awaiting_approval   # RF1: BORN awaiting_approval (no-auto-publish invariant);
                                                # the prior `queued` default inverted the human gate — a Post()
                                                # with no explicit state was publishable on the next publish_due.
    account: str                                # canonical handle, e.g. "a"
    account_id: str                             # hosted-backend id (FIX F06)
    platform: Platform
    caption: str
    hashtags: list[str] = Field(default_factory=list)
    media_urls: list[str] = Field(default_factory=list)
    aspect: Fmt = Fmt.r9x16
    scheduled_time: Optional[str] = None
    submission_id: Optional[str] = None         # set BEFORE network return is confirmed (dedupe)
    public_url: Optional[str] = None
    media_id: Optional[str] = None              # Leg 2 (Insight): the Instagram Graph media id of THIS live post,
                                                # resolved from /{ig_user}/media by permalink (reconcile.resolve_media_ids).
                                                # The identity the sole-source Graph insights read keys on. None until
                                                # resolved / for non-IG posts (back-compat: old ledgers load fine).
    product_type: Optional[str] = None          # Leg 2 (Insight): the media's real media_product_type (AD|FEED|STORY|
                                                # REELS), stamped from the live media at resolve alongside media_id. The
                                                # insights request is DERIVED from it (meta_graph.insights_metrics_for) so
                                                # a feed video is never asked for a reels-only metric. None until resolved.
    error_reason: Optional[str] = None
    metrics: dict = Field(default_factory=dict)
    # P3 append-only metrics time-series: one sparse row per captured cadence offset, each a superset of
    # a `metrics` snapshot + {"offset","captured_at"} provenance. `metrics` above stays EXACTLY the LATEST
    # snapshot (byte-identical back-compat: every existing reader stays on it). An old ledger row lacking
    # this key defaults to [] (Pydantic default_factory; independent of extra="ignore").
    metrics_series: list[dict] = Field(default_factory=list)
    render_id: Optional[str] = None     # optional Render pointer; None -> serve Clip.path
    # P1 attribution key (one writer = crosspost): the creative dims P3 aggregates reach by and P4 ranks.
    # All None on old ledgers + when the upstream dim is unknown (validate-or-default; never crashes a load).
    first_frame_kind: Optional[str] = None  # "visual" | "transcript" — how the opening frame was chosen
    clip_profile: Optional[str] = None      # song | talk — the per-video-type group ("hook for which video type")
    cut_seconds: Optional[float] = None     # rendered clip length (observational; length not varied)
    variation_axis: Optional[str] = None    # P2 (one writer = crosspost): the cheap-text axis this variant moved
    # Leg 3 (Culmination) — the two varied-but-previously-unstamped dims, so aggregate_by_dim can rank
    # them like any P4 dim. All None on old ledgers -> skipped by aggregate_by_dim (back-compat).
    top_bias: Optional[bool] = None     # framing (one writer = crosspost): moment.framing at mint; joins _P4_DIMS.
    publish_hour: Optional[int] = None  # timing (one writer = run.py/reconcile published transition): the operator-
                                        # local HOUR of the TRUE publish time (published_at bucketed in operator_tz).
    publish_dow: Optional[int] = None   # timing: the operator-local weekday (0=Mon..6=Sun) of the true publish time.
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

    @field_validator("account", mode="before")
    @classmethod
    def _canon_post_account(cls, v):
        return _canon_account_str(v) if v else v

    @model_validator(mode="after")
    def _enforce_published_url_invariant(self) -> "Post":
        # R1: PostState.published / analyzed / retired is a TERMINAL SUCCESS state. Its meaning is "the
        # operator has a permalink they can verify" — not just "the backend acknowledged". Bind that meaning
        # at the type level so no door (DryRunPoster.publish, _publish_one, actions.mark_published,
        # cli.cmd_resolve, a stray Post(...) constructor) can produce the ghost row Post(state=published,
        # public_url=""). Five such rows on 2026-06-29 (5 sidecar JSONs at 05_scheduled/post_*.json) made the
        # operator say "I can't see them" — they SHIPPED to dryrun and the Posted tub had nothing to render.
        # A terminal-positive row therefore requires a REAL permalink (dryrun-boundary M3: a dryrun post
        # never reaches a terminal state — it halts `queued` at the publish_due boundary — so there is no
        # 'dryrun://' escape any more). failed/error/etc are NEGATIVE terminals and may legitimately lack a
        # URL (a pre-network error has nothing to point at), so they're NOT gated here.
        if self.state in _POST_TERMINAL_REQUIRES_URL:
            if not (self.public_url or "").strip():
                raise ValueError(
                    f"Post(id={self.id!r}, state={self.state.value}) requires a non-empty public_url — "
                    f"'published'/'analyzed'/'retired' mean the operator has a real permalink. A backend "
                    f"that can't return one MUST park in needs_reconcile until the reconciler back-fills it "
                    f"(R1 invariant)."
                )
        return self


# R1: the terminal-positive set — states that imply "publish landed; here's a permalink".
# Defined at module scope so the @model_validator above can reference it cleanly.
_POST_TERMINAL_REQUIRES_URL = frozenset({PostState.published, PostState.analyzed, PostState.retired})


def is_real_submission_id(sid: Optional[str]) -> bool:
    # CULM-3: a REAL backend post id, NOT the birth client idempotency token (crosspost stamps
    # submission_id="fanops_<hash>" so an ambiguous publish stays pollable). Analytics + status are keyed by
    # the REAL id; a fanops_ token 404s. A published/analyzed post must carry a real id before pull_metrics
    # attributes, else learning silently freezes (the post never reaches a non-degraded analyzed shape).
    # (dryrun-boundary M2 removed the dryrun_ synthetic-id path: a dryrun post no longer stamps a
    # submission_id at all — it halts `queued` at the boundary — so this predicate need not name it.)
    if not sid:
        return False
    return not sid.startswith("fanops_")


class HookSource(str, Enum):
    per_account = "per_account"          # legacy provenance label (pre-P9 per-handle map); retained for old Render rows
    shared_fallback = "shared_fallback"  # the owner moment's on-screen hook (m.hook)
    none = "none"                        # no hook at all (hookless clip)


class Render(BaseModel):
    # The per-account SHIPPABLE artifact — a first-class child of the shared substrate Clip (the audit
    # foundation: nothing owned the per-account render, so "which file does @a ship" was smeared across
    # Post.parent_id + Post.media_urls + a loose orphan mp4, and the serve route GUESSED). A Render owns:
    # the rendered bytes (`path`), the burned on-screen hook (`hook_text` — THE single home; Post.variant_hook
    # is a read-only mirror), the upload cache (`media_url`, FIX-F44 parity), its lifecycle (`state`), and its
    # lineage (`batch_id`/`source_id`, for batch-scoped filing + the durable archive). CONTENT-ADDRESSED by
    # (clip, hook, band, framing): two surfaces with the SAME spec compute the same id -> ONE render, ONE file
    # (the anti-explosion dedup). A hookless surface has Post.render_id None
    # and serves the shared Clip.path. Captions are NOT here — they stay surface-keyed on the shared Clip
    # (the caption pipeline is intentionally untouched).
    id: str                                     # child_id("render", clip_id, hook[\x1fband:lo-hi][\x1fframe:x]) — see crosspost.account_render_spec
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
    hook_source: HookSource = HookSource.none   # P3: was this account's on-screen hook its OWN (per_account) or a
                                                # shared-moment fallback (shared_fallback)? crosspost's own_hook-vs-shared
                                                # resolution computed this then DISCARDED it (Post.variant_hook is None in BOTH
                                                # the fallback and the no-hook case). Additive; legacy renders -> none.
    cut_seconds: Optional[float] = None         # P3: the REALIZED seconds (ce-cs) of THIS account's cut (clip.py:449),
                                                # vs Clip.cut_seconds (the shared window) / the band-NAME label. None
                                                # when not a real account cut (failed cut or shared burn).


_ACCOUNT_HANDLE_RE = re.compile(r"^[a-z0-9._-]+$")

def normalize_account_handle(handle: str) -> str:
    """Canonical account handle — strip whitespace, drop a leading '@', lowercase. Identity on an already-
    canonical accounts.json value; the ONE read-side safety net for legacy ledger rows that still carry '@'."""
    return (handle or "").strip().lstrip("@").lower()


def validate_account_handle(handle: str) -> str:
    """Strict WRITE-boundary canonicalizer — lowercase, no '@', charset [a-z0-9._-]. Raises ValueError on
    blank or illegal characters (mirrors persona_store's _norm_focus validator at the control-file edge)."""
    h = (handle or "").strip().lstrip("@").lower()
    if not h:
        raise ValueError("handle is required")
    if not _ACCOUNT_HANDLE_RE.fullmatch(h):
        raise ValueError(f"invalid handle: {handle!r}")
    return h


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
    burn_subs: Optional[bool] = None                     # per-batch subtitle override: None => use global cfg.burn_subs;
                                                         # False => skip (e.g. music clips where lyric subs hurt); True => force on

def batch_id(name: str, created_at: str) -> str:
    """Content-addressed id keyed on (name, microsecond-precision created_at): a re-submit of the same
    (name, birth) yields the same id (idempotent), two distinct create_batch calls cannot collide.
    Deterministic across processes (ids.content_id)."""
    return content_id("batch", name, created_at)


# ---- ledger-rebuild (Instagram is the source of truth): the ImportedMedia entity ----
class ImportedMedia(BaseModel):
    # A live IG post PROBED from the platform that we did NOT author — it has NO Clip/Moment/Source in our
    # system, so it CANNOT be a Post (Post.parent_id is required and every lineage reader — posts_of/clips_of/
    # moments_of — depends on it). ImportedMedia is the DECIDED representation (PRD option a): Post keeps
    # meaning "authored here"; this peer means "mirrored from live". Keyed by the Graph `media_id` itself (a
    # NATURAL key — the platform's own id, NOT a content_id/child_id: there is no parent to hash off). Carries
    # exactly what the platform returns + what the insights read fills: permalink, product_type, metrics, and
    # the append-only metrics_series (mirroring Post's two fields). NO clip lineage by construction —
    # `hasattr(im, "parent_id")` is False, so a lineage reader can never be handed one. Additive top-level
    # `imported_media` map (v9->v10); old ledgers load with {} — the OFF/baseline shape is byte-identical.
    # fan-accounts-repost-freely: an ImportedMedia MIRRORS live, it never blocks reposting — no supersede/dedupe
    # logic keys on it anywhere.
    media_id: str                               # the Instagram Graph media id — THE natural key (one-per-media)
    permalink: Optional[str] = None             # the live IG permalink (the match key against a Post.public_url)
    product_type: Optional[str] = None          # media_product_type (AD|FEED|STORY|REELS); the insights request is
                                                # DERIVED from it (meta_graph.insights_metrics_for). None until resolved.
    timestamp: Optional[str] = None             # the media's live publish timestamp (Graph `timestamp`), for display/order
    caption: Optional[str] = None               # the media's live caption text, when the probe returns it (display-only)
    account: Optional[str] = None               # the credentialed handle this media was enumerated under (scope label;
                                                # META_IG_USER_ID is single-handle, so the projection is scoped to ONE handle)
    metrics: dict = Field(default_factory=dict)          # the LATEST insights snapshot (same shape as Post.metrics)
    metrics_series: list[dict] = Field(default_factory=list)   # append-only per-cadence rows (mirrors Post.metrics_series)
    error_reason: Optional[str] = None          # breadcrumb for an unresolved product_type / a transient insights miss
    imported_at: Optional[str] = None           # wall-clock ISO-Z when first mirrored into the ledger (audit)


# ---- agent-step contracts (all carry request_id for correlation — FIX F21; the GATE stamps the
# authoritative rid + (for moments) source_id AFTER validation — MOL-167: decision schemas do NOT ask the model to echo them) ----
class MomentRequest(BaseModel):
    source_id: str
    request_id: str
    duration: float
    transcript: list[dict] = Field(default_factory=list)
    transcript_total: int = 0       # AGENT-2: FULL segment count (transcript may be budget-truncated); 0 == not truncated
    signal_peaks: list[dict] = Field(default_factory=list)
    language: Optional[str] = None
    guidance: str = ""
    clip_profile: str = "talk"      # content-type band selector (bands.band_for); "talk" -> today's behavior
    frames: list[str] = Field(default_factory=list)   # Phase 1: source stills the vision author SEES while picking + hooking (fail-open [] when no source)
    personas: list[dict] = Field(default_factory=list)   # P1: per-active-persona FULL spec dicts (handle+directive+band+
                                                         # framing+selection_scope+hook_angle+corpus). [] -> persona-blind.

# P1 (MOL-142): the keys each MomentRequest.personas[] entry carries once _pick_personas resolves (P4a).
PERSONA_PICK_SPEC_KEYS = frozenset({"handle", "directive", "band", "framing", "selection_scope",
                                    "content_focus", "intensity", "hook_angle", "corpus"})

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
    personas: list[str] = Field(default_factory=list)   # P1: owner handle(s); single-owner convention (len<=1).
                                                         # 0 = persona-blind; 1 -> Moment.affinities at birth.
    segments: list[tuple[float, float]] = Field(default_factory=list)   # S1 supercut: ordered non-overlapping spans; [] = single-window (byte-identical)

    @field_validator("start", "end")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("timestamp must be a finite number (no NaN/Infinity)")
        return v

    @field_validator("personas")
    @classmethod
    def _single_owner(cls, v: list[str]) -> list[str]:
        if len(v) > 1:
            raise ValueError("MomentPick.personas must have at most one owner handle")
        return v

    @model_validator(mode="after")
    def _apply_segments_envelope(self) -> "MomentPick":
        if self.segments:
            segs = _validate_segments(self.segments)
            self.segments = segs
            self.start = segs[0][0]
            self.end = segs[-1][1]
        return self

    @field_serializer("segments")
    def _dump_segments(self, segs: list[tuple[float, float]]) -> list[list[float]]:
        return _segments_dump(segs)

class MomentDecision(BaseModel):
    source_id: Optional[str] = None             # gate-populated (moments kind); not model-authored
    request_id: Optional[str] = None            # gate-populated; not model-authored
    picks: list[MomentPick] = Field(default_factory=list)

# M1b pass-2: ONE per-pick frame-seeing hook gate. The request carries the PICKED WINDOW + frames
# extracted over that window (clip.fit_window), so the author writes a hook grounded in the exact
# footage the clip opens on — the operator's #1 ask. Gate key = moment_hooks__{source_id}.{owner}.{token}
# (owner omitted when persona-blind), so N picks -> N independent gates; correlation is by the gate KEY
# (filename), not a body field.
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
    personas: list[dict] = Field(default_factory=list)      # [{handle, persona}] owner voice for P6; [] -> shared hook

class MomentHookDecision(BaseModel):
    request_id: Optional[str] = None            # gate-populated; not model-authored
    hook: Optional[str] = None      # the window-grounded on-screen RETENTION hook; None/"" -> this pick ships CLEAN (valid)
    hook_frames_unread: bool = False   # AGENT-9: NOT a model field — the responder STAMPS it (like request_id) when
                                       # claude_json_meta proves the attached frames were never read; ingest lifts it
                                       # onto Moment.hook_frames_unread. Default False -> a model-only answer is unchanged.

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
    # AGENT-7: hook/axis/rationale were REMOVED — the caption gate is hashtags-only (the frame-seeing moment
    # gate owns the on-screen hook via m.hook), so these were never read and only widened the LLM --json-schema,
    # tempting the model to author a hook here. The DORMANT variant A/B machinery's persisted side lives on the
    # stored meta_captions entry (_caption_entry hook/axis keys, read by variant_amplify/digest/crosspost) and
    # is untouched. Old on-disk responses carrying these keys still parse (pydantic extra="ignore").

class CaptionSet(BaseModel):
    request_id: Optional[str] = None            # gate-populated; not model-authored
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
