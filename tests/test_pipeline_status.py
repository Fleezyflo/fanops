# tests/test_pipeline_status.py
"""Pipeline control-plane status: run=, wait=, and source visibility for stuck gates."""

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, ClipState, Moment, MomentState, Source, SourceState
from fanops.agentstep import write_request
from fanops.pipeline_status import top_wait_line, visible_source_ids, source_wait_line, source_backlog
from fanops.cli import cmd_status


def _moments_decided_with_caption_gate(cfg, *, sid="src_1", clip_id="clip_1"):
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id=sid, source_path="/x.mp4", state=SourceState.moments_decided))
        led.add_moment(Moment(id="m1", parent_id=sid, state=MomentState.decided,
                              content_token="tok", start=0.0, end=5.0, reason="pick"))
        led.add_clip(Clip(id=clip_id, parent_id="m1", state=ClipState.captions_requested,
                          path="/x/clip.mp4", duration=5.0))
    write_request(cfg, kind="captions", key=clip_id, payload={"clip_id": clip_id})


def test_moments_decided_source_with_caption_gate_visible_in_status(tmp_path, capsys):
    cfg = Config(root=tmp_path)
    _moments_decided_with_caption_gate(cfg)
    led = Ledger.load(cfg)
    assert "src_1" in visible_source_ids(led, cfg)
    cmd_status(cfg)
    out = capsys.readouterr().out
    assert "src_1 state=moments_decided" in out
    assert "wait=moments_decided:captions:clip_1" in out


def test_top_wait_matches_pending_count(tmp_path):
    cfg = Config(root=tmp_path)
    _moments_decided_with_caption_gate(cfg)
    led = Ledger.load(cfg)
    from fanops.agentstep import pending
    assert len(pending(cfg, kind="captions")) == 1
    assert top_wait_line(cfg, led) is not None
    assert "captions:clip_1" in top_wait_line(cfg, led)


def test_corrupt_request_surfaces_wait_error(tmp_path):
    cfg = Config(root=tmp_path)
    _moments_decided_with_caption_gate(cfg)
    req = cfg.agent_io / "requests" / "captions__clip_1.request.json"
    req.write_text("{not json")
    led = Ledger.load(cfg)
    line = source_wait_line(cfg, led, "src_1")
    assert line == "wait=error:captions:clip_1"


def test_corrupt_gate_quarantines_source_on_heal(tmp_path):
    cfg = Config(root=tmp_path)
    _moments_decided_with_caption_gate(cfg)
    req = cfg.agent_io / "requests" / "captions__clip_1.request.json"
    req.write_text("{not json")
    with Ledger.transaction(cfg) as led:
        from fanops.pipeline_status import heal_corrupt_gates
        assert heal_corrupt_gates(led, cfg) == 1
    s = Ledger.load(cfg).sources["src_1"]
    assert s.state is SourceState.error and "corrupt gate" in (s.error_reason or "")
    bl = source_backlog(Ledger.load(cfg), cfg)
    assert bl.recoverable == 1


def test_moments_decided_without_gate_or_clip_hidden(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_done", source_path="/x.mp4", state=SourceState.moments_decided))
    led = Ledger.load(cfg)
    assert "src_done" not in visible_source_ids(led, cfg)


def test_source_backlog_moments_decided_is_inventory(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_done", source_path="/x.mp4", state=SourceState.moments_decided))
    bl = source_backlog(Ledger.load(cfg), cfg)
    assert bl.actionable == 0 and bl.inventory == 1
    assert bl.rows[0].bucket == "inventory"


def test_source_backlog_retired_and_discovered_are_inventory(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_r", source_path="/r.mp4", state=SourceState.retired))
        led.add_source(Source(id="src_d", source_path="/d.mp4", state=SourceState.discovered))
    bl = source_backlog(Ledger.load(cfg), cfg)
    assert bl.inventory == 2 and bl.actionable == 0


def test_source_backlog_moments_requested_with_gate_is_blocked(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/x.mp4", state=SourceState.moments_requested))
    write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    bl = source_backlog(Ledger.load(cfg), cfg)
    assert bl.blocked_on_gates == 1 and bl.actionable == 0


def test_source_backlog_error_is_recoverable(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_e", source_path="/e.mp4", state=SourceState.error, error_reason="boom"))
    bl = source_backlog(Ledger.load(cfg), cfg)
    assert bl.recoverable == 1 and bl.rows[0].bucket == "recoverable"


def test_source_backlog_shows_artifact_summary(tmp_path):
    cfg = Config(root=tmp_path)
    from fanops.artifacts import stamp_stage
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_e", source_path="/e.mp4", state=SourceState.error, error_reason="boom"))
    stamp_stage(cfg, "src_e", "transcribe", artifact="transcripts/e.json", schema=1)
    stamp_stage(cfg, "src_e", "signals", artifact="signals/src_e.json", schema=3)
    bl = source_backlog(Ledger.load(cfg), cfg)
    row = next(r for r in bl.rows if r.id == "src_e")
    assert row.artifacts == "transcribe+signals"
