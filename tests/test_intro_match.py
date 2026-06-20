# tests/test_intro_match.py — M6 (structural-hooks): the LLM-vision intro PAIRING matcher gate.
# request_intro_match writes one agent gate per router-reserved (clean_awaiting_strategy:intro_tease)
# moment carrying the clip context vs the candidate third-party intro assets; the llm responder answers ranked
# pairings; ingest writes them onto Moment.intro_matches for the producer. Gated on cfg.intro_tease +
# FANOPS_RESPONDER=llm, FAIL-OPEN (no responder / corrupt answer -> moment stays unmatched, never wedges).
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, SourceState, MomentState
from fanops.agentstep import pending, latest_request_id, response_path
from fanops.router import awaiting
from fanops.intro_match import (request_intro_match, ingest_intro_match,
                                intro_match_pending, MATCHER_VERSION, _candidates)


def _cfg(tmp_path, monkeypatch, *, llm=True, tease=True):
    monkeypatch.setenv("FANOPS_RESPONDER", "llm" if llm else "manual")
    if tease: monkeypatch.setenv("FANOPS_INTRO_TEASE", "1")
    else: monkeypatch.delenv("FANOPS_INTRO_TEASE", raising=False)
    return Config(root=tmp_path)

def _seed(cfg, *, reserve=True, candidates=2):
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path=str(cfg.sources / "s1.mp4"),
                          state=SourceState.moments_decided))
    led.add_moment(Moment(id="m1", parent_id="s1", state=MomentState.decided, start=0.0, end=18.0,
                          reason="he walks on stage", transcript_excerpt="watch this", hook=None,
                          hook_strategy=awaiting("intro_tease") if reserve else "clean_final"))
    for i in range(candidates):
        led.add_source(Source(id=f"i{i+1}", source_path=str(cfg.sources / f"i{i+1}.mp4"),
                              state=SourceState.catalogued, origin_kind="third_party"))
    return led

def _answer(cfg, *, items):
    # simulate the llm responder: write each pending gate's response echoing its request_id
    for key in pending(cfg, kind="intro_match"):
        rid = latest_request_id(cfg, "intro_match", key)
        response_path(cfg, "intro_match", key).write_text(json.dumps({"request_id": rid, "items": items}))


def test_candidates_are_third_party_usable_only(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch); led = _seed(cfg)
    led.add_source(Source(id="nat", source_path="x", origin_kind="native"))           # native -> excluded
    led.add_source(Source(id="ret", source_path="y", origin_kind="third_party",
                          state=SourceState.retired))                                 # retired -> excluded
    assert [s.id for s in _candidates(led)] == ["i1", "i2"]

def test_request_writes_one_gate_with_clip_and_candidates(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch); led = _seed(cfg)
    request_intro_match(led, cfg)
    keys = pending(cfg, kind="intro_match")
    assert len(keys) == 1                                                             # one gate for m1
    from fanops.agentstep import request_path
    payload = json.loads(request_path(cfg, "intro_match", keys[0]).read_text())
    assert payload["clip"]["moment_id"] == "m1" and payload["matcher_version"] == MATCHER_VERSION
    assert {c["asset_id"] for c in payload["candidates"]} == {"i1", "i2"}

def test_no_gate_when_responder_not_llm(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, llm=False); led = _seed(cfg)
    request_intro_match(led, cfg)
    assert pending(cfg, kind="intro_match") == []

def test_no_gate_when_intro_tease_off(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, tease=False); led = _seed(cfg)
    request_intro_match(led, cfg)
    assert pending(cfg, kind="intro_match") == []

def test_no_gate_when_no_candidates(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch); led = _seed(cfg, candidates=0)
    request_intro_match(led, cfg)
    assert pending(cfg, kind="intro_match") == []                                     # benign: nothing to pair

def test_request_is_idempotent(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch); led = _seed(cfg)
    request_intro_match(led, cfg)
    key = pending(cfg, kind="intro_match")[0]
    rid1 = latest_request_id(cfg, "intro_match", key)
    request_intro_match(led, cfg)                                                     # second pass
    assert latest_request_id(cfg, "intro_match", key) == rid1                         # not re-minted

def test_pending_true_until_answered(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch); led = _seed(cfg)
    request_intro_match(led, cfg)
    assert intro_match_pending(led, cfg) is True
    _answer(cfg, items=[{"moment_id": "m1", "asset_id": "i1", "fit_score": 0.9,
                         "rationale": "stage entrance matches the drop", "tease_text": "wait for it"}])
    assert intro_match_pending(led, cfg) is False

def test_ingest_writes_ranked_pairings_to_moment(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch); led = _seed(cfg)
    request_intro_match(led, cfg)
    _answer(cfg, items=[
        {"moment_id": "m1", "asset_id": "i1", "fit_score": 0.6, "rationale": "ok", "tease_text": "wait for it"},
        {"moment_id": "m1", "asset_id": "i2", "fit_score": 0.95, "rationale": "best", "tease_text": "3 incoming"}])
    ingest_intro_match(led, cfg)
    matches = led.moments["m1"].intro_matches
    assert [m["asset_id"] for m in matches] == ["i2", "i1"]                            # best fit first
    assert matches[0]["tease_text"] == "3 incoming" and matches[0]["fit_score"] == 0.95

def test_ingest_noop_until_response(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch); led = _seed(cfg)
    request_intro_match(led, cfg)
    ingest_intro_match(led, cfg)                                                      # no response yet
    assert led.moments["m1"].intro_matches is None

def test_ingest_filters_unknown_asset_and_textless_pairings(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch); led = _seed(cfg)
    request_intro_match(led, cfg)
    _answer(cfg, items=[
        {"moment_id": "m1", "asset_id": "GHOST", "fit_score": 0.99, "rationale": "x", "tease_text": "hi"},  # not a candidate
        {"moment_id": "m1", "asset_id": "i1", "fit_score": 0.5, "rationale": "x", "tease_text": ""},          # no tease text
        {"moment_id": "m1", "asset_id": "i2", "fit_score": 0.4, "rationale": "ok", "tease_text": "wait"}])
    ingest_intro_match(led, cfg)
    assert [m["asset_id"] for m in led.moments["m1"].intro_matches] == ["i2"]          # only the real, renderable pairing

def test_ingest_failopen_on_corrupt_response(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch); led = _seed(cfg)
    request_intro_match(led, cfg)
    for key in pending(cfg, kind="intro_match"):
        response_path(cfg, "intro_match", key).write_text("{not json")
    ingest_intro_match(led, cfg)                                                      # must not raise
    assert led.moments["m1"].intro_matches is None                                    # corrupt -> unmatched
