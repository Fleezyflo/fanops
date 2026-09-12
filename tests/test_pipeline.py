# tests/test_pipeline.py
import json
import shutil
from pathlib import Path

import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState, PostState
from fanops.pipeline import advance
from tests.fixtures.speech_segments import talk_seg

@pytest.fixture(autouse=True)
def _gate_off(monkeypatch):
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "0")
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")

def _put(p, b): p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)

def _is_asr(cmd):
    # The transcribe subprocess, EITHER engine: the legacy `whisper` CLI, or the default
    # faster-whisper runner (`python -m fanops._fwrun`). Both carry --output_dir + audio-last, so the
    # fakes below are engine-agnostic (dev has the [asr] extra -> fw runner; CI doesn't -> whisper CLI).
    return cmd[0] == "whisper" or "fanops._fwrun" in cmd

def _ff(mocker):
    def fake(cmd, **kw):
        joined = " ".join(str(c) for c in cmd)
        if _is_asr(cmd):
            outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
            (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps(
                {"language": "en", "segments": [talk_seg("they slept on me", start=13.0, end=20.0)]}))
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
    for mod in ("transcribe", "signals", "clip", "ingest", "media_probe"):
        mocker.patch(f"fanops.{mod}.subprocess.run", side_effect=fake)

class _Http:
    status_code = 200
    text = '{"posts":[]}'
    def json(self):
        return {"posts": []}

def test_advance_stops_at_gate_then_continues(tmp_path, monkeypatch, mocker):
    # dryrun-boundary: posts are born awaiting_approval; after operator approve they may be due, but
    # dryrun never reaches PostState.published (preview sidecar only). Walk the real gates + ledger.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "98432", "platforms": ["instagram", "tiktok"], "status": "active"}]}))
    _put(cfg.inbox / "raw.mp4", b"V")
    _ff(mocker)
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
        picks=[MomentPick(start=13.0, end=20.0, reason="punchline",
                          transcript_excerpt="they slept on me")]).model_dump_json())

    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["awaiting"]["moment_hooks"] == 1 and s["clips"] == 0

    hook_keys = gate_keys_for(cfg, "moment_hooks", f"{src_id}.")
    hook_key = hook_keys[0]
    hrid = latest_request_id(cfg, "moment_hooks", hook_key)
    response_path(cfg, "moment_hooks", hook_key).write_text(
        MomentHookDecision(request_id=hrid, hook="wait for the beat switch").model_dump_json())

    from fanops.source_tags import source_tag_locks_path
    lock_p = source_tag_locks_path(cfg)
    lock_p.parent.mkdir(parents=True, exist_ok=True)
    lock_p.write_text(json.dumps({
        src_id: {"pile": [], "lock": [], "researched_at": "2026-08-17T00:00:00Z"},
    }))

    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["moments"] == 1 and s["clips"] >= 1 and s["awaiting"]["captions"] == 1

    led = Ledger.load(cfg); clip_id = next(iter(led.clips))
    rid2 = latest_request_id(cfg, "captions", clip_id)
    response_path(cfg, "captions", clip_id).write_text(CaptionSet(request_id=rid2, items=[
        CaptionItem(surface="a/instagram", caption="no warning. just impact."),
        CaptionItem(surface="a/tiktok", caption="wait for it.")]).model_dump_json())

    s = advance(cfg, base_time="2020-01-01T00:00:00Z")
    assert s["posts"] == 2 and s["published"] == 0
    assert len(list(cfg.scheduled.glob("*.json"))) == 0
    assert "needs_reconcile" in s

    with Ledger.transaction(cfg) as led:
        for pid in list(led.posts): led.approve_post(pid, now_iso="2020-01-01T00:00:00Z")
    s = advance(cfg, base_time="2020-01-01T00:00:00Z")
    led = Ledger.load(cfg)
    assert len(led.posts) == 2
    assert all(p.state is not PostState.awaiting_approval for p in led.posts.values())
    assert all(p.state is not PostState.published for p in led.posts.values())
    assert s["published"] == 0
    assert list(cfg.scheduled.glob("*.json"))

def test_advance_summary_counts_hook_burn_failed(tmp_path):
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
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    led = Ledger.load(cfg)
    led.add_source(__import__("fanops.models", fromlist=["Source"]).Source(
        id="src_1", source_path=str(cfg.sources / "src_1.mp4"), state=SourceState.transcribed,
        transcript=[{"start": 0, "end": 1, "text": "x"}], meta={"transcribed": True}))
    led.save()
    real_which = shutil.which
    def _which(name, *a, **k):
        if name == "ffmpeg":
            return None
        return real_which(name, *a, **k)
    monkeypatch.setattr(shutil, "which", _which)
    def absent(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])
    mocker.patch("fanops.signals.subprocess.run", side_effect=absent)
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert Ledger.load(cfg).sources["src_1"].state is SourceState.error
    assert "ffmpeg" in (Ledger.load(cfg).sources["src_1"].error_reason or "")
    assert s["errors"] >= 1

