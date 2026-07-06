# tests/test_render_stage_e.py — Stage E of the Render foundation: (1) Schedule + Posted read-models
# carry variant_hook (the Render.hook_text mirror) so the operator SEES which hook each account got and
# can correlate hook->lift; (2) the durable archive records the per-account render identity (render_id +
# hook + file) so "what shipped for @a" survives a swept Render / lost ledger; (3) GC reclaims a Render's
# file once NO post references it (reburn-orphan / deleted post), mirroring the clip sweep.
import json, os, time
from datetime import datetime, timezone
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, Render, Platform, PostState, ClipState,
                           MomentState, RenderState, Fmt)

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
FUTURE = "2099-01-01T00:00:00Z"


def _base(cfg):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
    return led


# ---- E1: the read-models surface the per-account hook ----
def test_schedule_row_carries_variant_hook(tmp_path):
    from fanops.studio import views
    cfg = Config(root=tmp_path); led = _base(cfg)
    led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="c", state=PostState.queued, scheduled_time=FUTURE,
                      render_id="render_x", variant_hook="watch his face", public_url="dryrun://p1"))
    led.save()
    rows = views.schedule_rows(Ledger.load(cfg), cfg, now=NOW)
    assert rows and rows[0].variant_hook == "watch his face"

def test_posted_row_carries_variant_hook(tmp_path):
    from fanops.studio import views
    cfg = Config(root=tmp_path); led = _base(cfg)
    led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="c", state=PostState.published, scheduled_time="2026-06-01T00:00:00Z",
                      public_url="http://x", variant_hook="the smile gives it away"))
    led.save()
    rows = views.posted_library(Ledger.load(cfg), cfg)
    assert rows and rows[0].variant_hook == "the smile gives it away"


# ---- E2: the durable archive records the render identity ----
def test_archive_records_render_identity(tmp_path):
    from fanops.post.run import _archive_published
    cfg = Config(root=tmp_path)
    p = Post(id="p_pub", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
             caption="c", state=PostState.published, published_at="2026-06-05T10:00:00Z",
             render_id="render_x", variant_hook="he wrote this for one person",
             media_urls=["file:///clips/batch/src/render_x.9x16.mp4"], public_url="http://ig/x")
    _archive_published(cfg, p)
    rec = json.loads((cfg.published / "2026-06-05" / "p_pub.json").read_text())
    assert rec["render_id"] == "render_x"
    assert rec["variant_hook"] == "he wrote this for one person"
    assert rec["media"] == "file:///clips/batch/src/render_x.9x16.mp4"


# ---- E3: GC reclaims an UNREFERENCED render's file; spares a referenced one ----
def test_gc_sweeps_unreferenced_render_keeps_referenced(tmp_path):
    from fanops.cli import cmd_gc
    cfg = Config(root=tmp_path); cfg.clips.mkdir(parents=True, exist_ok=True)
    orphan = cfg.clips / "render_orphan.mp4"; orphan.write_bytes(b"O")
    kept = cfg.clips / "render_kept.mp4"; kept.write_bytes(b"K")
    old = time.time() - 40 * 86400
    os.utime(orphan, (old, old)); os.utime(kept, (old, old))     # both old enough to age out
    led = _base(cfg)
    led.add_render(Render(id="render_orphan", clip_id="clip_1", account="a", surface_key="k",
                          path=str(orphan), state=RenderState.rendered))
    led.add_render(Render(id="render_kept", clip_id="clip_1", account="a", surface_key="k",
                          path=str(kept), state=RenderState.rendered))
    # only render_kept is referenced by a live post; render_orphan is a reburn leftover
    led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption="c", state=PostState.queued, render_id="render_kept", public_url="dryrun://p1"))
    led.save()
    cmd_gc(cfg, 30)
    assert not orphan.exists()                                   # unreferenced + old -> reclaimed
    assert kept.exists()                                         # a live post still serves it -> spared


# ---- E1 (UI): the rendered Schedule panel shows the per-account hook ----
def test_schedule_panel_renders_hook_column(tmp_path):
    pytest.importorskip("flask")
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig1"}}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/s.mp4"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="r", state=MomentState.clipped))
        led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                          caption="c", state=PostState.queued, scheduled_time="2099-06-06T12:00:00Z",
                          variant_hook="watch his face", public_url="dryrun://p1"))
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().get("/schedule").data
    assert b"sched-caption" in html and "watch his face".encode() in html and b"\xe2\x9c\xa6" in html
