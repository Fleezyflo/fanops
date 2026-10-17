# tests/test_transcribe.py
import json, subprocess
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.transcribe import (whisper_cmd, fw_cmd, transcribe_source, _adopt_cached_transcript,
                               _finalize_segments, _segment, adopt_transcript_keep_state)
from tests.fixtures.speech_segments import LEGACY_EN, talk_seg


def _ok():
    class R: returncode = 0; stderr = ""; stdout = ""
    return R()


def _write_fw_json(cmd, payload):
    outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps(payload))


def _is_demucs(cmd):
    return isinstance(cmd, (list, tuple)) and len(cmd) >= 3 and cmd[1] == "-m" and cmd[2] == "demucs"


def _write_demucs_vocals(cmd):
    out = Path(cmd[cmd.index("-o") + 1])
    d = out / "htdemucs" / Path(cmd[-1]).stem
    d.mkdir(parents=True, exist_ok=True)
    (d / "vocals.mp3").write_bytes(b"VOCALS")


def test_segment_passes_through_quality_metadata():
    raw = {"start": 0.0, "end": 2.0, "text": " hi", "avg_logprob": -0.5, "no_speech_prob": 0.1, "compression_ratio": 1.4}
    seg = _segment(raw)
    assert seg["text"] == "hi" and seg["avg_logprob"] == -0.5
    assert seg["no_speech_prob"] == 0.1 and seg["compression_ratio"] == 1.4

def test_whisper_cmd_shape():
    cmd = whisper_cmd("/s/x.mp4", "/out", model="small")
    assert cmd[0] == "whisper" and "--output_format" in cmd and "json" in cmd
    assert "--output_dir" in cmd and "small" in cmd
    # word-level timestamps drive the active-caption sync — request them from whisper
    assert "--word_timestamps" in cmd and cmd[cmd.index("--word_timestamps") + 1] == "True"

def test_whisper_cmd_language_passthrough():
    # Single asr_language value -> legacy whisper --language; comma-list -> omit (multilingual auto-detect).
    cmd = whisper_cmd("/s/x.mp4", "/out", model="turbo", language="ar")
    assert "--language" in cmd and cmd[cmd.index("--language") + 1] == "ar"
    cmd = whisper_cmd("/s/x.mp4", "/out", model="turbo", language="en,ar")
    assert "--language" not in cmd

def test_fw_cmd_shape():
    # The faster-whisper runner invocation: `python -m fanops._fwrun --model <m> --language <l>
    # --output_dir <out> <audio>`. Same --output_dir flag + audio-LAST shape as whisper_cmd, so the
    # .json lookup and the engine-agnostic transcribe tests don't care which engine ran.
    import sys
    cmd = fw_cmd("/s/x.mp3", "/out", "large-v3", "")
    assert cmd[0] == sys.executable and cmd[1] == "-m" and cmd[2] == "fanops._fwrun"
    assert cmd[cmd.index("--model") + 1] == "large-v3"
    assert cmd[cmd.index("--language") + 1] == ""            # "" -> runner auto-detects (EN+AR)
    assert cmd[cmd.index("--output_dir") + 1] == "/out" and cmd[-1] == "/s/x.mp3"

def test_transcribe_prefers_faster_whisper_when_available(tmp_path, mocker, monkeypatch):
    # transcribe_source shells fanops._fwrun with the FANOPS_ASR_MODEL pin, never the legacy `whisper` CLI.
    monkeypatch.setenv("FANOPS_ASR_MODEL", "large-v3")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        _write_fw_json(cmd, {"language": "ar", "segments": []})
        return _ok()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    led = transcribe_source(led, cfg, "src_1")
    assert captured["cmd"][0] != "whisper"
    assert captured["cmd"][2] == "fanops._fwrun"                       # ran the faster-whisper runner
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == "large-v3"
    assert led.sources["src_1"].state is SourceState.transcribed

def test_transcribe_selects_fw_model_by_source_duration(tmp_path, mocker, monkeypatch):
    # With no explicit model=, duration-aware asr_model_for picks large-v3 for short sources and steps
    # down for long sources that would blow the whisper timeout budget.
    monkeypatch.delenv("FANOPS_ASR_MODEL", raising=False)
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "0")           # skip demucs; isolate the model-selection wiring
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="short", source_path=str(cfg.sources / "short.mp4"), state=SourceState.catalogued, duration=60.0))
    led.add_source(Source(id="long", source_path=str(cfg.sources / "long.mp4"), state=SourceState.catalogued, duration=3600.0))
    models = []
    def fake_run(cmd, **kw):
        models.append(cmd[cmd.index("--model") + 1])
        _write_fw_json(cmd, {"language": "en", "segments": []})
        return _ok()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    transcribe_source(led, cfg, "short"); transcribe_source(led, cfg, "long")
    assert models == ["large-v3", "small"]

