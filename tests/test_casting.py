# tests/test_casting.py — Face 3: per-account moment casting (affinities, budget, batch-bounded, fail-open).
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Batch, MomentState
from fanops.accounts import Accounts
from fanops.casting import persona_fit_score, cast_moments


def _accounts(cfg, accts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accts}))

def _acct(handle, persona="x", aid="1"):
    return {"handle": handle, "account_id": aid, "platforms": ["instagram"], "status": "active", "persona": persona}

def _moment(led, mid, *, reason="r", hook=None, signal=0.0, transcript="", batch=None):
    led.add_moment(Moment(id=mid, parent_id="src_1", content_token=mid, start=0, end=7, reason=reason,
                          hook=hook, signal_score=signal, transcript_excerpt=transcript, state=MomentState.decided))


def test_config_casting_flag_defaults_on(tmp_path):
    c = Config(root=tmp_path)
    assert c.account_casting is True            # per-account selection defaults ON (no per-account budget knob)

def test_persona_fit_score_is_deterministic_total_order():
    m1 = Moment(id="m1", parent_id="s", start=0, end=7, reason="guitar riff solo", signal_score=1.0)
    m2 = Moment(id="m2", parent_id="s", start=0, end=7, reason="guitar riff solo", signal_score=2.0)
    assert persona_fit_score("guitar music", m2) > persona_fit_score("guitar music", m1)   # tie overlap -> higher signal
    assert persona_fit_score("guitar", m1)[0] >= 1                                          # 'guitar' overlaps the corpus
    assert persona_fit_score(None, m1)[0] == 0                                              # None persona -> zero overlap

def test_cast_moments_budget_caps_per_account_by_fit(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct("@a", "guitar")])
    led = Ledger.load(cfg); led.add_source(Source(id="src_1", source_path="/s.mp4"))
    for i in range(5): _moment(led, f"m{i}", reason="guitar", signal=float(i))
    led = cast_moments(led, cfg, Accounts.load(cfg), budget=3)    # the heuristic's own cap, passed explicitly
    cast = {m.id for m in led.moments.values() if m.affinities == ["@a"]}
    uncast = {m.id for m in led.moments.values() if m.affinities == []}
    assert cast == {"m2", "m3", "m4"} and uncast == {"m0", "m1"}   # budget 3 -> top-3 by signal

def test_cast_moments_account_target_bounds(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct("@a"), _acct("@b", aid="2")])
    led = Ledger.load(cfg); led.add_source(Source(id="src_1", source_path="/s.mp4"))
    _moment(led, "m0", signal=1.0)
    led = cast_moments(led, cfg, Accounts.load(cfg), account_target=["@a"])
    assert led.moments["m0"].affinities == ["@a"]                  # @b never casts (outside the target)

def test_cast_moments_per_moment_batch_bound(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct("@a"), _acct("@b", aid="2")])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", batch_id="batch_a"))
    led.add_batch(Batch(id="batch_a", name="a", target_accounts=["@a"]))
    _moment(led, "m0", signal=1.0)
    led = cast_moments(led, cfg, Accounts.load(cfg))               # no account_target -> resolve per-moment batch
    assert led.moments["m0"].affinities == ["@a"]                  # bounded by the source's batch target

def test_cast_moments_empty_persona_still_fills_budget(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct("@a", persona="")])
    led = Ledger.load(cfg); led.add_source(Source(id="src_1", source_path="/s.mp4"))
    _moment(led, "m0", signal=1.0)
    led = cast_moments(led, cfg, Accounts.load(cfg))
    assert led.moments["m0"].affinities == ["@a"]                  # zero-overlap persona still casts by signal

def test_cast_moments_idempotent(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct("@a")])
    led = Ledger.load(cfg); led.add_source(Source(id="src_1", source_path="/s.mp4"))
    _moment(led, "m0", signal=1.0)
    led = cast_moments(led, cfg, Accounts.load(cfg)); first = list(led.moments["m0"].affinities)
    led = cast_moments(led, cfg, Accounts.load(cfg))              # already-cast skipped, deterministic re-run
    assert led.moments["m0"].affinities == first == ["@a"]

def test_cast_moments_fail_open(tmp_path, mocker):
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct("@a")])
    led = Ledger.load(cfg); led.add_source(Source(id="src_1", source_path="/s.mp4"))
    _moment(led, "m0", signal=1.0)
    mocker.patch("fanops.casting.persona_fit_score", side_effect=RuntimeError("boom"))
    out = cast_moments(led, cfg, Accounts.load(cfg))             # internal error -> return led unchanged, no raise
    assert out.moments["m0"].affinities == []


# ---- M4b: casting WRITES the durable selection FACTS (which account got which moment + WHY) ----
def test_cast_moments_writes_heuristic_selection_facts(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct("@a", "guitar")])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", batch_id="b1"))
    for i in range(3): _moment(led, f"m{i}", reason="guitar solo", signal=float(i))
    led = cast_moments(led, cfg, Accounts.load(cfg), budget=2)
    facts = {f.moment_id: f for f in led.selection_facts_of_account("@a")}
    assert set(facts) == {"m2", "m1"}                              # one fact per CAST (moment, account); m0 uncast -> no fact
    f = facts["m2"]
    assert f.method == "heuristic" and f.reason == "guitar solo"   # the editorial WHY is captured
    assert f.overlap >= 1 and f.signal == 2.0 and f.rank == 0      # the fit signal + best-pick rank
    assert facts["m1"].rank == 1                                   # second pick by signal
    assert f.source_id == "src_1" and f.batch_id == "b1" and f.created_at is not None   # lineage + audit timestamp
