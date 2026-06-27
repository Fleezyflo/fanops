# tests/test_transcribe_legacy_duration.py — WS6 (audit c0-f2 transcribe): the duration-aware ASR selection
# (short source -> large-v3 for accuracy, long/unknown -> the fast default to land under the timeout) was wired
# ONLY into the faster-whisper path (cfg.asr_model_for). The legacy `whisper` CLI fallback — the path CI and
# air-gapped hosts actually take when the [asr] extra is absent — used a FIXED cfg.whisper_model, so on those
# hosts a 10-second source and a 40-minute source got the identical model: short clips lost the accuracy upgrade,
# and a long source on a too-heavy pin risked the timeout. The fix mirrors asr_model_for with whisper_model_for.
import json
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


def test_whisper_model_for_pin_wins_verbatim(monkeypatch):
    monkeypatch.setenv("FANOPS_WHISPER_MODEL", "small")   # operator pin is their call, never overridden
    cfg = Config(root=Path("/tmp/nonexistent-x"))
    assert cfg.whisper_model_for(60.0) == "small"
    assert cfg.whisper_model_for(3600.0) == "small"


def test_legacy_whisper_fallback_is_duration_aware(tmp_path, mocker, monkeypatch):
    # The WIRE: with the [asr] extra absent and no pin, transcribe_source must pick the legacy whisper model
    # from the SOURCE duration. _resolve_model is mocked to identity so we isolate the duration choice from the
    # offline cache-remap (which test_transcribe's fallback test deliberately doesn't assert a model name past).
    monkeypatch.delenv("FANOPS_WHISPER_MODEL", raising=False)
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "0")
    mocker.patch("fanops.transcribe._fw_available", return_value=False)
    mocker.patch("fanops.transcribe._resolve_model", side_effect=lambda m: m)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="short", source_path=str(cfg.sources / "short.mp4"), state=SourceState.catalogued, duration=60.0))
    led.add_source(Source(id="long", source_path=str(cfg.sources / "long.mp4"), state=SourceState.catalogued, duration=3600.0))
    models = []
    def fake_run(cmd, **kw):
        assert cmd[0] == "whisper"                         # legacy CLI, not the runner
        models.append(cmd[cmd.index("--model") + 1])
        outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps({"language": "en", "segments": []}))
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    transcribe_source(led, cfg, "short"); transcribe_source(led, cfg, "long")
    assert models == ["large-v3", "turbo"]
