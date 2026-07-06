import json
from fanops.config import Config
from fanops.responder import get_responder, ManualResponder

def test_manual_responder_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_RESPONDER", "manual")
    cfg = Config(root=tmp_path)
    r = get_responder(cfg)
    assert isinstance(r, ManualResponder)
    assert r.answer_pending(cfg) == 0                # writes nothing; a human does

def test_responder_screens_text_once(tmp_path, monkeypatch):
    # MOL-166: model-authored text is screened ONCE at the responder boundary — em-dashes never land on disk.
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    from fanops.agentstep import write_request, response_path
    from fanops.responder import LlmResponder
    write_request(cfg, kind="moments", key="src_1",
                  payload={"source_id": "src_1", "duration": 20.0,
                           "transcript": [{"start": 14, "end": 18, "text": "they slept on me"}],
                           "signal_peaks": []})
    def fake_model(kind, payload):
        return {"source_id": payload["source_id"],
                "picks": [{"start": 14.0, "end": 18.0, "reason": "punchline — then the beat drops",
                           "transcript_excerpt": "they slept on me"}]}
    n = LlmResponder(cfg, model=fake_model).answer_pending(cfg)
    assert n == 1
    data = json.loads(response_path(cfg, "moments", "src_1").read_text())
    assert "—" not in data["picks"][0]["reason"]
    assert "punchline" in data["picks"][0]["reason"]

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
    # stub the claude -p call at the seam used by the production default model: (one valid pick, model)
    mocker.patch("fanops.responder.claude_json_meta",
                 return_value=({"picks": [{"start": 1.0, "end": 4.0, "reason": "bar",
                                           "transcript_excerpt": "x", "signal_score": 0.0}]}, "opus", False))
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


def test_toctou_guard_intact(tmp_path, monkeypatch):
    # MOL-167: the rid_before/rid_after staleness guard is untouched — mid-call re-seed drops the stale answer.
    _assert_toctou_stale_answer_dropped(tmp_path, monkeypatch)

def test_responder_drops_stale_answer_when_gate_reseeded_mid_model_call(tmp_path, monkeypatch):
    _assert_toctou_stale_answer_dropped(tmp_path, monkeypatch)

def _assert_toctou_stale_answer_dropped(tmp_path, monkeypatch):
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


def test_llm_responder_retries_once_on_timeout(tmp_path, monkeypatch):
    # A transient caption-gate timeout (which stranded 2 clips before) is RETRIED once, then succeeds.
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    from fanops.agentstep import write_request, response_path
    from fanops.responder import LlmResponder
    from fanops.llm import LlmTimeoutError
    write_request(cfg, kind="moments", key="src_1",
                  payload={"source_id": "src_1", "duration": 20.0,
                           "transcript": [{"start": 14, "end": 18, "text": "x"}], "signal_peaks": []})
    calls = {"n": 0}
    def flaky(kind, payload):
        calls["n"] += 1
        if calls["n"] == 1: raise LlmTimeoutError("claude -p timed out after 300s")
        return {"source_id": payload["source_id"],
                "picks": [{"start": 14.0, "end": 18.0, "reason": "punchline"}]}
    n = LlmResponder(cfg, model=flaky).answer_pending(cfg)
    assert n == 1 and calls["n"] == 2                       # retried once, then answered
    assert response_path(cfg, "moments", "src_1").exists()

def test_moments_model_passes_frames_as_images_for_vision(mocker):
    # Phase 1: the AUTHOR is a vision call — the moments gate hands its sampled source frames to
    # claude as images so the hook is written SEEING the footage. The moments payload carries frames
    # at the TOP level.
    from fanops.responder import _default_claude_model
    spy = mocker.patch("fanops.responder.claude_json_meta", return_value=({"picks": []}, None, False))
    _default_claude_model("moments", {"source_id": "s", "duration": 10.0, "frames": ["/k/a.jpg", "/k/b.jpg"]})
    assert spy.call_args.kwargs.get("images") == ["/k/a.jpg", "/k/b.jpg"]

def test_moments_model_without_frames_stays_text_only(mocker):
    from fanops.responder import _default_claude_model
    spy = mocker.patch("fanops.responder.claude_json_meta", return_value=({"picks": []}, None, False))
    _default_claude_model("moments", {"source_id": "s", "duration": 10.0})   # no frames -> fail-open text-only
    assert not spy.call_args.kwargs.get("images")

