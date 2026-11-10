"""Studio caption/hook edit mutations (no Flask)."""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from pydantic import ValidationError

from fanops.config import Config
from fanops.errors import ToolchainMissingError, reason
from fanops.ledger import Ledger
from fanops.models import CaptionSet, ClipState, Platform, Post, PostState
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


def _source_of(led: Ledger, post):
    clip = led.clips.get(getattr(post, "parent_id", None))
    moment = led.moments.get(getattr(clip, "parent_id", None)) if clip is not None else None
    return led.sources.get(getattr(moment, "parent_id", None)) if moment is not None else None


def _parts_for_edit(cfg: Config, led: Ledger, post, textarea: str) -> tuple[str, list[str], bool]:
    """`(sentence, tags, keep_hashtags)`.

    YouTube stores the textarea as `Post.caption` and leaves hashtags for `_youtube_tags`.
    IG/TT with an empty lock stores the textarea and no tags. A non-empty lock splits the
    send string and ships the lock intersection.
    """
    from fanops.caption_compose import (_source_lock_measurements, _source_lock_tags,
                                        split_posted_caption)
    from fanops.hashtags import ship_from_lock
    if post.platform is Platform.youtube:
        return textarea, list(post.hashtags or []), True
    src = _source_of(led, post)
    lock = _source_lock_tags(cfg, src)
    if not lock:
        return textarea, [], False
    sentence, tokens = split_posted_caption(textarea)
    return sentence, ship_from_lock(
        tokens, lock, artist_name=cfg.artist_name,
        measurements=_source_lock_measurements(cfg, src)), False


def text_for_caption_edit(cfg: Config, led: Ledger, post, textarea: str) -> str:
    """String `edit_caption` should see for a sentence-only Review box.

    YouTube stores the textarea as-is. IG/TT that already contain tag tokens keep
    that composed string. A sentence with no tokens and a source lock is joined
    with `post.hashtags` (after swaps) so `_parts_for_edit` ships those tags
    instead of clearing them.
    """
    from fanops.caption_compose import (compose_posted_caption, split_posted_caption,
                                        _source_lock_tags)
    raw = textarea if isinstance(textarea, str) else ""
    if post is None or post.platform is Platform.youtube:
        return raw
    _sentence, tokens = split_posted_caption(raw)
    if tokens or not _source_lock_tags(cfg, _source_of(led, post)):
        return raw
    return compose_posted_caption(raw, list(post.hashtags or []))


def _write_seed_caption(led: Ledger, post, *, caption: str, tags: list[str], clip_id: str | None) -> None:
    """Write the seed `meta_captions` entry mint reads: sentence, shipped tags, `tag_sources` lock.

    Key is `account/platform`, or the legacy `@` key when that is the one present.
    Preserves the entry's `hook`, `axis`, `rationale`, and `fallback`.
    """
    if not clip_id:
        return
    clip = led.clips.get(clip_id)
    if clip is None:
        return
    from fanops.caption_compose import _surface_keys
    from fanops.caption_ingest import _caption_entry
    key, legacy = _surface_keys(post)
    meta = dict(getattr(clip, "meta_captions", None) or {})
    use = key if key in meta else (legacy if legacy in meta else key)
    prev = meta.get(use) if isinstance(meta.get(use), dict) else {}
    entry = _caption_entry(
        list(tags), list(prev.get("hashtags_raw") or []),
        caption=caption, tag_sources={t: "lock" for t in tags})
    for field in ("hook", "axis", "rationale", "fallback"):
        if field in prev:
            entry[field] = prev[field]
    meta[use] = entry
    clip.meta_captions = meta


