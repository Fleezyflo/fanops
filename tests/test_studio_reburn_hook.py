# P9: reburn_hook updates the owner-moment hook and re-renders the shared clip (no per-post variant).
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt)
from fanops.studio.actions import reburn_hook
from fanops.studio.preview_media import media_path_for_post

FUTURE = "2099-01-01T00:00:00Z"

def _accounts(cfg, accts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accts}))

def _seed(cfg, *, platform=Platform.instagram, state=PostState.awaiting_approval, hook="OLD HOOK", meta=None):
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped, hook=hook))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16,
                      state=ClipState.captioned, meta_captions=(meta or {})))
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="a", account_id="1", platform=platform,
                      caption="c", state=state, scheduled_time=FUTURE, public_url="dryrun://p_edit"))
    led.save(); return led


def test_reburn_updates_moment_hook_and_rerenders(tmp_path, mocker):
    cfg = Config(root=tmp_path); _seed(cfg)
    rendered = Clip(id="clip_1", parent_id="mom_1", path=str(cfg.clips / "clip_1.mp4"),
                    aspect=Fmt.r9x16, state=ClipState.rendered)
    rm = mocker.patch("fanops.clip.render_moment", return_value=(Ledger.load(cfg), rendered))
    res = reburn_hook(cfg, "p_edit", "NEW HOOK")
    assert res.ok is True and res.detail["hook"] == "NEW HOOK"
    led = Ledger.load(cfg)
    assert led.moments["mom_1"].hook == "NEW HOOK"
    rm.assert_called_once()


def test_reburn_serves_rerendered_clip_path(tmp_path, mocker):
    cfg = Config(root=tmp_path); _seed(cfg, platform=Platform.youtube)
    out = cfg.clips / "clip_1_yt.mp4"; out.write_bytes(b"yt")
    rendered = Clip(id="clip_1", parent_id="mom_1", path=str(out), aspect=Fmt.r16x9, state=ClipState.rendered)
    mocker.patch("fanops.clip.render_moment", return_value=(Ledger.load(cfg), rendered))
    assert reburn_hook(cfg, "p_edit", "YT HOOK").ok is True
    assert media_path_for_post(cfg, Ledger.load(cfg), "p_edit") == str(out)


def _fake_render_reset(led, cfg, moment_id, *, aspect=Fmt.r9x16, **kw):
    # mimic render_moment's SUCCESS: fresh clip record (state reset, captions wiped) so the action's restore is exercised
    c = led.clips["clip_1"]
    new = c.model_copy(update={"state": ClipState.rendered, "meta_captions": {}, "hook_burn_failed": False})
    led.clips[c.id] = new
    return led, new


def test_reburn_does_not_touch_meta_captions(tmp_path, mocker):
    seeded = {"a/instagram": {"caption": "c", "hashtags": ["#x"]}}
    cfg = Config(root=tmp_path); _seed(cfg, meta=seeded)
    mocker.patch("fanops.clip.render_moment", side_effect=_fake_render_reset)
    reburn_hook(cfg, "p_edit", "NEW HOOK")
    mc = Ledger.load(cfg).clips["clip_1"].meta_captions
    assert mc == seeded and "hook" not in mc.get("a/instagram", {})


def test_reburn_preserves_clip_state(tmp_path, mocker):
    cfg = Config(root=tmp_path); _seed(cfg, meta={"a/instagram": {"caption": "c"}})
    mocker.patch("fanops.clip.render_moment", side_effect=_fake_render_reset)
    reburn_hook(cfg, "p_edit", "NEW HOOK")
    assert Ledger.load(cfg).clips["clip_1"].state is ClipState.captioned   # captioned state PRESERVED across re-render


