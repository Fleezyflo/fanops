"""Creative-variation v2 — the loop closing, proven END-TO-END ON DISK (the project's Integrate bar).

The unit tests prove the gated scorer (`best_hooks`) and that `request_captions` injects the hint;
this integration test proves the whole arrow lands on real artifacts: a REAL ledger.json on disk
where account @a's "WIN" hook clearly out-lifts @b's "LOSE" over >= MIN_POSTS analyzed posts, reloaded
from disk (so the variant posts genuinely round-trip the on-disk ledger — no in-memory mocking), then
`request_captions` with learning ON, then the ACTUAL caption request file read back from
`04_agent_io/requests/` — asserting the winning hook reached the agent request, AND that the committed
`caption_prompt` rendered from that on-disk payload carries the win as a STYLE cue. That is the open
A/B loop, closed, observed on disk rather than asserted in memory."""
from __future__ import annotations
import json
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, MomentState, ClipState, PostState, Platform)
from fanops.caption import request_captions
from fanops.prompts import caption_prompt
from fanops.agentstep import request_path

pytestmark = pytest.mark.integration


def _seed_on_disk_ledger(cfg: Config) -> None:
    """Build a real ledger.json on disk: a captionable clip (with its parent moment + source) plus
    a clear per-surface A/B winner — @a/instagram's "WIN" hook (mean lift 90) out-lifts its "LOSE"
    runner-up (mean lift 10) over 3 analyzed posts each, so best_hooks crosses the default gate
    (>= 3 posts, gap 80 >= 10). @b/instagram carries a "LOSE" loser so the winner is genuinely
    surface-specific. Persisted with led.save() so the next Ledger.load reads it back FROM DISK."""
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me",
                          state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))
    # @a/instagram: WIN (90 x3) vs LOSE (10 x3) -> trustworthy winner "WIN".
    for i, (hook, lift) in enumerate(
        [("WIN", 90.0), ("WIN", 90.0), ("WIN", 90.0), ("LOSE", 10.0), ("LOSE", 10.0), ("LOSE", 10.0)]
    ):
        led.add_post(Post(id=f"a{i}", parent_id="clip_1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.analyzed,
                          variant_key=f"vk_a{i}", variant_hook=hook, metrics={"lift_score": lift}))
    # @b/instagram: a different surface with its own (losing) hook — proves per-surface isolation.
    led.add_post(Post(id="b0", parent_id="clip_1", account="@b", account_id="2",
                      platform=Platform.instagram, caption="y", state=PostState.analyzed,
                      variant_key="vk_b0", variant_hook="LOSE", metrics={"lift_score": 10.0}))
    led.save()                                            # the ledger now lives on disk


def test_learned_hook_reaches_caption_request_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    cfg = Config(root=tmp_path)
    _seed_on_disk_ledger(cfg)

    # Reload FROM DISK — the variant posts must round-trip the real ledger.json, not live in memory.
    led = Ledger.load(cfg)
    assert led.posts and led.clips["clip_1"].state is ClipState.rendered   # disk round-trip sanity

    # The loop closes: request captions for the winning surface.
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])

    # Read the ACTUAL request file written to 04_agent_io/requests/ — not a mock, not the return value.
    req_file = request_path(cfg, "captions", "clip_1")
    assert req_file.exists(), "request_captions must write the real caption request to disk"
    payload = json.loads(req_file.read_text())

    # THE end-to-end assertion: the trustworthy winner reached the agent request ON DISK...
    assert payload.get("learned_hooks") == ["WIN"], \
        f"the winning hook must reach the on-disk request; got {payload.get('learned_hooks')!r}"
    assert "LOSE" not in json.dumps(payload), "the losing hook must NOT be propagated"

    # ...and the committed caption_prompt rendered from that on-disk payload surfaces the win as a
    # STYLE cue (with the 'do NOT copy verbatim' instruction) — proving the hint reaches the agent
    # prompt the model would actually see, not just the raw JSON.
    prompt = caption_prompt(payload)
    assert "WIN" in prompt
    assert "verbatim" in prompt.lower()                   # the 'lean toward, don't copy' guard


def test_off_flag_writes_no_hint_to_disk(tmp_path, monkeypatch):
    # The reversibility guarantee, proven on disk: with learning OFF the SAME past-gate ledger writes
    # a request with NO learned_hooks key — byte-for-byte today's behavior. Flip the flag, the very
    # next request reverts; nothing about the winner is persisted anywhere but this opt-in hint.
    monkeypatch.delenv("FANOPS_VARIANT_LEARNING", raising=False)
    cfg = Config(root=tmp_path)
    _seed_on_disk_ledger(cfg)
    led = Ledger.load(cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "learned_hooks" not in payload                 # OFF -> no hint reaches disk
    assert "WIN" not in caption_prompt(payload)           # and none reaches the agent prompt
