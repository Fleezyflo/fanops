import json
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState, PostState
from fanops.pipeline import advance

def _put(p, b): p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)

def _is_asr(cmd):
    # The transcribe subprocess, EITHER engine: the legacy `whisper` CLI, or the default
    # faster-whisper runner (`python -m fanops._fwrun`). Both carry --output_dir + audio-last, so the
    # fakes below are engine-agnostic (dev has the [asr] extra -> fw runner; CI doesn't -> whisper CLI).
    return cmd[0] == "whisper" or "fanops._fwrun" in cmd

def _ff(mocker):
    def fake(cmd, **kw):
        joined = " ".join(cmd)
        if _is_asr(cmd):
            outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
            (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps(
                {"language": "en", "segments": [{"start": 14.0, "end": 18.0, "text": "they slept on me"}]}))
            class R: returncode=0; stderr=""; stdout=""
            return R()
        if cmd[0] in ("ffmpeg",) and "null" in cmd:
            class R:
                returncode=0; stdout=""
                stderr = ("silence_end: 16.0 | silence_duration: 1.0" if "silencedetect" in joined
                          else "[scdet @ 0x] lavfi.scd.score: 28.0, lavfi.scd.time: 16.0")
            return R()
        if cmd[0] == "ffprobe":
            class R:
                returncode=0; stderr=""
                stdout = "video" if "codec_type" in joined else "1920\n1080\n20.0\n"
            return R()
        # a FLAG last-arg (e.g. the `ffmpeg -filters` capability probe) is NOT an output path —
        # writing it would drop a junk `-filters` file into the repo root on every suite run
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode=0; stderr=""; stdout=""
        return R()
    for mod in ("transcribe", "signals", "clip", "ingest"):
        mocker.patch(f"fanops.{mod}.subprocess.run", side_effect=fake)

def test_advance_stops_at_gate_then_continues(tmp_path, monkeypatch, mocker):
    # dryrun-boundary: the pipeline's publish tail must actually SHIP the approved posts, and a dryrun post
    # no longer reaches `published` (it's held at the processing<->distribution seam). So run the publish
    # leg on a genuinely LIVE backend (postiz) with a stubbed poster + stubbed media uploaders (no network),
    # so `continues` past the approval gate means the posts truly enter the rail and reach `published`.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    # explicit per-channel backend for BOTH platforms so each routes to postiz (explicit wins — no platform
    # gate on an explicit provider), keeping the 2-posts-both-publish shape the test asserts.
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "98432", "platforms": ["instagram", "tiktok"], "status": "active",
         "backends": {"instagram": "postiz", "tiktok": "postiz"}}]}))
    _put(cfg.inbox / "raw.mp4", b"V")
    _ff(mocker)
    # stub the network leg of publish: a poster that promotes to submitted with a REAL permalink (so the
    # submitted->published gate fires) + media uploaders that return an https url (so no real upload runs,
    # whether a post carries plain-clip media, a file:// variant render, or a render_id).
    import fanops.post.run as run
    class _OkPoster:
        def __init__(self, cfg): pass
        def publish(self, led_, post_id):
            led_.posts[post_id].state = PostState.submitted; led_.posts[post_id].submission_id = "s"
            led_.posts[post_id].public_url = "https://www.instagram.com/reel/AAA/"
            return led_
    mocker.patch.object(run, "get_poster", return_value=_OkPoster(cfg))
    mocker.patch.object(run, "ensure_clip_media", return_value="https://cdn.postiz.test/c.mp4")
    mocker.patch.object(run, "get_media_uploader", return_value=lambda c, p, **kw: "https://cdn.postiz.test/c.mp4")
    mocker.patch("fanops.post.media.ensure_render_media", return_value="https://cdn.postiz.test/r.mp4")
    from fanops.models import MomentDecision, MomentPick, MomentHookDecision, CaptionSet, CaptionItem
    from fanops.agentstep import response_path, latest_request_id

    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["sources"] == 1 and s["awaiting"]["moments"] == 1 and s["posts"] == 0

    src_id = next(iter(Ledger.load(cfg).sources))
    from fanops.agentstep import gate_keys_for
    dotted = gate_keys_for(cfg, "moments", f"{src_id}.")
    pick_key = dotted[0] if dotted else src_id
    rid = latest_request_id(cfg, "moments", pick_key)
    response_path(cfg, "moments", pick_key).write_text(MomentDecision(
        source_id=src_id, request_id=rid,
        picks=[MomentPick(start=14.0, end=18.0, reason="punchline",
                          transcript_excerpt="they slept on me")]).model_dump_json())

    # M1b: answering the PICK gate lands picks_decided + opens the per-pick frame-seeing hook gate —
    # nothing renders yet (the hook is still owed; render keys on `decided`).
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["awaiting"]["moment_hooks"] == 1 and s["clips"] == 0

    hook_keys = gate_keys_for(cfg, "moment_hooks", f"{src_id}.")
    hook_key = hook_keys[0]
    hrid = latest_request_id(cfg, "moment_hooks", hook_key)
    response_path(cfg, "moment_hooks", hook_key).write_text(
        MomentHookDecision(request_id=hrid, hook="wait for the beat switch").model_dump_json())

    # answering the hook gate promotes the moment to decided -> the clip renders, captions are requested.
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["moments"] == 1 and s["clips"] >= 1 and s["awaiting"]["captions"] == 1

    led = Ledger.load(cfg); clip_id = next(iter(led.clips))
    rid2 = latest_request_id(cfg, "captions", clip_id)
    response_path(cfg, "captions", clip_id).write_text(CaptionSet(request_id=rid2, items=[
        CaptionItem(surface="a/instagram", caption="no warning. just impact."),
        CaptionItem(surface="a/tiktok", caption="wait for it.")]).model_dump_json())

    s = advance(cfg, base_time="2020-01-01T00:00:00Z")   # base in the PAST so posts are due
    # Post-approval gate: crosspost creates the 2 posts but they are BORN awaiting_approval, so an
    # unattended advance publishes NONE of them (the whole point of the gate).
    assert s["posts"] == 2 and s["published"] == 0
    assert len(list(cfg.scheduled.glob("*.json"))) == 0

    # the operator approves both posts (the human gate) -> queued; the next pass publishes them. On the
    # LIVE backend the stubbed poster promotes each to published with a real permalink.
    with Ledger.transaction(cfg) as led:
        for pid in list(led.posts): led.approve_post(pid, now_iso="2020-01-01T00:00:00Z")
    s = advance(cfg, base_time="2020-01-01T00:00:00Z")
    assert s["published"] == 2
    # AUDIT C1: needs_reconcile is an actionable parked state (ambiguous publish — may be live) that must be
    # visible in the advance() summary the unattended operator sees. Our stub poster returns a permalink, so
    # every post promotes cleanly to published — none park — but the KEY must exist.
    assert s["needs_reconcile"] == 0
    # both posts reached the terminal published state (the live rail's "shipped" proof; the dryrun poster's
    # 04_scheduled/*.json payloads don't exist on a live backend).
    led = Ledger.load(cfg)
    assert len(led.posts) == 2 and all(p.state is PostState.published for p in led.posts.values())

