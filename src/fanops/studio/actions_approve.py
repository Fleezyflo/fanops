"""Studio approval mutations (no Flask): the human gate that promotes awaiting_approval posts to queued."""
from __future__ import annotations
from datetime import datetime
from typing import Callable, Optional, Sequence

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ClipState, PostState, PLATFORM_MAX_SECONDS, validate_account_handle
from fanops.audit import write_audit
from fanops.log import get_logger
from fanops.errors import fail_open
from fanops.timeutil import iso_z
from fanops.studio.views import suggest_time
from fanops.studio.views_common import suggest_times_for_batch
from fanops.studio.actions_common import ActionResult, _now, _inherit_captions, _normalize_ids


def _apply_schedule_for_account(led: Ledger, cfg: Config, handle: str, *, now: datetime) -> int:
    """Re-spread every queued post on one account (post-approve respread + accept-suggested). Returns moved count."""
    posts = [p for p in led.posts.values() if p.state is PostState.queued and p.account == handle]
    moved = 0
    for pid, t in suggest_times_for_batch(cfg, posts, now=now).items():
        p = led.posts[pid]
        if p.scheduled_time != t:
            p.scheduled_time = t
            moved += 1
    return moved


def _clip_over_cap(cfg: Config, led: Ledger, clip, platform) -> Optional[tuple[float, int]]:
    """Return (duration, cap) when a clip exceeds its platform ceiling, else None."""
    cap = PLATFORM_MAX_SECONDS.get(platform)
    if cap is None:
        return None
    from fanops.clip import realized_clip_seconds
    m = led.moments.get(clip.parent_id)
    dur = realized_clip_seconds(clip, m)
    if dur is None or dur <= 0 or dur <= cap:
        return None
    return dur, cap


def _over_cap_refusal(cfg: Config, led: Ledger, post) -> Optional[str]:
    """THE platform-duration gate on the approve side — sole owner of the `cut_over_cap` refusal, asked by
    every approve route. Returns the operator reason (stamped on the post and logged) when the post's
    realized cut exceeds its platform ceiling, else None. `approve_with_hook` used to carry no cap check at
    all, so the same post on the same ledger was admissible or not purely by which button the operator
    pressed (MOL-832); a second hand-written copy of the predicate is how that gap opened."""
    clip = led.clips.get(post.parent_id)
    if clip is None:
        return None
    over = _clip_over_cap(cfg, led, clip, post.platform)
    if over is None:
        return None
    dur, cap = over
    post.error_reason = reason = f"realized cut {round(dur, 1)}s exceeds {post.platform.value} cap {cap}s"
    get_logger(cfg)("approve", post.id, "cut_over_cap", realized=round(dur, 1), cap=cap)
    return reason

