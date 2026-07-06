# tests/integration/test_variant_ucb_real.py
"""Creative-variation v3 — the UCB bandit closing the loop, proven END-TO-END ON DISK + deterministic."""
from __future__ import annotations
import json
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, MomentState, ClipState, PostState, Platform)
from fanops.caption import request_captions
from fanops.agentstep import request_path

pytestmark = pytest.mark.integration


def _add_analyzed(led, pid, hook, lift):
    mid, cid = f"m_{pid}", f"c_{pid}"
    led.add_moment(Moment(id=mid, parent_id="src_1", content_token=pid, start=0, end=7, reason="r",
                          transcript_excerpt="they slept on me", state=MomentState.clipped, hook=hook))
    led.add_clip(Clip(id=cid, parent_id=mid, path=f"/{cid}.mp4", state=ClipState.rendered))
    led.add_post(Post(id=pid, parent_id=cid, account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.analyzed, metrics={"lift_score": lift},
                      public_url=f"dryrun://{cid}"))


def _seed_on_disk_ledger(cfg: Config) -> None:
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))
    for i in range(1, 9):
        _add_analyzed(led, f"L{i}", "LEAD", 60.0)
    _add_analyzed(led, "N1", "NEW", 59.0)
    led.save()


def test_ucb_real_request_carries_bandit_pick_and_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.setenv("FANOPS_VARIANT_UCB", "1")
    cfg = Config(root=tmp_path)
    _seed_on_disk_ledger(cfg)
    led = Ledger.load(cfg)
    assert led.posts and led.clips["clip_1"].state is ClipState.rendered
    led = request_captions(led, cfg, "clip_1", [("a", Platform.instagram)])
    path = request_path(cfg, "captions", "clip_1")
    first = path.read_text()
    payload = json.loads(first)
    assert payload.get("learned_hooks") == ["NEW"]
    cfg2 = Config(root=tmp_path / "rerun")
    _seed_on_disk_ledger(cfg2)
    led2 = Ledger.load(cfg2)
    request_captions(led2, cfg2, "clip_1", [("a", Platform.instagram)])
    assert request_path(cfg2, "captions", "clip_1").read_text() == first
