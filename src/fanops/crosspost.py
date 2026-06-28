"""Cross-post fan-out: one captioned, non-held, non-retired clip -> one Post per (active
account, platform). Post id AND schedule seed derive from surface_key() via SHA1 (FIX F00/F77
— cross-process stable; v1's hash() duplicated posts every run). Each surface posts the clip
in ITS platform's aspect, rendering on demand (FIX F20). The resolved NUMERIC account_id is
stored (FIX F06). decide_tag is invoked (FIX F31). Held/retired clips are skipped (FIX F55)."""
from __future__ import annotations
import hashlib, random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fanops import overlay
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.accounts import Accounts
from fanops.bands import band_for
from fanops.models import (Post, PostState, ClipState, MomentState, Fmt, HookSource,
                           PLATFORM_ASPECT, PLATFORM_MAX_SECONDS)
from fanops.ids import child_id, surface_key, _hash
from fanops.clip import render_moment, render_account_cut
from fanops.tagging import decide_tag, ARTIST_HANDLE
from fanops.timeutil import parse_iso as _parse, iso_z
from fanops.casting import account_selection_admits, casting_gate_pending, casting_gate_failed_to_open   # RF1 durable-selection gate + P1 casting-pending wait + xc-2 failed-gate defer
from fanops.log import get_logger

# Staggering constants. _STEP_MIN is the fixed per-index spacing; _JITTER_MAX is the bounded
# random nudge. AUDIT H1/H2: _JITTER_MAX MUST stay strictly less than _STEP_MIN so that
# index*_STEP + jitter is MONOTONIC in index — consecutive indices differ by at least
# _STEP_MIN - (_JITTER_MAX-1) > 0, so a higher index can never land before a lower one.
_STEP_MIN = 40
_JITTER_MAX = 30          # < _STEP_MIN — do not raise past it without breaking monotonicity
_ANCHOR_SPAN = 50         # per-(surface,clip) head start, 0.._ANCHOR_SPAN min after base

def _seed(account: str, platform: str, date_str: str, clip_id: str = "") -> int:
    # SHA1, NOT builtin hash() (FIX F00) — deterministic across processes. clip_id is part of the
    # seed (AUDIT H1/H2) so two clips on the SAME surface get DIFFERENT times instead of colliding
    # on the identical minute (the old seed ignored the clip -> lockstep fingerprint).
    h = hashlib.sha1(f"{account}|{platform}|{date_str}|{clip_id}".encode()).hexdigest()
    return int(h[:8], 16)

def surface_time(base: datetime, account: str, platform: str, date_str: str, index: int,
                 *, clip_id: str = "", lead_minutes: int = 0) -> str:
    seed = _seed(account, platform, date_str, clip_id)
    rng = random.Random(seed)                        # ONE stable stream per (surface,clip) — NOT
                                                     # reseeded per index (that made the step a
                                                     # fresh draw each call -> non-monotonic).
    # lead_minutes is a CONSTANT editorial offset (spec §4): it shifts every surface/index equally,
    # so the schedule stays content-addressed + byte-deterministic and the jitter<step monotonicity
    # proof is untouched (a constant translation preserves ordering). Default 0 == today's behavior.
    anchor = base + timedelta(minutes=lead_minutes + (seed % _ANCHOR_SPAN))
    # Draw the jitter sequence deterministically up to `index` so each index has its own nudge,
    # but the dominant term is the FIXED step -> strictly increasing in index.
    jitter = [rng.randint(0, _JITTER_MAX - 1) for _ in range(index + 1)][index]
    t = anchor + timedelta(minutes=index * _STEP_MIN + jitter)
    return iso_z(t)

# Clip states whose file is a usable render target. A denylist (everything-but-retired)
# wrongly reused error-state clips (dangling file); an allowlist also future-proofs against
# new ClipStates. Excludes error/held/captions_requested -> those fall through to a re-render.
_REUSABLE_CLIP_STATES = (ClipState.rendered, ClipState.captioned, ClipState.queued,
                         ClipState.published, ClipState.analyzed)

def _clip_for_aspect(led: Ledger, cfg: Config, moment_id: str, aspect: Fmt):
    for c in led.clips_of(moment_id):
        if c.aspect is aspect and c.state in _REUSABLE_CLIP_STATES:
            return c
    led, clip = render_moment(led, cfg, moment_id, aspect=aspect)   # rebind led (was discarded as led2)
    return clip