def test_transcribe_passes_asr_language_to_fw_runner(tmp_path, mocker, monkeypatch):
    # FANOPS_ASR_LANGUAGE -> cfg.asr_language -> fw_cmd --language, threaded through transcribe_source
    # (the env->cmd chain test_fw_cmd_shape can't see). Pin "ar" for a single-language account.
    monkeypatch.setenv("FANOPS_ASR_LANGUAGE", "ar")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        _write_fw_json(cmd, {"language": "ar", "segments": []})
        return _ok()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    transcribe_source(led, cfg, "src_1")
    assert captured["cmd"][captured["cmd"].index("--language") + 1] == "ar"

def test_transcribe_passes_default_asr_language_to_fw_runner(tmp_path, mocker, monkeypatch):
    # Default cfg.asr_language is "en,ar" (comma-list). The runner, not transcribe_source, interprets
    # that as multilingual=True; this pin is the env->argv contract.
    monkeypatch.delenv("FANOPS_ASR_LANGUAGE", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        _write_fw_json(cmd, {"language": "en", "segments": []})
        return _ok()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    transcribe_source(led, cfg, "src_1")
    assert captured["cmd"][2] == "fanops._fwrun"
    assert captured["cmd"][captured["cmd"].index("--language") + 1] == "en,ar"

def test_transcribe_uses_isolated_vocals_when_enabled(tmp_path, mocker, monkeypatch):
    # Isolation ON: real isolate_vocals (demucs at the subprocess edge) then whisper transcribes the
    # moved source-stem mp3, not the raw mix. .json lookup still resolves.
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "1")        # conftest forces 0; opt back in
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    captured = {}
    def fake_run(cmd, **kw):
        if _is_demucs(cmd):
            _write_demucs_vocals(cmd)
            return _ok()
        captured["cmd"] = cmd
        _write_fw_json(cmd, {"language": "ar", "segments": [{"start": 0.0, "end": 2.0, "text": " ورا الستارة"}]})
        return _ok()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    led = transcribe_source(led, cfg, "src_1")
    assert captured["cmd"][2] == "fanops._fwrun"
    assert captured["cmd"][-1].endswith("src_1.mp3")       # whisper transcribed the ISOLATED mp3 (source stem)
    s = led.sources["src_1"]
    assert s.state is SourceState.transcribed and s.transcript[0]["text"] == "ورا الستارة"
    assert s.meta.get("vocals_isolated") is True

def test_transcribe_errors_when_isolation_unavailable(tmp_path, mocker, monkeypatch):
    # isolation ON but demucs unspawnable -> isolate_vocals raises ToolchainMissingError -> source
    # errors; whisper must NOT decode the mix.
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        if _is_demucs(cmd):
            raise FileNotFoundError(2, "No such file", "demucs")
        return _ok()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    led = transcribe_source(led, cfg, "src_1")
    assert all(c[2] != "fanops._fwrun" for c in calls if len(c) > 2)
    s = led.sources["src_1"]
    assert s.state is SourceState.error
    assert "vocals isolation failed:" in (s.error_reason or "")

def test_transcribe_captures_word_timestamps_when_present(tmp_path, mocker):
    # whisper --word_timestamps adds a per-segment `words` list ([{word,start,end}]); capture it so
    # the overlay can sync active captions word-by-word. Absent -> the field is simply omitted.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    def fake_run(cmd, **kw):
        _write_fw_json(cmd, {
            "language": "en",
            "segments": [{"start": 0.0, "end": 2.0, "text": " hi there",
                          "words": [{"word": " hi", "start": 0.0, "end": 0.5},
                                    {"word": " there", "start": 0.5, "end": 1.2}]}]})
        return _ok()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    led = transcribe_source(led, cfg, "src_1")
    seg = led.sources["src_1"].transcript[0]
    assert seg["words"][0]["word"] == " hi" and seg["words"][1]["end"] == 1.2

