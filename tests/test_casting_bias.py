# tests/test_casting_bias.py — Leg 3 Task 4 (the heaviest, PR-B): casting SELECTION gets the reach prior
# it never had. A per-(account, clip_profile) reach winner leans the LLM casting gate toward accounts with
# PROVEN reach on that content type — mirroring casting._learned_account_signal (a READ-ONLY, validation-
# frozen brief hint), NOT a ledger mutation. Bias-ONLY (C1: never retires/cascades/tracks), own kill switch
# (default OFF), validation-frozen (inert until learning_validated), fail-SAFE (exception -> byte-identical).
# The explore-guard (crux #6) is load-bearing: the prior NUDGES an otherwise-tie, it can NEVER remove an
# account from the pool nor starve an under-exposed one — every active account keeps getting cast so it can
# prove itself (no reach-monoculture). An under-exposed (account, type) cell is OMITTED from the prior
# (unproven != losing), never emitted as a negative.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState


def _post(led, pid, *, account="@a", profile="talk", reach=0.0, state=PostState.analyzed):
    led.add_post(Post(id=pid, parent_id="c1", account=account, account_id="1", platform=Platform.instagram,
                      caption="x", state=state, metrics={"reach": reach}, public_url="dryrun://c1",
                      clip_profile=profile))


def _validate(cfg):
    # stamp the plumbing half of the gate (mirrors what a real non-degraded live metric auto-confirms)
    from fanops import cutover
    cutover._save_state(cfg, {"metrics_confirmed": True})


def _seed_two_accounts(led, *, winner="@a", loser="@b", profile="talk"):
    # @winner has PROVEN high reach on `profile`; @loser is proven-low. Both clear the min attributed floor.
    for i in range(8):
        _post(led, f"w{i}", account=winner, profile=profile, reach=2000.0)
    for i in range(8):
        _post(led, f"l{i}", account=loser, profile=profile, reach=50.0)


# ======================================================================================
# Composite reach aggregation — the (account, clip_profile) cell aggregate_by_dim can't express.
# ======================================================================================
def test_reach_by_account_type_groups_by_account_and_profile(tmp_path):
    from fanops.casting_bias import reach_by_account_type
    led = Ledger.load(Config(root=tmp_path))
    _post(led, "a1", account="@a", profile="talk", reach=1000.0)
    _post(led, "a2", account="@a", profile="talk", reach=1000.0)
    _post(led, "b1", account="@b", profile="song", reach=300.0)
    agg = reach_by_account_type(led)
    assert agg[("@a", "talk")]["n"] == 2
    assert agg[("@a", "talk")]["reach_mean"] == 1000.0
    assert agg[("@b", "song")]["reach_mean"] == 300.0


def test_reach_by_account_type_skips_unanalyzed_and_missing(tmp_path):
    from fanops.casting_bias import reach_by_account_type
    led = Ledger.load(Config(root=tmp_path))
    _post(led, "ok", account="@a", profile="talk", reach=500.0)
    _post(led, "pending", account="@a", profile="talk", reach=999.0, state=PostState.queued)  # not analyzed
    # a post with clip_profile None is skipped (no cell)
    led.add_post(Post(id="noprof", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.analyzed, metrics={"reach": 999.0}, public_url="dryrun://c1"))
    agg = reach_by_account_type(led)
    assert set(agg.keys()) == {("@a", "talk")}
    assert agg[("@a", "talk")]["n"] == 1


# ======================================================================================
# The gated prior — casting_reach_prior(led, cfg, handles) -> {handle: {profile: reach_mean}}.
# ======================================================================================
def test_casting_reach_prior_emits_proven_cells_when_unlocked(tmp_path):
    from fanops.casting_bias import casting_reach_prior
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _seed_two_accounts(led)
    _validate(cfg)
    prior = casting_reach_prior(led, cfg, ["@a", "@b"])
    # both cells proven -> both present; @a's reach dominates @b's on 'talk'
    assert prior["@a"]["talk"] == 2000.0
    assert prior["@b"]["talk"] == 50.0


def test_casting_reach_prior_frozen_until_validated(tmp_path):
    from fanops.casting_bias import casting_reach_prior
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _seed_two_accounts(led)                                # plenty of signal ...
    # ... but learning NOT validated -> the prior is EMPTY (validation-frozen, exactly like p4_unlocked).
    assert casting_reach_prior(led, cfg, ["@a", "@b"]) == {}


def test_casting_reach_prior_explore_guard_omits_underexposed(tmp_path):
    # crux #6: an account/cell with < the min attributed floor is UNPROVEN, not losing. It is OMITTED from
    # the prior (never a negative signal) so casting keeps treating it neutrally -> it still gets cast and can
    # prove itself. No reach-monoculture: the prior can only ADD a lean for a proven cell, never subtract one.
    from fanops.casting_bias import casting_reach_prior
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for i in range(8):                                     # @a: proven on talk
        _post(led, f"a{i}", account="@a", profile="talk", reach=1500.0)
    for i in range(2):                                     # @new: only 2 posts -> under-exposed cell
        _post(led, f"n{i}", account="@new", profile="talk", reach=1.0)
    _validate(cfg)
    prior = casting_reach_prior(led, cfg, ["@a", "@new"])
    assert "@a" in prior                                   # proven cell present
    assert "@new" not in prior                             # under-exposed -> OMITTED (unproven, not starved)


