# tests/test_p4_dim_bias.py — P4(b) cross-account reach dim-bias actuator (amplify-only, validation-frozen).
import ast
import json
import pathlib
from fanops.agentstep import request_path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Post, Platform, PostState, Source, Moment, Clip, SourceState, MomentState)
from fanops.p4_dim_bias import dim_bias_candidates, apply_p4_dim_bias


def _dim_post(led, pid, ffk, reach, state=PostState.analyzed):
    led.add_post(Post(id=pid, parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=state, first_frame_kind=ffk, metrics={"reach": reach}, public_url="dryrun://c1"))

def _seed_lineage(led, *, source_id="s1", clip_id="c1", moment_id="m1"):
    led.add_source(Source(id=source_id, source_path="x.mp4", state=SourceState.transcribed,
                          duration=10.0, transcript=[], language="en"))
    led.add_moment(Moment(id=moment_id, parent_id=source_id, start=0.0, end=4.0, reason="r",
                          transcript_excerpt="ex"))
    led.add_clip(Clip(id=clip_id, parent_id=moment_id, path=f"{clip_id}.mp4"))

def _validate(cfg):
    from fanops import cutover
    cutover._save_state(cfg, {"metrics_confirmed": True})   # learning_validated precondition

def _gated_led(cfg, *, visual_reach=1000.0, transcript_reach=100.0):
    # 8 visual + 8 transcript analyzed posts (clears enough_attributed_signal >=8/>=2); visual leads reach.
    led = Ledger.load(cfg)
    for i in range(8):
        _dim_post(led, f"v{i}", "visual", visual_reach)
    for i in range(8):
        _dim_post(led, f"t{i}", "transcript", transcript_reach)
    _seed_lineage(led)                                       # v0 (lowest id) -> c1 -> m1 -> s1
    return led

def _frozen(led):
    return json.dumps({
        "sources": {k: v.model_dump() for k, v in led.sources.items()},
        "moments": {k: v.model_dump() for k, v in led.moments.items()},
        "clips": {k: v.model_dump() for k, v in led.clips.items()},
        "posts": {k: v.model_dump() for k, v in led.posts.items()},
    }, sort_keys=True, default=str)


# ---- B1: dim_bias_candidates — pure, per-dim p4_unlocked gated, reach-first, comparative ----
def test_no_candidate_without_cutover(tmp_path):
    cfg = Config(root=tmp_path); led = _gated_led(cfg)        # signal present but NOT validated
    assert dim_bias_candidates(led, cfg) == []

def test_higher_reach_value_is_the_candidate_once_unlocked(tmp_path):
    cfg = Config(root=tmp_path); led = _gated_led(cfg); _validate(cfg)
    cands = dim_bias_candidates(led, cfg)
    assert len(cands) == 1
    c = cands[0]
    assert c["dim"] == "first_frame_kind" and c["winning_value"] == "visual"
    assert c["post_id"] == "v0"                               # lowest-id representative (deterministic)

def test_insufficient_signal_no_candidate(tmp_path):
    # validated but only 3 posts per value (< the >=8 attributed-signal floor) -> still []
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for i in range(3): _dim_post(led, f"v{i}", "visual", 1000.0)
    for i in range(3): _dim_post(led, f"t{i}", "transcript", 100.0)
    _validate(cfg)
    assert dim_bias_candidates(led, cfg) == []

def test_reach_gap_too_small_no_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_P4_MIN_REACH_GAP", "10000")   # demand a 10k-impression lead
    cfg = Config(root=tmp_path); led = _gated_led(cfg, visual_reach=1000.0, transcript_reach=100.0)
    _validate(cfg)
    assert dim_bias_candidates(led, cfg) == []               # gap 900 < 10000 -> no clear lead

