import json
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Post, Clip, Moment, Source, PostState, ClipState, MomentState,
                           SourceState, Platform, MomentDecision, MomentPick)
from fanops.agentstep import request_path, response_path, latest_request_id
from fanops.adjust import classify_outcomes, amplify, retire
from fanops.moments import ingest_moments
from fanops.clip import render_aspects_for
from fanops.models import Fmt

def _analyzed_post(led, lift, pid, cid, mid, sid):
    if sid not in led.sources:
        led.add_source(Source(id=sid, source_path="/s.mp4", state=SourceState.moments_decided,
                              duration=30.0, transcript=[{"start":14,"end":18,"text":"they slept on me"}],
                              signal_peaks=[], meta={"transcribed": True}))
    led.add_moment(Moment(id=mid, parent_id=sid, content_token="14-21", start=14, end=21,
                          reason="punchline + beat drop", transcript_excerpt="they slept on me",
                          state=MomentState.clipped))
    led.add_clip(Clip(id=cid, parent_id=mid, path="/c.mp4", state=ClipState.analyzed))
    led.add_post(Post(id=pid, parent_id=cid, account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.analyzed, metrics={"lift_score": lift}))

def test_classify_excludes_failed_and_ranks_by_lift(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    for pid, lift in [("p1", 300), ("p2", 5), ("p3", 250), ("p4", 1)]:
        led.add_post(Post(id=pid, parent_id="c", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x",
                          state=PostState.analyzed, metrics={"lift_score": lift}))
    # a failed post with no lift_score must NOT be classified (FIX F22)
    led.add_post(Post(id="pf", parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.failed,
                      metrics={"error": "boom"}))
    # winner_pct=0.5 -> top 2 winners; retire_pct=0.5 + floor 20 -> bottom 2 that are <20
    r = classify_outcomes(led, winner_pct=0.5, retire_pct=0.5, lift_floor=20.0)
    assert set(r["winners"]) == {"p1", "p3"}
    assert set(r["losers"]) == {"p2", "p4"}        # both below floor 20 and bottom-ranked
    assert "pf" not in r["winners"] and "pf" not in r["losers"]

def test_classify_floor_protects_good_clips_from_retirement(tmp_path):
    # A bottom-ranked post that still clears the lift_floor is NOT retired (conservative policy).
    led = Ledger.load(Config(root=tmp_path))
    for pid, lift in [("hi", 500), ("mid", 100), ("ok", 60)]:   # all >= floor 20
        led.add_post(Post(id=pid, parent_id="c", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x",
                          state=PostState.analyzed, metrics={"lift_score": lift}))
    r = classify_outcomes(led, winner_pct=0.34, retire_pct=0.34, lift_floor=20.0)
    assert "hi" in r["winners"]
    assert r["losers"] == []                        # 'ok' is bottom but lift 60 >= 20 -> spared

def test_classify_empty_population(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    r = classify_outcomes(led)
    assert r == {"winners": [], "losers": []}

@pytest.mark.integration
def test_amplify_then_ingest_then_render_produces_new_clip(tmp_path):
    # FIX F60: prove the learning loop's forward half end to end.
    # CI-2/CI-1: this is an INTEGRATION test — render_aspects_for() below shells out to REAL
    # ffmpeg, which is the repo's literal definition of the `integration` marker. The no-toolchain
    # `unit` CI job (pytest -m "not integration") has no ffmpeg, so without this marker the call
    # raised FileNotFoundError. Marked integration -> runs in the `e2e` job (ffmpeg installed),
    # where it genuinely renders. The amplify->ingest forward half is ALSO covered as a true unit
    # test by test_amplify_preserves_winners_published_lineage (no render call), so the unit suite
    # keeps that coverage.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _analyzed_post(led, 400, "p1", "clip_1", "mom_1", "src_1")
    led = amplify(led, cfg, ["p1"])
    payload = json.loads(request_path(cfg, "moments", "src_1").read_text())
    assert "they slept on me" in payload["guidance"]
    assert led.sources["src_1"].state is SourceState.moments_requested
    # agent answers the amplify request with a NEW moment
    rid = latest_request_id(cfg, "moments", "src_1")
    response_path(cfg, "moments", "src_1").write_text(MomentDecision(
        source_id="src_1", request_id=rid,
        picks=[MomentPick(start=20.0, end=26.0, reason="second wave like the first")]).model_dump_json())
    led = ingest_moments(led, cfg, "src_1")
    new = [m for m in led.moments_of("src_1") if m.content_token == "20.00-26.00"]
    assert len(new) == 1
    led, clips = render_aspects_for(led, cfg, new[0].id, aspects={Fmt.r9x16})  # REAL ffmpeg (integration)
    # The amplified moment is wired up for rendering and survives the render pass.
    assert new[0].id in {m.id for m in led.moments_of("src_1")}

def test_retire_suppresses_lineage_including_moment(tmp_path):
    from fanops.clip import render_aspects_for
    from fanops.models import Fmt
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _analyzed_post(led, 1, "pL", "cL", "mL", "sL")
    led = retire(led, ["pL"])
    assert led.is_retired_clip("cL")                 # leaf suppressed (FIX F55)
    assert led.is_retired_moment("mL")               # lineage suppressed (the real fix)
    led, clips = render_aspects_for(led, cfg, "mL", aspects={Fmt.r16x9})
    assert clips == []                                # guard fires -> no resurrected clip

def test_amplify_respects_per_source_budget(tmp_path):
    # E1 (amplify_cap): a source that has already been amplified up to max_amplify_per_source
    # must NOT be re-requested. src.meta['amplify_count'] tracks the per-source count; at the cap
    # amplify() skips the source entirely — no write_request, no state flip — so the source stays
    # in moments_decided (it was a winner, already decided), NOT moments_requested. This bounds an
    # autonomous LLM from growing one source's clips without limit.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.moments_decided, duration=30.0,
                          transcript=[{"start":14,"end":18,"text":"they slept on me"}], signal_peaks=[],
                          meta={"amplify_count": 3}))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="14.00-18.00", start=14, end=18,
                          reason="punchline", transcript_excerpt="they slept on me", state=MomentState.clipped))
    led.add_clip(Clip(id="c1", parent_id="m1", path="/c.mp4", state=ClipState.analyzed))
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.analyzed, metrics={"lift_score": 400.0}))
    led = amplify(led, cfg, ["p1"], max_amplify_per_source=3)
    # at the cap, the source is neither re-requested nor state-flipped
    assert led.sources["s1"].state is SourceState.moments_decided
    # the cap is not silently bumped past the ceiling
    assert led.sources["s1"].meta.get("amplify_count") == 3

