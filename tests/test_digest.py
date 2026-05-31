from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Clip, Post, SourceState, ClipState, PostState, Platform
from fanops.agentstep import write_request
from fanops.digest import render_digest

def test_counts_holds_failures(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/x", state=SourceState.transcribed))
    led.add_source(Source(id="s2", source_path="/y", state=SourceState.error, error_reason="bad codec"))
    led.add_clip(Clip(id="c1", parent_id="m1", path="/c", state=ClipState.held, held=True, held_reason="begging"))
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.failed,
                      error_reason="blotato 422"))
    md = render_digest(led, cfg)
    assert "# FAN OPS Ledger Digest" in md
    assert "Sources" in md and "transcribed" in md
    assert "Brand-risk holds" in md and "begging" in md
    assert "Failures" in md and "blotato 422" in md and "bad codec" in md

def test_lists_pending_agent_steps(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    write_request(cfg, kind="moments", key="s1", payload={"source_id": "s1"})
    write_request(cfg, kind="captions", key="c1", payload={"clip_id": "c1"})
    md = render_digest(led, cfg)
    assert "Awaiting agent" in md and "moments: s1" in md and "captions: c1" in md
