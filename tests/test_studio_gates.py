# tests/test_studio_gates.py — Phase 3a: answer the moment/caption agent gates from Studio,
# through the SAME validated Pydantic + agent-IO contract the responder uses (no new write path).
from fanops.config import Config
from fanops.agentstep import write_request, pending, read_response
from fanops.models import MomentDecision, MomentHookDecision, CaptionSet
from fanops.studio import views, actions


def _moments_req(cfg, key="s1"):
    return write_request(cfg, kind="moments", key=key, payload={
        "source_id": key, "duration": 10.0,
        "transcript": [{"start": 0.0, "end": 2.0, "text": "yo"}],
        "signal_peaks": [{"t": 1.0, "score": 0.9}], "language": "en"})

def _moment_hooks_req(cfg, key="s1.1.00-9.00"):
    # M1b PASS 2: a per-pick frame-seeing hook gate (key = source.token), carrying the picked window +
    # the personas the manual form renders a per-account field for.
    return write_request(cfg, kind="moment_hooks", key=key, payload={
        "source_id": "s1", "moment_id": "m1", "token": "1.00-9.00", "start": 1.0, "end": 9.0,
        "reason": "the bar lands", "transcript_excerpt": "they slept on me", "language": "en",
        "frames": [], "signal_peaks": [], "personas": [{"handle": "@a", "persona": "underground"}]})

def _captions_req(cfg, key="c1"):
    return write_request(cfg, kind="captions", key=key, payload={
        "clip_id": key, "transcript_excerpt": "yo", "language": "en", "guidance": "",
        "surfaces": [{"surface": "a|instagram", "platform": "instagram"}]})


# ---- views.gate_rows (lock-free read of the pending request files) -------------------------------
def test_gate_rows_lists_pending_moments_with_context(tmp_path):
    cfg = Config(root=tmp_path); _moments_req(cfg)
    m = [r for r in views.gate_rows(cfg) if r["kind"] == "moments"][0]
    assert m["key"] == "s1" and m["duration"] == 10.0 and m["transcript"] and m["language"] == "en"

def test_gate_rows_lists_pending_captions_with_surfaces(tmp_path):
    cfg = Config(root=tmp_path); _captions_req(cfg)
    c = [r for r in views.gate_rows(cfg) if r["kind"] == "captions"][0]
    assert c["key"] == "c1" and c["surfaces"][0]["surface"] == "a|instagram"

def test_gate_rows_surfaces_corrupt_request_as_dismissible(tmp_path):
    # Corrupt request files are surfaced as dismiss-only rows (corrupt=True) — never as an empty,
    # unanswerable gate form whose blank submission could write a bad gate answer (ecc audit).
    from fanops.agentstep import request_path
    cfg = Config(root=tmp_path); _moments_req(cfg)              # one valid pending moments gate
    request_path(cfg, "moments", "s1").write_text("{ not json")  # corrupt it on disk
    rows = views.gate_rows(cfg)
    hit = [r for r in rows if r.get("key") == "s1"]
    assert len(hit) == 1 and hit[0].get("corrupt") is True
    assert "transcript" not in hit[0]                            # no empty answer form payload

def test_gate_rows_empty_when_nothing_pending(tmp_path):
    assert views.gate_rows(Config(root=tmp_path)) == []


# ---- actions.answer_gate (validated write through the contract) ----------------------------------
def test_answer_moments_writes_valid_response_and_clears_gate(tmp_path):
    cfg = Config(root=tmp_path); _moments_req(cfg)
    res = actions.answer_gate(cfg, "moments", "s1",
                              {"picks": [{"start": 1.0, "end": 5.0, "reason": "bar lands"}]})
    assert res.ok
    dec = read_response(cfg, "moments", "s1", MomentDecision)
    assert dec is not None and dec.picks[0].reason == "bar lands" and dec.source_id == "s1"
    assert pending(cfg, kind="moments") == []            # gate cleared (response matches request_id)

def test_answer_captions_writes_valid_response(tmp_path):
    cfg = Config(root=tmp_path); _captions_req(cfg)
    res = actions.answer_gate(cfg, "captions", "c1",
                              {"items": [{"surface": "a|instagram", "caption": "fire", "language": "en"}]})
    assert res.ok
    cs = read_response(cfg, "captions", "c1", CaptionSet)
    assert cs is not None and cs.items[0].caption == "fire"

def test_answer_rejects_invalid_pick_without_writing(tmp_path):
    # A NaN timestamp is rejected by MomentPick's validator BEFORE the response lands — the gate
    # stays pending (a bad browser answer must never write a corrupt response).
    cfg = Config(root=tmp_path); _moments_req(cfg)
    res = actions.answer_gate(cfg, "moments", "s1",
                              {"picks": [{"start": float("nan"), "end": 5.0, "reason": "x"}]})
    assert not res.ok and "finite" in (res.error or "").lower()
    assert pending(cfg, kind="moments") == ["s1"]        # still pending — not written

