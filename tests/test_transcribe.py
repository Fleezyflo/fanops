# tests/test_transcribe.py
import json, subprocess
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.transcribe import whisper_cmd, transcribe_source

def test_whisper_cmd_shape():
    cmd = whisper_cmd("/s/x.mp4", "/out", model="small")
    assert cmd[0] == "whisper" and "--output_format" in cmd and "json" in cmd
    assert "--output_dir" in cmd and "small" in cmd

def test_transcribe_parses_segments_language_and_advances(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    def fake_run(cmd, **kw):
        outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
        stem = Path(cmd[-1]).stem
        (outdir / f"{stem}.json").write_text(json.dumps({
            "language": "en",
            "segments": [{"start": 0.0, "end": 3.0, "text": " they slept on me"},
                         {"start": 3.0, "end": 6.5, "text": " not anymore"}]}))
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    led = transcribe_source(led, cfg, "src_1")
    s = led.sources["src_1"]
    assert s.state is SourceState.transcribed and s.language == "en"
    assert s.transcript[0]["text"] == "they slept on me" and s.transcript[1]["end"] == 6.5

def test_empty_speech_is_marked_ran_not_failed(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    def fake_run(cmd, **kw):
        outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps({"language":"en","segments":[]}))
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
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

def test_whisper_absent_goes_to_error_not_crash(tmp_path, mocker):
    # whisper binary off PATH -> subprocess.run raises FileNotFoundError before the process
    # starts (check=False only suppresses a nonzero RETURNCODE). Mirror the no-JSON branch:
    # record SourceState.error gracefully with a clear "toolchain missing: whisper" reason,
    # NOT an uncaught raise that the pipeline reports as an opaque "FileNotFoundError: whisper".
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    def absent(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=absent)
    led = transcribe_source(led, cfg, "src_1")     # must NOT raise
    assert led.sources["src_1"].state is SourceState.error
    assert "toolchain missing: whisper" in (led.sources["src_1"].error_reason or "")

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
    assert seen.get("timeout") == 1800.0                              # the bound is actually wired
