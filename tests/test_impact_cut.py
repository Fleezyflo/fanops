# tests/test_impact_cut.py — M4 (structural-hooks): the deterministic impact-cut planner. Pure,
# no ffmpeg, no LLM, no third-party asset — it ranks a source's existing signal_peaks and computes
# a cut-before-peak window [m.start, peak_t - IMPACT_LEAD_EPS] (the "wait for it" tease ends just
# before impact). Task 1 covers the pure planner + the StitchPlan builder; render/pipeline come later.
from fanops.models import Source, Moment, Clip, MomentState, SourceState, ClipState, StitchState, StitchPlan
from fanops.impact_cut import (IMPACT_LEAD_EPS, DURATION_TOLERANCE, IMPACT_MIN_DURATION,
                               plan_impact_cut, make_stitch_plan)
from fanops.models import stitch_plan_id


def _m(start=0.0, end=18.0):
    return Moment(id="m1", parent_id="s1", state=MomentState.decided, start=start, end=end, reason="r")

def _s(peaks):
    return Source(id="s1", source_path="/x/s1.mp4", state=SourceState.signalled, signal_peaks=peaks)


def test_constants():
    assert IMPACT_LEAD_EPS == 0.4                 # the cut lands 0.4s before impact (PRD resolved decision)
    assert DURATION_TOLERANCE == 0.5              # duration-validity tolerance, in line with the snap shift
    assert IMPACT_MIN_DURATION >= 1.0             # a tease shorter than this is degenerate -> no plan

def test_picks_max_score_peak():
    # two peaks in window; the impact = the strongest (max score), cut ends EPS before it
    plan = plan_impact_cut(_m(0.0, 18.0), _s([{"t": 5.0, "score": 0.3}, {"t": 10.0, "score": 0.9}]))
    assert plan == {"cut_start": 0.0, "cut_end": round(10.0 - IMPACT_LEAD_EPS, 3)}

def test_tie_break_earliest_t():
    # equal scores -> earliest t wins (deterministic across processes)
    plan = plan_impact_cut(_m(0.0, 18.0), _s([{"t": 12.0, "score": 0.7}, {"t": 8.0, "score": 0.7}]))
    assert plan["cut_end"] == round(8.0 - IMPACT_LEAD_EPS, 3)

def test_none_when_peak_too_close_to_start():
    # peak at t=2 -> cut_end=1.6 -> span 1.6 < IMPACT_MIN_DURATION -> out of range -> no plan (benign skip)
    assert plan_impact_cut(_m(0.0, 18.0), _s([{"t": 2.0, "score": 0.9}])) is None

def test_none_when_no_peak_in_window():
    # the only peak is outside [0,18] -> nothing to cut before
    assert plan_impact_cut(_m(0.0, 18.0), _s([{"t": 99.0, "score": 0.9}])) is None

def test_skips_non_numeric_t():
    # signal_peaks is an UNVALIDATED on-disk sidecar; a bad t is skipped, never raises (mirror router)
    plan = plan_impact_cut(_m(0.0, 18.0), _s([{"t": "oops", "score": 9.0}, {"t": 10.0, "score": 0.9}]))
    assert plan["cut_end"] == round(10.0 - IMPACT_LEAD_EPS, 3)

def test_empty_peaks_is_none():
    assert plan_impact_cut(_m(0.0, 18.0), _s([])) is None

def test_make_stitch_plan_builds_suggested_plan():
    clip = Clip(id="clip_abc", parent_id="m1", path="/x/clip_abc.mp4", state=ClipState.rendered)
    plan = make_stitch_plan(clip, _m(0.0, 18.0), _s([{"t": 10.0, "score": 0.9}]), base_fp="fp123")
    assert plan is not None
    assert plan.state is StitchState.suggested
    assert plan.clip_id == "clip_abc"
    assert plan.strategy_key == "impact_cut"
    assert plan.base_fingerprint == "fp123"
    assert plan.plan_params == {"cut_start": 0.0, "cut_end": round(10.0 - IMPACT_LEAD_EPS, 3)}
    # durable, content-addressed id keyed on clip + sorted pairing inputs (NOT the render fingerprint)
    assert plan.id == stitch_plan_id("clip_abc", [], "impact_cut", plan.plan_params)

def test_make_stitch_plan_none_when_no_valid_cut():
    clip = Clip(id="clip_abc", parent_id="m1", path="/x/clip_abc.mp4", state=ClipState.rendered)
    assert make_stitch_plan(clip, _m(0.0, 18.0), _s([{"t": 2.0, "score": 0.9}]), base_fp="fp123") is None


# ---- M5: suggestions carry a rank score + a one-line rationale (the routine-loop's operator-facing value) ----
def test_make_stitch_plan_sets_rank_and_rationale():
    clip = Clip(id="clip_abc", parent_id="m1", path="/x/clip_abc.mp4", state=ClipState.rendered)
    plan = make_stitch_plan(clip, _m(0.0, 18.0), _s([{"t": 5.0, "score": 0.3}, {"t": 10.0, "score": 0.9}]),
                            base_fp="fp123")
    assert plan.rank_score == 0.9                          # ranks on the impact peak's score (deterministic)
    assert "10.0" in plan.rationale and "0.9" in plan.rationale   # human-readable WHY (peak time + score)
    assert "impact" in plan.rationale.lower()

def test_make_stitch_plan_rationale_names_audio_energy_signal():
    # Theme 1 (T1.3): when the winning peak is an energy-scored speech_resume, the operator-facing
    # rationale must say AUDIO-ENERGY (not the generic "impact peak"), so the reviewer can see the
    # cut now tracks a real loudness drop, not a visual cut.
    clip = Clip(id="c1", parent_id="m1", state=ClipState.rendered, path="/x/c.mp4", duration=18.0)
    src = _s([{"t": 5.0, "kind": "scene_cut", "score": 0.3},
              {"t": 10.0, "kind": "speech_resume", "score": 0.95, "energy": 0.95}])
    plan = make_stitch_plan(clip, _m(0.0, 18.0), src, base_fp="fp")
    assert "audio-energy" in plan.rationale.lower()
    assert "10.0" in plan.rationale                       # the winning peak time
    assert plan.rank_score == 0.95                        # ranks on the energy-derived score

def test_make_stitch_plan_rationale_keeps_impact_wording_for_scene_peak():
    # A scene-cut winner (no energy field) keeps the existing "impact peak ... (score ...)" wording —
    # no behavior change on the non-energy path.
    clip = Clip(id="c1", parent_id="m1", state=ClipState.rendered, path="/x/c.mp4", duration=18.0)
    src = _s([{"t": 10.0, "kind": "scene_cut", "score": 0.42}])
    plan = make_stitch_plan(clip, _m(0.0, 18.0), src, base_fp="fp")
    low = plan.rationale.lower()
    assert "impact peak" in low and "audio-energy" not in low
    assert "0.42" in plan.rationale

def test_make_stitch_plan_rationale_is_none_safe_default():
    # a plain StitchPlan (no rationale supplied) still constructs (optional field, rides default — no migration)
    p = StitchPlan(id="x", clip_id="c", strategy_key="impact_cut")
    assert p.rationale is None and p.rank_score is None