def test_one_bad_source_does_not_wedge_the_pass(tmp_path, monkeypatch, mocker):
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
            if audio.exists() and audio.read_bytes() == b"B":
                raise OSError("whisper exploded")
            outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
            (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps(
                {"language": "en", "segments": [talk_seg("hi", start=0, end=2)]}))
            class R: returncode=0; stderr=""; stdout=""
            return R()
        if cmd[0] == "ffmpeg":
            class R: returncode=0; stdout=""; stderr="silence_end: 1.0 | silence_duration: 0.5"
            return R()
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode=0; stderr=""; stdout=""
        return R()
    for mod in ("transcribe", "signals", "clip", "ingest", "media_probe"):
        mocker.patch(f"fanops.{mod}.subprocess.run", side_effect=fake)
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    states = sorted(x.state.value for x in led.sources.values())
    assert "error" in states
    assert any(v in states for v in ("moments_requested", "signalled", "transcribed"))


def test_advance_auto_resumes_error_source_with_warm_transcript(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    vid = cfg.inbox / "vid.mp4"
    _put(vid, b"V")
    asr_calls = []
    def fake(cmd, **kw):
        joined = " ".join(str(c) for c in cmd)
        if _is_asr(cmd):
            asr_calls.append(tuple(cmd))
            outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
            (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps(
                {"language": "en", "segments": [talk_seg("they slept on me", start=13.0, end=20.0)]}))
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
    for mod in ("transcribe", "signals", "clip", "ingest", "media_probe"):
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


def test_advance_reports_run_delta_and_last_post_age(tmp_path, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from fanops.models import Post, PostState, Platform
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    sched = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    led = Ledger.load(cfg)
    led.add_post(Post(id="post_pre", parent_id="clip_x", state=PostState.published,
                      account="a", account_id="1", platform=Platform.instagram,
                      caption="seeded", scheduled_time=sched, public_url="dryrun://post_pre"))
    led.save()

    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["published"] >= 1
    assert s["published_in_run"] == 0
    assert isinstance(s["last_published_age_hours"], float)
    assert s["last_published_age_hours"] > 0
    assert 4.5 < s["last_published_age_hours"] < 5.6

def test_advance_last_post_age_is_none_when_scheduled_time_absent(tmp_path, monkeypatch):
    from fanops.models import Post, PostState, Platform
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    led = Ledger.load(cfg)
    led.add_post(Post(id="post_pre", parent_id="clip_x", state=PostState.published,
                      account="a", account_id="1", platform=Platform.instagram,
                      caption="seeded", scheduled_time=None, public_url="dryrun://post_pre"))
    led.save()
    s = advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert s["published"] >= 1
    assert s["published_in_run"] == 0
    assert s["last_published_age_hours"] is None


def _needs_reconcile_post():
    from fanops.models import Post, PostState, Platform
    return Post(id="p", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
               caption="x", state=PostState.needs_reconcile, submission_id="sub_x", public_url="dryrun://p")

def test_advance_postiz_now_reconciles_its_parked_posts(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk"); monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); led.add_post(_needs_reconcile_post()); led.save()
    spy = mocker.patch("fanops.post.metrics.postiz_read.requests.get", return_value=_Http())
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert spy.called
    assert Ledger.load(cfg).posts["p"].state is PostState.needs_reconcile

def test_postiz_without_key_is_not_live_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False); monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    assert Config(root=tmp_path).is_live_backend is False

def test_advance_dryrun_never_reconciles(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); led.add_post(_needs_reconcile_post()); led.save()
    spy = mocker.patch("fanops.post.metrics.postiz_read.requests.get", return_value=_Http())
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert not spy.called
    assert Ledger.load(cfg).posts["p"].state is PostState.needs_reconcile

def test_advance_zernio_backend_still_calls_reconciler(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_POSTER", "zernio"); monkeypatch.setenv("ZERNIO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); led.add_post(_needs_reconcile_post()); led.save()
    spy = mocker.patch("fanops.post.metrics.zernio_read.requests.get", return_value=_Http())
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    assert spy.called
    assert Ledger.load(cfg).posts["p"].state is PostState.needs_reconcile

def _accts_one(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "98432", "platforms": ["instagram"], "status": "active"}]}))

