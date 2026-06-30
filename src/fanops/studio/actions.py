# src/fanops/studio/actions.py — CREATE
"""Lock-safe Studio mutations (no Flask). Each public action opens ONE Ledger.transaction and does
its existence + state(queued) + not-imminent guard + mutation INSIDE the lock, on the in-lock
freshly-loaded ledger — mirroring the cmd_reconcile/cmd_resolve recovery verbs in cli.py so it cannot
lose-update against a concurrent cron `fanops run`. Reads/normalization that can fail happen OUTSIDE the lock."""
from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import ValidationError

from fanops.config import Config
from fanops.errors import AuthError, ToolchainMissingError, reason
from fanops.ledger import Ledger
from fanops.models import CaptionSet, ClipState, MomentCastingDecision, MomentDecision, MomentHookDecision, Post, PostState
from fanops.ids import child_id, surface_key, _hash
from fanops import overlay
from fanops.timeutil import parse_iso, iso_z
from fanops.studio.views import _imminent
from fanops.studio.actions_common import ActionResult, _now, _inherit_captions  # noqa: F401
from fanops.audit import write_audit
from fanops.studio.actions_run import (run_ingest, run_pull, save_uploads, save_uploads_and_ingest, save_thirdparty_uploads, run_ingest_thirdparty, run_advance, run_prepare)  # noqa: F401
from fanops.studio.actions_approve import (approve_posts, reject_posts, unapprove_post, approve_with_hook, approve_clip, approve_batch, approve_account, approve_moment, approve_as_is, approve_stitches, dismiss_stitches, release_stitches)  # noqa: F401
from fanops.studio.actions_casting import cast_add, cast_remove  # noqa: F401

SNOOZE_DAYS = 365
_GATE_MODELS = {"moments": MomentDecision, "moment_hooks": MomentHookDecision, "moment_casting": MomentCastingDecision, "captions": CaptionSet}

def _normalize_z(new_time: str) -> str:
    """Parse an ISO time, COERCE naive -> UTC (iso_z would otherwise treat naive as LOCAL time),
    and re-emit the canonical ...Z aware form. Raises ValueError on unparseable input."""
    dt = parse_iso(new_time)                       # raises ValueError on garbage
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)       # explicit UTC coercion (never local-tz guess)
    return iso_z(dt)


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


def reschedule_post(cfg: Config, post_id: str, new_time: str, *, now: Optional[datetime] = None) -> ActionResult:
    now = _now(now)
    try:
        z = _normalize_z(new_time)                 # OUTSIDE the lock: reject bad input early
    except (ValueError, TypeError) as exc:
        return ActionResult(ok=False, error=f"bad time {new_time!r}: {str(exc)[:120]}")
    with Ledger.transaction(cfg) as led:
        p, err = _guard_editable_post(led, post_id, now)
        if err:
            return ActionResult(ok=False, error=err)
        p.scheduled_time = z
    return ActionResult(ok=True, detail={"post_id": post_id, "scheduled_time": z})


