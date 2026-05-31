# tests/test_ingest.py
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import SourceState
from fanops.ingest import ingest_drops, sha256_of, is_excluded, scan_local, probe_dimensions

def _put(p, b):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)

def test_sha256_stable(tmp_path):
    f = tmp_path / "a.bin"; f.write_bytes(b"hi")
    assert sha256_of(f) == sha256_of(f)

def test_catalogues_and_probes(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put(cfg.inbox / "a.mp4", b"V")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1920, 1080, 12.0))
    led = ingest_drops(Ledger.load(cfg), cfg)
    s = next(iter(led.sources.values()))
    assert s.state is SourceState.catalogued and s.source_origin == "drop" and s.sha256
    assert s.width == 1920 and s.height == 1080 and s.duration == 12.0

def test_dedupe_by_content_not_path(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(0, 0, 0.0))
    _put(cfg.inbox / "a.mp4", b"SAME"); _put(cfg.inbox / "b.mp4", b"SAME")
    led = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    led = ingest_drops(led, cfg)
    assert len(led.sources) == 1

def test_skips_audio_only_drop(tmp_path, mocker):
    # An audio-only file (no video stream) is NOT catalogued: the clip pipeline reframes via
    # ffmpeg -vf, which is silently ignored on audio-only input and would emit a videoless
    # 'clip'. has_video_stream() gates it out at ingest.
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "voice.wav", b"A"); _put(cfg.inbox / "perf.mp4", b"V")
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 5.0))
    mocker.patch("fanops.ingest.has_video_stream",
                 side_effect=lambda p: p.suffix.lower() != ".wav")
    led = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    assert next(iter(led.sources.values())).meta["original_name"] == "perf.mp4"

def test_is_excluded():
    assert is_excluded("Moh Flow passport & ID.zip")
    assert is_excluded("Agreement - Accelerator.pdf")
    assert not is_excluded("adidas - day 01 moh flow.MOV")

def test_skips_pii(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(0, 0, 0.0))
    _put(cfg.inbox / "passport scan.jpg", b"S"); _put(cfg.inbox / "perf.mp4", b"V")
    led = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    assert next(iter(led.sources.values())).meta["original_name"] == "perf.mp4"

def test_scan_excludes_pii(tmp_path):
    d = tmp_path / "D"; d.mkdir()
    (d / "passport.jpg").write_bytes(b"x"); (d / "clip.mp4").write_bytes(b"y")
    assert {Path(c).name for c in scan_local([d])} == {"clip.mp4"}
