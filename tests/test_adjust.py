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

# Retirement now needs a real POOL (adjust._MIN_SCORED_N / _MIN_DISTINCT_SCORES), so every fixture
# below that asserts on losers carries at least that many distinctly-scored posts. The n=3/n=4 pools
# these tests used to run on retired a post off a single data point — the defect, not the contract.
def _pool(led, pairs, degraded=()):
    for pid, lift in pairs:
        metrics = {"lift_score": lift}
        if pid in degraded:
            metrics["lift_degraded"] = True
        led.add_post(Post(id=pid, parent_id="c", account="a", account_id="1",
                          platform=Platform.instagram, caption="x",
                          state=PostState.analyzed, metrics=metrics, public_url="dryrun://c"))
    return led

# The live shape this ticket was measured against: the whole distribution sits at or under 3.01, so
# the shipped absolute floor of 20.0 sat >6x above every post that has ever existed on this system.
_LIVE_SHAPE = [("t1", 3.0), ("t2", 2.5), ("t3", 2.0), ("t4", 1.8), ("t5", 1.5),
               ("t6", 1.2), ("t7", 1.0), ("t8", 0.9), ("mid", 0.5), ("lo", 0.0)]

def test_classify_excludes_failed_and_ranks_by_lift(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    _pool(led, [("p1", 300), ("p2", 5), ("p3", 250), ("p4", 1), ("p5", 200),
                ("p6", 150), ("p7", 100), ("p8", 60)])
    # a failed post with no lift_score must NOT be classified (FIX F22)
    led.add_post(Post(id="pf", parent_id="c", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.failed,
                      metrics={"error": "boom"}, public_url="dryrun://pf"))
    # winner_pct=0.5 -> top 4 winners; retire_pct=0.5 -> bottom 4, of which the two under the floor go
    r = classify_outcomes(led, winner_pct=0.5, retire_pct=0.5, lift_floor=20.0)
    assert set(r["winners"]) == {"p1", "p3", "p5", "p6"}
    assert set(r["losers"]) == {"p2", "p4"}        # bottom-ranked AND under the floor
    assert "pf" not in r["winners"] and "pf" not in r["losers"]

def test_classify_floor_protects_good_clips_from_retirement(tmp_path):
    # A bottom-ranked post that still clears the floor is NOT retired (conservative policy).
    led = Ledger.load(Config(root=tmp_path))
    _pool(led, [("hi", 500), ("w2", 400), ("w3", 300), ("w4", 200),
                ("w5", 150), ("ok3", 120), ("ok2", 100), ("ok", 60)])   # all >= floor 20
    r = classify_outcomes(led, winner_pct=0.34, retire_pct=0.34, lift_floor=20.0)
    assert "hi" in r["winners"]
    assert r["losers"] == []                        # the bottom three all clear 20 -> spared

def test_classify_empty_population(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    r = classify_outcomes(led)
    assert r == {"winners": [], "losers": []}

def test_classify_spares_lift_degraded_bottom_post(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    _pool(led, _LIVE_SHAPE, degraded={"lo"})
    r = classify_outcomes(led)
    assert r["losers"] == []                        # degraded bottom post spared from autonomous retire()

def test_classify_retires_non_degraded_bottom_post(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    _pool(led, _LIVE_SHAPE)
    r = classify_outcomes(led)
    assert r["losers"] == ["lo"]                    # same fixture without lift_degraded -> bottom retires

# ============ MOL-795: retirement is a SIGNAL, not a ratchet (D1 floor, D2 pool, D3 trigger) ============
def test_shipped_lift_floor_no_longer_retires_the_whole_bottom_slice(tmp_path):
    # D1. lift_floor=20.0 is the value _learn_pass ran with on every 600s tick. On the live shape it
    # sits above EVERY post, so the `< lift_floor` conjunct excluded nobody and the bottom slice
    # retired whole. The derived floor (a fraction of the pool's median) now binds instead: 'mid'
    # (0.5) survives, 'lo' (0.0) does not — the same call, the same 20.0, a strictly smaller cut.
    led = Ledger.load(Config(root=tmp_path))
    _pool(led, _LIVE_SHAPE)
    r = classify_outcomes(led, winner_pct=0.3, retire_pct=0.2, lift_floor=20.0)
    assert r["losers"] == ["lo"]                    # the bottom slice is {mid, lo}; only 'lo' is a failure
    assert "mid" not in r["losers"]

def test_lift_floor_can_still_tighten_but_never_loosen(tmp_path):
    # D1 corollary. lift_floor survives as the operator's ADDITIONAL ceiling via min(), so it can only
    # make a pass more conservative. Below the derived floor it binds; above it, it is inert by
    # construction — an operator can no longer raise it to force the whole bottom slice out.
    led = Ledger.load(Config(root=tmp_path))
    _pool(led, _LIVE_SHAPE)
    assert classify_outcomes(led, lift_floor=0.0)["losers"] == []          # tighter -> nothing retires
    assert classify_outcomes(led, lift_floor=1e9) == classify_outcomes(led, lift_floor=20.0)   # looser -> no effect

def test_retirement_needs_a_pool_not_a_data_point(tmp_path):
    # D2, with its negative control: the SAME worst post is spared at n=7 and retired at n=8. Nothing
    # about that post changes — only whether the pool clears _MIN_SCORED_N.
    thin = [("w1", 3.0), ("w2", 2.0), ("w3", 1.5), ("w4", 1.2), ("w5", 0.9), ("w6", 0.5), ("w8", 0.0)]
    led = _pool(Ledger.load(Config(root=tmp_path / "thin")), thin)
    assert classify_outcomes(led)["losers"] == []           # n=7 -> below the signal floor, retire nothing
    assert classify_outcomes(led)["winners"]                # ...while WINNERS are unaffected (amplify only mints)
    grown = _pool(Ledger.load(Config(root=tmp_path / "grown")), thin + [("w7", 0.1)])
    assert set(classify_outcomes(grown)["losers"]) == {"w7", "w8"}         # n=8 -> the floor is allowed to bind

def test_a_pool_with_one_distinct_score_has_no_ranking_and_retires_nothing(tmp_path):
    # D2. Ten posts that all scored identically carry a full bottom slice and zero information: which
    # posts land in it is insertion order. _MIN_DISTINCT_SCORES refuses to call that a ranking.
    led = Ledger.load(Config(root=tmp_path))
    _pool(led, [(f"p{i}", 1.0) for i in range(10)])
    assert classify_outcomes(led)["losers"] == []

def test_a_tight_pool_retires_nobody_while_a_wide_one_retires(tmp_path):
    # D3, with its negative control. Same n, same pcts, same lift_floor — only the SHAPE differs.
    # `round(n * retire_pct)` always names a bottom slice, so while rank decided retirement the pass
    # could never retire nothing. Now the floor is the trigger and the slice is only a cap.
    tight = [("a", 1.04), ("b", 1.03), ("c", 1.02), ("d", 1.01), ("e", 0.95),
             ("f", 0.94), ("g", 0.93), ("h", 0.92), ("i", 0.81), ("j", 0.80)]
    led = _pool(Ledger.load(Config(root=tmp_path / "tight")), tight)
    assert classify_outcomes(led)["losers"] == []           # worst posts sit near the middle -> no failures
    wide = _pool(Ledger.load(Config(root=tmp_path / "wide")), _LIVE_SHAPE)
    assert classify_outcomes(wide)["losers"] == ["lo"]      # a genuine collapse still retires

@pytest.mark.integration
def test_amplify_then_ingest_then_render_produces_new_clip(tmp_path, monkeypatch):
    # T2.3 sanctioned update: the subject here is amplify's FORWARD half (request -> ingest -> render),
    # not admission. Under FANOPS_QUEUE_GATE (default ON) a machine-origin re-open is now PARKED for an
    # operator release, so the gate is turned off explicitly to reach the path under test. The park
    # itself is asserted by tests/test_machine_reopen_admission.py.
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "0")
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

def test_retire_suppresses_the_lineage_without_relabelling_posts(tmp_path):
    # T3.7 sanctioned update. retire used to COPY the suppression down onto every never-shipped descendant,
    # duplicating what Ledger.is_suppressed now derives from the retired clip on every read. Retiring the
    # clip/moment is the ONLY write; the stored labels underneath it are left exactly as they were, so a
    # post's own state stays a record of what it did, not a cache of its ancestor's fate. The stranding this
    # replaced is still fixed — is_suppressed answers `suppressed` for the whole lineage either way.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _analyzed_post(led, 1, "pL", "cL", "mL", "sL")
    for pid, st in (("pAw", PostState.awaiting_approval), ("pQ", PostState.queued),
                    ("pNR", PostState.needs_reconcile)):
        led.add_post(Post(id=pid, parent_id="cL", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=st, public_url="dryrun://1"))
    led = retire(led, ["pL"])
    assert led.posts["pAw"].state is PostState.awaiting_approval  # stored label UNTOUCHED — no write-time copy
    assert led.posts["pQ"].state is PostState.queued              # ditto: derivation, not relabelling
    assert led.posts["pNR"].state is PostState.needs_reconcile    # MAY be live on the platform -> preserved
    assert led.posts["pL"].state is PostState.analyzed            # the performance record is untouched
    for pid in ("pAw", "pQ", "pNR"):
        assert led.is_suppressed(led.posts[pid])                  # the whole lineage reads as dead anyway
    retire(led, ["pL"])                                           # idempotent: a second pass changes nothing
    assert led.posts["pNR"].state is PostState.needs_reconcile and led.posts["pL"].state is PostState.analyzed

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

def test_amplify_preserves_winners_published_lineage(tmp_path, monkeypatch):
    # CRITICAL: amplifying a winner must NOT delete the winner's own published/analyzed post.
    # The post is live on the platform; deleting its ledger record orphans it (untrackable).
    # T2.3 sanctioned update: gate OFF, so the machine re-open is SERVED rather than parked — the
    # lineage rule under test only fires once the request round-trips into ingest_moments.
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "0")
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


def test_amplify_default_guidance_unchanged_without_extra(tmp_path, monkeypatch):
    # extra_guidance defaults to "" -> the written moment-request guidance must NOT contain any
    # injected hook block; behavior byte-identical to today (the existing callers pass nothing).
    # T2.3 sanctioned update: gate OFF so the request is written at all (the subject is its CONTENT).
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "0")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _analyzed_post(led, 90.0, "p1", "c1", "m1", "s1")
    amplify(led, cfg, ["p1"])
    payload = json.loads(request_path(cfg, "moments", "s1").read_text())
    assert "lean toward" not in payload["guidance"].lower()
    assert payload["guidance"].startswith("AMPLIFY:")


def test_amplify_injects_extra_guidance(tmp_path, monkeypatch):
    # T2.3 sanctioned update: gate OFF so the request is written at all (the subject is its CONTENT).
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "0")
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
    _pool(led, [("top", 100), ("w2", 90), ("w3", 80), ("w4", 70),
                ("w5", 60), ("w6", 50), ("edge", 5), ("low", 0.0)])
    # derived floor = 0.25 * median(65) = 16.25, so 'edge' (5) is both a winner and under the floor
    r = classify_outcomes(led, winner_pct=0.9, retire_pct=0.67, lift_floor=20.0)
    assert "edge" in r["winners"]                      # rank 7 of 8 -> inside the top 90%
    assert "edge" not in r["losers"]                   # ...so it must NOT also be retired
    assert r["losers"] == ["low"]                      # the true bottom still retires


