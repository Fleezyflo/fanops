"""Cross-post fan-out: one captioned, non-held, non-retired clip -> one Post per (active
account, platform). Post id AND schedule seed derive from surface_key() via SHA1 (FIX F00/F77
— cross-process stable; v1's hash() duplicated posts every run). Each surface posts the clip
in ITS platform's aspect, rendering on demand (FIX F20). The resolved NUMERIC account_id is
stored (FIX F06). decide_tag is invoked (FIX F31). Held/retired clips are skipped (FIX F55)."""
from __future__ import annotations
import hashlib, random
from datetime import datetime, timedelta, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.accounts import Accounts
from fanops.models import Post, PostState, ClipState, MomentState, Platform, Fmt, PLATFORM_ASPECT
from fanops.ids import child_id, surface_key
from fanops.clip import render_moment
from fanops.tagging import decide_tag, ARTIST_HANDLE

def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

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
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

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
        for i, surf in enumerate(surfaces):
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
            led.add_post(Post(
                id=pid, parent_id=target_clip.id, state=PostState.queued,
                account=surf.account, account_id=surf.account_id, platform=surf.platform,
                caption=caption, hashtags=cap.get("hashtags", []), aspect=aspect,
                scheduled_time=sched))
        led.set_clip_state(clip.id, ClipState.queued)
    return led
