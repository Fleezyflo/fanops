# tests/test_pipeline_status_perf.py
"""ISSUE-1 step-1: source_backlog is a read — no mkdir on the render path — plus a byte-identical
behavioral guard on a MULTI-source fixture that exercises every bucket at once."""

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.agentstep import write_request
from fanops.pipeline_status import (
    source_backlog, visible_source_ids, top_wait_line, source_wait_line,
)


def _seed_multi_source_every_bucket(cfg, n_gated=6):
    gated = [f"src_g{i:02d}" for i in range(n_gated)]
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_held", source_path="/h.mp4", state=SourceState.pending))
        led.add_source(Source(id="src_inv", source_path="/i.mp4", state=SourceState.discovered))
        led.add_source(Source(id="src_err", source_path="/e.mp4", state=SourceState.error, error_reason="boom"))
        for sid in gated:
            led.add_source(Source(id=sid, source_path=f"/{sid}.mp4", state=SourceState.moments_requested))
    for sid in gated:
        write_request(cfg, kind="moments", key=sid, payload={"source_id": sid})
    return sorted(gated)


def _dirs(root):
    return {p for p in root.rglob("*") if p.is_dir()}


def test_no_mkdir_on_read_path(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_multi_source_every_bucket(cfg, n_gated=4)
    led = Ledger.load(cfg)
    before = _dirs(tmp_path)
    source_backlog(led, cfg)
    assert _dirs(tmp_path) == before


def test_visible_and_top_wait_no_mkdir(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_multi_source_every_bucket(cfg, n_gated=4)
    led = Ledger.load(cfg)
    before = _dirs(tmp_path)
    visible_source_ids(led, cfg)
    top_wait_line(cfg, led)
    assert _dirs(tmp_path) == before


def test_multi_source_buckets_and_waitlines_exact(tmp_path):
    cfg = Config(root=tmp_path)
    gated = _seed_multi_source_every_bucket(cfg, n_gated=3)
    led = Ledger.load(cfg)
    bl = source_backlog(led, cfg)

    assert (bl.held, bl.inventory, bl.recoverable, bl.blocked_on_gates, bl.actionable) == (1, 1, 1, 3, 0)

    by_id = {r.id: r for r in bl.rows}
    assert [r.id for r in bl.rows] == sorted(by_id)
    assert by_id["src_held"].bucket == "held"
    assert by_id["src_inv"].bucket == "inventory"
    assert by_id["src_err"].bucket == "recoverable"
    for sid in gated:
        r = by_id[sid]
        assert r.bucket == "blocked_on_gates"
        assert r.wait_line == f"wait=moments_requested:moments:{sid} (attempt 0/3)"

    assert top_wait_line(cfg, led) is not None
    for sid in gated:
        assert source_wait_line(cfg, led, sid) == f"wait=moments_requested:moments:{sid} (attempt 0/3)"
