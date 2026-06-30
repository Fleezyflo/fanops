"""Phantom publish cleanup — reconcile-only promoted rows without published_at."""
from __future__ import annotations
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Clip, Source, Moment, Platform, PostState, ClipState, MomentState, Fmt
from fanops.reconcile import reconcile_posts
from fanops.studio.actions import revert_phantom_published
from fanops.studio.views_results import is_phantom_published
from fanops.timeutil import iso_z

_NOW = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
_PAST = iso_z(_NOW)


def _seed(cfg, pid, *, state=PostState.published, platform=Platform.tiktok, published_at=None,
          public_url="https://www.tiktok.com/@x/video/1", submission_id="zid1", metrics=None):
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "c0.mp4").write_bytes(b"V")
    led = Ledger.load(cfg)
    if "s1" not in led.sources:
        led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    if "m1" not in led.moments:
        led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    if "c0" not in led.clips:
        led.add_clip(Clip(id="c0", parent_id="m1", path=str(cdir / "c0.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id=pid, parent_id="c0", account="@tt", account_id="z1", platform=platform,
                      caption="c", state=state, scheduled_time=_PAST, submission_id=submission_id,
                      public_url=public_url, published_at=published_at, metrics=metrics or {}))
    led.save()


def test_is_phantom_published_detects_reconcile_only_row(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, "p1", published_at=None)
    p = Ledger.load(cfg).posts["p1"]
    assert is_phantom_published(p, cfg=cfg)


def test_is_phantom_published_keeps_real_ship(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, "p1", published_at=_PAST)
    p = Ledger.load(cfg).posts["p1"]
    assert not is_phantom_published(p, cfg=cfg)


def test_is_phantom_published_keeps_analyzed_with_metrics(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, "p1", state=PostState.analyzed, platform=Platform.instagram,
          public_url="https://www.instagram.com/reel/abc/", metrics={"views": 100, "lift_score": 1.0})
    p = Ledger.load(cfg).posts["p1"]
    assert not is_phantom_published(p, cfg=cfg)


def test_reconcile_stamps_published_at_on_promote(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, "p1", state=PostState.needs_reconcile, published_at=None, public_url="")
    led = reconcile_posts(Ledger.load(cfg), cfg,
                          get_status=lambda sid: {"status": "published", "publicUrl": "https://www.tiktok.com/@x/video/9"})
    p = led.posts["p1"]
    assert p.state is PostState.published
    assert p.published_at and p.published_at.endswith("Z")


def test_revert_phantom_published_clears_and_keeps_analyzed_ig(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, "tt1", published_at=None)
    _seed(cfg, "tt2", published_at=None)
    _seed(cfg, "ig1", state=PostState.analyzed, platform=Platform.instagram,
          public_url="https://www.instagram.com/reel/abc/", metrics={"views": 50, "lift_score": 2.0})
    res = revert_phantom_published(cfg, None, reason="test_cleanup")
    assert res.ok and res.detail["reverted"] == 2
    led = Ledger.load(cfg)
    assert led.posts["tt1"].state is PostState.awaiting_approval
    assert not led.posts["tt1"].submission_id
    assert led.posts["ig1"].state is PostState.analyzed
    assert led.posts["ig1"].metrics.get("views") == 50


def test_revert_phantom_published_dry_run(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, "p1", published_at=None)
    res = revert_phantom_published(cfg, None, reason="dry", dry_run=True)
    assert res.ok and res.detail["would_revert"] == 1
    assert Ledger.load(cfg).posts["p1"].state is PostState.published


def test_revert_phantom_sidecar_evidence(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, "p1", published_at=None)
    cfg.scheduled.mkdir(parents=True, exist_ok=True)
    (cfg.scheduled / "p1.json").write_text('{"text": "dryrun payload"}')
    res = revert_phantom_published(cfg, ["p1"], reason="dryrun_sidecar")
    assert res.ok and res.detail["evidence"]["p1"] == "dryrun_sidecar+live_url"