def test_reburn_hook_burn_failed_warns_not_rollback(tmp_path, mocker):
    cfg = Config(root=tmp_path); _seed(cfg)
    err_clip = Clip(id="clip_1", parent_id="mom_1", path=str(cfg.clips / "clip_1.mp4"),
                    aspect=Fmt.r9x16, state=ClipState.error, error_reason="burn failed")
    mocker.patch("fanops.clip.render_moment", return_value=(Ledger.load(cfg), err_clip))
    res = reburn_hook(cfg, "p_edit", "NEW HOOK")
    assert res.ok is False


def test_reburn_rejects_non_editable(tmp_path, mocker):
    cfg = Config(root=tmp_path); _seed(cfg, state=PostState.published)
    mocker.patch("fanops.clip.render_moment")
    res = reburn_hook(cfg, "p_edit", "NEW HOOK")
    assert res.ok is False and ("published" in (res.error or "") or "editable" in (res.error or ""))


def test_reburn_unknown_post(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = reburn_hook(cfg, "nope", "NEW HOOK")
    assert res.ok is False and "no such post" in (res.error or "").lower()


def test_reburn_route_swaps_edit_field(tmp_path, monkeypatch):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed(cfg)
    rendered = Clip(id="clip_1", parent_id="mom_1", path=str(cfg.clips / "clip_1.mp4"),
                    aspect=Fmt.r9x16, state=ClipState.rendered, hook_burn_failed=True)
    monkeypatch.setattr("fanops.clip.render_moment", lambda *a, **k: (Ledger.load(cfg), rendered))
    app = create_app(cfg); app.config.update(TESTING=True)
    client = app.test_client()
    r = client.post("/reburn-hook/p_edit", data={"hook": "ROUTED HOOK", "swap": "card"})
    assert r.status_code == 200 and b"ROUTED HOOK" in r.data
    assert b'id="hook-p_edit"' in r.data and b'name="hook"' in r.data
    assert b'hx-target="#card-clip_1"' in r.data and b"/reburn-hook/p_edit" in r.data
    body = r.data.decode()
    marker = body.find('class="hook-preburn"')
    assert marker >= 0 and "ROUTED HOOK" in body[marker:marker + 200]
    assert 'src="/clips/clip_1?v=' in body
    feed = client.post("/reburn-hook/p_edit", data={"hook": "ROUTED HOOK", "swap": "feed"})
    assert feed.status_code == 200
    fb = feed.data.decode()
    fmark = fb.find('class="hook-preburn"')
    assert fmark >= 0 and "ROUTED HOOK" in fb[fmark:fmark + 200]
    assert 'src="/clips/clip_1?v=' in fb and 'src="/media/p_edit"' not in fb
    assert 'hx-target="#feed-p_edit"' in fb
    saved = Ledger.load(cfg)
    assert saved.moments["mom_1"].hook == "ROUTED HOOK"
    assert saved.posts["p_edit"].state is PostState.awaiting_approval


def test_card_and_compact_include_hook_textarea(tmp_path):
    from fanops.source_tags import source_tag_locks_path
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
    ]}))
    led = _seed(cfg)
    led.clips["clip_1"].hook_burn_failed = True
    led.posts["p_edit"].caption = "The pocket stays."
    led.posts["p_edit"].hashtags = ["#keep", "#low"]
    led.save()
    meas = {
        "#keep": {"play_count": 10.0, "current_top_reel_play_max_7d": 1.0},
        "#low": {"play_count": 3.0, "current_top_reel_play_max_7d": 1.0},
    }
    lock = ["#keep", "#low"]
    path = source_tag_locks_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"src_1": {
        "pile": lock, "lock": lock, "researched_at": "2026-08-17T00:00:00Z",
        "measurements": meas,
    }}))
    app = create_app(cfg); app.config.update(TESTING=True)
    client = app.test_client()

    def _order(html):
        hook_at = html.find('id="hook-p_edit"')
        tags_at = html.find('class="tag-swaps"')
        return hook_at, tags_at

    for url in ("/review?account=all", "/review?account=all&compact=1"):
        html = client.get(url).data.decode()
        hook_at, tags_at = _order(html)
        cap_at = html.find('class="ro surface-caption"')
        assert hook_at >= 0 and tags_at > hook_at and cap_at > tags_at
        assert html.count('id="hook-p_edit"') == 1
        assert html.count('name="hook"') == 1
        assert html.count('class="tag-swaps"') == 1
        assert "/reburn-hook/p_edit" in html
        assert 'hx-target="#card-clip_1"' in html
        assert "burned into this clip on approval" in html
        assert 'form="fanops-tag-swap"' not in html
        snippet = html[cap_at:cap_at + 120]
        assert "The pocket stays." in snippet and "#keep" not in snippet
        if "compact" not in url:
            marker = html.find('class="hook-preburn"')
            assert marker >= 0 and "OLD HOOK" in html[marker:marker + 200]
            assert 'src="/clips/clip_1?v=' in html
    feed = client.get("/review?account=a").data.decode()
    hook_at, tags_at = _order(feed)
    cap_at = feed.find('name="caption"')
    assert hook_at >= 0 and tags_at > hook_at and cap_at > tags_at
    assert feed.count('id="hook-p_edit"') == 1 and feed.count('name="hook"') == 1
    assert feed.count('class="tag-swaps"') == 1
    assert 'hx-target="#feed-p_edit"' in feed
    assert ">The pocket stays.</textarea>" in feed
    fmark = feed.find('class="hook-preburn"')
    assert fmark >= 0 and "OLD HOOK" in feed[fmark:fmark + 200]
    assert 'src="/clips/clip_1?v=' in feed and 'src="/media/p_edit"' not in feed
    led.moments["mom_1"].hook = None
    led.save()
    empty = client.get("/review?account=all").data.decode()
    assert 'id="hook-p_edit"' in empty and 'name="hook"' in empty
    assert 'class="hook-preburn"' not in empty


