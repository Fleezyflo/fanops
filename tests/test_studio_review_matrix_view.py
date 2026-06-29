# tests/test_studio_review_matrix_view.py — Slice 2b: the moment×account matrix wired into Review.
# Route-level contract: the video-bearing CARDS are the DEFAULT awaiting view (the operator must SEE the clip to
# approve); the matrix is OPT-IN (?view=matrix) because it goes structurally sparse under per-account casting;
# ?view=account keeps the pivot. Column-approve is CHANNEL-scoped (handle×platform×source), row-approve moment-scoped.
import json
import pytest
pytest.importorskip("flask")
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt

NOW = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
def _z(dt): return dt.isoformat().replace("+00:00", "Z")

def _seed(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        {"handle": "@b", "account_id": "2", "platforms": ["instagram", "tiktok"], "status": "active"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "base.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42BASECLIP")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src1", source_path="/know-time.mp4", created_at=_z(NOW)))            # newest → default focus
        led.add_source(Source(id="src0", source_path="/older.mp4", created_at=_z(NOW - timedelta(days=2))))
        led.add_moment(Moment(id="m1", parent_id="src1", content_token="0-7", start=0, end=7, reason="early", state=MomentState.clipped))
        led.add_moment(Moment(id="m2", parent_id="src1", content_token="10-20", start=10, end=20, reason="late", state=MomentState.clipped))
        led.add_moment(Moment(id="m0", parent_id="src0", content_token="0-5", start=0, end=5, reason="old", state=MomentState.clipped))
        led.add_clip(Clip(id="c1", parent_id="m1", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_clip(Clip(id="c2", parent_id="m2", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_clip(Clip(id="c0", parent_id="m0", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
        # m1: @a IG + @b IG cast; @b TikTok UNCAST (no post → "—")
        led.add_post(Post(id="p_m1_a", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram, caption="A", state=PostState.awaiting_approval, public_url=f"dryrun://p_m1_a"))
        led.add_post(Post(id="p_m1_b", parent_id="c1", account="@b", account_id="2", platform=Platform.instagram, caption="B", state=PostState.awaiting_approval, public_url=f"dryrun://p_m1_b"))
        # m2: @b on BOTH platforms
        led.add_post(Post(id="p_m2_bi", parent_id="c2", account="@b", account_id="2", platform=Platform.instagram, caption="Bi", state=PostState.awaiting_approval, public_url=f"dryrun://p_m2_bi"))
        led.add_post(Post(id="p_m2_bt", parent_id="c2", account="@b", account_id="2", platform=Platform.tiktok, caption="Bt", state=PostState.awaiting_approval, public_url=f"dryrun://p_m2_bt"))
        # src0 has its own awaiting post (proves the picker + that default focus is src1, not src0)
        led.add_post(Post(id="p_m0_a", parent_id="c0", account="@a", account_id="1", platform=Platform.instagram, caption="Old", state=PostState.awaiting_approval, public_url=f"dryrun://p_m0_a"))

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def test_review_defaults_to_cards_with_video(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review").data.decode()
    assert 'class="review-matrix"' not in html                       # the sparse matrix is no longer the default
    assert "<video" in html                                          # the master clip player is on the DEFAULT view — approve what you can SEE
    assert 'class="button active" aria-current="page">Moments' in html  # the cards (Moments) toggle is the active default

def test_matrix_view_renders_table(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review?view=matrix").data.decode()
    assert 'class="review-matrix"' in html and "<table" in html      # opt-in matrix is a REAL table
    assert 'scope="col"' in html and 'scope="row"' in html           # channel cols + moment rows are header cells
    assert "0–7" in html or "0–7" in html                       # m1 window present as a row head

def test_matrix_focuses_newest_source_by_default(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review?view=matrix").data.decode()
    assert "know-time.mp4" in html                                   # src1 (newest) is focused, not src0/older.mp4

def test_matrix_uncast_channel_renders_dash(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review?view=matrix").data.decode()
    assert "—" in html                                               # m1 × @b·tiktok is uncast → em-dash cell

def test_view_list_renders_legacy_cards_not_matrix(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review?view=list").data.decode()
    assert 'class="review-matrix"' not in html                       # the escape hatch: legacy moment cards
    assert "clip-grid" in html

def test_view_account_renders_pivot_not_matrix(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review?view=account&account=@a").data.decode()
    assert 'class="review-matrix"' not in html                       # account pivot still owns ?view=account

def test_three_way_view_toggle_matrix_active(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review?view=matrix").data.decode()
    assert "view=list" in html and "view=account" in html            # the toggle offers all three modes
    assert 'class="button active" aria-current="page">Matrix' in html  # Matrix is active when explicitly selected

def test_matrix_column_approve_is_channel_scoped(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review?view=matrix").data.decode()
    # the column-approve button must carry the CHANNEL target (platform + source) so it never clears a sibling channel/source
    assert "approve-channel" in html and "ch_platform=instagram" in html and "ch_source=src1" in html

def test_matrix_row_approve_targets_moment(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review?view=matrix").data.decode()
    assert "/posts/approve-moment/m1" in html                        # row-approve hits the moment route

def test_approve_moment_route_promotes_row(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _client(cfg).post("/posts/approve-moment/m1")
    assert r.status_code == 200
    led = Ledger.load(cfg)
    assert led.posts["p_m1_a"].state is PostState.queued and led.posts["p_m1_b"].state is PostState.queued
    assert led.posts["p_m2_bi"].state is PostState.awaiting_approval  # a DIFFERENT moment is untouched

def test_approve_channel_route_promotes_one_channel(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _client(cfg).post("/posts/approve-channel?ch_account=@b&ch_platform=tiktok&ch_source=src1")
    assert r.status_code == 200
    led = Ledger.load(cfg)
    assert led.posts["p_m2_bt"].state is PostState.queued            # @b·tiktok cleared...
    assert led.posts["p_m2_bi"].state is PostState.awaiting_approval  # ...@b·instagram (sibling channel) untouched
    assert led.posts["p_m1_b"].state is PostState.awaiting_approval

def test_approve_channel_missing_source_is_rejected_not_widened(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    # column-approve with NO ch_source must be REJECTED server-side — never widen to approve @a·IG across ALL sources
    r = _client(cfg).post("/posts/approve-channel?ch_account=@a&ch_platform=instagram")
    assert r.status_code == 200
    led = Ledger.load(cfg)
    assert led.posts["p_m1_a"].state is PostState.awaiting_approval     # src1 @a·IG untouched
    assert led.posts["p_m0_a"].state is PostState.awaiting_approval     # src0 @a·IG NOT swept in (the scope bug this guards)

def test_approve_channel_missing_account_is_rejected(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = _client(cfg).post("/posts/approve-channel?ch_platform=instagram&ch_source=src1")  # no ch_account
    assert r.status_code == 200
    led = Ledger.load(cfg)
    assert led.posts["p_m1_a"].state is PostState.awaiting_approval     # nothing approved (no misleading 0-count success on a real action)

def test_matrix_source_picker_lists_all_sources(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    html = _client(cfg).get("/review?view=matrix").data.decode()
    assert "source=src1" in html and "source=src0" in html           # the picker can switch the focused source

def test_review_empty_shows_teaching_state(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": []}))
    html = _client(cfg).get("/review").data.decode()
    assert 'class="review-matrix"' not in html and "No footage yet" in html   # no source → guided empty state, never a 500
