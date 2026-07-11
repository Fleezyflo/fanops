# tests/test_artifacts.py — advisory manifest stamp/infer/adopt + purge coverage
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops import artifacts
from fanops.transcribe import purge_source_artifacts


def _cfg(tmp_path):
    return Config(root=tmp_path)


def test_stamp_stage_writes_manifest_atomically(tmp_path):
    cfg = _cfg(tmp_path)
    artifacts.stamp_stage(cfg, "src_1", "transcribe", artifact="transcripts/vid.json", schema=1, sha256="abc")
    p = artifacts.manifest_path(cfg, "src_1")
    assert p.exists()
    d = json.loads(p.read_text())
    assert d["v"] == 1 and d["source_id"] == "src_1" and d["sha256"] == "abc"
    assert d["stages"]["transcribe"]["artifact"] == "transcripts/vid.json"
    assert d["stages"]["transcribe"]["schema"] == 1
    assert "at" in d["stages"]["transcribe"]


def test_infer_resume_stage_from_warm_transcript(tmp_path):
    cfg = _cfg(tmp_path)
    out = cfg.agent_io / "transcripts"
    out.mkdir(parents=True)
    (out / "vid.json").write_text(json.dumps({"language": "en", "segments": [{"start": 0, "end": 1, "text": "hi"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path=str(cfg.sources / "vid.mp4"), state=SourceState.error,
                              error_reason="TimeoutExpired: whisper hung"))
    assert artifacts.infer_resume_stage(cfg, Ledger.load(cfg), "src_1") == "transcribed"


def test_infer_resume_stage_signalled_when_signals_sidecar_warm(tmp_path):
    cfg = _cfg(tmp_path)
    stem = "vid"
    (cfg.agent_io / "transcripts").mkdir(parents=True)
    (cfg.agent_io / "transcripts" / f"{stem}.json").write_text(json.dumps(
        {"language": "en", "segments": [{"start": 0, "end": 1, "text": "hi"}]}))
    (cfg.agent_io / "signals").mkdir(parents=True)
    (cfg.agent_io / "signals" / "src_1.json").write_text(json.dumps({"v": 3, "peaks": [{"t": 1.0}], "duration": 10.0}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path=str(cfg.sources / "vid.mp4"), state=SourceState.error,
                              error_reason="TimeoutExpired: ffmpeg hung"))
    assert artifacts.infer_resume_stage(cfg, Ledger.load(cfg), "src_1") == "signalled"


def test_adopt_warm_artifacts_loads_transcript_json(tmp_path):
    cfg = _cfg(tmp_path)
    path = str(cfg.sources / "vid.mp4")
    (cfg.agent_io / "transcripts").mkdir(parents=True)
    (cfg.agent_io / "transcripts" / "vid.json").write_text(json.dumps(
        {"language": "en", "segments": [{"start": 0, "end": 1, "text": "warm"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path=path, state=SourceState.error, transcript=None,
                              meta={"transcribed": False}, error_reason="TimeoutExpired: x"))
        led = artifacts.adopt_warm_artifacts(led, cfg, "src_1")
    s = led.sources["src_1"]
    assert s.transcript == [{"start": 0, "end": 1, "text": "warm"}]
    assert s.meta["transcribed"] is True


def test_artifact_summary_joins_stages(tmp_path):
    cfg = _cfg(tmp_path)
    artifacts.stamp_stage(cfg, "src_1", "transcribe", artifact="transcripts/v.json", schema=1)
    artifacts.stamp_stage(cfg, "src_1", "signals", artifact="signals/src_1.json", schema=3)
    assert artifacts.artifact_summary(cfg, "src_1") == "transcribe+signals"


def test_purge_covers_manifest_framing_keyframes(tmp_path):
    cfg = _cfg(tmp_path)
    sid, path = "src_1", str(cfg.sources / "vid.mp4")
    (cfg.agent_io / "manifests").mkdir(parents=True)
    (cfg.agent_io / "manifests" / f"{sid}.json").write_text("{}")
    (cfg.agent_io / "framing").mkdir(parents=True)
    (cfg.agent_io / "framing" / f"{sid}.detect.json").write_text("{}")
    (cfg.agent_io / "keyframes" / sid).mkdir(parents=True)
    (cfg.agent_io / "keyframes" / sid / "abc").mkdir()
    cfg.clips.mkdir(parents=True, exist_ok=True)
    (cfg.clips / "clip_x.render.json").write_text("{}")
    purge_source_artifacts(cfg, sid, path, clip_ids=["clip_x"])
    assert not (cfg.agent_io / "manifests" / f"{sid}.json").exists()
    assert not (cfg.agent_io / "framing" / f"{sid}.detect.json").exists()
    assert not (cfg.agent_io / "keyframes" / sid).exists()
    assert not (cfg.clips / "clip_x.render.json").exists()
