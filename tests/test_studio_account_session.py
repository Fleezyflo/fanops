# Account-scoped session — light UX plan: session bar, home launcher, review defaults.
import json
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt

def _accounts(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))

def _seed(cfg, n=2):
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    for i in range(n):
        cid = f"c{i}"; (cdir / f"{cid}.mp4").write_bytes(b"V")
        led.add_clip(Clip(id=cid, parent_id="m1", path=str(cdir / f"{cid}.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id=f"p{i}", parent_id=cid, account="@a", account_id="1", platform=Platform.instagram,
                          caption="c", state=PostState.awaiting_approval, public_url="dryrun://p"))
    led.save()

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def test_home_account_launcher_shows_work_counts(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, 2)
    html = _client(cfg).get("/").data.decode()
    assert "Review (2)" in html
    assert 'data-acct-awaiting="@a"' in html

def test_session_bar_on_scoped_pages(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, 1)
    html = _client(cfg).get("/review?account=@a").data.decode()
    assert "account-session-bar" in html and "@a" in html and "Review" in html and "Clear filter" in html

def test_review_defaults_to_focus_with_video(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, 1)
    html = _client(cfg).get("/review?account=@a").data.decode()
    assert "review-focus" in html and "<video" in html

def test_focus_mode_renders_player(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, 2)
    html = _client(cfg).get("/review?account=@a&focus=1").data.decode()
    assert "review-focus" in html and "1 / 2" in html

def test_rail_operator_labels(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    html = _client(cfg).get("/").data.decode()
    assert "Add &amp; run" in html and ">Blocked<" in html and "Manual publish" in html


def test_account_at_prefix_resolves_bare_handle(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "markmakmouly", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    cid = "c0"; (cdir / f"{cid}.mp4").write_bytes(b"V")
    led.add_clip(Clip(id=cid, parent_id="m1", path=str(cdir / f"{cid}.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="p0", parent_id=cid, account="markmakmouly", account_id="1", platform=Platform.instagram,
                      caption="c", state=PostState.awaiting_approval, public_url="dryrun://p"))
    led.save()
    html = _client(cfg).get("/review?account=@markmakmouly").data.decode()
    assert "review-focus" in html and "No work for" not in html