# ======================= P4(a): account-aware (per-surface) WINNER ranking =======================
def _ap(led, pid, lift, account="a", platform=Platform.instagram):
    led.add_post(Post(id=pid, parent_id="c", account=account, account_id="1", platform=platform,
                      caption="x", state=PostState.analyzed, metrics={"lift_score": lift}, public_url="dryrun://c"))

def test_per_surface_lets_a_small_accounts_best_win(tmp_path):
    # A1: @big (6 posts) would crowd @small (2 posts) out of the GLOBAL top winner_pct. per_surface=True
    # ranks each (account, platform) on its OWN pool, so @small's best (lift 40) wins in its bucket even
    # though it never wins globally.
    led = Ledger.load(Config(root=tmp_path))
    for pid, lift in [("b1", 300), ("b2", 250), ("b3", 200), ("b4", 150), ("b5", 120), ("b6", 100)]:
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
    for pid, lift in [("b1", 300), ("b2", 250), ("b3", 200), ("b4", 150), ("b5", 120), ("b6", 100)]:
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
    for pid, lift in [("b1", 300), ("b2", 250), ("b3", 200), ("b4", 150), ("b5", 120), ("b6", 100)]:
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
    for pid, lift in [("b1", 300), ("b2", 250), ("b3", 200), ("b4", 150), ("b5", 120), ("b6", 100), ("b7", 80)]:
        _ap(led, pid, lift, account="big")
    _ap(led, "solo", 2, account="solo")                   # one post, below floor, globally the worst
    off = classify_outcomes(led, winner_pct=0.3, retire_pct=0.5, lift_floor=20.0, per_surface=False)
    on = classify_outcomes(led, winner_pct=0.3, retire_pct=0.5, lift_floor=20.0, per_surface=True)
    assert "solo" in off["losers"]                        # globally it WOULD be retired
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
