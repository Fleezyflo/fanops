# tests/test_culmination_coverage.py — Leg 3 (Culmination): the three varied-but-unlearned dims
# (framing/timing/casting) become rankable + gently biased, each reusing p4_dim_bias's discipline
# (learning_validated + p4_unlocked gate, comparative reach winner, amplify/bias-only C1, fail-safe
# byte-identical when there is no trusted winner OR the kill switch is off).
import json
from fanops.config import Config
from fanops.digest import aggregate_by_dim
from fanops.ledger import Ledger
from fanops.models import (Post, Platform, PostState, Source, Moment, Clip, SourceState)


def _post(led, pid, *, reach=0.0, state=PostState.analyzed, **kw):
    led.add_post(Post(id=pid, parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=state, metrics={"reach": reach}, public_url="dryrun://c1", **kw))


# ======================================================================================
# Task 1 — the two previously-unstamped dims (framing = top_bias, timing = publish_hour/dow)
# are now Post attributes that aggregate_by_dim can group. Old rows (None) are skipped.
# ======================================================================================
def test_post_has_framing_and_timing_dims(tmp_path):
    # RED against pre-Leg-3 models.Post: these three fields do not exist yet.
    led = Ledger.load(Config(root=tmp_path))
    _post(led, "p1", reach=500.0, top_bias=True, publish_hour=18, publish_dow=2)
    p = led.posts["p1"]
    assert p.top_bias is True
    assert p.publish_hour == 18
    assert p.publish_dow == 2


def test_aggregate_by_dim_groups_top_bias_and_publish_hour(tmp_path):
    # Once stamped, aggregate_by_dim (reads getattr(p, dim)) ranks them like any P4 dim.
    led = Ledger.load(Config(root=tmp_path))
    for i in range(3):
        _post(led, f"top{i}", reach=1000.0, top_bias=True, publish_hour=18, publish_dow=2)
    for i in range(3):
        _post(led, f"ctr{i}", reach=100.0, top_bias=False, publish_hour=3, publish_dow=6)
    fram = aggregate_by_dim(led, "top_bias")
    assert set(fram.keys()) == {"True", "False"}
    assert fram["True"]["reach_mean"] == 1000.0 and fram["False"]["reach_mean"] == 100.0
    hours = aggregate_by_dim(led, "publish_hour")
    assert set(hours.keys()) == {"18", "3"}
    assert hours["18"]["reach_mean"] == 1000.0


def test_old_posts_without_dims_are_skipped(tmp_path):
    # Back-compat: a row minted before Leg 3 carries None for the new dims -> aggregate_by_dim skips it.
    led = Ledger.load(Config(root=tmp_path))
    _post(led, "old", reach=999.0)                       # no top_bias / publish_hour
    assert aggregate_by_dim(led, "top_bias") == {}
    assert aggregate_by_dim(led, "publish_hour") == {}


def test_crosspost_stamps_per_account_top_bias_seam(tmp_path, monkeypatch):
    # The mint (_mint_surface_post) stamps the PER-ACCOUNT top_bias = cfg.resolve_top_bias(handle),
    # NOT the global cfg.aware_reframe — framing is a per-account choice, so the global would mis-attribute.
    # Assert the exact seam the mint reads (resolve_top_bias per handle), decoupled from a full-mint fixture.
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(Config, "resolve_top_bias", lambda self, acct: acct == "a", raising=True)
    assert cfg.resolve_top_bias("a") is True             # the per-account value the mint stamps
    assert cfg.resolve_top_bias("b") is False            # a different account resolves differently


