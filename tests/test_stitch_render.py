# tests/test_stitch_render.py — M4 (structural-hooks): the impact-cut SUGGEST step. For each moment the
# M2 router reserved `clean_awaiting_strategy:impact_cut` whose bare clip exists, create a suggested
# impact-cut StitchPlan (idempotent, content-addressed, base fingerprint pinned), then re-route the
# moment `hook_strategy -> stitch:impact_cut`. Renders nothing — pure ledger mutation, safe in-lock.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, MomentState, SourceState, ClipState,
                           StitchState, StitchPlan, PostState, Platform, Fmt)
from fanops.router import awaiting, CLEAN_FINAL
from fanops.stitch_render import (mine_suggestions, render_approved_stitches,
                                  prewarm_approved_stitches, _stitch_clip_id)


def _seed(cfg, *, peaks, hook_strategy, clip_state=ClipState.rendered):
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path=str(cfg.sources / "s1.mp4"), state=SourceState.signalled,
                          signal_peaks=peaks, width=1920, height=1080, duration=20.0))
    led.add_moment(Moment(id="m1", parent_id="s1", state=MomentState.clipped, start=0.0, end=18.0,
                          reason="r", hook_strategy=hook_strategy))
    led.clips["clip_base"] = Clip(id="clip_base", parent_id="m1", path=str(cfg.clips / "clip_base.mp4"),
                                  state=clip_state)
    return led

def _write_fp(cfg, clip_id, fp):
    cfg.clips.mkdir(parents=True, exist_ok=True)
    (cfg.clips / f"{clip_id}.render.json").write_text(json.dumps({"fp": fp}))


def test_suggest_creates_plan_and_reroutes(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, peaks=[{"t": 12.0, "score": 0.9}], hook_strategy=awaiting("impact_cut"))
    _write_fp(cfg, "clip_base", "basefp")
    mine_suggestions(led, cfg)
    plans = list(led.stitch_plans.values())
    assert len(plans) == 1
    p = plans[0]
    assert p.state is StitchState.suggested and p.clip_id == "clip_base"
    assert p.strategy_key == "impact_cut" and p.base_fingerprint == "basefp"
    assert p.plan_params == {"cut_start": 0.0, "cut_end": 11.6}
    assert led.moments["m1"].hook_strategy == "stitch:impact_cut"     # format handler acted

def test_suggest_is_idempotent(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, peaks=[{"t": 12.0, "score": 0.9}], hook_strategy=awaiting("impact_cut"))
    _write_fp(cfg, "clip_base", "basefp")
    mine_suggestions(led, cfg)
    led.moments["m1"].hook_strategy = awaiting("impact_cut")          # force a re-scan of the same moment
    mine_suggestions(led, cfg)
    assert len(led.stitch_plans) == 1                                # content-addressed dedup, no duplicate

def test_suggest_does_not_reemit_dismissed(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, peaks=[{"t": 12.0, "score": 0.9}], hook_strategy=awaiting("impact_cut"))
    _write_fp(cfg, "clip_base", "basefp")
    mine_suggestions(led, cfg)
    pid = next(iter(led.stitch_plans))
    led.dismiss_stitch_plan(pid)
    led.moments["m1"].hook_strategy = awaiting("impact_cut")          # re-open the moment
    mine_suggestions(led, cfg)
    assert led.stitch_plans[pid].state is StitchState.dismissed       # terminal — never resurrected

def test_suggest_skips_non_routed_moment(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, peaks=[{"t": 12.0, "score": 0.9}], hook_strategy=CLEAN_FINAL)
    _write_fp(cfg, "clip_base", "basefp")
    mine_suggestions(led, cfg)
    assert led.stitch_plans == {}

def test_suggest_no_plan_when_cut_degenerate(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, peaks=[{"t": 2.0, "score": 0.9}], hook_strategy=awaiting("impact_cut"))  # cut too short
    _write_fp(cfg, "clip_base", "basefp")
    mine_suggestions(led, cfg)
    assert led.stitch_plans == {}
    assert led.moments["m1"].hook_strategy == awaiting("impact_cut")  # NOT re-routed (nothing produced)

def test_suggest_ignores_stitch_draft_base(tmp_path):
    # never stitch a stitch: a stitch_draft clip is not a valid base for an impact-cut
    cfg = Config(root=tmp_path)
    led = _seed(cfg, peaks=[{"t": 12.0, "score": 0.9}], hook_strategy=awaiting("impact_cut"),
                clip_state=ClipState.stitch_draft)
    _write_fp(cfg, "clip_base", "basefp")
    mine_suggestions(led, cfg)
    assert led.stitch_plans == {}


