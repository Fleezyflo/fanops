# P9: reburn_hook updates the owner-moment hook and re-renders the shared clip (no per-post variant).
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt)
from fanops.studio.actions import reburn_hook

FUTURE = "2099-01-01T00:00:00Z"

def _seed(cfg, *, platform=Platform.instagram, state=PostState.awaiting_approval, hook="OLD HOOK",
          source_path="/s.mp4", meta=None):
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1.mp4"; base.write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=source_path, language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped, hook=hook))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16,
                      state=ClipState.captioned, meta_captions=(meta or {})))
    led.add_post(Post(id="p_edit", parent_id="clip_1", account="a", account_id="1", platform=platform,
                      caption="c", state=state, scheduled_time=FUTURE, public_url="https://www.instagram.com/p/p_edit/"))
    led.save(); return led


def test_reburn_render_fail_leaves_moment_hook_unchanged(tmp_path):
    # Real reburn_hook (no fanops.clip.render_moment patch). Missing source file makes ffmpeg fail.
    cfg = Config(root=tmp_path)
    _seed(cfg, source_path=str(tmp_path / "missing.mp4"))
    res = reburn_hook(cfg, "p_edit", "NEW HOOK")
    assert res.ok is False
    assert Ledger.load(cfg).moments["mom_1"].hook == "OLD HOOK"


def test_reburn_rejects_non_editable(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, state=PostState.published)
    res = reburn_hook(cfg, "p_edit", "NEW HOOK")
    assert res.ok is False and ("published" in (res.error or "") or "editable" in (res.error or ""))
    assert Ledger.load(cfg).moments["mom_1"].hook == "OLD HOOK"


def test_reburn_unknown_post(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = reburn_hook(cfg, "nope", "NEW HOOK")
    assert res.ok is False and "no such post" in (res.error or "").lower()
    assert Ledger.load(cfg).moments["mom_1"].hook == "OLD HOOK"


def test_reburn_route_unknown_post_clean_error(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/reburn-hook/nope", data={"hook": "x"})
    assert r.status_code == 200 and b"no such post" in r.data
    assert Ledger.load(cfg).moments["mom_1"].hook == "OLD HOOK"
