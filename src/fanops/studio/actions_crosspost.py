"""Studio crosspost and repost mutations (no Flask)."""
from __future__ import annotations
import os
from datetime import datetime
from typing import Optional

from fanops.config import Config
from fanops.errors import fail_open
from fanops.ledger import Ledger
from fanops.models import Platform, Post, PostState
from fanops.ids import child_id, surface_key, _hash
from fanops.timeutil import iso_z
from fanops.log import get_logger
from fanops.studio.actions_common import ActionResult, _now


def _warm_target_aspect(cfg: Config, moment_id: str, aspect) -> None:
    # #4 lock-free pre-render (mirror pipeline._prewarm): _clip_for_aspect on a THROWAWAY Ledger.load snapshot
    # reuses an existing render OR runs render_moment, which writes cid.mp4 + its fingerprint sidecar with NO
    # flock held. The in-transaction _clip_for_aspect below then hits the fingerprint-skip and mints
    # microseconds-fast instead of running ffmpeg (600s-bound) UNDER the lock — N bulk clips no longer
    # serialize N renders behind the write lock. FAIL-OPEN: any error here just means the in-lock path renders
    # as today (never a crash); the snapshot state is discarded — only the on-disk mp4+fp persist, and the
    # transaction re-resolves authoritatively.
    from fanops.crosspost import _clip_for_aspect
    with fail_open("studio.actions._warm_target_aspect"):
        _clip_for_aspect(Ledger.load(cfg), cfg, moment_id, aspect)


def repost_post(cfg: Config, post_id: str) -> ActionResult:
    """'Post again' (post-approval-lifecycle): spawn a NEW awaiting_approval post from the SAME clip+surface
    as a shipped post, re-entering the approval gate. The source post stays immutable history. Honors
    fan-accounts-repost-freely — reposting is allowed; this is NOT a supersede. The new id is content-
    addressed with a repost epoch (count of existing posts for this clip+surface) so it never collides with
    the original or a prior repost, and `add_post`'s setdefault therefore does not silently drop it. The
    operator schedules it on approval (scheduled_time=None). One transaction, never a 500."""
    try:
        with Ledger.transaction(cfg) as led:
            src = led.posts.get(post_id)
            if src is None: return ActionResult(ok=False, error=f"no such post: {post_id}")
            skey = surface_key(src.account, src.platform.value)
            epoch = sum(1 for p in led.posts.values()                       # originals + prior reposts for this surface
                        if p.parent_id == src.parent_id and p.account == src.account and p.platform is src.platform)
            new_id = child_id("post", src.parent_id, f"{skey}#r{epoch}")
            # DECLARE post_type (Postiz "post" for IG); never copy src — a legacy row may carry Meta vocab.
            # TikTok: None — Zernio OpenAPI createPost has no post-type enum.
            led.add_post(Post(id=new_id, parent_id=src.parent_id, state=PostState.awaiting_approval,
                              account=src.account, account_id=src.account_id, platform=src.platform,
                              caption=src.caption, hashtags=list(src.hashtags or []), aspect=src.aspect,
                              media_urls=list(src.media_urls or []), scheduled_time=None,
                              created_at=iso_z(_now(None)),   # content-lifecycle: fresh birth day (aware)
                              post_type=("post" if src.platform is Platform.instagram else None),
                              submission_id=f"fanops_{_hash('idemp', new_id)}",
                              first_frame_kind=src.first_frame_kind,
                              cut_seconds=src.cut_seconds, clip_profile=src.clip_profile,
                              batch_id=src.batch_id,
                              variation_axis=src.variation_axis))
    except Exception as exc:
        get_logger(cfg)("repost", post_id, "repost_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"repost failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"post_id": new_id, "source_id": post_id, "batch_id": src.batch_id, "account": src.account})