def test_advance_summary_counts_hook_burn_failed(tmp_path):
    # V2 M1/F9: a clip that silently lost its hook (couldn't burn) is COUNTED in the advance() summary
    # the unattended operator sees — not buried only in run.log. dryrun seeds none, so plant one.
    from fanops.models import Source, Moment, Clip, MomentState, SourceState
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/x.mp4", state=SourceState.moments_decided))
        led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7,
                              reason="r", state=MomentState.clipped))
        led.clips["c1"] = Clip(id="c1", parent_id="m1", path="/c1.mp4", hook_burn_failed=True)
    s = advance(cfg, base_time="2020-01-01T00:00:00Z")
    assert s["hook_burn_failed"] == 1

def test_signals_toolchain_absent_is_quarantined_not_a_crash(tmp_path, monkeypatch, mocker):
    # ffmpeg absent during the signals pass raises a typed ToolchainMissingError, but detect_signals
    # runs INSIDE advance()'s per-source quarantine, so the source goes to SourceState.error and the
    # pass returns normally — it must NOT crash advance() (unlike ingest, which is pre-quarantine).
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    # a source already transcribed, so advance() proceeds straight to the signals step
    led = Ledger.load(cfg)
    led.add_source(__import__("fanops.models", fromlist=["Source"]).Source(
        id="src_1", source_path=str(cfg.sources / "src_1.mp4"), state=SourceState.transcribed,
        transcript=[{"start": 0, "end": 1, "text": "x"}], meta={"transcribed": True}))
    led.save()
    def absent(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])
    mocker.patch("fanops.signals.subprocess.run", side_effect=absent)
    mocker.patch("fanops.signals.shutil.which", return_value=None)   # MOL-122: model absence at PATH too, so the
                                                                     # in-lock probe raises (a real absent toolchain
                                                                     # has ffmpeg off PATH) -> quarantine preserved.
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")        # must NOT raise
    assert Ledger.load(cfg).sources["src_1"].state is SourceState.error
    assert "ffmpeg" in (Ledger.load(cfg).sources["src_1"].error_reason or "")
    assert s["errors"] >= 1                                    # surfaced in the summary count

def test_one_bad_source_does_not_wedge_the_pass(tmp_path, monkeypatch, mocker):
    # FIX F03: a source whose whisper crashes goes to error; others still advance. The fault is keyed to
    # the BAD source's content (b"B"), NOT a call counter — Phase D's lock-free pre-warm runs the
    # transcribe subprocess too, so a call-count fault would fire on the wrong (warm) attempt. A
    # deterministic per-source fault models a genuinely-corrupt source (which fails every attempt).
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    _put(cfg.inbox / "good.mp4", b"G"); _put(cfg.inbox / "bad.mp4", b"B")
    def fake(cmd, **kw):
        if cmd[0] == "ffprobe":
            class R:
                returncode=0; stderr=""
                stdout = "video" if "codec_type" in " ".join(cmd) else "1920\n1080\n20.0\n"
            return R()
        if _is_asr(cmd):
            audio = Path(cmd[-1])
            if audio.exists() and audio.read_bytes() == b"B":   # the corrupt source: whisper always fails
                raise OSError("whisper exploded")
            outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
            (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps(
                {"language":"en","segments":[{"start":0,"end":2,"text":"hi"}]}))
            class R: returncode=0; stderr=""; stdout=""
            return R()
        if cmd[0] == "ffmpeg":
            class R: returncode=0; stdout=""; stderr="silence_end: 1.0 | silence_duration: 0.5"
            return R()
        # a FLAG last-arg (e.g. the `ffmpeg -filters` capability probe) is NOT an output path —
        # writing it would drop a junk `-filters` file into the repo root on every suite run
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode=0; stderr=""; stdout=""
        return R()
    for mod in ("transcribe", "signals", "clip", "ingest"):
        mocker.patch(f"fanops.{mod}.subprocess.run", side_effect=fake)
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    states = sorted(x.state.value for x in led.sources.values())
    assert "error" in states                         # the bad one quarantined
    assert any(v in states for v in ("moments_requested", "signalled", "transcribed"))  # good one progressed


