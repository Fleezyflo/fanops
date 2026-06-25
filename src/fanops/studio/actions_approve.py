"""Studio approval mutations (no Flask): the human gate that promotes awaiting_approval posts to queued
(per-post, per-clip, per-account, per-moment, with/without the restored hook), reject/un-approve, and the
stitch-plan lifecycle. Each runs under one Ledger.transaction. Depends only on actions_common
(ActionResult/_now/_inherit_captions) and views.suggest_time; no approval action calls a sibling action
module, so the graph stays acyclic (actions.py->actions_approve for clear_time's unapprove only)."""
from __future__ import annotations
from datetime import datetime
from typing import Callable, Optional, Sequence

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.accounts import Accounts
from fanops.ids import surface_key
from fanops.models import ClipState, PostState, Render, RenderState
from fanops.crosspost import account_render_spec, render_account_file
from fanops.log import get_logger
from fanops.timeutil import iso_z
from fanops.studio.views import suggest_time
from fanops.studio.actions_common import ActionResult, _now, _inherit_captions


def _acct_for(accts: Accounts, handle: str):
    return next((a for a in accts.accounts if a.handle == handle), None)   # None -> account_render_spec's global defaults

def _warm_renders(cfg: Config, snap: Ledger, ids: Sequence[str], accts: Accounts) -> dict:
    """LOCK-FREE: render each DISTINCT (clip, hook) variant file for the posts `ids` selects to its
    content-addressed path, so the in-lock adopt records the Render WITHOUT running ffmpeg under the flock (the
    60s flock guard / the approve_with_hook precedent). Returns {post_id: RenderPlan}. Deduped: a hook whose
    Render ENTITY already exists is skipped (in-lock reuses it), and identical hooks within the batch render
    once. Per-post fail-open — a render error is LOGGED and just omits the plan; the in-lock adopt then leaves
    the post un-materialized (M1: never ffmpeg under the flock) and the spine skips approving it with a
    render_unavailable_skip_approve breadcrumb, so the NEXT warm pass (a re-click) retries the burn off-lock."""
    plans, by_rid = {}, {}
    for pid in ids:
        post = snap.posts.get(pid)
        if post is None or not post.variant_hook: continue
        clip = snap.clips.get(post.parent_id)
        if clip is None: continue
        try:
            acct = _acct_for(accts, post.account)
            rid, *_ = account_render_spec(cfg, clip=clip, hook=post.variant_hook, acct=acct)
            if snap.get_render(rid) is not None: continue            # entity already exists -> in-lock reuses it
            if rid in by_rid: plans[pid] = by_rid[rid]; continue      # identical hook already warmed this batch
            mom = snap.moments.get(clip.parent_id)
            src = snap.sources.get(mom.parent_id) if mom is not None else None
            plan = render_account_file(snap, cfg, post=post, acct=acct, target_clip=clip, src=src)
            by_rid[rid] = plan; plans[pid] = plan
        except Exception as e:                                       # fail-open + M1: in-lock leaves it un-materialized,
            get_logger(cfg)("approve", pid, "warm_render_failed", err=str(e)[:120]); continue   # spine skips, next pass retries
    return plans

def _adopt_render(led: Ledger, cfg: Config, post, plan, accts: Accounts) -> None:
    """IN-LOCK: ensure the per-account Render exists (adopt the warmed file, or render in-lock as a fallback)
    and point `post` at its burned file + stamp the realized cut profile — BEFORE the post is promoted to
    queued (publish-needs-media: a variant post is NEVER queued without its render). Reuses an existing
    content-addressed Render (dedup / anti-explosion). A clean no-op when the post carries no variant_hook
    (the OFF / hookless firewall holds at approval too)."""
    if not post.variant_hook: return
    clip = led.clips.get(post.parent_id)
    if clip is None: return
    acct = _acct_for(accts, post.account)
    # rid is recomputed in-lock from the CURRENT variant_hook: if the hook changed between the warm snapshot
    # and here, this rid won't match the warmed plan -> the post is left UN-materialized (M1: ffmpeg never runs
    # under the flock), the spine skips it with a render_unavailable_skip_approve breadcrumb, and the next warm
    # pass (a re-click) burns the correct file off the lock.
    rid, _wants, profile, _top = account_render_spec(cfg, clip=clip, hook=post.variant_hook, acct=acct)
    if led.get_render(rid) is None:
        if plan is None or plan.render_id != rid:                    # no usable warm (fail-open / race / hook changed)
            return                                                   # M1: do NOT burn under the flock — leave it for the next warm pass
        led.add_render(Render(id=plan.render_id, clip_id=clip.id, account=post.account,
                              surface_key=surface_key(post.account, post.platform.value),
                              hook_text=post.variant_hook, path=plan.vpath, state=RenderState.rendered,
                              batch_id=plan.batch_id, source_id=plan.source_id, is_account_cut=plan.produced,
                              hook_source=plan.hook_source, cut_seconds=plan.realized))   # first-write-wins (race-safe)
    r = led.get_render(rid)                                          # authoritative (a racing writer may have added it)
    post.render_id = rid
    post.media_urls = [f"file://{r.path}"]
    if r.is_account_cut:                                             # a real cut stamps ITS OWN length profile (P4 dim)
        post.clip_profile = profile