def test_third_party_skipped_in_both_loops_native_still_processed(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _accts_one(cfg)
    native = cfg.sources / "n.mp4"; _put(native, b"V"); _ff(mocker)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_native", source_path=str(native),
                              state=SourceState.catalogued, sha256="n",
                              width=1920, height=1080, duration=20.0))
        led.add_source(Source(id="src_tp", source_path=str(cfg.sources / "t.mp4"),
                              origin_kind="third_party", state=SourceState.catalogued, sha256="t"))
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    led = Ledger.load(cfg)
    assert led.sources["src_tp"].state is SourceState.catalogued
    assert led.sources["src_native"].state is not SourceState.catalogued
    tp_json = cfg.agent_io / "transcripts" / "t.json"
    assert not tp_json.exists()

def test_discovered_source_is_inert(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _accts_one(cfg)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_disc", source_path=str(cfg.sources / "d.mp4"),
                              state=SourceState.discovered, sha256="d"))
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    assert Ledger.load(cfg).sources["src_disc"].state is SourceState.discovered

def test_native_renders_clip_while_third_party_inert(tmp_path, monkeypatch, mocker):
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
    assert any(c.parent_id == "mom_n" for c in led.clips.values())
    assert led.sources["src_tp"].state is SourceState.catalogued
    assert all(m.parent_id != "src_tp" for m in led.moments.values())


def _seed_clean_decided(cfg):
    src = cfg.sources / "src_r.mp4"; _put(src, b"V")
    from fanops.models import Moment, MomentState
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_r", source_path=str(src), state=SourceState.moments_decided,
                              sha256="r", width=1920, height=1080, duration=20.0,
                              signal_peaks=[{"t": 16.0, "score": 0.9}],
                              transcript=[{"start": 0, "end": 2, "text": "hi"}]))
        led.add_moment(Moment(id="mom_r", parent_id="src_r", state=MomentState.decided,
                              start=14.0, end=18.0, reason="punchline"))

def test_router_on_annotates_clean_moment_with_impact_cut_reservation(tmp_path, monkeypatch, mocker):
    from fanops.router import awaiting
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("FANOPS_HOOK_ROUTER", "1")
    cfg = Config(root=tmp_path); _accts_one(cfg); _ff(mocker); _seed_clean_decided(cfg)
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    assert Ledger.load(cfg).moments["mom_r"].hook_strategy == awaiting("impact_cut")

def test_router_off_no_annotation_clip_still_renders(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.delenv("FANOPS_HOOK_ROUTER", raising=False)
    cfg = Config(root=tmp_path); _accts_one(cfg); _ff(mocker); _seed_clean_decided(cfg)
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    led = Ledger.load(cfg)
    assert led.moments["mom_r"].hook_strategy is None
    assert any(c.parent_id == "mom_r" for c in led.clips.values())

def test_hook_quality_scoreboard_fires_on_default_path(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.delenv("FANOPS_HOOK_ROUTER", raising=False)
    cfg = Config(root=tmp_path); _accts_one(cfg); _ff(mocker); _seed_clean_decided(cfg)
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "hook_quality" in log


def _seed_wide_clean_decided(cfg):
    src = cfg.sources / "src_w.mp4"; _put(src, b"V")
    from fanops.models import Moment, MomentState
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_w", source_path=str(src), state=SourceState.moments_decided,
                              sha256="w", width=1920, height=1080, duration=20.0,
                              signal_peaks=[{"t": 12.0, "score": 0.9}],
                              transcript=[{"start": 0, "end": 2, "text": "hi"}]))
        led.add_moment(Moment(id="mom_w", parent_id="src_w", state=MomentState.decided,
                              start=0.0, end=18.0, reason="punchline"))

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
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("FANOPS_HOOK_ROUTER", "1"); monkeypatch.delenv("FANOPS_IMPACT_CUT", raising=False)
    cfg = Config(root=tmp_path); _accts_one(cfg); _ff(mocker); _seed_wide_clean_decided(cfg)
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    led = Ledger.load(cfg)
    assert led.stitch_plans == {}
    from fanops.router import awaiting
    assert led.moments["mom_w"].hook_strategy == awaiting("impact_cut")

def test_impact_cut_killswitch_warns_and_does_not_render(tmp_path, monkeypatch, mocker):
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
    assert led.stitch_plans["plan_k"].state is StitchState.approved
    assert not any(c.state is ClipState.stitch_draft for c in led.clips.values())
    assert "feature OFF" in cfg.log_path.read_text()