# ---- MOL-80: an unreadable base fingerprint at mine time must SKIP minting this pass (retry next pass),
# never mint a plan with a None pin that permanently disables the _precheck drift guard. The content-
# addressed id excludes the fingerprint, so a None baked in once could never heal via re-mining. ----
def test_suggest_skips_when_fingerprint_absent(tmp_path):
    # no {clip}.render.json sidecar yet (the ordinary race where the base fp write hasn't landed) -> the
    # plan is NOT minted this pass; the moment stays reserved so a later pass re-mines it once the fp reads.
    cfg = Config(root=tmp_path)
    led = _seed(cfg, peaks=[{"t": 12.0, "score": 0.9}], hook_strategy=awaiting("impact_cut"))
    # deliberately do NOT write the fingerprint sidecar
    mine_suggestions(led, cfg)
    assert led.stitch_plans == {}                                    # no plan with a None pin
    assert led.moments["m1"].hook_strategy == awaiting("impact_cut")  # stays reserved -> retries next pass

def test_suggest_mints_once_fingerprint_lands(tmp_path):
    # the skip is one-pass, not permanent: once the sidecar lands, the SAME pairing mints normally with a
    # real, non-None base_fingerprint (so the _precheck drift guard is armed for its lifetime).
    cfg = Config(root=tmp_path)
    led = _seed(cfg, peaks=[{"t": 12.0, "score": 0.9}], hook_strategy=awaiting("impact_cut"))
    mine_suggestions(led, cfg)                                       # pass 1: fp absent -> skipped
    assert led.stitch_plans == {}
    _write_fp(cfg, "clip_base", "basefp")                           # sidecar lands
    mine_suggestions(led, cfg)                                       # pass 2: fp readable -> mints
    plans = list(led.stitch_plans.values())
    assert len(plans) == 1
    assert plans[0].base_fingerprint == "basefp"                    # real pin -> drift guard armed
    assert led.moments["m1"].hook_strategy == "stitch:impact_cut"


# ---- Task 4: render APPROVED plans (lock-free in prod) into stitch_draft clips + supersede ----
def _seed_approved(cfg, *, base_fp="basefp", cur_fp="basefp"):
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path=str(cfg.sources / "s1.mp4"), state=SourceState.signalled,
                          signal_peaks=[{"t": 12.0, "score": 0.9}], width=1920, height=1080, duration=20.0))
    led.add_moment(Moment(id="m1", parent_id="s1", state=MomentState.clipped, start=0.0, end=18.0, reason="r"))
    led.clips["clip_base"] = Clip(id="clip_base", parent_id="m1", path=str(cfg.clips / "clip_base.mp4"),
                                  state=ClipState.rendered, aspect=Fmt.r9x16)
    _write_fp(cfg, "clip_base", cur_fp)
    led.add_stitch_plan(StitchPlan(id="plan1", clip_id="clip_base", strategy_key="impact_cut",
                                   plan_params={"cut_start": 0.0, "cut_end": 11.6},
                                   state=StitchState.approved, base_fingerprint=base_fp))
    return led

def _base_post(state):
    return Post(id="post_base", parent_id="clip_base", account="a", account_id="1",
                platform=Platform.instagram, caption="c", state=state, public_url="dryrun://post_base")

def _ff(mocker, *, dur=11.6):
    def fake_run(cmd, **kw):
        if not str(cmd[-1]).startswith("-"):
            from pathlib import Path
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"STITCH")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    mocker.patch("fanops.clip._probe_duration", return_value=dur)