def test_p4_min_reach_gap_rejects_negative(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_P4_MIN_REACH_GAP", "-5")
    assert Config(root=tmp_path).p4_min_reach_gap == 0.0      # negative -> default (no anti-lead emission)

def test_exact_reach_tie_no_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_P4_MIN_REACH_GAP", "0")
    cfg = Config(root=tmp_path); led = _gated_led(cfg, visual_reach=1000.0, transcript_reach=1000.0)
    _validate(cfg)
    assert dim_bias_candidates(led, cfg) == []

def test_reach_gap_exactly_at_threshold_emits_winner(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_P4_MIN_REACH_GAP", "10000")
    cfg = Config(root=tmp_path); led = _gated_led(cfg, visual_reach=10000.0, transcript_reach=0.0)
    _validate(cfg)
    cands = dim_bias_candidates(led, cfg)
    assert len(cands) == 1 and cands[0]["winning_value"] == "visual"


# ---- B3: apply_p4_dim_bias — amplify-only, never retires ----
def test_apply_amplifies_winning_dim_source_no_retire(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_P4_DIM_BIAS", "1")
    cfg = Config(root=tmp_path); led = _gated_led(cfg); _validate(cfg)
    apply_p4_dim_bias(led, cfg)
    assert led.sources["s1"].state is SourceState.moments_requested
    payload = json.loads(request_path(cfg, "moments", "s1").read_text())
    assert "visual" in payload["guidance"] and "first frame kind" in payload["guidance"]
    assert payload["guidance"].startswith("AMPLIFY:")        # base amplify guidance still leads; dim is a SUFFIX
    assert int(led.sources["s1"].meta.get("amplify_count", 0)) == 1
    # AMPLIFY-ONLY: nothing retired/deleted, the representative post survives analyzed.
    assert not led.is_retired_clip("c1")
    assert led.moments["m1"].state is not MomentState.retired
    assert led.posts["v0"].state is PostState.analyzed


# ---- B5: byte-identical CONTENT when the kill switch is OFF (mutation sentinel) ----
def test_inert_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_P4_DIM_BIAS", raising=False)
    cfg = Config(root=tmp_path); led = _gated_led(cfg); _validate(cfg)   # fully gated but flag OFF
    before = _frozen(led)
    apply_p4_dim_bias(led, cfg)
    assert _frozen(led) == before                            # default OFF -> ledger content untouched

def test_apply_failsafe_logs_the_failing_dim(tmp_path, monkeypatch, mocker):
    # fail-SAFE, not fail-silent (review fix): an amplify error is contained, never propagates, and the
    # log names WHICH dim failed (so 'one amplified, one failed' is distinguishable from 'zero amplified').
    monkeypatch.setenv("FANOPS_P4_DIM_BIAS", "1")
    cfg = Config(root=tmp_path); led = _gated_led(cfg); _validate(cfg)
    mocker.patch("fanops.p4_dim_bias.amplify", side_effect=RuntimeError("boom"))
    apply_p4_dim_bias(led, cfg)                               # must NOT raise
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "p4_dim_bias" in log and "first_frame_kind" in log   # the failing dim is named, not a generic '-'

def test_inert_until_learning_validated(tmp_path, monkeypatch):
    # flag ON but NO cutover.json -> inert (validation-frozen) and logs skipped_unvalidated (not silent).
    monkeypatch.setenv("FANOPS_P4_DIM_BIAS", "1")
    cfg = Config(root=tmp_path); led = _gated_led(cfg)       # no _validate(cfg)
    before = _frozen(led)
    apply_p4_dim_bias(led, cfg)
    assert _frozen(led) == before
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "skipped_unvalidated" in log


# ---- B6: AST — p4_dim_bias is AMPLIFY-ONLY (references no retire/cascade name, even via string/alias) ----
_FORBIDDEN = ("retire", "_delete_moment_cascade", "retire_clip", "set_moment_state", "set_clip_state")

def _all_referenced_names(src_path):
    tree = ast.parse(src_path.read_text())
    found = set()
    for sub in ast.walk(tree):
        if isinstance(sub, ast.Attribute):
            found.add(sub.attr)
        elif isinstance(sub, ast.Name):
            found.add(sub.id)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            found.add(sub.value)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            for n in sub.names:
                found.add(n.name)
                if n.asname:
                    found.add(n.asname)
    return found

def test_p4_dim_bias_never_touches_retire_or_cascade():
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "fanops"
    leaked = sorted(_all_referenced_names(root / "p4_dim_bias.py") & set(_FORBIDDEN))
    assert not leaked, f"p4_dim_bias must never reference retire/cascade; found: {leaked}"


# ---- B7: cmd_p4_bias — one transaction, clean inert run on an empty ledger ----
def test_cmd_p4_bias_inert_on_empty_ledger(tmp_path, monkeypatch, capsys):
    import fanops.cli as cli
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path); Ledger.load(cfg).save()
    assert cli.cmd_p4_bias(cfg) == 0
    assert "p4-bias: 0 source(s) amplified" in capsys.readouterr().out
