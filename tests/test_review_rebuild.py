# U6: Review page rebuild — per-account continuous feed, composite approve-with-edits.
import json
import pytest
pytest.importorskip("flask")
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt)
from fanops.studio.views_common import REVIEW_FEED_SLICE
from fanops.timeutil import iso_z

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)


def _z(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _accounts(cfg, handles=("a",)):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": "1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig1"}} for h in handles]}))


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


def _lineage(led, *, sid, mid, cid, path, reason="pick reason", hook="HOOK", batch_id=None, created_at=None):
    led.add_source(Source(id=sid, source_path=f"/v/{sid}.mp4", language="en",
                          created_at=created_at or _z(NOW - timedelta(days=1))))
    led.add_moment(Moment(id=mid, parent_id=sid, content_token="0-7", start=0, end=7,
                          reason=reason, state=MomentState.clipped, hook=hook))
    led.add_clip(Clip(id=cid, parent_id=mid, path=path, aspect=Fmt.r9x16, state=ClipState.captioned))


def _await_post(led, pid, cid, account, *, caption="cap", created_at=None, batch_id=None):
    led.add_post(Post(id=pid, parent_id=cid, account=account, account_id="1", platform=Platform.instagram,
                      caption=caption, state=PostState.awaiting_approval,
                      scheduled_time=_z(NOW + timedelta(hours=3)),
                      created_at=created_at or _z(NOW), batch_id=batch_id))


def _seed_feed_card(cfg, *, handle="a", pid="p1", caption="await caption", hook="SCROLL HOOK"):
    cfg.clips.mkdir(parents=True, exist_ok=True)
    clip_path = cfg.clips / "clip_1.mp4"
    clip_path.write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    with Ledger.transaction(cfg) as led:
        _lineage(led, sid="src_1", mid="m1", cid="clip_1", path=str(clip_path), hook=hook)
        _await_post(led, pid, "clip_1", handle, caption=caption)


def _seed_85_posts(cfg, handle="a"):
    """85 awaiting posts for one account — initial feed shows ≤12 videos."""
    _accounts(cfg, handles=(handle,))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "base.mp4"
    base.write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    with Ledger.transaction(cfg) as led:
        for i in range(85):
            sid = f"src_{i // 17}"
            if sid not in led.sources:
                led.add_source(Source(id=sid, source_path=f"/v/{sid}.mp4", language="en",
                                      created_at=_z(NOW - timedelta(days=10 - i // 17))))
            mid = f"mom_{i}"
            cid = f"clip_{i}"
            led.add_moment(Moment(id=mid, parent_id=sid, content_token=f"{i}-{i+5}", start=i, end=i + 5,
                                  reason=f"r{i}", state=MomentState.clipped, hook=f"H{i}"))
            led.add_clip(Clip(id=cid, parent_id=mid, path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned))
            _await_post(led, f"p_{i}", cid, handle, caption=f"cap{i}",
                        created_at=_z(NOW - timedelta(minutes=85 - i)))


def test_bare_review_switcher_and_account_feed_slice(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts(cfg, handles=("a", "b"))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "c.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    with Ledger.transaction(cfg) as led:
        for acct in ("a", "b"):
            _lineage(led, sid="src_1", mid=f"m_{acct}", cid=f"c_{acct}", path=str(base))
            _await_post(led, f"aw_{acct}", f"c_{acct}", acct)
    bare = _client(cfg).get("/review").data.decode()
    assert "review-switcher" in bare
    assert "review-account-picker" not in bare
    full = _client(cfg).get("/review?account=a").data.decode()
    assert "review-feed" in full
    assert "Show more" not in full
    assert full.count("<video") <= REVIEW_FEED_SLICE


def test_feed_initial_slice_capped_at_twelve_for_large_seed(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_85_posts(cfg)
    html = _client(cfg).get("/review?account=a").data.decode()
    assert html.count("<video") == REVIEW_FEED_SLICE
    assert "Show more" not in html
    assert "feed-sentinel" in html


def test_feed_card_renders_caption_hook_reason(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed_feed_card(cfg, caption="my cap", hook="MY HOOK")
    html = _client(cfg).get("/review?account=a").data.decode()
    assert "my cap" in html
    assert "MY HOOK" in html
    assert "pick reason" in html


def test_approve_with_edits_promotes_edited(tmp_path, mocker, monkeypatch):
    monkeypatch.setattr("fanops.studio.actions._now", lambda n=None: NOW)
    cfg = Config(root=tmp_path); _accounts(cfg); _seed_feed_card(cfg)
    from fanops.models import Clip as ClipModel
    mocker.patch("fanops.clip.render_moment", return_value=(None, ClipModel(
        id="clip_1", parent_id="m1", path=str(cfg.clips / "clip_1.mp4"), aspect=Fmt.r9x16, state=ClipState.captioned)))
    c = _client(cfg)
    html = c.post("/posts/approve-with-edits/p1?account=a",
                  data={"caption": "edited cap", "hook": "NEW HOOK"}).data.decode()
    led = Ledger.load(cfg)
    assert led.posts["p1"].state is PostState.queued
    assert led.posts["p1"].caption == "edited cap"
    assert led.posts["p1"].edited_at == iso_z(NOW)
    assert led.moments["m1"].hook == "NEW HOOK"
    assert "review-feed" in html or "edited cap" in html


def test_approve_with_edits_untouched_no_render(tmp_path, mocker, monkeypatch):
    monkeypatch.setattr("fanops.studio.actions._now", lambda n=None: NOW)
    cfg = Config(root=tmp_path); _accounts(cfg); _seed_feed_card(cfg)
    rm = mocker.patch("fanops.clip.render_moment")
    c = _client(cfg)
    c.post("/posts/approve-with-edits/p1?account=a",
           data={"caption": "await caption", "hook": "SCROLL HOOK"})
    rm.assert_not_called()
    led = Ledger.load(cfg)
    assert led.posts["p1"].state is PostState.queued
    assert led.posts["p1"].caption == "await caption"
    assert led.posts["p1"].edited_at is None


def test_approve_with_edits_offbrand_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("fanops.studio.actions._now", lambda n=None: NOW)
    cfg = Config(root=tmp_path); _accounts(cfg); _seed_feed_card(cfg)
    res = _client(cfg).post("/posts/approve-with-edits/p1?account=a",
                            data={"caption": "stream now — link in bio", "hook": "SCROLL HOOK"})
    html = res.data.decode()
    assert Ledger.load(cfg).posts["p1"].state is PostState.awaiting_approval
    assert "off-brand" in html.lower() or "rejected" in html.lower()


def test_approve_with_edits_reburn_fail_stays_awaiting(tmp_path, mocker, monkeypatch):
    monkeypatch.setattr("fanops.studio.actions._now", lambda n=None: NOW)
    cfg = Config(root=tmp_path); _accounts(cfg); _seed_feed_card(cfg)
    mocker.patch("fanops.clip.render_moment", side_effect=RuntimeError("burn failed"))
    html = _client(cfg).post("/posts/approve-with-edits/p1?account=a",
                             data={"caption": "await caption", "hook": "BAD HOOK"}).data.decode()
    assert Ledger.load(cfg).posts["p1"].state is PostState.awaiting_approval
    assert "burn" in html.lower() or "failed" in html.lower() or "re-burn" in html.lower()


def test_select_source_bulk_approve(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "c.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    with Ledger.transaction(cfg) as led:
        _lineage(led, sid="src_a", mid="ma1", cid="ca1", path=str(base))
        _lineage(led, sid="src_b", mid="mb1", cid="cb1", path=str(base))
        _await_post(led, "pa1", "ca1", "a")
        _await_post(led, "pa2", "ca1", "a")
        _await_post(led, "pb1", "cb1", "a")
    c = _client(cfg)
    html = c.get("/review?account=a").data.decode()
    assert 'data-review-action="select-source"' in html
    c.post("/posts/approve?account=a", data={"ids": ["pa1", "pa2"]})
    led = Ledger.load(cfg)
    assert led.posts["pa1"].state is PostState.queued
    assert led.posts["pa2"].state is PostState.queued
    assert led.posts["pb1"].state is PostState.awaiting_approval


def test_switcher_hides_zero_counter_accounts(tmp_path):
    cfg = Config(root=tmp_path)
    _accounts(cfg, handles=("a", "b", "c"))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "c.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    with Ledger.transaction(cfg) as led:
        _lineage(led, sid="src_1", mid="m1", cid="c1", path=str(base))
        _await_post(led, "p1", "c1", "a")
        _await_post(led, "p2", "c1", "b")
    html = _client(cfg).get("/review").data.decode()
    assert "review-switcher" in html
    assert ">c<" not in html and 'account=c' not in html
    assert "account=a" in html and "account=b" in html