def test_advance_mutations_are_all_under_a_held_lock(tmp_path, monkeypatch, mocker):
    # AUDIT B4 (Phase D restructure): every ledger mutation must happen inside a held-lock transaction
    # — no lock-free load + trailing save. Phase D moved the SLOW subprocess stages (whisper/ffmpeg) out
    # of the lock into a lock-free pre-warm BETWEEN two transactions: a short INGEST transaction and the
    # main commit transaction. So advance() opens exactly TWO transactions (not one), and the lost-update
    # protection still holds (proved by test_pipeline_prewarm.test_main_transaction_excludes_concurrent_writer
    # + test_ledger_lock.test_transaction_holds_lock_across_the_whole_block). The pre-warm holds NO lock
    # and saves NO state.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    spy = mocker.spy(Ledger, "transaction")
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert spy.call_count == 2            # ingest tx + main tx; slow work runs lock-free between them


def test_advance_rollback_recovers_warm_artifacts(tmp_path, monkeypatch, mocker):
    # audit x-f5: a LATE uncaught raise inside the main transaction rolls the WHOLE pass back (deliberate —
    # never persist a half-applied pass). That is SAFE because the heavy work was warmed OUT OF LOCK: the
    # transcript artifact survives the rollback and the NEXT pass adopts it instead of re-running whisper.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    _put(cfg.inbox / "vid.mp4", b"V")
    asr_calls = []
    def fake(cmd, **kw):
        joined = " ".join(cmd)
        if _is_asr(cmd):
            asr_calls.append(tuple(cmd))                      # count the EXPENSIVE whisper invocations
            outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
            (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps(
                {"language": "en", "segments": [{"start": 14.0, "end": 18.0, "text": "they slept on me"}]}))
            class R: returncode=0; stderr=""; stdout=""
            return R()
        if cmd[0] == "ffprobe":
            class R:
                returncode=0; stderr=""
                stdout = "video" if "codec_type" in joined else "1920\n1080\n20.0\n"
            return R()
        if cmd[0] == "ffmpeg" and "null" in cmd:
            class R: returncode=0; stdout=""; stderr="silence_end: 16.0 | silence_duration: 1.0"
            return R()
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode=0; stderr=""; stdout=""
        return R()
    for mod in ("transcribe", "signals", "clip", "ingest"):
        mocker.patch(f"fanops.{mod}.subprocess.run", side_effect=fake)

    # PASS 1: force a late uncaught raise from an in-transaction stage -> the whole main transaction rolls back.
    mocker.patch("fanops.pipeline._stage_structural_hooks", side_effect=RuntimeError("late boom"))
    import pytest
    with pytest.raises(RuntimeError, match="late boom"):
        advance(cfg, base_time="2026-06-02T18:00:00Z")
    after_p1 = len(asr_calls)
    src = next(iter(Ledger.load(cfg).sources.values()))
    src_id = src.id
    assert src.state is SourceState.catalogued             # rolled back to pre-main-tx state
    transcript = cfg.agent_io / "transcripts" / f"{Path(src.source_path).stem}.json"
    assert after_p1 >= 1 and transcript.exists()           # the warm artifact SURVIVED the rollback

    # PASS 2: clean (no forced raise). The source must advance WITHOUT re-running whisper — the warm
    # transcript is adopted, proving the rolled-back pass's expensive work was recovered, not redone.
    mocker.stopall()
    for mod in ("transcribe", "signals", "clip", "ingest"):
        mocker.patch(f"fanops.{mod}.subprocess.run", side_effect=fake)
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert Ledger.load(cfg).sources[src_id].state is not SourceState.catalogued   # progressed past catalogued
    assert len(asr_calls) == after_p1, "whisper re-ran — the warm artifact was not recovered after rollback"


def test_advance_auto_resumes_error_source_with_warm_transcript(tmp_path, monkeypatch, mocker):
    # Source quarantined after whisper timeout with transcript JSON on disk: next advance() adopts
    # transcribed/signalled without a second ASR invocation.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    vid = cfg.inbox / "vid.mp4"
    _put(vid, b"V")
    asr_calls = []
    def fake(cmd, **kw):
        joined = " ".join(cmd)
        if _is_asr(cmd):
            asr_calls.append(tuple(cmd))
            outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
            (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps(
                {"language": "en", "segments": [{"start": 14.0, "end": 18.0, "text": "they slept on me"}]}))
            class R: returncode=0; stderr=""; stdout=""
            return R()
        if cmd[0] == "ffprobe":
            class R:
                returncode=0; stderr=""
                stdout = "video" if "codec_type" in joined else "1920\n1080\n20.0\n"
            return R()
        if cmd[0] == "ffmpeg" and "null" in cmd:
            class R: returncode=0; stdout=""; stderr="silence_end: 16.0 | silence_duration: 1.0"
            return R()
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode=0; stderr=""; stdout=""
        return R()
    for mod in ("transcribe", "signals", "clip", "ingest"):
        mocker.patch(f"fanops.{mod}.subprocess.run", side_effect=fake)
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    after_first = len(asr_calls)
    assert after_first >= 1
    src = next(iter(Ledger.load(cfg).sources.values()))
    with Ledger.transaction(cfg) as led:
        s = led.sources[src.id]
        led.sources[src.id] = s.model_copy(update={"state": SourceState.error,
                                                    "error_reason": "TimeoutExpired: whisper timed out after 600s",
                                                    "transcript": None, "meta": {"transcribed": False}})
    transcript = cfg.agent_io / "transcripts" / f"{Path(src.source_path).stem}.json"
    assert transcript.exists()
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert len(asr_calls) == after_first, "whisper re-ran on auto-resume — warm transcript not adopted"
    s2 = Ledger.load(cfg).sources[src.id]
    assert s2.state is not SourceState.error
    assert s2.state.value in ("transcribed", "signalled", "moments_requested")


