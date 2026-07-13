# tests/test_source_resume.py
# MOL-121: stage-aware source resume. `retry-source` must not discard a good transcript — an errored
# source whose transcript is intact resumes at `transcribed` (re-enters at signals, the stage that broke),
# not `catalogued` (a full re-transcribe of ~20 min of proven work). One helper owns the transition so the
# CLI verb and the Studio Resume button (MOL-123) call the SAME code, never a parallel implementation.
# MOL-471: `--force --from-stage catalogued` is the explicit operator gate for a full T0 reset — purge disk
# caches, discard gates, reconcile moments away, and rewind to catalogued (moments_decided included).
import json, time
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Moment, MomentState, Source, SourceState
from fanops.pipeline import resume_source
from fanops.cli import main
from fanops.studio import actions
from tests.fixtures.speech_segments import talk_seg


def _errored(cfg, *, transcript, transcribed):
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.error,
                              error_reason="TimeoutExpired: ffmpeg ...",
                              transcript=transcript, meta={"transcribed": transcribed}))


def test_resume_keeps_transcript_and_re_enters_at_signals(tmp_path):
    # AUTO: transcript present -> resume at `transcribed`, transcript + flag preserved, error cleared.
    cfg = Config(root=tmp_path)
    _errored(cfg, transcript=[{"start": 0, "end": 1, "text": "hi"}], transcribed=True)
    with Ledger.transaction(cfg) as led:
        resume_source(led, "s1")
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.transcribed
    assert s.error_reason is None
    assert s.transcript == [{"start": 0, "end": 1, "text": "hi"}]   # NOT discarded
    assert s.meta["transcribed"] is True                            # re-transcribe NOT forced


def test_resume_without_transcript_falls_back_to_full_retry(tmp_path):
    # No transcript (transcription itself never produced one) -> full retry: catalogued + flag cleared,
    # byte-identical to today's retry-source.
    cfg = Config(root=tmp_path)
    _errored(cfg, transcript=None, transcribed=True)               # flag set but no transcript = never really transcribed
    with Ledger.transaction(cfg) as led:
        resume_source(led, "s1")
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.catalogued
    assert s.meta["transcribed"] is False
    assert s.error_reason is None


def test_resume_empty_transcript_is_full_retry(tmp_path):
    # transcript == [] means "ran, no speech" — but an errored source with an empty transcript never
    # reached a usable transcribed state; treat it as full retry (there is nothing to preserve).
    cfg = Config(root=tmp_path)
    _errored(cfg, transcript=[], transcribed=True)
    with Ledger.transaction(cfg) as led:
        resume_source(led, "s1")
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.catalogued and s.meta["transcribed"] is False


def test_resume_refuses_a_healthy_source(tmp_path):
    # Guard: resume only acts on error / moments_empty. A healthy in-flight source must NEVER be rewound
    # (that would silently re-mint / duplicate work). resume_source returns False and leaves state intact.
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.moments_decided,
                              transcript=[{"start": 0, "end": 1, "text": "hi"}], meta={"transcribed": True}))
    with Ledger.transaction(cfg) as led:
        changed = resume_source(led, "s1")
        assert changed is False
    assert Ledger.load(cfg).sources["s1"].state is SourceState.moments_decided


def test_resume_moments_empty_full_retry(tmp_path):
    # moments_empty is the documented re-runnable sibling — resume it as a full retry (re-request moments).
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.moments_empty))
    with Ledger.transaction(cfg) as led:
        assert resume_source(led, "s1") is True
    assert Ledger.load(cfg).sources["s1"].state is SourceState.catalogued


# ── CLI surface: retry-source defaults to AUTO (stage-aware); --from-stage catalogued forces full retry ──

def test_cli_retry_source_auto_preserves_transcript(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    _errored(cfg, transcript=[{"start": 0, "end": 1, "text": "hi"}], transcribed=True)
    assert main(["retry-source", "s1"]) == 0
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.transcribed and s.transcript and s.meta["transcribed"] is True


def test_cli_retry_source_from_stage_catalogued_forces_full_retry(tmp_path, monkeypatch):
    # The legacy full-retry stays reachable explicitly, even with a transcript present.
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    _errored(cfg, transcript=[{"start": 0, "end": 1, "text": "hi"}], transcribed=True)
    assert main(["retry-source", "s1", "--from-stage", "catalogued"]) == 0
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.catalogued and s.meta["transcribed"] is False


def test_cli_retry_source_unknown_id_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Config(root=tmp_path)
    assert main(["retry-source", "nope"]) == 2
    assert "no such source: nope" in capsys.readouterr().err


# ── MOL-471: `--force --from-stage catalogued` T0 reset recipe ──

def _moments_decided(cfg, *, sid="s1", path="/clip.mp4"):
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id=sid, source_path=path, state=SourceState.moments_decided,
                              language="en", transcript=[{"start": 0, "end": 1, "text": "hi"}],
                              meta={"transcribed": True, "vocals_isolated": True}))
        led.add_moment(Moment(id="m1", parent_id=sid, state=MomentState.decided,
                              content_token="tok", start=0.0, end=5.0, reason="pick"))


def _seed_transcribe_cache(cfg, *, sid="s1", path="/clip.mp4"):
    out = cfg.agent_io / "transcripts"
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(path).stem
    cache = out / f"{stem}.json"
    cache.write_text(json.dumps({"segments": [{"start": 0, "end": 1, "text": "stale"}], "language": "en"}))
    (out / "vocals" / "htdemucs" / stem).mkdir(parents=True, exist_ok=True)
    (out / "vocals" / "htdemucs" / stem / "vocals.mp3").write_bytes(b"vocals")
    (out / f"{stem}.mp3").write_bytes(b"stem")
    (cfg.agent_io / "signals").mkdir(parents=True, exist_ok=True)
    (cfg.agent_io / "signals" / f"{sid}.json").write_text("{}")
    return cache


