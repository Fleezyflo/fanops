# tests/test_studio_render_serve.py — Stage C of the Render foundation: /media is a PURE
# post->render->path lookup. A post with render_id serves the per-account Render's own file (the
# authoritative artifact, priority over media_urls/base — no 3-way guess). Legacy posts (render_id None)
# still resolve via media_urls then base (back-compat). A missing render FILE 404s (surfaces, never a
# silent textless base swap). Mirrors test_studio_app's seed + flask test_client.
import json
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, Render, Platform, PostState, ClipState,
                           MomentState, RenderState, Fmt)


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def _seed(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "base.mp4"; base.write_bytes(b"BASECLIP")
    render = cfg.clips / "render_x.9x16.mp4"; render.write_bytes(b"RENDERX!")
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_render(Render(id="render_x", clip_id="clip_1", account="@a", surface_key="@a|instagram",
                          hook_text="H", path=str(render), state=RenderState.rendered))
    led.save()
    return base, render


def test_media_serves_the_per_account_render(tmp_path):
    cfg = Config(root=tmp_path); base, render = _seed(cfg)
    led = Ledger.load(cfg)
    # a post pointing at the render — even with media_urls ALSO set to the base, the render wins (priority).
    led.add_post(Post(id="p_r", parent_id="clip_1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="c", state=PostState.queued, render_id="render_x",
                      media_urls=[f"file://{base}"], public_url="dryrun://p_r"))
    led.save()
    r = _client(cfg).get("/media/p_r")
    assert r.status_code == 200 and r.data == render.read_bytes()   # the render file, NOT the base

def test_media_legacy_post_without_render_id_serves_base(tmp_path):
    cfg = Config(root=tmp_path); base, _ = _seed(cfg)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p_base", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="c", state=PostState.queued, public_url="dryrun://p_base"))   # render_id None
    led.save()
    r = _client(cfg).get("/media/p_base")
    assert r.status_code == 200 and r.data == base.read_bytes()     # back-compat: shared base

def test_media_render_id_set_but_entity_missing_falls_through(tmp_path):
    # resilient: render_id points at a swept Render, but media_urls still has the same file -> serve it.
    cfg = Config(root=tmp_path); base, render = _seed(cfg)
    led = Ledger.load(cfg)
    led.add_post(Post(id="p_gone", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="c", state=PostState.queued,
                      render_id="render_SWEPT", media_urls=[f"file://{render}"], public_url="dryrun://p_gone"))
    led.save()
    r = _client(cfg).get("/media/p_gone")
    assert r.status_code == 200 and r.data == render.read_bytes()

def test_media_missing_render_file_404s(tmp_path):
    # render_id resolves to a Render whose FILE was deleted -> 404 (the missing render surfaces; never a
    # silent fall-through to the textless base).
    cfg = Config(root=tmp_path); base, render = _seed(cfg)
    render.unlink()                                                 # the render file is gone
    led = Ledger.load(cfg)
    led.add_post(Post(id="p_nofile", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="c", state=PostState.queued, render_id="render_x", public_url="dryrun://p_nofile"))
    led.save()
    assert _client(cfg).get("/media/p_nofile").status_code == 404
