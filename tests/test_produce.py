# tests/test_produce.py — errored source warming via infer_resume_stage
import json
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState, Moment, MomentState
from fanops.produce import _produce_one
from tests.fixtures.speech_segments import LOW_LOGPROB


def test_produce_warms_errored_source_with_warm_transcript(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    path = str(tmp_path / "vid.mp4")
    Path(path).write_bytes(b"V")
    (cfg.agent_io / "transcripts").mkdir(parents=True)
    (cfg.agent_io / "transcripts" / "vid.json").write_text(json.dumps(
        {"language": "en", "segments": [{"start": 0, "end": 1, "text": "warm"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path=path, state=SourceState.error,
                              error_reason="TimeoutExpired: ffmpeg hung"))
    sig_calls = []
    def fake(cmd, **kw):
        joined = " ".join(cmd)
        sig_calls.append(joined)
        if cmd[0] == "ffmpeg" and "null" in cmd:
            class R:
                returncode=0; stdout=""
                stderr = ("silence_end: 16.0 | silence_duration: 1.0" if "silencedetect" in joined
                          else "[scdet @ 0x] lavfi.scd.score: 28.0, lavfi.scd.time: 16.0")
            return R()
        class R: returncode=0; stderr=""; stdout=""
        return R()
    mocker.patch("fanops.signals.subprocess.run", side_effect=fake)
    logs = []
    _produce_one(cfg, "s1", set(), log=lambda *a, **k: logs.append((a, k)))
    assert any("warm_resume" in str(x) for x in logs)
    assert (cfg.agent_io / "signals" / "s1.json").exists()
    assert sig_calls, "signals ffmpeg should run to warm sidecar"


def test_produce_one_returns_error_when_whisper_sets_error(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    path = str(tmp_path / "vid.mp4")
    Path(path).write_bytes(b"V")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path=path, state=SourceState.catalogued))

    def fake(led, cfg, source_id, **kw):
        led.set_source_state(source_id, SourceState.error,
                             error_reason="whisper produced no JSON (rc=1): boom")
        return led
    mocker.patch("fanops.produce.transcribe_source", side_effect=fake)
    res = _produce_one(cfg, "s1", set(), log=lambda *a, **k: None)
    assert res.error_reason and "no JSON" in res.error_reason
    assert Ledger.load(cfg).sources["s1"].state is SourceState.catalogued


def test_produce_one_returns_error_when_json_missing(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    path = str(tmp_path / "vid.mp4")
    Path(path).write_bytes(b"V")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path=path, state=SourceState.catalogued))
    mocker.patch("fanops.produce.transcribe_source", side_effect=lambda led, cfg, source_id, **kw: led)
    res = _produce_one(cfg, "s1", set(), log=lambda *a, **k: None)
    assert res.error_reason == "whisper produced no transcript JSON"


def test_produce_retries_asr_when_hook_windows_lack_speech(tmp_path, mocker, monkeypatch):
    monkeypatch.delenv("FANOPS_ISOLATE_VOCALS", raising=False)
    cfg = Config(root=tmp_path)
    src_path = cfg.sources / "src_1.mp4"
    src_path.parent.mkdir(parents=True, exist_ok=True)
    src_path.write_bytes(b"V")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path=str(src_path),
                              state=SourceState.picks_decided, duration=60.0, language="en",
                              transcript=[{**LOW_LOGPROB, "start": 10.0, "end": 28.0}],
                              meta={"transcribed": True}))
        led.moments["m1"] = Moment(id="m1", parent_id="src_1", state=MomentState.picked,
                                   content_token="14.00-22.00", start=14.0, end=22.0, reason="r")
    calls = []
    def fake(led, cfg, source_id, **kw):
        calls.append(kw)
        return led
    mocker.patch("fanops.produce.transcribe_source", side_effect=fake)
    _produce_one(cfg, "src_1", set(), log=lambda *a, **k: None)
    assert calls and calls[0].get("force") is True
    assert (cfg.agent_io / "transcripts" / "src_1.asr_retry").exists()


def test_produce_source_ids_skips_inventory_and_orders_newest_first(tmp_path):
    from fanops.produce import produce_source_ids
    cfg = Config(root=tmp_path)
    path = str(tmp_path / "vid.mp4")
    Path(path).write_bytes(b"V")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_old_work", source_path=path, state=SourceState.picks_decided,
                              created_at="2026-07-13T00:00:00Z"))
        led.add_source(Source(id="src_new_work", source_path=path, state=SourceState.catalogued,
                              created_at="2026-08-29T00:00:00Z"))
        led.add_source(Source(id="src_library", source_path=path, state=SourceState.moments_decided,
                              created_at="2026-08-30T00:00:00Z"))
        led.add_source(Source(id="src_retired", source_path=path, state=SourceState.retired,
                              created_at="2026-08-31T00:00:00Z"))
        led.add_source(Source(id="src_third", source_path=path, state=SourceState.catalogued,
                              origin_kind="third_party", created_at="2026-09-01T00:00:00Z"))
    ids = produce_source_ids(Ledger.load(cfg))
    assert ids == ["src_new_work", "src_old_work"]


def test_run_all_calls_produce_one_only_for_work_remaining_newest_first(tmp_path, mocker):
    from fanops.produce import run_all, SourceResult
    cfg = Config(root=tmp_path)
    path = str(tmp_path / "vid.mp4")
    Path(path).write_bytes(b"V")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_old", source_path=path, state=SourceState.picks_decided,
                              created_at="2026-07-01T00:00:00Z"))
        led.add_source(Source(id="src_new", source_path=path, state=SourceState.catalogued,
                              created_at="2026-08-01T00:00:00Z"))
        led.add_source(Source(id="src_done", source_path=path, state=SourceState.moments_decided,
                              created_at="2026-08-15T00:00:00Z"))
    seen = []
    mocker.patch("fanops.produce._produce_one",
                 side_effect=lambda cfg, sid, aspects, log=None: seen.append(sid) or SourceResult(sid, None))
    run_all(cfg, set(), log=lambda *a, **k: None)
    assert seen == ["src_new", "src_old"]
