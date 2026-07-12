# Flow-first UX: focus entry, approve stays in review, schedule auto-ship UI.
import json
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops.studio import views

def _accounts(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "ig1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig1"}}]}))

def _seed(cfg, n=2):
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    for i in range(n):
        cid = f"c{i}"; (cdir / f"{cid}.mp4").write_bytes(b"V")
        led.add_clip(Clip(id=cid, parent_id="m1", path=str(cdir / f"{cid}.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id=f"p{i}", parent_id=cid, account="a", account_id="ig1", platform=Platform.instagram,
                          caption="c", state=PostState.awaiting_approval))
    led.save()

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def test_home_review_link_includes_account(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg)
    html = _client(cfg).get("/").data.decode()
    assert "/review?account=" in html and 'class="home-acct-badge">2</span>' in html

def test_approve_in_focus_shows_next_clip_not_schedule(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg)
    html = _client(cfg).post("/posts/approve?account=@a&view=account&focus=1&fi=0", data={"ids": "p0"}).data.decode()
    assert "next clip" in html.lower()
    assert "Open schedule" not in html

def test_review_handoff_picks_busiest_account(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, n=3)
    h = views.review_handoff(cfg)
    assert h["account"] == "a" and h["awaiting"] == 3

def test_schedule_auto_ship_false_without_daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1")
    cfg = Config(root=tmp_path)
    assert views.schedule_auto_ship(cfg) is False

def test_rail_posts_label_is_results(tmp_path):
    cfg = Config(root=tmp_path)
    html = _client(cfg).get("/review").data.decode()
    assert ">Results</a>" in html or "Results</a>" in html

def test_review_handoff_includes_dominant_batch(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    (cdir / "c0.mp4").write_bytes(b"V")
    led.add_clip(Clip(id="c0", parent_id="m1", path=str(cdir / "c0.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
    for i, bid in enumerate(["b1", "b1", "b2"]):
        led.add_post(Post(id=f"p{i}", parent_id="c0", account="a", account_id="ig1", platform=Platform.instagram,
                          caption="c", state=PostState.awaiting_approval, batch_id=bid))
    led.save()
    h = views.review_handoff(cfg)
    assert h.get("batch") == "b1"

def test_focus_shows_hook_preburn_notice(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, n=1)
    led = Ledger.load(cfg)
    led.moments["m1"] = led.moments["m1"].model_copy(update={"hook": "Wait for it"})
    led.save()
    html = _client(cfg).get("/review?account=@a&view=account&focus=1&fi=0").data.decode()
    assert "focus-hook-banner" in html and "Wait for it" in html