def test_amplify_preserves_winners_published_lineage(tmp_path):
    # CRITICAL: amplifying a winner must NOT delete the winner's own published/analyzed post.
    # The post is live on the platform; deleting its ledger record orphans it (untrackable).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.moments_decided, duration=30.0,
                          transcript=[{"start":14,"end":18,"text":"they slept on me"}], signal_peaks=[]))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="14.00-18.00", start=14, end=18,
                          reason="punchline", transcript_excerpt="they slept on me", state=MomentState.clipped))
    led.add_clip(Clip(id="c1", parent_id="m1", path="/c.mp4", state=ClipState.analyzed))
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.published, submission_id="SUB123", metrics={"lift_score":400.0}))
    led = amplify(led, cfg, ["p1"])
    rid = latest_request_id(cfg, "moments", "s1")
    response_path(cfg, "moments", "s1").write_text(MomentDecision(
        source_id="s1", request_id=rid,
        picks=[MomentPick(start=20.0, end=26.0, reason="second wave")]).model_dump_json())
    led = ingest_moments(led, cfg, "s1")
    # the winning published post + its clip MUST survive (still trackable on-platform)
    assert "p1" in led.posts and led.posts["p1"].state is PostState.published
    assert "c1" in led.clips
    # its moment is RETIRED (suppressed from future work) but not erased
    assert led.moments["m1"].state is MomentState.retired
    # the NEW amplify moment was still created
    assert any(m.content_token == "20.00-26.00" for m in led.moments_of("s1"))


def test_amplify_default_guidance_unchanged_without_extra(tmp_path):
    # extra_guidance defaults to "" -> the written moment-request guidance must NOT contain any
    # injected hook block; behavior byte-identical to today (the existing callers pass nothing).
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _analyzed_post(led, 90.0, "p1", "c1", "m1", "s1")
    amplify(led, cfg, ["p1"])
    payload = json.loads(request_path(cfg, "moments", "s1").read_text())
    assert "lean toward" not in payload["guidance"].lower()
    assert payload["guidance"].startswith("AMPLIFY:")


def test_amplify_injects_extra_guidance(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _analyzed_post(led, 90.0, "p1", "c1", "m1", "s1")
    amplify(led, cfg, ["p1"], extra_guidance="WINNING_HOOK_TEXT")
    payload = json.loads(request_path(cfg, "moments", "s1").read_text())
    assert "WINNING_HOOK_TEXT" in payload["guidance"]
    assert payload["guidance"].startswith("AMPLIFY:")     # base guidance still leads