def _approve_ids_with_render(cfg: Config, *, resolve_ids: Callable[[Ledger], Sequence[str]],
                             now: Optional[datetime], detail: dict) -> ActionResult:
    """The shared approve spine (slice 2: burn on approval). Warm the per-account renders OUTSIDE the flock for
    the posts `resolve_ids` selects, then in ONE transaction adopt each render (mint the Render + point the
    post at its burned file) and promote awaiting->queued. resolve_ids(led) -> the post-id list, applied to a
    lock-free snapshot to warm, then RE-APPLIED in-lock so a concurrent state change can't approve a
    no-longer-eligible post. One `now` stamp for the batch (consistent stale-schedule bump). Materialize is a
    clean no-op when creative_variation is OFF or a post has no variant_hook (OFF firewall). Never a 500."""
    now = _now(now); now_iso = iso_z(now)
    accts = Accounts.load(cfg)
    snap = Ledger.load(cfg)                                          # lock-free: resolve + pre-warm the renders off the flock
    plans = _warm_renders(cfg, snap, resolve_ids(snap), accts) if cfg.creative_variation else {}
    approved = 0
    try:
        with Ledger.transaction(cfg) as led:
            for pid in list(resolve_ids(led)):                       # P1: untimed/stale post -> a strictly-future suggestion (not now)
                post = led.posts.get(pid)
                if post is not None and cfg.creative_variation:
                    _adopt_render(led, cfg, post, plans.get(pid), accts)   # render BEFORE queued (publish-needs-media)
                    if post.variant_hook and not post.media_urls:   # render could NOT be materialized (e.g. clip gone)
                        get_logger(cfg)("approve", pid, "render_unavailable_skip_approve")   # surface it; never a silent hookless ship
                        continue                                    # don't queue a variant post without its burned file
                sugg = suggest_time(cfg, post, now=now) if post is not None else None
                led.approve_post(pid, now_iso=now_iso, suggested_iso=sugg)
                approved += 1
    except Exception as exc:
        return ActionResult(ok=False, error=f"approve failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={**detail, "approved": approved})

def approve_posts(cfg: Config, ids: Sequence[str], *, now: Optional[datetime] = None) -> ActionResult:
    """Post-approval gate (multi-select, the Review-tab batch): awaiting_approval -> queued for each selected
    post in ONE transaction, idempotent (a non-awaiting post is a no-op). Slice 2: each approved per-account
    surface's on-screen hook is BURNED here (the render is warmed off the flock, then adopted) so ONLY approved
    posts ever render. One `now` stamp for the whole batch (consistent stale-schedule bump). Never a 500."""
    sel = [i for i in (ids or []) if i]
    return _approve_ids_with_render(cfg, resolve_ids=lambda led: sel, now=now, detail={})

def reject_posts(cfg: Config, ids: Sequence[str]) -> ActionResult:
    """Operator discard (multi-select): awaiting_approval -> rejected (terminal) for each selected post
    in ONE transaction, idempotent. Never a 500."""
    sel = [i for i in (ids or []) if i]
    try:
        with Ledger.transaction(cfg) as led:
            for pid in sel: led.reject_post(pid)
    except Exception as exc:
        return ActionResult(ok=False, error=f"reject failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"rejected": len(sel)})

def unapprove_post(cfg: Config, post_id: str) -> ActionResult:
    """Send an approved-but-unsent post back to Review (the Schedule-tab 'send back' control): queued ->
    awaiting_approval. Idempotent; a non-queued post is a clean no-op. Tight transaction, no network."""
    try:
        with Ledger.transaction(cfg) as led:
            if post_id not in led.posts: return ActionResult(ok=False, error=f"no such post: {post_id}")
            led.unapprove_post(post_id)
    except Exception as exc:
        return ActionResult(ok=False, error=f"unapprove failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"post_id": post_id})

def _warm_hooked_render(cfg: Config, moment_id: str, aspect, hook: str) -> bool:
    """Lock-free pre-render of the HOOKED clip (mirror _warm_target_aspect, but FORCE the burn): set the
    restored hook on a THROWAWAY Ledger.load snapshot's moment and call render_moment, which writes cid.mp4
    + its fingerprint sidecar with the hook burned and NO flock held. The in-lock render_moment in
    approve_with_hook then hits the fingerprint-skip and adopts it WITHOUT running ffmpeg under the lock.
    Returns True when the render is warmed (the in-lock call will fingerprint-SKIP ffmpeg) OR there is
    nothing to warm; False ONLY when the warm ffmpeg FAILED — the caller then ABORTS rather than letting
    render_moment burn under the flock (the M1 'never ffmpeg under the lock' invariant; one click must never
    hold the flock for a 600s burn). A failure is LOGGED, never silently swallowed."""
    from fanops.clip import render_moment
    try:
        snap = Ledger.load(cfg)
        mom = snap.moments.get(moment_id)
        if mom is None: return True                                  # nothing to warm; the in-lock path skips the render too
        snap.moments[moment_id] = mom.model_copy(update={"hook": hook, "hook_removed": None})
        render_moment(snap, cfg, moment_id, aspect=aspect)
        return True
    except Exception as e:
        get_logger(cfg)("approve_with_hook", moment_id, "warm_failed", err=str(e)[:120])   # don't swallow silently
        return False

def approve_with_hook(cfg: Config, clip_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """The 'restore the auto-removed hook, then approve' half of the removed-hook choice (the operator's
    core ask, slice 2). RESTORES moment.hook from moment.hook_removed, RE-RENDERS the clip so the hook BURNS
    into the mp4 (lock-free pre-warm -> in-lock fingerprint-skip; mirrors crosspost's #4 warm), PRESERVES the
    clip's captioned state + per-surface captions across the re-render, then approves EVERY awaiting_approval
    post of the clip. A render failure rolls the whole thing back (atomic) and surfaces the error — the
    operator asked for the hook, so we never silently ship clean. No awaiting posts -> a clean no-op that
    does NOT touch a possibly-shipped render. One transaction for the commit; the heavy ffmpeg ran outside it."""
    from fanops.clip import render_moment
    if cfg.creative_variation:
        return ActionResult(ok=False, error="creative variation is ON — per-surface hooks own the on-screen "
                            "burn, so the moment hook can't be restored this way (turn off FANOPS_CREATIVE_VARIATION).")
    now = _now(now); now_iso = iso_z(now)
    snap = Ledger.load(cfg)                               # lock-free: resolve the removed hook + PRE-WARM the render
    c0 = snap.clips.get(clip_id)
    if c0 is None: return ActionResult(ok=False, error=f"no such clip: {clip_id}")
    m0 = snap.moments.get(c0.parent_id)
    removed = (m0.hook_removed if m0 is not None else None)
    if removed and not _warm_hooked_render(cfg, c0.parent_id, c0.aspect, removed):   # ffmpeg OUTSIDE the flock
        # M1 invariant: the off-lock pre-warm FAILED, so the in-lock render_moment would burn ffmpeg under the
        # flock (a 600s hold). Abort with a retry instead — the operator re-clicks and the next pass re-warms.
        return ActionResult(ok=False, error="couldn't pre-render the hooked clip off the lock — retry approve "
                            "(the on-screen hook is never burned under the ledger flock)")
    approved = 0
    try:
        with Ledger.transaction(cfg) as led:
            clip = led.clips.get(clip_id)
            if clip is None: return ActionResult(ok=False, error=f"no such clip: {clip_id}")
            ids = [p.id for p in led.posts.values()
                   if p.parent_id == clip_id and p.state is PostState.awaiting_approval]
            mom = led.moments.get(clip.parent_id)
            restored = (mom.hook_removed if mom is not None else None)
            if ids and restored:                          # only re-render when there's actually a post to ship with it
                led.moments[clip.parent_id] = mom.model_copy(update={"hook": restored, "hook_removed": None})
                orig = led.clips[clip_id]
                led, rc = render_moment(led, cfg, clip.parent_id, aspect=clip.aspect)   # fp-skip adopts the warm mp4
                if rc.state is ClipState.error:
                    raise RuntimeError(rc.error_reason or "clip re-render failed")
                if rc.hook_burn_failed:                        # CRITICAL (ecc review): a SUCCESSFUL render that
                    # couldn't burn the hook (ffmpeg lacks the text filter, or the hook made no burnable text)
                    # would ship the post CLEAN. The operator asked for the hook -> roll back, never silent-clean.
                    raise RuntimeError("hook burn failed — ffmpeg can't render on-screen text (no libass), "
                                       "or the hook produced nothing burnable; not shipping clean")
                led.clips[clip_id] = led.clips[clip_id].model_copy(
                    update={"state": orig.state, "meta_captions": _inherit_captions(orig.meta_captions)})   # keep captioned state + DEEP-copied captions
            for pid in ids:                                  # P1: untimed/stale post -> a strictly-future suggestion (not now)
                post = led.posts.get(pid)
                sugg = suggest_time(cfg, post, now=now) if post is not None else None
                led.approve_post(pid, now_iso=now_iso, suggested_iso=sugg)
            approved = len(ids)
    except Exception as exc:
        return ActionResult(ok=False, error=f"approve-with-hook failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"approved": approved, "clip_id": clip_id, "hook": bool(removed)})

def _approve_matching(cfg: Config, pred=None, *, pred_for=None, now: Optional[datetime] = None,
                      detail: Optional[dict] = None) -> ActionResult:
    """Approve EVERY awaiting_approval post matching the predicate in ONE transaction (the shared spine for the
    scoped bulk-approve actions). One `now` stamp for the whole batch so approve_post's stale-schedule bump
    is consistent; P1 strictly-future suggestion per post (never machine-guns to now). Idempotent, never a
    500. `detail` is merged into the result (e.g. {"clip_id": ...} / {"account": ...}).

    Two predicate forms: `pred(p)` (post-only — the existing clip/account scope) OR `pred_for(led) -> pred`
    when the predicate needs IN-LOCK ledger context (Phase 4 source scope walks clip -> moment.parent_id from
    led.moments — built ONCE inside the transaction, never per-post I/O, off a fresh in-lock read). Slice 2:
    each approved surface's per-account render is materialized (burned) before it is promoted to queued."""
    def _resolve(led):
        p = pred_for(led) if pred_for is not None else pred
        return [post.id for post in led.posts.values() if post.state is PostState.awaiting_approval and p(post)]
    return _approve_ids_with_render(cfg, resolve_ids=_resolve, now=now, detail=detail or {})

def approve_clip(cfg: Config, clip_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """M3b 'all accounts of this moment': one-click approve EVERY awaiting_approval surface of ONE clip, so
    the operator approves a whole moment's per-account set without ticking each box. Idempotent, never a 500."""
    return _approve_matching(cfg, lambda p: p.parent_id == clip_id, now=now, detail={"clip_id": clip_id})

def approve_account(cfg: Config, handle: str, *, batch: Optional[str] = None, source: Optional[str] = None,
                    platform: Optional[str] = None, now: Optional[datetime] = None) -> ActionResult:
    """M3b/Phase 4 'this account across the whole video': one-click approve EVERY awaiting_approval post of ONE
    account, scopable to a batch (Post.batch_id), a source (Phase 4: the stable Source.id via clip ->
    moment.parent_id), AND a platform (Slice 2: a matrix COLUMN is a handle×platform CHANNEL, so column-approve
    clears only that channel — without it, approving @b's IG column would also clear @b's TikTok column). A blank
    handle -> clean no-op (the button only shows under an active account filter). Idempotent, never a 500.

    The source scope walks lineage, which lives only on the in-lock ledger — so when `source` is set we build a
    `clip_id -> source_id` map ONCE inside the transaction (pred_for) and close over it; a post whose clip has
    broken lineage maps to a sentinel that matches NO source filter, so a scoped approve never over-approves on
    a dangling clip. platform=None / source=None each restore the broader scope (byte-identical legacy path)."""
    handle = (handle or "").strip()
    if not handle:
        return ActionResult(ok=True, detail={"account": None, "approved": 0})
    det = {"account": handle, "batch": batch, "source": source, "platform": platform}
    def _chan(p) -> bool: return platform is None or p.platform.value == platform   # column = handle × platform
    if source is None:                          # legacy path (post-only predicate, no lineage walk); platform=None -> byte-identical
        return _approve_matching(cfg, lambda p: p.account == handle and (batch is None or p.batch_id == batch) and _chan(p),
                                 now=now, detail=det)
    def _pred_for(led):                         # Phase 4: build the clip -> source map ONCE from the in-lock ledger
        src_of = {c.id: (m.parent_id if (m := led.moments.get(c.parent_id)) is not None else None)
                  for c in led.clips.values()}
        return lambda p: (p.account == handle and (batch is None or p.batch_id == batch) and _chan(p)
                          and src_of.get(p.parent_id) == source)   # dangling clip -> None != source -> excluded
    return _approve_matching(cfg, pred_for=_pred_for, now=now, detail=det)

def approve_moment(cfg: Config, moment_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """Matrix 'approve this whole moment-row': approve EVERY awaiting_approval post across ALL channels AND ALL
    clips (a moment may span aspects) of ONE moment, in one click. A moment uniquely identifies its source
    (Moment.parent_id), so this is inherently source-scoped — it can never over-approve onto another source.
    The lineage (post -> clip.parent_id == moment) lives only on the in-lock ledger, so we build the
    moment's clip-id set ONCE inside the transaction (pred_for) and close over it. Idempotent, never a 500."""
    def _pred_for(led):
        clip_ids = {c.id for c in led.clips.values() if c.parent_id == moment_id}
        return lambda p: p.parent_id in clip_ids
    return _approve_matching(cfg, pred_for=_pred_for, now=now, detail={"moment": moment_id})

def approve_as_is(cfg: Config, clip_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """The 'ship it clean' half of the removed-hook choice: one-click approve EVERY awaiting_approval post of
    a clip WITHOUT restoring the auto-removed hook. Functionally identical to approve_clip (a clip with no
    hook_removed has nothing to restore) — delegates to it and records the no-hook choice. hook_removed stays
    on the moment as a record (the choice re-applies to any future repost). Idempotent, never a 500."""
    r = approve_clip(cfg, clip_id, now=now)
    if not r.ok:
        return r
    return ActionResult(ok=True, detail={**r.detail, "hook": False})

def approve_stitches(cfg: Config, ids: Sequence[str]) -> ActionResult:
    """M3 operator approval (multi-select): suggested -> approved for each selected stitch_plan in ONE
    transaction, idempotent (a non-suggested plan is a no-op). Never a 500."""
    sel = [i for i in (ids or []) if i]
    try:
        with Ledger.transaction(cfg) as led:
            for pid in sel: led.approve_stitch_plan(pid)
    except Exception as exc:
        return ActionResult(ok=False, error=f"approve failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"approved": len(sel)})

def dismiss_stitches(cfg: Config, ids: Sequence[str]) -> ActionResult:
    """M3 operator dismiss (multi-select): suggested|approved -> dismissed (terminal) for each selected
    stitch_plan in ONE transaction, idempotent. Never a 500."""
    sel = [i for i in (ids or []) if i]
    try:
        with Ledger.transaction(cfg) as led:
            for pid in sel: led.dismiss_stitch_plan(pid)
    except Exception as exc:
        return ActionResult(ok=False, error=f"dismiss failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"dismissed": len(sel)})

def release_stitches(cfg: Config, ids: Sequence[str]) -> ActionResult:
    """M4 operator RELEASE (multi-select): the second gate — a rendered `stitch_draft` clip the operator
    reviewed is promoted to `captioned` (now crosspost-eligible), inheriting the base clip's per-surface
    captions (an impact-cut keeps the same subject/caption as the bare clip the operator already saw). The
    ONLY transition out of stitch_draft is this explicit operator action — re-checked in-lock so a
    non-stitch_draft id is a clean no-op. Captions come from the best captioned sibling (same moment +
    aspect); none found -> released with whatever captions the base carries (crosspost skips empty surfaces).
    One transaction, idempotent, never a 500."""
    sel = [i for i in (ids or []) if i]
    released = 0
    try:
        with Ledger.transaction(cfg) as led:
            for cid in sel:
                c = led.clips.get(cid)
                if c is None or c.state is not ClipState.stitch_draft:
                    continue                                  # only a rendered stitch_draft releases
                base = _best_caption_sibling(led, c)
                if base is not None:
                    c.meta_captions = _inherit_captions(base.meta_captions)
                c.state = ClipState.captioned
                released += 1
    except Exception as exc:
        return ActionResult(ok=False, error=f"release failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"released": released})

def _best_caption_sibling(led, stitch):
    """The clip whose captions the stitch inherits: a non-stitch sibling (same moment + aspect) that
    carries meta_captions, preferring a captioned one. None if no caption-bearing sibling exists."""
    sibs = [c for c in led.clips.values() if c.parent_id == stitch.parent_id and c.aspect is stitch.aspect
            and c.id != stitch.id and c.state is not ClipState.stitch_draft and c.meta_captions]
    if not sibs:
        return None
    sibs.sort(key=lambda c: (c.state is not ClipState.captioned, c.id))   # captioned first, then deterministic
    return sibs[0]
