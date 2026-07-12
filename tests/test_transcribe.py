# tests/test_transcribe.py
import json, subprocess
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.transcribe import whisper_cmd, fw_cmd, transcribe_source, _segment

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

def test_fwrun_enables_multilingual_for_comma_list(tmp_path, mocker):
    # "en,ar" enables multilingual=True (language=None) — per-segment detection; NOT a candidate pin.
    from fanops import _fwrun
    calls = {}
    class _Info: language = "en"
    class _Fake:
        def transcribe(self, audio, **kw): calls.update(kw); return ([], _Info())
    mocker.patch("fanops._fwrun._load_model", return_value=_Fake())
    (tmp_path / "x.mp3").write_bytes(b"")
    _fwrun.transcribe_to_json(str(tmp_path / "x.mp3"), str(tmp_path), "medium", "en,ar")
    assert calls["multilingual"] is True and calls["language"] is None
    calls.clear()
    _fwrun.transcribe_to_json(str(tmp_path / "x.mp3"), str(tmp_path), "medium", "ar")
    assert calls["multilingual"] is False and calls["language"] == "ar"

def test_transcribe_prefers_faster_whisper_when_available(tmp_path, mocker, monkeypatch):
    # DEFAULT engine: when faster-whisper (the [asr] extra) is importable, transcribe_source runs the
    # fanops._fwrun runner with the pinned FANOPS_ASR_MODEL (here large-v3), NOT the legacy `whisper`
    # CLI. Subprocess is mocked; this proves the SELECTION + the asr_model pin wiring.
    monkeypatch.setenv("FANOPS_ASR_MODEL", "large-v3")
    mocker.patch("fanops.transcribe._fw_available", return_value=True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps({"language": "ar", "segments": []}))
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    led = transcribe_source(led, cfg, "src_1")
    assert captured["cmd"][2] == "fanops._fwrun"                       # ran the faster-whisper runner
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == "large-v3"
    assert led.sources["src_1"].state is SourceState.transcribed

def test_transcribe_selects_fw_model_by_source_duration(tmp_path, mocker, monkeypatch):
    # UNAWARE-CONFIG FIX: with no FANOPS_ASR_MODEL pin, transcribe_source picks the fw model from the
    # SOURCE duration — short -> large-v3 (accuracy), long -> medium (speed/safety under the timeout).
    monkeypatch.delenv("FANOPS_ASR_MODEL", raising=False)
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "0")           # skip demucs; isolate the model-selection wiring
    mocker.patch("fanops.transcribe._fw_available", return_value=True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="short", source_path=str(cfg.sources / "short.mp4"), state=SourceState.catalogued, duration=60.0))
    led.add_source(Source(id="long", source_path=str(cfg.sources / "long.mp4"), state=SourceState.catalogued, duration=3600.0))
    models = []
    def fake_run(cmd, **kw):
        models.append(cmd[cmd.index("--model") + 1])
        outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps({"language": "en", "segments": []}))
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    transcribe_source(led, cfg, "short"); transcribe_source(led, cfg, "long")
    assert models == ["large-v3", "small"]

def test_transcribe_passes_asr_language_to_fw_runner(tmp_path, mocker, monkeypatch):
    # FANOPS_ASR_LANGUAGE -> cfg.asr_language -> fw_cmd --language, threaded through transcribe_source
    # (the env->cmd chain test_fw_cmd_shape can't see). Default "" auto-detects EN+AR; pin "ar" for a
    # single-language account. Proves a refactor can't silently drop the pin.
    monkeypatch.setenv("FANOPS_ASR_LANGUAGE", "ar")
    mocker.patch("fanops.transcribe._fw_available", return_value=True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps({"language": "ar", "segments": []}))
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    transcribe_source(led, cfg, "src_1")
    assert captured["cmd"][captured["cmd"].index("--language") + 1] == "ar"

def test_transcribe_falls_back_to_whisper_cli_when_fw_unavailable(tmp_path, mocker):
    # FAIL-OPEN: no faster-whisper (CI / air-gapped) -> degrade to the legacy `whisper` CLI (turbo),
    # today's behavior, so transcription still works. Never an error just because the extra is absent.
    mocker.patch("fanops.transcribe._fw_available", return_value=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps({"language": "en", "segments": []}))
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    led = transcribe_source(led, cfg, "src_1")
    assert captured["cmd"][0] == "whisper"                             # legacy CLI, not the runner
    assert led.sources["src_1"].state is SourceState.transcribed

