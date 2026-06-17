# tests/test_studio_stitches.py — M3 (structural-hooks): the Studio approval spine. Stitch suggestions
# are operator-gated via a multi-select checkbox list — nothing posts until the operator approves.
import pytest
pytest.importorskip("flask")   # Studio is the optional [studio] extra
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import StitchPlan, StitchState
from fanops.studio import views


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def _seed_suggested(cfg, pid="sp1", strategy="impact_cut"):
    with Ledger.transaction(cfg) as led:
        led.add_stitch_plan(StitchPlan(id=pid, clip_id="clip_1", strategy_key=strategy))


# ---- read-model ----
def test_pending_stitches_lists_suggested(tmp_path):
    cfg = Config(root=tmp_path); _seed_suggested(cfg)
    plans = views.pending_stitches(cfg)
    assert [p["id"] for p in plans] == ["sp1"] and plans[0]["strategy_key"] == "impact_cut"

def test_pending_stitches_fail_open_on_absent_ledger(tmp_path):
    assert views.pending_stitches(Config(root=tmp_path)) == []   # never 500

def test_pending_stitches_excludes_non_suggested(tmp_path):
    cfg = Config(root=tmp_path); _seed_suggested(cfg, "sp1")
    with Ledger.transaction(cfg) as led:
        led.approve_stitch_plan("sp1")                            # approved -> no longer pending
    assert views.pending_stitches(cfg) == []


# ---- routes ----
def test_stitches_route_renders(tmp_path):
    cfg = Config(root=tmp_path); _seed_suggested(cfg)
    r = _client(cfg).get("/stitches")
    assert r.status_code == 200 and b"sp1" in r.data

def test_approve_selected_only(tmp_path):
    cfg = Config(root=tmp_path); _seed_suggested(cfg, "sp1"); _seed_suggested(cfg, "sp2")
    r = _client(cfg).post("/stitches/approve", data={"ids": ["sp1"]})
    assert r.status_code == 200
    led = Ledger.load(cfg)
    assert led.stitch_plans["sp1"].state is StitchState.approved   # selected -> approved
    assert led.stitch_plans["sp2"].state is StitchState.suggested  # unselected -> untouched

def test_dismiss_selected(tmp_path):
    cfg = Config(root=tmp_path); _seed_suggested(cfg, "sp1")
    r = _client(cfg).post("/stitches/dismiss", data={"ids": ["sp1"]})
    assert r.status_code == 200 and Ledger.load(cfg).stitch_plans["sp1"].state is StitchState.dismissed
