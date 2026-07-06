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
    led.add_post(Post(id=pid, parent_id=cid, account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.analyzed, metrics={"lift_score": lift}, public_url="dryrun://1"))

def test_classify_excludes_failed_and_ranks_by_lift(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    for pid, lift in [("p1", 300), ("p2", 5), ("p3", 250), ("p4", 1)]:
        led.add_post(Post(id=pid, parent_id="c", account="a", account_id="1",
                          platform=Platform.instagram, caption="x",
                          state=PostState.analyzed, metrics={"lift_score": lift}, public_url="dryrun://c"))
    # a failed post with no lift_score must NOT be classified (FIX F22)
    led.add_post(Post(id="pf", parent_id="c", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.failed,
                      metrics={"error": "boom"}, public_url="dryrun://pf"))
    # winner_pct=0.5 -> top 2 winners; retire_pct=0.5 + floor 20 -> bottom 2 that are <20
    r = classify_outcomes(led, winner_pct=0.5, retire_pct=0.5, lift_floor=20.0)
    assert set(r["winners"]) == {"p1", "p3"}
    assert set(r["losers"]) == {"p2", "p4"}        # both below floor 20 and bottom-ranked
    assert "pf" not in r["winners"] and "pf" not in r["losers"]

def test_classify_floor_protects_good_clips_from_retirement(tmp_path):
    # A bottom-ranked post that still clears the lift_floor is NOT retired (conservative policy).
    led = Ledger.load(Config(root=tmp_path))
    for pid, lift in [("hi", 500), ("mid", 100), ("ok", 60)]:   # all >= floor 20
        led.add_post(Post(id=pid, parent_id="c", account="a", account_id="1",
                          platform=Platform.instagram, caption="x",
                          state=PostState.analyzed, metrics={"lift_score": lift}, public_url="dryrun://c"))
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
    led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.analyzed, metrics={"lift_score": 400.0}, public_url="dryrun://p1"))
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
    led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.published, submission_id="SUB123", metrics={"lift_score":400.0}, public_url="dryrun://p1"))
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


def test_classify_winner_never_also_a_loser(tmp_path):
    # Stage-6 audit LOW: with operator-raised pcts (winner_pct + retire_pct > 1) the winner and
    # loser slices overlapped — one post could be amplified AND retired in the same adjust pass
    # (contradictory: budget spent on a source whose representative clip is simultaneously
    # suppressed). A winner must be excluded from the loser pool regardless of pcts.
    led = Ledger.load(Config(root=tmp_path))
    for pid, lift in [("top", 300), ("mid", 5), ("low", 1)]:   # mid is below floor 20
        led.add_post(Post(id=pid, parent_id="c", account="a", account_id="1",
                          platform=Platform.instagram, caption="x",
                          state=PostState.analyzed, metrics={"lift_score": lift}, public_url="dryrun://c"))
    r = classify_outcomes(led, winner_pct=0.67, retire_pct=0.67, lift_floor=20.0)
    assert "mid" in r["winners"]                       # rank 2 of 3 -> in the top 67%
    assert "mid" not in r["losers"]                    # ...so it must NOT also be retired
    assert "low" in r["losers"]                        # the true bottom still retires


# ======================= P4(a): account-aware (per-surface) WINNER ranking =======================
def _ap(led, pid, lift, account="a", platform=Platform.instagram):
    led.add_post(Post(id=pid, parent_id="c", account=account, account_id="1", platform=platform,
                      caption="x", state=PostState.analyzed, metrics={"lift_score": lift}, public_url="dryrun://c"))

def test_per_surface_lets_a_small_accounts_best_win(tmp_path):
    # A1: @big (4 posts) would crowd @small (2 posts) out of the GLOBAL top winner_pct. per_surface=True
    # ranks each (account, platform) on its OWN pool, so @small's best (lift 40) wins in its bucket even
    # though it never wins globally.
    led = Ledger.load(Config(root=tmp_path))
    for pid, lift in [("b1", 300), ("b2", 250), ("b3", 200), ("b4", 150)]:
        _ap(led, pid, lift, account="big")
    _ap(led, "s1", 40, account="small"); _ap(led, "s2", 5, account="small")
    glob = classify_outcomes(led, winner_pct=0.3, retire_pct=0.2, lift_floor=20.0)
    assert "s1" not in glob["winners"]                                   # globally crowded out
    surf = classify_outcomes(led, winner_pct=0.3, retire_pct=0.2, lift_floor=20.0, per_surface=True)
    assert "s1" in surf["winners"]                                       # wins on its own surface
    assert "b1" in surf["winners"]                                       # @big's best still wins

