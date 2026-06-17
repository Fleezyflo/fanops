# src/fanops/impact_cut.py
"""M4 (structural-hooks): the deterministic IMPACT-CUT planner — the first concrete stitch format.

An impact-cut re-cuts a clean clip so it ends ~0.4s BEFORE the strongest signal peak (the "wait for
it" tease that stops just before the payoff). It is pure + deterministic: it ranks a source's existing
`signal_peaks` (no ffmpeg, no LLM, no third-party asset) and computes the cut window. The render override
+ approval/lifecycle live elsewhere (clip.py / stitch_render.py); this module only DECIDES the cut and
builds the `suggested` StitchPlan. It is still operator-gated (born `stitch_draft`) — we never auto-ship.

Peak ranking: the impact = the in-window peak with max `score` (tie -> earliest `t`, for cross-process
determinism). `signal_peaks` is an UNVALIDATED on-disk sidecar, so a non-numeric `t`/`score` is skipped,
never raised (mirrors router._has_peak_in_window). When the top peak yields a degenerate (too-short) cut
the planner returns None — a benign skip (no plan), NOT an error; the bare clip still ships."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional
from fanops.models import StitchPlan, StitchState, stitch_plan_id
if TYPE_CHECKING:
    from fanops.models import Source, Moment, Clip

# How far (seconds) before the impact peak the cut lands — small lead so the tease ends JUST before
# impact (PRD resolved decision, 2026-06-17). DURATION_TOLERANCE bounds the post-render duration check
# (in line with snap_window's max_shift). A cut whose span is below IMPACT_MIN_DURATION is degenerate
# (a sub-second tease is not a watchable clip) -> no plan.
IMPACT_LEAD_EPS = 0.4
DURATION_TOLERANCE = 0.5
IMPACT_MIN_DURATION = 3.0

STRATEGY_KEY = "impact_cut"


def _impact_peak_t(src: "Source", lo: float, hi: float) -> Optional[float]:
    """The time of the strongest peak inside [lo, hi] (max score, tie -> earliest t), or None if the
    window holds no usable peak. A peak with a non-numeric t or score is skipped (semi-trusted sidecar)."""
    best: Optional[tuple[float, float]] = None     # (score, -t-ranked-as-earliest) chosen below
    best_t: Optional[float] = None
    for p in src.signal_peaks or []:
        try: t = float(p.get("t")); score = float(p.get("score"))
        except (TypeError, ValueError): continue
        if not (lo <= t <= hi): continue
        # rank: higher score wins; on a tie the earlier t wins (deterministic)
        if best is None or score > best[0] or (score == best[0] and t < best[1]):
            best = (score, t); best_t = t
    return best_t


def plan_impact_cut(m: "Moment", src: "Source") -> Optional[dict]:
    """Compute the impact-cut window for a clean moment, or None when no usable cut exists.

    Returns {"cut_start", "cut_end"} where cut_start = m.start and cut_end = peak_t - IMPACT_LEAD_EPS.
    None (benign, not an error) when: no peak in [m.start, m.end], or the resulting span is shorter than
    IMPACT_MIN_DURATION (too close to the start). Deterministic and side-effect-free."""
    peak_t = _impact_peak_t(src, m.start, m.end)
    if peak_t is None:
        return None
    cut_start = round(float(m.start), 3)
    cut_end = round(peak_t - IMPACT_LEAD_EPS, 3)
    if cut_end - cut_start < IMPACT_MIN_DURATION:
        return None                                # degenerate / out of range -> no plan
    return {"cut_start": cut_start, "cut_end": cut_end}


def make_stitch_plan(clip: "Clip", m: "Moment", src: "Source", *, base_fp: Optional[str]) -> Optional[StitchPlan]:
    """Build the `suggested` StitchPlan for an impact-cut of `clip`, or None when no valid cut exists.

    The id is content-addressed on the clip id + the (empty) asset set + strategy + params, so re-emitting
    the same pairing yields the same id (dedup) while re-rendering the base never re-mints it. `base_fp`
    pins the base clip's current render fingerprint so a later re-render of the base auto-dismisses the
    plan (the supersede rule). Impact-cut uses no paired assets (asset_ids stays empty)."""
    params = plan_impact_cut(m, src)
    if params is None:
        return None
    return StitchPlan(id=stitch_plan_id(clip.id, [], STRATEGY_KEY, params), clip_id=clip.id,
                      strategy_key=STRATEGY_KEY, asset_ids=[], plan_params=params,
                      state=StitchState.suggested, base_fingerprint=base_fp)