def test_advance_persists_progress_when_crosspost_raises(tmp_path, monkeypatch, mocker):
    # AUDIT M2 (hardened, NOT merely subsumed by B1): a raise from the volatile crosspost stage
    # must NOT discard the pass's earlier in-memory progress. Before this guard, a crosspost raise
    # propagated past the single save -> the whole pass (transcribe/signal/moments/clips/captions)
    # was rolled back. With the try/except, the transaction still exits cleanly and the exit-save
    # persists the completed transitions. We inject a catalogued source as the "earlier in-pass
    # progress" (mocking ingest_drops — NO real ffprobe, so this stays a true unit test that runs
    # in the no-toolchain CI job; CI-1 lesson), force crosspost to raise, and assert the source
    # survived in the saved ledger (not rolled back).
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    # ingest_staged injects a catalogued source into the pass's ledger (no toolchain shelled)
    from fanops.ingest import IngestCounts, StagedInbox
    def fake_ingest(led, cfg, staged, **kw):
        led.add_source(Source(id="src_prog", source_path=str(cfg.sources / "src_prog.mp4"),
                              state=SourceState.error))   # terminal state: no further stage touches it
        return led, IngestCounts()
    mocker.patch("fanops.pipeline.stage_inbox_candidates", return_value=StagedInbox(cfg.inbox, [], [], IngestCounts()))
    mocker.patch("fanops.pipeline.ingest_staged", side_effect=fake_ingest)
    mocker.patch("fanops.pipeline._archive_staged")
    # make crosspost blow up mid-pass
    mocker.patch("fanops.pipeline.crosspost_clips", side_effect=RuntimeError("crosspost boom"))
    advance(cfg, base_time="2026-06-02T18:00:00Z")   # must NOT raise
    saved = Ledger.load(cfg)
    assert "src_prog" in saved.sources                # the in-pass progress was PERSISTED, not rolled back


def test_advance_persists_progress_when_publish_raises_nonauth(tmp_path, monkeypatch, mocker):
    # AUDIT M2 (review finding): publish_due is the one in-transaction stage that was NOT wrapped.
    # A NON-auth raise from it (e.g. an unforeseen error) must NOT skip the exit-save and roll back
    # the pass. Mirror crosspost's guard: catch+log+continue, so completed work persists. Inject a
    # catalogued source as the earlier in-pass progress (mock ingest_drops — NO real ffprobe, so
    # this runs in the no-toolchain CI unit job; CI-1 lesson).
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    from fanops.ingest import IngestCounts, StagedInbox
    def fake_ingest(led, cfg, staged, **kw):
        led.add_source(Source(id="src_prog", source_path=str(cfg.sources / "src_prog.mp4"),
                              state=SourceState.error))   # terminal: no further stage touches it
        return led, IngestCounts()
    mocker.patch("fanops.pipeline.stage_inbox_candidates", return_value=StagedInbox(cfg.inbox, [], [], IngestCounts()))
    mocker.patch("fanops.pipeline.ingest_staged", side_effect=fake_ingest)
    mocker.patch("fanops.pipeline._archive_staged")
    mocker.patch("fanops.pipeline.publish_due", side_effect=RuntimeError("publish boom"))
    advance(cfg, base_time="2026-06-02T18:00:00Z")   # must NOT raise
    saved = Ledger.load(cfg)
    assert "src_prog" in saved.sources                # in-pass progress PERSISTED, not rolled back


def test_advance_reports_run_delta_and_last_post_age(tmp_path, monkeypatch, mocker):
    # B5/E2 (mutation-proven load-bearing): the advance() summary must carry a THIS-RUN published
    # delta (NOT the cumulative published count) and the age of the newest published post, so a
    # heartbeat monitor can tell 'alive-but-idle' from 'cron is dead'. We SEED an already-published
    # post (with a scheduled_time 5h in the PAST) and SAVE it BEFORE calling advance, so it is in
    # the `before` snapshot at transaction entry. advance() this pass makes NO new drops, so the
    # THIS-RUN delta must be 0 EVEN THOUGH cumulative published == 1 — this binds the set-difference
    # guarantee (a hollow test with no prior post can't tell delta from cumulative, both being 0).
    # And last_published_age_hours must be a real positive float (~5.0) from that past scheduled_time.
    from datetime import datetime, timezone, timedelta
    from fanops.models import Post, PostState, Platform
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    # pre-existing published post, scheduled 5h ago, committed to disk BEFORE the pass opens
    sched = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    led = Ledger.load(cfg)
    led.add_post(Post(id="post_pre", parent_id="clip_x", state=PostState.published,
                      account="a", account_id="1", platform=Platform.instagram,
                      caption="seeded", scheduled_time=sched, public_url="dryrun://post_pre"))
    led.save()

    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    # cumulative published reflects the pre-existing post ...
    assert s["published"] >= 1
    # ... but the THIS-RUN delta excludes it (the post was in `before` at txn entry).
    assert s["published_in_run"] == 0
    # the newest published post has a parseable past scheduled_time -> a real positive age (~5h).
    assert isinstance(s["last_published_age_hours"], float)
    assert s["last_published_age_hours"] > 0
    assert 4.5 < s["last_published_age_hours"] < 5.6      # ~5h ago, allowing for clock drift in the run

