# tests/integration/test_variant_ucb_real.py
"""Creative-variation v3 — the UCB bandit closing the loop, proven END-TO-END ON DISK + deterministic.
A REAL ledger.json where a thin-lead surface makes UCB's pick DIFFER from greedy's (greedy emits
nothing; UCB explores the under-sampled challenger), reloaded from disk, then request_captions with
learning+UCB on, then the ACTUAL caption request file read back from 04_agent_io/requests/ — asserting
the BANDIT pick reached the agent request. Then a byte-identical re-run proves the content-addressed
determinism invariant on disk (no RNG)."""
from __future__ import annotations
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, MomentState, ClipState, PostState, Platform)
from fanops.caption import request_captions
from fanops.agentstep import request_path

pytestmark = pytest.mark.integration


def _seed_on_disk_ledger(cfg: Config) -> None:
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))
    # @a/instagram THIN LEAD: 8x LEAD@60 + 1x NEW@59 -> greedy gap 1 < MIN_GAP 10 (emits nothing);
    # UCB explores the under-sampled NEW. So UCB-on puts "NEW" in the request; greedy would put none.
    for i in range(1, 9):
        led.add_post(Post(id=f"L{i}", parent_id="clip_1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.analyzed,
                          variant_key=f"vk_L{i}", variant_hook="LEAD", metrics={"lift_score": 60.0}))
    led.add_post(Post(id="N1", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      variant_key="vk_N1", variant_hook="NEW", metrics={"lift_score": 59.0}))
    led.save()


def test_ucb_real_request_carries_bandit_pick_and_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.setenv("FANOPS_VARIANT_UCB", "1")
    cfg = Config(root=tmp_path)
    _seed_on_disk_ledger(cfg)
    led = Ledger.load(cfg)                                # round-trip from disk
    assert led.posts and led.clips["clip_1"].state is ClipState.rendered
    import json
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    path = request_path(cfg, "captions", "clip_1")
    first = path.read_text()
    payload = json.loads(first)
    assert payload.get("learned_hooks") == ["NEW"]        # the UCB pick on disk (greedy would be absent)
    # DETERMINISM (content-addressed, no RNG): an INDEPENDENT run over an IDENTICALLY-seeded ledger
    # in a SEPARATE root writes a BYTE-IDENTICAL request file — request_id and all. We use a second
    # root (not a same-root re-run) deliberately: agentstep stamps a CHAINED request_id (it hashes in
    # the PRIOR request's id as an anti-replay/idempotency guard), so a same-root second call to the
    # SAME key legitimately gets a different request_id (prev differs) — that chain is by-design, not
    # non-determinism. Re-seeding a fresh root reproduces the identical prior state (prev="0"), so the
    # bandit's pick AND the full serialized payload are bit-for-bit reproducible from identical input.
    cfg2 = Config(root=tmp_path / "rerun")
    _seed_on_disk_ledger(cfg2)
    led2 = Ledger.load(cfg2)
    request_captions(led2, cfg2, "clip_1", [("@a", Platform.instagram)])
    assert request_path(cfg2, "captions", "clip_1").read_text() == first   # byte-identical from identical state