def repost_to_other_accounts(cfg: Config, post_id: str, *, target_accounts: Optional[list] = None,
                             all_others: bool = False, now: Optional[datetime] = None) -> ActionResult:
    """U8 'Repost anywhere': re-post an already-shipped clip onto ONE or ALL OTHER accounts. A THIN composer
    over crosspost_to_account (NOT a fourth Post() mint site): resolve the source post's clip + platform +
    account, then fan out one crosspost_to_account per target on the SAME platform as the shipped row. Each
    minted post is born awaiting_approval (approval gate intact, scheduled_time=None — no auto-schedule).
    Targets: all_others=True -> every OTHER active handle; else the picked (deduped) target_accounts minus the
    source account. Per-target honesty mirrors crosspost_all_to_account: ok=True when ANY target minted OR
    already_exists, ok=False only when EVERY target skipped. Same-account reuse is NOT this path — that's
    repost_post (epoch id, fan-accounts-repost-freely); crosspost_to_account on the same (clip,surface) would
    just report already_exists. Never a 500 (crosspost_to_account fail-opens per target)."""
    from fanops.accounts import Accounts
    now = _now(now)
    src = Ledger.load(cfg).posts.get(post_id)                    # read-only resolve (the mint verb owns the transaction)
    if src is None: return ActionResult(ok=False, error=f"no such post: {post_id}")
    clip_id = src.parent_id; platform = src.platform.value; source_account = src.account
    if all_others:
        # Accounts.load unguarded — mirrors the sibling crosspost_all_to_account (Ledger.load) and the
        # _posted_panel that renders this result (Accounts.load(cfg).active()); a broken accounts.json 500s
        # the whole Posted tab regardless, so a redundant local catch here would only add a silent swallow.
        targets = [a.handle for a in Accounts.load(cfg).active() if a.handle != source_account]
    else:
        seen: dict = {}                                          # dedup preserving order; drop the source account
        for h in (target_accounts or []):
            if h and h != source_account and h not in seen: seen[h] = True
        targets = list(seen)
    if not targets: return ActionResult(ok=False, error="pick at least one other account to repost to")
    lines: list = []
    for handle in targets:
        r = crosspost_to_account(cfg, clip_id, handle, platform, now=now)
        surface = (r.detail or {}).get("surface") if r.ok else f"{handle}/{platform}"
        if not r.ok:
            lines.append({"surface": surface, "status": "skipped", "post_id": None, "error": r.error})
        elif r.detail and r.detail.get("already_exists"):
            lines.append({"surface": surface, "status": "already_exists", "post_id": r.detail.get("post_id"), "error": None})
        else:
            lines.append({"surface": surface, "status": "minted", "post_id": (r.detail or {}).get("post_id"), "error": None})
    minted = [ln for ln in lines if ln["status"] == "minted"]
    existed = [ln for ln in lines if ln["status"] == "already_exists"]
    review_account = (minted[0]["surface"].split("/")[0] if minted else targets[0])   # first mint (else first pick) for the Review link
    detail = {"outcome": "repost_anywhere", "lines": lines, "batch_id": src.batch_id, "review_account": review_account}
    if not minted and not existed:                              # every target skipped -> honest failure (mirror crosspost_all_to_account)
        return ActionResult(ok=False, error=f"nothing reposted ({len(lines)} skipped) — no matching surface / held / retired", detail=detail)
    return ActionResult(ok=True, detail=detail)


