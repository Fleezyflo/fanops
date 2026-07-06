# tests/test_actions_casting.py — RF1 Task 6: the operator cast OVERRIDE action. The operator adds or removes
# a single (moment, account) from that account's durable AccountSelection (method=operator — a human decision
# SUPERSEDES llm/migrated). The sum-type stays honest: removing an account's LAST moment DROPS the record
# (back to "no selection" -> the gate denies it on a cast source), never an illegal empty operator row.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, MomentState, AccountSelection, SelectionMethod, account_selection_id)
from fanops.casting import account_selection_admits
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


def test_cast_add_creates_operator_selection(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = actions.cast_add(cfg, "s", "a", "m0")
    assert res.ok
    led = Ledger.load(cfg)
    sel = led.account_selection_for("s", "a")
    assert sel is not None and sel.moment_ids == ["m0"] and sel.method == SelectionMethod.operator
    assert account_selection_admits(cfg, led, _mom(led, "m0"), "a") is True    # now admitted
    assert account_selection_admits(cfg, led, _mom(led, "m1"), "a") is False   # not added -> denied


def test_cast_add_appends_and_supersedes_method(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    with Ledger.transaction(cfg) as led:                                        # start from an llm selection
        led.add_account_selection(AccountSelection(id=account_selection_id("s", "a"), source_id="s",
                                                   account="a", moment_ids=["m0"], method=SelectionMethod.llm))
    assert actions.cast_add(cfg, "s", "a", "m1").ok
    sel = Ledger.load(cfg).account_selection_for("s", "a")
    assert sel.moment_ids == ["m0", "m1"] and sel.method == SelectionMethod.operator   # human supersedes llm


def test_cast_handles_for_derives_from_account_selection_not_stored_tag(tmp_path):
    # MOM-3 ROOT: cast_handles_for derives the cast set from the durable AccountSelection an operator cast_add
    # writes — NOT the legacy Moment.affinities tag (which the override never touches), so the Review read model
    # can no longer diverge from the gate. cast_remove of the last pick drops the record -> not cast.
    cfg = Config(root=tmp_path); _seed(cfg)
    actions.cast_add(cfg, "s", "a", "m0"); actions.cast_add(cfg, "s", "b", "m0")
    led = Ledger.load(cfg)
    assert led.cast_handles_for("s", "m0") == ["a", "b"]      # derived view sees the operator override
    assert led.moments["m0"].affinities == []                  # the stored tag is NOT the source of truth
    assert led.cast_handles_for("s", "m1") == []               # m1 cast to nobody
    actions.cast_remove(cfg, "s", "a", "m0")
    assert Ledger.load(cfg).cast_handles_for("s", "m0") == ["b"]   # @a's last pick removed -> record dropped -> not cast


def test_cast_remove_drops_record_when_last_moment_removed(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    assert actions.cast_add(cfg, "s", "a", "m0").ok
    assert actions.cast_remove(cfg, "s", "a", "m0").ok
    led = Ledger.load(cfg)
    assert led.account_selection_for("s", "a") is None                         # record dropped (no illegal empty row)
    # on a CAST source (@b still has a selection) the dropped account is denied; here no other selection -> legacy fallback
    with Ledger.transaction(cfg) as led2:
        led2.add_account_selection(AccountSelection(id=account_selection_id("s", "b"), source_id="s",
                                                    account="b", moment_ids=["m1"], method=SelectionMethod.operator))
    led = Ledger.load(cfg)
    assert account_selection_admits(cfg, led, _mom(led, "m0"), "a") is False   # cast source, no record -> DENY


def test_cast_remove_keeps_other_moments(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    assert actions.cast_add(cfg, "s", "a", "m0").ok
    assert actions.cast_add(cfg, "s", "a", "m1").ok
    assert actions.cast_remove(cfg, "s", "a", "m0").ok
    sel = Ledger.load(cfg).account_selection_for("s", "a")
    assert sel.moment_ids == ["m1"] and sel.method == SelectionMethod.operator


def test_cast_add_rejects_unknown_moment(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    assert actions.cast_add(cfg, "s", "a", "nope").ok is False
    assert Ledger.load(cfg).account_selection_for("s", "a") is None


def test_cast_remove_on_missing_selection_is_noop(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = actions.cast_remove(cfg, "s", "a", "m0")
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
    sel = Ledger.load(cfg).account_selection_for("s", "a")
    assert sel is not None and sel.moment_ids == ["m0"] and sel.method == SelectionMethod.operator


def test_cast_route_requires_source_and_account(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed_app(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/cast/add/m0")                          # no source/account
    assert r.status_code == 200 and b"needs a source" in r.data
    assert Ledger.load(cfg).account_selection_for("s", "a") is None
