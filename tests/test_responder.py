import json
from fanops.config import Config
from fanops.responder import get_responder, ManualResponder

def test_manual_responder_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_RESPONDER", "manual")
    cfg = Config(root=tmp_path)
    r = get_responder(cfg)
    assert isinstance(r, ManualResponder)
    assert r.answer_pending(cfg) == 0                # writes nothing; a human does

def test_llm_responder_writes_valid_response(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    from fanops.agentstep import write_request, response_path
    write_request(cfg, kind="moments", key="src_1",
                  payload={"source_id": "src_1", "duration": 20.0,
                           "transcript": [{"start": 14, "end": 18, "text": "they slept on me"}],
                           "signal_peaks": []})
    def fake_model(kind, payload):
        return {"source_id": payload["source_id"],
                "picks": [{"start": 14.0, "end": 18.0, "reason": "punchline",
                           "transcript_excerpt": "they slept on me"}]}
    from fanops.responder import LlmResponder
    n = LlmResponder(cfg, model=fake_model).answer_pending(cfg)
    assert n == 1
    data = json.loads(response_path(cfg, "moments", "src_1").read_text())
    assert data["picks"][0]["reason"] == "punchline" and "request_id" in data

def test_llm_responder_invalid_output_leaves_gate_pending_not_raise(tmp_path, monkeypatch):
    # NEW contract (audit N1 + decision a): a present-but-invalid model response must NOT raise.
    # It logs and leaves the gate pending (no response file), so the tick survives.
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    from fanops.agentstep import write_request, response_path
    from fanops.responder import LlmResponder
    write_request(cfg, kind="moments", key="src_1",
                  payload={"source_id": "src_1", "duration": 10.0, "transcript": [],
                           "signal_peaks": [], "language": "en", "guidance": ""})
    # model returns a pick missing the required `reason` -> pydantic rejects
    r = LlmResponder(cfg, model=lambda kind, payload: {"picks": [{"start": 1.0, "end": 4.0}]})
    n = r.answer_pending(cfg)                         # must NOT raise
    assert n == 0
    assert not response_path(cfg, "moments", "src_1").exists()   # gate stays pending


def _seed_moment_request(cfg, key="s1"):
    from fanops.agentstep import write_request
    write_request(cfg, kind="moments", key=key,
                  payload={"source_id": key, "duration": 10.0, "transcript": [], "signal_peaks": [],
                           "language": "en", "guidance": ""})

def test_get_responder_llm_is_usable_without_explicit_model(tmp_path, monkeypatch, mocker):
    # gap #1: the production default must be a WORKING model (claude -p), not a stub that raises.
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    _seed_moment_request(cfg)
    # stub the claude -p call at the seam used by the production default model: return one valid pick
    mocker.patch("fanops.responder.claude_json",
                 return_value={"picks": [{"start": 1.0, "end": 4.0, "reason": "bar",
                                          "transcript_excerpt": "x", "signal_score": 0.0}]})
    from fanops.responder import get_responder
    r = get_responder(cfg)
    n = r.answer_pending(cfg)
    assert n == 1
    from fanops.agentstep import response_path
    written = json.loads(response_path(cfg, "moments", "s1").read_text())
    assert written["picks"][0]["start"] == 1.0
    assert "request_id" in written
    assert written["source_id"] == "s1"            # source_id injected for the moments kind

def test_responder_quarantines_one_bad_request_and_answers_the_rest(tmp_path, monkeypatch):
    # H2 / decision b: one request whose model call raises must NOT halt the others.
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    _seed_moment_request(cfg, "good")
    _seed_moment_request(cfg, "bad")
    def model(kind, payload):
        if payload["source_id"] == "bad":
            raise RuntimeError("transient LLM 500")
        return {"picks": [{"start": 1.0, "end": 4.0, "reason": "r"}]}
    from fanops.responder import LlmResponder
    from fanops.agentstep import response_path
    r = LlmResponder(cfg, model=model)
    n = r.answer_pending(cfg)
    assert n == 1                                  # good answered, bad quarantined
    assert response_path(cfg, "moments", "good").exists()
    assert not response_path(cfg, "moments", "bad").exists()   # bad gate left pending

def test_responder_toolchain_missing_is_quarantined_not_crash(tmp_path, monkeypatch):
    # decision b: if `claude` is absent, ToolchainMissingError for one gate must be caught per-request
    # (logged, gate stays pending), NOT crash the whole tick.
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    _seed_moment_request(cfg, "s1")
    from fanops.errors import ToolchainMissingError
    from fanops.responder import LlmResponder
    from fanops.agentstep import response_path
    def absent(kind, payload):
        raise ToolchainMissingError("claude not found on PATH")
    r = LlmResponder(cfg, model=absent)
    n = r.answer_pending(cfg)                       # must NOT raise
    assert n == 0
    assert not response_path(cfg, "moments", "s1").exists()

def test_responder_forces_gate_source_id_over_model_value(tmp_path, monkeypatch):
    # Issue A: the GATE is authoritative for source_id. The claude -p schema marks source_id required,
    # so the model returns one; a hallucinated/mismatched model source_id must NOT win — the gate's
    # source_id (the real lineage parent) must be what lands on disk.
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    from fanops.agentstep import write_request, response_path
    from fanops.responder import LlmResponder
    import json as J
    write_request(cfg, kind="moments", key="real_src",
                  payload={"source_id": "real_src", "duration": 10.0, "transcript": [],
                           "signal_peaks": [], "language": "en", "guidance": ""})
    # model HALLUCINATES a different source_id
    r = LlmResponder(cfg, model=lambda kind, payload: {
        "source_id": "HALLUCINATED_WRONG",
        "picks": [{"start": 1.0, "end": 4.0, "reason": "r"}]})
    n = r.answer_pending(cfg)
    assert n == 1
    written = J.loads(response_path(cfg, "moments", "real_src").read_text())
    assert written["source_id"] == "real_src"   # the GATE wins, not the model's hallucination


def test_responder_drops_stale_answer_when_gate_reseeded_mid_model_call(tmp_path, monkeypatch):
    # AUDIT A3 (answer-stale TOCTOU): answer_pending reads payload P1 (under rid R1), runs the SLOW
    # model call, then reads the rid. If an overlapping `fanops run` re-seeds the gate (new rid R2 +
    # new payload P2) DURING the model call, the OLD code read R2 AFTER the call and stamped the
    # P1-derived answer with R2 -> read_response's freshness check (R2==R2) PASSED -> the wrong-payload
    # answer was applied as fresh. The fix captures the rid BEFORE the model call and re-verifies it is
    # still latest AFTER; on mismatch it drops the stale answer (gate stays pending for the new request).
    # We model the overlapping re-seed by having the injected model itself re-write the request mid-call.
    from fanops.config import Config
    from fanops.responder import LlmResponder
    from fanops.agentstep import write_request, read_response, latest_request_id
    from fanops.models import MomentDecision
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    write_request(cfg, kind="moments", key="s1",
                  payload={"source_id": "s1", "duration": 10.0, "transcript": [], "signal_peaks": [],
                           "language": "en", "guidance": ""})
    r1 = latest_request_id(cfg, "moments", "s1")

    def reseeding_model(kind, payload):
        # Simulate an overlapping `fanops run` re-seeding the gate DURING the slow model call:
        # a NEW request_id + NEW payload land on disk before this (P1-derived) answer is written.
        write_request(cfg, kind="moments", key="s1",
                      payload={"source_id": "s1", "duration": 99.0, "transcript": [], "signal_peaks": [],
                               "language": "en", "guidance": "RESEEDED"})
        return {"picks": [{"start": 1.0, "end": 4.0, "reason": "from-P1"}]}

    n = LlmResponder(cfg, model=reseeding_model).answer_pending(cfg)
    r2 = latest_request_id(cfg, "moments", "s1")
    assert r2 != r1                                   # the gate WAS re-seeded mid-call
    # The stale (P1-derived) answer must NOT be applied as fresh for the new request R2.
    fresh = read_response(cfg, "moments", "s1", MomentDecision)
    assert fresh is None, "stale P1-derived answer was wrongly accepted as fresh for the re-seeded gate (TOCTOU)"
    # And answer_pending must report it did NOT successfully answer the (now-stale) request.
    assert n == 0
