import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, SourceState, MomentState, ClipState, Platform, MomentDecision, MomentPick
from fanops.agentstep import response_path, request_path, latest_request_id
from fanops.moments import request_moments, ingest_moments, validate_pick

def _src(led, cfg, dur=20.0):
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.signalled, duration=dur, language="en",
                          transcript=[{"start": 0, "end": 3, "text": "intro"},
                                      {"start": 14, "end": 18, "text": "they slept on me"}],
                          signal_peaks=[{"t": 16.0, "kind": "scene_cut", "score": 0.6}],
                          meta={"transcribed": True}))

def test_request_moments_writes_request_with_transcript_signals_language(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    payload = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert payload["duration"] == 20.0
    assert payload["transcript"][1]["text"] == "they slept on me"
    assert payload["signal_peaks"][0]["t"] == 16.0
    assert payload["language"] == "en"
    assert "request_id" in payload
    assert led.sources["src_1"].state is SourceState.moments_requested

def test_validate_pick_rejects_bad_bounds():
    assert validate_pick(MomentPick(start=5, end=3, reason="r"), duration=20.0) is not None  # end<start
    assert validate_pick(MomentPick(start=-1, end=3, reason="r"), duration=20.0) is not None # start<0
    assert validate_pick(MomentPick(start=15, end=25, reason="r"), duration=20.0) is not None# end>dur
    assert validate_pick(MomentPick(start=0, end=5, reason="r"), duration=20.0) is None      # ok

def test_ingest_moments_creates_content_addressed_units(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    rid = latest_request_id(cfg, "moments", "src_1")
    response_path(cfg, "moments", "src_1").write_text(MomentDecision(
        source_id="src_1", request_id=rid,
        picks=[MomentPick(start=14.0, end=18.5, reason="punchline + scene cut at 16",
                          transcript_excerpt="they slept on me", signal_score=0.6)]
    ).model_dump_json())
    led = ingest_moments(led, cfg, "src_1")
    moms = led.moments_of("src_1")
    assert len(moms) == 1
    assert moms[0].content_token == "14.00-18.50"
    assert moms[0].reason.startswith("punchline")
    assert led.sources["src_1"].state is SourceState.moments_decided

def test_amplify_style_reingest_reconciles_not_noop(tmp_path):
    # The v1 bug: a NEW decision must actually replace, update, and cascade-delete.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    rid = latest_request_id(cfg, "moments", "src_1")
    response_path(cfg, "moments", "src_1").write_text(MomentDecision(
        source_id="src_1", request_id=rid,
        picks=[MomentPick(start=0.0, end=2.0, reason="A"),
               MomentPick(start=14.0, end=18.0, reason="B")]).model_dump_json())
    led = ingest_moments(led, cfg, "src_1")
    # hang a clip+post off moment A so we can prove cascade-delete
    a = next(m for m in led.moments_of("src_1") if m.content_token == "0.00-2.00")
    led.add_clip(Clip(id="c_a", parent_id=a.id, path="/c"))
    led.add_post(Post(id="p_a", parent_id="c_a", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x"))
    # now a fresh request + a NEW decision dropping A, keeping B (updated), adding C
    led = request_moments(led, cfg, "src_1")
    rid2 = latest_request_id(cfg, "moments", "src_1")
    response_path(cfg, "moments", "src_1").write_text(MomentDecision(
        source_id="src_1", request_id=rid2,
        picks=[MomentPick(start=14.0, end=18.0, reason="B-better"),
               MomentPick(start=6.0, end=8.0, reason="C")]).model_dump_json())
    led = ingest_moments(led, cfg, "src_1")
    tokens = {m.content_token: m for m in led.moments_of("src_1")}
    assert set(tokens) == {"14.00-18.00", "6.00-8.00"}     # A gone, C added
    assert tokens["14.00-18.00"].reason == "B-better"       # B updated in place (not blocked)
    assert "c_a" not in led.clips and "p_a" not in led.posts # A's lineage cascade-deleted

def test_ingest_all_invalid_marks_source_error(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    rid = latest_request_id(cfg, "moments", "src_1")
    response_path(cfg, "moments", "src_1").write_text(MomentDecision(
        source_id="src_1", request_id=rid,
        picks=[MomentPick(start=50, end=60, reason="out of bounds")]).model_dump_json())
    led = ingest_moments(led, cfg, "src_1")
    assert led.moments_of("src_1") == []
    assert led.sources["src_1"].state is SourceState.error

def test_ingest_moments_noop_without_matching_response(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = ingest_moments(led, cfg, "src_1")     # no response yet
    assert led.moments_of("src_1") == []
    assert led.sources["src_1"].state is SourceState.moments_requested
