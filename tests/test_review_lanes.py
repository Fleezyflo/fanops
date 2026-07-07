# tests/test_review_lanes.py — RF6 (Review per-account lanes). Unit tests for the data layer.
# The lanes read-model's source of truth is the single-owner Moment.affinities (the crosspost gate input after
# the P11/MOL-152 casting teardown), NOT post existence (the matrix's rule). So it can show what the matrix
# cannot: a moment cast with no post yet, and a TARGETED account with ZERO posts. Locks: lane per active/owner
# account, per-row is_cast from the affinity set, the fans-to-all header (no moment on the source is attributed),
# the post side (lead post collapse), decided-only rows, and the empty-source guard.
import json
import pytest
pytest.importorskip("flask")
from datetime import datetime, timezone
from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState)
from fanops.studio import views

NOW = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.isoformat().replace("+00:00", "Z")


def _cfg(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"}]}))
    return cfg

def _seed_moments(cfg):
    # one source, four DECIDED moments (the lane universe of rows), plus one PICKED (hookless) moment that
    # must NOT appear as a row (rows are decided-only, mirroring the matrix's moment set).
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src1", source_path="/know-time.mp4", created_at=_z(NOW)))
        led.add_moment(Moment(id="m0", parent_id="src1", content_token="0-7", start=0, end=7, reason="guitar", state=MomentState.decided))
        led.add_moment(Moment(id="m1", parent_id="src1", content_token="8-15", start=8, end=15, reason="crowd", state=MomentState.decided))
        led.add_moment(Moment(id="m2", parent_id="src1", content_token="16-22", start=16, end=22, reason="drum", state=MomentState.decided))
        led.add_moment(Moment(id="m3", parent_id="src1", content_token="23-30", start=23, end=30, reason="outro", state=MomentState.decided))
        led.add_moment(Moment(id="mp", parent_id="src1", content_token="31-38", start=31, end=38, reason="picked", state=MomentState.picked))

def _cast(cfg, moment_id, handles):
    # P11/MOL-152: cast = Moment.affinities (the single-owner crosspost gate input the operator override mutates).
    with Ledger.transaction(cfg) as led:
        led.moments[moment_id].affinities = list(handles)

def _lanes(cfg):
    led = Ledger.load(cfg); accts = Accounts.load(cfg)
    return views.account_lanes(led, accts, cfg, source_id="src1", now=NOW)

def _lane(view, handle):
    return next(ln for ln in view.lanes if ln.account == handle)


# ---- lane per active account, zero affinities -> all rows uncast, fans_all True (the matrix CAN'T do this) ----
def test_lane_per_active_account_with_no_affinities(tmp_path):
    cfg = _cfg(tmp_path); _seed_moments(cfg)
    view = _lanes(cfg)
    assert {ln.account for ln in view.lanes} == {"a", "b"}      # both active accounts get a lane
    for ln in view.lanes:
        assert ln.fans_all is True                                # no moment attributed -> fans to all
        assert ln.method is None
        assert all(r.is_cast is False for r in ln.rows)           # every row uncast
        assert ln.cast_count == 0 and ln.moment_count == 4        # decided-only: mp excluded


# ---- only decided moments become rows (the picked/hookless mp is excluded) ----
def test_rows_are_decided_moments_only_sorted_by_start(tmp_path):
    cfg = _cfg(tmp_path); _seed_moments(cfg)
    lane = _lane(_lanes(cfg), "a")
    assert [r.moment_id for r in lane.rows] == ["m0", "m1", "m2", "m3"]   # mp (picked) absent, sorted by start
    assert lane.rows[0].window == "0–7"                                   # en-dash window, raw seconds


# ---- cast state reads from Moment.affinities, not posts ----
def test_cast_state_from_affinities(tmp_path):
    cfg = _cfg(tmp_path); _seed_moments(cfg)
    _cast(cfg, "m0", ["a"]); _cast(cfg, "m1", ["a"])
    lane = _lane(_lanes(cfg), "a")
    cast = {r.moment_id: r.is_cast for r in lane.rows}
    assert cast == {"m0": True, "m1": True, "m2": False, "m3": False}     # exactly the owned moments
    assert lane.method is None and lane.cast_count == 2 and lane.fans_all is False


# ---- a TARGETED account with NO post still gets a lane with the cast row marked (matrix can't show this) ----
def test_zero_post_targeted_account_still_has_cast_lane(tmp_path):
    cfg = _cfg(tmp_path); _seed_moments(cfg)
    _cast(cfg, "m2", ["b"])                                       # cast @b on m2 but NEVER mint a post
    lane = _lane(_lanes(cfg), "b")
    row = next(r for r in lane.rows if r.moment_id == "m2")
    assert row.is_cast is True and row.post is None              # cast is TRUE despite no post existing
    assert lane.cast_count == 1


# ---- the post side: a lead post for (@a, m0) populates row.post with its state ----
def test_post_side_populated_from_lead_post(tmp_path):
    cfg = _cfg(tmp_path); _seed_moments(cfg)
    _cast(cfg, "m0", ["a"])
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="c0", parent_id="m0", path="/c0.mp4", state=ClipState.queued))
        led.add_post(Post(id="p_a_m0", parent_id="c0", account="a", account_id="1",
                          platform=Platform.instagram, caption="A", state=PostState.awaiting_approval, public_url="dryrun://p_a_m0"))
    lane = _lane(_lanes(cfg), "a")
    row = next(r for r in lane.rows if r.moment_id == "m0")
    assert row.post is not None and row.post.state == "awaiting_approval" and row.post.account == "a"