def test_casting_reach_prior_emits_single_proven_account(tmp_path):
    # ANNOTATE-not-rank: unlike p4/timing (which pick ONE comparative winner across >= 2 values), this hint
    # annotates each PROVEN cell. A single proven account is a valid lean ("@a reaches on talk") and IS
    # emitted — the >= 2-values gate does NOT apply. (Contrast the under-exposed guard: a cell BELOW the
    # per-cell floor is what gets omitted, not a lone proven cell.)
    from fanops.casting_bias import casting_reach_prior
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for i in range(8):
        _post(led, f"a{i}", account="@a", profile="talk", reach=1500.0)
    _validate(cfg)
    assert casting_reach_prior(led, cfg, ["@a"]) == {"@a": {"talk": 1500.0}}


def test_casting_reach_prior_fail_safe(tmp_path, monkeypatch):
    # any internal error -> the prior degrades to {} (casting stays byte-identical), never raises into the gate.
    from fanops import casting_bias
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _seed_two_accounts(led); _validate(cfg)
    monkeypatch.setattr(casting_bias, "reach_by_account_type",
                        lambda led: (_ for _ in ()).throw(RuntimeError("boom")), raising=True)
    assert casting_bias.casting_reach_prior(led, cfg, ["@a", "@b"]) == {}


# ======================================================================================
# The kill switch — cfg.casting_bias, DEFAULT OFF.
# ======================================================================================
def test_casting_bias_flag_default_off(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_CASTING_BIAS", raising=False)
    assert Config(root=tmp_path).casting_bias is False


def test_casting_bias_flag_on(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_CASTING_BIAS", "1")
    assert Config(root=tmp_path).casting_bias is True


# ======================================================================================
# The injection seam — request_moment_casting carries the reach prior ONLY when the switch is ON + unlocked,
# and NEVER removes an account from the brief (explore-guard at the wire). Byte-identical when OFF.
# ======================================================================================
def _casting_fixture(tmp_path, monkeypatch):
    # a source with a decided moment + two persona-bearing active accounts, so the casting gate opens.
    from fanops.models import Source, SourceState, Moment, MomentState
    from fanops.accounts import Accounts
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="", state=SourceState.moments_decided))
    led.add_moment(Moment(id="m1", parent_id="s1", start=0.0, end=5.0, reason="r", state=MomentState.decided,
                          transcript_excerpt="hi", signal_score=1.0))
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "persona": "voice A"},
        {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active", "persona": "voice B"}]}))
    accts = Accounts.load(cfg)
    return cfg, led, accts


def test_request_moment_casting_carries_reach_prior_when_on(tmp_path, monkeypatch):
    from fanops import casting
    cfg, led, accts = _casting_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("FANOPS_CASTING_BIAS", "1")
    _seed_two_accounts(led, winner="@a", loser="@b"); _validate(cfg)
    captured = {}
    monkeypatch.setattr(casting, "write_request",
                        lambda cfg, kind, key, payload: captured.update(payload), raising=True)
    casting.request_moment_casting(led, cfg, "s1", accts)
    assert "reach_prior" in captured                       # the prior rode into the brief
    assert captured["reach_prior"]["@a"]["talk"] == 2000.0


def test_request_moment_casting_omits_reach_prior_when_off(tmp_path, monkeypatch):
    from fanops import casting
    cfg, led, accts = _casting_fixture(tmp_path, monkeypatch)
    monkeypatch.delenv("FANOPS_CASTING_BIAS", raising=False)   # switch OFF (default)
    _seed_two_accounts(led, winner="@a", loser="@b"); _validate(cfg)
    captured = {}
    monkeypatch.setattr(casting, "write_request",
                        lambda cfg, kind, key, payload: captured.update(payload), raising=True)
    casting.request_moment_casting(led, cfg, "s1", accts)
    assert "reach_prior" not in captured                   # byte-identical to today when OFF


def test_request_moment_casting_keeps_all_accounts_when_prior_present(tmp_path, monkeypatch):
    # the wire-level explore-guard: even with a prior, BOTH personas stay in the brief. The prior leans the
    # LLM's choice; it can NEVER shrink the candidate pool (that would starve the loser account).
    from fanops import casting
    cfg, led, accts = _casting_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("FANOPS_CASTING_BIAS", "1")
    _seed_two_accounts(led, winner="@a", loser="@b"); _validate(cfg)
    captured = {}
    monkeypatch.setattr(casting, "write_request",
                        lambda cfg, kind, key, payload: captured.update(payload), raising=True)
    casting.request_moment_casting(led, cfg, "s1", accts)
    handles = {p["handle"] for p in captured["personas"]}
    assert handles == {"@a", "@b"}                          # loser NOT dropped — still cast, can still win
