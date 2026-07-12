"""Studio approval mutations (no Flask): the human gate that promotes awaiting_approval posts to queued."""
from __future__ import annotations
from datetime import datetime
from typing import Callable, Optional, Sequence

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ClipState, PostState, PLATFORM_MAX_SECONDS, validate_account_handle
from fanops.audit import write_audit
from fanops.log import get_logger
from fanops.timeutil import iso_z
from fanops.studio.views import suggest_time
from fanops.studio.views_common import suggest_times_for_batch
from fanops.studio.actions_common import ActionResult, _now, _inherit_captions


def _approve_ids_with_render(cfg: Config, *, resolve_ids: Callable[[Ledger], Sequence[str]],
                             now: Optional[datetime], detail: dict) -> ActionResult:
    """P9: promote awaiting->queued. Owner-moment clip is already rendered — no re-cut at approval."""
    now = _now(now); now_iso = iso_z(now)
    approved = 0
    try:
        with Ledger.transaction(cfg) as led:
            ids_in_batch = list(resolve_ids(led))
            batch_posts = [led.posts[i] for i in ids_in_batch if i in led.posts]
            sched = suggest_times_for_batch(cfg, batch_posts, now=now)
            for pid in ids_in_batch:
                post = led.posts.get(pid)
                if post is None:
                    continue
                clip = led.clips.get(post.parent_id)
                if clip is not None:
                    cap = PLATFORM_MAX_SECONDS.get(post.platform)
                    from fanops.clip import realized_clip_seconds
                    m = led.moments.get(clip.parent_id)
                    clip_dur = realized_clip_seconds(clip, m)
                    if cap is not None and clip_dur is not None and clip_dur > 0 and clip_dur > cap:
                        post.error_reason = f"realized cut {round(clip_dur, 1)}s exceeds {post.platform.value} cap {cap}s"
                        get_logger(cfg)("approve", pid, "cut_over_cap", realized=round(clip_dur, 1), cap=cap)
                        continue
                sugg = sched.get(pid) or suggest_time(cfg, post, now=now)
                led.approve_post(pid, now_iso=now_iso, suggested_iso=sugg)
                approved += 1
            audited_ids = [i for i in ids_in_batch if i in led.posts]
    except Exception as exc:
        return ActionResult(ok=False, error=f"approve failed: {str(exc)[:160]}")
    if approved and audited_ids:
        write_audit(cfg, "approve", audited_ids, reason="studio_approve_batch", approved=approved, now=now_iso)
    sched_detail: dict = {}
    if approved and audited_ids:
        try:
            led2 = Ledger.load(cfg)
            times = sorted(t for i in audited_ids if (p := led2.posts.get(i)) and p.scheduled_time for t in [led2.posts[i].scheduled_time])
            accts = list({led2.posts[i].account for i in audited_ids if i in led2.posts})
            sched_detail = {"outcome": "approved_scheduled", "next_time": times[0] if times else None,
                            "last_time": times[-1] if times else None,
                            "schedule_account": accts[0] if len(accts) == 1 else None}
        except Exception:
            sched_detail = {"outcome": "approved_scheduled"}
    return ActionResult(ok=True, detail={**detail, "approved": approved, "render_pending": 0, **sched_detail})

BULK_APPROVE_CONFIRM_AT = 15

def approve_posts(cfg: Config, ids: Sequence[str], *, now: Optional[datetime] = None, confirmed: bool = False) -> ActionResult:
    sel = [i for i in (ids or []) if i]
    if len(sel) > BULK_APPROVE_CONFIRM_AT and not confirmed:
        return ActionResult(ok=False, error=(f"Approving {len(sel)} posts queues them for the daemon — "
                            "approved ≠ live. Tick batch confirm, then approve again."))
    return _approve_ids_with_render(cfg, resolve_ids=lambda led: sel, now=now, detail={})

