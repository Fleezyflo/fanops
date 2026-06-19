# tests/test_responder_concurrent.py
"""Responder gate fan-out under FANOPS_CONCURRENT_SOURCES (the same flag gates the source map AND the
responder). The contract is EQUIVALENCE: flag-ON produces the SAME response files + the SAME `answered`
count as flag-OFF, the per-key rid_before/rid_after stale-drop guard survives concurrency (each gate is
a unique path + local-variable guard), and WORKERS=1 degenerates to the sequential path. NO timing
assertions — tests inject `model=` so no real claude -p subprocess runs (the 60s pytest timeout is a
deadlock detector, unrelated to the 300s subprocess cap)."""
import json

from fanops.config import Config
from fanops.responder import LlmResponder
from fanops.agentstep import write_request, response_path, read_response, latest_request_id
from fanops.models import MomentDecision


def _seed(cfg, keys):
    for k in keys:
        write_request(cfg, kind="moments", key=k,
                      payload={"source_id": k, "duration": 10.0, "transcript": [], "signal_peaks": [],
                               "language": "en", "guidance": ""})


def _good_model(kind, payload):
    return {"source_id": payload["source_id"],
            "picks": [{"start": 14.0, "end": 18.0, "reason": "punchline",
                       "transcript_excerpt": "they slept on me"}]}


def _responses(cfg, keys):
    # The deterministic per-key response payload (request_id stripped — it differs only by rid, which
    # is content-addressed + identical across runs seeded identically). Compares the ANSWER content.
    out = {}
    for k in keys:
        rp = response_path(cfg, "moments", k)
        if rp.exists():
            d = json.loads(rp.read_text()); d.pop("request_id", None); out[k] = d
    return out


def test_flag_on_same_response_files_and_count(tmp_path, monkeypatch):
    # EQUIVALENCE: N gates answered flag-OFF vs flag-ON yield the SAME response files + the SAME
    # `answered`. Two independent roots seeded identically; compare answer content (rid-independent).
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    keys = ["s0", "s1", "s2", "s3"]

    monkeypatch.delenv("FANOPS_CONCURRENT_SOURCES", raising=False)
    off = Config(root=tmp_path / "off"); _seed(off, keys)
    n_off = LlmResponder(off, model=_good_model).answer_pending(off)

    monkeypatch.setenv("FANOPS_CONCURRENT_SOURCES", "1")
    on = Config(root=tmp_path / "on"); _seed(on, keys)
    n_on = LlmResponder(on, model=_good_model).answer_pending(on)

    assert n_on == n_off == 4
    assert _responses(on, keys) == _responses(off, keys)


def test_workers_one_equals_sequential_responder(tmp_path, monkeypatch):
    # WORKERS=1 degenerates the pool to serial -> identical responses + count to the flag-OFF run.
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    keys = ["s0", "s1", "s2"]

    monkeypatch.delenv("FANOPS_CONCURRENT_SOURCES", raising=False)
    off = Config(root=tmp_path / "off"); _seed(off, keys)
    n_off = LlmResponder(off, model=_good_model).answer_pending(off)

    monkeypatch.setenv("FANOPS_CONCURRENT_SOURCES", "1"); monkeypatch.setenv("FANOPS_CONCURRENT_WORKERS", "1")
    on = Config(root=tmp_path / "on"); _seed(on, keys)
    n_on = LlmResponder(on, model=_good_model).answer_pending(on)

    assert n_on == n_off == 3
    assert _responses(on, keys) == _responses(off, keys)


def test_stale_answer_dropped_under_concurrency(tmp_path, monkeypatch):
    # AUDIT A3 ported to the pooled path: ONE gate is re-seeded (new rid + payload) DURING its own
    # model call; its P1-derived answer MUST be dropped (rid_after != rid_before), gate stays pending.
    # The OTHER gates answer normally — proving the per-key TOCTOU guard is thread-safe (local-variable
    # rid_before/rid_after, unique paths), so concurrency doesn't let a stale answer slip through.
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    monkeypatch.setenv("FANOPS_CONCURRENT_SOURCES", "1")
    cfg = Config(root=tmp_path)
    _seed(cfg, ["good0", "good1", "stale"])
    r_stale_before = latest_request_id(cfg, "moments", "stale")

    def model(kind, payload):
        if payload["source_id"] == "stale":
            # simulate an overlapping `fanops run` re-seeding THIS gate mid-call (new rid + payload)
            write_request(cfg, kind="moments", key="stale",
                          payload={"source_id": "stale", "duration": 99.0, "transcript": [],
                                   "signal_peaks": [], "language": "en", "guidance": "RESEEDED"})
        return _good_model(kind, payload)

    n = LlmResponder(cfg, model=model).answer_pending(cfg)
    assert latest_request_id(cfg, "moments", "stale") != r_stale_before     # the gate WAS re-seeded
    assert read_response(cfg, "moments", "stale", MomentDecision) is None    # stale answer dropped
    # the two clean gates answered; the stale one did not -> answered == 2
    assert n == 2
    assert read_response(cfg, "moments", "good0", MomentDecision) is not None
    assert read_response(cfg, "moments", "good1", MomentDecision) is not None


def test_pool_not_constructed_when_flag_off(tmp_path, monkeypatch, mocker):
    # BYTE-IDENTICAL guard: with the flag OFF, answer_pending takes the sequential path and never
    # constructs a ThreadPoolExecutor. Patch it to raise on construction; the OFF run must NOT raise
    # and must still answer every gate.
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    monkeypatch.delenv("FANOPS_CONCURRENT_SOURCES", raising=False)
    def boom(*a, **k): raise AssertionError("ThreadPoolExecutor constructed on the flag-OFF path")
    mocker.patch("fanops.responder.ThreadPoolExecutor", side_effect=boom)
    cfg = Config(root=tmp_path); _seed(cfg, ["s0", "s1"])
    n = LlmResponder(cfg, model=_good_model).answer_pending(cfg)             # must not raise
    assert n == 2


def test_empty_gates_on(tmp_path, monkeypatch):
    # Edge: nothing pending -> the ON path returns 0 without constructing a pool over an empty list.
    monkeypatch.setenv("FANOPS_RESPONDER", "llm"); monkeypatch.setenv("FANOPS_CONCURRENT_SOURCES", "1")
    cfg = Config(root=tmp_path)
    assert LlmResponder(cfg, model=_good_model).answer_pending(cfg) == 0