def test_advance_last_post_age_is_none_when_scheduled_time_absent(tmp_path, monkeypatch, mocker):
    # B5/E2 companion: a published post with NO parseable scheduled_time yields last_published_age_hours
    # == None (the _parse-returns-None branch), while the THIS-RUN delta still excludes the pre-existing
    # post. Pins both the None-age path and that delta is a set-difference, not the cumulative count.
    from fanops.models import Post, PostState, Platform
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    led = Ledger.load(cfg)
    led.add_post(Post(id="post_pre", parent_id="clip_x", state=PostState.published,
                      account="a", account_id="1", platform=Platform.instagram,
                      caption="seeded", scheduled_time=None, public_url="dryrun://post_pre"))   # no scheduled_time -> age None
    led.save()
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["published"] >= 1
    assert s["published_in_run"] == 0
    assert s["last_published_age_hours"] is None


def test_advance_still_halts_on_fatal_auth_error_from_publish(tmp_path, monkeypatch, mocker):
    # The stage-level guard must NOT swallow a FATAL PostizAuthError — a bad key means every post
    # fails, so halting (and rolling back the pass) is the intended F52 behavior. The CLI run guard
    # turns the raise into a clean exit; here we just assert advance() RE-RAISES it.
    import pytest
    from fanops.errors import PostizAuthError
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    mocker.patch("fanops.pipeline.publish_due", side_effect=PostizAuthError("401 invalid key"))
    with pytest.raises(PostizAuthError):
        advance(cfg, base_time="2026-06-02T18:00:00Z")


def test_advance_halts_on_fatal_auth_error_from_crosspost(tmp_path, monkeypatch, mocker):
    # Phase-B-followup (review Minor): the crosspost stage wrapper must re-raise a fatal
    # PostizAuthError (symmetry with publish_due), not log-and-continue. crosspost has no Blotato
    # call today, but if one is added a bad key must halt the run, not be silently swallowed.
    import pytest
    from fanops.errors import PostizAuthError
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    mocker.patch("fanops.pipeline.crosspost_clips", side_effect=PostizAuthError("401 bad key"))
    with pytest.raises(PostizAuthError):
        advance(cfg, base_time="2026-06-02T18:00:00Z")


# ---- M2 Task 5a: is_live_backend gate site #1 — the Blotato status reconciler stays Blotato-only ----
def _needs_reconcile_post():
    from fanops.models import Post, PostState, Platform
    return Post(id="p", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
               caption="x", state=PostState.needs_reconcile, submission_id="sub_x", public_url="dryrun://p")

def test_advance_postiz_now_reconciles_its_parked_posts(tmp_path, monkeypatch, mocker):
    # P2 INVERTS the old M2 behavior: Postiz GAINED a status-reconcile path (PostizStatusClient over the
    # date-windowed GET /public/v1/posts), so a postiz+key deployment's needs_reconcile posts ARE now
    # healed inside advance — each daemon fire publishes due posts AND heals parked ones. Gated on
    # is_live_backend (key present), so dryrun and key-less postiz stay shut (below).
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk"); monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); led.add_post(_needs_reconcile_post()); led.save()
    spy = mocker.patch("fanops.pipeline.reconcile_due", side_effect=lambda _cfg: {"needs_reconcile": 0, "published": 0})
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    spy.assert_called_once()

def test_postiz_without_key_is_not_live_backend(tmp_path, monkeypatch):
    # The reconcile gate is `is_live_backend AND poster_backend in (...)`. Widening the tuple to add
    # postiz does NOT loosen the key requirement: postiz WITHOUT a key is not a live backend, so the
    # gate (and all publishing) stays shut. Proven at the config layer — advance() itself, called
    # without the CLI preflight, HALTS in publish_due on the missing key (F52), a separate guard.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False); monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    assert Config(root=tmp_path).is_live_backend is False

def test_advance_dryrun_never_reconciles(tmp_path, monkeypatch, mocker):
    # dryrun is neither a live backend nor in the reconcile allow-list — the gate stays shut end-to-end
    # (DryRunPoster needs no key, so advance completes; reconcile_posts is never invoked).
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); led.add_post(_needs_reconcile_post()); led.save()
    spy = mocker.patch("fanops.pipeline.reconcile_due", side_effect=lambda _cfg: {"needs_reconcile": 0, "published": 0})
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    spy.assert_not_called()

def test_advance_zernio_backend_still_calls_reconciler(tmp_path, monkeypatch, mocker):
    # Back-compat: a live (zernio) + key backend STILL reconciles its stranded posts (unchanged from pre-M2).
    monkeypatch.setenv("FANOPS_POSTER", "zernio"); monkeypatch.setenv("ZERNIO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); led.add_post(_needs_reconcile_post()); led.save()
    spy = mocker.patch("fanops.pipeline.reconcile_due", side_effect=lambda _cfg: {"needs_reconcile": 0, "published": 0})
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    spy.assert_called_once()

def _accts_one(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "98432", "platforms": ["instagram"], "status": "active"}]}))

