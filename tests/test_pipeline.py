import json
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.pipeline import advance

def _put(p, b): p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)

def _ff(mocker):
    def fake(cmd, **kw):
        joined = " ".join(cmd)
        if cmd[0] == "whisper":
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
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "98432", "platforms": ["instagram", "tiktok"], "status": "active"}]}))
    _put(cfg.inbox / "raw.mp4", b"V")
    _ff(mocker)
    from fanops.models import MomentDecision, MomentPick, CaptionSet, CaptionItem
    from fanops.agentstep import response_path, latest_request_id

    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["sources"] == 1 and s["awaiting"]["moments"] == 1 and s["posts"] == 0

    src_id = next(iter(Ledger.load(cfg).sources))
    rid = latest_request_id(cfg, "moments", src_id)
    response_path(cfg, "moments", src_id).write_text(MomentDecision(
        source_id=src_id, request_id=rid,
        picks=[MomentPick(start=14.0, end=18.0, reason="punchline",
                          transcript_excerpt="they slept on me")]).model_dump_json())

    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["moments"] == 1 and s["clips"] >= 1 and s["awaiting"]["captions"] == 1

    led = Ledger.load(cfg); clip_id = next(iter(led.clips))
    rid2 = latest_request_id(cfg, "captions", clip_id)
    response_path(cfg, "captions", clip_id).write_text(CaptionSet(request_id=rid2, items=[
        CaptionItem(surface="@a/instagram", caption="no warning. just impact."),
        CaptionItem(surface="@a/tiktok", caption="wait for it.")]).model_dump_json())

    s = advance(cfg, base_time="2020-01-01T00:00:00Z")   # base in the PAST so posts are due
    assert s["posts"] == 2 and s["published"] == 2
    # AUDIT C1: needs_reconcile is an actionable parked state (ambiguous publish — may be live).
    # It must be visible in the advance() summary the unattended operator sees, not only the
    # digest. The dryrun backend never produces it, so the count is 0, but the KEY must exist.
    assert s["needs_reconcile"] == 0
    assert len(list(cfg.scheduled.glob("*.json"))) == 2

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
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")        # must NOT raise
    assert Ledger.load(cfg).sources["src_1"].state is SourceState.error
    assert "ffmpeg" in (Ledger.load(cfg).sources["src_1"].error_reason or "")
    assert s["errors"] >= 1                                    # surfaced in the summary count

def test_one_bad_source_does_not_wedge_the_pass(tmp_path, monkeypatch, mocker):
    # FIX F03: a source whose whisper crashes goes to error; others still advance.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    _put(cfg.inbox / "good.mp4", b"G"); _put(cfg.inbox / "bad.mp4", b"B")
    call = {"n": 0}
    def fake(cmd, **kw):
        if cmd[0] == "ffprobe":
            class R:
                returncode=0; stderr=""
                stdout = "video" if "codec_type" in " ".join(cmd) else "1920\n1080\n20.0\n"
            return R()
        if cmd[0] == "whisper":
            call["n"] += 1
            if call["n"] == 1:                      # first source: whisper raises
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


def test_advance_runs_inside_a_single_transaction(tmp_path, monkeypatch, mocker):
    # B1 (AUDIT B4): advance() must take the ledger transaction lock for the WHOLE pass (no
    # lock-free load + trailing save). Exactly ONE transaction wraps the pass.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    spy = mocker.spy(Ledger, "transaction")
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert spy.call_count == 1            # exactly one transaction wraps the pass


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
    # ingest_drops injects a catalogued source into the pass's ledger (no toolchain shelled)
    def fake_ingest(led, cfg, **kw):
        led.add_source(Source(id="src_prog", source_path=str(cfg.sources / "src_prog.mp4"),
                              state=SourceState.error))   # terminal state: no further stage touches it
        return led
    mocker.patch("fanops.pipeline.ingest_drops", side_effect=fake_ingest)
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
    def fake_ingest(led, cfg, **kw):
        led.add_source(Source(id="src_prog", source_path=str(cfg.sources / "src_prog.mp4"),
                              state=SourceState.error))   # terminal: no further stage touches it
        return led
    mocker.patch("fanops.pipeline.ingest_drops", side_effect=fake_ingest)
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
                      account="@a", account_id="1", platform=Platform.instagram,
                      caption="seeded", scheduled_time=sched))
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
                      account="@a", account_id="1", platform=Platform.instagram,
                      caption="seeded", scheduled_time=None))   # no scheduled_time -> age None
    led.save()
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["published"] >= 1
    assert s["published_in_run"] == 0
    assert s["last_published_age_hours"] is None


def test_advance_still_halts_on_fatal_auth_error_from_publish(tmp_path, monkeypatch, mocker):
    # The stage-level guard must NOT swallow a FATAL BlotatoAuthError — a bad key means every post
    # fails, so halting (and rolling back the pass) is the intended F52 behavior. The CLI run guard
    # turns the raise into a clean exit; here we just assert advance() RE-RAISES it.
    import pytest
    from fanops.errors import BlotatoAuthError
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    mocker.patch("fanops.pipeline.publish_due", side_effect=BlotatoAuthError("401 invalid key"))
    with pytest.raises(BlotatoAuthError):
        advance(cfg, base_time="2026-06-02T18:00:00Z")


def test_advance_halts_on_fatal_auth_error_from_crosspost(tmp_path, monkeypatch, mocker):
    # Phase-B-followup (review Minor): the crosspost stage wrapper must re-raise a fatal
    # BlotatoAuthError (symmetry with publish_due), not log-and-continue. crosspost has no Blotato
    # call today, but if one is added a bad key must halt the run, not be silently swallowed.
    import pytest
    from fanops.errors import BlotatoAuthError
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    mocker.patch("fanops.pipeline.crosspost_clips", side_effect=BlotatoAuthError("401 bad key"))
    with pytest.raises(BlotatoAuthError):
        advance(cfg, base_time="2026-06-02T18:00:00Z")
