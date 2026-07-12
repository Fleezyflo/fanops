# U6: composite approve-with-edits on the per-account feed cards.
import json
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt

FUTURE = "2099-06-06T12:00:00Z"

def _accounts(cfg, handle="a"):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": handle, "account_id": "ig1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig1"}}]}))

def _seed(cfg, *, pid="p1", caption="await caption", hook="SCROLL HOOK", handle="a"):
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "clip_1.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hook=hook))
    led.add_clip(Clip(id="clip_1", parent_id="m1", path=str(cdir / "clip_1.mp4"), aspect=Fmt.r9x16,
                      state=ClipState.captioned))
    led.add_post(Post(id=pid, parent_id="clip_1", account=handle,
                      account_id="ig1", platform=Platform.instagram, caption=caption,
                      state=PostState.awaiting_approval, scheduled_time=FUTURE))
    led.save()

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def test_feed_card_renders_caption_and_hook_fields(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg)
    html = _client(cfg).get("/review?account=a").data.decode()
    assert "review-feed-card" in html
    assert 'name="caption"' in html and 'name="hook"' in html
    assert "await caption" in html and "SCROLL HOOK" in html

def test_feed_caption_edit_via_composite_approve(tmp_path, mocker, monkeypatch):
    from datetime import datetime, timezone
    from fanops.timeutil import iso_z
    NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("fanops.studio.actions._now", lambda n=None: NOW)
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg)
    from fanops.models import Clip as ClipModel
    mocker.patch("fanops.clip.render_moment", return_value=(None, ClipModel(
        id="clip_1", parent_id="m1", path=str(cfg.clips / "clip_1.mp4"), aspect=Fmt.r9x16, state=ClipState.captioned)))
    html = _client(cfg).post("/posts/approve-with-edits/p1?account=a",
                             data={"caption": "edited in feed", "hook": "NEW HOOK"}).data.decode()
    assert Ledger.load(cfg).posts["p1"].caption == "edited in feed"
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued
    assert Ledger.load(cfg).posts["p1"].edited_at == iso_z(NOW)
    assert "Approved" in html or "approved" in html.lower()

def test_feed_reburn_via_composite_approve(tmp_path, mocker):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg)
    rendered = Clip(id="clip_1", parent_id="m1", path=str(cfg.clips / "clip_1.mp4"),
                    aspect=Fmt.r9x16, state=ClipState.rendered)
    mocker.patch("fanops.clip.render_moment", return_value=(Ledger.load(cfg), rendered))
    _client(cfg).post("/posts/approve-with-edits/p1?account=a",
                      data={"caption": "await caption", "hook": "NEW HOOK TEXT"})
    assert Ledger.load(cfg).moments["m1"].hook == "NEW HOOK TEXT"

def test_account_all_list_unchanged(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg)
    html = _client(cfg).get("/review?account=all&view=list").data.decode()
    assert "review-feed-card" not in html
    assert 'hx-post="/posts/approve-with-edits/' not in html
