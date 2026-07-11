# tests/test_transcribe_stem_cache.py — MOL-482: reuse cached demucs stem on whisper retry; preserve stem on whisper-only force reset
import json, subprocess
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.pipeline import resume_source
from fanops.transcribe import transcribe_source, purge_source_artifacts


def _catalogued(cfg, *, sid="src_1", path=None, sha256="abc123"):
    path = path or str(cfg.sources / "src_1.mp4")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"SRC")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id=sid, source_path=path, state=SourceState.catalogued, sha256=sha256))


def test_whisper_retry_reuses_cached_stem_skips_demucs(tmp_path, mocker, monkeypatch):
    # demucs already ran; whisper failed; stem mp3 on disk with matching src.sha256 -> skip isolate_vocals.
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "1")
    cfg = Config(root=tmp_path)
    path = str(cfg.sources / "src_1.mp4")
    _catalogued(cfg, path=path, sha256="deadbeef")
    out_dir = cfg.agent_io / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    iso = mocker.patch("fanops.transcribe.isolate_vocals")
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=subprocess.TimeoutExpired("whisper", 1))
    with Ledger.transaction(cfg) as led:
        led = transcribe_source(led, cfg, "src_1")
    assert led.sources["src_1"].state is SourceState.error
    iso.assert_called_once()                                      # first pass ran demucs
    (out_dir / "src_1.mp3").write_bytes(b"STEM")                  # demucs output landed before whisper died
    iso.reset_mock()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=subprocess.TimeoutExpired("whisper", 1))
    with Ledger.transaction(cfg) as led:
        led = transcribe_source(led, cfg, "src_1")
    iso.assert_not_called()                                       # retry reused stem — no demucs
    assert led.sources["src_1"].meta.get("vocals_isolated") is True


def test_force_reset_whisper_timeout_preserves_stem(tmp_path):
    cfg = Config(root=tmp_path)
    path = str(tmp_path / "clip.mp4")
    _catalogued(cfg, sid="s1", path=path)
    out = cfg.agent_io / "transcripts"
    out.mkdir(parents=True, exist_ok=True)
    stem_mp3 = out / "clip.mp3"
    stem_mp3.write_bytes(b"stem")
    demucs_dir = out / "vocals" / "htdemucs" / "clip"
    demucs_dir.mkdir(parents=True, exist_ok=True)
    (demucs_dir / "vocals.mp3").write_bytes(b"vocals")
    cache = out / "clip.json"
    cache.write_text(json.dumps({"segments": [], "language": "en"}))
    with Ledger.transaction(cfg) as led:
        led.sources["s1"] = led.sources["s1"].model_copy(update={
            "state": SourceState.error,
            "error_reason": "whisper timed out after 2700s",
            "meta": {"transcribed": False},
        })
        assert resume_source(led, "s1", from_stage="catalogued", force=True, cfg=cfg) is True
    assert not cache.exists()
    assert stem_mp3.exists()
    assert (demucs_dir / "vocals.mp3").exists()


def test_force_reset_generic_error_still_purges_stem(tmp_path):
    cfg = Config(root=tmp_path)
    path = str(tmp_path / "clip.mp4")
    _catalogued(cfg, sid="s1", path=path)
    out = cfg.agent_io / "transcripts"
    out.mkdir(parents=True, exist_ok=True)
    stem_mp3 = out / "clip.mp3"
    stem_mp3.write_bytes(b"stem")
    demucs_dir = out / "vocals" / "htdemucs" / "clip"
    demucs_dir.mkdir(parents=True, exist_ok=True)
    (demucs_dir / "vocals.mp3").write_bytes(b"vocals")
    with Ledger.transaction(cfg) as led:
        led.sources["s1"] = led.sources["s1"].model_copy(update={
            "state": SourceState.error,
            "error_reason": "toolchain missing: whisper (FileNotFoundError)",
        })
        assert resume_source(led, "s1", from_stage="catalogued", force=True, cfg=cfg) is True
    assert not stem_mp3.exists()
    assert not demucs_dir.exists()


def test_purge_preserve_vocals_skips_stem_paths(tmp_path):
    cfg = Config(root=tmp_path)
    path = str(tmp_path / "vid.mp4")
    out = cfg.agent_io / "transcripts"
    out.mkdir(parents=True, exist_ok=True)
    stem_mp3 = out / "vid.mp3"
    stem_mp3.write_bytes(b"stem")
    demucs_dir = out / "vocals" / "htdemucs" / "vid"
    demucs_dir.mkdir(parents=True, exist_ok=True)
    (demucs_dir / "vocals.mp3").write_bytes(b"vocals")
    cache = out / "vid.json"
    cache.write_text("{}")
    purge_source_artifacts(cfg, "s1", path, preserve_vocals=True)
    assert not cache.exists()
    assert stem_mp3.exists()
    assert (demucs_dir / "vocals.mp3").exists()
