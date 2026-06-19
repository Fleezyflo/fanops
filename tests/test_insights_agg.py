"""P3 T2: attribute REACH (not engagement-skewed lift) to the creative dims P1 stamps. aggregate_by_dim
groups analyzed posts by one stamped dim and reports raw reach + count + engagement context per value —
the INPUT P4 later ranks. Reach-first by construction: lift_score weights reach 0.001 (inert), so the
surface must read the raw `reach` metric, never lift_score. Pure + empty-safe; no learner here."""
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.digest import aggregate_by_dim


def _analyzed(pid, *, hook_pattern=None, reach=0.0, saves=0.0, platform=Platform.instagram,
              clip_profile=None, state=PostState.analyzed):
    return Post(id=pid, parent_id="c1", account="@a", account_id="1", platform=platform,
                caption="x", state=state, hook_pattern=hook_pattern, clip_profile=clip_profile,
                metrics={"reach": reach, "saves": saves, "lift_score": saves * 4.0 + reach * 0.001})

def test_aggregate_by_dim_groups_reach_per_value(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(_analyzed("p1", hook_pattern="open_loop", reach=1000, saves=10))
    led.add_post(_analyzed("p2", hook_pattern="open_loop", reach=3000, saves=20))
    led.add_post(_analyzed("p3", hook_pattern="curiosity", reach=500, saves=5))
    agg = aggregate_by_dim(led, "hook_pattern")
    assert set(agg) == {"open_loop", "curiosity"}
    assert agg["open_loop"]["n"] == 2
    assert agg["open_loop"]["reach_mean"] == 2000.0
    assert agg["open_loop"]["reach_sum"] == 4000.0
    assert agg["curiosity"]["n"] == 1 and agg["curiosity"]["reach_mean"] == 500.0

def test_aggregate_by_dim_skips_posts_without_the_dim(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(_analyzed("p1", hook_pattern="pov", reach=900))
    led.add_post(_analyzed("p2", hook_pattern=None, reach=9999))   # no dim -> excluded
    agg = aggregate_by_dim(led, "hook_pattern")
    assert set(agg) == {"pov"} and agg["pov"]["n"] == 1

def test_aggregate_by_dim_only_analyzed_posts(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(_analyzed("p1", hook_pattern="proof", reach=800))
    led.add_post(_analyzed("p2", hook_pattern="proof", reach=999, state=PostState.queued))  # not analyzed
    agg = aggregate_by_dim(led, "hook_pattern")
    assert agg["proof"]["n"] == 1 and agg["proof"]["reach_sum"] == 800.0

def test_aggregate_by_dim_empty_safe(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    assert aggregate_by_dim(led, "hook_pattern") == {}

def test_aggregate_by_dim_ranks_by_reach_not_lift(tmp_path):
    # a value with HUGE engagement-lift but tiny reach must not outrank a high-reach value: the surface
    # reports raw reach (the committed objective), so reach_mean is what a reader/P4 ranks on.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(_analyzed("p1", hook_pattern="comment_bait", reach=100, saves=1000))   # lift huge, reach tiny
    led.add_post(_analyzed("p2", hook_pattern="contrarian", reach=5000, saves=1))       # reach high
    agg = aggregate_by_dim(led, "hook_pattern")
    assert agg["contrarian"]["reach_mean"] > agg["comment_bait"]["reach_mean"]

def test_aggregate_by_dim_works_on_clip_profile(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(_analyzed("p1", hook_pattern="pov", clip_profile="song", reach=2000))
    led.add_post(_analyzed("p2", hook_pattern="pov", clip_profile="talk", reach=400))
    agg = aggregate_by_dim(led, "clip_profile")
    assert agg["song"]["reach_mean"] == 2000.0 and agg["talk"]["reach_mean"] == 400.0


# --- P3 T4: the P4 unlock gate (plumbing AND signal) --------------------------------------------
import json
from fanops.validation_gate import enough_attributed_signal, p4_unlocked

def _seed_n(led, value, n, *, dim_value_kw):
    for i in range(n):
        led.add_post(_analyzed(f"{value}_{i}", reach=1000, **dim_value_kw))

def test_enough_signal_false_below_threshold(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _seed_n(led, "ol", 8, dim_value_kw={"hook_pattern": "open_loop"})
    _seed_n(led, "cu", 3, dim_value_kw={"hook_pattern": "curiosity"})   # only 1 value clears >=8
    assert enough_attributed_signal(led, "hook_pattern") is False

def test_enough_signal_true_when_two_values_clear(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _seed_n(led, "ol", 8, dim_value_kw={"hook_pattern": "open_loop"})
    _seed_n(led, "cu", 9, dim_value_kw={"hook_pattern": "curiosity"})
    assert enough_attributed_signal(led, "hook_pattern") is True

def test_p4_unlocked_requires_both_validation_and_signal(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _seed_n(led, "ol", 8, dim_value_kw={"hook_pattern": "open_loop"})
    _seed_n(led, "cu", 8, dim_value_kw={"hook_pattern": "curiosity"})
    assert p4_unlocked(led, cfg, "hook_pattern") is False           # signal present but not validated
    cfg.cutover_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.cutover_path.write_text(json.dumps({"metrics_confirmed": True}))
    assert p4_unlocked(led, cfg, "hook_pattern") is True            # plumbing + signal

def test_p4_unlocked_false_when_validated_but_thin(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    cfg.cutover_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.cutover_path.write_text(json.dumps({"metrics_confirmed": True}))
    _seed_n(led, "ol", 8, dim_value_kw={"hook_pattern": "open_loop"})  # only 1 value -> thin
    assert p4_unlocked(led, cfg, "hook_pattern") is False

def test_digest_surfaces_reach_by_dim_when_p4_unlocked(tmp_path):
    # #7: once plumbing is confirmed AND a dim has enough attributed signal, render_digest surfaces the
    # already-built+tested aggregate_by_dim output as a read-only "Reach by creative dim" section.
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _seed_n(led, "ol", 8, dim_value_kw={"hook_pattern": "open_loop"})
    _seed_n(led, "cu", 8, dim_value_kw={"hook_pattern": "curiosity"})
    cfg.cutover_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.cutover_path.write_text(json.dumps({"metrics_confirmed": True}))
    out = render_digest(led, cfg)
    assert "Reach by creative dim" in out
    assert "open_loop" in out and "curiosity" in out

def test_digest_hides_reach_by_dim_when_gated(tmp_path):
    # #7: gated per dim — no confirmed plumbing (no cutover.json) -> section absent (byte-identical to today).
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _seed_n(led, "ol", 8, dim_value_kw={"hook_pattern": "open_loop"})
    _seed_n(led, "cu", 8, dim_value_kw={"hook_pattern": "curiosity"})
    out = render_digest(led, cfg)                                   # no cutover.json -> not validated -> hidden
    assert "Reach by creative dim" not in out
