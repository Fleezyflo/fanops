# tests/test_artifacts.py — Pipeline Artifact Resume: manifest stamp/infer/adopt/purge
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.artifacts import (stamp_stage, infer_resume_stage, adopt_warm_artifacts,
                               purge_all_source_artifacts, artifact_summary)


def _src(cfg, *, sid="s1", path="/clip.mp4", state=SourceState.catalogued, **kw):
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id=sid, source_path=path, state=state, **kw))


def test_stamp_stage_writes_manifest(tmp_path):
    cfg = Config(root=tmp_path)
    _src(cfg)
    apath = cfg.agent_io / "transcripts" / "clip.json"
    stamp_stage(cfg, "s1", "transcribe", apath, 1)
    mf = cfg.agent_io / "manifests" / "s1.json"
    assert mf.exists()
    d = json.loads(mf.read_text())
    assert d["v"] == 1 and d["source_id"] == "s1"
    assert d["stages"]["transcribe"]["artifact"].endswith("04_agent_io/transcripts/clip.json")
    assert d["stages"]["transcribe"]["schema"] == 1
    assert "at" in d["stages"]["transcribe"]


def test_stamp_stage_atomic_merge(tmp_path):
    cfg = Config(root=tmp_path)
    _src(cfg)
    stamp_stage(cfg, "s1", "transcribe", "a.json", 1)
    stamp_stage(cfg, "s1", "signals", "b.json", 3)
    d = json.loads((cfg.agent_io / "manifests" / "s1.json").read_text())
    assert set(d["stages"]) == {"transcribe", "signals"}


def test_infer_resume_stage_transcript_only(tmp_path):
    cfg = Config(root=tmp_path)
    _src(cfg)
    out = cfg.agent_io / "transcripts"
    out.mkdir(parents=True, exist_ok=True)
    (out / "clip.json").write_text(json.dumps({"segments": [{"start": 0, "end": 1, "text": "hi"}], "language": "en"}))
    s = Ledger.load(cfg).sources["s1"]
    assert infer_resume_stage(cfg, "s1", s) is SourceState.transcribed


def test_infer_resume_stage_signals_when_both_exist(tmp_path):
    cfg = Config(root=tmp_path)
    _src(cfg)
    (cfg.agent_io / "transcripts").mkdir(parents=True, exist_ok=True)
    (cfg.agent_io / "transcripts" / "clip.json").write_text(json.dumps(
        {"segments": [{"start": 0, "end": 1, "text": "hi"}], "language": "en"}))
    (cfg.agent_io / "signals").mkdir(parents=True, exist_ok=True)
    (cfg.agent_io / "signals" / "s1.json").write_text(json.dumps({"v": 3, "peaks": [{"t": 1.0, "kind": "scene_cut", "score": 0.5}], "duration": 10.0}))
    s = Ledger.load(cfg).sources["s1"]
    assert infer_resume_stage(cfg, "s1", s) is SourceState.signalled


def test_infer_resume_stage_missing_file_returns_none(tmp_path):
    cfg = Config(root=tmp_path)
    _src(cfg)
    mf = cfg.agent_io / "manifests" / "s1.json"
    mf.parent.mkdir(parents=True, exist_ok=True)
    mf.write_text(json.dumps({"v": 1, "source_id": "s1", "stages": {"transcribe": {"artifact": "gone.json", "schema": 1, "at": "x"}}}))
    s = Ledger.load(cfg).sources["s1"]
    assert infer_resume_stage(cfg, "s1", s) is None


def test_adopt_warm_artifacts_loads_transcript(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/clip.mp4", state=SourceState.error,
                              transcript=None, meta={"transcribed": False}))
    (cfg.agent_io / "transcripts").mkdir(parents=True, exist_ok=True)
    (cfg.agent_io / "transcripts" / "clip.json").write_text(json.dumps(
        {"segments": [{"start": 0, "end": 1, "text": "warm"}], "language": "en"}))
    with Ledger.transaction(cfg) as led:
        led = adopt_warm_artifacts(led, cfg, "s1")
    s = led.sources["s1"]
    assert s.transcript == [{"start": 0, "end": 1, "text": "warm"}]
    assert s.meta["transcribed"] is True


def test_adopt_warm_artifacts_loads_signals(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/clip.mp4", state=SourceState.error,
                              transcript=[{"start": 0, "end": 1, "text": "x"}],
                              meta={"transcribed": True}, signal_peaks=None))
    (cfg.agent_io / "signals").mkdir(parents=True, exist_ok=True)
    (cfg.agent_io / "signals" / "s1.json").write_text(json.dumps(
        {"v": 3, "peaks": [{"t": 2.0, "kind": "speech_resume", "score": 0.5}], "duration": 20.0}))
    with Ledger.transaction(cfg) as led:
        led = adopt_warm_artifacts(led, cfg, "s1")
    assert led.sources["s1"].signal_peaks == [{"t": 2.0, "kind": "speech_resume", "score": 0.5}]


def test_purge_all_source_artifacts_extended(tmp_path):
    from fanops.models import Moment, MomentState, Clip, ClipState, Fmt
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/clip.mp4", state=SourceState.moments_decided,
                              transcript=[{"start": 0, "end": 1, "text": "hi"}], meta={"transcribed": True}))
        led.add_moment(Moment(id="m1", parent_id="s1", state=MomentState.decided, content_token="t", start=0, end=5, reason="x"))
        led.clips["cid1"] = Clip(id="cid1", parent_id="m1", state=ClipState.rendered, path="/x", aspect=Fmt.r9x16)
    (cfg.agent_io / "transcripts").mkdir(parents=True, exist_ok=True)
    (cfg.agent_io / "transcripts" / "clip.json").write_text("{}")
    (cfg.agent_io / "signals").mkdir(parents=True, exist_ok=True)
    (cfg.agent_io / "signals" / "s1.json").write_text("{}")
    (cfg.agent_io / "framing").mkdir(parents=True, exist_ok=True)
    (cfg.agent_io / "framing" / "s1.detect.json").write_text("{}")
    (cfg.agent_io / "keyframes" / "s1").mkdir(parents=True, exist_ok=True)
    (cfg.agent_io / "keyframes" / "s1" / "abc").mkdir()
    (cfg.agent_io / "manifests").mkdir(parents=True, exist_ok=True)
    (cfg.agent_io / "manifests" / "s1.json").write_text("{}")
    cfg.clips.mkdir(parents=True, exist_ok=True)
    (cfg.clips / "cid1.render.json").write_text('{"fp": "x"}')
    purge_all_source_artifacts(cfg, "s1", "/clip.mp4", led=Ledger.load(cfg))
    assert not (cfg.agent_io / "transcripts" / "clip.json").exists()
    assert not (cfg.agent_io / "signals" / "s1.json").exists()
    assert not (cfg.agent_io / "framing" / "s1.detect.json").exists()
    assert not (cfg.agent_io / "keyframes" / "s1").exists()
    assert not (cfg.agent_io / "manifests" / "s1.json").exists()
    assert not (cfg.clips / "cid1.render.json").exists()


def test_artifact_summary_joins_stages(tmp_path):
    cfg = Config(root=tmp_path)
    (cfg.agent_io / "transcripts").mkdir(parents=True, exist_ok=True)
    (cfg.agent_io / "transcripts" / "clip.json").write_text(json.dumps({"segments": []}))
    (cfg.agent_io / "signals").mkdir(parents=True, exist_ok=True)
    (cfg.agent_io / "signals" / "s1.json").write_text(json.dumps({"v": 3, "peaks": []}))
    assert artifact_summary(cfg, "s1", "/clip.mp4") == "transcribe+signals"
