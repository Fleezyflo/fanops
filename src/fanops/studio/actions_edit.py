"""Studio caption/hook edit mutations (no Flask)."""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from pydantic import ValidationError

from fanops.config import Config
from fanops.errors import ToolchainMissingError, reason
from fanops.ledger import Ledger
from fanops.models import CaptionSet, ClipState, Post, PostState
from fanops.log import get_logger
from fanops.timeutil import iso_z
from fanops.studio.views import _imminent
from fanops.studio.actions_common import ActionResult, _inherit_captions

def _guard_editable_post(led: Ledger, post_id: str, now: datetime) -> tuple[Optional[Post], Optional[str]]:
    """Return (post, None) if the post is editable: an awaiting_approval post (the Review worklist — gated,
    so never imminent) OR a queued (approved) post that is not imminent (the Schedule cockpit). Else
    (None, error). post-approval-lifecycle: the operator edits/regenerates/reschedules BEFORE approving."""
    if post_id not in led.posts:
        return None, f"no such post: {post_id}"
    p = led.posts[post_id]
    if p.state is PostState.awaiting_approval:
        return p, None                                 # awaiting -> always editable (it cannot ship yet)
    if p.state is not PostState.queued:
        return None, f"post {post_id} is {p.state.value}; only awaiting-approval or queued posts are editable"
    if _imminent(p.scheduled_time, now):
        return None, f"post {post_id} is imminent/already due — shipping now, cannot edit"
    return p, None


def _stamp_edited(led: Ledger, post_id: str, now: datetime) -> None:
    led.posts[post_id].edited_at = iso_z(now)


def edit_caption(cfg: Config, post_id: str, caption: str, *, now: Optional[datetime] = None) -> ActionResult:
    from fanops.caption_ingest import brand_risk_flag       # function-local: the ONE off-brand guardrail captions use (no module cycle)
    from fanops.studio import actions as _actions
    now = _actions._now(now)
    flag = brand_risk_flag(caption, cfg)             # MOL-86: SAME guard as regenerate_caption / ingest_captions — no bypass
    if flag:
        return ActionResult(ok=False, error=f"caption rejected — {flag}. Edit it to stay on-brand.")
    with Ledger.transaction(cfg) as led:
        p, err = _guard_editable_post(led, post_id, now)
        if err:
            return ActionResult(ok=False, error=err)
        p.caption = caption
        _stamp_edited(led, post_id, now)
    return ActionResult(ok=True, detail={"post_id": post_id, "caption": caption})


