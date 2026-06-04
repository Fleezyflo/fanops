"""Cross-post fan-out: one captioned, non-held, non-retired clip -> one Post per (active
account, platform). Post id AND schedule seed derive from surface_key() via SHA1 (FIX F00/F77
— cross-process stable; v1's hash() duplicated posts every run). Each surface posts the clip
in ITS platform's aspect, rendering on demand (FIX F20). The resolved NUMERIC account_id is
stored (FIX F06). decide_tag is invoked (FIX F31). Held/retired clips are skipped (FIX F55)."""
from __future__ import annotations
import hashlib, random
from datetime import datetime, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.accounts import Accounts
from fanops.models import (Post, PostState, ClipState, Fmt,
                           PLATFORM_ASPECT, PLATFORM_MAX_SECONDS)
from fanops.ids import child_id, surface_key, _hash
from fanops import overlay
from fanops.clip import render_moment
from fanops.tagging import decide_tag, ARTIST_HANDLE
from fanops.timeutil import parse_iso as _parse, iso_z

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
                 *, clip_id: str = "") -> str:
    seed = _seed(account, platform, date_str, clip_id)
    rng = random.Random(seed)                        # ONE stable stream per (surface,clip) — NOT
                                                     # reseeded per index (that made the step a
                                                     # fresh draw each call -> non-monotonic).
    anchor = base + timedelta(minutes=seed % _ANCHOR_SPAN)
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
        for i, surf in enumerate(surfaces):
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
                continue   # TODO(Task 23): log skipped surface — activated after captioning, no caption
            caption = cap["caption"]
            # subtle, non-synchronized artist tag on its own line (FIX F31)
            # clip.id (the captioned seed clip) keys the schedule so two different clips don't
            # collide on the same surface/minute (AUDIT H1/H2). Use the seed clip, not target_clip,
            # so the same content schedules consistently across its per-platform aspect renders.
            sched = surface_time(base, surf.account, surf.platform.value, date_str, i, clip_id=clip.id)
            if decide_tag(led, account=surf.account, clip_id=clip.id, when=_parse(sched)):
                caption = f"{caption}\n{ARTIST_HANDLE}"
            # Creative variation (gated, fail-open): when enabled AND this surface carries a hook,
            # burn a per-account hook onto the SHARED base clip -> a per-account file, and stamp a
            # DETERMINISTIC variant_key (= surface_key, content-addressed; NOT random/hash()) plus
            # the hook text (observe-only). With variation OFF or no hook, media_urls stays [] so
            # publish_due's lazy ensure_clip_media(parent_id) path runs (today's shared-clip
            # behavior). With it ON, the pre-populated file:// URL makes publish_due upload the
            # variant file through the SAME poster seam (run.py: `if not post.media_urls`).
            variant_key = None
            variant_hook = None
            media_path = target_clip.path
            hook_v = (cap.get("hook") if isinstance(cap, dict) else None)
            if cfg.creative_variation and hook_v:
                variant_key = surface_key(surf.account, surf.platform.value)
                tw, th = {Fmt.r9x16: (1080, 1920), Fmt.r1x1: (1080, 1080),
                          Fmt.r16x9: (1920, 1080)}.get(aspect, (1080, 1920))
                cfg.clips.mkdir(parents=True, exist_ok=True)   # variant lives beside clips (clip.py does the same)
                vpath = str(cfg.clips / f"{target_clip.id}_{_hash('variant', variant_key)}.mp4")
                overlay.burn_hook_only(target_clip.path, vpath, hook_v, width=tw, height=th,
                                       font=cfg.subtitle_font)   # fail-open: vpath always exists
                media_path = vpath
                variant_hook = hook_v
            led.add_post(Post(
                id=pid, parent_id=target_clip.id, state=PostState.queued,
                account=surf.account, account_id=surf.account_id, platform=surf.platform,
                caption=caption, hashtags=cap.get("hashtags", []), aspect=aspect,
                scheduled_time=sched,
                media_urls=[f"file://{media_path}"] if cfg.creative_variation and hook_v else [],
                # AUDIT H1: stamp a stable, content-addressed CLIENT idempotency token at birth so
                # an ambiguous publish is ALWAYS pollable (a real Blotato id overwrites it in
                # blotato_rest). pid is content-addressed -> a re-run computes the identical token.
                submission_id=f"fanops_{_hash('idemp', pid)}",
                variant_key=variant_key, variant_hook=variant_hook))
        led.set_clip_state(clip.id, ClipState.queued)
    return led
