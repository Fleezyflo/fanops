# tests/test_studio_stitches.py — M3 (structural-hooks): the Studio approval spine. Stitch suggestions
# are operator-gated via a multi-select checkbox list — nothing posts until the operator approves.
import pytest
pytest.importorskip("flask")   # Studio is the optional [studio] extra
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import StitchPlan, StitchState, Clip, ClipState, Fmt
from fanops.studio import views
from fanops.studio import actions


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

def test_pending_stitches_surfaces_intro_tease(tmp_path):
    # M6: the Stitches panel is strategy-agnostic — an intro_tease suggestion surfaces with its rank + rationale
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_stitch_plan(StitchPlan(id="it1", clip_id="clip_1", strategy_key="intro_tease",
                                       asset_ids=["intro1"], rank_score=0.9, rationale="stage entrance pairs"))
    rows = views.pending_stitches(cfg)
    assert len(rows) == 1 and rows[0]["strategy_key"] == "intro_tease"
    assert rows[0]["rationale"] == "stage entrance pairs" and rows[0]["asset_ids"] == ["intro1"]

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


# ---- Task 5: operator RELEASE of a rendered stitch_draft clip -> captioned (inherits base captions) ----
def _seed_stitch_draft(cfg, *, base_state=ClipState.captioned, caps=None):
    caps = caps if caps is not None else {"@a/instagram": {"caption": "c", "hashtags": ["#x"]}}
    with Ledger.transaction(cfg) as led:
        led.clips["clip_base"] = Clip(id="clip_base", parent_id="m1", path="/x/clip_base.mp4",
                                      aspect=Fmt.r9x16, state=base_state, meta_captions=caps)
        led.clips["stitch_x"] = Clip(id="stitch_x", parent_id="m1", path="/x/stitch_x.mp4",
                                     aspect=Fmt.r9x16, state=ClipState.stitch_draft)

def test_pending_stitch_drafts_lists_rendered_drafts(tmp_path):
    cfg = Config(root=tmp_path); _seed_stitch_draft(cfg)
    drafts = views.pending_stitch_drafts(cfg)
    assert [d["id"] for d in drafts] == ["stitch_x"]

def test_release_promotes_stitch_draft_and_inherits_captions(tmp_path):
    cfg = Config(root=tmp_path); _seed_stitch_draft(cfg)
    r = actions.release_stitches(cfg, ["stitch_x"])
    assert r.ok
    led = Ledger.load(cfg)
    c = led.clips["stitch_x"]
    assert c.state is ClipState.captioned                      # now crosspost-eligible
    assert c.meta_captions == {"@a/instagram": {"caption": "c", "hashtags": ["#x"]}}  # inherited from base

def test_release_only_touches_stitch_draft(tmp_path):
    # a non-stitch_draft clip id is never promoted by release (re-checked in-lock)
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.clips["plain"] = Clip(id="plain", parent_id="m1", path="/x/plain.mp4", state=ClipState.rendered)
    actions.release_stitches(cfg, ["plain"])
    assert Ledger.load(cfg).clips["plain"].state is ClipState.rendered

def test_release_route(tmp_path):
    cfg = Config(root=tmp_path); _seed_stitch_draft(cfg)
    r = _client(cfg).post("/stitches/release", data={"ids": ["stitch_x"]})
    assert r.status_code == 200 and Ledger.load(cfg).clips["stitch_x"].state is ClipState.captioned


# ---- M5: the routine loop's operator-facing value — rationale + rank in the approval surface ----
def test_pending_stitches_surfaces_rationale_ordered_by_rank(tmp_path):
    with Ledger.transaction(cfg := Config(root=tmp_path)) as led:
        led.add_stitch_plan(StitchPlan(id="lo", clip_id="c1", strategy_key="impact_cut",
                                       rank_score=0.3, rationale="impact peak at 4.0s (score 0.3)"))
        led.add_stitch_plan(StitchPlan(id="hi", clip_id="c2", strategy_key="impact_cut",
                                       rank_score=0.9, rationale="impact peak at 11.0s (score 0.9)"))
    plans = views.pending_stitches(cfg)
    assert [p["id"] for p in plans] == ["hi", "lo"]               # best-fit (highest rank) first
    assert plans[0]["rationale"] == "impact peak at 11.0s (score 0.9)"
    assert plans[0]["rank_score"] == 0.9

def test_stitches_route_shows_rationale(tmp_path):
    with Ledger.transaction(cfg := Config(root=tmp_path)) as led:
        led.add_stitch_plan(StitchPlan(id="sp9", clip_id="c1", strategy_key="impact_cut",
                                       rank_score=0.8, rationale="impact peak at 9.0s (score 0.8)"))
    r = _client(cfg).get("/stitches")
    assert r.status_code == 200 and b"impact peak at 9.0s" in r.data