def test_force_reset_purges_on_disk_caches(tmp_path):
    cfg = Config(root=tmp_path)
    _moments_decided(cfg)
    cache = _seed_transcribe_cache(cfg)
    before = cache.stat().st_mtime
    time.sleep(0.02)
    with Ledger.transaction(cfg) as led:
        assert resume_source(led, "s1", from_stage="catalogued", force=True, cfg=cfg) is True
    assert not cache.exists()
    assert not (cfg.agent_io / "signals" / "s1.json").exists()
    assert not (cfg.agent_io / "transcripts" / "vocals" / "htdemucs" / "clip" / "vocals.mp3").exists()
    cache.write_text("{}")                                      # simulate a fresh transcribe landing later
    assert cache.stat().st_mtime > before


def test_force_reset_rewinds_moments_decided_source(tmp_path):
    cfg = Config(root=tmp_path)
    _moments_decided(cfg)
    _seed_transcribe_cache(cfg)
    with Ledger.transaction(cfg) as led:
        assert resume_source(led, "s1", from_stage="catalogued", force=True, cfg=cfg) is True
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.catalogued
    assert s.transcript is None and s.language is None
    assert s.meta.get("transcribed") is False
    assert "m1" not in Ledger.load(cfg).moments


def test_cli_refuses_moments_decided_without_force(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    _moments_decided(cfg)
    assert main(["retry-source", "s1"]) == 2
    assert Ledger.load(cfg).sources["s1"].state is SourceState.moments_decided
    assert "not recoverable" in capsys.readouterr().err


def test_force_reset_catalogued_on_already_catalogued_is_idempotent(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.catalogued))
    with Ledger.transaction(cfg) as led:
        assert resume_source(led, "s1", from_stage="catalogued", force=True, cfg=cfg) is True
    s1 = Ledger.load(cfg).sources["s1"]
    assert s1.state is SourceState.catalogued
    with Ledger.transaction(cfg) as led:
        assert resume_source(led, "s1", from_stage="catalogued", force=True, cfg=cfg) is True
    assert Ledger.load(cfg).sources["s1"].state is SourceState.catalogued


def test_cli_force_from_catalogued_resets_moments_decided(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    _moments_decided(cfg)
    _seed_transcribe_cache(cfg)
    assert main(["retry-source", "s1", "--force", "--from-stage", "catalogued"]) == 0
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.catalogued and s.transcript is None and s.meta.get("transcribed") is False


def test_force_reset_purges_manifest_framing_keyframes(tmp_path):
    cfg = Config(root=tmp_path)
    _moments_decided(cfg, path=str(tmp_path / "clip.mp4"))
    (cfg.agent_io / "manifests").mkdir(parents=True)
    (cfg.agent_io / "manifests" / "s1.json").write_text("{}")
    (cfg.agent_io / "framing").mkdir(parents=True)
    (cfg.agent_io / "framing" / "s1.detect.json").write_text("{}")
    (cfg.agent_io / "keyframes" / "s1").mkdir(parents=True)
    _seed_transcribe_cache(cfg)
    with Ledger.transaction(cfg) as led:
        assert resume_source(led, "s1", from_stage="catalogued", force=True, cfg=cfg) is True
    assert not (cfg.agent_io / "manifests" / "s1.json").exists()
    assert not (cfg.agent_io / "framing" / "s1.detect.json").exists()
    assert not (cfg.agent_io / "keyframes" / "s1").exists()


def test_auto_resume_from_error_with_warm_transcript(tmp_path):
    cfg = Config(root=tmp_path)
    path = str(tmp_path / "vid.mp4")
    Path(path).write_bytes(b"V")
    (cfg.agent_io / "transcripts").mkdir(parents=True)
    (cfg.agent_io / "transcripts" / "vid.json").write_text(json.dumps(
        {"language": "en", "segments": [talk_seg("warm", start=0, end=1)]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path=path, state=SourceState.error,
                              error_reason="TimeoutExpired: whisper hung", transcript=None,
                              meta={"transcribed": False}))
    from fanops.pipeline import reconcile_source_progress
    from fanops.log import get_logger
    with Ledger.transaction(cfg) as led:
        reconcile_source_progress(led, cfg, get_logger(cfg))
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.transcribed and s.transcript and s.error_reason is None


def test_toolchain_missing_error_not_auto_resumed(tmp_path):
    cfg = Config(root=tmp_path)
    path = str(tmp_path / "vid.mp4")
    (cfg.agent_io / "transcripts").mkdir(parents=True)
    (cfg.agent_io / "transcripts" / "vid.json").write_text(json.dumps(
        {"language": "en", "segments": [talk_seg("warm", start=0, end=1)]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path=path, state=SourceState.error,
                              error_reason="toolchain missing: whisper (FileNotFoundError)"))
    from fanops.pipeline import reconcile_source_progress
    from fanops.log import get_logger
    with Ledger.transaction(cfg) as led:
        reconcile_source_progress(led, cfg, get_logger(cfg))
    assert Ledger.load(cfg).sources["s1"].state is SourceState.error


def test_studio_force_reset_threads_from_stage_and_force(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    _moments_decided(cfg, sid="src_1", path="/x/src_1.mp4")
    _seed_transcribe_cache(cfg, sid="src_1", path="/x/src_1.mp4")
    res = actions.resume_source_studio(cfg, "src_1", from_stage="catalogued", force=True)
    assert res.ok
    s = Ledger.load(cfg).sources["src_1"]
    assert s.state is SourceState.catalogued and s.transcript is None