def test_transcribe_uses_isolated_vocals_when_enabled(tmp_path, mocker, monkeypatch):
    # With isolation ON, transcribe_source strips the beat first and whisper transcribes the ISOLATED
    # vocals (moved under the source stem), not the raw mix. isolate_vocals is mocked (the demucs run
    # is covered in test_vocals); here we prove the WIRING + that the .json lookup still resolves.
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "1")        # conftest forces 0; opt back in
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    voc = tmp_path / "isolated_vocals.mp3"; voc.write_bytes(b"VOCALS")   # exists so the move succeeds
    iso = mocker.patch("fanops.transcribe.isolate_vocals", return_value=str(voc))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps({
            "language": "ar", "segments": [{"start": 0.0, "end": 2.0, "text": " ورا الستارة"}]}))
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    led = transcribe_source(led, cfg, "src_1")
    iso.assert_called_once()                                # isolation ran
    assert captured["cmd"][-1].endswith("src_1.mp3")       # whisper transcribed the ISOLATED mp3 (source stem)
    s = led.sources["src_1"]
    assert s.state is SourceState.transcribed and s.transcript[0]["text"] == "ورا الستارة"

def test_transcribe_failopen_to_source_stem_when_vocal_move_fails(tmp_path, mocker, monkeypatch):
    # ECC-review fix #3: when the isolated-vocals move raises OSError (e.g. cross-device), the OLD
    # fallback used the vocals path (stem "vocals") so whisper wrote vocals.json — the per-source
    # cache lookup ({source_stem}.json) then MISSED forever, re-transcribing every run and clobbering
    # the shared vocals.json. The fallback must keep the SOURCE stem so the JSON name is deterministic.
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    voc = tmp_path / "isolated_vocals.mp3"; voc.write_bytes(b"VOCALS")
    mocker.patch("fanops.transcribe.isolate_vocals", return_value=str(voc))
    mocker.patch("pathlib.Path.replace", side_effect=OSError("cross-device link"))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps({"language": "en", "segments": []}))
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    led = transcribe_source(led, cfg, "src_1")
    stem = Path(captured["cmd"][-1]).stem
    assert stem == "src_1", f"move-failure fallback used stem {stem!r}; must stay the SOURCE stem for a stable cache"
    assert led.sources["src_1"].state is SourceState.transcribed

def test_transcribe_failopen_to_raw_when_isolation_unavailable(tmp_path, mocker, monkeypatch):
    # isolation ON but demucs absent -> isolate_vocals returns the RAW path -> whisper transcribes the
    # original source (today's behavior). Never blocks.
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    mocker.patch("fanops.transcribe.isolate_vocals", side_effect=lambda p, o, **k: p)   # fail-open: raw
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps({"language": "en", "segments": []}))
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    led = transcribe_source(led, cfg, "src_1")
    assert captured["cmd"][-1].endswith("src_1.mp4")       # transcribed the RAW source, not a vocals file
    assert led.sources["src_1"].state is SourceState.transcribed

def test_transcribe_captures_word_timestamps_when_present(tmp_path, mocker):
    # whisper --word_timestamps adds a per-segment `words` list ([{word,start,end}]); capture it so
    # the overlay can sync active captions word-by-word. Absent -> the field is simply omitted.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    def fake_run(cmd, **kw):
        outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps({
            "language": "en",
            "segments": [{"start": 0.0, "end": 2.0, "text": " hi there",
                          "words": [{"word": " hi", "start": 0.0, "end": 0.5},
                                    {"word": " there", "start": 0.5, "end": 1.2}]}]}))
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    led = transcribe_source(led, cfg, "src_1")
    seg = led.sources["src_1"].transcript[0]
    assert seg["words"][0]["word"] == " hi" and seg["words"][1]["end"] == 1.2

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
    mocker.patch("fanops.transcribe._fw_available", return_value=False)   # legacy `whisper` CLI path
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
    (out_dir / "src_1.json").write_text(json.dumps(
        {"language": "en", "segments": [{"start": 0.0, "end": 2.0, "text": " cached line"}]}))
    spy = mocker.patch("fanops.transcribe.subprocess.run")
    led = transcribe_source(led, cfg, "src_1")
    spy.assert_not_called()                                   # warm artifact reused — no whisper, no isolation
    s = led.sources["src_1"]
    assert s.state is SourceState.transcribed and s.language == "en"
    assert s.transcript[0]["text"] == "cached line" and s.meta.get("transcribed") is True

def test_transcribe_reruns_when_cached_json_is_corrupt(tmp_path, mocker):
    # Conservative skip: a truncated/corrupt cached JSON must NOT be adopted — fall through to a real
    # run (which overwrites it), never silently produce an empty/garbage transcript.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued))
    out_dir = cfg.agent_io / "transcripts"; out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "src_1.json").write_text('{"language": "en", "segme')        # truncated
    def fake_run(cmd, **kw):
        outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps(
            {"language": "en", "segments": [{"start": 0.0, "end": 1.0, "text": " real"}]}))
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
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
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    led = transcribe_source(led, cfg, "src_1")     # must NOT raise
    s = led.sources["src_1"]
    assert s.state is SourceState.error
    assert "whisper JSON malformed" in (s.error_reason or "")
    assert s.meta.get("transcribed") is not True   # a re-run actually retries