def edit_caption(cfg: Config, post_id: str, caption: str, *, now: Optional[datetime] = None) -> ActionResult:
    from fanops.caption_ingest import brand_risk_flag       # function-local: the ONE off-brand guardrail captions use (no module cycle)
    from fanops.recaption import _seed_clip_id
    from fanops.studio import actions as _actions
    now = _actions._now(now)
    preview = Ledger.load(cfg)
    p0, err0 = _guard_editable_post(preview, post_id, now)
    if err0:
        return ActionResult(ok=False, error=err0)
    sentence0, _tags0, _keep0 = _parts_for_edit(cfg, preview, p0, caption)
    flag = brand_risk_flag(sentence0, cfg)       # MOL-86: SAME guard as regenerate_caption / ingest_captions — no bypass
    if flag:
        return ActionResult(ok=False, error=f"caption rejected — {flag}. Edit it to stay on-brand.")
    stored: list[str] = []
    sentence = sentence0
    with Ledger.transaction(cfg) as led:
        p, err = _guard_editable_post(led, post_id, now)
        if err:
            return ActionResult(ok=False, error=err)
        sentence, tags, keep = _parts_for_edit(cfg, led, p, caption)
        flag = brand_risk_flag(sentence, cfg)
        if flag:
            return ActionResult(ok=False, error=f"caption rejected — {flag}. Edit it to stay on-brand.")
        clip_id = _seed_clip_id(led, p)
        p.caption = sentence
        if not keep:
            p.hashtags = list(tags)
        stored = list(p.hashtags or [])
        _write_seed_caption(led, p, caption=sentence, tags=stored, clip_id=clip_id)
        _stamp_edited(led, post_id, now)
    return ActionResult(ok=True, detail={"post_id": post_id, "caption": sentence, "hashtags": stored})


def swap_shipped_tag(cfg: Config, post_id: str, old_tag: str, new_tag: str, *,
                     now: Optional[datetime] = None) -> ActionResult:
    """Replace one tag in the list `send_caption` is showing, then `ship_from_lock`.

    `Post.caption` stays the sentence already stored (YouTube keeps its raw caption).
    `Post.hashtags` becomes the shipped list. State is not touched.
    """
    from fanops.caption_compose import (send_caption, _source_lock_measurements, _source_lock_tags)
    from fanops.hashtags import norm_tag, ship_from_lock
    from fanops.recaption import _seed_clip_id
    from fanops.studio import actions as _actions
    now = _actions._now(now)
    old_k, new_k = norm_tag(old_tag or ""), norm_tag(new_tag or "")
    if not old_k or not new_k:
        return ActionResult(ok=False, error="tag swap needs the current tag and the replacement")
    preview = Ledger.load(cfg)
    _p0, err0 = _guard_editable_post(preview, post_id, now)
    if err0:
        return ActionResult(ok=False, error=err0)
    with Ledger.transaction(cfg) as led:
        p, err = _guard_editable_post(led, post_id, now)
        if err:
            return ActionResult(ok=False, error=err)
        _text, showing, _entry = send_caption(cfg, led, p)
        replaced = False
        nxt: list[str] = []
        for t in showing:
            if not replaced and norm_tag(t) == old_k:
                nxt.append(new_tag)
                replaced = True
            else:
                nxt.append(t)
        if not replaced:
            return ActionResult(ok=False, error=f"{old_tag} is not on this post")
        src = _source_of(led, p)
        shipped = ship_from_lock(
            nxt, _source_lock_tags(cfg, src), artist_name=cfg.artist_name,
            measurements=_source_lock_measurements(cfg, src))
        p.hashtags = list(shipped)
        _write_seed_caption(led, p, caption=(p.caption or ""), tags=list(shipped),
                            clip_id=_seed_clip_id(led, p))
        _stamp_edited(led, post_id, now)
        return ActionResult(ok=True, detail={
            "post_id": post_id, "caption": p.caption, "hashtags": list(shipped),
            "state": p.state.value})


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
    from fanops.caption_compose import (_hashtag_metrics_for, _source_lock_measurements,
                                        _source_lock_tags)
    from fanops.caption_ingest import brand_risk_flag
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
    meas = _source_lock_measurements(cfg, src)
    lock = _source_lock_tags(cfg, src)
    hashtag_metrics = _hashtag_metrics_for(meas, lock)
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
    new_caption, new_tags = (item.caption or "").strip(), ship_from_lock(
        picks, lock, artist_name=cfg.artist_name, measurements=meas)
    from fanops.recaption import _seed_clip_id
    with Ledger.transaction(cfg) as led2:               # re-guard + write INSIDE a short transaction
        # fresh now: the model call may have taken ~180s, during which the post could have become
        # imminent/due — re-check against real wall-clock (fail-safe), not the stale entry-time now.
        p2, err2 = _guard_editable_post(led2, post_id, _actions._now(None))
        if err2:
            return ActionResult(ok=False, error=err2)
        clip_id = _seed_clip_id(led2, p2)
        p2.caption = new_caption
        p2.hashtags = new_tags
        _write_seed_caption(led2, p2, caption=new_caption, tags=list(new_tags), clip_id=clip_id)
        _stamp_edited(led2, post_id, now)
    return ActionResult(ok=True, detail={"post_id": post_id, "caption": new_caption, "hashtags": new_tags})


