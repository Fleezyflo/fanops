# tests/test_router.py — M2 (structural-hooks): the hook-strategy ROUTER, a read-only Moment-level
# classifier that runs AFTER the specificity critic and BEFORE the render loop. It annotates
# Moment.hook_strategy and renders nothing. This file covers the reserved-key registry + routing
# vocabulary (Task 1); the route_moments classifier behavior is covered below it (Task 2).
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, SourceState, MomentState
from fanops.router import STRATEGY_KEYS, TEXT, CLEAN_FINAL, CLEAN_AWAITING, awaiting, route_moments


def test_strategy_keys_registry():
    # the proven structural-hook families reserved NOW so clean_awaiting_strategy:<key> can name a
    # reserved-but-unbuilt strategy (PRD resolved decision, 2026-06-17)
    assert set(STRATEGY_KEYS) == {"impact_cut", "intro_tease", "cold_open", "open_loop",
                                  "before_after", "pov_card", "loop", "reaction"}
    assert STRATEGY_KEYS[0] == "impact_cut"          # MVP builds impact_cut first (M4)

def test_routing_reason_vocabulary():
    assert TEXT == "text" and CLEAN_FINAL == "clean_final"
    assert CLEAN_AWAITING == "clean_awaiting_strategy"
    assert awaiting("impact_cut") == "clean_awaiting_strategy:impact_cut"


# ---- Task 2: route_moments classifier ----
def _seed(cfg, *, peaks):
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path=str(cfg.sources / "s1.mp4"),
                          state=SourceState.moments_decided, signal_peaks=peaks))
    return led

def _add(led, mid, *, hook, start=0.0, end=18.0, edited=True, judged=True):
    led.add_moment(Moment(id=mid, parent_id="s1", state=MomentState.decided, start=start, end=end,
                          reason="r", hook=hook, hook_edited=edited, hook_judged=judged))

def test_route_text_when_hook_survived_critic(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg, peaks=[])
    _add(led, "m1", hook="they built the whole thing alone")
    route_moments(led, cfg)
    assert led.moments["m1"].hook_strategy == TEXT          # has a hook -> no structural strategy

def test_route_clean_awaiting_impact_cut_when_peak_in_window(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg, peaks=[{"t": 6.0, "score": 0.9}])
    _add(led, "m1", hook=None, start=0.0, end=18.0)         # clean + a peak at t=6 inside [0,18]
    route_moments(led, cfg)
    assert led.moments["m1"].hook_strategy == awaiting("impact_cut")

def test_route_clean_final_when_no_peak_in_window(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg, peaks=[{"t": 99.0, "score": 0.9}])   # peak outside [0,18]
    _add(led, "m1", hook=None, start=0.0, end=18.0)
    route_moments(led, cfg)
    assert led.moments["m1"].hook_strategy == CLEAN_FINAL

def test_route_skips_held_moment_awaiting_critic(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg, peaks=[])
    _add(led, "m1", hook="might be rejected", judged=False)  # critic verdict not yet landed
    route_moments(led, cfg, hold_judge=True)
    assert led.moments["m1"].hook_strategy is None           # not routed until the hook is final

def test_route_ignores_malformed_peak_t(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg, peaks=[{"t": "oops", "score": 0.9}])  # non-numeric t
    _add(led, "m1", hook=None, start=0.0, end=18.0)
    route_moments(led, cfg)                                  # must not raise on bad sidecar data
    assert led.moments["m1"].hook_strategy == CLEAN_FINAL    # bad peak ignored -> no reservable peak


# ---- M6 (intro-tease): a clean moment with NO usable peak is reserved for intro_tease WHEN that format is
# enabled (so the matcher can pair it with an intro asset); with intro_tease OFF it stays clean_final
# (non-regression). A peak moment still goes to impact_cut (the deterministic high-confidence format wins). ----
def test_route_clean_intro_tease_when_enabled_and_no_peak(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_INTRO_TEASE", "1")
    cfg = Config(root=tmp_path); led = _seed(cfg, peaks=[])           # clean clip, no peak
    _add(led, "m1", hook=None, start=0.0, end=18.0)
    route_moments(led, cfg)
    assert led.moments["m1"].hook_strategy == awaiting("intro_tease")

def test_route_clean_final_when_intro_tease_off(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_INTRO_TEASE", raising=False)
    cfg = Config(root=tmp_path); led = _seed(cfg, peaks=[])
    _add(led, "m1", hook=None, start=0.0, end=18.0)
    route_moments(led, cfg)
    assert led.moments["m1"].hook_strategy == CLEAN_FINAL             # off -> today's behavior unchanged

def test_route_peak_still_impact_cut_when_intro_tease_on(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_INTRO_TEASE", "1")
    cfg = Config(root=tmp_path); led = _seed(cfg, peaks=[{"t": 6.0, "score": 0.9}])
    _add(led, "m1", hook=None, start=0.0, end=18.0)                   # peak in window -> impact_cut precedence
    route_moments(led, cfg)
    assert led.moments["m1"].hook_strategy == awaiting("impact_cut")
