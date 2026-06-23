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


def test_config_casting_flags_default_on_and_budget_six(tmp_path):
    c = Config(root=tmp_path)
    assert c.account_casting is True and c.cast_pick_budget == 6   # per-account selection defaults ON now

def test_persona_fit_score_is_deterministic_total_order():
    m1 = Moment(id="m1", parent_id="s", start=0, end=7, reason="guitar riff solo", signal_score=1.0)
    m2 = Moment(id="m2", parent_id="s", start=0, end=7, reason="guitar riff solo", signal_score=2.0)
    assert persona_fit_score("guitar music", m2) > persona_fit_score("guitar music", m1)   # tie overlap -> higher signal
    assert persona_fit_score("guitar", m1)[0] >= 1                                          # 'guitar' overlaps the corpus
    assert persona_fit_score(None, m1)[0] == 0                                              # None persona -> zero overlap

def test_cast_moments_budget_caps_per_account_by_fit(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_CAST_PICK_BUDGET", "3")            # pin the cap so the test is default-independent
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct("@a", "guitar")])
    led = Ledger.load(cfg); led.add_source(Source(id="src_1", source_path="/s.mp4"))
    for i in range(5): _moment(led, f"m{i}", reason="guitar", signal=float(i))
    led = cast_moments(led, cfg, Accounts.load(cfg))
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


# ---- exclusive routing (FANOPS_CAST_EXCLUSIVE): each moment -> single best-fit account; drop poor-fit ----

def test_config_cast_exclusive_default_off_and_env_on(tmp_path, monkeypatch):
    assert Config(root=tmp_path).cast_exclusive is False          # opt-in; unset -> off (byte-identical default)
    monkeypatch.setenv("FANOPS_CAST_EXCLUSIVE", "1")
    assert Config(root=tmp_path).cast_exclusive is True

def test_exclusive_routes_each_moment_to_single_best_fit_account(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_CAST_EXCLUSIVE", "1")
    cfg = Config(root=tmp_path)
    _accounts(cfg, [_acct("@guit", "guitar riff melody"), _acct("@drum", "drums beat rhythm", aid="2")])
    led = Ledger.load(cfg); led.add_source(Source(id="src_1", source_path="/s.mp4"))
    _moment(led, "m_g", reason="guitar riff", signal=1.0)         # fits @guit only
    _moment(led, "m_d", reason="drums beat", signal=1.0)          # fits @drum only
    led = cast_moments(led, cfg, Accounts.load(cfg))
    assert led.moments["m_g"].affinities == ["@guit"]            # routed to its single best fit, not both
    assert led.moments["m_d"].affinities == ["@drum"]

def test_exclusive_drops_moment_fitting_no_persona(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_CAST_EXCLUSIVE", "1")
    cfg = Config(root=tmp_path)
    _accounts(cfg, [_acct("@guit", "guitar riff melody"), _acct("@drum", "drums beat rhythm", aid="2")])
    led = Ledger.load(cfg); led.add_source(Source(id="src_1", source_path="/s.mp4"))
    _moment(led, "m_none", reason="qwerty zxcvb", signal=5.0)     # zero token overlap with EITHER persona
    led = cast_moments(led, cfg, Accounts.load(cfg))
    assert led.moments["m_none"].affinities == []                # fits nobody -> dropped (suppressed at crosspost)

def test_exclusive_ignores_pick_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_CAST_EXCLUSIVE", "1")
    monkeypatch.setenv("FANOPS_CAST_PICK_BUDGET", "1")            # budget mode would cap @guit at 1
    cfg = Config(root=tmp_path); _accounts(cfg, [_acct("@guit", "guitar")])
    led = Ledger.load(cfg); led.add_source(Source(id="src_1", source_path="/s.mp4"))
    for i in range(3): _moment(led, f"m{i}", reason="guitar", signal=float(i))
    led = cast_moments(led, cfg, Accounts.load(cfg))
    cast = {m.id for m in led.moments.values() if m.affinities == ["@guit"]}
    assert cast == {"m0", "m1", "m2"}                            # ALL routed (no count cap in exclusive mode)

def test_exclusive_respects_batch_target(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_CAST_EXCLUSIVE", "1")
    cfg = Config(root=tmp_path)
    _accounts(cfg, [_acct("@guit", "guitar"), _acct("@drum", "drums", aid="2")])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", batch_id="batch_d"))
    led.add_batch(Batch(id="batch_d", name="d", target_accounts=["@drum"]))   # batch targets @drum only
    _moment(led, "m_g", reason="guitar", signal=1.0)            # content fits @guit, but batch excludes @guit
    led = cast_moments(led, cfg, Accounts.load(cfg))
    # within the allowed set ({@drum}) only @drum can claim it, but "drums" persona has ZERO overlap with
    # "guitar" -> dropped. Never @guit (outside the batch target), and not force-assigned to a zero-fit @drum.
    assert led.moments["m_g"].affinities == []