def account_render_spec(cfg: Config, *, clip, hook: str, acct):
    """The per-account Render IDENTITY (content-addressed id) + cut decision for (clip, hook, account).
    SINGLE SOURCE so the crosspost mint (below) AND the Studio re-burn (actions.reburn_hook) compute the SAME
    render_id and the SAME cut/burn choice — they MUST NOT drift (audit H1: a re-burn that recomputed a
    bare-hook id silently reverted the M2 per-account length/framing CUT). Pure (no I/O). acct may be None
    (removed/unknown handle) -> the global defaults -> a bare-hook id + no cut (byte-identical to the shared
    clip). content-addresses by (clip, hook[, band][, frame]); the band tag stays first so a band-only id is
    unchanged from M2b. Returns (render_id, wants_cut, profile, top_bias)."""
    profile = cfg.resolve_clip_profile(acct)
    band = band_for(profile)
    top_bias = cfg.resolve_top_bias(acct)
    band_differs = band != band_for(cfg.clip_profile)
    frame_differs = top_bias != cfg.aware_reframe
    wants_cut = bool(hook) and (band_differs or frame_differs)
    tag = [hook]
    if band_differs: tag.append(f"band:{band.lo:g}-{band.hi:g}")
    if frame_differs: tag.append(f"frame:{'top' if top_bias else 'center'}")
    return child_id("render", clip.id, "\x1f".join(tag)), wants_cut, profile, top_bias

@dataclass
class RenderPlan:
    """The result of rendering ONE per-account burned file (pure render-to-disk, NO ledger write) — adopted
    into a Render by the approve actions. render_id is content-addressed; produced=True ONLY when a real
    per-account CUT succeeded (else the shared-clip burn fallback wrote vpath); realized is the cut's seconds
    (None unless a cut). vpath ALWAYS exists once render_account_file returns (cut OR burn OR fail-open)."""
    render_id: str
    vpath: str
    produced: bool
    realized: float | None
    profile: str
    hook_source: HookSource
    batch_id: str | None
    source_id: str | None

_ASPECT_WH = {Fmt.r9x16: (1080, 1920), Fmt.r1x1: (1080, 1080), Fmt.r16x9: (1920, 1080)}

def render_account_file(led: Ledger, cfg: Config, *, post, acct, target_clip, src, caller: str = "approve") -> RenderPlan:
    """Render (burn) the per-account file for `post.variant_hook` to its content-addressed path and return its
    RenderPlan. PURE render-to-disk — NO ledger mutation — so it is safe LOCK-FREE (the approve warm pass) OR
    in-lock (the fallback). This is the mint's old burn block MOVED here (slice 2: burn on approval) so
    approval and the Studio re-burn share ONE renderer (anti-drift H1). A real CUT when the account's
    band/framing diverges (its own length + crop off the SAME moment); else the shared-clip burn onto
    target_clip. `caller` is the log event category (the only caller today is approval). Fail-open: any ffmpeg
    failure leaves a breadcrumb and falls back, and vpath always exists."""
    hook = post.variant_hook
    aspect = target_clip.aspect
    rid, wants_cut, profile, top_bias = account_render_spec(cfg, clip=target_clip, hook=hook, acct=acct)
    mom = led.moments.get(target_clip.parent_id)
    own = mom.hooks_by_persona.get(post.account) if mom is not None else None
    hook_source = HookSource.per_account if own else (HookSource.shared_fallback if hook else HookSource.none)
    batch_id = src.batch_id if src is not None else None
    source_id = src.id if src is not None else None
    vpath = cfg.render_path(batch_id, source_id, rid, aspect)
    tw, th = _ASPECT_WH.get(aspect, (1080, 1920))
    surface = f"{post.account}/{post.platform.value}"
    produced, realized = False, None
    if wants_cut:                                          # a real per-account CUT: the account's own length AND framing
        produced, realized = render_account_cut(led, cfg, target_clip.parent_id, aspect=aspect, profile=profile,
                                                hook=hook, out_path=vpath, top_bias=top_bias)
        if not produced:                                  # the cut failed -> fall back to the global-length shared burn
            get_logger(cfg)(caller, target_clip.id, "account_cut_failed", surface=surface, profile=profile)
    if not produced:                                      # default band/frame OR a failed cut -> shared-clip burn
        burned = overlay.burn_hook_only(target_clip.path, vpath, hook, width=tw, height=th, font=cfg.subtitle_font)
        if not burned:                                    # a hookless ship leaves the SAME breadcrumb the mint used to
            get_logger(cfg)(caller, target_clip.id, "hook_burn_failed", surface=surface)
    return RenderPlan(rid, vpath, produced, realized, profile, hook_source, batch_id, source_id)

