# src/fanops/stitch_render.py
"""M4/M5 (structural-hooks): the stitch PRODUCER — the ledger-side steps that turn router reservations
into operator-approved, rendered structural hooks.

  mine_suggestions(led, cfg, log)    -> M5 routine pass: collect candidates across strategies, RANK by
                                        fit, dedupe, emit at most MAX_SUGGESTIONS_PER_PASS NEW `suggested`
                                        plans (in-lock safe; renders nothing). Re-routes drained moments.
  render_approved_stitches(led, cfg) -> renders `approved` plans into `stitch_draft` clips (M4).

Both are gated by the caller on `cfg.impact_cut` (default OFF). mining only mutates the ledger, so it
runs inside the advance transaction; the heavy render in render_approved_stitches runs LOCK-FREE."""
from __future__ import annotations
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ClipState, StitchState, StitchPlan, stitch_plan_id, Clip
from fanops.ids import child_id
from fanops.router import awaiting, stitched, CLEAN_AWAITING
from fanops.impact_cut import make_stitch_plan, STRATEGY_KEY

# A bare clip in any of these states is not a valid impact-cut base: error/retired are broken/gone, and a
# stitch_draft is itself a stitch (never stitch a stitch).
_NON_BASE_STATES = (ClipState.error, ClipState.retired, ClipState.stitch_draft)
_IMPACT_AWAITING = awaiting("impact_cut")        # "clean_awaiting_strategy:impact_cut"
_IMPACT_STITCHED = stitched("impact_cut")        # "stitch:impact_cut"
# M6 intro-tease: the second strategy. _INTRO_AWAITING moments carry the matcher's pairings on
# Moment.intro_matches; the producer pairs the TOP one. INTRO_TEASE_SECONDS is the "wait for it" tease
# length the prepend shows the intro for (the matcher picks the asset + text, not the duration in MVP).
INTRO_STRATEGY = "intro_tease"
_INTRO_AWAITING = awaiting("intro_tease")        # "clean_awaiting_strategy:intro_tease"
INTRO_TEASE_SECONDS = 2.0
# M6 retry-cap: an intro_tease plan whose lock-free prewarm keeps failing (a flaky matcher pair / unrenderable
# intro asset) is PARKED after this many failed in-lock commit passes — bounded, not retried forever. The
# prewarm runs once per advance before the commit, so each commit-with-no-warm-composite = one failed attempt.
MAX_INTRO_RENDER_ATTEMPTS = 3


def _read_fingerprint(cfg: Config, clip_id: str) -> str | None:
    """The base clip's pinned render fingerprint (from its {cid}.render.json sidecar), or None if absent/
    unreadable. None pins nothing — a later render then can't detect base drift, so it renders as-pinned."""
    try:
        return json.loads((cfg.clips / f"{clip_id}.render.json").read_text()).get("fp")
    except (OSError, json.JSONDecodeError, ValueError):
        return None


# M5: the routine pass emits at most this many NEW suggestions per pass — an anti-spam UX bound (PRD),
# NOT a cursor. Capped-out candidates stay reserved and are reconsidered next pass (they drain over passes).
MAX_SUGGESTIONS_PER_PASS = 5


def _impact_cut_candidates(led: Ledger, cfg: Config, log) -> list[tuple]:
    """Collect (plan, moment_id) impact-cut candidates from router-reserved moments WITHOUT mutating the
    ledger — the read-only mining step. Per-candidate fail-open: a strategy error on one clip logs + skips,
    never aborts the pass (PRD: a poisoned pair must not wedge the loop)."""
    out: list[tuple] = []
    for m in list(led.moments.values()):
        if (m.hook_strategy or "") != _IMPACT_AWAITING:
            continue
        src = led.sources.get(m.parent_id)
        if src is None:
            continue
        for c in list(led.clips.values()):
            if c.parent_id != m.id or c.state in _NON_BASE_STATES:
                continue
            try:
                plan = make_stitch_plan(c, m, src, base_fp=_read_fingerprint(cfg, c.id))
            except Exception as e:                       # fail-open per candidate — the pass still completes
                log("impact_cut", c.id, "warn", err=str(e)[:120]); continue
            if plan is not None:
                out.append((plan, m.id))
    return out


