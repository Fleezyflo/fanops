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
