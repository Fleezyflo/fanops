"""Cross-post fan-out: one captioned, non-held, non-retired clip -> one Post per (active
account, platform). Post id AND schedule seed derive from surface_key() via SHA1 (FIX F00/F77
— cross-process stable; v1's hash() duplicated posts every run). Each surface posts the clip
in ITS platform's aspect, rendering on demand (FIX F20). The resolved NUMERIC account_id is
stored (FIX F06). decide_tag is invoked (FIX F31). Held/retired clips are skipped (FIX F55)."""
from __future__ import annotations
import hashlib, random
from datetime import datetime, timedelta, timezone
from fanops import overlay
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.accounts import Accounts
from fanops.bands import band_for
from fanops.models import (Post, PostState, ClipState, Fmt, Render, RenderState,
                           PLATFORM_ASPECT, PLATFORM_MAX_SECONDS)
from fanops.ids import child_id, surface_key, _hash
from fanops.clip import render_moment, render_account_cut
from fanops.tagging import decide_tag, ARTIST_HANDLE
from fanops.timeutil import parse_iso as _parse, iso_z
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

def crosspost_clips(led: Ledger, cfg: Config, accounts: Accounts, *, base_time: str) -> Ledger:
    base = _parse(base_time)
    date_str = base.date().isoformat()
    surfaces = accounts.surfaces()
    acct_by_handle = {a.handle: a for a in accounts.accounts}    # M2: per-account clip_profile lookup by handle
    # operate on the set of clips that are captioned + not held + not retired
    seed_clips = [c for c in led.clips_in_state(ClipState.captioned)
                  if not c.held and not led.is_retired_clip(c.id)
                  and not led.is_retired_moment(c.parent_id)]
    for clip in seed_clips:
        moment_id = clip.parent_id
        # AUDIT (g): the clip's PLAYABLE duration is its MOMENT window (end - start). Clip has no
        # .duration field; the seed clip is rendered from [start,end] of the source, so the window
        # — not the full source length — is the right value. Guard a missing moment defensively
        # (treat as UNKNOWN -> fail-open, never skip). dur is None/<=0 => unknown.
        m = led.moments.get(moment_id)
        clip_dur = (m.end - m.start) if m is not None else None
        # Account-First Studio: resolve the named-batch account target ONCE per clip via the
        # moment->source lineage (m.parent_id == source id). A non-empty target_accounts HARD-bounds
        # which surfaces a post is born for (the casting-OFF enforcement path); empty/missing => no skip.
        src = led.sources.get(m.parent_id) if m is not None else None
        src_batch = src.batch_id if src is not None else None
        tgt = led.get_batch(src_batch).target_accounts if (src_batch and led.get_batch(src_batch)) else []
        n_skipped = 0   # T5: per-CLIP tally of batch-target exclusions (reset inside the clip loop, never bled across clips)
        for i, surf in enumerate(surfaces):
            if tgt and surf.account not in tgt:
                n_skipped += 1
                get_logger(cfg)("crosspost", clip.id, "batch_target_skip",
                                surface=f"{surf.account}/{surf.platform.value}", batch=src_batch)
                continue   # batch targets a specific account set; this surface isn't in it (no post born)
            if cfg.account_casting and m is not None:
                if m.affinities:
                    if surf.account not in m.affinities:
                        continue   # affinity gate (Face 3): a cast moment fans ONLY to its accounts.
                elif cfg.cast_exclusive:
                    continue   # EXCLUSIVE routing: a moment that fit NO persona is DROPPED (suppressed), not
                               # fanned to all — the per-clip cast_dropped breadcrumb is logged after the loop.
                # else (uncast + non-exclusive): fall through -> fans to all (shipped Face 3 budget-mode behavior).
                # A2: the whole block is gated on cfg.account_casting so flag-OFF IGNORES persisted affinities.
            # Per-surface duration clamp: if the duration is KNOWN (> 0) AND exceeds this
            # platform's hard cap, SKIP this surface only (conservative — the clip can still post
            # to platforms whose cap it satisfies, and the whole clip isn't wedged). Unknown
            # duration or a platform with no cap -> DO NOT skip (fail-open; the old code posted
            # regardless and we must never silently drop a post over an unprobed length).
            max_secs = PLATFORM_MAX_SECONDS.get(surf.platform)
            if max_secs is not None and clip_dur is not None and clip_dur > 0 and clip_dur > max_secs:
                continue   # over-cap for this surface -> no post here (still posts to others)
            aspect = PLATFORM_ASPECT.get(surf.platform, Fmt.r9x16)
            target_clip = _clip_for_aspect(led, cfg, moment_id, aspect)
            if target_clip.state not in _REUSABLE_CLIP_STATES:
                continue   # on-demand render failed (error/dangling file) -> no post for this surface
            skey = surface_key(surf.account, surf.platform.value)
            pid = child_id("post", target_clip.id, skey)        # stable, content-addressed
            cap = clip.meta_captions.get(f"{surf.account}/{surf.platform.value}")
            if cap is None:
                # No caption for THIS surface (clip captioned for some surfaces but not this one).
                # An autonomous run would otherwise drop a real post with zero trace — leave a
                # breadcrumb before skipping so the missing post is diagnosable in run.log.
                get_logger(cfg)("crosspost", clip.id, "skipped_surface",
                                surface=f"{surf.account}/{surf.platform.value}")
                continue
            caption = cap["caption"]
            # subtle, non-synchronized artist tag on its own line (FIX F31)
            # clip.id (the captioned seed clip) keys the schedule so two different clips don't
            # collide on the same surface/minute (AUDIT H1/H2). Use the seed clip, not target_clip,
            # so the same content schedules consistently across its per-platform aspect renders.
            sched = surface_time(base, surf.account, surf.platform.value, date_str, i,
                                 clip_id=clip.id, lead_minutes=cfg.publish_lead_minutes)
            if decide_tag(led, account=surf.account, clip_id=clip.id, when=_parse(sched)):
                caption = f"{caption}\n{ARTIST_HANDLE}"
            # Per-account creative variation (gated by FANOPS_CREATIVE_VARIATION, default OFF; fail-open).
            # When ON and this surface has a per-account hook, mint a first-class RENDER — the per-account
            # shippable artifact (Stage B of the Render foundation). The Render owns the burned file + the
            # hook (the single source of truth); Post.render_id is the authoritative "which file" pointer and
            # the serve route resolves post->render->path (no media_urls guessing). CONTENT-ADDRESSED by
            # (target_clip.id, hook): two surfaces with the SAME hook on the SAME aspect-clip dedup to ONE
            # render + ONE file (anti-explosion). OFF / no hook -> render_id None, media_urls [], variant_*
            # None == today's shared-clip behavior (byte-identical).
            render_id = None
            variant_key = None
            variant_hook = None
            media_urls = []
            # ROOT FIX: the on-screen per-account hook is the FRAME-SEEING moment author's hook for THIS
            # handle (m.hooks_by_persona[handle]) — falling back to the shared moment hook. The blind
            # caption gate no longer authors a shipped hook. m guarded defensively (None -> no hook).
            hook_v = (m.hooks_by_persona.get(surf.account) or m.hook) if m is not None else None
            # M2: resolve THIS account's own LENGTH band. When it differs from the global band, the account's
            # Render is a real per-account CUT (its own length — @short 8-15s, @long 28-45s off the SAME
            # moment), content-addressed with a band tag so two accounts sharing one hook but cut to different
            # lengths never collide on one file. Same band -> the shared-clip burn path + un-tagged id
            # (byte-identical to today). did_cut gates the provenance stamp (only a real cut claims its length).
            acct_profile = cfg.resolve_clip_profile(acct_by_handle.get(surf.account))
            acct_band = band_for(acct_profile)
            wants_cut = bool(hook_v) and acct_band != band_for(cfg.clip_profile)
            did_cut = False
            if cfg.creative_variation and hook_v:
                variant_key = skey
                variant_hook = hook_v
                # content-address by (clip, hook[, band]): identical hooks on this aspect-clip share ONE render;
                # a per-account band adds a tag so @short and @long don't dedup onto one (wrong-length) file.
                token = f"{hook_v}\x1fband:{acct_band.lo:g}-{acct_band.hi:g}" if wants_cut else hook_v
                render_id = child_id("render", target_clip.id, token)
                if led.get_render(render_id) is None:
                    tw, th = {Fmt.r9x16: (1080, 1920), Fmt.r1x1: (1080, 1080),
                              Fmt.r16x9: (1920, 1080)}.get(aspect, (1080, 1920))
                    src_id = src.id if src is not None else None
                    vpath = cfg.render_path(src_batch, src_id, render_id, aspect)   # filed under clips/{batch}/{src}/
                    produced = False
                    if wants_cut:                                  # a real per-account CUT from the source at the account band
                        produced = render_account_cut(led, cfg, moment_id, aspect=aspect, profile=acct_profile,
                                                       hook=hook_v, out_path=vpath, top_bias=cfg.aware_reframe)
                        if not produced:                           # the cut failed -> fell back to the GLOBAL-length shared burn.
                            get_logger(cfg)("crosspost", target_clip.id, "account_cut_failed",   # never a SILENT wrong-length ship
                                            surface=f"{surf.account}/{surf.platform.value}", profile=acct_profile)
                    if not produced:                               # default band OR a failed cut -> shared-clip burn (vpath always exists)
                        overlay.burn_hook_only(target_clip.path, vpath, hook_v, width=tw, height=th,
                                               font=cfg.subtitle_font)   # atomic + fail-open: vpath always exists
                    did_cut = produced
                    led.add_render(Render(id=render_id, clip_id=target_clip.id, account=surf.account,
                                          surface_key=skey, hook_text=hook_v, path=vpath,
                                          state=RenderState.rendered, batch_id=src_batch, source_id=src_id,
                                          is_account_cut=produced))   # truthful: a failed cut fell back to the shared burn
                else:
                    did_cut = led.get_render(render_id).is_account_cut   # read the TRUTH (a prior failed cut stays False)
                media_urls = [f"file://{led.get_render(render_id).path}"]
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
                # M2: a real per-account cut stamps ITS OWN length profile (provenance + the P4 clip_profile
                # dim is then accurate); every other post keeps the global profile (byte-identical to today).
                clip_profile=(acct_profile if did_cut else cfg.clip_profile), batch_id=src_batch,   # Account-First Studio: denormalized batch (None=ungrouped)
                variation_axis=(cap.get("axis") if isinstance(cap, dict) else None)))   # P2: the axis this variant moved
        if tgt:   # T5: one structured exclusion summary per batched clip (the ONLY persistent record — excluded
                  # surfaces become no Post). Silent when tgt==[] (unbatched/ALL-sentinel) -> byte-identical fan-out.
            get_logger(cfg)("crosspost", clip.id, "batch_target_summary",
                            skipped=n_skipped, kept=len(surfaces) - n_skipped, batch=src_batch)
        if cfg.account_casting and cfg.cast_exclusive and m is not None and not m.affinities:
            # EXCLUSIVE drop: this clip's moment fit no active persona -> it produced NO post on any surface.
            # The suppressed clip leaves no Post, so this is the ONLY trace — never a silent drop (audit: no_silent_caps).
            get_logger(cfg)("crosspost", clip.id, "cast_dropped", moment=moment_id, reason="no_persona_fit")
        led.set_clip_state(clip.id, ClipState.queued)
    return led