# ---- M1 (structural-hooks): third-party sources are INERT to clip-production ----
def test_third_party_skipped_in_both_loops_native_still_processed(tmp_path, monkeypatch, mocker):
    # The guard skips a third_party source as the FIRST line of BOTH _prewarm and the in-lock loop, so
    # transcribe_source is NEVER called for it (asserting "0 clips" alone is hollow — the gate blocks
    # clips regardless). A native source IS still processed (transcribe IS called for it).
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _accts_one(cfg)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_native", source_path=str(cfg.sources / "n.mp4"),
                              state=SourceState.catalogued, sha256="n"))
        led.add_source(Source(id="src_tp", source_path=str(cfg.sources / "t.mp4"),
                              origin_kind="third_party", state=SourceState.catalogued, sha256="t"))
    # advance() calls transcribe_source from BOTH binding sites (pipeline._prewarm AND produce._produce_one),
    # each a module-level import. Patch BOTH to the SAME spy so the real whisper never runs and every call
    # is observed on one object (patching only fanops.pipeline let the produce.py call hit real whisper).
    spy = mocker.patch("fanops.pipeline.transcribe_source", side_effect=lambda led, cfg, sid: led)
    mocker.patch("fanops.produce.transcribe_source", side_effect=spy)
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    sids = [c.args[2] for c in spy.call_args_list]
    assert "src_native" in sids and "src_tp" not in sids

def test_discovered_source_is_inert(tmp_path, monkeypatch, mocker):
    # a rebuild orphan (SourceState.discovered) matches no processing state -> never transcribed,
    # stays discovered (Task 5's rebuild relies on this inertness).
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _accts_one(cfg)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_disc", source_path=str(cfg.sources / "d.mp4"),
                              state=SourceState.discovered, sha256="d"))
    # patch BOTH transcribe_source bindings (pipeline + produce) so a leaked call to either is caught (see
    # the note in test_third_party_skipped_in_both_loops_native_still_processed).
    spy = mocker.patch("fanops.pipeline.transcribe_source", side_effect=lambda led, cfg, sid: led)
    prod_spy = mocker.patch("fanops.produce.transcribe_source", side_effect=lambda led, cfg, sid: led)
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    assert spy.call_count == 0 and prod_spy.call_count == 0 and Ledger.load(cfg).sources["src_disc"].state is SourceState.discovered

def test_native_renders_clip_while_third_party_inert(tmp_path, monkeypatch, mocker):
    # non-regression: with a third_party source present, a native moment STILL renders to a clip, and
    # the third_party source produces no moment/clip and stays catalogued (never enters the pipeline).
    from fanops.models import Moment, MomentState
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _accts_one(cfg)
    src = cfg.sources / "src_n.mp4"; _put(src, b"V"); _ff(mocker)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_n", source_path=str(src), state=SourceState.moments_decided,
                              sha256="n", width=1920, height=1080, duration=20.0,
                              transcript=[{"start": 0, "end": 2, "text": "hi"}]))
        led.add_moment(Moment(id="mom_n", parent_id="src_n", state=MomentState.decided,
                              start=14.0, end=18.0, reason="punchline"))
        led.add_source(Source(id="src_tp", source_path=str(cfg.sources / "tp.mp4"),
                              origin_kind="third_party", state=SourceState.catalogued, sha256="t"))
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    led = Ledger.load(cfg)
    assert any(c.parent_id == "mom_n" for c in led.clips.values())     # native moment rendered
    assert led.sources["src_tp"].state is SourceState.catalogued       # third-party untouched
    assert all(m.parent_id != "src_tp" for m in led.moments.values())  # no moment from third-party


# ---- M2 (structural-hooks): the hook-strategy router is wired after the critic, before render ----
def _seed_clean_decided(cfg):
    # a clean (no-hook) decided moment whose source has a signal peak inside the moment window
    src = cfg.sources / "src_r.mp4"; _put(src, b"V")
    from fanops.models import Moment, MomentState
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_r", source_path=str(src), state=SourceState.moments_decided,
                              sha256="r", width=1920, height=1080, duration=20.0,
                              signal_peaks=[{"t": 16.0, "score": 0.9}],
                              transcript=[{"start": 0, "end": 2, "text": "hi"}]))
        led.add_moment(Moment(id="mom_r", parent_id="src_r", state=MomentState.decided,
                              start=14.0, end=18.0, reason="punchline"))   # hook=None -> clean

def test_router_on_annotates_clean_moment_with_impact_cut_reservation(tmp_path, monkeypatch, mocker):
    from fanops.router import awaiting
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("FANOPS_HOOK_ROUTER", "1")
    cfg = Config(root=tmp_path); _accts_one(cfg); _ff(mocker); _seed_clean_decided(cfg)
    advance(cfg, base_time="2099-01-01T00:00:00Z")                     # router runs after critic, before render
    assert Ledger.load(cfg).moments["mom_r"].hook_strategy == awaiting("impact_cut")

def test_router_off_no_annotation_clip_still_renders(tmp_path, monkeypatch, mocker):
    # non-regression: router DEFAULT OFF -> no annotation, and the clean clip renders exactly as before
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.delenv("FANOPS_HOOK_ROUTER", raising=False)
    cfg = Config(root=tmp_path); _accts_one(cfg); _ff(mocker); _seed_clean_decided(cfg)
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    led = Ledger.load(cfg)
    assert led.moments["mom_r"].hook_strategy is None                  # observe-only; OFF = no delta
    assert any(c.parent_id == "mom_r" for c in led.clips.values())     # clip still renders

