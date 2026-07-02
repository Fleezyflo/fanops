"""Reconcile promotes a needs_reconcile row to published WITH a published_at stamp.

(dryrun-boundary M3 deleted the phantom-publish DETECTOR + revert action + their tests — the phantom
`published` class is now unconstructable. The one test that outlived them is this reconcile-promotion
characterization: it's about reconcile stamping published_at, not about the deleted scaffolding.)"""
from __future__ import annotations
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Clip, Source, Moment, Platform, PostState, ClipState, MomentState, Fmt
from fanops.reconcile import reconcile_posts
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


def test_reconcile_stamps_published_at_on_promote(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, "p1", state=PostState.needs_reconcile, published_at=None, public_url="")
    led = reconcile_posts(Ledger.load(cfg), cfg,
                          get_status=lambda sid: {"status": "published", "publicUrl": "https://www.tiktok.com/@x/video/9"})
    p = led.posts["p1"]
    assert p.state is PostState.published
    assert p.published_at and p.published_at.endswith("Z")