def test_render_approved_creates_stitch_draft_and_in_use(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = _seed_approved(cfg); _ff(mocker)
    render_approved_stitches(led, cfg)
    stitches = [c for c in led.clips.values() if c.state is ClipState.stitch_draft]
    assert len(stitches) == 1 and stitches[0].id != "clip_base"
    assert led.stitch_plans["plan1"].state is StitchState.in_use

def test_render_approved_stale_fingerprint_auto_dismisses(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = _seed_approved(cfg, base_fp="OLD", cur_fp="NEW"); _ff(mocker)
    render_approved_stitches(led, cfg)
    p = led.stitch_plans["plan1"]
    assert p.state is StitchState.dismissed and "re-rendered" in (p.error_reason or "")  # stale-plan guard
    assert not any(c.state is ClipState.stitch_draft for c in led.clips.values())  # never rendered

def test_render_approved_renders_even_with_live_base_post(tmp_path, mocker):
    # FAN ACCOUNTS repost freely: an already-published base does NOT block its stitch — a stitch is an
    # ADDITIVE post (both go out). The live base post is left untouched.
    cfg = Config(root=tmp_path); led = _seed_approved(cfg); _ff(mocker)
    led.posts["post_base"] = _base_post(PostState.published)            # a LIVE base post
    render_approved_stitches(led, cfg)
    assert led.stitch_plans["plan1"].state is StitchState.in_use
    assert any(c.state is ClipState.stitch_draft for c in led.clips.values())
    assert led.posts["post_base"].state is PostState.published          # untouched (additive, not supersede)

def test_render_approved_does_not_retire_queued_base_post(tmp_path, mocker):
    # FAN ACCOUNTS: the bare post is NOT retired when a stitch renders — the bare clip and the stitch
    # both ship (no double-post prevention).
    cfg = Config(root=tmp_path); led = _seed_approved(cfg); _ff(mocker)
    led.posts["post_base"] = _base_post(PostState.queued)
    render_approved_stitches(led, cfg)
    assert led.posts["post_base"].state is PostState.queued             # still queued -> bare clip still posts
    assert led.stitch_plans["plan1"].state is StitchState.in_use

def test_render_approved_duration_fail_errors_plan(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = _seed_approved(cfg); _ff(mocker, dur=2.0)  # far from expected 11.6
    render_approved_stitches(led, cfg)
    p = led.stitch_plans["plan1"]
    assert p.state is StitchState.error and "duration" in (p.error_reason or "")

def test_render_approved_skips_suggested(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = _seed_approved(cfg); _ff(mocker)
    led.stitch_plans["plan1"].state = StitchState.suggested            # not approved -> not rendered
    render_approved_stitches(led, cfg)
    assert led.stitch_plans["plan1"].state is StitchState.suggested
    assert not any(c.state is ClipState.stitch_draft for c in led.clips.values())

# ---- Task 6: resilience sweep (failure-mode table) ----
def test_render_approved_cut_out_of_range_errors(tmp_path, mocker):
    # a plan whose window is invalid (cut_end <= cut_start) must error BEFORE rendering — never a render
    cfg = Config(root=tmp_path); led = _seed_approved(cfg); _ff(mocker)
    led.stitch_plans["plan1"].plan_params = {"cut_start": 10.0, "cut_end": 4.0}   # inverted -> out of range
    render_approved_stitches(led, cfg)
    p = led.stitch_plans["plan1"]
    assert p.state is StitchState.error and "out of range" in (p.error_reason or "")
    assert not any(c.state is ClipState.stitch_draft for c in led.clips.values())

def test_render_approved_cut_beyond_source_duration_errors(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = _seed_approved(cfg); _ff(mocker)
    led.stitch_plans["plan1"].plan_params = {"cut_start": 0.0, "cut_end": 99.0}   # source is 20s
    render_approved_stitches(led, cfg)
    p = led.stitch_plans["plan1"]
    assert p.state is StitchState.error and "out of range" in (p.error_reason or "")

def test_render_approved_moment_missing_errors_not_raises(tmp_path, mocker):
    # robustness: a base clip orphaned from its moment errors the plan VISIBLY (never a KeyError that
    # aborts the loop and leaves the plan stuck approved with no reason)
    cfg = Config(root=tmp_path); led = _seed_approved(cfg); _ff(mocker)
    del led.moments["m1"]                                  # orphan the base clip from its moment
    render_approved_stitches(led, cfg)                     # must not raise
    p = led.stitch_plans["plan1"]
    assert p.state is StitchState.error and "moment missing" in (p.error_reason or "")


# ---- M5 Task 2: the generic routine pass — rank + per-pass top-N cap + per-candidate fail-open ----
def _seed_n_routed(cfg, scores):
    # N clean moments each routed clean_awaiting:impact_cut, each with its own base clip + a peak whose
    # score is scores[i] (so ranking is observable). Source duration 20s, window [0,18], peak at t=12.
    led = Ledger.load(cfg)
    for i, sc in enumerate(scores):
        sid, mid, cid = f"s{i}", f"m{i}", f"clip{i}"
        led.add_source(Source(id=sid, source_path=str(cfg.sources / f"{sid}.mp4"), state=SourceState.signalled,
                              signal_peaks=[{"t": 12.0, "score": sc}], width=1920, height=1080, duration=20.0))
        led.add_moment(Moment(id=mid, parent_id=sid, state=MomentState.clipped, start=0.0, end=18.0,
                              reason="r", hook_strategy=awaiting("impact_cut")))
        led.clips[cid] = Clip(id=cid, parent_id=mid, path=str(cfg.clips / f"{cid}.mp4"), state=ClipState.rendered)
        _write_fp(cfg, cid, f"fp{i}")
    return led

def test_mine_caps_new_suggestions_per_pass(tmp_path, mocker):
    mocker.patch("fanops.stitch_render.MAX_SUGGESTIONS_PER_PASS", 2)
    cfg = Config(root=tmp_path); led = _seed_n_routed(cfg, [0.9, 0.8, 0.7, 0.6])   # 4 candidates, cap 2
    mine_suggestions(led, cfg)
    assert len(led.stitch_plans) == 2                                # only the cap is emitted this pass
    # the capped-out moments stay reserved (clean_awaiting) so they retry next pass — not lost
    reserved = [m for m in led.moments.values() if (m.hook_strategy or "") == awaiting("impact_cut")]
    assert len(reserved) == 2

def test_mine_emits_highest_ranked_first(tmp_path, mocker):
    mocker.patch("fanops.stitch_render.MAX_SUGGESTIONS_PER_PASS", 2)
    cfg = Config(root=tmp_path); led = _seed_n_routed(cfg, [0.2, 0.95, 0.5, 0.9])   # top two = 0.95, 0.9
    mine_suggestions(led, cfg)
    emitted_scores = sorted((p.rank_score for p in led.stitch_plans.values()), reverse=True)
    assert emitted_scores == [0.95, 0.9]                             # the cap keeps the BEST-fit suggestions

def test_mine_drains_across_passes(tmp_path, mocker):
    mocker.patch("fanops.stitch_render.MAX_SUGGESTIONS_PER_PASS", 2)
    cfg = Config(root=tmp_path); led = _seed_n_routed(cfg, [0.9, 0.8, 0.7, 0.6])
    mine_suggestions(led, cfg); mine_suggestions(led, cfg)           # two passes drain all 4
    assert len(led.stitch_plans) == 4

def test_mine_per_candidate_fail_open(tmp_path, mocker):
    # a strategy error on ONE candidate logs + skips; the rest of the pass still completes
    cfg = Config(root=tmp_path); led = _seed_n_routed(cfg, [0.9, 0.8])
    import fanops.stitch_render as sr
    real = sr.make_stitch_plan
    def boom(clip, m, src, *, base_fp):
        if clip.id == "clip0": raise RuntimeError("strategy blew up")
        return real(clip, m, src, base_fp=base_fp)
    mocker.patch("fanops.stitch_render.make_stitch_plan", side_effect=boom)
    mine_suggestions(led, cfg)                                       # must NOT raise
    assert len(led.stitch_plans) == 1                                # clip1 still emitted; clip0 skipped
    assert led.moments["m0"].hook_strategy == awaiting("impact_cut")  # failed moment stays reserved (retries next pass)


# ---- M6 (intro-tease): the SECOND producer registered in mine_suggestions. For each moment the router
# reserved clean_awaiting_strategy:intro_tease whose matcher pairings landed (Moment.intro_matches), emit a
# suggested intro_tease StitchPlan for the TOP pairing, ranked against impact_cut by fit; re-route the moment
# stitch:intro_tease once its plan exists. Gated on cfg.intro_tease (a stale reservation after disable -> no plan). ----
def _seed_intro(cfg, *, matches):
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path=str(cfg.sources / "s1.mp4"), state=SourceState.signalled,
                          width=1920, height=1080, duration=20.0))
    led.add_source(Source(id="intro1", source_path=str(cfg.sources / "intro1.mp4"),
                          state=SourceState.catalogued, origin_kind="third_party"))
    led.add_moment(Moment(id="m1", parent_id="s1", state=MomentState.clipped, start=0.0, end=18.0,
                          reason="r", hook_strategy=awaiting("intro_tease"), intro_matches=matches))
    led.clips["clip_base"] = Clip(id="clip_base", parent_id="m1", path=str(cfg.clips / "clip_base.mp4"),
                                  state=ClipState.rendered)
    return led

def test_intro_tease_suggest_creates_plan_and_reroutes(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_INTRO_TEASE", "1")
    cfg = Config(root=tmp_path)
    led = _seed_intro(cfg, matches=[{"asset_id": "intro1", "fit_score": 0.88,
                                     "rationale": "stage entrance", "tease_text": "wait for it"}])
    _write_fp(cfg, "clip_base", "basefp")
    mine_suggestions(led, cfg)
    plans = [p for p in led.stitch_plans.values() if p.strategy_key == "intro_tease"]
    assert len(plans) == 1
    p = plans[0]
    assert p.clip_id == "clip_base" and p.asset_ids == ["intro1"] and p.base_fingerprint == "basefp"
    assert p.plan_params["intro_asset_id"] == "intro1" and p.plan_params["tease_text"] == "wait for it"
    assert p.rank_score == 0.88 and p.rationale == "stage entrance"
    assert led.moments["m1"].hook_strategy == "stitch:intro_tease"   # the intro_tease handler acted

def test_intro_tease_pairs_only_the_top_match_not_rank_two(tmp_path, monkeypatch):
    # audit c7-f2 (MVP decision pinned): with several matcher pairings, the producer pairs ONLY the best-fit
    # one — rank-2..N are not turned into plans or held as a fallback. A future mine pass re-pairs from the
    # then-current matches; rank-2-on-dismiss is a deferred fast-follow. This test pins the top-only contract.
    monkeypatch.setenv("FANOPS_INTRO_TEASE", "1")
    cfg = Config(root=tmp_path)
    led = _seed_intro(cfg, matches=[
        {"asset_id": "intro1", "fit_score": 0.92, "rationale": "best", "tease_text": "wait for it"},
        {"asset_id": "intro2", "fit_score": 0.71, "rationale": "second", "tease_text": "no"}])
    led.add_source(Source(id="intro2", source_path=str(cfg.sources / "intro2.mp4"),
                          state=SourceState.catalogued, origin_kind="third_party"))
    _write_fp(cfg, "clip_base", "basefp")
    mine_suggestions(led, cfg)
    plans = [p for p in led.stitch_plans.values() if p.strategy_key == "intro_tease"]
    assert len(plans) == 1                                            # exactly one plan, never one-per-match
    assert plans[0].asset_ids == ["intro1"]                           # the TOP match, not rank-2 (intro2)

def test_intro_tease_no_plan_when_unmatched(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_INTRO_TEASE", "1")
    cfg = Config(root=tmp_path)
    led = _seed_intro(cfg, matches=None)                             # matcher hasn't answered yet
    _write_fp(cfg, "clip_base", "basefp")
    mine_suggestions(led, cfg)
    assert led.stitch_plans == {}                                    # benign: nothing to suggest yet
    assert led.moments["m1"].hook_strategy == awaiting("intro_tease")  # stays reserved for next pass

def test_intro_tease_skips_when_fingerprint_absent(tmp_path, monkeypatch):
    # MOL-80 (second mining site): the same skip-when-fp-absent rule applies to intro_tease — no plan is
    # minted with a None pin; once the sidecar lands a later pass mints it with a real base_fingerprint.
    monkeypatch.setenv("FANOPS_INTRO_TEASE", "1")
    cfg = Config(root=tmp_path)
    led = _seed_intro(cfg, matches=[{"asset_id": "intro1", "fit_score": 0.88,
                                     "rationale": "x", "tease_text": "wait"}])
    # no fingerprint sidecar for clip_base yet
    mine_suggestions(led, cfg)
    assert led.stitch_plans == {}                                    # skipped this pass, no None pin
    assert led.moments["m1"].hook_strategy == awaiting("intro_tease")  # stays reserved -> retries next pass
    _write_fp(cfg, "clip_base", "basefp")
    mine_suggestions(led, cfg)
    plans = [p for p in led.stitch_plans.values() if p.strategy_key == "intro_tease"]
    assert len(plans) == 1 and plans[0].base_fingerprint == "basefp"

def test_intro_tease_off_emits_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_INTRO_TEASE", raising=False)
    cfg = Config(root=tmp_path)
    led = _seed_intro(cfg, matches=[{"asset_id": "intro1", "fit_score": 0.88,
                                     "rationale": "x", "tease_text": "wait"}])      # a stale reservation
    _write_fp(cfg, "clip_base", "basefp")
    mine_suggestions(led, cfg)
    assert led.stitch_plans == {}                                    # format off -> no intro_tease plans

def test_intro_tease_ranks_against_impact_cut_by_fit(tmp_path, monkeypatch):
    # both producers feed ONE ranked pass: a higher-fit intro_tease outranks a lower-score impact_cut.
    monkeypatch.setenv("FANOPS_INTRO_TEASE", "1")
    cfg = Config(root=tmp_path)
    led = _seed_intro(cfg, matches=[{"asset_id": "intro1", "fit_score": 0.97,
                                     "rationale": "x", "tease_text": "wait"}])
    _write_fp(cfg, "clip_base", "basefp")
    # add a competing impact_cut-routed moment with a weaker peak score
    led.add_source(Source(id="s2", source_path=str(cfg.sources / "s2.mp4"), state=SourceState.signalled,
                          signal_peaks=[{"t": 12.0, "score": 0.3}], width=1920, height=1080, duration=20.0))
    led.add_moment(Moment(id="m2", parent_id="s2", state=MomentState.clipped, start=0.0, end=18.0,
                          reason="r", hook_strategy=awaiting("impact_cut")))
    led.clips["clip2"] = Clip(id="clip2", parent_id="m2", path=str(cfg.clips / "clip2.mp4"), state=ClipState.rendered)
    _write_fp(cfg, "clip2", "fp2")
    import fanops.stitch_render as sr
    monkeypatch.setattr(sr, "MAX_SUGGESTIONS_PER_PASS", 1)           # only the BEST-fit suggestion survives the cap
    mine_suggestions(led, cfg)
    assert len(led.stitch_plans) == 1
    assert next(iter(led.stitch_plans.values())).strategy_key == "intro_tease"   # 0.97 beats 0.3


# ---- M6 Task 5: render-approved DISPATCHES by strategy_key. intro_tease renders via the compose-PREPEND
# path (MoviePy, LOCK-FREE prewarm + in-lock fingerprint-skip ADOPT — never MoviePy under the flock), born
# stitch_draft, same supersede precedence as impact_cut. impact_cut keeps the render_moment cut-window path. ----
def _seed_intro_approved(cfg, *, asset_id="intro1", base_fp="basefp", cur_fp="basefp", add_intro=True):
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path=str(cfg.sources / "s1.mp4"), state=SourceState.signalled,
                          width=1920, height=1080, duration=20.0))
    if add_intro:
        led.add_source(Source(id="intro1", source_path=str(cfg.sources / "intro1.mp4"),
                              state=SourceState.catalogued, origin_kind="third_party"))
    led.add_moment(Moment(id="m1", parent_id="s1", state=MomentState.clipped, start=0.0, end=18.0, reason="r"))
    led.clips["clip_base"] = Clip(id="clip_base", parent_id="m1", path=str(cfg.clips / "clip_base.mp4"),
                                  state=ClipState.rendered, aspect=Fmt.r9x16)
    _write_fp(cfg, "clip_base", cur_fp)
    params = {"intro_asset_id": asset_id, "tease_text": "wait for it", "intro_seconds": 2.0}
    led.add_stitch_plan(StitchPlan(id="iplan", clip_id="clip_base", strategy_key="intro_tease",
                                   asset_ids=[asset_id], plan_params=params,
                                   state=StitchState.approved, base_fingerprint=base_fp))
    return led

def _prewarm_intro_composite(cfg, led, *, asset_id="intro1"):
    # lay down the prewarmed composite mp4 + the compose-fp sidecar the in-lock commit checks (matching what
    # the code computes: base.path, intro.source_path, plan_params, src.width=1920, src.height=1080)
    from fanops.compose import _compose_fingerprint
    base = led.clips["clip_base"]; intro = led.sources[asset_id]
    cid = _stitch_clip_id("iplan", base.aspect.value)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    (cfg.clips / f"{cid}.mp4").write_bytes(b"COMPOSED")
    fp = _compose_fingerprint(base.path, intro.source_path, led.stitch_plans["iplan"].plan_params, 1920, 1080)
    (cfg.clips / f"{cid}.render.json").write_text(json.dumps({"fp": fp}))
    return cid

def test_intro_render_adopts_prewarmed_composite(tmp_path):
    cfg = Config(root=tmp_path); led = _seed_intro_approved(cfg)
    cid = _prewarm_intro_composite(cfg, led)
    render_approved_stitches(led, cfg)                              # adopts the warm mp4 — NO MoviePy in-lock
    assert led.clips[cid].state is ClipState.stitch_draft and led.clips[cid].parent_id == "m1"
    assert led.stitch_plans["iplan"].state is StitchState.in_use

def test_intro_render_waits_when_not_prewarmed(tmp_path):
    # lock-free discipline: with no prewarmed composite the commit must NOT render MoviePy under the lock —
    # it leaves the plan approved so the next prewarm produces it, then a later commit adopts.
    cfg = Config(root=tmp_path); led = _seed_intro_approved(cfg)
    render_approved_stitches(led, cfg)
    assert led.stitch_plans["iplan"].state is StitchState.approved  # still approved, not errored
    assert not any(c.state is ClipState.stitch_draft for c in led.clips.values())

def test_intro_render_errors_when_intro_asset_missing(tmp_path):
    cfg = Config(root=tmp_path); led = _seed_intro_approved(cfg, asset_id="ghost", add_intro=False)
    render_approved_stitches(led, cfg)
    assert led.stitch_plans["iplan"].state is StitchState.error
    assert "intro asset missing" in (led.stitch_plans["iplan"].error_reason or "")

def test_intro_render_does_not_retire_queued_base_post(tmp_path):
    # FAN ACCOUNTS: additive — the bare post survives alongside the intro_tease stitch (no double-post block)
    cfg = Config(root=tmp_path); led = _seed_intro_approved(cfg); _prewarm_intro_composite(cfg, led)
    led.posts["post_base"] = _base_post(PostState.queued)
    render_approved_stitches(led, cfg)
    assert led.posts["post_base"].state is PostState.queued
    assert led.stitch_plans["iplan"].state is StitchState.in_use

def test_intro_render_base_superseded_dismiss(tmp_path):
    # shared supersede precedence applies to intro_tease too: a drifted base fingerprint auto-dismisses
    cfg = Config(root=tmp_path); led = _seed_intro_approved(cfg, base_fp="OLD", cur_fp="NEW")
    _prewarm_intro_composite(cfg, led)
    render_approved_stitches(led, cfg)
    assert led.stitch_plans["iplan"].state is StitchState.dismissed
    assert not any(c.state is ClipState.stitch_draft for c in led.clips.values())

def test_intro_render_renders_even_with_live_base_post(tmp_path):
    # FAN ACCOUNTS: a published base does NOT block its intro_tease stitch — both ship (additive)
    cfg = Config(root=tmp_path); led = _seed_intro_approved(cfg); _prewarm_intro_composite(cfg, led)
    led.posts["post_base"] = _base_post(PostState.published)        # LIVE base post
    render_approved_stitches(led, cfg)
    assert led.stitch_plans["iplan"].state is StitchState.in_use
    assert any(c.state is ClipState.stitch_draft for c in led.clips.values())
    assert led.posts["post_base"].state is PostState.published      # untouched

def test_prewarm_intro_stamps_fp_lockfree(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = _seed_intro_approved(cfg); logs = []
    def fake_prepend(b, i, o, *, tease_text, intro_seconds, **kw):
        from pathlib import Path
        Path(o).parent.mkdir(parents=True, exist_ok=True); Path(o).write_bytes(b"COMPOSED"); return True
    mocker.patch("fanops.compose.prepend_intro", side_effect=fake_prepend)
    prewarm_approved_stitches(led, cfg, lambda *a, **k: logs.append(a))
    cid = _stitch_clip_id("iplan", "9:16")
    assert (cfg.clips / f"{cid}.mp4").exists()
    # the stamped fp must equal what the in-lock commit will recompute -> a following commit ADOPTS it
    render_approved_stitches(led, cfg)
    assert led.clips[cid].state is ClipState.stitch_draft and led.stitch_plans["iplan"].state is StitchState.in_use


# ---- M6 Task 6: retry-cap (flaky matcher/compose pairs park after N failed passes) + per-format strategies
# filter (the kill-switch freezes a disabled format's approved plans) + the disabled-format count. ----
def _intro_fail_marker(cfg, led, *, asset_id="intro1"):
    # the genuine-compose-failure marker the lock-free prewarm writes when prepend_intro produced no composite
    # (audit c7-f3) — fp-matched to iplan's stitch clip, so the in-lock commit counts it as a real attempt.
    from fanops.compose import _compose_fingerprint
    base = led.clips["clip_base"]; intro = led.sources[asset_id]
    cid = _stitch_clip_id("iplan", base.aspect.value)
    fp = _compose_fingerprint(base.path, intro.source_path, led.stitch_plans["iplan"].plan_params, 1920, 1080)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    (cfg.clips / f"{cid}.introfail.json").write_text(json.dumps({"fp": fp}))

def test_intro_render_parks_after_retry_cap(tmp_path):
    # GENUINE compose failures (a fp-matched introfail marker each pass) burn the cap; after the cap the plan is
    # PARKED (error), not retried forever (audit c7-f3 — only real failures count, not a not-yet-warm miss).
    import fanops.stitch_render as sr
    cfg = Config(root=tmp_path); led = _seed_intro_approved(cfg)
    for _ in range(sr.MAX_INTRO_RENDER_ATTEMPTS - 1):
        _intro_fail_marker(cfg, led)
        render_approved_stitches(led, cfg)
        assert led.stitch_plans["iplan"].state is StitchState.approved   # still trying, under the cap
    _intro_fail_marker(cfg, led)
    render_approved_stitches(led, cfg)                                   # the capping pass
    assert led.stitch_plans["iplan"].state is StitchState.error
    assert "after" in (led.stitch_plans["iplan"].error_reason or "")

def test_intro_render_attempts_reset_implicitly_on_adopt(tmp_path):
    # a genuine-failure pass increments attempts; a later successful prewarm still adopts (attempts moot once warm)
    cfg = Config(root=tmp_path); led = _seed_intro_approved(cfg)
    _intro_fail_marker(cfg, led)
    render_approved_stitches(led, cfg)                                   # 1 genuine-failure attempt
    assert led.stitch_plans["iplan"].render_attempts == 1
    _prewarm_intro_composite(cfg, led)                                   # success also clears the marker on adopt
    render_approved_stitches(led, cfg)
    assert led.stitch_plans["iplan"].state is StitchState.in_use

def test_render_approved_strategies_filter_freezes_disabled(tmp_path):
    # per-format kill-switch: a strategy NOT in the enabled set is frozen (left approved), never rendered
    cfg = Config(root=tmp_path); led = _seed_intro_approved(cfg); _prewarm_intro_composite(cfg, led)
    render_approved_stitches(led, cfg, strategies={"impact_cut"})        # intro_tease disabled
    assert led.stitch_plans["iplan"].state is StitchState.approved
    assert not any(c.state is ClipState.stitch_draft for c in led.clips.values())

def test_mine_strategies_filter_excludes_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_INTRO_TEASE", "1")
    cfg = Config(root=tmp_path)
    led = _seed_intro(cfg, matches=[{"asset_id": "intro1", "fit_score": 0.9, "rationale": "x", "tease_text": "w"}])
    _write_fp(cfg, "clip_base", "basefp")
    mine_suggestions(led, cfg, strategies={"impact_cut"})                # intro_tease excluded from this pass
    assert led.stitch_plans == {}

def test_approved_disabled_count(tmp_path):
    from fanops.stitch_render import approved_disabled_count
    cfg = Config(root=tmp_path); led = _seed_intro_approved(cfg)
    assert approved_disabled_count(led, enabled={"impact_cut"}) == 1     # the intro plan's format is disabled
    assert approved_disabled_count(led, enabled={"intro_tease"}) == 0    # enabled -> not frozen
    assert approved_disabled_count(led, enabled=set()) == 1              # both off -> frozen


def test_commit_intro_logs_and_burns_only_on_genuine_failure(tmp_path, mocker):
    # M6 observability + audit c7-f3: an approved intro_tease plan that isn't warm because the prewarm ATTEMPTED
    # and failed (a fp-matched introfail marker) burns a render_attempt AND leaves a breadcrumb naming the plan.
    from pathlib import Path
    from fanops.stitch_render import _commit_intro
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    base = Clip(id="b", parent_id="m", path=str(tmp_path / "b.mp4"), state=ClipState.queued, aspect=Fmt.r9x16)
    p = StitchPlan(id="sp_intro", clip_id="b", strategy_key="intro_tease", state=StitchState.approved)
    mocker.patch("fanops.stitch_render._intro_compose_fp", return_value="fp")
    mocker.patch("fanops.stitch_render._intro_render_target",
                 return_value=(base, base, "cid_x", Path(str(tmp_path / "nope.mp4"))))   # out_path absent -> not warm
    cfg.clips.mkdir(parents=True, exist_ok=True)
    (cfg.clips / "cid_x.introfail.json").write_text(json.dumps({"fp": "fp"}))            # genuine failure marker
    _commit_intro(led, cfg, p, base)
    assert p.render_attempts == 1                                        # a genuine attempt was consumed
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "sp_intro" in log and "intro" in log.lower()                 # breadcrumb names the plan

def test_commit_intro_transient_miss_waits_without_burning(tmp_path, mocker):
    # audit c7-f3: not warm AND no failure marker = a transient/structural miss (the prewarm hasn't produced the
    # composite yet). The plan must WAIT — no render_attempt burned — with a "waiting" breadcrumb, never parked.
    from pathlib import Path
    from fanops.stitch_render import _commit_intro
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    base = Clip(id="b", parent_id="m", path=str(tmp_path / "b.mp4"), state=ClipState.queued, aspect=Fmt.r9x16)
    p = StitchPlan(id="sp_intro", clip_id="b", strategy_key="intro_tease", state=StitchState.approved)
    mocker.patch("fanops.stitch_render._intro_compose_fp", return_value="fp")
    mocker.patch("fanops.stitch_render._intro_render_target",
                 return_value=(base, base, "cid_x", Path(str(tmp_path / "nope.mp4"))))   # not warm, NO marker
    _commit_intro(led, cfg, p, base)
    assert p.render_attempts == 0                                        # transient miss does NOT burn the cap
    assert p.state is StitchState.approved                              # still waiting, never parked
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "sp_intro" in log and "waiting" in log.lower()              # breadcrumb shows the wait
