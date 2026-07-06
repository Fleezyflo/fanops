"""Creative-variation v3 — variant-gated amplification closing END-TO-END ON DISK (the Integrate bar)."""
from __future__ import annotations
import json
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, SourceState
from fanops.variant_amplify import apply_variant_amplify
from fanops.variant_learning import _hook_for_post
from fanops.agentstep import request_path
from fanops import cutover

pytestmark = pytest.mark.integration


def _validate(cfg):
    cutover._save_state(cfg, {"metrics_confirmed": True})


def _win(pid, hook, lift, *, moment_id="m1", clip_id="c1"):
    """P9: hook on owner moment; WIN/LOSE lineages share one moment+clip per hook family."""
    return Post(id=pid, parent_id=clip_id, account="a", account_id="1", platform=Platform.instagram,
                caption="x", state=PostState.analyzed, metrics={"lift_score": lift}, public_url="dryrun://c1")


def _ensure_lineage(led, moment_id, clip_id, hook):
    if moment_id not in led.moments:
        led.add_moment(Moment(id=moment_id, parent_id="s1", start=0.0, end=4.0, reason="r",
                              transcript_excerpt="ex", hook=hook))
    if clip_id not in led.clips:
        led.add_clip(Clip(id=clip_id, parent_id=moment_id, path="c1.mp4"))


def test_sustained_winner_amplifies_source_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    _validate(cfg)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.transcribed,
                              duration=10.0, transcript=[], language="en"))
        _ensure_lineage(led, "m_win", "c_win", "WIN")
        _ensure_lineage(led, "m_lose", "c_lose", "LOSE")
        for i in range(8):
            led.add_post(_win(str(i), "WIN", 95.0, moment_id="m_win", clip_id="c_win"))
        for i in range(3):
            led.add_post(_win(f"l{i}", "LOSE", 1.0, moment_id="m_lose", clip_id="c_lose"))

    nid = 100
    for _ in range(cfg.variant_amplify_min_streak):
        with Ledger.transaction(cfg) as led:
            led = apply_variant_amplify(led, cfg)
        with Ledger.transaction(cfg) as led:
            led.add_post(_win(str(nid), "WIN", 95.0, moment_id="m_win", clip_id="c_win")); nid += 1

    with Ledger.transaction(cfg) as led:
        led = apply_variant_amplify(led, cfg)

    led = Ledger.load(cfg)
    assert led.sources["s1"].state is SourceState.moments_requested
    payload = json.loads(request_path(cfg, "moments", "s1").read_text())
    assert "WIN" in payload["guidance"]
    assert payload["guidance"].startswith("AMPLIFY:")
    surviving = [p for p in led.posts.values()
                 if _hook_for_post(led, p) == "WIN" and p.state is PostState.analyzed]
    assert surviving, "variant-amplify must never delete the winning posts (G2)"


def test_no_sustained_winner_never_amplifies_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_AMPLIFY", "1")
    cfg = Config(root=tmp_path)
    _validate(cfg)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.transcribed,
                              duration=10.0, transcript=[], language="en"))
        _ensure_lineage(led, "m_win", "c_win", "WIN")
        _ensure_lineage(led, "m_lose", "c_lose", "LOSE")
        for i in range(20):
            led.add_post(_win(str(i), "WIN", 99.0, moment_id="m_win", clip_id="c_win"))
        for i in range(3):
            led.add_post(_win(f"l{i}", "LOSE", 1.0, moment_id="m_lose", clip_id="c_lose"))

    with Ledger.transaction(cfg) as led:
        led = apply_variant_amplify(led, cfg)

    led = Ledger.load(cfg)
    assert led.sources["s1"].state is SourceState.transcribed
    assert not request_path(cfg, "moments", "s1").exists()
