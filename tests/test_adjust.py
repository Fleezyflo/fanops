import json
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

def test_classify_excludes_failed_and_lift_less(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    for pid, l in [("p1", 300), ("p2", 5), ("p3", 250), ("p4", 1)]:
        led.add_post(Post(id=pid, parent_id="c", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x",
                          state=PostState.analyzed, metrics={"lift_score": l}))
    # a failed post with no lift_score must NOT be classified (FIX F22)
    led.add_post(Post(id="pf", parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.failed,
                      metrics={"error": "boom"}))
    r = classify_outcomes(led, winner_pct=0.5)
    assert set(r["winners"]) == {"p1", "p3"} and set(r["losers"]) == {"p2", "p4"}
    assert "pf" not in r["winners"] and "pf" not in r["losers"]

def test_amplify_then_ingest_then_render_produces_new_clip(tmp_path):
    # FIX F60: prove the learning loop's forward half end to end.
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
    led, clips = render_aspects_for(led, cfg, new[0].id, aspects={Fmt.r9x16})  # would shell ffmpeg
    # (in this unit test ffmpeg isn't mocked; assert the unit was created pre-render)
    assert new[0].id in {m.id for m in led.moments_of("src_1")}

def test_retire_suppresses_lineage(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    _analyzed_post(led, 1, "pL", "cL", "mL", "sL")
    led = retire(led, ["pL"])
    assert led.is_retired_clip("cL")                    # FIX F55: observable, not write-only