def _approve_ids_with_render(cfg: Config, *, resolve_ids: Callable[[Ledger], Sequence[str]],
                             now: Optional[datetime], detail: dict) -> ActionResult:
    """P9: promote awaiting->queued. Owner-moment clip is already rendered — no re-cut at approval."""
    now = _now(now); now_iso = iso_z(now)
    approved = 0
    skipped_retired = 0; cut_over_cap = 0
    approved_ids: list[str] = []
    try:
        with Ledger.transaction(cfg) as led:
            ids_in_batch = list(resolve_ids(led))
            batch_posts = [led.posts[i] for i in ids_in_batch if i in led.posts]
            sched = suggest_times_for_batch(cfg, batch_posts, now=now)
            for pid in ids_in_batch:
                post = led.posts.get(pid)
                if post is None:
                    continue
                # A RETIRED lineage is never approvable. crosspost/crosspost_to_account already refuse to MINT
                # onto one; this is the missing APPROVE-side twin — these posts were minted BEFORE their parent
                # was retired (re-decision cascade preserves awaiting_approval posts and retires the MOMENT),
                # so they survive with a live clip under a dead moment. Promoting one to `queued` publishes
                # lineage the system already dropped. Asked of `Ledger.can_promote`, the OWNER — which closes the
                # old `clip is not None` fail-open (a post whose clip row is GONE was approvable) because
                # is_suppressed fails CLOSED. Counted in `skipped_retired` — never silently swallowed.
                if not led.can_promote(post):
                    skipped_retired += 1
                    get_logger(cfg)("approve", pid, "skipped_retired_lineage", account=post.account)
                    continue
                if _over_cap_refusal(cfg, led, post) is not None:
                    cut_over_cap += 1   # counted + rendered by `_publish_outcome.html`: this `continue` used to leave NO trace, so a tick of N came back "Approved N-1". The cap's value/policy/trigger are unchanged — only its silence is.
                    continue
                sugg = sched.get(pid) or suggest_time(cfg, post, now=now)
                led.approve_post(pid, now_iso=now_iso, suggested_iso=sugg)
                approved += 1
                approved_ids.append(pid)
            # MOL-869: approve_post keeps still-future mint times, so identical futures lockstep.
            # Re-spread each approved account's full queued set (no occupied — same as
            # accept_suggested_account) inside this open transaction.
            approved_accts = {led.posts[i].account for i in approved_ids if i in led.posts}
            for handle in sorted(approved_accts):
                _apply_schedule_for_account(led, cfg, handle, now=now)
            audited_ids = [i for i in approved_ids if i in led.posts]   # audit what PROMOTED, not what was offered
    except Exception as exc:
        get_logger(cfg)("approve", "-", "approve_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"approve failed: {str(exc)[:160]}")
    if approved and audited_ids:
        write_audit(cfg, "approve", audited_ids, reason="studio_approve_batch", approved=approved, now=now_iso)
    sched_detail: dict = {}
    if approved and audited_ids:
        sched_detail = {"outcome": "approved_scheduled"}
        with fail_open("studio.actions_approve._approve_ids_with_render.sched_detail"):
            led2 = Ledger.load(cfg)
            times = sorted(t for i in audited_ids if (p := led2.posts.get(i)) and p.scheduled_time for t in [led2.posts[i].scheduled_time])
            accts = list({led2.posts[i].account for i in audited_ids if i in led2.posts})
            sched_detail = {"outcome": "approved_scheduled", "next_time": times[0] if times else None,
                            "last_time": times[-1] if times else None,
                            "schedule_account": accts[0] if len(accts) == 1 else None}
    return ActionResult(ok=True, detail={**detail, "approved": approved, "render_pending": 0,
                                         "skipped_retired": skipped_retired, "cut_over_cap": cut_over_cap, **sched_detail})

BULK_APPROVE_CONFIRM_AT = 15

def _stitch_ids_action(cfg: Config, ids: Sequence[str], *, verb: str, ledger_fn: Callable[[Ledger, str], None],
                       detail_key: str) -> ActionResult:
    sel = _normalize_ids(ids)
    try:
        with Ledger.transaction(cfg) as led:
            for pid in sel:
                ledger_fn(led, pid)
    except Exception as exc:
        get_logger(cfg)(verb, "-", f"{verb}_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"{verb} failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={detail_key: len(sel)})


def approve_posts(cfg: Config, ids: Sequence[str], *, now: Optional[datetime] = None, confirmed: bool = False) -> ActionResult:
    sel = _normalize_ids(ids)
    if len(sel) > BULK_APPROVE_CONFIRM_AT and not confirmed:
        return ActionResult(ok=False, error=(f"Approving {len(sel)} posts queues them for the daemon — "
                            "approved ≠ live. Tick batch confirm, then approve again."))
    return _approve_ids_with_render(cfg, resolve_ids=lambda led: sel, now=now, detail={})

def reject_posts(cfg: Config, ids: Sequence[str]) -> ActionResult:
    sel = _normalize_ids(ids)
    audited_ids: list[str] = []
    try:
        with Ledger.transaction(cfg) as led:
            # Audit what DISCARDED, not what was offered (same rule as the approve side): reject_post silently
            # no-ops on a missing or already-decided post, so the awaiting_approval PRE-state is the only truth
            # about which ids this call moved. Without this line a rejection leaves no trace at all, and "the
            # operator never rejected" is indistinguishable from "the rejection was re-minted away".
            audited_ids = [i for i in sel if (p := led.posts.get(i)) is not None
                           and p.state is PostState.awaiting_approval]
            for pid in sel: led.reject_post(pid)
    except Exception as exc:
        get_logger(cfg)("reject", "-", "reject_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"reject failed: {str(exc)[:160]}")
    if audited_ids:
        write_audit(cfg, "reject", audited_ids, reason="studio_reject_batch", rejected=len(audited_ids))
    # MOL-834: banner count = discarded set (audited_ids), not offered selection (sel).
    return ActionResult(ok=True, detail={"rejected": len(audited_ids)})

def unapprove_post(cfg: Config, post_id: str) -> ActionResult:
    try:
        with Ledger.transaction(cfg) as led:
            if post_id not in led.posts: return ActionResult(ok=False, error=f"no such post: {post_id}")
            led.unapprove_post(post_id)
    except Exception as exc:
        get_logger(cfg)("unapprove", post_id, "unapprove_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"unapprove failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"post_id": post_id})

def _warm_hooked_render(cfg: Config, moment_id: str, aspect, hook: str) -> bool:
    from fanops.clip import render_moment
    try:
        snap = Ledger.load(cfg)
        mom = snap.moments.get(moment_id)
        if mom is None: return True
        snap.moments[moment_id] = mom.model_copy(update={"hook": hook, "hook_removed": None})
        render_moment(snap, cfg, moment_id, aspect=aspect)
        return True
    except Exception as e:
        get_logger(cfg)("approve_with_hook", moment_id, "warm_failed", err=str(e)[:120])
        return False

def approve_with_hook(cfg: Config, clip_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    from fanops.clip import render_moment
    now = _now(now); now_iso = iso_z(now)
    snap = Ledger.load(cfg)
    c0 = snap.clips.get(clip_id)
    if c0 is None: return ActionResult(ok=False, error=f"no such clip: {clip_id}")
    m0 = snap.moments.get(c0.parent_id)
    removed = (m0.hook_removed if m0 is not None else None)
    if removed and not _warm_hooked_render(cfg, c0.parent_id, c0.aspect, removed):
        return ActionResult(ok=False, error="couldn't pre-render the hooked clip off the lock — retry approve")
    approved = 0; cut_over_cap = 0
    try:
        with Ledger.transaction(cfg) as led:
            clip = led.clips.get(clip_id)
            if clip is None: return ActionResult(ok=False, error=f"no such clip: {clip_id}")
            # Same guard as the bulk engine, asked of the OWNER. NOT `can_seed`: this route has never refused a
            # HELD clip and must not start — `is_suppressed` reads lineage only.
            if led.is_suppressed(clip):
                return ActionResult(ok=False, error=f"clip {clip_id} is retired — not eligible for approval")  # loud here (one clip)
            # T2.5: the owned worklist, filtered to this clip. Identical set to the old hand-rolled scan — the
            # `is_suppressed(clip)` guard three lines up already proved this lineage live, so `can_promote`
            # admits every awaiting post under it — but the predicate now has ONE author.
            ids = [p.id for p in led.review_posts() if p.parent_id == clip_id]
            # The SAME cap the bulk engine enforces, from the SAME owner — a per-POST verdict, because one
            # clip's surfaces can straddle two platform ceilings. Asked BEFORE the restore so a fully-dropped
            # clip neither re-cuts nor spends its `hook_removed`, and counted so the refusal reaches the operator.
            admitted = [pid for pid in ids if _over_cap_refusal(cfg, led, led.posts[pid]) is None]
            cut_over_cap = len(ids) - len(admitted)
            mom = led.moments.get(clip.parent_id)
            restored = (mom.hook_removed if mom is not None else None)
            if admitted and restored:
                led.moments[clip.parent_id] = mom.model_copy(update={"hook": restored, "hook_removed": None})
                orig = led.clips[clip_id]
                led, rc = render_moment(led, cfg, clip.parent_id, aspect=clip.aspect)
                if rc.state is ClipState.error:
                    raise RuntimeError(rc.error_reason or "clip re-render failed")
                if rc.hook_burn_failed:
                    raise RuntimeError("hook burn failed — not shipping clean")
                led.clips[clip_id] = led.clips[clip_id].model_copy(
                    update={"meta_captions": _inherit_captions(orig.meta_captions)})
                led.set_clip_state(clip_id, orig.state)
            batch_posts = [led.posts[pid] for pid in admitted if pid in led.posts]
            sched = suggest_times_for_batch(cfg, batch_posts, now=now)
            for pid in admitted:
                sugg = sched.get(pid)
                led.approve_post(pid, now_iso=now_iso, suggested_iso=sugg)
                approved += 1
    except Exception as exc:
        get_logger(cfg)("approve_with_hook", clip_id, "approve_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"approve-with-hook failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"approved": approved, "clip_id": clip_id, "hook": bool(removed),
                                         "cut_over_cap": cut_over_cap})

def _approve_matching(cfg: Config, pred=None, *, pred_for=None, now: Optional[datetime] = None,
                      detail: Optional[dict] = None) -> ActionResult:
    def _resolve(led):
        p = pred_for(led) if pred_for is not None else pred
        # T2.5: off the owned state accessor, not a hand-rolled scan of `led.posts`. The worklist's OTHER half
        # (`can_promote` — a live lineage) is deliberately NOT applied here: `_approve_ids_with_render` asks it
        # per post and COUNTS the refusal into `skipped_retired` + a `skipped_retired_lineage` breadcrumb.
        # Pre-filtering to `review_posts()` would resolve to the same promotions and lose that report, so a
        # stale Review page approving a since-retired clip would come back "approved 0" with no reason and no
        # log line. Selection states the operator's intent; the owner states the verdict, out loud.
        return [post.id for post in led.posts_in_state(PostState.awaiting_approval) if p(post)]
    return _approve_ids_with_render(cfg, resolve_ids=_resolve, now=now, detail=detail or {})

def approve_batch(cfg: Config, batch_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    bid = (batch_id or "").strip()
    if not bid:
        return ActionResult(ok=True, detail={"batch": None, "approved": 0})
    return _approve_matching(cfg, lambda p: p.batch_id == bid, now=now, detail={"batch": bid})

def approve_clip(cfg: Config, clip_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    return _approve_matching(cfg, lambda p: p.parent_id == clip_id, now=now, detail={"clip_id": clip_id})

def approve_account(cfg: Config, handle: str, *, batch: Optional[str] = None, source: Optional[str] = None,
                    platform: Optional[str] = None, now: Optional[datetime] = None) -> ActionResult:
    handle = (handle or "").strip()
    if not handle:
        return ActionResult(ok=True, detail={"account": None, "approved": 0})
    try:
        handle = validate_account_handle(handle)
    except ValueError:
        return ActionResult(ok=True, detail={"account": handle, "approved": 0})
    det = {"account": handle, "batch": batch, "source": source, "platform": platform}
    def _chan(p) -> bool: return platform is None or p.platform.value == platform
    if source is None:
        return _approve_matching(cfg, lambda p: p.account == handle and (batch is None or p.batch_id == batch) and _chan(p),
                                 now=now, detail=det)
    def _pred_for(led):
        src_of = {c.id: (m.parent_id if (m := led.moments.get(c.parent_id)) is not None else None)
                  for c in led.clips.values()}
        return lambda p: (p.account == handle and (batch is None or p.batch_id == batch) and _chan(p)
                          and src_of.get(p.parent_id) == source)
    return _approve_matching(cfg, pred_for=_pred_for, now=now, detail=det)

def approve_moment(cfg: Config, moment_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    def _pred_for(led):
        clip_ids = {c.id for c in led.clips.values() if c.parent_id == moment_id}
        return lambda p: p.parent_id in clip_ids
    return _approve_matching(cfg, pred_for=_pred_for, now=now, detail={"moment": moment_id})

def approve_with_edits(cfg: Config, post_id: str, *, caption: str, hook: str,
                       now: Optional[datetime] = None) -> ActionResult:
    """U6: composite approve — persist caption/hook edits when dirty, then promote ONE awaiting post."""
    from fanops.studio.actions_edit import edit_caption, reburn_hook, _guard_editable_post
    now = _now(now)
    led = Ledger.load(cfg)
    p, err = _guard_editable_post(led, post_id, now)
    if err:
        return ActionResult(ok=False, error=err)
    if p.state is not PostState.awaiting_approval:
        return ActionResult(ok=False, error=f"post {post_id} is {p.state.value}; only awaiting posts can be approved")
    clip = led.clips.get(p.parent_id)
    mom = led.moments.get(clip.parent_id) if clip is not None else None
    cur_caption = p.caption or ""
    cur_hook = ((mom.hook if mom is not None else None) or "").strip()
    new_hook = (hook or "").strip()
    if (caption or "") != cur_caption:
        res = edit_caption(cfg, post_id, caption, now=now)
        if not res.ok:
            return res
    if new_hook != cur_hook:
        res = reburn_hook(cfg, post_id, new_hook, now=now)
        if not res.ok:
            return res
    return _approve_ids_with_render(cfg, resolve_ids=lambda led: [post_id], now=now, detail={"post_id": post_id})

def approve_as_is(cfg: Config, clip_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    r = approve_clip(cfg, clip_id, now=now)
    if not r.ok:
        return r
    return ActionResult(ok=True, detail={**r.detail, "hook": False})

def approve_stitches(cfg: Config, ids: Sequence[str]) -> ActionResult:
    return _stitch_ids_action(cfg, ids, verb="approve_stitches", ledger_fn=lambda led, pid: led.approve_stitch_plan(pid),
                              detail_key="approved")

def dismiss_stitches(cfg: Config, ids: Sequence[str]) -> ActionResult:
    return _stitch_ids_action(cfg, ids, verb="dismiss_stitches", ledger_fn=lambda led, pid: led.dismiss_stitch_plan(pid),
                              detail_key="dismissed")

def release_stitches(cfg: Config, ids: Sequence[str]) -> ActionResult:
    sel = _normalize_ids(ids)
    released = 0
    try:
        with Ledger.transaction(cfg) as led:
            for cid in sel:
                c = led.clips.get(cid)
                if c is None or c.state is not ClipState.stitch_draft:
                    continue
                base = _best_caption_sibling(led, c)
                if base is not None:
                    c.meta_captions = _inherit_captions(base.meta_captions)
                led.set_clip_state(cid, ClipState.captioned)
                released += 1
    except Exception as exc:
        get_logger(cfg)("release_stitches", "-", "release_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"release failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"released": released})

def _best_caption_sibling(led, stitch):
    sibs = [c for c in led.clips.values() if c.parent_id == stitch.parent_id and c.aspect is stitch.aspect
            and c.id != stitch.id and c.state is not ClipState.stitch_draft and c.meta_captions]
    if not sibs:
        return None
    sibs.sort(key=lambda c: (c.state is not ClipState.captioned, c.id))
    return sibs[0]