def test_default_model_pins_llm_model_and_logs_provenance(mocker, tmp_path):
    # V2 M1/F1+F10: the production responder PINS cfg.llm_model on the claude call AND emits one
    # provenance line per creative call (the model that answered + the prompt + brief fingerprints) so
    # every clip/caption is traceable to the EXACT model+brief that produced it.
    cfg = Config(root=tmp_path)
    cfg.control.mkdir(parents=True, exist_ok=True)
    cfg.context_path.write_text("BRAND: confident")
    from fanops.responder import _default_claude_model
    meta = mocker.patch("fanops.responder.claude_json_meta",
                        return_value=({"picks": []}, "claude-opus-4-x", False))   # the model that answered
    logfn = mocker.Mock()
    out = _default_claude_model("moments", {"source_id": "s1", "duration": 10.0}, cfg=cfg, log=logfn)
    assert out == {"picks": []}
    assert meta.call_args.kwargs["model"] == "opus"                        # per-gate pin: moments -> opus (vision author)
    prov = next(c for c in logfn.call_args_list if c.args[2] == "call")     # the provenance line
    assert prov.args[0] == "llm"
    assert prov.kwargs["model"] == "claude-opus-4-x"                        # the answering model surfaced
    assert len(prov.kwargs["prompt_sha"]) == 12                            # prompt fingerprint
    assert prov.kwargs["brief_sha"] != "absent"                            # brief fingerprint present

def test_default_model_provenance_falls_back_to_pinned_when_envelope_lacks_model(mocker, tmp_path):
    # Audit C2/H: when the envelope reports no model, the provenance line records the PINNED value
    # (never empty), and "absent" brief_sha when there's no brief.
    cfg = Config(root=tmp_path)
    from fanops.responder import _default_claude_model
    mocker.patch("fanops.responder.claude_json_meta", return_value=({"picks": []}, None, False))
    logfn = mocker.Mock()
    _default_claude_model("moments", {"source_id": "s1", "duration": 10.0}, cfg=cfg, log=logfn)
    prov = next(c for c in logfn.call_args_list if c.args[2] == "call")
    assert prov.kwargs["model"] == "opus" and prov.kwargs["brief_sha"] == "absent"   # moments -> opus

def test_llm_responder_double_timeout_leaves_gate_pending_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    from fanops.agentstep import write_request, response_path
    from fanops.responder import LlmResponder
    from fanops.llm import LlmTimeoutError
    write_request(cfg, kind="moments", key="src_1",
                  payload={"source_id": "src_1", "duration": 20.0, "transcript": [], "signal_peaks": []})
    def always_timeout(kind, payload): raise LlmTimeoutError("timed out")
    n = LlmResponder(cfg, model=always_timeout).answer_pending(cfg)   # must NOT raise
    assert n == 0                                          # not answered
    assert not response_path(cfg, "moments", "src_1").exists()   # gate stays pending (visible via log)

# --- M1b: the moment_hooks gate (pass 2 — the frame-seeing hook AUTHOR) -----------------------------
def test_moment_hooks_model_passes_window_frames_as_images(mocker):
    # The whole point of the split: the HOOK pass is a vision call grounded in the PICKED WINDOW's
    # frames. The responder must attach moment_hooks `frames` as images (same plumbing as the pick pass).
    from fanops.responder import _default_claude_model
    spy = mocker.patch("fanops.responder.claude_json_meta", return_value=({"hook": "x"}, None, False))
    _default_claude_model("moment_hooks", {"source_id": "s", "moment_id": "m", "token": "1.00-5.00",
                                           "start": 1.0, "end": 5.0, "frames": ["/k/w0.jpg", "/k/w1.jpg"]})
    assert spy.call_args.kwargs.get("images") == ["/k/w0.jpg", "/k/w1.jpg"]

def test_moment_hooks_gate_pins_opus(mocker, tmp_path):
    # The hook author is the CREATIVE vision gate -> opus (the watch-through driver), like the old
    # single-pass moments gate. (The pick pass also stays opus; the cost is owned, see plan D5.)
    cfg = Config(root=tmp_path)
    meta = mocker.patch("fanops.responder.claude_json_meta", return_value=({"hook": "x"}, "claude-opus-4-x", False))
    from fanops.responder import _default_claude_model
    _default_claude_model("moment_hooks", {"source_id": "s", "moment_id": "m", "token": "1.00-5.00",
                                           "start": 1.0, "end": 5.0}, cfg=cfg)
    assert meta.call_args.kwargs["model"] == "opus"