def test_reburn_route_unknown_post_clean_error(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/reburn-hook/nope", data={"hook": "x"})
    assert r.status_code == 200 and b"no such post" in r.data


def test_swap_tag_route_shows_chosen_play_count(tmp_path):
    from fanops.source_tags import source_tag_locks_path
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    led = _seed(cfg)
    led.posts["p_edit"].caption = "The pocket stays."
    led.posts["p_edit"].hashtags = ["#keep", "#low"]
    led.save()
    meas = {
        "#keep": {"play_count": 10.0, "current_top_reel_play_max_7d": 1.0},
        "#also": {"play_count": 50.5, "current_top_reel_play_max_7d": 2.0},
        "#low": {"play_count": 3.0, "current_top_reel_play_max_7d": 1.0},
    }
    lock = ["#keep", "#also", "#low"]
    path = source_tag_locks_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"src_1": {
        "pile": lock, "lock": lock, "researched_at": "2026-08-17T00:00:00Z",
        "measurements": meas,
    }}))
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/swap-tag/p_edit", data={"old_tag": "#low", "new_tag": "#also", "swap": "card"})
    assert r.status_code == 200
    assert b"#also" in r.data and b"50.5" in r.data
    saved = Ledger.load(cfg).posts["p_edit"]
    assert saved.caption == "The pocket stays."
    assert saved.state is PostState.awaiting_approval


def test_failed_swap_keeps_the_review_card(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    _seed(cfg)
    app = create_app(cfg)
    app.config.update(TESTING=True)
    client = app.test_client()
    card = client.post("/swap-tag/p_edit", data={"old_tag": "#missing", "new_tag": "#also", "swap": "card"})
    body = card.data.decode()
    assert card.status_code == 200
    assert 'id="card-clip_1"' in body
    assert "#missing is not on this post" in body
    assert 'name="hook"' in body
    feed = client.post("/swap-tag/p_edit", data={"old_tag": "#missing", "new_tag": "#also", "swap": "feed"})
    feed_body = feed.data.decode()
    assert 'id="feed-p_edit"' in feed_body
    assert "#missing is not on this post" in feed_body
    assert Ledger.load(cfg).posts["p_edit"].state is PostState.awaiting_approval