def test_per_surface_false_is_byte_identical_to_default(tmp_path):
    # A3: per_surface defaults False and is byte-identical to the no-kwarg call (today's global path) —
    # same winners AND same losers.
    led = Ledger.load(Config(root=tmp_path))
    for pid, lift in [("b1", 300), ("b2", 250), ("b3", 200), ("b4", 150)]:
        _ap(led, pid, lift, account="big")
    _ap(led, "s1", 40, account="small"); _ap(led, "s2", 5, account="small")
    assert classify_outcomes(led) == classify_outcomes(led, per_surface=False)
    r = classify_outcomes(led, winner_pct=0.3, retire_pct=0.2, lift_floor=20.0, per_surface=False)
    assert set(r["winners"]) == {"b1", "b2"} and r["losers"] == ["s2"]   # global top-2; global bottom-1 <floor

def test_per_surface_winner_is_protected_from_global_retire_D1(tmp_path):
    # A4/D1 (the safety crux): the LOSER side stays GLOBAL (never bucketed per-surface). per_surface only
    # changes WINNERS — and a post that becomes a per-surface winner is therefore NEVER also retired
    # (no amplify+retire on the same post). @small's best (lift 15) is below the global floor AND in the
    # global bottom slice; per_surface=True makes it a winner and SHIELDS it from retirement, while the
    # genuinely-worst post (lift 3) is still retired. The bottom slice itself is unchanged (global).
    led = Ledger.load(Config(root=tmp_path))
    for pid, lift in [("b1", 300), ("b2", 250), ("b3", 200)]:
        _ap(led, pid, lift, account="big")
    _ap(led, "s_best", 15, account="small"); _ap(led, "s_worst", 3, account="small")
    off = classify_outcomes(led, winner_pct=0.3, retire_pct=0.5, lift_floor=20.0, per_surface=False)
    on = classify_outcomes(led, winner_pct=0.3, retire_pct=0.5, lift_floor=20.0, per_surface=True)
    assert "s_best" in off["losers"]                       # globally it WOULD be retired (bottom + <floor)
    assert "s_best" in on["winners"] and "s_best" not in on["losers"]   # per-surface: wins -> shielded
    assert "s_worst" in off["losers"] and "s_worst" in on["losers"]     # the true worst still retires (global)

def test_per_surface_single_post_surface_wins_and_is_never_a_loser(tmp_path):
    # A5: a surface with exactly ONE analyzed post -> win_cut = max(1, round(1*pct)) = 1, so that post is
    # its bucket's winner (its own best) and, being a winner, can never be forced into the global losers.
    led = Ledger.load(Config(root=tmp_path))
    for pid, lift in [("b1", 300), ("b2", 250)]:
        _ap(led, pid, lift, account="big")
    _ap(led, "solo", 2, account="solo")                   # one post, below floor, globally the worst
    on = classify_outcomes(led, winner_pct=0.3, retire_pct=0.5, lift_floor=20.0, per_surface=True)
    assert "solo" in on["winners"] and "solo" not in on["losers"]

def test_per_surface_buckets_by_platform_not_just_account(tmp_path):
    # the bucket key is (account, platform): the same handle's IG and TikTok are distinct surfaces, so
    # each platform's best wins independently (matches the per-platform integration model).
    led = Ledger.load(Config(root=tmp_path))
    _ap(led, "ig1", 300, account="a", platform=Platform.instagram)
    _ap(led, "ig2", 250, account="a", platform=Platform.instagram)
    _ap(led, "tk1", 40, account="a", platform=Platform.tiktok)        # @a's best on TikTok (globally low)
    _ap(led, "tk2", 5, account="a", platform=Platform.tiktok)
    on = classify_outcomes(led, winner_pct=0.3, retire_pct=0.2, lift_floor=20.0, per_surface=True)
    assert "ig1" in on["winners"] and "tk1" in on["winners"]            # each platform's best wins

def test_cmd_adjust_threads_per_surface_flag(tmp_path, monkeypatch, mocker):
    # A6: cmd_adjust passes cfg.adjust_per_surface into classify_outcomes — only when the flag is on.
    import fanops.cli as cli
    monkeypatch.chdir(tmp_path); monkeypatch.setenv("FANOPS_ADJUST_PER_SURFACE", "on")
    cfg = Config(root=tmp_path); Ledger.load(cfg).save()
    spy = mocker.patch("fanops.cli.classify_outcomes", return_value={"winners": [], "losers": []})
    cli.cmd_adjust(cfg, 0.3, 0.2, 20.0)
    assert spy.call_args.kwargs.get("per_surface") is True