def clear_time(cfg: Config, post_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """P1: deliberately DROP a post's scheduled_time. On an awaiting post just clears it. On a QUEUED post,
    FIRST sends it back to awaiting_approval (unapprove) THEN clears — both in ONE transaction, in that order,
    so the post is NEVER persisted as queued-and-timeless (which publish_due would publish-now). Reuses
    _guard_editable_post (rejects unknown/imminent/wrong-state), mirroring reschedule_post's shape. The
    unapprove uses the immutable model_copy (ledger layer); the scheduled_time=None is the in-place actions-
    layer edit (like reschedule_post's in-place p.scheduled_time = z) — consistent with both conventions."""
    now = _now(now)
    with Ledger.transaction(cfg) as led:
        p, err = _guard_editable_post(led, post_id, now)
        if err:
            return ActionResult(ok=False, error=err)
        if p.state is PostState.queued:
            led.unapprove_post(post_id)        # queued -> awaiting FIRST (model_copy), so it's never queued+None
        led.posts[post_id].scheduled_time = None
    return ActionResult(ok=True, detail={"post_id": post_id})


def edit_caption(cfg: Config, post_id: str, caption: str, *, now: Optional[datetime] = None) -> ActionResult:
    now = _now(now)
    with Ledger.transaction(cfg) as led:
        p, err = _guard_editable_post(led, post_id, now)
        if err:
            return ActionResult(ok=False, error=err)
        p.caption = caption
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
    from fanops.caption import brand_risk_flag
    now = _now(now)
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
    payload = {"clip_id": p.parent_id, "language": src.language if src else None,
               "transcript_excerpt": moment.transcript_excerpt if moment else "",
               "guidance": full_guidance,
               "surfaces": [{"surface": surface, "platform": p.platform.value}]}
    if model is None:
        from fanops.llm import claude_json
        model = claude_json
    try:                                                # the slow generation, OUTSIDE any lock
        out = model(caption_prompt(payload), CaptionSet.model_json_schema())
    except ToolchainMissingError as exc:
        return ActionResult(ok=False, error="Regenerate needs the `claude` CLI on PATH (run "
                            f"`fanops autopilot` once to enable auto mode): {str(exc)[:160]}")
    except Exception as exc:
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
    new_caption, new_tags = item.caption, list(item.hashtags or [])
    with Ledger.transaction(cfg) as led2:               # re-guard + write INSIDE a short transaction
        # fresh now: the model call may have taken ~180s, during which the post could have become
        # imminent/due — re-check against real wall-clock (fail-safe), not the stale entry-time now.
        p2, err2 = _guard_editable_post(led2, post_id, _now(None))
        if err2:
            return ActionResult(ok=False, error=err2)
        p2.caption = new_caption
        p2.hashtags = new_tags
    return ActionResult(ok=True, detail={"post_id": post_id, "caption": new_caption, "hashtags": new_tags})


def reburn_hook(cfg: Config, post_id: str, hook: str, *, now: Optional[datetime] = None) -> ActionResult:
    """Face 4 — re-burn ONE editable surface's on-screen HOOK (NO LLM). The operator edits the literal
    per-account hook text; this re-burns it via ffmpeg (overlay.burn_hook_only) onto the SAME deterministic
    variant path /media serves, then a SHORT transaction flips post.variant_hook + post.media_urls ONLY.
    Both survive repost_post (the real 'Post again' reuse path). It NEVER writes clip.meta_captions['hook']
    — that key is dead (the on-screen-hook source of truth is Moment.hooks_by_persona, read at crosspost).
    Gated on cfg.creative_variation (per-surface variant burns only exist then). The 600s ffmpeg runs
    LOCK-FREE (the 60s flock guard forbids holding the lock across it — mirror regenerate_caption); the
    field flip is re-guarded inside a short transaction. hook_burn_failed (burn returns False — no libass /
    nothing burnable) -> ok=True, detail.hook_burned=False (WARN, surfaced; an EDIT, so NO rollback, unlike
    approve_with_hook). Does NOT publish — safe on any backend, no confirm gate."""
    if not cfg.creative_variation:
        return ActionResult(ok=False, error="re-burn needs per-account hooks ON (FANOPS_CREATIVE_VARIATION)")
    from fanops.models import Fmt, PLATFORM_ASPECT, Render, RenderState
    now = _now(now)
    led = Ledger.load(cfg)                              # lock-free read: reject early, then burn OUTSIDE the lock
    p, err = _guard_editable_post(led, post_id, now)
    if err:
        return ActionResult(ok=False, error=err)
    clip = led.clips.get(p.parent_id)
    if clip is None:
        return ActionResult(ok=False, error=f"no clip for post {post_id}")
    # The on-screen hook is owned by the per-account RENDER (the single source of truth). A hook EDIT changes
    # the content -> a NEW content-addressed render id (child_id of clip+hook); burn it (atomic, LOCK-FREE)
    # and point the post at it. The render's hook_text ALWAYS matches the burned pixels — the old reburn
    # mutated post.variant_hook alone and drifted from the file. Lineage for filing: clip->moment->source.
    aspect = PLATFORM_ASPECT.get(p.platform, Fmt.r9x16)
    tw, th = {Fmt.r9x16: (1080, 1920), Fmt.r1x1: (1080, 1080), Fmt.r16x9: (1920, 1080)}.get(aspect, (1080, 1920))
    # AUDIT H1: the render IDENTITY + cut decision come from the SAME source the crosspost mint uses
    # (account_render_spec), so a re-burn of an OVERRIDE account (its own length/framing) PRESERVES the
    # per-account CUT instead of silently reverting it to a bare-hook, global-length, centred shared clip.
    from fanops.crosspost import account_render_spec
    from fanops.clip import render_account_cut
    from fanops.models import HookSource
    from fanops.accounts import Accounts
    acct = next((a for a in Accounts.load(cfg).accounts if a.handle == p.account), None)   # None -> global defaults
    rid, wants_cut, acct_profile, acct_top_bias = account_render_spec(cfg, clip=clip, hook=hook, acct=acct)
    mom = led.moments.get(clip.parent_id)
    src = led.sources.get(mom.parent_id) if mom is not None else None
    batch_id = src.batch_id if src is not None else None
    source_id = src.id if src is not None else None
    skey = surface_key(p.account, p.platform.value)
    vpath = cfg.render_path(batch_id, source_id, rid, aspect)   # filed under clips/{batch}/{src}/; mkdirs
    produced, realized = False, None
    if wants_cut:                                       # override account: re-cut the SOURCE at its own band+crop (LOCK-FREE)
        produced, realized = render_account_cut(led, cfg, clip.parent_id, aspect=aspect, profile=acct_profile,
                                                hook=hook, out_path=vpath, top_bias=acct_top_bias)
    burned = produced
    # P3: a re-burn supplies a LITERAL operator-typed hook -> it IS account-specific (per_account); there is no
    # "shared fallback" on an explicit edit. Empty hook -> none. cut_seconds rides the same re-mint (anti-drift H1).
    hook_source = HookSource.per_account if (hook or "").strip() else HookSource.none
    if not produced:                                    # default band/frame OR a failed cut -> shared-clip burn
        burned = overlay.burn_hook_only(clip.path, vpath, hook, width=tw, height=th,
                                        font=cfg.subtitle_font)   # LOCK-FREE; atomic + fail-open: vpath always exists
    with Ledger.transaction(cfg) as led2:               # re-guard + write INSIDE a short transaction
        p2, err2 = _guard_editable_post(led2, post_id, _now(None))   # fresh now: the burn may have made it imminent
        if err2:
            return ActionResult(ok=False, error=err2)
        # add_render is content-addressed first-write-wins: a re-burn of an EXISTING hook reuses the same
        # render; a NEW hook adds a fresh one (the prior render, if now unreferenced, is GC-swept by state).
        # is_account_cut mirrors the crosspost mint: truthful when an override account got its own cut.
        led2.add_render(Render(id=rid, clip_id=p2.parent_id, account=p2.account, surface_key=skey,
                               hook_text=hook, path=vpath, state=RenderState.rendered,
                               batch_id=batch_id, source_id=source_id, is_account_cut=produced,
                               hook_source=hook_source, cut_seconds=realized))   # P3: never stale on re-burn (H1 anti-drift)
        p2.render_id = rid                              # the authoritative pointer
        p2.variant_hook = hook                          # read-only mirror of Render.hook_text (carried by repost_post)
        p2.media_urls = [f"file://{vpath}"]
    return ActionResult(ok=True, detail={"post_id": post_id, "hook": hook, "hook_burned": bool(burned),
                                         "render_id": rid, "media_url": f"file://{vpath}"})


def approve_candidate(cfg: Config, eid: str) -> ActionResult:
    """Track C: approve a discover candidate from the browser — move 00_review/<eid>.jpg into
    00_review/approved/ (what the operator used to do by hand in Finder). eid must be a bare stem
    (no path separators / ..) so a Studio POST can't move an arbitrary file. No ledger touch — this
    is a review-folder move; `fanops intake` then copies the original into the inbox."""
    if not eid or "/" in eid or "\\" in eid or ".." in eid:
        return ActionResult(ok=False, error=f"bad candidate id: {eid!r}")
    src = cfg.review / f"{eid}.jpg"
    if not src.exists():
        return ActionResult(ok=False, error=f"no such candidate: {eid}")
    dst = cfg.review / "approved" / f"{eid}.jpg"
    try:                                               # read-only mount / disk full / rename race
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
    except OSError as exc:
        return ActionResult(ok=False, error=f"approve failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"eid": eid})


# Non-terminal states an operator may mark "posted by hand". `error` is included (ecc:python-review):
# it is semantically a recoverable failure like `failed` (digest.py treats them alike), so the UI
# must not strand an error-state post. Excludes the terminal published/analyzed/retired.
_POSTABLE = {PostState.queued, PostState.needs_reconcile, PostState.submitting,
             PostState.submitted, PostState.failed, PostState.error}

def mark_published(cfg: Config, post_id: str, url: Optional[str] = None) -> ActionResult:
    """Track B: the operator posted this clip by hand — force the post to `published` (+ REQUIRED
    live URL). Like `fanops resolve <id> published` but STRICTER (ecc:python-review): resolve is the
    unguarded force-anything escape hatch, whereas this rejects an already-terminal
    (published/analyzed/retired) post so a double-click can't churn terminal state. Tight local
    transaction, no network.

    R1/D9: `url` is now REQUIRED (non-empty after strip). Saying "I posted by hand" MEANS the
    operator has a permalink they can paste — refusing the action without one closes the third door
    onto the ghost-row class (alongside D1: DryRunPoster, D2: _publish_one). Without this check the
    same operator-driven path produced Post(state=published, public_url='') — a row that says
    SHIPPED but the Posted tub can't render."""
    if not (url or "").strip():
        return ActionResult(ok=False, error=(
            "mark_published requires a non-empty url — you said you posted by hand, paste the "
            "permalink so the Posted tub has something to render (R1/D9)."))
    with Ledger.transaction(cfg) as led:
        if post_id not in led.posts:
            return ActionResult(ok=False, error=f"no such post: {post_id}")
        p = led.posts[post_id]
        if p.state not in _POSTABLE:
            return ActionResult(ok=False, error=f"post {post_id} is {p.state.value} — only an unpublished post can be marked posted")
        # R1: set the URL BEFORE the state flip so the @model_validator sees a consistent shape on
        # the next ledger save (Pydantic re-validates the modified instance on serialization).
        p.public_url = url.strip()
        p.state = PostState.published
    # R3/D17: audit the SUCCESS — 'I posted by hand' is the most opaque action; the audit gives the operator a breadcrumb.
    write_audit(cfg, "mark_published", [post_id], reason="studio_mark_published", url=url.strip())
    return ActionResult(ok=True, detail={"post_id": post_id, "url": url})



def _studio_publish_guard(cfg: Config, post=None) -> Optional[str]:
    """Studio publish actions must not silently dryrun when the operator expects live."""
    if not cfg.is_live:
        return "Not live — flip Go Live before publishing. Nothing reaches social in dryrun."
    if post is not None:
        from fanops.accounts import Accounts
        from fanops.post.run import _post_provider
        accts = Accounts.load(cfg)
        prov = _post_provider(cfg, accts, post)
        if prov == "dryrun":
            return (f"{post.account} on {post.platform.value} routes to dryrun — map the channel in Go Live → Accounts.")
        if prov is None:
            return (f"{post.account} on {post.platform.value} is not mapped — connect the channel in Go Live.")
    return None


def accept_suggested_account(cfg: Config, handle: str, *, now: Optional[datetime] = None) -> ActionResult:
    """Apply batch spread suggestions to every queued post on one account."""
    from fanops.studio.views_common import suggest_times_for_batch
    now = _now(now); moved = 0
    try:
        with Ledger.transaction(cfg) as led:
            posts = [p for p in led.posts.values() if p.state is PostState.queued and p.account == handle]
            sched = suggest_times_for_batch(cfg, posts, now=now)
            for pid, t in sched.items():
                p = led.posts[pid]
                if p.scheduled_time != t:
                    p.scheduled_time = t
                    moved += 1
    except Exception as exc:
        return ActionResult(ok=False, error=f"accept suggestions failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"rescheduled": moved, "outcome": "suggestions_accepted", "handle": handle})


def preflight_publish_media(cfg: Config, post) -> str | None:
    """Return an error string when local media exceeds backend caps (fail BEFORE network)."""
    from pathlib import Path
    from fanops.models import Platform
    path = None
    if post.render_id:
        r = Ledger.load(cfg).renders.get(post.render_id)
        path = r.path if r else None
    elif post.media_urls and post.media_urls[0].startswith("file://"):
        path = post.media_urls[0][7:]
    elif post.media_urls and not post.media_urls[0].startswith("http"):
        path = post.media_urls[0]
    if not path or not Path(path).exists():
        return None
    size = Path(path).stat().st_size
    backend = cfg.effective_publish_mode()
    if post.platform is Platform.tiktok and backend == "zernio" and size > cfg.zernio_max_upload_bytes:
        from fanops.post.compress import maybe_shrink_for_cap
        shrunk = maybe_shrink_for_cap(cfg, Path(path), cfg.zernio_max_upload_bytes, label="preflight")
        if shrunk.stat().st_size <= cfg.zernio_max_upload_bytes:
            return None
        return f"oversize: {size} bytes > {cfg.zernio_max_upload_bytes} — re-render shorter"
    return None


def reconcile_inflight(cfg: Config) -> ActionResult:
    """Poll backends for permalinks on in-flight posts (Studio reconcile strip)."""
    if not cfg.is_live:
        return ActionResult(ok=False, error="Publishing is off — turn on Go Live before checking for links.")
    from fanops.reconcile import reconcile_due
    try:
        summary = reconcile_due(cfg)
    except Exception as exc:
        return ActionResult(ok=False, error=f"reconcile failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"outcome": "reconciled", **summary})

def publish_now(cfg: Config, post_id: str, *, confirmed: bool = True) -> ActionResult:
    """Ship ONE reviewed post IMMEDIATELY from the Studio (milestone 5: publish in the UI) via the
    SAME poster path the pipeline uses (post.run.publish_post) — a real post on a live backend, a
    dryrun no-op->published locally — IGNORING the post's (future) schedule, so the occasional-batch
    operator can review then ship without waiting for the schedule or touching the CLI. Same
    live-publish confirm + fatal-auth surfacing as run_advance; queued-only; scoped to THIS post
    (other scheduled posts are untouched). Distinct from mark_published (Track B: 'I posted by hand')
    — this actually drives the poster."""
    from fanops.post.run import publish_post
    if cfg.is_live and not confirmed:
        # UI-LIE-FIX: per-channel truth, not the legacy global.
        return ActionResult(ok=False, error=f"LIVE backend ({cfg.effective_publish_mode()}): this "
                            "PUBLISHES the post to a real account — tick the confirm box, then click again.")
    # Short lock-free guard read for a friendly message; publish_post's own CLAIM transaction is the
    # authoritative queued-only gate (a state change in the gap is re-validated there -> a clean no-op).
    led = Ledger.load(cfg)
    if post_id not in led.posts:
        return ActionResult(ok=False, error=f"no such post: {post_id}")
    post = led.posts[post_id]
    st = post.state
    if st is not PostState.queued:
        return ActionResult(ok=False, error=f"post {post_id} is {st.value} — only a queued post can be published")
    if (err := _studio_publish_guard(cfg, post)):
        return ActionResult(ok=False, error=err)
    if (pf := preflight_publish_media(cfg, post)):
        try:
            with Ledger.transaction(cfg) as led:
                p = led.posts.get(post_id)
                if p is not None:
                    p.state = PostState.failed
                    p.error_reason = pf
        except Exception:
            pass
        return ActionResult(ok=False, error=pf)
    try:
        # network runs OUTSIDE the ledger lock (per-post claim->network->finalize) — the Studio no longer
        # holds the flock across the publish round-trip, so a concurrent daemon pass isn't starved.
        state = publish_post(cfg, post_id)
    except AuthError as exc:
        # UI-LIE-FIX: the auth-key name comes from the EXCEPTION CLASS, not a backend guess
        # (BlotatoAuthError -> BLOTATO_API_KEY, etc). This is unambiguous: the backend that raised
        # owns the key. Replaces the old `if cfg.poster_backend == 'postiz'` ternary that lied on
        # per-channel deployments and didn't even know zernio existed.
        key = Config.auth_key_name_from_error(exc)
        return ActionResult(ok=False, error=f"FATAL auth failure — check {key}: {str(exc)[:160]}")
    except Exception as exc:
        # A non-auth failure (media upload RuntimeError, corrupt clip.path, etc.) must NOT escape to
        # Flask as a 500 — the cockpit surfaces it cleanly (mirrors run_advance's broad catch).
        return ActionResult(ok=False, error=f"publish failed: {str(exc)[:160]}")
    # ONLY 'published' is success: _publish_one advances submitted -> published on a clean poster return,
    # so any other terminal state means the post did NOT fully ship. A None return means the CLAIM gate
    # found it no longer queued (e.g. a concurrent daemon pass just claimed it between the guard read and
    # the claim) — tell the operator to retry rather than print a confusing "post is None".
    if state in ("published", "needs_reconcile", "submitted"):
        pub = Ledger.load(cfg).posts.get(post_id)
        if cfg.is_live and pub is not None and str(pub.public_url or "").startswith("dryrun://"):
            return ActionResult(ok=False, error=("LIVE publish ran dryrun — post NOT on social. "
                                "Restart Studio (or Go Live) so FANOPS_LIVE=1 is active, then retry."))
        from fanops.studio.views_results import classify_post_delivery
        delivery = classify_post_delivery(pub) if pub else "dryrun"
        outcome = {"live": "live_shipped", "inflight": "inflight_submitted", "dryrun": "dryrun_local"}.get(delivery, "live_shipped")
        write_audit(cfg, "publish_now", [post_id], reason="studio_publish_now",
                    backend=cfg.effective_publish_mode())
        return ActionResult(ok=True, detail={"post_id": post_id, "state": state, "outcome": outcome,
                                             "submission_id": getattr(pub, "submission_id", None),
                                             "public_url": getattr(pub, "public_url", None),
                                             "backend": cfg.effective_publish_mode()})
    if state is None:
        return ActionResult(ok=False, error="post was not claimable (it may be publishing already) — refresh and try again")
    return ActionResult(ok=False, error=f"publish did not complete (post is {state}) — see the run log")


def answer_gate(cfg: Config, kind: str, key: str, data: dict) -> ActionResult:
    """Answer a moment/caption agent gate from the browser through the SAME validated contract the
    responder uses (Phase 3a): echo the latest request_id, validate the FULL response against its
    Pydantic model, and write response.json ONLY if valid — a bad answer never lands, so the gate
    stays pending (the operator can retry). No Ledger lock: gate files live under 04_agent_io, not
    the ledger; read_response's request_id staleness check is the safety net, not a lock."""
    from fanops.agentstep import latest_request_id, response_path
    model = _GATE_MODELS.get(kind)
    if model is None:
        return ActionResult(ok=False, error=f"unknown gate kind: {kind!r}")
    rid = latest_request_id(cfg, kind, key)
    if rid is None:
        return ActionResult(ok=False, error=f"no pending {kind} gate for {key!r}")
    full = {"request_id": rid, **data}
    if kind == "moments":
        full["source_id"] = key                    # MomentDecision echoes the source it decides
    try:
        validated = model(**full)
    except ValidationError as exc:
        return ActionResult(ok=False, error=reason(exc))
    response_path(cfg, kind, key).write_text(validated.model_dump_json(indent=2))
    return ActionResult(ok=True, detail={"kind": kind, "key": key})


def snooze_clip(cfg: Config, clip_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """Push every non-imminent queued post of a clip ~SNOOZE_DAYS into the future, in ONE
    transaction (atomic — never a partial snooze). Computes the snooze time directly (iso_z) and
    applies an inline per-post imminence + state check — it does not use _guard_editable_post/_normalize_z
    (it operates over many posts of a clip, not one editable post)."""
    now = _now(now)
    z = iso_z(now + timedelta(days=SNOOZE_DAYS))
    with Ledger.transaction(cfg) as led:
        if clip_id not in led.clips:
            return ActionResult(ok=False, error=f"no such clip: {clip_id}")
        count = 0
        for p in led.posts.values():
            # bump both approved (queued) and pre-approval (awaiting_approval) posts — Review shows the
            # latter, so a Review-card snooze must actually move something (not a silent 0-count no-op).
            if (p.parent_id == clip_id and p.state in (PostState.queued, PostState.awaiting_approval)
                    and not _imminent(p.scheduled_time, now)):
                p.scheduled_time = z
                count += 1
    return ActionResult(ok=True, detail={"clip_id": clip_id, "count": count, "scheduled_time": z})


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
            led.add_post(Post(id=new_id, parent_id=src.parent_id, state=PostState.awaiting_approval,
                              account=src.account, account_id=src.account_id, platform=src.platform,
                              caption=src.caption, hashtags=list(src.hashtags or []), aspect=src.aspect,
                              media_urls=list(src.media_urls or []), scheduled_time=None,
                              created_at=iso_z(_now(None)),   # content-lifecycle: fresh birth day (aware)
                              submission_id=f"fanops_{_hash('idemp', new_id)}",
                              first_frame_kind=src.first_frame_kind,
                              cut_seconds=src.cut_seconds, clip_profile=src.clip_profile,
                              batch_id=src.batch_id,   # Account-First Studio: a repost keeps its source batch grouping
                              variant_key=src.variant_key, variant_hook=src.variant_hook,
                              variation_axis=src.variation_axis))   # carry P2 axis so a repost's attribution isn't lost
    except Exception as exc:
        return ActionResult(ok=False, error=f"repost failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"post_id": new_id, "source_id": post_id})

def _warm_target_aspect(cfg: Config, moment_id: str, aspect) -> None:
    # #4 lock-free pre-render (mirror pipeline._prewarm): _clip_for_aspect on a THROWAWAY Ledger.load snapshot
    # reuses an existing render OR runs render_moment, which writes cid.mp4 + its fingerprint sidecar with NO
    # flock held. The in-transaction _clip_for_aspect below then hits the fingerprint-skip and mints
    # microseconds-fast instead of running ffmpeg (600s-bound) UNDER the lock — N bulk clips no longer
    # serialize N renders behind the write lock. FAIL-OPEN: any error here just means the in-lock path renders
    # as today (never a crash); the snapshot state is discarded — only the on-disk mp4+fp persist, and the
    # transaction re-resolves authoritatively.
    from fanops.crosspost import _clip_for_aspect
    try: _clip_for_aspect(Ledger.load(cfg), cfg, moment_id, aspect)
    except Exception: pass

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
    from fanops.models import Platform, PLATFORM_ASPECT, PLATFORM_MAX_SECONDS, Fmt
    from fanops.crosspost import _clip_for_aspect
    now = _now(now)
    try: plat = Platform(platform)
    except ValueError: return ActionResult(ok=False, error=f"unknown platform: {platform!r}")
    try: accts = Accounts.load(cfg)
    except Exception as exc: return ActionResult(ok=False, error=f"accounts.json: {str(exc)[:160]}")
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
            if clip.held or led.is_retired_clip(clip.id) or led.is_retired_moment(clip.parent_id):
                return ActionResult(ok=False, error=f"clip {clip_id} is held/retired — not eligible for cross-post")
            m = led.moments.get(clip.parent_id)
            source = led.sources.get(m.parent_id) if m is not None else None
            src_batch = source.batch_id if source is not None else None   # AUDIT M2: inherit the clip's ingest-batch
            # lineage (like repost_post) so the reuse post groups + approves with its batched siblings — a None-batch
            # post showed in the ?batch= drill-in (card derives bid from a sibling) but approve_account silently skipped it.
            clip_dur = (m.end - m.start) if m is not None else None
            max_secs = PLATFORM_MAX_SECONDS.get(plat)
            if max_secs is not None and clip_dur is not None and clip_dur > 0 and clip_dur > max_secs:
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
            led.add_post(Post(id=pid, parent_id=target_clip.id, state=PostState.awaiting_approval,
                              account=surf.account, account_id=surf.account_id, platform=surf.platform,
                              caption=caption, hashtags=hashtags, aspect=aspect, scheduled_time=None,
                              created_at=iso_z(now), submission_id=f"fanops_{_hash('idemp', pid)}",
                              clip_profile=cfg.clip_profile, batch_id=src_batch))
    except Exception as exc:
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

def _seconds_away(scheduled_time: Optional[str], now: datetime, *, window_s: int = 60) -> bool:
    """M3: a tight protect-window for reschedule — TRUE only when the post fires in the next
    `window_s` seconds (default 60). PAST-DUE posts are NOT protected (the operator's complaint:
    'Reschedule all silently reschedules nothing' is exactly the bug where past-due was treated
    as imminent). Distinct from `_imminent` (5 min, used for the EDIT-DISABLED UI guard — a
    different concern; editing a 4-min-out post races the publisher, but RESPREADING it doesn't)."""
    if not scheduled_time:
        return False                                # missing time -> respread, never protect
    try:
        dt = parse_iso(scheduled_time)
    except (ValueError, TypeError):
        return False                                # unparseable -> respread, never protect
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return now <= dt <= now + timedelta(seconds=window_s)


def reschedule_bucket(cfg: Config, *, now: Optional[datetime] = None, handle: Optional[str] = None) -> ActionResult:
    """Routine re-spread of the APPROVED bucket: re-stagger every queued (approved) post onto a fresh
    cadence starting from `now`, reusing the M4 batch-aware spread engine (suggest_times_for_batch) so
    Approve and Reschedule share ONE cadence story — a single source of truth, no drift. Past-due posts
    ARE respread (M3 fix: today's broad `_imminent` 5-min gate silently no-op'd the bucket the operator
    cares about); only TRULY about-to-fire posts (seconds away) are protected, via `_seconds_away`.
    Never touches awaiting/published/etc. One transaction, idempotent-by-`now`, never a 500. The
    Schedule-tab 'reschedule all' control. An optional `handle` scopes the respread to ONE account
    (the per-account M3 control); None = the whole bucket."""
    from fanops.studio.views_common import suggest_times_for_batch
    now = _now(now)
    due: list = []
    try:
        with Ledger.transaction(cfg) as led:
            due = [p for p in led.posts.values()
                   if p.state is PostState.queued
                   and not _seconds_away(p.scheduled_time, now)
                   and (handle is None or p.account == handle)]
            due.sort(key=lambda p: (p.scheduled_time or "", p.account, p.platform.value, p.id))  # stable order in
            sched = suggest_times_for_batch(cfg, due, now=now)
            for p in due:
                p.scheduled_time = sched[p.id]
    except Exception as exc:
        return ActionResult(ok=False, error=f"reschedule failed: {str(exc)[:160]}")
    # R3/D17: audit which posts moved + the handle scope (None = whole bucket).
    if due:
        write_audit(cfg, "reschedule_bucket", [p.id for p in due],
                    reason="studio_reschedule_bucket", handle=handle, rescheduled=len(due))
    return ActionResult(ok=True, detail={"rescheduled": len(due), "handle": handle})


def shift_account_schedule(cfg: Config, handle: str, hours: float, *, now: Optional[datetime] = None) -> ActionResult:
    """Nudge every queued post for one account by a fixed offset — preserves relative spacing."""
    handle = (handle or "").strip()
    if not handle:
        return ActionResult(ok=True, detail={"shifted": 0, "handle": None, "hours": hours})
    now = _now(now)
    delta = timedelta(hours=hours)
    moved = 0
    try:
        with Ledger.transaction(cfg) as led:
            for p in led.posts.values():
                if p.state is not PostState.queued or p.account != handle:
                    continue
                if _seconds_away(p.scheduled_time, now) or not p.scheduled_time:
                    continue
                try:
                    dt = parse_iso(p.scheduled_time)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    p.scheduled_time = iso_z(dt + delta)
                    moved += 1
                except (ValueError, TypeError):
                    continue
    except Exception as exc:
        return ActionResult(ok=False, error=f"shift failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"shifted": moved, "handle": handle, "hours": hours})

def reschedule_account(cfg: Config, handle: str, *, now: Optional[datetime] = None) -> ActionResult:
    """M3 per-account respread: re-stagger one account's queued posts on a fresh cadence (past-due
    included), leaving every other account untouched. Thin wrapper over `reschedule_bucket` so the
    M3 PRD outcome — a 'Reschedule this account' control that respreads exactly one account — has a
    named entry point; the per-account scoping is enforced inside the transaction (no race)."""
    return reschedule_bucket(cfg, now=now, handle=handle)


def publish_due_bucket(cfg: Config, *, handle: Optional[str] = None, batch: Optional[str] = None,
                       confirmed: bool = True, now: Optional[datetime] = None) -> ActionResult:
    """Publish every DUE queued post in scope (Schedule 'Publish all due'). LIVE requires confirm + shows rate."""
    from fanops.errors import AuthError
    from fanops.post.run import publish_due
    from fanops.studio.views_results import due_publish_plan
    from fanops.timeutil import iso_z
    plan = due_publish_plan(cfg, handle=handle, batch=batch, now=_now(now))
    if plan.due == 0:
        return ActionResult(ok=True, detail={"due": 0, "published": 0, "plan": plan.__dict__})
    if (err := _studio_publish_guard(cfg)):
        return ActionResult(ok=False, error=err)
    if cfg.is_live and not confirmed:
        tail = f", est. {plan.est_minutes} min" if plan.est_minutes and plan.postiz_due else ""
        rate = f"~{plan.rate_per_min}/min Postiz cap" if plan.rate_per_min else "live backends"
        return ActionResult(ok=False, error=(f"LIVE: Publish all due ships {plan.due} post(s) ({rate}{tail}) — "
                            "tick the confirm box, then click again."))
    try:
        summary = publish_due(cfg, now=iso_z(_now(now)), account=handle, batch_id=batch)
    except AuthError as exc:
        key = Config.auth_key_name_from_error(exc)
        return ActionResult(ok=False, error=f"FATAL auth failure — check {key}: {str(exc)[:160]}")
    except Exception as exc:
        return ActionResult(ok=False, error=f"publish due failed: {str(exc)[:160]}")
    write_audit(cfg, "publish_due_bucket", [], reason="studio_publish_due_bucket", handle=handle, batch=batch, **summary)
    return ActionResult(ok=True, detail={**summary, "plan": plan.__dict__})


_REVIEW_REVERT_BLOCKED = frozenset({
    PostState.published, PostState.analyzed, PostState.needs_reconcile,
    PostState.submitting, PostState.submitted,
})


def resolve_post(cfg: Config, post_id: str, status: str, *, url: Optional[str] = None) -> ActionResult:
    """Studio twin of cmd_resolve — operator forces ground truth on stuck inflight posts."""
    from fanops.models import _POST_TERMINAL_REQUIRES_URL
    if post_id not in (Ledger.load(cfg).posts):
        return ActionResult(ok=False, error=f"no such post: {post_id}")
    try:
        st = PostState(status)
    except ValueError:
        st = PostState.published if status == "published" else PostState.failed
    if st in _POST_TERMINAL_REQUIRES_URL and not (url or "").strip():
        return ActionResult(ok=False, error="Paste the live permalink to mark this post published.")
    try:
        with Ledger.transaction(cfg) as led:
            if post_id not in led.posts:
                return ActionResult(ok=False, error=f"no such post: {post_id}")
            p = led.posts[post_id]
            if (url or "").strip():
                p.public_url = url.strip()
            p.state = st
            if st is PostState.failed:
                p.error_reason = p.error_reason or "marked failed by operator"
    except Exception as exc:
        return ActionResult(ok=False, error=f"resolve failed: {str(exc)[:160]}")
    write_audit(cfg, "resolve_post", [post_id], reason="studio_resolve", status=st.value, url=(url or "").strip())
    outcome = "live_shipped" if st is PostState.published else "failed"
    return ActionResult(ok=True, detail={"post_id": post_id, "outcome": outcome, "state": st.value,
                                          "public_url": (url or "").strip() or None})


def pull_metrics_studio(cfg: Config, *, window: str = "30d") -> ActionResult:
    """Pull analytics for live posts — closes the Posted→Learn loop from Studio."""
    if not cfg.is_live:
        return ActionResult(ok=False, error="Publishing is off — turn on Go Live before pulling metrics.")
    from fanops.track import pull_metrics, _default_list_posts
    from fanops.digest import write_digest
    try:
        led0 = Ledger.load(cfg)
        pollable = [p for p in led0.posts.values()
                    if p.submission_id and p.state in (PostState.published, PostState.analyzed)]
        if not pollable:
            return ActionResult(ok=True, detail={"outcome": "metrics_pulled", "analyzed": 0, "series_rows": 0,
                                                    "degraded": 0, "pollable": 0})
        rows = list(_default_list_posts(cfg, posts=pollable)(window))
    except (RuntimeError, AuthError) as exc:
        return ActionResult(ok=False, error=str(exc)[:160])
    except Exception as exc:
        return ActionResult(ok=False, error=f"metrics pull failed: {str(exc)[:160]}")
    try:
        with Ledger.transaction(cfg) as led:
            before = {pid: len(p.metrics_series) for pid, p in led.posts.items()}
            led = pull_metrics(led, cfg, list_posts=lambda _w: rows, window=window)
            analyzed = len(led.posts_in_state(PostState.analyzed))
            added = deg = 0
            for pid, p in led.posts.items():
                new_rows = p.metrics_series[before.get(pid, 0):]
                added += len(new_rows)
                deg += sum(1 for r in new_rows if r.get("lift_degraded"))
    except Exception as exc:
        return ActionResult(ok=False, error=f"metrics apply failed: {str(exc)[:160]}")
    try:
        write_digest(Ledger.load(cfg), cfg)
    except Exception:
        pass
    write_audit(cfg, "pull_metrics", [], reason="studio_pull_metrics", analyzed=analyzed, series_rows=added)
    return ActionResult(ok=True, detail={"outcome": "metrics_pulled", "analyzed": analyzed,
                                          "series_rows": added, "degraded": deg, "pollable": len(pollable)})


def bulk_send_to_review(cfg: Config, post_ids: list[str], *, reason: str) -> ActionResult:
    """R3/D7: the operator's wipe-and-revert flow as a first-class API. For each id move
    state -> awaiting_approval and clear the post-publish telemetry (scheduled_time,
    public_url, metrics, published_at). The session's hand-edited 67-post revert becomes
    one atomic call. Best-effort: known ids are moved; unknown ids surface in the result
    (operator typo never passes for success). Atomic per id (one transaction holding the
    flock for the whole batch). The reason field is the operator's intent — pinned in the
    audit so 'why this batch went back to Review' is in the log."""
    ids = [str(i) for i in (post_ids or []) if i]
    moved: list[str] = []
    skipped: list[str] = []
    unknown: list[str] = []
    try:
        with Ledger.transaction(cfg) as led:
            for pid in ids:
                if pid not in led.posts:
                    unknown.append(pid); continue
                p = led.posts[pid]
                if p.state in _REVIEW_REVERT_BLOCKED:
                    skipped.append(pid); continue
                p.state = PostState.awaiting_approval
                p.scheduled_time = None
                p.public_url = ""
                p.metrics = {}
                p.published_at = None
                # Don't touch submission_id / batch_id — keep the lineage so the operator can
                # see "this post was once part of batch X" in the audit / Posted history.
                moved.append(pid)
    except Exception as exc:
        return ActionResult(ok=False, error=f"bulk_send_to_review failed: {str(exc)[:160]}")
    # R3/D17: audit the bulk revert — the most operator-impactful action in the system.
    if moved:
        write_audit(cfg, "bulk_send_to_review", moved, reason=reason,
                    unknown=unknown, moved=len(moved))
    return ActionResult(ok=True, detail={"moved": len(moved), "skipped": len(skipped), "unknown": unknown,
                                          "post_ids": moved})


def restore_persona_hook(cfg: Config, post_id: str, *, now: Optional[datetime] = None) -> ActionResult:
    """Restore a guard-stripped per-account hook onto this surface and re-burn preview media."""
    if not cfg.creative_variation:
        return ActionResult(ok=False, error="per-account hook restore needs FANOPS_CREATIVE_VARIATION")
    led = Ledger.load(cfg)
    p = led.posts.get(post_id)
    if p is None:
        return ActionResult(ok=False, error=f"no such post: {post_id}")
    clip = led.clips.get(p.parent_id)
    mom = led.moments.get(clip.parent_id) if clip is not None else None
    if mom is None:
        return ActionResult(ok=False, error="no moment for post")
    removed = (mom.hooks_by_persona_removed or {}).get(p.account)
    if not removed:
        return ActionResult(ok=False, error="no stripped hook to restore for this account")
    try:
        with Ledger.transaction(cfg) as led2:
            m = led2.moments.get(mom.id)
            if m is None:
                return ActionResult(ok=False, error="moment gone")
            hbp = dict(m.hooks_by_persona or {})
            hbp[p.account] = removed
            hbr = {k: v for k, v in (m.hooks_by_persona_removed or {}).items() if k != p.account}
            led2.moments[mom.id] = m.model_copy(update={"hooks_by_persona": hbp, "hooks_by_persona_removed": hbr})
            post = led2.posts.get(post_id)
            if post is not None:
                post.variant_hook = removed
    except Exception as exc:
        return ActionResult(ok=False, error=f"restore hook failed: {str(exc)[:160]}")
    return reburn_hook(cfg, post_id, removed, now=now)


def retry_rate_limited_failures(cfg: Config, *, reason: str = "studio_retry_rate_limit", stagger_min: int = 2) -> ActionResult:
    """Queue all failed posts whose error_reason is a rate-limit (429) for daemon retry."""
    from fanops.studio.views_results import classify_failure
    ids = [pid for pid, p in Ledger.load(cfg).posts.items()
           if p.state in (PostState.failed, PostState.error) and classify_failure(p) == "rate_limit"]
    if not ids:
        return ActionResult(ok=True, detail={"retried": 0, "post_ids": []})
    retried: list[str] = []
    now = _now(None)
    try:
        with Ledger.transaction(cfg) as led:
            for i, pid in enumerate(ids):
                p = led.posts.get(pid)
                if p is None or p.state not in (PostState.failed, PostState.error):
                    continue
                p.state = PostState.queued
                p.submission_id = None
                p.error_reason = None
                p.scheduled_time = iso_z(now + timedelta(minutes=stagger_min * i))
                retried.append(pid)
    except Exception as exc:
        return ActionResult(ok=False, error=f"retry_rate_limited failed: {str(exc)[:160]}")
    if retried:
        write_audit(cfg, "recover_posts", retried, reason=reason, recover_action="retry", retried=len(retried))
    return ActionResult(ok=True, detail={"retried": len(retried), "post_ids": retried, "outcome": "retried_rate_limit", "stagger_min": stagger_min})


def recover_posts(cfg: Config, post_ids: list[str], *, action: str, reason: str = "") -> ActionResult:
    """S1 recovery cockpit: retry (failed→queued, retryable buckets only), review (→awaiting_approval),
    or discard (failed→rejected). Atomic per batch; unknown ids reported; oversize never retried."""
    from fanops.studio.views_results import classify_failure, _RETRYABLE_FAILURES
    ids = [str(i) for i in (post_ids or []) if i]
    if not ids:
        return ActionResult(ok=True, detail={"retried": 0, "discarded": 0, "reviewed": 0, "skipped": 0, "unknown": []})
    action = (action or "").strip().lower()
    if action == "review":
        return bulk_send_to_review(cfg, ids, reason=reason or "studio_recover_review")
    retried: list[str] = []; discarded: list[str] = []; skipped: list[str] = []; unknown: list[str] = []
    try:
        with Ledger.transaction(cfg) as led:
            for pid in ids:
                if pid not in led.posts:
                    unknown.append(pid); continue
                p = led.posts[pid]
                if p.state not in (PostState.failed, PostState.error):
                    skipped.append(pid); continue
                if action == "retry":
                    if classify_failure(p) not in _RETRYABLE_FAILURES:
                        skipped.append(pid); continue
                    p.state = PostState.queued
                    p.submission_id = None
                    p.error_reason = None
                    retried.append(pid)
                elif action == "discard":
                    p.state = PostState.rejected
                    discarded.append(pid)
                else:
                    return ActionResult(ok=False, error=f"unknown recover action: {action}")
    except Exception as exc:
        return ActionResult(ok=False, error=f"recover_posts failed: {str(exc)[:160]}")
    if retried or discarded:
        write_audit(cfg, "recover_posts", retried or discarded, reason=reason,
                    recover_action=action, retried=len(retried), discarded=len(discarded),
                    skipped=len(skipped), unknown=unknown)
    detail = {"retried": len(retried), "discarded": len(discarded), "reviewed": 0,
              "skipped": len(skipped), "unknown": unknown, "post_ids": retried or discarded}
    return ActionResult(ok=True, detail=detail)


def release_held_clip(cfg: Config, clip_id: str) -> ActionResult:
    """Clear a brand-risk hold from the browser — the UI twin of `fanops unhold`. Reuses the canonical
    transition (cli.py unhold): held->captions_requested so the next advance re-runs the caption gate.
    Tight local transaction, no network. Rejects a non-held clip so a stray click can't churn a live
    clip's state (stricter than the operator-trusted CLI verb)."""
    with Ledger.transaction(cfg) as led:
        if clip_id not in led.clips: return ActionResult(ok=False, error=f"no such clip: {clip_id}")
        c = led.clips[clip_id]
        if not c.held: return ActionResult(ok=False, error=f"clip {clip_id} is not held (state={c.state.value})")
        c.held = False; c.held_reason = None; c.state = ClipState.captions_requested
    return ActionResult(ok=True, detail={"clip_id": clip_id, "state": ClipState.captions_requested.value})
