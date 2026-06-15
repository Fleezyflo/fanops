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

def test_corrupt_response_is_logged_not_silent(tmp_path):
    # A torn/half-written response.json is swallowed (return None) and looks IDENTICAL to "no
    # response yet" — the agent gate silently stalls with no breadcrumb. Fail-closed is correct
    # (a corrupt answer must never be applied), but it must leave ONE log line so an operator can
    # tell "stuck on corrupt JSON" apart from "still pending". The None return is unchanged.
    cfg = Config(root=tmp_path)
    write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    response_path(cfg, "moments", "src_1").write_text("{ this is not json")
    assert read_response(cfg, "moments", "src_1", MomentDecision) is None     # fail-closed (unchanged)
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "src_1" in log and "corrupt" in log.lower()

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

def test_write_request_is_atomic_via_os_replace(tmp_path, mocker):
    # FIX 3: write_request did a plain p.write_text (a reader could see a half-written request; the
    # implicit "all writers hold the ledger flock" was the only thing making a torn read safe). It
    # must write to a temp path then os.replace it into place (the ledger._save_unlocked pattern), so
    # the swap-in is atomic. Spy os.replace to bind that the atomic path is actually taken; the
    # request must still round-trip and leave no temp orphan.
    cfg = Config(root=tmp_path)
    import fanops.agentstep as ag
    spy = mocker.spy(ag.os, "replace")
    rid = write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    final = cfg.agent_io / "requests" / "moments__src_1.request.json"
    assert spy.call_count == 1 and str(spy.call_args.args[1]) == str(final)   # temp -> final via os.replace
    leftovers = [p.name for p in final.parent.iterdir() if p.name != final.name]
    assert leftovers == []                                     # no temp orphan left behind
    assert latest_request_id(cfg, "moments", "src_1") == rid   # round-trips
    assert json.loads(final.read_text())["request_id"] == rid

def test_pending_logs_breadcrumb_on_corrupt_response(tmp_path):
    # FIX 4: pending()'s `except Exception: ok=False` swallowed a torn response.json with NO log,
    # unlike read_response/latest_request_id which log. A corrupt response there silently keeps the
    # key pending with no breadcrumb. pending() must leave one log line (key still returned pending).
    cfg = Config(root=tmp_path)
    write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    response_path(cfg, "moments", "src_1").write_text("{ not json")
    assert pending(cfg, kind="moments") == ["src_1"]           # still pending (unchanged)
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "src_1" in log and "corrupt_response_in_pending" in log