def _intro_tease_candidates(led: Ledger, cfg: Config, log) -> list[tuple]:
    """Collect (plan, moment_id) intro-tease candidates from router-reserved moments whose matcher pairings
    have landed (Moment.intro_matches) WITHOUT mutating the ledger. Gated on cfg.intro_tease — a stale
    reservation left by a since-disabled format must not produce plans. Pairs the TOP (best-fit) match per
    moment onto each of its bare clips; rank_score = the pairing's fit_score so it ranks against impact_cut.
    Per-candidate fail-open (a malformed match logs + skips, never aborts the pass)."""
    if not cfg.intro_tease:
        return []
    out: list[tuple] = []
    for m in list(led.moments.values()):
        if (m.hook_strategy or "") != _INTRO_AWAITING:
            continue
        matches = m.intro_matches or []
        if not matches:                                  # matcher not answered (or no usable pairing) -> benign skip
            continue
        top = matches[0]                                 # ingest sorted best-fit first
        for c in list(led.clips.values()):
            if c.parent_id != m.id or c.state in _NON_BASE_STATES:
                continue
            try:
                params = {"intro_asset_id": top["asset_id"], "tease_text": top["tease_text"],
                          "intro_seconds": INTRO_TEASE_SECONDS}
                plan = StitchPlan(id=stitch_plan_id(c.id, [top["asset_id"]], INTRO_STRATEGY, params),
                                  clip_id=c.id, strategy_key=INTRO_STRATEGY, asset_ids=[top["asset_id"]],
                                  plan_params=params, state=StitchState.suggested,
                                  base_fingerprint=_read_fingerprint(cfg, c.id),
                                  rank_score=round(float(top["fit_score"]), 4), rationale=top.get("rationale"))
            except Exception as e:                       # fail-open per candidate — the pass still completes
                log("intro_tease", c.id, "warn", err=str(e)[:120]); continue
            out.append((plan, m.id))
    return out


def _enabled(strategies, key: str) -> bool:
    """A strategy is active for this pass when no filter is given (None = all, the default the unit tests use)
    or it is in the per-format enabled set the pipeline passes (so a disabled format produces/renders nothing)."""
    return strategies is None or key in strategies


def mine_suggestions(led: Ledger, cfg: Config, log=None, strategies=None) -> Ledger:
    """The routine pairing pass (M5) — the load-bearing loop. Collect candidate stitch suggestions across
    strategies (impact_cut is the only producer today; M6 adds intro-tease), RANK by `rank_score` (desc;
    tie -> content-addressed id, deterministic), DEDUPE against the ledger (an id already present in ANY
    state is never re-emitted — `dismissed` stays terminal), and emit at most MAX_SUGGESTIONS_PER_PASS
    NEW ones this pass (anti-spam). A moment is re-routed to `stitch:<strategy>` only once ALL of its
    candidates exist in the ledger, so a capped-out aspect stays reserved and is reconsidered next pass.
    Ledger-only mutation (safe in-lock); renders nothing."""
    log = log or (lambda *a, **k: None)
    candidates: list[tuple] = []
    if _enabled(strategies, STRATEGY_KEY):
        candidates += _impact_cut_candidates(led, cfg, log)
    if _enabled(strategies, INTRO_STRATEGY):
        candidates += _intro_tease_candidates(led, cfg, log)
    fresh = [(p, mid) for (p, mid) in candidates if p.id not in led.stitch_plans]
    fresh.sort(key=lambda pm: (-(pm[0].rank_score or 0.0), pm[0].id))   # best fit first, deterministic tie-break
    for plan, _mid in fresh[:MAX_SUGGESTIONS_PER_PASS]:
        led.add_stitch_plan(plan)                        # idempotent: setdefault by content-addressed id
    # re-route a moment only when EVERY one of its candidate plans now exists (emitted now or earlier/dismissed),
    # so a capped-out candidate keeps the moment reserved for next pass instead of being silently dropped
    by_moment: dict[str, list[str]] = {}
    for plan, mid in candidates:
        by_moment.setdefault(mid, []).append(plan.id)
    for mid, plan_ids in by_moment.items():
        if all(pid in led.stitch_plans for pid in plan_ids):
            cur = led.moments[mid].hook_strategy or ""        # derive the stitched key from this moment's reservation
            if cur.startswith(CLEAN_AWAITING + ":"):          # clean_awaiting_strategy:<key> -> stitch:<key>
                led.moments[mid].hook_strategy = stitched(cur.split(":", 1)[1])
    return led


