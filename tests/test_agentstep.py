# tests/test_agentstep.py
import json
from pathlib import Path
import fanops.agentstep as agentstep
from fanops.config import Config
from fanops.models import MomentDecision
from fanops.agentstep import write_request, write_response, read_response, pending, response_path, latest_request_id
from fanops.agentstep import _attempts_path, bump_attempts, clear_attempts

def test_write_request_creates_file_with_id(tmp_path):
    cfg = Config(root=tmp_path)
    rid = write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    assert rid and latest_request_id(cfg, "moments", "src_1") == rid


def test_atomic_write_tmp_shares_target_dir(tmp_path, mocker):
    # audit c2-f3: os.replace is atomic only when tmp and target share a filesystem; both the request and the
    # response writers must keep their tmp in the target's OWN directory. Pins it against a refactor to a /tmp
    # scratch dir that would silently break atomicity (the in-code assert also enforces it at runtime).
    cfg = Config(root=tmp_path)
    seen = []
    real = agentstep.os.replace
    mocker.patch("fanops.agentstep.os.replace", side_effect=lambda s, d: (seen.append((Path(s), Path(d))), real(s, d))[1])
    write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    write_response(cfg, "moments", "src_1", json.dumps({"request_id": "x", "moments": []}))
    assert seen and all(src.parent == dst.parent for src, dst in seen)

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
    assert response_path(cfg, "moments", "src_1").exists()   # corrupt JSON kept on disk (unchanged)

def test_schema_invalid_response_is_logged_and_unlinked(tmp_path):
    # H08: present-but-schema-invalid response.json must log a breadcrumb AND unlink the file so
    # the gate is visibly pending (not silently re-read forever). Corrupt JSON keeps the file.
    cfg = Config(root=tmp_path)
    rid = write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    response_path(cfg, "moments", "src_1").write_text(json.dumps(
        {"source_id": "src_1", "request_id": rid, "picks": [{"start": 1.0, "end": 2.0}]}))  # missing reason
    assert read_response(cfg, "moments", "src_1", MomentDecision) is None
    assert not response_path(cfg, "moments", "src_1").exists()
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "src_1" in log and "invalid" in log.lower()
    assert pending(cfg, kind="moments") == ["src_1"]

def test_matching_response_validates(tmp_path):
    cfg = Config(root=tmp_path)
    rid = write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    response_path(cfg, "moments", "src_1").write_text(json.dumps({
        "source_id": "src_1", "request_id": rid,
        "picks": [{"start": 1.0, "end": 8.0, "reason": "bar lands"}]}))
    dec = read_response(cfg, "moments", "src_1", MomentDecision)
    assert isinstance(dec, MomentDecision) and dec.picks[0].end == 8.0

def test_write_response_is_atomic_and_roundtrips(tmp_path):
    # audit (LOW): the responder now writes answers via write_response (temp + os.replace) so a concurrent
    # reader never sees a torn file. Round-trips through read_response and leaves no .tmp sibling behind.
    from fanops.agentstep import write_response
    cfg = Config(root=tmp_path)
    rid = write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    write_response(cfg, "moments", "src_1", json.dumps({
        "source_id": "src_1", "request_id": rid,
        "picks": [{"start": 0.0, "end": 5.0, "reason": "r"}]}))
    dec = read_response(cfg, "moments", "src_1", MomentDecision)
    assert isinstance(dec, MomentDecision) and dec.picks[0].end == 5.0
    assert not list(response_path(cfg, "moments", "src_1").parent.glob("*.tmp"))   # no torn temp left behind

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

# MOL-229 — gate attempts sidecar helpers
def test_attempts_path_is_sidecar_of_request(tmp_path):
    cfg = Config(root=tmp_path)
    p = _attempts_path(cfg, "moments", "src_1")
    req = response_path(cfg, "moments", "src_1")
    assert p.parent == req.parent
    assert p.name == "moments__src_1.attempts.json"

def test_bump_attempts_monotonically_increasing(tmp_path):
    cfg = Config(root=tmp_path)
    assert bump_attempts(cfg, "moments", "src_1") == 1
    assert bump_attempts(cfg, "moments", "src_1") == 2
    assert bump_attempts(cfg, "moments", "src_1") == 3

def test_clear_attempts_resets_to_zero(tmp_path):
    cfg = Config(root=tmp_path)
    bump_attempts(cfg, "moments", "src_1")
    bump_attempts(cfg, "moments", "src_1")
    clear_attempts(cfg, "moments", "src_1")
    assert bump_attempts(cfg, "moments", "src_1") == 1   # starts from 1 again

def test_clear_attempts_idempotent_on_missing(tmp_path):
    cfg = Config(root=tmp_path)
    clear_attempts(cfg, "moments", "src_1")   # must not raise when file absent
    assert bump_attempts(cfg, "moments", "src_1") == 1

def test_attempts_are_per_gate_independent(tmp_path):
    cfg = Config(root=tmp_path)
    bump_attempts(cfg, "moments", "src_1")
    bump_attempts(cfg, "moments", "src_1")
    bump_attempts(cfg, "hooks", "src_1")
    assert bump_attempts(cfg, "moments", "src_1") == 3
    assert bump_attempts(cfg, "hooks", "src_1") == 2

def test_attempts_no_effect_on_request_response(tmp_path):
    cfg = Config(root=tmp_path)
    rid = write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    bump_attempts(cfg, "moments", "src_1")
    bump_attempts(cfg, "moments", "src_1")
    clear_attempts(cfg, "moments", "src_1")
    assert latest_request_id(cfg, "moments", "src_1") == rid   # request untouched
    assert pending(cfg, kind="moments") == ["src_1"]            # still pending

# MOL-231 — clear_attempts lifecycle: write_request + discard_gate
def test_write_request_clears_attempts(tmp_path):
    # A re-written request re-opens the gate; prior attempt count must be reset so the
    # next bump_attempts starts from 1 (not from N+1 after a prior failed run).
    cfg = Config(root=tmp_path)
    write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    bump_attempts(cfg, "moments", "src_1")
    bump_attempts(cfg, "moments", "src_1")
    # second write_request re-opens gate — attempts sidecar must be removed
    write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1", "v": 2})
    assert not _attempts_path(cfg, "moments", "src_1").exists()   # sidecar gone
    assert bump_attempts(cfg, "moments", "src_1") == 1            # count restarts from 1

def test_discard_gate_clears_attempts(tmp_path):
    # Discarding a gate removes its sidecar so a re-created gate under the same key
    # starts with a clean attempt count.
    cfg = Config(root=tmp_path)
    from fanops.agentstep import discard_gate
    write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    bump_attempts(cfg, "moments", "src_1")
    bump_attempts(cfg, "moments", "src_1")
    discard_gate(cfg, "moments", "src_1")
    assert not _attempts_path(cfg, "moments", "src_1").exists()   # sidecar gone
    # re-creating the gate starts clean
    write_request(cfg, kind="moments", key="src_1", payload={"source_id": "src_1"})
    assert bump_attempts(cfg, "moments", "src_1") == 1
