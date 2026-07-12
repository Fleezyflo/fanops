# tests/test_source_sharding.py — S03 native inbox source sharding at catalogue time
import subprocess
import pytest
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.ingest import (ingest_drops, stage_inbox_candidates, ingest_staged, _archive_staged,
                           shard_points, _stem_is_shard_part, _shard_silence_cmd)

def _put(p: Path, b: bytes = b"VID") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b); return p

def _mock_video_ingest(mocker, *, duration: float, width: int = 1920, height: int = 1080):
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(width, height, duration))
    mocker.patch("fanops.ingest.sha256_of", side_effect=lambda p: f"sha-{p.name}")

def test_source_shard_min_off_never_calls_ffmpeg(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_SOURCE_SHARD_MIN", "0")
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "long.mp4")
    _mock_video_ingest(mocker, duration=7200.0)
    spy = mocker.patch("fanops.ingest.subprocess.run")
    led, counts = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    assert not any("ffmpeg" in (c[0] if c else "") for c, _ in spy.call_args_list if c)

def test_under_threshold_no_split(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "short.mp4")
    _mock_video_ingest(mocker, duration=1200.0)   # 20 min < default 45 min threshold
    mocker.patch("fanops.ingest.shard_points", side_effect=AssertionError("shard_points must not run"))
    mocker.patch("fanops.ingest.shard_file", side_effect=AssertionError("shard_file must not run"))
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    assert led.sources[next(iter(led.sources))].duration == 1200.0

def test_shard_points_snaps_to_silence(mocker):
    stderr = """
[silencedetect @ 0x] silence_start: 1480.0
[silencedetect @ 0x] silence_end: 1520.0 | silence_duration: 40.0
"""
    cp = subprocess.CompletedProcess([], 0, stdout="", stderr=stderr)
    mocker.patch("fanops.ingest._run_ffmpeg", return_value=cp)
    points = shard_points(Path("/fake/long.mp4"), 3000.0, target_s=1500.0)
    assert points == [1500.0]

def test_shard_points_hard_cut_when_silent_free(mocker):
    cp = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    mocker.patch("fanops.ingest._run_ffmpeg", return_value=cp)
    points = shard_points(Path("/fake/long.mp4"), 3000.0, target_s=1500.0)
    assert points == [1500.0]

def test_shard_file_fail_open(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "long.mp4", b"LONGVIDEO")
    _mock_video_ingest(mocker, duration=3600.0)
    mocker.patch("fanops.ingest.shard_points", return_value=[1800.0])
    mocker.patch("fanops.ingest.shard_file", return_value=None)
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    src = next(iter(led.sources.values()))
    assert src.degraded_reason == "shard_failed"

def test_parts_inherit_batch_and_origin(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_SOURCE_SHARD_MIN", "1")
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "long.mp4")
    _mock_video_ingest(mocker, duration=120.0)
    p1 = cfg.inbox / "long-p01.mp4"; p2 = cfg.inbox / "long-p02.mp4"
    _put(p1, b"P1"); _put(p2, b"P2")
    mocker.patch("fanops.ingest.shard_points", return_value=[60.0])
    mocker.patch("fanops.ingest.shard_file", return_value=[p1, p2])
    led, _ = ingest_drops(Ledger.load(cfg), cfg, origin="upload", batch_id="batch_named")
    assert len(led.sources) == 2
    for s in led.sources.values():
        assert s.source_origin == "upload"
        assert s.origin_kind == "native"
        assert s.batch_id == "batch_named"

def test_third_party_never_sharded(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_SOURCE_SHARD_MIN", "1")
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "long.mp4")
    _mock_video_ingest(mocker, duration=3600.0)
    mocker.patch("fanops.ingest.shard_points", side_effect=AssertionError("third_party must not shard"))
    mocker.patch("fanops.ingest.shard_file", side_effect=AssertionError("third_party must not shard"))
    led, _ = ingest_drops(Ledger.load(cfg), cfg, origin_kind="third_party")
    assert len(led.sources) == 1

def test_part_stem_not_re_sharded(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_SOURCE_SHARD_MIN", "1")
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "foo-p01.mp4")
    _mock_video_ingest(mocker, duration=3600.0)
    mocker.patch("fanops.ingest.shard_points", side_effect=AssertionError("part stem must not re-shard"))
    mocker.patch("fanops.ingest.shard_file", side_effect=AssertionError("part stem must not re-shard"))
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    assert led.sources[next(iter(led.sources))].duration == 3600.0

def test_stem_is_shard_part():
    assert _stem_is_shard_part("foo-p01") is True
    assert _stem_is_shard_part("foo-p99") is True
    assert _stem_is_shard_part("foo-p1") is False
    assert _stem_is_shard_part("long") is False

def test_shard_silence_cmd_includes_vn():
    cmd = _shard_silence_cmd(Path("/x.mp4"))
    assert "-vn" in cmd
    assert "silencedetect=noise=-35dB:d=1.5" in cmd

@pytest.mark.integration
def test_shard_integration_lavfi_split(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_SOURCE_SHARD_MIN", "1")
    cfg = Config(root=tmp_path)
    src = cfg.inbox / "live.mp4"
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=90",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "90",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac", str(src)],
        capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"ffmpeg unavailable: {r.stderr[:200]}")
    staged = stage_inbox_candidates(cfg, origin="upload", batch_id="batch_int")
    led = Ledger.load(cfg)
    led, counts = ingest_staged(led, cfg, staged, batch_id="batch_int")
    led.save()
    _archive_staged(cfg, staged)
    assert counts.added == 2
    assert len(led.sources) == 2
    shas = {s.sha256 for s in led.sources.values()}
    assert len(shas) == 2
    batches = {s.batch_id for s in led.sources.values()}
    assert batches == {"batch_int"}
    assert not src.exists()
    assert (cfg.inbox / ".ingested" / "live.mp4").exists() or any(
        (cfg.inbox / ".ingested").glob("live*.mp4"))
