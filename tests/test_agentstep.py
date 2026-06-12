# tests/test_agentstep.py
import json
from fanops.config import Config
from fanops.models import MomentDecision
from fanops.agentstep import write_request, read_response, pending, response_path, latest_request_id

def test_write_request_creates_file_with_id(tmp_path):
    cfg = Config(root=tmp_path)
    rid = write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    assert rid and latest_request_id(cfg, "moments", "src_1") == rid

def test_pending_lists_until_matching_response(tmp_path):
    cfg = Config(root=tmp_path)
    rid = write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    assert pending(cfg, kind="moments") == ["src_1"]
    response_path(cfg, "moments", "src_1").write_text(json.dumps(
        {"source_id": "src_1", "request_id": rid, "picks": []}))
    assert pending(cfg, kind="moments") == []

def test_stale_response_is_ignored(tmp_path):
    cfg = Config(root=tmp_path)
    write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    # answer with a WRONG request_id (stale)
    response_path(cfg, "moments", "src_1").write_text(json.dumps(
        {"source_id": "src_1", "request_id": "STALE", "picks": [{"start":1,"end":2,"reason":"x"}]}))
    assert read_response(cfg, "moments", "src_1", MomentDecision) is None
    assert pending(cfg, kind="moments") == ["src_1"]       # still pending

def test_matching_response_validates(tmp_path):
    cfg = Config(root=tmp_path)
    rid = write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    response_path(cfg, "moments", "src_1").write_text(json.dumps({
        "source_id": "src_1", "request_id": rid,
        "picks": [{"start": 1.0, "end": 8.0, "reason": "bar lands"}]}))
    dec = read_response(cfg, "moments", "src_1", MomentDecision)
    assert isinstance(dec, MomentDecision) and dec.picks[0].end == 8.0

def test_rewrite_invalidates_prior_response(tmp_path):
    cfg = Config(root=tmp_path)
    rid1 = write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    # agent answers the first request correctly
    response_path(cfg, "moments", "src_1").write_text(json.dumps({
        "source_id": "src_1", "request_id": rid1,
        "picks": [{"start": 1.0, "end": 8.0, "reason": "bar lands"}]}))
    assert read_response(cfg, "moments", "src_1", MomentDecision) is not None
    assert pending(cfg, kind="moments") == []

    # the request is re-written (e.g. amplify) → new id, prior response invalidated on disk
    rid2 = write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1", "v": 2})
    assert rid2 != rid1
    assert not response_path(cfg, "moments", "src_1").exists()   # (a) old response unlinked
    assert read_response(cfg, "moments", "src_1", MomentDecision) is None  # (b) nothing matches yet
    assert pending(cfg, kind="moments") == ["src_1"]

    # even a stale answer carrying the OLD id must not satisfy the new request
    response_path(cfg, "moments", "src_1").write_text(json.dumps({
        "source_id": "src_1", "request_id": rid1, "picks": []}))
    assert read_response(cfg, "moments", "src_1", MomentDecision) is None
    assert pending(cfg, kind="moments") == ["src_1"]
