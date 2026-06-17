# src/fanops/stitch_render.py
"""M4 (structural-hooks): the impact-cut PRODUCER — the two ledger-side steps that turn a router
reservation into an operator-approved, rendered impact-cut.

  suggest_impact_cuts(led, cfg)      -> creates `suggested` StitchPlans for routed moments (in-lock safe;
                                        renders nothing). Re-routes the moment to `stitch:impact_cut`.
  render_approved_stitches(led, cfg) -> renders `approved` plans into `stitch_draft` clips (T4).

Both are gated by the caller on `cfg.impact_cut` (default OFF). suggest only mutates the ledger, so it
runs inside the advance transaction; the heavy render in render_approved_stitches runs LOCK-FREE."""
from __future__ import annotations
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ClipState, StitchState, PostState
from fanops.ids import child_id
from fanops.router import awaiting, stitched
from fanops.impact_cut import make_stitch_plan, STRATEGY_KEY

# A bare clip in any of these states is not a valid impact-cut base: error/retired are broken/gone, and a
# stitch_draft is itself a stitch (never stitch a stitch).
_NON_BASE_STATES = (ClipState.error, ClipState.retired, ClipState.stitch_draft)
_IMPACT_AWAITING = awaiting("impact_cut")        # "clean_awaiting_strategy:impact_cut"
_IMPACT_STITCHED = stitched("impact_cut")        # "stitch:impact_cut"
# A base post in any of these states is LIVE on (or in-flight to) a platform — supersede must BLOCK and let
# the operator decide, never silently retire a possibly-published post. A `queued` post is retired instead.
_LIVE_POST_STATES = (PostState.submitting, PostState.submitted, PostState.published,
                     PostState.needs_reconcile, PostState.analyzed)


def _read_fingerprint(cfg: Config, clip_id: str) -> str | None:
    """The base clip's pinned render fingerprint (from its {cid}.render.json sidecar), or None if absent/
    unreadable. None pins nothing — a later render then can't detect base drift, so it renders as-pinned."""
    try:
        return json.loads((cfg.clips / f"{clip_id}.render.json").read_text()).get("fp")
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def suggest_impact_cuts(led: Ledger, cfg: Config) -> Ledger:
    """For each moment the router reserved `clean_awaiting_strategy:impact_cut`, create a suggested
    impact-cut StitchPlan for each valid bare clip (idempotent via the content-addressed id; the base
    fingerprint is pinned for the supersede check), then re-route the moment to `stitch:impact_cut`.
    A degenerate cut yields no plan and the moment stays reserved (re-tried next pass). Renders nothing."""
    for m in list(led.moments.values()):
        if (m.hook_strategy or "") != _IMPACT_AWAITING:
            continue
        src = led.sources.get(m.parent_id)
        if src is None:
            continue
        produced = False
        for c in list(led.clips.values()):
            if c.parent_id != m.id or c.state in _NON_BASE_STATES:
                continue
            plan = make_stitch_plan(c, m, src, base_fp=_read_fingerprint(cfg, c.id))
            if plan is None:
                continue
            led.add_stitch_plan(plan)            # idempotent: setdefault by content-addressed id (dedup re-emit)
            produced = True
        if produced:
            led.moments[m.id].hook_strategy = _IMPACT_STITCHED   # the format handler acted
    return led


def _stitch_clip_id(plan_id: str, aspect_value: str) -> str:
    """Content-addressed id for a stitched clip — keyed on the plan + aspect so it can NEVER collide with
    the bare clip's child_id("clip", moment, aspect) (a stitch is a new clip, never an in-place swap)."""
    return child_id("stitch", plan_id, aspect_value)

def _approved_impact_plans(led: Ledger):
    return [p for p in led.stitch_plans.values()
            if p.state is StitchState.approved and p.strategy_key == STRATEGY_KEY]

def approved_impact_cut_count(led: Ledger) -> int:
    """How many impact-cut plans are approved-but-not-yet-rendered. Used by the forward-only kill-switch:
    when the feature is OFF, the pipeline logs this count rather than silently freezing the plans."""
    return len(_approved_impact_plans(led))


def prewarm_approved_stitches(led: Ledger, cfg: Config, log) -> None:
    """Lock-free: render each approved impact-cut plan's mp4 + render-fingerprint sidecar so the in-lock
    commit (render_approved_stitches) ADOPTS the warm output with no ffmpeg under the lock. Mutations to
    this throwaway `led` are discarded; only the on-disk artifacts persist. Fail-open per plan."""
    from fanops.clip import render_moment                # local import: clip imports are heavy; avoid at module load
    for p in _approved_impact_plans(led):
        base = led.clips.get(p.clip_id)
        if base is None or base.state in _NON_BASE_STATES:
            continue
        try:
            cw = (p.plan_params["cut_start"], p.plan_params["cut_end"])
            render_moment(led, cfg, base.parent_id, aspect=base.aspect, cut_window=cw,
                          clip_id=_stitch_clip_id(p.id, base.aspect.value), born_state=ClipState.stitch_draft)
        except Exception as e:                            # fail-open: the commit pass renders it in-lock instead
            log("impact_cut", p.id, "warn", err=str(e)[:120])


def render_approved_stitches(led: Ledger, cfg: Config) -> Ledger:
    """In-lock commit for each approved impact-cut plan. Supersede precedence (PRD, CLOSED requirement):
      - base clip gone               -> plan `error` "base clip missing"
      - base fingerprint drifted     -> plan auto-`dismissed` "base superseded" (the pinned render changed)
      - a LIVE base post exists       -> plan `error` "cannot supersede a live post" (operator decides)
      - else render the stitch_draft clip (adopts the prewarmed mp4 via the fingerprint-skip — no ffmpeg
        under the lock), set the plan `in_use`, and RETIRE any still-queued base post (no feed double-post).
    A failed stitch render (e.g. duration-validity) errors the plan; the bare clip already shipped upstream."""
    from fanops.clip import render_moment
    for p in _approved_impact_plans(led):
        base = led.clips.get(p.clip_id)
        if base is None:
            p.state = StitchState.error; p.error_reason = "base clip missing"; continue
        cur_fp = _read_fingerprint(cfg, p.clip_id)
        if p.base_fingerprint is not None and cur_fp != p.base_fingerprint:
            p.state = StitchState.dismissed; p.error_reason = "base superseded"; continue
        if any(po.parent_id == p.clip_id and po.state in _LIVE_POST_STATES for po in led.posts.values()):
            p.state = StitchState.error; p.error_reason = "cannot supersede a live post"; continue
        cw = (p.plan_params["cut_start"], p.plan_params["cut_end"])
        led, clip = render_moment(led, cfg, base.parent_id, aspect=base.aspect, cut_window=cw,
                                  clip_id=_stitch_clip_id(p.id, base.aspect.value), born_state=ClipState.stitch_draft)
        if clip.state is ClipState.error:
            p.state = StitchState.error; p.error_reason = clip.error_reason or "stitch render failed"; continue
        p.state = StitchState.in_use
        for po in led.posts.values():                     # retire a still-queued bare post so the feed never doubles up
            if po.parent_id == p.clip_id and po.state is PostState.queued:
                po.state = PostState.retired
    return led