def _stitch_clip_id(plan_id: str, aspect_value: str) -> str:
    """Content-addressed id for a stitched clip — keyed on the plan + aspect so it can NEVER collide with
    the bare clip's child_id("clip", moment, aspect) (a stitch is a new clip, never an in-place swap)."""
    return child_id("stitch", plan_id, aspect_value)

def _cut_in_range(params: dict, src) -> bool:
    """A plan's cut window is renderable iff 0 <= cut_start < cut_end and (when the source duration is
    known) cut_end does not run past the end of the source. Guards a malformed plan (or a base that got
    shorter since approval) -> the caller errors it 'cut out of range' instead of producing a bad clip."""
    try:
        cs = float(params.get("cut_start")); ce = float(params.get("cut_end"))
    except (TypeError, ValueError):
        return False
    if cs < 0 or ce <= cs:
        return False
    dur = getattr(src, "duration", None)
    return not (dur and ce > float(dur))

def _approved_impact_plans(led: Ledger):
    return [p for p in led.stitch_plans.values()
            if p.state is StitchState.approved and p.strategy_key == STRATEGY_KEY]

def approved_impact_cut_count(led: Ledger) -> int:
    """How many impact-cut plans are approved-but-not-yet-rendered. Used by the forward-only kill-switch:
    when the feature is OFF, the pipeline logs this count rather than silently freezing the plans."""
    return len(_approved_impact_plans(led))


def _approved_plans(led: Ledger, strategies=None):
    """Approved-but-unrendered plans (render_approved dispatches by strategy_key), filtered to the per-format
    enabled set when given — so a disabled format's approved plans are FROZEN (the forward-only kill-switch)."""
    return [p for p in led.stitch_plans.values()
            if p.state is StitchState.approved and _enabled(strategies, p.strategy_key)]


def approved_disabled_count(led: Ledger, *, enabled) -> int:
    """How many approved plans belong to a DISABLED format (strategy not in `enabled`). The pipeline logs this
    so a per-format kill-switch never silently freezes plans — they render when the format is re-enabled."""
    return sum(1 for p in led.stitch_plans.values()
               if p.state is StitchState.approved and p.strategy_key not in enabled)


def _intro_render_target(led: Ledger, cfg: Config, p: StitchPlan):
    """Resolve (base_clip, intro_source, stitch_cid, out_path) for an intro_tease plan, or None when the base
    clip or the intro asset is gone (commit then errors / waits, never raises)."""
    base = led.clips.get(p.clip_id)
    asset_id = (p.asset_ids or [None])[0]
    intro = led.sources.get(asset_id) if asset_id else None
    if base is None or intro is None or not intro.source_path:
        return None
    cid = _stitch_clip_id(p.id, base.aspect.value)
    return base, intro, cid, cfg.clips / f"{cid}.mp4"


def _intro_compose_fp(led: Ledger, base: Clip, intro, params: dict) -> str:
    """The compose fingerprint pinning a prewarmed intro composite (base + intro + params + base source dims),
    so the lock-free prewarm and the in-lock commit agree on when to adopt — mirrors clip._render_fingerprint."""
    from fanops.compose import _compose_fingerprint
    mom = led.moments.get(base.parent_id)
    src = led.sources.get(mom.parent_id) if mom else None
    w = (src.width if src else 0) or 0; h = (src.height if src else 0) or 0
    return _compose_fingerprint(base.path, intro.source_path, params, w, h)