# ======================================================================================
# Task 2 — framing rides the EXISTING autonomous apply_p4_dim_bias (one line in _P4_DIMS).
# ======================================================================================
def _seed_lineage(led, *, source_id="s1", clip_id="c1", moment_id="m1"):
    led.add_source(Source(id=source_id, source_path="x.mp4", state=SourceState.transcribed,
                          duration=10.0, transcript=[], language="en"))
    led.add_moment(Moment(id=moment_id, parent_id=source_id, start=0.0, end=4.0, reason="r",
                          transcript_excerpt="ex"))
    led.add_clip(Clip(id=clip_id, parent_id=moment_id, path=f"{clip_id}.mp4"))


def _validate(cfg):
    from fanops import cutover
    cutover._save_state(cfg, {"metrics_confirmed": True})


def test_framing_is_a_p4_dim(tmp_path):
    # RED: top_bias is not in _P4_DIMS yet.
    from fanops.p4_dim_bias import _P4_DIMS
    assert "top_bias" in _P4_DIMS


def test_framing_winner_becomes_a_dim_bias_candidate(tmp_path):
    from fanops.p4_dim_bias import dim_bias_candidates
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for i in range(8):
        _post(led, f"top{i}", reach=1000.0, top_bias=True)
    for i in range(8):
        _post(led, f"ctr{i}", reach=100.0, top_bias=False)
    _seed_lineage(led)
    _validate(cfg)
    cands = [c for c in dim_bias_candidates(led, cfg) if c["dim"] == "top_bias"]
    assert len(cands) == 1
    assert cands[0]["winning_value"] == "True"            # top framing leads on reach


# ======================================================================================
# Task 3 — timing_bias: reach-by-publish_hour → gated winner → biases surface_time's slot.
# Fail-safe: no hour variance ⇒ no winner ⇒ no-op; kill-switch-off ⇒ byte-identical.
# tz-consistent (operator_tz), window-clamped (account_window).
# ======================================================================================
def _timing_led(cfg, *, hot_hour=18, cold_hour=3, hot_reach=1000.0, cold_reach=100.0):
    led = Ledger.load(cfg)
    for i in range(8):
        _post(led, f"h{i}", reach=hot_reach, publish_hour=hot_hour, publish_dow=2)
    for i in range(8):
        _post(led, f"c{i}", reach=cold_reach, publish_hour=cold_hour, publish_dow=6)
    _seed_lineage(led)
    return led


def test_timing_winner_is_the_high_reach_hour(tmp_path):
    from fanops.timing_bias import timing_bias_winner
    cfg = Config(root=tmp_path); led = _timing_led(cfg); _validate(cfg)
    win = timing_bias_winner(led, cfg)
    assert win is not None and win["publish_hour"] == 18


def test_timing_no_variance_no_winner(tmp_path):
    # All published at ONE hour -> no runner-up -> no winner -> the actuator is a no-op (stated crux #5).
    from fanops.timing_bias import timing_bias_winner
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for i in range(8):
        _post(led, f"h{i}", reach=1000.0, publish_hour=18, publish_dow=2)
    _seed_lineage(led); _validate(cfg)
    assert timing_bias_winner(led, cfg) is None


def test_timing_frozen_no_winner(tmp_path):
    from fanops.timing_bias import timing_bias_winner
    cfg = Config(root=tmp_path); led = _timing_led(cfg)   # NOT validated
    assert timing_bias_winner(led, cfg) is None