# ---- operator cast_add adds to the affinity set (co-ownership) ----
def test_operator_cast_add_extends_affinities(tmp_path):
    cfg = _cfg(tmp_path); _seed_moments(cfg)
    from fanops.studio.actions_casting import cast_add
    _cast(cfg, "m0", ["a"])
    cast_add(cfg, "src1", "a", "m1")                            # operator adds m1 -> @a owns m0 + m1
    lane = _lane(_lanes(cfg), "a")
    assert lane.method is None and lane.cast_count == 2
    assert {r.moment_id for r in lane.rows if r.is_cast} == {"m0", "m1"}


# ---- an owner account not in accounts.json active set STILL gets a lane (affinity-only handle) ----
def test_affinity_only_account_gets_a_lane(tmp_path):
    cfg = _cfg(tmp_path); _seed_moments(cfg)
    _cast(cfg, "m3", ["ghost"])                                  # @ghost is not an active account, only an owner
    view = _lanes(cfg)
    assert "ghost" in {ln.account for ln in view.lanes}         # union includes affinity-owner accounts
    ghost = _lane(view, "ghost")
    assert next(r for r in ghost.rows if r.moment_id == "m3").is_cast is True


# ---- empty / unknown source: no rows, no crash ----
def test_unknown_source_yields_empty_view(tmp_path):
    cfg = _cfg(tmp_path); _seed_moments(cfg)
    led = Ledger.load(cfg); accts = Accounts.load(cfg)
    view = views.account_lanes(led, accts, cfg, source_id="does_not_exist", now=NOW)
    assert view.source_id == "does_not_exist"
    assert all(len(ln.rows) == 0 for ln in view.lanes)           # accounts may get lanes, but with zero rows


# ---- an attributed source DENIES a non-owner: its lane is uncast, and the source-has-chosen fans_all is False ----
def test_attributed_source_denies_non_owner(tmp_path):
    cfg = _cfg(tmp_path); _seed_moments(cfg)
    _cast(cfg, "m0", ["a"])                                       # only @a owns m0; @b owns nothing
    lane_b = _lane(_lanes(cfg), "b")
    assert lane_b.cast_count == 0 and lane_b.fans_all is False    # @b is DENIED, not silently fanned to all
    assert all(r.is_cast is False for r in lane_b.rows)


# ---- OFF-firewall: account_lanes is a PURE READ — casting=0 doesn't change the truth view, and viewing mints nothing ----
def test_off_firewall_lanes_still_render_readonly(tmp_path, monkeypatch):
    # FANOPS_ACCOUNT_CASTING flips the crosspost GATE, NOT the lanes read-model: account_lanes never reads
    # cfg.account_casting, so a recorded affinity STILL shows as cast in the truth view under OFF, every lane
    # still renders, and merely BUILDING the view mints no posts. (The gate's OFF behavior is tested elsewhere.)
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    cfg = _cfg(tmp_path); _seed_moments(cfg)
    assert cfg.account_casting is False                          # firewall is OFF for this test
    _cast(cfg, "m0", ["a"])
    view = _lanes(cfg)
    assert {ln.account for ln in view.lanes} == {"a", "b"}     # every active account still gets a lane under OFF
    a = _lane(view, "a")
    assert next(r for r in a.rows if r.moment_id == "m0").is_cast is True   # recorded cast still shown as cast (read is config-independent)
    assert a.cast_count == 1
    assert len(Ledger.load(cfg).posts) == 0                      # VIEWING is a pure read — nothing was minted
