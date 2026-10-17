"""Smart, need-aware driving (council: Path B). The pipeline is ALREADY need-aware for claude —
`answer_pending` only spawns `claude` for genuinely pending gates. This pins two things that make that
truth LEGIBLE (the operator's real "haphazard" complaint) and idle cheap:
  1. pipeline.pending_gate_count reuses the SAME awaiting predicate the run loop uses (GATE_KINDS +
     agentstep.pending) — no drift, so "is there AI work?" can't lie.
  2. the Home daemon banner surfaces AI on/off + the pending-gate count."""
from __future__ import annotations
import subprocess

from fanops.config import Config
from fanops import pipeline
from fanops import daemon
from fanops.agentstep import write_request


def test_gate_kinds_is_derived_from_responder_schema():
    import subprocess as sp, sys
    code = ("import fanops.responder as r\n"
            "r._SCHEMA = {**r._SCHEMA, 'zzz_probe_gate': r.CaptionSet}\n"
            "import fanops.pipeline as p\n"
            "assert 'zzz_probe_gate' in p.GATE_KINDS, ('drift: GATE_KINDS=' + repr(p.GATE_KINDS))\n"
            "print('DERIVED_OK')\n")
    out = sp.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0 and "DERIVED_OK" in out.stdout, (out.stdout + out.stderr)

def test_pending_gate_count_zero_on_empty(tmp_path):
    assert pipeline.pending_gate_count(Config(root=tmp_path)) == 0


def test_pending_gate_count_reuses_real_predicate(tmp_path):
    cfg = Config(root=tmp_path)
    write_request(cfg, kind="moments", key="k1", payload={"source_id": "s1"})
    write_request(cfg, kind="moments", key="k2", payload={"source_id": "s2"})
    assert pipeline.pending_gate_count(cfg) == 2


def test_daemon_health_surfaces_ai_state_and_pending(tmp_path, monkeypatch):
    from fanops.studio import views
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    monkeypatch.setattr(daemon.subprocess, "run", lambda cmd, *a, **k: subprocess.CompletedProcess(
        cmd, 0, '\t"PID" = 4321;\n\t"LastExitStatus" = 0;\n', ""))
    cfg = Config(root=tmp_path)
    for i in range(3):
        write_request(cfg, kind="moments", key=f"k{i}", payload={"source_id": f"s{i}"})
    dh = views.daemon_health(cfg)
    assert dh is not None
    assert dh["responder"] == "llm"
    assert dh["pending_gates"] == 3