def test_moment_hooks_responder_writes_valid_decision(tmp_path, monkeypatch):
    # End-to-end gate round-trip: a moment_hooks request is answered into a schema-valid
    # MomentHookDecision. Correlation is by the gate KEY (source.token), so NO source_id injection.
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    from fanops.agentstep import write_request, response_path
    from fanops.responder import LlmResponder
    key = "src_1.14.00-21.00"
    write_request(cfg, kind="moment_hooks", key=key,
                  payload={"source_id": "src_1", "moment_id": "m1", "token": "14.00-21.00",
                           "start": 14.0, "end": 21.0, "reason": "punchline",
                           "transcript_excerpt": "they slept on me", "frames": []})
    def fake_model(kind, payload):
        return {"hook": "the line you replay", "hooks_by_persona": {"@a": "for who you can't get over"}}
    n = LlmResponder(cfg, model=fake_model).answer_pending(cfg)
    assert n == 1
    data = json.loads(response_path(cfg, "moment_hooks", key).read_text())
    assert data["hook"] == "the line you replay" and data["hooks_by_persona"]["@a"] and "request_id" in data


# ---- AGENT-2: a context-limit failure becomes a LABELLED degraded source, never an infinite-pending wedge ----
def test_context_limit_failure_marks_source_degraded_not_infinite_pending(tmp_path, monkeypatch):
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    from fanops.agentstep import write_request, response_path
    from fanops.responder import LlmResponder
    from fanops.llm import LlmContextLimitError
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg); led.add_source(Source(id="src_1", source_path="/s.mp4", state=SourceState.signalled)); led.save()
    write_request(cfg, kind="moments", key="src_1",
                  payload={"source_id": "src_1", "duration": 99.0, "transcript": [], "signal_peaks": []})
    def boom(kind, payload): raise LlmContextLimitError("claude -p context limit: prompt is too long")
    n = LlmResponder(cfg, model=boom).answer_pending(cfg)
    assert n == 0
    assert not response_path(cfg, "moments", "src_1").exists()        # no answer written
    src = Ledger.load(cfg).sources["src_1"]
    assert src.degraded_reason and "context limit" in src.degraded_reason.lower()   # LABELLED, not silent-pending


def test_schema_request_id_gate_populated(tmp_path, monkeypatch):
    # MOL-167: a MomentDecision built WITHOUT request_id/source_id from the model validates; the gate injects both.
    from fanops.models import MomentDecision, MomentPick
    from fanops.agentstep import write_request, response_path, latest_request_id
    from fanops.responder import LlmResponder
    dec = MomentDecision(picks=[MomentPick(start=1.0, end=4.0, reason="r")])
    assert not dec.request_id and not dec.source_id
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    write_request(cfg, kind="moments", key="src_1",
                  payload={"source_id": "src_1", "duration": 9.0, "transcript": [], "signal_peaks": []})
    rid = latest_request_id(cfg, "moments", "src_1")
    n = LlmResponder(cfg, model=lambda kind, payload: {"picks": [{"start": 0.0, "end": 7.0, "reason": "drop"}]}).answer_pending(cfg)
    assert n == 1
    data = json.loads(response_path(cfg, "moments", "src_1").read_text())
    assert data["request_id"] == rid and data["source_id"] == "src_1"

def test_gate_stamps_authoritative_rid_ignores_model_echo(tmp_path, monkeypatch):
    # MOL-167: no echo-verify — a garbage model request_id is discarded unconditionally.
    from fanops.agentstep import write_request, response_path, latest_request_id
    from fanops.responder import LlmResponder
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path)
    write_request(cfg, kind="moments", key="src_1",
                  payload={"source_id": "src_1", "duration": 9.0, "transcript": [], "signal_peaks": []})
    rid = latest_request_id(cfg, "moments", "src_1")
    events = []
    monkeypatch.setattr("fanops.responder.get_logger", lambda cfg: (lambda *a, **k: events.append(a)))
    n = LlmResponder(cfg, model=lambda kind, payload: {"request_id": "garbage-rid", "source_id": "wrong",
                "picks": [{"start": 0.0, "end": 7.0, "reason": "drop"}]}).answer_pending(cfg)
    assert n == 1
    data = json.loads(response_path(cfg, "moments", "src_1").read_text())
    assert data["request_id"] == rid and data["source_id"] == "src_1"
    assert not any("rid_mismatch" in ev for ev in events)


def test_context_limit_marks_source_degraded_for_captions_gate(tmp_path):
    # context-limit-no-source-mark: a captions gate that hits the LLM context limit must mark its SOURCE
    # degraded (the no-silent-degradation principle), same as the moment gates. Captions key on a CLIP id, so
    # the source is resolved clip->moment->source. Previously captions was skipped (sid=None) -> the stall was
    # invisible in status/digest and the operator had to grep run.log.
    from fanops.responder import LlmResponder
    from fanops.ledger import Ledger
    from fanops.models import Source, Moment, MomentState, Clip, ClipState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.queued))
    led.save()
    LlmResponder(cfg)._mark_context_limit(cfg, "captions", "clip_1", "payload too big")
    src = Ledger.load(cfg).sources["src_1"]
    assert src.degraded_reason and "captions" in src.degraded_reason and "context limit" in src.degraded_reason
