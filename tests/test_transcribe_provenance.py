# tests/test_transcribe_provenance.py — transcript provenance (subtitle-garbage incident 2026-07-12):
# the ledger/manifest recorded WHAT was transcribed but never WHICH engine+model produced it, so a
# bad-model incident was only diagnosable by inferring the engine from JSON shape and the model from
# env archaeology. _produce_transcript now stamps engine/model/wall_s into the advisory manifest and
# emits one "transcribed" log line carrying the same fields plus duration (wall_s vs duration is the
# measured per-host RTF — the calibration data config._ASR_MODEL_RTF needs).
import json
from pathlib import Path
from fanops.artifacts import manifest_path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.transcribe import transcribe_source


def test_produce_transcript_stamps_engine_model_and_wall_time(tmp_path, mocker, monkeypatch):
    monkeypatch.delenv("FANOPS_WHISPER_MODEL", raising=False)
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "0")
    mocker.patch("fanops.transcribe._fw_available", return_value=False)   # legacy CLI path
    mocker.patch("fanops.transcribe._resolve_model", side_effect=lambda m: m)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path=str(cfg.sources / "vid.mp4"),
                          state=SourceState.catalogued, duration=60.0, sha256="abc"))
    def fake_run(cmd, **kw):
        outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{Path(cmd[-1]).stem}.json").write_text(
            json.dumps({"language": "en", "segments": [{"start": 0.0, "end": 1.0, "text": "hi"}]}))
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    transcribe_source(led, cfg, "s1")
    rec = json.loads(manifest_path(cfg, "s1").read_text())["stages"]["transcribe"]
    assert rec["engine"] == "whisper-cli" and rec["model"] == "large-v3"   # 60s source -> accuracy pick
    assert isinstance(rec["wall_s"], (int, float)) and rec["wall_s"] >= 0
    lines = [json.loads(x) for x in cfg.log_path.read_text().splitlines() if x.strip()]
    line = next(r for r in lines if r.get("outcome") == "transcribed")
    assert line["engine"] == "whisper-cli" and line["model"] == "large-v3"
    assert line["unit_id"] == "s1" and line["duration"] == "60.0" and "wall_s" in line