def prewarm_approved_stitches(led: Ledger, cfg: Config, log, strategies=None) -> None:
    """Lock-free: render each approved plan's mp4 + render-fingerprint sidecar so the in-lock commit ADOPTS
    the warm output with no heavy render under the lock. Dispatches by strategy_key (impact_cut -> ffmpeg
    cut-window; intro_tease -> MoviePy compose-prepend) and skips disabled formats. Mutations to this
    throwaway `led` are discarded; only the on-disk artifacts persist. Fail-open per plan."""
    from fanops.clip import render_moment                # local import: clip imports are heavy; avoid at module load
    for p in _approved_plans(led, strategies):
        if p.strategy_key == INTRO_STRATEGY:
            _prewarm_intro(led, cfg, p, log)
        else:
            _prewarm_impact(led, cfg, p, render_moment, log)


def _prewarm_impact(led: Ledger, cfg: Config, p: StitchPlan, render_moment, log) -> None:
    base = led.clips.get(p.clip_id)
    if base is None or base.state in _NON_BASE_STATES:
        return
    try:
        cw = (p.plan_params["cut_start"], p.plan_params["cut_end"])
        render_moment(led, cfg, base.parent_id, aspect=base.aspect, cut_window=cw,
                      clip_id=_stitch_clip_id(p.id, base.aspect.value), born_state=ClipState.stitch_draft)
    except Exception as e:                                # fail-open: the commit pass renders it in-lock instead
        log("impact_cut", p.id, "warn", err=str(e)[:120])


def _prewarm_intro(led: Ledger, cfg: Config, p: StitchPlan, log) -> None:
    """Lock-free compose-prepend: produce the composite mp4 and stamp its compose-fp sidecar so the in-lock
    commit adopts it (MoviePy NEVER runs under the flock — unlike impact_cut, intro_tease has no in-lock
    fallback; an un-prewarmed plan simply waits for the next pass). Fail-open: prepend_intro already degrades
    to a base copy on any MoviePy failure, in which case no fp is stamped and the commit keeps waiting."""
    from fanops.compose import prepend_intro
    target = _intro_render_target(led, cfg, p)
    if target is None:
        return                                            # base/intro gone -> commit errors/waits
    base, intro, cid, out_path = target
    if base.state in _NON_BASE_STATES:
        return
    fp = _intro_compose_fp(led, base, intro, p.plan_params)
    if out_path.exists() and out_path.stat().st_size > 0 and _read_fingerprint(cfg, cid) == fp:
        return                                            # already warm
    try:
        ok = prepend_intro(base.path, intro.source_path, str(out_path),
                           tease_text=p.plan_params.get("tease_text", ""),
                           intro_seconds=float(p.plan_params.get("intro_seconds", INTRO_TEASE_SECONDS)),
                           log=lambda m: log("intro_tease", p.id, "warn", err=m))
        if ok:                                            # stamp the skip fingerprint ONLY on a real composite
            (cfg.clips / f"{cid}.render.json").write_text(json.dumps({"fp": fp}))
    except Exception as e:                                # belt-and-braces: prepend_intro itself fails open, so rare
        log("intro_tease", p.id, "warn", err=str(e)[:120])


def render_approved_stitches(led: Ledger, cfg: Config, strategies=None) -> Ledger:
    """In-lock commit for each approved plan (of an ENABLED format). COMMON supersede precedence (PRD, CLOSED
    requirement) runs first for every strategy: base clip gone -> `error` "base clip missing"; base fingerprint
    drifted -> auto-`dismissed` "base superseded"; a LIVE base post exists -> `error` "cannot supersede a live
    post". Then the render DISPATCHES by strategy_key (impact_cut -> render_moment cut-window; intro_tease ->
    adopt the prewarmed compose composite). A successful commit sets the plan `in_use` and RETIRES any
    still-queued base post; a failed render errors the plan (the bare clip already shipped upstream)."""
    from fanops.clip import render_moment
    for p in _approved_plans(led, strategies):
        base = _precheck(led, cfg, p)
        if base is None:
            continue                                      # precheck set the terminal state + reason
        if p.strategy_key == INTRO_STRATEGY:
            _commit_intro(led, cfg, p, base)
        else:
            _commit_impact(led, cfg, p, base, render_moment)
    return led