def crosspost_to_account(cfg: Config, clip_id: str, target_account: str, platform: str, *,
                         now: Optional[datetime] = None) -> ActionResult:
    """Cross-account reuse (content-lifecycle Phase 4): mint a fresh awaiting_approval post of an EXISTING clip
    on a NEW (target_account, platform) surface — how a later-onboarded account gets posts for clips that
    already left ClipState.captioned. Honors fan-accounts-repost-freely: NO supersede/dedup beyond the per-
    (clip,surface) content-addressed setdefault; NO one-version-per-moment guard. Does NOT reset clip state and
    does NOT re-run moments. Aspect-correct (renders/reuses the target aspect via _clip_for_aspect) and
    duration-capped (PLATFORM_MAX_SECONDS, mirroring crosspost_clips). Caption: the clip's per-surface caption
    if present, else an EMPTY caption + empty hashtags (the operator edits in Review before approving — a
    deliberate softening of the seed-tag fallback, which lives upstream in the caption pipeline, not at mint).
    created_at is wall-clock birth (NOT part of the pid). Enters the standard approval gate, scheduled_time=None.
    One transaction, never a 500."""
    from fanops.accounts import Accounts
    from fanops.models import PLATFORM_ASPECT, Fmt
    from fanops.crosspost import _clip_for_aspect
    now = _now(now)
    try: plat = Platform(platform)
    except ValueError: return ActionResult(ok=False, error=f"unknown platform: {platform!r}")
    try: accts = Accounts.load(cfg)
    except Exception as exc:
        get_logger(cfg)("crosspost", target_account, "accounts_load_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"accounts.json: {str(exc)[:160]}")
    surf = next((s for s in accts.surfaces() if s.account == target_account and s.platform is plat), None)
    if surf is None:
        return ActionResult(ok=False, error=f"no active surface {target_account}/{platform} — onboard it in Go Live first")
    skey = surface_key(surf.account, surf.platform.value)
    aspect = PLATFORM_ASPECT.get(plat, Fmt.r9x16)
    pre = Ledger.load(cfg).clips.get(clip_id)                                  # #4: lock-free read of the moment id...
    if pre is not None: _warm_target_aspect(cfg, pre.parent_id, aspect)        # ...so the target aspect renders OUTSIDE the flock
    try:
        with Ledger.transaction(cfg) as led:
            clip = led.clips.get(clip_id)
            if clip is None: return ActionResult(ok=False, error=f"no such clip: {clip_id}")
            if not led.can_seed(clip):   # the OWNER folds held + retired lineage, and fails CLOSED on a missing moment
                return ActionResult(ok=False, error=f"clip {clip_id} is held/retired — not eligible for cross-post")
            m = led.moments.get(clip.parent_id)
            source = led.sources.get(m.parent_id) if m is not None else None
            src_batch = source.batch_id if source is not None else None   # AUDIT M2: inherit the clip's ingest-batch
            # lineage (like repost_post) so the reuse post groups + approves with its batched siblings — a None-batch
            # post showed in the ?batch= drill-in (card derives bid from a sibling) but approve_account silently skipped it.
            from fanops.studio.actions_approve import _clip_over_cap
            over = _clip_over_cap(cfg, led, clip, plat)
            if over is not None:
                clip_dur, max_secs = over
                return ActionResult(ok=False, error=f"clip duration {clip_dur:.0f}s exceeds {platform} cap {max_secs}s")
            target_clip = _clip_for_aspect(led, cfg, clip.parent_id, aspect)   # the RIGHT-aspect render (H7); warm -> fingerprint-skip
            pid = child_id("post", target_clip.id, skey)
            if pid in led.posts:                                               # honest report (H9)
                return ActionResult(ok=True, detail={"post_id": pid, "clip_id": clip_id, "already_exists": True,
                                                     "surface": f"{surf.account}/{surf.platform.value}"})
            if not (target_clip.path and os.path.exists(target_clip.path)):    # #10: a gc-swept render -> refuse at mint,
                return ActionResult(ok=False, error=f"clip {clip_id} render missing on disk — re-run the clip before cross-posting")  # not silently at publish
            cap = clip.meta_captions.get(f"{surf.account}/{surf.platform.value}")
            caption = cap["caption"] if isinstance(cap, dict) and cap.get("caption") else ""
            hashtags = list(cap.get("hashtags", [])) if isinstance(cap, dict) else []
            # DECLARE post_type (Postiz "post" for IG); never copy a source post's field.
            # TikTok: None — Zernio OpenAPI createPost has no post-type enum.
            led.add_post(Post(id=pid, parent_id=target_clip.id, state=PostState.awaiting_approval,
                              account=surf.account, account_id=surf.account_id, platform=surf.platform,
                              caption=caption, hashtags=hashtags, aspect=aspect, scheduled_time=None,
                              created_at=iso_z(now), submission_id=f"fanops_{_hash('idemp', pid)}",
                              post_type=("post" if surf.platform is Platform.instagram else None),
                              clip_profile=cfg.clip_profile, batch_id=src_batch))
    except Exception as exc:
        get_logger(cfg)("crosspost", clip_id, "crosspost_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"cross-post failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"post_id": pid, "clip_id": clip_id, "already_exists": False,
                                         "surface": f"{surf.account}/{surf.platform.value}"})


def crosspost_all_to_account(cfg: Config, source_account: str, target_account: str, platform: str, *,
                             now: Optional[datetime] = None) -> ActionResult:
    """Bulk cross-account backfill (content-lifecycle Phase 4): mint an awaiting_approval post on
    (target_account, platform) for EVERY clip already posted to source_account. Each enters the approval gate.
    Honors repost-freely (per-(clip,surface) setdefault is the only dedup, so a re-run is a clean no-op).
    clip_ids is a SET — a multi-platform source_account yields one source post per platform per clip, the set
    collapses them to ONE crosspost_to_account call per clip (correct: fan out once per clip). Reports
    minted / already_exists / skipped honestly. LATENCY (ECC review): a FIRST fan-out to an aspect that has
    no existing render makes each clip pay an ffmpeg render (600s-bound) under its own short lock — N clips
    serialize N renders. Not a deadlock (per-clip lock, released between clips) and mirrors crosspost_clips;
    the common same-aspect reuse returns instantly. Operator-gated, single-operator Studio."""
    if source_account == target_account:                 # bulk backfill is CROSS-account; same->same is a no-op
        return ActionResult(ok=False, error=f"source and target are the same account ({source_account}) — pick a different target")
    led = Ledger.load(cfg)
    clip_ids = sorted({p.parent_id for p in led.posts.values() if p.account == source_account})
    if not clip_ids:
        return ActionResult(ok=False, error=f"no clips posted to {source_account} — nothing to backfill")
    minted, existed, skipped = [], [], []
    for cid in clip_ids:
        r = crosspost_to_account(cfg, cid, target_account, platform, now=now)
        if not r.ok: skipped.append(cid)
        elif r.detail and r.detail.get("already_exists"): existed.append(cid)
        else: minted.append(cid)
    if not minted and not existed:
        return ActionResult(ok=False, error=f"nothing minted ({len(skipped)} skipped) — held/retired or bad surface",
                            detail={"minted": 0, "already_exists": 0, "skipped": len(skipped)})
    return ActionResult(ok=True, detail={"minted": len(minted), "already_exists": len(existed),
                                         "skipped": len(skipped), "target": f"{target_account}/{platform}"})