def _seed_clean_nopeak_decided(cfg):
    src = cfg.sources / "src_i.mp4"; _put(src, b"V")
    from fanops.models import Moment, MomentState
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_i", source_path=str(src), state=SourceState.moments_decided,
                              sha256="i", width=1920, height=1080, duration=20.0,
                              transcript=[{"start": 0, "end": 2, "text": "hi"}]))
        led.add_moment(Moment(id="mom_i", parent_id="src_i", state=MomentState.decided,
                              start=0.0, end=18.0, reason="clean payoff"))
        led.add_source(Source(id="intro_a", source_path=str(cfg.sources / "intro_a.mp4"),
                              state=SourceState.catalogued, origin_kind="third_party"))

def test_intro_tease_matcher_gate_requested(tmp_path, monkeypatch, mocker):
    from fanops.agentstep import pending
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("FANOPS_HOOK_ROUTER", "1"); monkeypatch.setenv("FANOPS_INTRO_TEASE", "1")
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path); _accts_one(cfg); _ff(mocker); _seed_clean_nopeak_decided(cfg)
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    led = Ledger.load(cfg)
    from fanops.router import awaiting
    assert led.moments["mom_i"].hook_strategy == awaiting("intro_tease")
    assert pending(cfg, kind="intro_match")
    assert not [p for p in led.stitch_plans.values() if p.strategy_key == "intro_tease"]

def test_intro_tease_off_no_matcher_gate(tmp_path, monkeypatch, mocker):
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
    assert led.stitch_plans["plan_i"].state is StitchState.approved
    assert not any(c.state is ClipState.stitch_draft for c in led.clips.values())
    assert "feature OFF" in cfg.log_path.read_text()


def test_run_summary_carries_frames_unread_count(tmp_path):
    from fanops.models import Moment, MomentState
    from fanops.pipeline import _build_summary
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.moments_decided, duration=20.0))
    led.moments["m1"] = Moment(id="m1", parent_id="s1", start=1.0, end=5.0, reason="r",
                               state=MomentState.decided, hook_frames_unread=True)
    led.moments["m2"] = Moment(id="m2", parent_id="s1", start=6.0, end=9.0, reason="r",
                               state=MomentState.decided)
    led.save()
    s = _build_summary(cfg, before=set())
    assert s["frames_unread"] == 1


def test_build_summary_malformed_and_naive_times_do_not_raise(tmp_path):
    from fanops.models import Post, PostState, Platform
    from fanops.pipeline import _build_summary
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.published, public_url="https://example.com/a",
                      scheduled_time="not-a-time"))
    led.add_post(Post(id="p2", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="y", state=PostState.published, public_url="https://example.com/b",
                      scheduled_time="2020-01-01T00:00:00"))
    led.save()
    s = _build_summary(cfg, before=set())
    assert s["published"] == 2
    assert s["last_published_age_hours"] is not None


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


def test_run_summary_needs_reconcile_counts_the_whole_column(tmp_path):
    from fanops.models import Post, PostState, Platform
    from fanops.pipeline import _build_summary
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for pid, reason in (("nr1", "timeout after send"), ("nr2", None), ("nr3", "held for an operator decision")):
        led.add_post(Post(id=pid, parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.needs_reconcile, error_reason=reason))
    led.save()
    s = _build_summary(cfg, before=set())
    assert s["needs_reconcile"] == 3
    assert "gave_up" not in s


def test_advance_stamps_produce_error_when_transcript_missing(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    src = cfg.sources / "x.mp4"; _put(src, b"V")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path=str(src), state=SourceState.catalogued,
                              width=1920, height=1080, duration=10.0))
    def fake(cmd, **kw):
        if _is_asr(cmd):
            class R: returncode=1; stderr="boom"; stdout=""
            return R()
        if cmd[0] == "ffprobe":
            class R:
                returncode=0; stderr=""
                stdout = "video" if "codec_type" in " ".join(str(c) for c in cmd) else "1920\n1080\n10.0\n"
            return R()
        class R: returncode=0; stderr=""; stdout=""
        return R()
    for mod in ("transcribe", "signals", "clip", "ingest", "media_probe"):
        mocker.patch(f"fanops.{mod}.subprocess.run", side_effect=fake)
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.error
    assert "no JSON" in (s.error_reason or "")
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    s2 = Ledger.load(cfg).sources["s1"]
    assert s2.state is SourceState.error
    assert "no JSON" in (s2.error_reason or "")