def test_hook_quality_scoreboard_fires_on_default_path(tmp_path, monkeypatch, mocker):
    # the viewer_pov_rate scoreboard is independent of the (deleted) editor/critic — it measures the
    # FINAL on-screen hooks, so it must still log on a normal run with the router DEFAULT OFF (else the
    # operator's hook-quality visibility silently vanished with the cascade).
    import fanops.pipeline as pipeline
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.delenv("FANOPS_HOOK_ROUTER", raising=False)
    calls = []
    monkeypatch.setattr(pipeline, "log_hook_quality", lambda led, cfg: calls.append(1))
    cfg = Config(root=tmp_path); _accts_one(cfg); _ff(mocker); _seed_clean_decided(cfg)
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    assert calls, "log_hook_quality must fire on the default path (router off)"


# ---- M4 (structural-hooks): impact-cut SUGGEST wired after the render loop (gated, fail-open) ----
def _seed_wide_clean_decided(cfg):
    # a clean (no-hook) decided moment wide enough for a real impact-cut: peak at t=12 inside [0,18] ->
    # cut [0, 11.6], span 11.6s >= IMPACT_MIN_DURATION
    src = cfg.sources / "src_w.mp4"; _put(src, b"V")
    from fanops.models import Moment, MomentState
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_w", source_path=str(src), state=SourceState.moments_decided,
                              sha256="w", width=1920, height=1080, duration=20.0,
                              signal_peaks=[{"t": 12.0, "score": 0.9}],
                              transcript=[{"start": 0, "end": 2, "text": "hi"}]))
        led.add_moment(Moment(id="mom_w", parent_id="src_w", state=MomentState.decided,
                              start=0.0, end=18.0, reason="punchline"))   # hook=None -> clean

def test_impact_cut_on_suggests_plan_and_reroutes(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("FANOPS_HOOK_ROUTER", "1"); monkeypatch.setenv("FANOPS_IMPACT_CUT", "1")
    cfg = Config(root=tmp_path); _accts_one(cfg); _ff(mocker); _seed_wide_clean_decided(cfg)
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    led = Ledger.load(cfg)
    plans = [p for p in led.stitch_plans.values() if p.strategy_key == "impact_cut"]
    assert len(plans) >= 1 and plans[0].state.value == "suggested"
    assert led.moments["mom_w"].hook_strategy == "stitch:impact_cut"

def test_impact_cut_off_no_plans(tmp_path, monkeypatch, mocker):
    # router on (so the moment is reserved) but the producer OFF -> no plans, no re-route (non-regression)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("FANOPS_HOOK_ROUTER", "1"); monkeypatch.delenv("FANOPS_IMPACT_CUT", raising=False)
    cfg = Config(root=tmp_path); _accts_one(cfg); _ff(mocker); _seed_wide_clean_decided(cfg)
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    led = Ledger.load(cfg)
    assert led.stitch_plans == {}
    from fanops.router import awaiting
    assert led.moments["mom_w"].hook_strategy == awaiting("impact_cut")  # reserved, not produced

def test_impact_cut_killswitch_warns_and_does_not_render(tmp_path, monkeypatch, mocker):
    # forward-only kill-switch: an approved plan with the feature OFF stays approved (not rendered, not
    # retracted) and the pass logs a WARNING naming the count — never a silent freeze (PRD).
    from fanops.models import StitchPlan, StitchState, ClipState, Clip
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.delenv("FANOPS_HOOK_ROUTER", raising=False); monkeypatch.delenv("FANOPS_IMPACT_CUT", raising=False)
    cfg = Config(root=tmp_path); _accts_one(cfg); _ff(mocker)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_k", source_path=str(cfg.sources / "src_k.mp4"),
                              state=SourceState.signalled, sha256="k", width=1920, height=1080, duration=20.0))
        led.clips["clip_k"] = Clip(id="clip_k", parent_id="m_k", path=str(cfg.clips / "clip_k.mp4"),
                                   state=ClipState.rendered)
        led.add_stitch_plan(StitchPlan(id="plan_k", clip_id="clip_k", strategy_key="impact_cut",
                                       plan_params={"cut_start": 0.0, "cut_end": 11.6}, state=StitchState.approved))
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    led = Ledger.load(cfg)
    assert led.stitch_plans["plan_k"].state is StitchState.approved      # not rendered, not retracted
    assert not any(c.state is ClipState.stitch_draft for c in led.clips.values())
    assert "feature OFF" in cfg.log_path.read_text()                     # the warning fired


# ---- M6 (intro-tease): the matcher gate + producer wired into advance (gated on intro_tease + responder llm) ----
def _seed_clean_nopeak_decided(cfg):
    # a clean (no-hook) decided moment with NO signal peak -> the router reserves it intro_tease (when enabled)
    src = cfg.sources / "src_i.mp4"; _put(src, b"V")
    from fanops.models import Moment, MomentState
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_i", source_path=str(src), state=SourceState.moments_decided,
                              sha256="i", width=1920, height=1080, duration=20.0,
                              transcript=[{"start": 0, "end": 2, "text": "hi"}]))   # no signal_peaks -> no impact-cut
        led.add_moment(Moment(id="mom_i", parent_id="src_i", state=MomentState.decided,
                              start=0.0, end=18.0, reason="clean payoff"))           # hook=None -> clean
        led.add_source(Source(id="intro_a", source_path=str(cfg.sources / "intro_a.mp4"),
                              state=SourceState.catalogued, origin_kind="third_party"))   # a candidate intro asset