def _precheck(led: Ledger, cfg: Config, p: StitchPlan):
    """Strategy-agnostic correctness guards before render. Returns the base Clip to render, or None when the
    plan was terminated here (state + error_reason already set). A stitch is an ADDITIVE post — it never
    supersedes or retires the base; the bare clip and the stitch each ship (FAN-account reuse, NOT an
    artist-profile one-version-per-moment feed), so an already-published base does NOT block its stitch."""
    base = led.clips.get(p.clip_id)
    if base is None:
        p.state = StitchState.error; p.error_reason = "base clip missing"; return None
    if p.base_fingerprint is not None and _read_fingerprint(cfg, p.clip_id) != p.base_fingerprint:
        # stale-plan guard (correctness, NOT feed policy): the base clip was re-rendered since the cut was
        # planned, so the pinned window may no longer be valid -> drop this plan, a fresh one re-mines.
        p.state = StitchState.dismissed; p.error_reason = "base re-rendered since planned"; return None
    return base


def _commit_impact(led: Ledger, cfg: Config, p: StitchPlan, base: Clip, render_moment) -> None:
    mom = led.moments.get(base.parent_id)                # base.parent_id is the moment id
    if mom is None:                                      # base orphaned from its moment -> fail VISIBLE, never
        p.state = StitchState.error; p.error_reason = "moment missing"; return  # a KeyError that wedges the loop
    src = led.sources.get(mom.parent_id)
    if not _cut_in_range(p.plan_params, src):
        p.state = StitchState.error; p.error_reason = "cut out of range"; return
    cw = (p.plan_params["cut_start"], p.plan_params["cut_end"])
    _led, clip = render_moment(led, cfg, base.parent_id, aspect=base.aspect, cut_window=cw,
                               clip_id=_stitch_clip_id(p.id, base.aspect.value), born_state=ClipState.stitch_draft)
    if clip.state is ClipState.error:
        p.state = StitchState.error; p.error_reason = clip.error_reason or "stitch render failed"; return
    p.state = StitchState.in_use                          # additive: the bare base post is left to ship too


def _commit_intro(led: Ledger, cfg: Config, p: StitchPlan, base: Clip) -> None:
    """Adopt the prewarmed compose composite (NEVER renders MoviePy in-lock). An un-prewarmed plan stays
    `approved` and waits for the next lock-free prewarm; a missing intro asset errors the plan."""
    target = _intro_render_target(led, cfg, p)
    if target is None:
        p.state = StitchState.error; p.error_reason = "intro asset missing"; return
    _base, intro, cid, out_path = target
    fp = _intro_compose_fp(led, base, intro, p.plan_params)
    if out_path.exists() and out_path.stat().st_size > 0 and _read_fingerprint(cfg, cid) == fp:
        led.clips[cid] = Clip(id=cid, parent_id=base.parent_id, state=ClipState.stitch_draft,
                              path=str(out_path), aspect=base.aspect)
        p.state = StitchState.in_use                      # additive: the bare base post is left to ship too
        return
    # not warm: the prewarm ran first this pass and produced no valid composite -> a FAILED attempt. Bound the
    # retries (flaky matcher pair / unrenderable intro asset) — park at the cap instead of looping forever.
    p.render_attempts += 1
    if p.render_attempts >= MAX_INTRO_RENDER_ATTEMPTS:
        p.state = StitchState.error
        p.error_reason = f"intro compose failed after {p.render_attempts} attempts"
