"""Smart, need-aware driving (council: Path B). The pipeline is ALREADY need-aware for claude —
`answer_pending` only spawns `claude` for genuinely pending gates. This pins two things that make that
truth LEGIBLE (the operator's real "haphazard" complaint) and idle cheap:
  1. pipeline.pending_gate_count reuses the SAME awaiting predicate the run loop uses (GATE_KINDS +
     agentstep.pending) — no drift (the Critic's non-negotiable), so "is there AI work?" can't lie.
  2. the Home daemon banner surfaces AI on/off + the pending-gate count, so the operator can SEE that
     claude runs ONLY to answer the N pending gates — never on a blind schedule."""
from __future__ import annotations
from fanops.config import Config
from fanops import pipeline


def test_pending_gate_count_zero_on_empty(tmp_path):
    # Idle pipeline: no gate requests -> zero pending -> a run has no AI work (fast, no claude).
    assert pipeline.pending_gate_count(Config(root=tmp_path)) == 0


def test_pending_gate_count_reuses_real_predicate(tmp_path, monkeypatch):
    # Must reuse the same GATE_KINDS + agentstep.pending the run loop uses — count = sum over kinds.
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(pipeline, "pending", lambda c, *, kind: ["k1", "k2"] if kind == "moments" else [])
    # 2 pending 'moments' + 0 for the other GATE_KINDS
    assert pipeline.pending_gate_count(cfg) == 2


def test_daemon_health_surfaces_ai_state_and_pending(tmp_path, monkeypatch):
    # The Home banner must carry the need-aware truth: whether AI is on + how many gates are pending,
    # so "claude runs only to answer these" is visible instead of looking like a blind schedule.
    from fanops.studio import views
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    monkeypatch.setattr("fanops.daemon.status", lambda c, **k: {"verdict": "alive", "loaded": True,
                        "pid": 1, "last_exit": 0, "heartbeat_age_s": 5})
    monkeypatch.setattr("fanops.daemon.installed_interval", lambda c: 600)
    monkeypatch.setattr(pipeline, "pending_gate_count", lambda c: 3)
    dh = views.daemon_health(Config(root=tmp_path))
    assert dh["responder"] == "llm"
    assert dh["pending_gates"] == 3