def _moment_hook_update(hook: str, transcript_excerpt: Optional[str]) -> dict:
    """Hook burn write. `transcript_excerpt` is set only when regen refreshed the window excerpt."""
    update = {"hook": hook, "hook_removed": None}
    if transcript_excerpt:
        update["transcript_excerpt"] = transcript_excerpt
    return update


def reburn_hook(cfg: Config, post_id: str, hook: str, *, now: Optional[datetime] = None,
                transcript_excerpt: Optional[str] = None) -> ActionResult:
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
    moment_update = _moment_hook_update(hook, transcript_excerpt)
    snap.moments[mom_id] = mom.model_copy(update=moment_update)
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
                led2.moments[mom_id] = m2.model_copy(update=moment_update)
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


def _hook_regen_reject(why: str) -> ActionResult:
    return ActionResult(ok=False, error=f"regenerated hook rejected — {why}. Edit it by hand or try again.")


def regenerate_hook(cfg: Config, post_id: str, guidance: str = "", *,
                    model=None, now: Optional[datetime] = None) -> ActionResult:
    """Re-run the frame-seeing hook author for ONE editable post and burn the accepted line.

    Same payload as moments.request_moment_hooks (fitted window, window frames, peaks, refreshed
    excerpt, personas, learned styles). moment_hook_prompt runs OUTSIDE the ledger lock — one call
    per click. The vision call is claude_json_meta (images=frames, moment_hooks model, keyframe
    read_root), same as responder._default_claude_model. Ingest parity (brand_risk_flag,
    is_weak_hook, language) rejects without writing and without nulling Moment.hook.
    Acceptance persists through reburn_hook. The clock is read again after the model
    call; a post that is no longer editable is refused before render_moment.
    """
    from fanops.accounts import Accounts
    from fanops.caption_ingest import _lang_base, brand_risk_flag
    from fanops.clip import fit_window
    from fanops.hookcheck import is_weak_hook
    from fanops.models import MomentHookDecision, MomentHookRequest
    from fanops.moment_hook_learning import proven_hook_styles
    from fanops.moments import (_hook_personas_for_moment, _hook_script_mismatch, _peak_in_segments,
                                _peak_in_window, _window_frames)
    from fanops.prompts import moment_hook_prompt
    from fanops.responder import screen_model_text
    from fanops.transcribe import excerpt_for_window
    from fanops.studio import actions as _actions
    now = _actions._now(now)
    led = Ledger.load(cfg)
    p, err = _guard_editable_post(led, post_id, now)
    if err:
        return ActionResult(ok=False, error=err)
    clip = led.clips.get(p.parent_id)
    if clip is None:
        return ActionResult(ok=False, error=f"no clip for post {post_id}")
    moment = led.moments.get(clip.parent_id)
    if moment is None:
        return ActionResult(ok=False, error="no moment for clip")
    src = led.sources.get(moment.parent_id)
    if src is None:
        return ActionResult(ok=False, error="no source for moment")
    base = cfg.context_path.read_text() if cfg.context_path.exists() else ""
    full_guidance = base
    if (guidance or "").strip():
        full_guidance = (base + "\n\nOPERATOR INSTRUCTION FOR THIS REGENERATION (highest priority): "
                         + guidance.strip())
    dur = src.duration or 0.0
    hi = dur if dur > 0 else float("inf")
    cs, ce = fit_window(moment.start, moment.end, dur, lo=0.0, hi=hi)
    env_peaks = [pk for pk in (src.signal_peaks or []) if _peak_in_window(pk, cs, ce)]
    if moment.segments:
        span_peaks = [pk for pk in (src.signal_peaks or []) if _peak_in_segments(pk, moment.segments)]
        peaks = span_peaks if span_peaks else env_peaks
    else:
        peaks = env_peaks
    segs = list(moment.segments) if moment.segments else None
    fresh = excerpt_for_window(src, cs, ce)
    stored_excerpt = moment.transcript_excerpt or ""
    excerpt_write = fresh if fresh and fresh != stored_excerpt else None
    accts = Accounts.load(cfg)
    styles = proven_hook_styles(led, cfg, accts)
    payload = MomentHookRequest(
        source_id=src.id, moment_id=moment.id, token=moment.content_token,
        request_id="", start=moment.start, end=moment.end, reason=moment.reason,
        transcript_excerpt=fresh or stored_excerpt, signal_score=moment.signal_score,
        language=src.language, guidance=full_guidance, clip_profile=cfg.clip_profile,
        frames=_window_frames(cfg, src, cs, ce, segments=segs),
        signal_peaks=peaks, personas=_hook_personas_for_moment(moment, accts)).model_dump()
    payload.pop("request_id", None)
    if styles:
        payload["learned_hooks"] = styles
    prompt = moment_hook_prompt(payload)
    schema = MomentHookDecision.model_json_schema()
    frames = payload.get("frames") or None
    try:
        if model is None:
            from fanops.llm import LlmFramesUnreadError, claude_json_meta
            try:
                out, _answered, frames_unread = claude_json_meta(
                    prompt, schema, images=frames, model=cfg.llm_model_for("moment_hooks"),
                    read_root=(str(cfg.agent_io / "keyframes") if frames else None))
            except LlmFramesUnreadError as exc:
                get_logger(cfg)("regenerate_hook", post_id, "frames_unread", err=str(exc)[:160])
                return _hook_regen_reject("frames were not read")
            if frames and frames_unread:
                get_logger(cfg)("regenerate_hook", post_id, "frames_unread",
                                err="attached frames were not read")
                return _hook_regen_reject("frames were not read")
        else:
            out = model(prompt, schema)
    except ToolchainMissingError as exc:
        return ActionResult(ok=False, error=f"Regenerate needs `{cfg.llm_cli_binary}` on PATH (run "
                            f"`fanops autopilot` once to enable auto mode): {str(exc)[:160]}")
    except Exception as exc:
        get_logger(cfg)("regenerate_hook", post_id, "regenerate_failed", err=str(exc)[:160])
        return ActionResult(ok=False, error=f"regenerate failed: {str(exc)[:160]}")
    try:
        dec = MomentHookDecision(**{**out, "request_id": "regen"})
    except (ValidationError, TypeError) as exc:
        return ActionResult(ok=False, error="regenerated hook was malformed: "
                            f"{reason(exc) if isinstance(exc, ValidationError) else exc}")
    dec = screen_model_text(dec)
    hook = dec.hook or ""
    flag = brand_risk_flag(hook, cfg)
    if flag:
        return _hook_regen_reject(flag)
    gate = Ledger.load(cfg)
    used: set[str] = set()
    cluster: set[str] = set()
    for other in gate.moments.values():
        if other.id == moment.id:
            continue
        text = (other.hook or "").strip().lower()
        if not text:
            continue
        used.add(text)
        if other.parent_id == src.id:
            cluster.add(text)
    if is_weak_hook(hook, used, cluster_scope=cluster):
        return _hook_regen_reject("weak hook")
    src_lang = _lang_base(src.language)
    if src_lang and _hook_script_mismatch(hook, src_lang):
        return _hook_regen_reject("language does not match the source")
    # The model call can outlast the 5-minute edit window. reburn_hook writes the mp4
    # before its in-lock guard, and that pre-check would see this function's entry clock.
    fresh = _actions._now(None)
    _fresh_post, err_fresh = _guard_editable_post(gate, post_id, fresh)
    if err_fresh:
        return ActionResult(ok=False, error=err_fresh)
    return reburn_hook(cfg, post_id, hook, now=fresh, transcript_excerpt=excerpt_write)

