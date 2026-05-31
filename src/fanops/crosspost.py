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

def _seed(account: str, platform: str, date_str: str) -> int:
    # SHA1, NOT builtin hash() (FIX F00) — deterministic across processes.
    h = hashlib.sha1(f"{account}|{platform}|{date_str}".encode()).hexdigest()
    return int(h[:8], 16)

def surface_time(base: datetime, account: str, platform: str, date_str: str, index: int) -> str:
    seed = _seed(account, platform, date_str)
    rng = random.Random(seed + index * 7919)         # stable seed; index spreads deterministically
    anchor = base + timedelta(minutes=seed % 50)
    t = anchor + timedelta(minutes=index * rng.randint(35, 95) + rng.randint(0, 7))
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _clip_for_aspect(led: Ledger, cfg: Config, moment_id: str, aspect: Fmt):
    for c in led.clips_of(moment_id):
        if c.aspect is aspect and c.state not in (ClipState.retired,):
            return c
    led2, clip = render_moment(led, cfg, moment_id, aspect=aspect)
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
            skey = surface_key(surf.account, surf.platform.value)
            pid = child_id("post", target_clip.id, skey)        # stable, content-addressed
            cap = clip.meta_captions.get(f"{surf.account}/{surf.platform.value}")
            if cap is None:
                continue                                         # no caption for this surface; skip (held earlier)
            caption = cap["caption"]
            # subtle, non-synchronized artist tag on its own line (FIX F31)
            sched = surface_time(base, surf.account, surf.platform.value, date_str, i)
            if decide_tag(led, account=surf.account, clip_id=clip.id, when=_parse(sched)):
                caption = f"{caption}\n{ARTIST_HANDLE}"
            led.add_post(Post(
                id=pid, parent_id=target_clip.id, state=PostState.queued,
                account=surf.account, account_id=surf.account_id, platform=surf.platform,
                caption=caption, hashtags=cap.get("hashtags", []), aspect=aspect,
                scheduled_time=sched))
        led.set_clip_state(clip.id, ClipState.queued)
    return led
