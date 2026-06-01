# tests/test_signals.py
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