def regenerate_caption(cfg: Config, post_id: str, guidance: str = "", *,
                       model=None, now: Optional[datetime] = None) -> ActionResult:
    """Review-first milestone 3 — re-run the caption model for ONE queued post and write the new
    caption back, so the operator changes a hint and 'gets it again' without hand-writing a caption
    or touching the CLI. Reuses the PRODUCTION caption prompt (prompts.caption_prompt) for the post's
    single surface, plus the operator's typed `guidance` as a highest-priority instruction. The SAME
    off-brand guard the pipeline applies (caption.brand_risk_flag) re-runs on the result — a
    regenerated off-brand caption is REJECTED, never written (no guardrail bypass). The slow model
    call runs OUTSIDE the ledger flock (it can be a ~180s `claude -p`, and holding the lock that long
    would deadlock a concurrent run — the 60s pytest timeout guards exactly that); the post is
    re-guarded INSIDE a short transaction before the write, so a run that publishes the post mid-call
    can't be clobbered. `model(prompt, schema)->dict` is injectable for tests; the default is the same
    `claude -p` the llm responder uses. Bounded to ONE model call per click (PRD cost mitigation).
    Does NOT publish — safe on any backend, so no confirm gate."""
    from fanops.prompts import caption_prompt
    from fanops.caption_compose import _hashtag_metrics_for, _source_lock_tags
    from fanops.caption_ingest import brand_risk_flag
    from fanops.hashtags import load_measurements
    from fanops.studio import actions as _actions
    now = _actions._now(now)
    led = Ledger.load(cfg)                              # lock-free read: reject early, build context
    p, err = _guard_editable_post(led, post_id, now)
    if err:
        return ActionResult(ok=False, error=err)
    surface = f"{p.account}/{p.platform.value}"         # the documented caption lookup contract
    clip = led.clips.get(p.parent_id)
    moment = led.moments.get(clip.parent_id) if clip else None
    src = led.sources.get(moment.parent_id) if moment else None
    base = cfg.context_path.read_text() if cfg.context_path.exists() else ""
    full_guidance = base
    if (guidance or "").strip():                        # operator hint is highest priority for this re-roll
        full_guidance = (base + "\n\nOPERATOR INSTRUCTION FOR THIS REGENERATION (highest priority): "
                         + guidance.strip())
    # Parity with the batch payload (caption.request_captions): regen menu = the source lock.
    # Empty lock → empty store. Never the 80-pile / persona corpus / ASR content tags.
    from fanops.accounts import Accounts
    from fanops.personas import caption_directive
    accts = Accounts.load(cfg)
    acct = next((a for a in accts.accounts if a.handle == p.account), None)
    persona = caption_directive(acct) if acct is not None else None
    excerpt = moment.transcript_excerpt if moment else ""
    lock = _source_lock_tags(cfg, src)
    hashtag_metrics = _hashtag_metrics_for(load_measurements(cfg), lock)
    payload = {"clip_id": p.parent_id, "language": src.language if src else None,
               "transcript_excerpt": excerpt,
               "guidance": full_guidance,
               "surfaces": [{"surface": surface, "platform": p.platform.value,
                             **({"persona": persona} if persona else {}),
                             **({"hashtag_store": lock} if lock else {})}],
               **({"hashtag_metrics": hashtag_metrics} if hashtag_metrics else {})}
    if model is None:
        # Gates are answered ONLY by the LLM, so Regenerate always uses the LLM (an injected `model` is the
        # test/programmatic path — unchanged).
        from fanops.llm import claude_json
        model = claude_json
    try:                                                # the slow generation, OUTSIDE any lock
        out = model(caption_prompt(payload), CaptionSet.model_json_schema())
    except ToolchainMissingError as exc:
        return ActionResult(ok=False, error=f"Regenerate needs `{cfg.llm_cli_binary}` on PATH (run "
                            f"`fanops autopilot` once to enable auto mode): {str(exc)[:160]}")
    except Exception as exc:
        get_logger(cfg)("regenerate", post_id, "regenerate_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"regenerate failed: {str(exc)[:160]}")
    try:
        cs = CaptionSet(**{**out, "request_id": "regen"})
    except (ValidationError, TypeError) as exc:
        return ActionResult(ok=False, error=f"regenerated caption was malformed: {reason(exc) if isinstance(exc, ValidationError) else exc}")
    item = next((it for it in cs.items if it.surface == surface), None)
    if item is None and len(cs.items) == 1:
        item = cs.items[0]                              # single-surface regen: accept a lone item
    if item is None:
        return ActionResult(ok=False, error=f"model returned no caption for {surface}")
    flag = brand_risk_flag(item.caption, cfg)           # SAME guard as ingest_captions — no bypass
    if flag:
        return ActionResult(ok=False, error=f"regenerated caption rejected — {flag}. "
                            "Edit it by hand or regenerate again.")
    from fanops.caption_ingest import _tags_in, is_tags_only_caption
    if is_tags_only_caption(item.caption):
        return ActionResult(ok=False, error="regenerated caption rejected — caption_tags_only. "
                            "Edit it by hand or regenerate again.")
    from fanops.hashtags import ship_from_lock
    picks = list(item.hashtags or []) or _tags_in(item.caption)
    new_caption, new_tags = (item.caption or "").strip(), ship_from_lock(picks, lock)
    with Ledger.transaction(cfg) as led2:               # re-guard + write INSIDE a short transaction
        # fresh now: the model call may have taken ~180s, during which the post could have become
        # imminent/due — re-check against real wall-clock (fail-safe), not the stale entry-time now.
        p2, err2 = _guard_editable_post(led2, post_id, _actions._now(None))
        if err2:
            return ActionResult(ok=False, error=err2)
        p2.caption = new_caption
        p2.hashtags = new_tags
        _stamp_edited(led2, post_id, now)
    return ActionResult(ok=True, detail={"post_id": post_id, "caption": new_caption, "hashtags": new_tags})


def reburn_hook(cfg: Config, post_id: str, hook: str, *, now: Optional[datetime] = None) -> ActionResult:
    """P9: re-burn the owner-moment hook — updates m.hook and re-renders the shared clip (no per-post variant)."""
    from fanops.clip import render_moment
    from fanops.studio import actions as _actions
    now = _actions._now(now)
    led = Ledger.load(cfg)
    p, err = _guard_editable_post(led, post_id, now)
    if err:
        return ActionResult(ok=False, error=err)
    clip = led.clips.get(p.parent_id)
    if clip is None:
        return ActionResult(ok=False, error=f"no clip for post {post_id}")
    mom_id = clip.parent_id
    snap = Ledger.load(cfg)
    mom = snap.moments.get(mom_id)
    if mom is None:
        return ActionResult(ok=False, error="no moment for clip")
    snap.moments[mom_id] = mom.model_copy(update={"hook": hook, "hook_removed": None})
    try:
        _, rc = render_moment(snap, cfg, mom_id, aspect=clip.aspect)
    except Exception as exc:
        get_logger(cfg)("reburn", post_id, "reburn_render_failed", err=str(exc)[:120])
        return ActionResult(ok=False, error=f"re-burn render failed: {str(exc)[:120]}")
    if rc.state is ClipState.error:
        return ActionResult(ok=False, error=rc.error_reason or "re-burn render failed")
    hook_burned = not rc.hook_burn_failed
    try:
        with Ledger.transaction(cfg) as led2:
            p2, err2 = _guard_editable_post(led2, post_id, _actions._now(None))
            if err2:
                return ActionResult(ok=False, error=err2)
            m2 = led2.moments.get(mom_id)
            if m2 is not None:
                led2.moments[mom_id] = m2.model_copy(update={"hook": hook, "hook_removed": None})
            c2 = led2.clips.get(rc.id)
            orig = c2
            if orig:
                led2.clips[rc.id] = rc.model_copy(update={"meta_captions": _inherit_captions(orig.meta_captions)})
                led2.set_clip_state(rc.id, orig.state)
            else:
                led2.clips[rc.id] = rc
            _stamp_edited(led2, post_id, now)
    except Exception as exc:
        get_logger(cfg)("reburn", post_id, "reburn_write_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"re-burn failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"post_id": post_id, "hook": hook, "hook_burned": hook_burned})