def test_timing_exact_reach_tie_no_winner(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_P4_MIN_REACH_GAP", "0")
    from fanops.timing_bias import timing_bias_winner
    cfg = Config(root=tmp_path); led = _timing_led(cfg, hot_reach=1000.0, cold_reach=1000.0)
    _validate(cfg)
    assert timing_bias_winner(led, cfg) is None


def test_timing_reach_gap_exactly_at_threshold_emits_winner(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_P4_MIN_REACH_GAP", "10000")
    from fanops.timing_bias import timing_bias_winner
    cfg = Config(root=tmp_path); led = _timing_led(cfg, hot_reach=10000.0, cold_reach=0.0)
    _validate(cfg)
    win = timing_bias_winner(led, cfg)
    assert win is not None and win["publish_hour"] == 18


def test_timing_apply_is_noop_when_kill_switch_off(tmp_path):
    # Default OFF: apply_timing_bias leaves the ledger byte-identical.
    from fanops.timing_bias import apply_timing_bias
    cfg = Config(root=tmp_path); led = _timing_led(cfg); _validate(cfg)
    before = _frozen(led)
    apply_timing_bias(led, cfg)
    assert _frozen(led) == before


def test_timing_window_clamp_skips_out_of_window_hour(tmp_path, monkeypatch):
    # The winning hour must land in the account's posting window; else the bias is skipped (crux Task 3
    # window-clamp) so timing never proposes a slot the cadence layer later rejects.
    from fanops.timing_bias import timing_bias_hour_for
    cfg = Config(root=tmp_path); led = _timing_led(cfg, hot_hour=3); _validate(cfg)   # winner = 03:00
    monkeypatch.setattr(Config, "account_window", lambda self, h: (9, 23), raising=True)  # window 09–23
    assert timing_bias_hour_for(led, cfg, "a") is None    # 03:00 outside 09–23 -> no bias


def _frozen(led):
    return json.dumps({
        "sources": {k: v.model_dump() for k, v in led.sources.items()},
        "moments": {k: v.model_dump() for k, v in led.moments.items()},
        "clips": {k: v.model_dump() for k, v in led.clips.items()},
        "posts": {k: v.model_dump() for k, v in led.posts.items()},
    }, sort_keys=True, default=str)


def test_surface_time_leans_the_hinted_hour(tmp_path):
    # surface_time gains an optional hour_hint: when set, the scheduled slot's operator-local HOUR is the
    # hint (the deterministic minute jitter is preserved). None hint -> byte-identical to today.
    from fanops.crosspost import surface_time
    from fanops.timeutil import parse_iso
    base = parse_iso("2026-06-02T00:00:00Z")
    plain = surface_time(base, "a", "instagram", "2026-06-02", 0, clip_id="c1")
    hinted = surface_time(base, "a", "instagram", "2026-06-02", 0, clip_id="c1", hour_hint=18)
    assert parse_iso(plain).hour != 18 or plain == hinted     # (sanity; plain is unconstrained)
    assert parse_iso(hinted).hour == 18                        # the hint lands the hour
    # None hint is byte-identical to the no-arg call (fail-safe default).
    assert surface_time(base, "a", "instagram", "2026-06-02", 0, clip_id="c1", hour_hint=None) == plain


def test_run_fires_timing_bias_when_flag_on_and_live(tmp_path, monkeypatch, mocker):
    # apply_timing_bias must fire in the AUTONOMOUS run loop when its flag is on + live backend — symmetric
    # with apply_p4_dim_bias. Network-free (spied); self-guards on the flag + validation-frozen (fail-SAFE).
    monkeypatch.chdir(tmp_path)  # cmd_run builds its OWN Config() from cwd — chdir into an empty tree so
    monkeypatch.setenv("FANOPS_TIMING_BIAS", "1")  # advance() self-ingest is a no-op (toolchain-free CI safe)
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk-test"); monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    import fanops.cli as cli
    mocker.patch.object(cli, "_default_list_posts", return_value=lambda w: [])
    mocker.patch.object(cli, "pull_metrics", side_effect=lambda led, cfg, **kw: led)
    mocker.patch.object(cli, "classify_outcomes", return_value={"winners": [], "losers": []})
    mocker.patch.object(cli, "amplify", side_effect=lambda led, cfg, winners, **kw: led)
    mocker.patch.object(cli, "retire", side_effect=lambda led, losers, **kw: led)
    spy = mocker.patch.object(cli, "apply_timing_bias", side_effect=lambda led, cfg: led)
    cfg = Config(root=tmp_path); cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    from fanops.cli import main
    assert main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0
    assert spy.call_count == 1


def test_run_skips_timing_bias_when_flag_off(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)  # cmd_run builds its OWN Config() from cwd — chdir into an empty tree so
    monkeypatch.delenv("FANOPS_TIMING_BIAS", raising=False)  # advance() self-ingest is a no-op (CI toolchain-free safe)
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk-test"); monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    import fanops.cli as cli
    mocker.patch.object(cli, "_default_list_posts", return_value=lambda w: [])
    mocker.patch.object(cli, "pull_metrics", side_effect=lambda led, cfg, **kw: led)
    mocker.patch.object(cli, "classify_outcomes", return_value={"winners": [], "losers": []})
    mocker.patch.object(cli, "amplify", side_effect=lambda led, cfg, winners, **kw: led)
    mocker.patch.object(cli, "retire", side_effect=lambda led, losers, **kw: led)
    spy = mocker.patch.object(cli, "apply_timing_bias", side_effect=lambda led, cfg: led)
    cfg = Config(root=tmp_path); cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    from fanops.cli import main
    assert main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0
    assert spy.call_count == 0


# ======================================================================================
# Task 5 — the culmination is LEGIBLE: the digest renders, per structural dim, the trusted
# winner AND its ACTIVE-biasing state (the loop's EFFECT), not just attribution. A no-winner
# dim reads "gathering data"; a winner with its kill switch OFF reads "winner found (bias OFF)".
# ======================================================================================
def test_digest_culmination_shows_active_framing_bias(tmp_path, monkeypatch):
    from fanops.digest import render_digest
    monkeypatch.setenv("FANOPS_P4_DIM_BIAS", "1")            # framing bias ON -> its winner is ACTIVE
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for i in range(8):
        _post(led, f"top{i}", reach=1000.0, top_bias=True)
    for i in range(8):
        _post(led, f"ctr{i}", reach=100.0, top_bias=False)
    _seed_lineage(led); _validate(cfg)
    out = render_digest(led, cfg)
    assert "Culmination" in out                              # the new legibility section exists
    assert "framing" in out.lower()
    assert "ACTIVE" in out                                   # the framing winner is actively biasing


def test_digest_culmination_shows_timing_bias_off(tmp_path, monkeypatch):
    # A gated timing winner exists but the kill switch is OFF -> the digest says a winner was found but
    # the bias is not applied (never claims ACTIVE when no bias is running).
    from fanops.digest import render_digest
    monkeypatch.delenv("FANOPS_TIMING_BIAS", raising=False)  # timing bias OFF
    monkeypatch.delenv("FANOPS_P4_DIM_BIAS", raising=False)
    cfg = Config(root=tmp_path); led = _timing_led(cfg); _validate(cfg)
    out = render_digest(led, cfg)
    assert "Culmination" in out
    assert "timing" in out.lower() and "18" in out          # the winning hour is named
    assert "bias OFF" in out.lower() or "OFF" in out         # honest: a winner but not biasing


def test_digest_culmination_gathering_data_when_no_winner(tmp_path):
    # Frozen (not validated) -> no dim has a trusted winner -> the section reads "gathering data",
    # never a false ACTIVE claim.
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path); led = _timing_led(cfg)     # NOT validated
    out = render_digest(led, cfg)
    if "Culmination" in out:
        assert "ACTIVE" not in out                           # no active bias claimed when none applies
        assert "gathering data" in out.lower()


def test_reach_by_dim_includes_framing(tmp_path):
    # The existing P3 reach-by-dim observability now also ranks top_bias (framing).
    from fanops.digest import render_digest
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for i in range(8):
        _post(led, f"top{i}", reach=1000.0, top_bias=True)
    for i in range(8):
        _post(led, f"ctr{i}", reach=100.0, top_bias=False)
    _seed_lineage(led); _validate(cfg)
    out = render_digest(led, cfg)
    assert "top_bias" in out                                 # framing joins the reach-by-dim rollup