def test_intro_tease_matcher_gate_requested(tmp_path, monkeypatch, mocker):
    # router on + intro_tease on + responder llm: advance reserves the clean moment intro_tease and OPENS the
    # matcher gate (no responder answers it here -> no plan yet, which is the benign fail-open).
    from fanops.agentstep import pending
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("FANOPS_HOOK_ROUTER", "1"); monkeypatch.setenv("FANOPS_INTRO_TEASE", "1")
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path); _accts_one(cfg); _ff(mocker); _seed_clean_nopeak_decided(cfg)
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    led = Ledger.load(cfg)
    from fanops.router import awaiting
    assert led.moments["mom_i"].hook_strategy == awaiting("intro_tease")  # reserved for the matcher
    assert pending(cfg, kind="intro_match")                               # the matcher gate was opened
    assert not [p for p in led.stitch_plans.values() if p.strategy_key == "intro_tease"]  # no answer -> no plan

def test_intro_tease_off_no_matcher_gate(tmp_path, monkeypatch, mocker):
    # intro_tease OFF: no reservation, no matcher gate, clean_final (non-regression)
    from fanops.agentstep import pending
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("FANOPS_HOOK_ROUTER", "1"); monkeypatch.delenv("FANOPS_INTRO_TEASE", raising=False)
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path); _accts_one(cfg); _ff(mocker); _seed_clean_nopeak_decided(cfg)
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    led = Ledger.load(cfg)
    from fanops.router import CLEAN_FINAL
    assert led.moments["mom_i"].hook_strategy == CLEAN_FINAL
    assert pending(cfg, kind="intro_match") == []

def test_intro_tease_killswitch_warns(tmp_path, monkeypatch, mocker):
    # an approved intro_tease plan with the format OFF stays approved (frozen, not rendered) + warns
    from fanops.models import StitchPlan, StitchState, ClipState, Clip
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.delenv("FANOPS_HOOK_ROUTER", raising=False); monkeypatch.delenv("FANOPS_INTRO_TEASE", raising=False)
    cfg = Config(root=tmp_path); _accts_one(cfg); _ff(mocker)
    with Ledger.transaction(cfg) as led:
        led.clips["clip_i"] = Clip(id="clip_i", parent_id="m_i", path=str(cfg.clips / "clip_i.mp4"),
                                   state=ClipState.rendered)
        led.add_stitch_plan(StitchPlan(id="plan_i", clip_id="clip_i", strategy_key="intro_tease",
                                       asset_ids=["intro_a"], plan_params={"intro_asset_id": "intro_a",
                                       "tease_text": "wait", "intro_seconds": 2.0}, state=StitchState.approved))
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    led = Ledger.load(cfg)
    assert led.stitch_plans["plan_i"].state is StitchState.approved      # frozen, not rendered
    assert not any(c.state is ClipState.stitch_draft for c in led.clips.values())
    assert "feature OFF" in cfg.log_path.read_text()


def test_run_summary_carries_frames_unread_count(tmp_path):
    # AGENT-9: a moment whose hook was authored frames-attached-but-unread is counted in the heartbeat
    # (like hook_burn_failed) so the degraded, text-grounded hook is VISIBLE, not just a log line.
    from fanops.models import Moment, MomentState
    from fanops.pipeline import _build_summary
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.moments_decided, duration=20.0))
    led.moments["m1"] = Moment(id="m1", parent_id="s1", start=1.0, end=5.0, reason="r",
                               state=MomentState.decided, hook_frames_unread=True)
    led.moments["m2"] = Moment(id="m2", parent_id="s1", start=6.0, end=9.0, reason="r",
                               state=MomentState.decided)   # frames read -> NOT counted
    led.save()
    s = _build_summary(cfg, before=set())
    assert s["frames_unread"] == 1


# MOL-444: drift guard — every PostState must be counted in RunSummary or explicitly excluded.
def test_run_summary_every_poststate_accounted(tmp_path):
    from fanops.models import PostState
    from fanops.pipeline import (_build_summary, _DIGEST_EXCLUDED_STATES, _RUNSUMMARY_NON_STATE_KEYS)
    summary = _build_summary(Config(root=tmp_path), before=set())
    state_keys = {k for k in summary if k not in _RUNSUMMARY_NON_STATE_KEYS}
    for state in PostState:
        if state in _DIGEST_EXCLUDED_STATES:
            assert state.value not in state_keys, f"{state.value} excluded but present in RunSummary"
        else:
            assert state.value in state_keys, (
                f"PostState.{state.name} ({state.value}) missing from RunSummary — "
                f"add a count or list it in _DIGEST_EXCLUDED_STATES")
    expected = {st.value for st in PostState if st not in _DIGEST_EXCLUDED_STATES}
    assert state_keys == expected, f"unexpected post-state keys: {state_keys - expected} / {expected - state_keys}"


# MOL-440: gave_up posts (needs_reconcile + GAVE UP: prefix) are split out of needs_reconcile.
def test_run_summary_gave_up_disjoint_from_needs_reconcile(tmp_path):
    from fanops.models import Post, PostState, Platform
    from fanops.pipeline import _build_summary
    from fanops.reconcile import _GIVEUP_PREFIX
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="nr1", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, error_reason="timeout after send"))
    led.add_post(Post(id="gu1", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile,
                      error_reason=f"{_GIVEUP_PREFIX} unresolved 72h past schedule on a fake token"))
    led.add_post(Post(id="gu2", parent_id="c", account="a", account_id="1", platform=Platform.tiktok,
                      caption="y", state=PostState.needs_reconcile,
                      error_reason=f"{_GIVEUP_PREFIX} operator parked"))
    led.save()
    s = _build_summary(cfg, before=set())
    assert s["gave_up"] == 2
    assert s["needs_reconcile"] == 1
    assert s["gave_up"] + s["needs_reconcile"] == 3
