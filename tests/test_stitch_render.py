# tests/test_stitch_render.py — M4 (structural-hooks): the impact-cut SUGGEST step. For each moment the
# M2 router reserved `clean_awaiting_strategy:impact_cut` whose bare clip exists, create a suggested
# impact-cut StitchPlan (idempotent, content-addressed, base fingerprint pinned), then re-route the
# moment `hook_strategy -> stitch:impact_cut`. Renders nothing — pure ledger mutation, safe in-lock.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, MomentState, SourceState, ClipState, StitchState
from fanops.router import awaiting, CLEAN_FINAL
from fanops.stitch_render import suggest_impact_cuts


def _seed(cfg, *, peaks, hook_strategy, clip_state=ClipState.rendered):
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path=str(cfg.sources / "s1.mp4"), state=SourceState.signalled,
                          signal_peaks=peaks, width=1920, height=1080, duration=20.0))
    led.add_moment(Moment(id="m1", parent_id="s1", state=MomentState.clipped, start=0.0, end=18.0,
                          reason="r", hook_strategy=hook_strategy))
    led.clips["clip_base"] = Clip(id="clip_base", parent_id="m1", path=str(cfg.clips / "clip_base.mp4"),
                                  state=clip_state)
    return led

def _write_fp(cfg, clip_id, fp):
    cfg.clips.mkdir(parents=True, exist_ok=True)
    (cfg.clips / f"{clip_id}.render.json").write_text(json.dumps({"fp": fp}))


def test_suggest_creates_plan_and_reroutes(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, peaks=[{"t": 12.0, "score": 0.9}], hook_strategy=awaiting("impact_cut"))
    _write_fp(cfg, "clip_base", "basefp")
    suggest_impact_cuts(led, cfg)
    plans = list(led.stitch_plans.values())
    assert len(plans) == 1
    p = plans[0]
    assert p.state is StitchState.suggested and p.clip_id == "clip_base"
    assert p.strategy_key == "impact_cut" and p.base_fingerprint == "basefp"
    assert p.plan_params == {"cut_start": 0.0, "cut_end": 11.6}
    assert led.moments["m1"].hook_strategy == "stitch:impact_cut"     # format handler acted

def test_suggest_is_idempotent(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, peaks=[{"t": 12.0, "score": 0.9}], hook_strategy=awaiting("impact_cut"))
    _write_fp(cfg, "clip_base", "basefp")
    suggest_impact_cuts(led, cfg)
    led.moments["m1"].hook_strategy = awaiting("impact_cut")          # force a re-scan of the same moment
    suggest_impact_cuts(led, cfg)
    assert len(led.stitch_plans) == 1                                # content-addressed dedup, no duplicate

def test_suggest_does_not_reemit_dismissed(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, peaks=[{"t": 12.0, "score": 0.9}], hook_strategy=awaiting("impact_cut"))
    _write_fp(cfg, "clip_base", "basefp")
    suggest_impact_cuts(led, cfg)
    pid = next(iter(led.stitch_plans))
    led.dismiss_stitch_plan(pid)
    led.moments["m1"].hook_strategy = awaiting("impact_cut")          # re-open the moment
    suggest_impact_cuts(led, cfg)
    assert led.stitch_plans[pid].state is StitchState.dismissed       # terminal — never resurrected

def test_suggest_skips_non_routed_moment(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, peaks=[{"t": 12.0, "score": 0.9}], hook_strategy=CLEAN_FINAL)
    _write_fp(cfg, "clip_base", "basefp")
    suggest_impact_cuts(led, cfg)
    assert led.stitch_plans == {}

def test_suggest_no_plan_when_cut_degenerate(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, peaks=[{"t": 2.0, "score": 0.9}], hook_strategy=awaiting("impact_cut"))  # cut too short
    _write_fp(cfg, "clip_base", "basefp")
    suggest_impact_cuts(led, cfg)
    assert led.stitch_plans == {}
    assert led.moments["m1"].hook_strategy == awaiting("impact_cut")  # NOT re-routed (nothing produced)

def test_suggest_ignores_stitch_draft_base(tmp_path):
    # never stitch a stitch: a stitch_draft clip is not a valid base for an impact-cut
    cfg = Config(root=tmp_path)
    led = _seed(cfg, peaks=[{"t": 12.0, "score": 0.9}], hook_strategy=awaiting("impact_cut"),
                clip_state=ClipState.stitch_draft)
    _write_fp(cfg, "clip_base", "basefp")
    suggest_impact_cuts(led, cfg)
    assert led.stitch_plans == {}
