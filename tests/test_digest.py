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

def test_write_digest_creates_file(tmp_path):
    from fanops.digest import write_digest
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/x", state=SourceState.transcribed))
    write_digest(led, cfg)
    assert cfg.digest_path.exists()
    assert "# FAN OPS Ledger Digest" in cfg.digest_path.read_text()

def test_empty_ledger_digest_has_no_sections(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    md = render_digest(led, cfg)
    assert "# FAN OPS Ledger Digest" in md
    assert "(none)" in md                          # empty stores render (none)
    assert "Brand-risk holds" not in md and "Failures" not in md and "Awaiting" not in md

def test_none_reason_renders_fallback(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pf", parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.failed))  # error_reason None
    md = render_digest(led, cfg)
    assert "Failures" in md and "(no reason given)" in md and "None" not in md.split("Failures")[1]

def test_published_unmeasured_surfaced(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    # a published post with NO metrics -> surfaced; a published post WITH metrics -> not
    led.add_post(Post(id="pm", parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.published))  # no metrics
    led.add_post(Post(id="pok", parent_id="c", account="@a", account_id="1",
                      platform=Platform.tiktok, caption="y", state=PostState.published,
                      metrics={"saves": 5, "lift_score": 20.0}))
    md = render_digest(led, cfg)
    assert "Published but unmeasured" in md
    assert "`pm`" in md.split("Published but unmeasured")[1]
    assert "`pok`" not in md.split("Published but unmeasured")[1]   # measured one not listed