def reject_posts(cfg: Config, ids: Sequence[str]) -> ActionResult:
    sel = [i for i in (ids or []) if i]
    try:
        with Ledger.transaction(cfg) as led:
            for pid in sel: led.reject_post(pid)
    except Exception as exc:
        return ActionResult(ok=False, error=f"reject failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"rejected": len(sel)})

def unapprove_post(cfg: Config, post_id: str) -> ActionResult:
    try:
        with Ledger.transaction(cfg) as led:
            if post_id not in led.posts: return ActionResult(ok=False, error=f"no such post: {post_id}")
            led.unapprove_post(post_id)
    except Exception as exc:
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
    approved = 0
    try:
        with Ledger.transaction(cfg) as led:
            clip = led.clips.get(clip_id)
            if clip is None: return ActionResult(ok=False, error=f"no such clip: {clip_id}")
            ids = [p.id for p in led.posts.values()
                   if p.parent_id == clip_id and p.state is PostState.awaiting_approval]
            mom = led.moments.get(clip.parent_id)
            restored = (mom.hook_removed if mom is not None else None)
            if ids and restored:
                led.moments[clip.parent_id] = mom.model_copy(update={"hook": restored, "hook_removed": None})
                orig = led.clips[clip_id]
                led, rc = render_moment(led, cfg, clip.parent_id, aspect=clip.aspect)
                if rc.state is ClipState.error:
                    raise RuntimeError(rc.error_reason or "clip re-render failed")
                if rc.hook_burn_failed:
                    raise RuntimeError("hook burn failed — not shipping clean")
                led.clips[clip_id] = led.clips[clip_id].model_copy(
                    update={"state": orig.state, "meta_captions": _inherit_captions(orig.meta_captions)})
            for pid in ids:
                post = led.posts.get(pid)
                sugg = suggest_time(cfg, post, now=now) if post is not None else None
                led.approve_post(pid, now_iso=now_iso, suggested_iso=sugg)
                approved += 1
    except Exception as exc:
        return ActionResult(ok=False, error=f"approve-with-hook failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"approved": approved, "clip_id": clip_id, "hook": bool(removed)})

def _approve_matching(cfg: Config, pred=None, *, pred_for=None, now: Optional[datetime] = None,
                      detail: Optional[dict] = None) -> ActionResult:
    def _resolve(led):
        p = pred_for(led) if pred_for is not None else pred
        return [post.id for post in led.posts.values() if post.state is PostState.awaiting_approval and p(post)]
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
    from fanops.studio.actions import edit_caption, reburn_hook, _guard_editable_post
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
    sel = [i for i in (ids or []) if i]
    try:
        with Ledger.transaction(cfg) as led:
            for pid in sel: led.approve_stitch_plan(pid)
    except Exception as exc:
        return ActionResult(ok=False, error=f"approve failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"approved": len(sel)})

def dismiss_stitches(cfg: Config, ids: Sequence[str]) -> ActionResult:
    sel = [i for i in (ids or []) if i]
    try:
        with Ledger.transaction(cfg) as led:
            for pid in sel: led.dismiss_stitch_plan(pid)
    except Exception as exc:
        return ActionResult(ok=False, error=f"dismiss failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"dismissed": len(sel)})

def release_stitches(cfg: Config, ids: Sequence[str]) -> ActionResult:
    sel = [i for i in (ids or []) if i]
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
                c.state = ClipState.captioned
                released += 1
    except Exception as exc:
        return ActionResult(ok=False, error=f"release failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"released": released})

def _best_caption_sibling(led, stitch):
    sibs = [c for c in led.clips.values() if c.parent_id == stitch.parent_id and c.aspect is stitch.aspect
            and c.id != stitch.id and c.state is not ClipState.stitch_draft and c.meta_captions]
    if not sibs:
        return None
    sibs.sort(key=lambda c: (c.state is not ClipState.captioned, c.id))
    return sibs[0]
