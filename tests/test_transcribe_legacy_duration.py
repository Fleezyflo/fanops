# tests/test_transcribe_legacy_duration.py — duration-aware model selection knobs, plus the refuse
# when faster-whisper is missing (no whisper-CLI fallback).
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.transcribe import transcribe_source


def test_whisper_model_for_scales_with_duration(monkeypatch):
    monkeypatch.delenv("FANOPS_WHISPER_MODEL", raising=False)
    cfg = Config(root=Path("/tmp/nonexistent-x"))
    assert cfg.whisper_model_for(60.0) == "large-v3"      # short -> accuracy upgrade
    assert cfg.whisper_model_for(3600.0) == "turbo"       # long  -> fast default (under the timeout)
    assert cfg.whisper_model_for(None) == "turbo"         # unknown duration -> fast default


def test_whisper_model_for_prefers_large_v3_whenever_it_fits(monkeypatch):
    # Mirror of asr_model_for: no fixed length gate — the chain starts at large-v3 and the timeout
    # budget alone steps it down (subtitle-garbage incident 2026-07-12).
    monkeypatch.delenv("FANOPS_WHISPER_MODEL", raising=False)
    cfg = Config(root=Path("/tmp/nonexistent-x"))
    assert cfg.whisper_model_for(600.0) == "large-v3"     # fits: 600*2.5 < the 2640s budget
    assert cfg.whisper_model_for(1500.0) == "turbo"       # large-v3 too slow -> turbo still fits


def test_whisper_model_for_pin_wins_verbatim(monkeypatch):
    monkeypatch.setenv("FANOPS_WHISPER_MODEL", "small")   # operator pin is their call, never overridden
    cfg = Config(root=Path("/tmp/nonexistent-x"))
    assert cfg.whisper_model_for(60.0) == "small"
    assert cfg.whisper_model_for(3600.0) == "small"


def test_missing_faster_whisper_does_not_run_whisper_cli(tmp_path, mocker):
    mocker.patch("fanops.transcribe._fw_available", return_value=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="short", source_path=str(cfg.sources / "short.mp4"),
                          state=SourceState.catalogued, duration=60.0))
    spy = mocker.patch("fanops.transcribe.subprocess.run")
    transcribe_source(led, cfg, "short")
    spy.assert_not_called()
    assert led.sources["short"].state is SourceState.error
    assert "[asr]" in (led.sources["short"].error_reason or "")