def test_transcribe_parses_segments_language_and_advances(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    def fake_run(cmd, **kw):
        _write_fw_json(cmd, {
            "language": "en",
            "segments": [{"start": 0.0, "end": 3.0, "text": " they slept on me"},
                         {"start": 3.0, "end": 6.5, "text": " not anymore"}]})
        return _ok()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    led = transcribe_source(led, cfg, "src_1")
    s = led.sources["src_1"]
    assert s.state is SourceState.transcribed and s.language == "en"
    assert s.transcript[0]["text"] == "they slept on me" and s.transcript[1]["end"] == 6.5
    js = cfg.agent_io / "transcripts" / "src_1.json"
    assert js.exists() and json.loads(js.read_text())["language"] == "en"

def test_empty_speech_is_marked_ran_not_failed(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    def fake_run(cmd, **kw):
        _write_fw_json(cmd, {"language":"en","segments":[]})
        return _ok()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    led = transcribe_source(led, cfg, "src_1")
    s = led.sources["src_1"]
    assert s.transcript == [] and s.state is SourceState.transcribed
    assert s.meta.get("transcribed") is True       # ran, just no speech

def test_missing_json_goes_to_error_not_crash(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    class R: returncode = 1; stderr = "boom"; stdout = ""
    mocker.patch("fanops.transcribe.subprocess.run", return_value=R())
    led = transcribe_source(led, cfg, "src_1")     # no json written
    assert led.sources["src_1"].state is SourceState.error
    assert "boom" in (led.sources["src_1"].error_reason or "")
    assert led.sources["src_1"].meta.get("preserve_vocals_on_retry") is True  # MOL-814: whisper-only

def test_fwrun_absent_goes_to_error_not_crash(tmp_path, mocker):
    # runner unspawnable -> subprocess.run raises FileNotFoundError before the process starts.
    # Record SourceState.error, never an uncaught raise.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    def absent(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=absent)
    led = transcribe_source(led, cfg, "src_1")     # must NOT raise
    assert led.sources["src_1"].state is SourceState.error
    assert "toolchain missing:" in (led.sources["src_1"].error_reason or "")
    assert led.sources["src_1"].meta.get("preserve_vocals_on_retry") is False  # MOL-814: not whisper-only

def test_transcribe_idempotent_when_already_done(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, transcript=[], meta={"transcribed": True}))
    spy = mocker.patch("fanops.transcribe.subprocess.run")
    led = transcribe_source(led, cfg, "src_1")
    spy.assert_not_called()

def test_whisper_hang_goes_to_error_not_crash(tmp_path, mocker):
    # THE flock-critical bound: transcribe_source runs INSIDE Ledger.transaction (pipeline.py),
    # so an unbounded hung whisper held the ledger lock forever — blocking every cron pass,
    # Studio write and recovery verb until the OS intervened. The run must carry a hard timeout=,
    # and TimeoutExpired must mirror the absent/no-JSON branches: SourceState.error with a clear
    # reason, `transcribed` left unset (re-runnable), never a raise.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    seen = {}
    def hung(cmd, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=hung)
    led = transcribe_source(led, cfg, "src_1")     # must NOT raise
    assert led.sources["src_1"].state is SourceState.error
    assert "timed out" in (led.sources["src_1"].error_reason or "")
    assert led.sources["src_1"].meta.get("transcribed") is not True   # a re-run actually retries
    assert led.sources["src_1"].meta.get("preserve_vocals_on_retry") is True  # MOL-814 / MOL-482
    from fanops.transcribe import _WHISPER_TIMEOUT
    assert seen.get("timeout") == _WHISPER_TIMEOUT                    # the bound is actually wired (2700s)

def test_transcribe_adopts_existing_json_and_skips_subprocess(tmp_path, mocker):
    # Phase D: a lock-free pre-warm pass already ran whisper to its DETERMINISTIC per-stem JSON.
    # transcribe_source must ADOPT that artifact and NOT shell whisper again — this is what keeps the
    # multi-minute subprocess OUT of the ledger lock. Whisper output is deterministic per source, so
    # reusing the JSON is equivalent to re-running it.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    out_dir = cfg.agent_io / "transcripts"; out_dir.mkdir(parents=True, exist_ok=True)
    # Quality-complete without no_speech_prob: avg_logprob + compression_ratio suffice to adopt.
    cached = {k: v for k, v in talk_seg("cached line").items() if k != "no_speech_prob"}
    (out_dir / "src_1.json").write_text(json.dumps(
        {"language": "en", "segments": [cached]}))
    spy = mocker.patch("fanops.transcribe.subprocess.run")
    led = transcribe_source(led, cfg, "src_1")
    spy.assert_not_called()                                   # warm artifact reused — no whisper, no isolation
    s = led.sources["src_1"]
    assert s.state is SourceState.transcribed and s.language == "en"
    assert s.transcript[0]["text"] == "cached line" and s.meta.get("transcribed") is True
    assert s.transcript[0]["trust_tier"] == "full" and s.transcript[0]["trusted"] is True

def test_stale_cache_without_metadata_not_adopted(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    out_dir = cfg.agent_io / "transcripts"; out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "src_1.json").write_text(json.dumps({"language": "en", "segments": [LEGACY_EN]}))
    assert _adopt_cached_transcript(led, "src_1", out_dir / "src_1.json", cfg=cfg) is False
    spy = mocker.patch("fanops.transcribe.subprocess.run")
    def fake_run(cmd, **kw):
        _write_fw_json(cmd, {"language": "en", "segments": [talk_seg("fresh line")]})
        return _ok()
    spy.side_effect = fake_run
    led = transcribe_source(led, cfg, "src_1")
    spy.assert_called_once()
    assert led.sources["src_1"].transcript[0]["text"] == "fresh line"

def test_finalize_segments_stamps_tier_fields():
    segs = _finalize_segments([talk_seg("hello world")], "en")
    assert segs[0]["trust_tier"] == "full" and segs[0]["trusted"] is True
    empty = _finalize_segments([{"start": 0.0, "end": 1.0, "text": "  "}], "en")
    assert empty[0]["trust_tier"] == "rejected" and empty[0]["trusted"] is False

def test_transcribe_reruns_when_cached_json_is_corrupt(tmp_path, mocker):
    # Conservative skip: a truncated/corrupt cached JSON must NOT be adopted — fall through to a real
    # run (which overwrites it), never silently produce an empty/garbage transcript.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    out_dir = cfg.agent_io / "transcripts"; out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "src_1.json").write_text('{"language": "en", "segme')        # truncated
    def fake_run(cmd, **kw):
        _write_fw_json(cmd, {"language": "en", "segments": [{"start": 0.0, "end": 1.0, "text": " real"}]})
        return _ok()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    led = transcribe_source(led, cfg, "src_1")               # must re-run, not adopt the corrupt cache
    assert led.sources["src_1"].state is SourceState.transcribed
    assert led.sources["src_1"].transcript[0]["text"] == "real"

def test_malformed_whisper_json_is_per_source_error_not_crash(tmp_path, mocker):
    # Stage-6 audit: whisper killed mid-write (disk full, OOM kill) leaves TRUNCATED JSON on disk.
    # That must park THIS source as a retriable error whose reason points at whisper — exactly like
    # the sibling absent/timeout/no-JSON branches; the parse was the one unguarded step (a bare
    # JSONDecodeError said nothing about whisper or which file).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    def fake_run(cmd, **kw):
        outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{Path(cmd[-1]).stem}.json").write_text('{"language": "en", "segme')   # truncated
        return _ok()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    led = transcribe_source(led, cfg, "src_1")     # must NOT raise
    s = led.sources["src_1"]
    assert s.state is SourceState.error
    assert "whisper JSON malformed" in (s.error_reason or "")
    assert s.meta.get("transcribed") is not True   # a re-run actually retries
    assert s.meta.get("preserve_vocals_on_retry") is False  # MOL-814: deliberate asymmetry vs timeout/no-JSON


def test_adopt_keep_state_does_not_rewind_picks_decided(tmp_path):
    cfg = Config(root=tmp_path)
    path = str(tmp_path / "vid.mp4")
    js_dir = cfg.agent_io / "transcripts"
    js_dir.mkdir(parents=True)
    (js_dir / "vid.json").write_text(json.dumps({
        "language": "en",
        "segments": [talk_seg("new lyric line", start=0.0, end=2.0)],
    }))
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path=path, state=SourceState.picks_decided,
                          duration=10.0, language="en",
                          transcript=[talk_seg("old", start=0.0, end=2.0)],
                          meta={"transcribed": True}))
    assert adopt_transcript_keep_state(led, cfg, "s1") is True
    assert led.sources["s1"].state is SourceState.picks_decided
    assert "new lyric" in led.sources["s1"].transcript[0]["text"]


def test_transcribe_source_force_bypasses_idempotent_cache(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    path = str(tmp_path / "vid.mp4")
    Path(path).write_bytes(b"V")
    (cfg.agent_io / "transcripts").mkdir(parents=True)
    (cfg.agent_io / "transcripts" / "vid.json").write_text(json.dumps({
        "language": "en", "segments": [talk_seg("cached", start=0.0, end=1.0)],
    }))
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path=path, state=SourceState.picks_decided,
                          duration=10.0, meta={"transcribed": True}))
    runs = []
    def fake_run(cmd, **kw):
        runs.append(cmd)
        _write_fw_json(cmd, {"language": "en", "segments": [talk_seg("fresh", start=0.0, end=1.0)]})
        return _ok()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    transcribe_source(led, cfg, "s1")
    assert runs == []                                    # transcribed=True, no force — no ASR
    transcribe_source(led, cfg, "s1", force=True)
    assert runs and runs[0][2] == "fanops._fwrun"        # force re-runs isolate+ASR
    assert led.sources["s1"].transcript[0]["text"] == "fresh"
    assert led.sources["s1"].state is SourceState.transcribed