def _moment_is_live_target(m) -> bool:
    """MOM-1: a captioned clip may seed a post only while its moment is a live render target (`decided`/
    `clipped`). A re-pick resets a moment to `picked` (and `error`/`retired` are dead) — its surviving captioned
    clip must NOT fan on stale casting intent until it re-decides. A MISSING moment keeps the existing fail-open
    (seed; the fan-out loop already handles `m is None` with an unknown duration) — narrowing that orphan path
    is out of MOM-1 scope."""
    return m is None or m.state in (MomentState.decided, MomentState.clipped)

def _seed_clips(led: Ledger) -> list:
    """The crosspost seed set: clips that are captioned + not held + not retired (clip or its moment), AND whose
    moment is still a live render target (decided/clipped, or absent -> existing fail-open). MOM-1: a re-pick
    resets a moment to `picked`; its surviving captioned clip must not seed a post on stale casting intent."""
    return [c for c in led.clips_in_state(ClipState.captioned)
            if not c.held and not led.is_retired_clip(c.id)
            and not led.is_retired_moment(c.parent_id)
            and _moment_is_live_target(led.moments.get(c.parent_id))]


def _mint_surface_post(led: Ledger, cfg: Config, clip, m, surf, i: int, *,
                       base, date_str: str, clip_dur, tgt, src_batch) -> int:
    """Born/skip ONE post for this clip x surface. Returns 1 when the surface is a BATCH-TARGET
    exclusion (the per-clip tally counts only these), else 0 for every other outcome (a born post OR
    any other skip). Owns all the per-surface gates + the add_post — the deepest-nested body of
    crosspost_clips, hoisted out verbatim (each `continue` -> `return 0`, the batch skip -> `return 1`)."""
    moment_id = clip.parent_id
    if tgt and surf.account not in tgt:
        get_logger(cfg)("crosspost", clip.id, "batch_target_skip",
                        surface=f"{surf.account}/{surf.platform.value}", batch=src_batch)
        return 1   # batch targets a specific account set; this surface isn't in it (no post born)
    if not account_selection_admits(cfg, led, m, surf.account):
        return 0   # RF1 durable-selection gate (Face 3), shared with caption-scoping so they can't drift:
                   # a cast source admits an account ONLY its AccountSelection's moments (or all, if its
                   # method is the LABELLED fan_all_default); a source with no selection falls back to the
                   # legacy affinities path; flag-OFF IGNORES selections (A2). See casting.account_selection_admits.
    # Per-surface duration clamp: if the duration is KNOWN (> 0) AND exceeds this
    # platform's hard cap, SKIP this surface only (conservative — the clip can still post
    # to platforms whose cap it satisfies, and the whole clip isn't wedged). Unknown
    # duration or a platform with no cap -> DO NOT skip (fail-open; the old code posted
    # regardless and we must never silently drop a post over an unprobed length).
    max_secs = PLATFORM_MAX_SECONDS.get(surf.platform)
    if max_secs is not None and clip_dur is not None and clip_dur > 0 and clip_dur > max_secs:
        return 0   # over-cap for this surface -> no post here (still posts to others)
    aspect = PLATFORM_ASPECT.get(surf.platform, Fmt.r9x16)
    target_clip = _clip_for_aspect(led, cfg, moment_id, aspect)
    if target_clip.state not in _REUSABLE_CLIP_STATES:
        return 0   # on-demand render failed (error/dangling file) -> no post for this surface
    skey = surface_key(surf.account, surf.platform.value)
    pid = child_id("post", target_clip.id, skey)        # stable, content-addressed
    cap = clip.meta_captions.get(f"{surf.account}/{surf.platform.value}")
    if cap is None:
        # No caption for THIS surface (clip captioned for some surfaces but not this one).
        # An autonomous run would otherwise drop a real post with zero trace — leave a
        # breadcrumb before skipping so the missing post is diagnosable in run.log.
        get_logger(cfg)("crosspost", clip.id, "skipped_surface",
                        surface=f"{surf.account}/{surf.platform.value}")
        return 0
    caption = cap["caption"]
    # subtle, non-synchronized artist tag on its own line (FIX F31)
    # clip.id (the captioned seed clip) keys the schedule so two different clips don't
    # collide on the same surface/minute (AUDIT H1/H2). Use the seed clip, not target_clip,
    # so the same content schedules consistently across its per-platform aspect renders.
    sched = surface_time(base, surf.account, surf.platform.value, date_str, i,
                         clip_id=clip.id, lead_minutes=cfg.publish_lead_minutes)
    if decide_tag(led, account=surf.account, clip_id=clip.id, when=_parse(sched)):
        caption = f"{caption}\n{ARTIST_HANDLE}"
    # Per-account creative variation (FANOPS_CREATIVE_VARIATION, default ON; fail-open). Slice 2 (burn
    # on approval): the mint RECORDS the per-account on-screen hook — the INTENT — but does NOT run
    # ffmpeg or mint a Render. The Render materializes when the operator APPROVES the surface
    # (actions_approve._adopt_render via render_account_file), so ONLY approved surfaces ever render
    # (the operator's anti-explosion ask — no "100 burned videos per run"). A born variant post carries
    # variant_hook + variant_key with render_id None + media_urls [] (review serves the MASTER clip;
    # approval points it at the burned file BEFORE it can become queued, so publish always has media).
    # OFF / no hook -> variant_* None, render_id None, media [] == the shared-clip behavior (byte-identical).
    render_id = None
    variant_key = None
    variant_hook = None
    media_urls = []
    # The on-screen per-account hook is the FRAME-SEEING moment author's hook for THIS handle
    # (m.hooks_by_persona[handle]), falling back to the shared moment hook. m guarded defensively.
    own_hook = m.hooks_by_persona.get(surf.account) if m is not None else None
    hook_v = own_hook or (m.hook if m is not None else None)
    if cfg.creative_variation and hook_v:
        variant_key = skey
        variant_hook = hook_v          # burned AT APPROVAL; account_render_spec(clip, hook, acct) there
                                       # recomputes the SAME content-addressed render id + cut decision (H1).
    existing = led.posts.get(pid)
    if existing is not None:
        # M2 (audit): a re-crosspost reaches an EXISTING post — pid is content-addressed on (clip,
        # surface), NOT the per-account hook, so add_post's first-write-wins would keep a STALE hook
        # after a re-decision (the operator would review/approve the old hook). Rewrite the variant
        # INTENT in place ONLY while the post is still AWAITING (the render is deferred to approval, so
        # there is no stale burn to undo) and ONLY on a real diff (a same-input re-run stays
        # byte-identical). A queued/published post keeps the hook the operator already approved.
        if existing.state is PostState.awaiting_approval and (
                existing.variant_hook != variant_hook or existing.variant_key != variant_key):
            existing.variant_hook = variant_hook; existing.variant_key = variant_key
        return 0
    led.add_post(Post(
        # BORN awaiting_approval (post-approval-lifecycle): nothing publishes until the operator
        # approves it in the Review tab. publish_due/publish_now iterate only `queued`, so a fresh
        # post is structurally unpublishable until Ledger.approve_post promotes it.
        id=pid, parent_id=target_clip.id, state=PostState.awaiting_approval,
        account=surf.account, account_id=surf.account_id, platform=surf.platform,
        caption=caption, hashtags=cap.get("hashtags", []), aspect=aspect,
        scheduled_time=sched, created_at=iso_z(datetime.now(timezone.utc)),   # wall-clock BIRTH (NOT in the pid)
        media_urls=media_urls,
        # AUDIT H1: stamp a stable, content-addressed CLIENT idempotency token at birth so
        # an ambiguous publish is ALWAYS pollable (a real Blotato id overwrites it in
        # blotato_rest). pid is content-addressed -> a re-run computes the identical token.
        submission_id=f"fanops_{_hash('idemp', pid)}",
        render_id=render_id, variant_key=variant_key, variant_hook=variant_hook,
        # P1 attribution key (one writer = here): the creative dims P3 groups reach by.
        # first_frame_kind/cut_seconds from the rendered clip, clip_profile from the global
        # video-type knob (its only home today — config.py). Absent dims default None cleanly.
        first_frame_kind=target_clip.first_frame_kind, cut_seconds=target_clip.cut_seconds,
        # clip_profile is the GLOBAL profile at the mint; a real per-account CUT re-stamps its OWN
        # length profile at APPROVAL (actions_approve._adopt_render), when the cut's realized truth is
        # known — so the P4 clip_profile dim is accurate by the time a post is published + learned on.
        clip_profile=cfg.clip_profile, batch_id=src_batch,   # Account-First Studio: denormalized batch (None=ungrouped)
        variation_axis=(cap.get("axis") if isinstance(cap, dict) else None)))   # P2: the axis this variant moved
    return 0


