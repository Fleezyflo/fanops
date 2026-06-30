# tests/test_review_lanes_view.py — RF6: the lanes view wired into Review (route level). ?view=lanes is
# OPT-IN and account-first; the default Review view (no ?view) must stay byte-identical (lanes is purely
# additive). _view_arg must ACCEPT 'lanes' (else it collapses to None and the branch never fires). The lane
# template + buttons are asserted here too once they exist (Task 4); this file owns the route contract.
import json
import pytest
pytest.importorskip("flask")
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState,
                           AccountSelection, SelectionMethod, account_selection_id, Fmt)

NOW = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.isoformat().replace("+00:00", "Z")


def _seed(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "base.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42BASECLIP")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src1", source_path="/know-time.mp4", created_at=_z(NOW)))
        led.add_moment(Moment(id="m0", parent_id="src1", content_token="0-7", start=0, end=7, reason="early", state=MomentState.decided))
        led.add_moment(Moment(id="m1", parent_id="src1", content_token="8-15", start=8, end=15, reason="late", state=MomentState.decided))
        led.add_clip(Clip(id="c0", parent_id="m0", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        # @a is cast on m0 (llm); @b has no selection (fans to all)
        led.add_account_selection(AccountSelection(id=account_selection_id("src1", "@a"), source_id="src1",
                                                   account="@a", moment_ids=["m0"], method=SelectionMethod.llm))
        led.add_post(Post(id="p_a_m0", parent_id="c0", account="@a", account_id="1", platform=Platform.instagram, caption="A", state=PostState.awaiting_approval, public_url="dryrun://p_a_m0"))

def _seed_with_post(cfg):
    # like _seed but @a's m0 post carries clip_profile="long" so its lane row renders the length-band spec chip.
    _seed(cfg)
    with Ledger.transaction(cfg) as led:
        p = led.posts["p_a_m0"]
        led.posts["p_a_m0"] = p.model_copy(update={"clip_profile": "long"})   # band_for('long') -> "28–45s"

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


def test_view_arg_accepts_lanes(tmp_path):
    # the wiring linchpin: ?view=lanes must survive _view_arg, else the lanes branch is dead.
    from fanops.studio.app import create_app, _view_arg
    app = create_app(Config(root=tmp_path))
    with app.test_request_context("/review?view=lanes"):
        assert _view_arg() == "lanes"

def test_lanes_view_returns_200(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _client(cfg).get("/review?view=lanes&source=src1")
    assert r.status_code == 200                                  # the new branch builds without a 500

def test_default_view_has_no_lane_markup(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review").data.decode()
    assert "account-lanes" not in html                          # lanes is opt-in — the default view is untouched

def test_lanes_view_renders_a_lane_per_account(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review?view=lanes&source=src1").data.decode()
    assert "account-lanes" in html                              # the lanes container rendered
    assert "@a" in html and "@b" in html                        # both active accounts get a lane (incl. zero-post @b)

def test_lanes_view_shows_cast_and_uncast_controls(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review?view=lanes&source=src1").data.decode()
    # @a is cast on m0 -> an UNCAST button (− uncast) hitting cast/remove; an uncast row -> a + cast button.
    assert "/cast/remove/m0" in html and "/cast/add/" in html
    assert "view=lanes" in html                                 # the buttons carry view=lanes (scope-stable re-render)
    # #3: the account rides a DISTINCT cast_account arg, NOT the global ?account= filter (no scope bleed).
    assert "cast_account=@a" in html                            # @a's lane buttons target @a via the distinct arg
    assert "&amp;account=@" not in html                         # clicking must not set the global account filter

def test_lanes_row_shows_spec_chips_for_a_cast_post(tmp_path):
    # #1: a lane row that HAS a post renders the per-account spec chips (length band) like the cards/matrix.
    cfg = Config(root=tmp_path); _seed_with_post(cfg)
    html = _client(cfg).get("/review?view=lanes&source=src1").data.decode()
    assert "lane-spec" in html                                  # the spec-chip container rendered for the post-bearing row
    assert "28–45s" in html                                     # @a's clip_profile=long -> the length band chip

def test_lanes_view_active_in_toggle(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review?view=lanes&source=src1").data.decode()
    assert "view=lanes" in html                                 # the nav switch offers/marks lanes
