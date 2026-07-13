# tests/test_pipeline_status_perf.py
"""ISSUE-1 step-1: source_backlog must scan the gate dir O(files), not O(sources x files).

The two acceptance criteria that are NOT already covered by test_pipeline_status.py:
  1. pending()/the request-dir glob is invoked O(1) in the number of sources for a full
     source_backlog / visible_source_ids / top_wait_line render — NOT once per source.
  2. no _dir() mkdir occurs on any read path — Path.mkdir must not fire during a render.

Plus a byte-identical behavioral guard on a MULTI-source fixture that exercises every bucket at
once, so the O(files) rewrite cannot silently change a bucket, count, wait-line, or ordering."""

from fanops import agentstep
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.agentstep import write_request
from fanops.pipeline_status import (
    source_backlog, visible_source_ids, top_wait_line, source_wait_line,
)


def _seed_multi_source_every_bucket(cfg, n_gated=6):
    """A ledger touching every backlog bucket, with n_gated sources each owning a real pending gate.
    Returns the list of gated source ids (sorted) so a test can scale n_gated and watch the scan cost."""
    gated = [f"src_g{i:02d}" for i in range(n_gated)]
    with Ledger.transaction(cfg) as led:
        # held / inventory / recoverable — no gates
        led.add_source(Source(id="src_held", source_path="/h.mp4", state=SourceState.pending))
        led.add_source(Source(id="src_inv", source_path="/i.mp4", state=SourceState.discovered))
        led.add_source(Source(id="src_err", source_path="/e.mp4", state=SourceState.error, error_reason="boom"))
        # blocked_on_gates — moments_requested + a moments gate each
        for sid in gated:
            led.add_source(Source(id=sid, source_path=f"/{sid}.mp4", state=SourceState.moments_requested))
    for sid in gated:
        write_request(cfg, kind="moments", key=sid, payload={"source_id": sid})
    return sorted(gated)


def _spy_pending(monkeypatch):
    """Count how many times the request-dir scan inside pending() fires. Returns a mutable counter dict."""
    counter = {"n": 0}
    real_pending = agentstep.pending

    def counted(cfg, *, kind):
        counter["n"] += 1
        return real_pending(cfg, kind=kind)

    # Patch the name pipeline_status bound at import (from fanops.agentstep import pending).
    monkeypatch.setattr("fanops.pipeline_status.pending", counted)
    return counter


def test_source_backlog_scan_is_o1_in_sources(tmp_path, monkeypatch):
    """A full source_backlog render at 3 gated sources and at 9 gated sources must invoke pending()
    the SAME number of times — the scan is O(files/kinds), never O(sources)."""
    from fanops.pipeline import GATE_KINDS
    n_kinds = len(GATE_KINDS)

    cfg_small = Config(root=tmp_path / "small")
    _seed_multi_source_every_bucket(cfg_small, n_gated=3)
    led_small = Ledger.load(cfg_small)
    counter = _spy_pending(monkeypatch)
    source_backlog(led_small, cfg_small)
    small = counter["n"]

    cfg_big = Config(root=tmp_path / "big")
    _seed_multi_source_every_bucket(cfg_big, n_gated=9)
    led_big = Ledger.load(cfg_big)
    counter = _spy_pending(monkeypatch)
    source_backlog(led_big, cfg_big)
    big = counter["n"]

    # Constant in source count. The pre-fix code called pending() ~3x per source per kind, so this
    # count tripled between 3 and 9 sources; O(files) makes it a fixed handful of full scans.
    assert small == big, f"pending() scaled with sources: {small} at n=3 vs {big} at n=9"
    # And it is a small constant multiple of the gate-kind count, not per-source.
    assert big <= n_kinds * 4, f"expected O(kinds) scans, got {big} for {n_kinds} kinds"


def test_no_mkdir_on_read_path(tmp_path, monkeypatch):
    """source_backlog is a pure READ — _dir() must not mkdir on any read. Spy Path.mkdir, assert zero."""
    cfg = Config(root=tmp_path)
    _seed_multi_source_every_bucket(cfg, n_gated=4)
    led = Ledger.load(cfg)

    from pathlib import Path
    calls = []
    real_mkdir = Path.mkdir

    def spy_mkdir(self, *a, **k):
        calls.append(str(self))
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", spy_mkdir)
    source_backlog(led, cfg)
    assert calls == [], f"read path called mkdir: {calls}"


def test_visible_and_top_wait_no_mkdir(tmp_path, monkeypatch):
    """The other two status entry points are read-only too."""
    cfg = Config(root=tmp_path)
    _seed_multi_source_every_bucket(cfg, n_gated=4)
    led = Ledger.load(cfg)

    from pathlib import Path
    calls = []
    real_mkdir = Path.mkdir

    def spy_mkdir(self, *a, **k):
        calls.append(str(self))
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", spy_mkdir)
    visible_source_ids(led, cfg)
    top_wait_line(cfg, led)
    assert calls == [], f"read path called mkdir: {calls}"


def test_multi_source_buckets_and_waitlines_exact(tmp_path):
    """Byte-identical behavioral guard: pin the exact bucket, per-bucket count, row ordering, and
    every row's wait-line on the multi-bucket fixture, so the O(files) rewrite cannot drift them."""
    cfg = Config(root=tmp_path)
    gated = _seed_multi_source_every_bucket(cfg, n_gated=3)
    led = Ledger.load(cfg)
    bl = source_backlog(led, cfg)

    assert (bl.held, bl.inventory, bl.recoverable, bl.blocked_on_gates, bl.actionable) == (1, 1, 1, 3, 0)

    # rows are sorted by source id (source_backlog iterates sorted(led.sources.items()))
    by_id = {r.id: r for r in bl.rows}
    assert [r.id for r in bl.rows] == sorted(by_id)
    assert by_id["src_held"].bucket == "held"
    assert by_id["src_inv"].bucket == "inventory"
    assert by_id["src_err"].bucket == "recoverable"
    for sid in gated:
        r = by_id[sid]
        assert r.bucket == "blocked_on_gates"
        assert r.wait_line == f"wait=moments_requested:moments:{sid} (attempt 0/3)"

    # top_wait_line is the oldest pending gate; source_wait_line matches per source.
    assert top_wait_line(cfg, led) is not None
    for sid in gated:
        assert source_wait_line(cfg, led, sid) == f"wait=moments_requested:moments:{sid} (attempt 0/3)"