def crosspost_clips(led: Ledger, cfg: Config, accounts: Accounts, *, base_time: str) -> Ledger:
    base = _parse(base_time)
    date_str = base.date().isoformat()
    surfaces = accounts.surfaces()
    for clip in _seed_clips(led):   # captioned + not held + not retired
        # AUDIT (g): the clip's PLAYABLE duration is its MOMENT window (end - start). Clip has no
        # .duration field; the seed clip is rendered from [start,end] of the source, so the window
        # — not the full source length — is the right value. Guard a missing moment defensively
        # (treat as UNKNOWN -> fail-open, never skip). dur is None/<=0 => unknown.
        m = led.moments.get(clip.parent_id)
        clip_dur = (m.end - m.start) if m is not None else None
        # Account-First Studio: resolve the named-batch account target ONCE per clip via the
        # moment->source lineage (m.parent_id == source id). A non-empty target_accounts HARD-bounds
        # which surfaces a post is born for (the casting-OFF enforcement path); empty/missing => no skip.
        src = led.sources.get(m.parent_id) if m is not None else None
        # P1 casting-pending wait + xc-2: defer when the casting answer hasn't landed (gate open, unanswered) OR
        # when the gate SHOULD have opened but didn't (request_moment_casting I/O failure) — both mean "fan out
        # NEXT pass," never silently fan-to-all this pass (mirror: a clip waits for its caption gate).
        if src is not None and (casting_gate_pending(cfg, src.id, led=led)
                                or casting_gate_failed_to_open(cfg, led, accounts, src.id)):
            get_logger(cfg)("crosspost", clip.id, "casting_pending_skip", source=src.id)
            continue
        src_batch = src.batch_id if src is not None else None
        tgt = led.get_batch(src_batch).target_accounts if (src_batch and led.get_batch(src_batch)) else []
        n_skipped = 0   # T5: per-CLIP tally of batch-target exclusions (reset each clip, never bled across clips)
        posts_before = len(led.posts)   # c8-f2: detect a clip consumed with ZERO posts born (selection denied all)
        for i, surf in enumerate(surfaces):
            n_skipped += _mint_surface_post(led, cfg, clip, m, surf, i, base=base, date_str=date_str,
                                            clip_dur=clip_dur, tgt=tgt, src_batch=src_batch)
        born = len(led.posts) - posts_before
        if tgt:   # T5: one structured exclusion summary per batched clip (the ONLY persistent record — excluded
                  # surfaces become no Post). Silent when tgt==[] (unbatched/ALL-sentinel) -> byte-identical fan-out.
            get_logger(cfg)("crosspost", clip.id, "batch_target_summary",
                            skipped=n_skipped, kept=len(surfaces) - n_skipped, batch=src_batch)
        elif born == 0:   # c8-f2: an UNBATCHED clip consumed to `queued` with NO post born (every surface denied by
                          # selection / cap / render-fail) — the ONLY crosspost-stage record of the silent drop.
            get_logger(cfg)("crosspost", clip.id, "no_post_born", source=(src.id if src is not None else None))
        led.set_clip_state(clip.id, ClipState.queued)
    return led
