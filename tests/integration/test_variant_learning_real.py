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


def _add_analyzed(led, pid, account, aid, hook, lift, *, platform=Platform.instagram):
    """P9: hook lives on the owner moment; each analyzed post gets its own moment+clip lineage."""
    mid, cid = f"m_{pid}", f"c_{pid}"
    led.add_moment(Moment(id=mid, parent_id="src_1", content_token=pid, start=0, end=7, reason="r",
                          transcript_excerpt="they slept on me", state=MomentState.clipped, hook=hook))
    led.add_clip(Clip(id=cid, parent_id=mid, path=f"/{cid}.mp4", state=ClipState.rendered))
    led.add_post(Post(id=pid, parent_id=cid, account=account, account_id=aid, platform=platform,
                      caption="x", state=PostState.analyzed, metrics={"lift_score": lift},
                      public_url=f"dryrun://{cid}"))


def _seed_on_disk_ledger(cfg: Config) -> None:
    """Build a real ledger.json on disk: a captionable clip plus per-hook analyzed lineages."""
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))
    for i, (hook, lift) in enumerate(
        [("WIN", 90.0), ("WIN", 90.0), ("WIN", 90.0), ("LOSE", 10.0), ("LOSE", 10.0), ("LOSE", 10.0)]
    ):
        _add_analyzed(led, f"a{i}", "a", "1", hook, lift)
    _add_analyzed(led, "b0", "b", "2", "LOSE", 10.0)
    led.save()


def test_learned_hook_reaches_caption_request_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    cfg = Config(root=tmp_path)
    _seed_on_disk_ledger(cfg)

    led = Ledger.load(cfg)
    assert led.posts and led.clips["clip_1"].state is ClipState.rendered

    led = request_captions(led, cfg, "clip_1", [("a", Platform.instagram)])

    req_file = request_path(cfg, "captions", "clip_1")
    assert req_file.exists(), "request_captions must write the real caption request to disk"
    payload = json.loads(req_file.read_text())

    assert payload.get("learned_hooks") == ["WIN"], \
        f"the winning hook must reach the on-disk request; got {payload.get('learned_hooks')!r}"
    assert "LOSE" not in json.dumps(payload), "the losing hook must NOT be propagated"

    prompt = caption_prompt(payload)
    assert "WIN" in prompt
    assert "verbatim" in prompt.lower()


def test_off_flag_writes_no_hint_to_disk(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_VARIANT_LEARNING", raising=False)
    cfg = Config(root=tmp_path)
    _seed_on_disk_ledger(cfg)
    led = Ledger.load(cfg)
    led = request_captions(led, cfg, "clip_1", [("a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "learned_hooks" not in payload
    assert "WIN" not in caption_prompt(payload)
