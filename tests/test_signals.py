# tests/test_signals.py
import subprocess
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.errors import ToolchainMissingError
from fanops.signals import parse_silences, parse_scene_changes, detect_signals

SILENCE_STDERR = """
[silencedetect @ 0x] silence_start: 2.5
[silencedetect @ 0x] silence_end: 4.0 | silence_duration: 1.5
[silencedetect @ 0x] silence_start: 9.2
[silencedetect @ 0x] silence_end: 10.0 | silence_duration: 0.8
"""
# Real scdet output form (ffmpeg prints these at -loglevel info):
SCENE_STDERR = """
[scdet @ 0x] lavfi.scd.score: 12.345, lavfi.scd.time: 1.20
[scdet @ 0x] lavfi.scd.score: 28.900, lavfi.scd.time: 6.80
"""

def test_parse_silences():
    s = parse_silences(SILENCE_STDERR)
    assert {round(x["t"], 1) for x in s} == {4.0, 10.0}
    assert all(x["kind"] == "speech_resume" for x in s)

def test_parse_scene_changes_from_scdet():
    sc = parse_scene_changes(SCENE_STDERR)
    assert {round(x["t"], 1) for x in sc} == {1.2, 6.8}
    assert all(x["kind"] == "scene_cut" for x in sc)
    assert any(x["score"] > 20 for x in sc)

def test_detect_signals_merges_advances_and_backfills_duration(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, duration=None,
                          transcript=[{"start": 0, "end": 1, "text": "x"}], meta={"transcribed": True}))
    def fake_run(cmd, **kw):
        joined = " ".join(cmd)
        class R:
            returncode = 0; stdout = ""
            stderr = SILENCE_STDERR if "silencedetect" in joined else SCENE_STDERR
        return R()
    mocker.patch("fanops.signals.subprocess.run", side_effect=fake_run)
    mocker.patch("fanops.signals.probe_dimensions", return_value=(1920, 1080, 12.0))
    led = detect_signals(led, cfg, "src_1")
    s = led.sources["src_1"]
    assert s.state is SourceState.signalled
    kinds = {p["kind"] for p in s.signal_peaks}
    assert "speech_resume" in kinds and "scene_cut" in kinds
    assert s.duration == 12.0                       # backfilled

def test_detect_signals_adopts_sidecar_and_skips_ffmpeg(tmp_path, mocker):
    # Phase D: a lock-free pre-warm pass wrote a deterministic per-source signals sidecar. detect_signals
    # must adopt it and NOT shell ffmpeg — keeping the two signal passes out of the ledger lock.
    import json
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, meta={"transcribed": True}))
    sc = cfg.agent_io / "signals"; sc.mkdir(parents=True, exist_ok=True)
    (sc / "src_1.json").write_text(json.dumps(
        {"peaks": [{"t": 4.0, "kind": "speech_resume", "score": 0.5}], "duration": 12.0}))
    spy = mocker.patch("fanops.signals.subprocess.run")
    led = detect_signals(led, cfg, "src_1")
    spy.assert_not_called()                                  # warm sidecar reused — no ffmpeg
    s = led.sources["src_1"]
    assert s.state is SourceState.signalled and s.duration == 12.0
    assert s.signal_peaks[0]["kind"] == "speech_resume"

def test_detect_signals_writes_sidecar_for_the_commit_pass(tmp_path, mocker):
    # The warm (real) run must PERSIST a sidecar so the in-lock commit pass can skip the ffmpeg passes.
    import json
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, meta={"transcribed": True}))
    def fake_run(cmd, **kw):
        joined = " ".join(cmd)
        class R:
            returncode = 0; stdout = ""
            stderr = SILENCE_STDERR if "silencedetect" in joined else SCENE_STDERR
        return R()
    mocker.patch("fanops.signals.subprocess.run", side_effect=fake_run)
    mocker.patch("fanops.signals.probe_dimensions", return_value=(1920, 1080, 12.0))
    detect_signals(led, cfg, "src_1")
    sidecar = cfg.agent_io / "signals" / "src_1.json"
    assert sidecar.exists(), "expected a written signals sidecar"
    d = json.loads(sidecar.read_text())
    assert d["duration"] == 12.0 and any(p["kind"] == "scene_cut" for p in d["peaks"])

def test_detect_signals_raises_toolchain_error_when_ffmpeg_absent(tmp_path, mocker):
    # ffmpeg off PATH -> subprocess.run raises FileNotFoundError before the process starts
    # (check=False suppresses only a nonzero RETURNCODE). detect_signals runs INSIDE the pipeline's
    # per-source quarantine, so the typed ToolchainMissingError it raises is caught there and the
    # source goes to SourceState.error (see test_pipeline) — the pass never crashes. Here we pin
    # that the helper raises the TYPED error (not a bare FileNotFoundError) so the quarantine
    # records a clear "toolchain missing" reason.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, meta={"transcribed": True}))
    def absent(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])
    mocker.patch("fanops.signals.subprocess.run", side_effect=absent)
    with pytest.raises(ToolchainMissingError, match="ffmpeg"):
        detect_signals(led, cfg, "src_1")

def test_detect_signals_is_time_bounded_and_timeout_propagates(tmp_path, mocker):
    # The ffmpeg signal passes run inside advance()'s ledger transaction — unbounded, a hang on a
    # corrupt source held the flock forever. Each pass must carry a hard timeout=; a hang raises
    # TimeoutExpired, which propagates BY DESIGN to advance()'s per-source quarantine (test_pipeline
    # pins that path) -> SourceState.error "TimeoutExpired: ..." — the same retriable-error
    # treatment as the typed ToolchainMissingError above, and the pass never crashes.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, meta={"transcribed": True}))
    seen = {}
    def hung(cmd, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
    mocker.patch("fanops.signals.subprocess.run", side_effect=hung)
    with pytest.raises(subprocess.TimeoutExpired):
        detect_signals(led, cfg, "src_1")
    assert seen.get("timeout") == 600.0                               # the bound is actually wired
