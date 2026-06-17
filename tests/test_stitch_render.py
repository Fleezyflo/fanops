# tests/test_stitch_render.py — M4 (structural-hooks): the impact-cut SUGGEST step. For each moment the
# M2 router reserved `clean_awaiting_strategy:impact_cut` whose bare clip exists, create a suggested
# impact-cut StitchPlan (idempotent, content-addressed, base fingerprint pinned), then re-route the
# moment `hook_strategy -> stitch:impact_cut`. Renders nothing — pure ledger mutation, safe in-lock.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, MomentState, SourceState, ClipState,
                           StitchState, StitchPlan, PostState, Platform, Fmt)
from fanops.router import awaiting, CLEAN_FINAL
from fanops.stitch_render import suggest_impact_cuts, render_approved_stitches, approved_impact_cut_count


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


# ---- Task 4: render APPROVED plans (lock-free in prod) into stitch_draft clips + supersede ----
def _seed_approved(cfg, *, base_fp="basefp", cur_fp="basefp"):
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path=str(cfg.sources / "s1.mp4"), state=SourceState.signalled,
                          signal_peaks=[{"t": 12.0, "score": 0.9}], width=1920, height=1080, duration=20.0))
    led.add_moment(Moment(id="m1", parent_id="s1", state=MomentState.clipped, start=0.0, end=18.0, reason="r"))
    led.clips["clip_base"] = Clip(id="clip_base", parent_id="m1", path=str(cfg.clips / "clip_base.mp4"),
                                  state=ClipState.rendered, aspect=Fmt.r9x16)
    _write_fp(cfg, "clip_base", cur_fp)
    led.add_stitch_plan(StitchPlan(id="plan1", clip_id="clip_base", strategy_key="impact_cut",
                                   plan_params={"cut_start": 0.0, "cut_end": 11.6},
                                   state=StitchState.approved, base_fingerprint=base_fp))
    return led

def _base_post(state):
    return Post(id="post_base", parent_id="clip_base", account="@a", account_id="1",
                platform=Platform.instagram, caption="c", state=state)

def _ff(mocker, *, dur=11.6):
    def fake_run(cmd, **kw):
        if not str(cmd[-1]).startswith("-"):
            from pathlib import Path
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"STITCH")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    mocker.patch("fanops.clip._probe_duration", return_value=dur)


def test_render_approved_creates_stitch_draft_and_in_use(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = _seed_approved(cfg); _ff(mocker)
    render_approved_stitches(led, cfg)
    stitches = [c for c in led.clips.values() if c.state is ClipState.stitch_draft]
    assert len(stitches) == 1 and stitches[0].id != "clip_base"
    assert led.stitch_plans["plan1"].state is StitchState.in_use

def test_render_approved_stale_fingerprint_auto_dismisses(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = _seed_approved(cfg, base_fp="OLD", cur_fp="NEW"); _ff(mocker)
    render_approved_stitches(led, cfg)
    p = led.stitch_plans["plan1"]
    assert p.state is StitchState.dismissed and "superseded" in (p.error_reason or "")
    assert not any(c.state is ClipState.stitch_draft for c in led.clips.values())  # never rendered

def test_render_approved_blocks_on_live_base_post(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = _seed_approved(cfg); _ff(mocker)
    led.posts["post_base"] = _base_post(PostState.published)            # a LIVE base post
    render_approved_stitches(led, cfg)
    p = led.stitch_plans["plan1"]
    assert p.state is StitchState.error and "live post" in (p.error_reason or "")
    assert not any(c.state is ClipState.stitch_draft for c in led.clips.values())

def test_render_approved_retires_queued_base_post(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = _seed_approved(cfg); _ff(mocker)
    led.posts["post_base"] = _base_post(PostState.queued)               # a not-yet-live base post
    render_approved_stitches(led, cfg)
    assert led.posts["post_base"].state is PostState.retired            # retired so the feed never double-posts
    assert led.stitch_plans["plan1"].state is StitchState.in_use

def test_render_approved_duration_fail_errors_plan(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = _seed_approved(cfg); _ff(mocker, dur=2.0)  # far from expected 11.6
    render_approved_stitches(led, cfg)
    p = led.stitch_plans["plan1"]
    assert p.state is StitchState.error and "duration" in (p.error_reason or "")

def test_render_approved_skips_suggested(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = _seed_approved(cfg); _ff(mocker)
    led.stitch_plans["plan1"].state = StitchState.suggested            # not approved -> not rendered
    render_approved_stitches(led, cfg)
    assert led.stitch_plans["plan1"].state is StitchState.suggested
    assert not any(c.state is ClipState.stitch_draft for c in led.clips.values())

def test_approved_impact_cut_count(tmp_path):
    cfg = Config(root=tmp_path); led = _seed_approved(cfg)
    assert approved_impact_cut_count(led) == 1
    led.stitch_plans["plan1"].state = StitchState.in_use
    assert approved_impact_cut_count(led) == 0
