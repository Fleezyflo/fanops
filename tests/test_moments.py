import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Clip, Post, SourceState, Platform, MomentDecision, MomentPick
from fanops.agentstep import response_path, request_path, latest_request_id
from fanops.moments import request_moments, ingest_moments, validate_pick, _drop_overlaps

def _mp(s, e, reason="r"):
    return MomentPick(start=s, end=e, reason=reason)

def _ingest_picks(led, cfg, source_id, picks):
    rid = latest_request_id(cfg, "moments", source_id)
    response_path(cfg, "moments", source_id).write_text(
        MomentDecision(source_id=source_id, request_id=rid, picks=picks).model_dump_json())
    return ingest_moments(led, cfg, source_id)

def test_drop_overlaps_keeps_first_drops_near_dupe():
    kept = _drop_overlaps([_mp(0, 18), _mp(5, 20), _mp(40, 58)])
    assert [(p.start, p.end) for p in kept] == [(0, 18), (40, 58)]

def test_drop_overlaps_all_overlap_keeps_first():
    kept = _drop_overlaps([_mp(0, 18), _mp(3, 20), _mp(5, 19)])
    assert len(kept) == 1 and (kept[0].start, kept[0].end) == (0, 18)

def test_drop_overlaps_disjoint_all_kept():
    assert len(_drop_overlaps([_mp(0, 15), _mp(20, 35), _mp(40, 55)])) == 3

def test_ingest_short_source_yields_clip_not_error(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=10.0)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [_mp(0.0, 10.0, "whole short source")])
    assert len(led.moments_of("src_1")) == 1
    assert led.sources["src_1"].state is SourceState.moments_decided   # NOT error

def test_ingest_overlapping_picks_deduped(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [_mp(0, 18), _mp(5, 20), _mp(40, 58)])
    assert len(led.moments_of("src_1")) == 2          # the near-dupe middle pick dropped

def test_ingest_empty_picks_visible_not_silent_cascade(tmp_path):
    # The model returns [] -> source ends moments_decided (re-runnable), and a PRIOR moment is
    # PRESERVED (no cascade-delete on an empty re-pick — the silent-drop fix).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [_mp(10, 28, "first")])
    assert len(led.moments_of("src_1")) == 1
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1", [])
    assert led.sources["src_1"].state is SourceState.moments_decided   # not error
    assert len(led.moments_of("src_1")) == 1                            # prior moment preserved

def test_ingest_sanitizes_em_dash_in_reason_and_hook(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg, dur=60.0)
    led = request_moments(led, cfg, "src_1")
    led = _ingest_picks(led, cfg, "src_1",
                        [MomentPick(start=10, end=28, reason="punchline — then the beat drops",
                                    transcript_excerpt="they slept on me — wait")])
    m = led.moments_of("src_1")[0]
    assert "—" not in m.reason and "—" not in (m.hook or "")

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

def test_moment_gets_derived_hook(tmp_path):
    # A reconciled Moment carries a deterministic hook derived from its transcript_excerpt
    # (the spoken text's first clause) — a punchy top-third line even with NO LLM. A future
    # LLM can overwrite Moment.hook directly.
    from fanops.overlay import derive_hook
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    rid = latest_request_id(cfg, "moments", "src_1")
    excerpt = "This changed everything for me."
    response_path(cfg, "moments", "src_1").write_text(MomentDecision(
        source_id="src_1", request_id=rid,
        picks=[MomentPick(start=14.0, end=18.5, reason="punchline",
                          transcript_excerpt=excerpt, signal_score=0.6)]
    ).model_dump_json())
    led = ingest_moments(led, cfg, "src_1")
    moms = led.moments_of("src_1")
    assert len(moms) == 1
    assert moms[0].hook                                      # non-empty
    assert moms[0].hook == derive_hook(excerpt)             # the derived first clause
    assert moms[0].hook == "This changed everything for me"

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
    # Stage-6 audit: the reason must say WHY the picks were invalid (here: past EOF), so the
    # operator can tell a garbage-timestamp model from a bad duration probe — not just a count.
    assert "end>" in (led.sources["src_1"].error_reason or "")

def test_ingest_moments_noop_without_matching_response(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    led = ingest_moments(led, cfg, "src_1")     # no response yet
    assert led.moments_of("src_1") == []
    assert led.sources["src_1"].state is SourceState.moments_requested

def test_ingest_partial_rejection_keeps_valid_drops_invalid(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _src(led, cfg)
    led = request_moments(led, cfg, "src_1")
    rid = latest_request_id(cfg, "moments", "src_1")
    response_path(cfg, "moments", "src_1").write_text(MomentDecision(
        source_id="src_1", request_id=rid,
        picks=[MomentPick(start=14.0, end=18.0, reason="valid keep"),
               MomentPick(start=5.0, end=3.0, reason="end<start invalid"),
               MomentPick(start=6.0, end=8.0, reason="valid keep 2")]).model_dump_json())
    led = ingest_moments(led, cfg, "src_1")
    tokens = {m.content_token for m in led.moments_of("src_1")}
    assert tokens == {"14.00-18.00", "6.00-8.00"}              # invalid dropped, two valid kept
    assert led.sources["src_1"].state is SourceState.moments_decided

def test_validate_pick_min_length_and_eof_tolerance():
    # too-short rule: (end-start) < 0.5 is invalid; exactly 0.5 is OK
    assert validate_pick(MomentPick(start=10.0, end=10.3, reason="r"), duration=20.0) is not None  # 0.30s too short
    assert validate_pick(MomentPick(start=10.0, end=10.5, reason="r"), duration=20.0) is None       # 0.50s ok
    # EOF +0.5 tolerance: end just past duration but within tolerance is OK; beyond is invalid
    assert validate_pick(MomentPick(start=10.0, end=20.5, reason="r"), duration=20.0) is None        # exactly dur+0.5 ok
    assert validate_pick(MomentPick(start=10.0, end=20.6, reason="r"), duration=20.0) is not None     # dur+0.6 invalid
    # duration==0 disables the EOF ceiling (unprobed source): a large end is NOT rejected on EOF grounds
    assert validate_pick(MomentPick(start=10.0, end=999.0, reason="r"), duration=0.0) is None

def test_validate_pick_rejects_nan_defense_in_depth():
    # Even if a NaN reaches validate_pick by some other path, it is rejected (not None/valid).
    import math
    # Build via model_construct to bypass the field validator, proving validate_pick guards too.
    p = MomentPick.model_construct(start=math.nan, end=math.nan, reason="r")
    assert validate_pick(p, duration=120.0) is not None
