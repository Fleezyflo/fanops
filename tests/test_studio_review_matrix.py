# tests/test_studio_review_matrix.py — Slice 2 (moment×account matrix). Unit tests for the data layer.
# Seeds the ADVERSARIAL shapes the fortification flagged: cast derived from POST EXISTENCE (not live
# affinities), a handle on TWO platforms → TWO channel columns, a repost stacking on one channel
# (multiplicity + deterministic lead), and a post with render_id=None (cell still renders).
import json
import pytest
pytest.importorskip("flask")
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState
from fanops.studio import views

NOW = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.isoformat().replace("+00:00", "Z")

def _cfg(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        {"handle": "@b", "account_id": "2", "platforms": ["instagram", "tiktok"], "status": "active"}]}))
    return cfg

def _seed(cfg):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src1", source_path="/know-time.mp4", created_at=_z(NOW)))
    led.add_source(Source(id="src0", source_path="/older.mp4", created_at=_z(NOW - timedelta(days=1))))  # no moments → excluded from choices
    led.add_moment(Moment(id="m2", parent_id="src1", content_token="10-20", start=10, end=20, reason="late", state=MomentState.clipped, affinities=[]))
    led.add_moment(Moment(id="m1", parent_id="src1", content_token="0-7", start=0, end=7, reason="early", state=MomentState.clipped, affinities=["@a"]))  # affinities say @a ONLY...
    led.add_clip(Clip(id="c1", parent_id="m1", path="/c1.mp4", state=ClipState.queued))
    led.add_clip(Clip(id="c2", parent_id="m2", path="/c2.mp4", state=ClipState.queued))
    # m1: @a IG and @b IG both have posts — @b is NOT in affinities, yet a post EXISTS → cell must be cast.
    led.add_post(Post(id="p_m1_a", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram, caption="A", state=PostState.awaiting_approval, render_id="r_a", public_url="dryrun://p_m1_a"))
    led.add_post(Post(id="p_m1_b", parent_id="c1", account="@b", account_id="2", platform=Platform.instagram, caption="B", state=PostState.awaiting_approval, public_url="dryrun://p_m1_b"))
    # m2: @a IG ORIGINAL + a REPOST (later created_at, epoch-suffixed) on the SAME channel → multiplicity 2, lead=repost.
    led.add_post(Post(id="p_m2_a", parent_id="c2", account="@a", account_id="1", platform=Platform.instagram, caption="A2", state=PostState.awaiting_approval, created_at=_z(NOW), public_url="dryrun://p_m2_a"))
    led.add_post(Post(id="p_m2_a_repost_999", parent_id="c2", account="@a", account_id="1", platform=Platform.instagram, caption="A2r", state=PostState.awaiting_approval, created_at=_z(NOW + timedelta(hours=1))))
    # m2: @b on BOTH platforms → two separate channel columns; the IG one has render_id=None (cell still renders).
    led.add_post(Post(id="p_m2_b_ig", parent_id="c2", account="@b", account_id="2", platform=Platform.instagram, caption="Big", state=PostState.awaiting_approval, render_id=None, public_url="dryrun://p_m2_b_ig"))
    led.add_post(Post(id="p_m2_b_tt", parent_id="c2", account="@b", account_id="2", platform=Platform.tiktok, caption="Btt", state=PostState.awaiting_approval, public_url="dryrun://p_m2_b_tt"))
    led.save()

def _matrix(cfg):
    led = Ledger.load(cfg); accts = Accounts.load(cfg)
    return views.review_matrix(led, accts, cfg, source_id="src1", now=NOW)

def test_source_choices_only_sources_with_moments(tmp_path):
    cfg = _cfg(tmp_path); _seed(cfg)
    choices = views.source_choices(Ledger.load(cfg))
    ids = [sid for sid, _ in choices]
    assert ids == ["src1"]   # src0 has no moments → excluded; src1 present

def test_rows_are_moments_sorted_by_start(tmp_path):
    cfg = _cfg(tmp_path); _seed(cfg)
    mv = _matrix(cfg)
    assert [r.moment_id for r in mv.rows] == ["m1", "m2"]   # 0–7 before 10–20
    assert mv.rows[0].window == "0–7"

def test_columns_are_handle_platform_channels(tmp_path):
    cfg = _cfg(tmp_path); _seed(cfg)
    mv = _matrix(cfg)
    cols = [(h, pf) for _, h, pf in mv.columns]
    assert ("@a", "instagram") in cols and ("@b", "instagram") in cols and ("@b", "tiktok") in cols
    assert len(mv.columns) == 3   # @b's two platforms are TWO columns, not one

def test_cast_is_post_existence_not_affinities(tmp_path):
    cfg = _cfg(tmp_path); _seed(cfg)
    mv = _matrix(cfg)
    row_m1 = next(r for r in mv.rows if r.moment_id == "m1")
    # m1.affinities == ["@a"], but @b·IG has a real post → its cell MUST be present (the grid follows posts, not affinities)
    b_ig = next(k for k, h, pf in mv.columns if h == "@b" and pf == "instagram")
    assert row_m1.cells.get(b_ig) is not None and row_m1.cells[b_ig].account == "@b"

def test_uncast_channel_is_none_cell(tmp_path):
    cfg = _cfg(tmp_path); _seed(cfg)
    mv = _matrix(cfg)
    row_m1 = next(r for r in mv.rows if r.moment_id == "m1")
    b_tt = next(k for k, h, pf in mv.columns if h == "@b" and pf == "tiktok")
    assert row_m1.cells.get(b_tt) is None   # @b never posted TikTok on m1 → uncast → renders "—"

def test_repost_multiplicity_lead_is_most_recent(tmp_path):
    cfg = _cfg(tmp_path); _seed(cfg)
    mv = _matrix(cfg)
    row_m2 = next(r for r in mv.rows if r.moment_id == "m2")
    a_ig = next(k for k, h, pf in mv.columns if h == "@a" and pf == "instagram")
    cell = row_m2.cells[a_ig]
    assert cell.multiplicity == 2 and set(cell.post_ids) == {"p_m2_a", "p_m2_a_repost_999"}
    assert cell.lead_post_id == "p_m2_a_repost_999"   # awaiting + later created_at wins

def test_render_id_none_cell_still_renders(tmp_path):
    cfg = _cfg(tmp_path); _seed(cfg)
    mv = _matrix(cfg)
    row_m2 = next(r for r in mv.rows if r.moment_id == "m2")
    b_ig = next(k for k, h, pf in mv.columns if h == "@b" and pf == "instagram")
    cell = row_m2.cells[b_ig]
    assert cell is not None and cell.is_account_cut is False   # no Render → not an account cut, but cell exists

def test_empty_source_short_circuits(tmp_path):
    cfg = _cfg(tmp_path); _seed(cfg)
    led = Ledger.load(cfg); accts = Accounts.load(cfg)
    mv = views.review_matrix(led, accts, cfg, source_id="src0", now=NOW)   # src0 has no moments
    assert mv.rows == [] and mv.columns == []