def test_answer_unknown_key_errors(tmp_path):
    res = actions.answer_gate(Config(root=tmp_path), "moments", "nope", {"picks": []})
    assert not res.ok

def test_answer_unknown_kind_errors(tmp_path):
    res = actions.answer_gate(Config(root=tmp_path), "bogus", "x", {})
    assert not res.ok


# ---- Flask wiring smoke (the tab renders + the POST writes through answer_gate) ------------------
def test_gates_route_renders_pending(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _captions_req(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().get("/gates")
    assert r.status_code == 200 and b"c1" in r.data

def test_gates_answer_caption_route_writes(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _captions_req(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/gates/answer/captions/c1",
                               data={"caption__@a|instagram": "fire", "language__@a|instagram": "en"})
    assert r.status_code == 200
    assert read_response(cfg, "captions", "c1", CaptionSet).items[0].caption == "fire"


# ---- M1b: the frame-seeing hook gate (pass 2) is answerable from Studio too --------------------------
def test_gate_rows_lists_pending_moment_hooks_with_window(tmp_path):
    cfg = Config(root=tmp_path); _moment_hooks_req(cfg)
    g = [r for r in views.gate_rows(cfg) if r["kind"] == "moment_hooks"][0]
    assert g["start"] == 1.0 and g["end"] == 9.0 and g["reason"] == "the bar lands"

def test_answer_moment_hooks_writes_valid_decision_and_clears_gate(tmp_path):
    cfg = Config(root=tmp_path); _moment_hooks_req(cfg)
    res = actions.answer_gate(cfg, "moment_hooks", "s1.1.00-9.00",
                              {"hook": "the part you'll replay"})
    assert res.ok
    dec = read_response(cfg, "moment_hooks", "s1.1.00-9.00", MomentHookDecision)
    assert dec.hook == "the part you'll replay" 
    assert pending(cfg, kind="moment_hooks") == []       # gate cleared (response matches request_id)

def test_answer_moment_hooks_blank_hook_is_a_valid_clean_decision(tmp_path):
    # A blank manual hook -> null -> a CLEAN clip (a valid decision, not an error).
    cfg = Config(root=tmp_path); _moment_hooks_req(cfg)
    res = actions.answer_gate(cfg, "moment_hooks", "s1.1.00-9.00", {"hook": None})
    assert res.ok and read_response(cfg, "moment_hooks", "s1.1.00-9.00", MomentHookDecision).hook is None

def test_gates_answer_moment_hooks_route_parses_form(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _moment_hooks_req(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/gates/answer/moment_hooks/s1.1.00-9.00",
                               data={"hook": "wait for the switch", "persona_hook__@a": "raw bars"})
    assert r.status_code == 200
    dec = read_response(cfg, "moment_hooks", "s1.1.00-9.00", MomentHookDecision)
    assert dec.hook == "wait for the switch" 


# ---- MOL-109 / PKT-3: a length-desynced pick form is a FORM-VALIDATION error, never a silent
#      truncation and never a 500 (zip(strict=True) on the pick_start/pick_end/pick_reason triples;
#      the ValueError re-renders the result partial at HTTP 200 — htmx 2.x drops non-2xx swaps). ----
def test_gates_answer_moments_mismatched_triples_is_validation_error_not_truncation(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _moments_req(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/gates/answer/moments/s1",
                               data={"pick_start": ["1.0", "6.0"], "pick_end": ["5.0", "9.0"],
                                     "pick_reason": ["bar lands"]})     # 2/2/1 — desynced submission
    assert r.status_code == 200                                         # handled swap, not a 500
    assert b"mismatched pick rows" in r.data                            # clear validation message shown
    assert read_response(cfg, "moments", "s1", MomentDecision) is None  # picks NOT ingested
    assert pending(cfg, kind="moments") != []                           # gate still open for a retry

def test_gates_answer_moments_route_matched_triples_still_ingests(tmp_path):
    # Happy path through the SAME route: equal-length triples parse and write the decision unchanged.
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _moments_req(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/gates/answer/moments/s1",
                               data={"pick_start": ["1.0", "6.0"], "pick_end": ["5.0", "9.0"],
                                     "pick_reason": ["bar lands", "the switch"]})
    assert r.status_code == 200
    dec = read_response(cfg, "moments", "s1", MomentDecision)
    assert dec is not None and len(dec.picks) == 2 and dec.picks[1].reason == "the switch"
