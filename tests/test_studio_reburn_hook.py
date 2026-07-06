# P9: reburn_hook updates the owner-moment hook and re-renders the shared clip (no per-post variant).
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt)
from fanops.studio.actions import reburn_hook
from fanops.studio.app import _media_path_for_post

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
    assert _media_path_for_post(Ledger.load(cfg), "p_edit") == str(out)


def test_reburn_does_not_touch_meta_captions(tmp_path, mocker):
    seeded = {"a/instagram": {"caption": "c", "hashtags": ["#x"]}}
    cfg = Config(root=tmp_path); _seed(cfg, meta=seeded)
    rendered = Clip(id="clip_1", parent_id="mom_1", path=str(cfg.clips / "clip_1.mp4"),
                    aspect=Fmt.r9x16, state=ClipState.rendered)
    mocker.patch("fanops.clip.render_moment", return_value=(Ledger.load(cfg), rendered))
    reburn_hook(cfg, "p_edit", "NEW HOOK")
    mc = Ledger.load(cfg).clips["clip_1"].meta_captions
    assert mc == seeded and "hook" not in mc.get("a/instagram", {})


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
                    aspect=Fmt.r9x16, state=ClipState.rendered, hook_burn_failed=False)
    monkeypatch.setattr("fanops.clip.render_moment", lambda *a, **k: (Ledger.load(cfg), rendered))
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/reburn-hook/p_edit", data={"hook": "ROUTED HOOK"})
    assert r.status_code == 200 and b"ROUTED HOOK" in r.data
    assert Ledger.load(cfg).moments["mom_1"].hook == "ROUTED HOOK"


def test_reburn_route_unknown_post_clean_error(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/reburn-hook/nope", data={"hook": "x"})
    assert r.status_code == 200 and b"no such post" in r.data
