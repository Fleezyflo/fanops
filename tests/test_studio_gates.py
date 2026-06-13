# tests/test_studio_gates.py — Phase 3a: answer the moment/caption agent gates from Studio,
# through the SAME validated Pydantic + agent-IO contract the responder uses (no new write path).
from fanops.config import Config
from fanops.agentstep import write_request, pending, read_response
from fanops.models import MomentDecision, CaptionSet
from fanops.studio import views, actions


def _moments_req(cfg, key="s1"):
    return write_request(cfg, kind="moments", key=key, payload={
        "source_id": key, "duration": 10.0,
        "transcript": [{"start": 0.0, "end": 2.0, "text": "yo"}],
        "signal_peaks": [{"t": 1.0, "score": 0.9}], "language": "en"})

def _captions_req(cfg, key="c1"):
    return write_request(cfg, kind="captions", key=key, payload={
        "clip_id": key, "transcript_excerpt": "yo", "language": "en", "guidance": "",
        "surfaces": [{"surface": "@a|instagram", "platform": "instagram"}]})


# ---- views.gate_rows (lock-free read of the pending request files) -------------------------------
def test_gate_rows_lists_pending_moments_with_context(tmp_path):
    cfg = Config(root=tmp_path); _moments_req(cfg)
    m = [r for r in views.gate_rows(cfg) if r["kind"] == "moments"][0]
    assert m["key"] == "s1" and m["duration"] == 10.0 and m["transcript"] and m["language"] == "en"

def test_gate_rows_lists_pending_captions_with_surfaces(tmp_path):
    cfg = Config(root=tmp_path); _captions_req(cfg)
    c = [r for r in views.gate_rows(cfg) if r["kind"] == "captions"][0]
    assert c["key"] == "c1" and c["surfaces"][0]["surface"] == "@a|instagram"

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
                              {"items": [{"surface": "@a|instagram", "caption": "fire", "language": "en"}]})
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
