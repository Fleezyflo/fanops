# tests/test_actions_casting.py — P13 (MOL-152/153): the operator cast OVERRIDE, ported OFF the deleted durable
# AccountSelection ONTO Moment.affinities. cast_add appends a handle to a decided moment's affinities; cast_remove
# pops it. Emptying the set leaves affinities==[] (the moment fans to all — the persona-blind path). A moment is
# single-owner by CONVENTION (the picker), but the operator override may deliberately CO-OWN (the human escape hatch).
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, MomentState
from fanops.casting import affinity_admits
from fanops.studio import actions


def _seed(cfg):
    led = Ledger.load(cfg)
    led.add_source(Source(id="s", source_path="/v.mp4"))
    for mid in ("m0", "m1"):
        led.add_moment(Moment(id=mid, parent_id="s", content_token=mid, start=0, end=7, reason="r",
                              state=MomentState.decided))
    led.save()

def _mom(led, mid):
    return led.moments[mid]


def test_cast_add_writes_affinities(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = actions.cast_add(cfg, "s", "a", "m0")
    assert res.ok
    led = Ledger.load(cfg)
    assert led.moments["m0"].affinities == ["a"]               # mutated the affinities tag, not an AccountSelection
    assert affinity_admits(cfg, _mom(led, "m0"), "a") is True   # now admitted
    assert affinity_admits(cfg, _mom(led, "m0"), "b") is False  # @b not an owner -> DENY (single-owner)
    assert affinity_admits(cfg, _mom(led, "m1"), "a") is True   # m1 persona-blind -> fans to all


def test_cast_remove_pops_affinities(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    assert actions.cast_add(cfg, "s", "a", "m0").ok
    assert actions.cast_remove(cfg, "s", "a", "m0").ok
    led = Ledger.load(cfg)
    assert led.moments["m0"].affinities == []                  # emptied -> fans to all (persona-blind), no stuck record
    assert affinity_admits(cfg, _mom(led, "m0"), "a") is True   # affinities==[] -> admit all


def test_operator_override_may_co_own(tmp_path):
    # the operator override may deliberately add a SECOND owner (the human escape hatch). The picker enforces
    # single-owner; this manual path does not — affinities grows to a co-owned set.
    cfg = Config(root=tmp_path); _seed(cfg)
    assert actions.cast_add(cfg, "s", "a", "m0").ok
    assert actions.cast_add(cfg, "s", "b", "m0").ok
    led = Ledger.load(cfg)
    assert led.moments["m0"].affinities == ["a", "b"]          # co-owned
    assert affinity_admits(cfg, _mom(led, "m0"), "a") is True
    assert affinity_admits(cfg, _mom(led, "m0"), "b") is True
    assert affinity_admits(cfg, _mom(led, "m0"), "c") is False  # a third handle is still DENIED


def test_cast_handles_for_reads_affinities(tmp_path):
    # the Review cast chips: cast_handles_for reads Moment.affinities (the single gate input), operator overrides included.
    cfg = Config(root=tmp_path); _seed(cfg)
    actions.cast_add(cfg, "s", "a", "m0"); actions.cast_add(cfg, "s", "b", "m0")
    led = Ledger.load(cfg)
    assert led.cast_handles_for("s", "m0") == ["a", "b"]
    assert led.cast_handles_for("s", "m1") == []               # m1 cast to nobody -> fans to all
    actions.cast_remove(cfg, "s", "a", "m0")
    assert Ledger.load(cfg).cast_handles_for("s", "m0") == ["b"]


def test_cast_add_is_idempotent(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    assert actions.cast_add(cfg, "s", "a", "m0").ok
    assert actions.cast_add(cfg, "s", "a", "m0").ok            # re-add
    assert Ledger.load(cfg).moments["m0"].affinities == ["a"]  # sorted-set union -> no duplicate


def test_cast_add_rejects_unknown_moment(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    assert actions.cast_add(cfg, "s", "a", "nope").ok is False


def test_cast_remove_on_missing_moment_is_noop(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = actions.cast_remove(cfg, "s", "a", "gone")
    assert res.ok and res.detail.get("noop") is True


# ---- routes: the operator override reachable over HTTP (end-to-end through the Flask app) ----
def _seed_app(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "persona": "x"}]}))
    led = Ledger.load(cfg)
    led.add_source(Source(id="s", source_path="/v.mp4"))
    led.add_moment(Moment(id="m0", parent_id="s", content_token="m0", start=0, end=7, reason="r",
                          state=MomentState.decided))
    led.save()


def test_cast_add_route_mutates_ledger(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed_app(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/cast/add/m0?source=s&account=@a")
    assert r.status_code == 200
    assert Ledger.load(cfg).moments["m0"].affinities == ["a"]


def test_cast_route_requires_source_and_account(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed_app(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/cast/add/m0")                          # no source/account
    assert r.status_code == 200 and b"needs a source" in r.data
    assert Ledger.load(cfg).moments["m0"].affinities == []
