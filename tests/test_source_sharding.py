# tests/test_source_sharding.py — S03 native inbox source sharding at catalogue time
import subprocess
import pytest
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.ingest import ingest_drops, stage_inbox_candidates, ingest_staged, _archive_staged
from fanops.ingest_shard import shard_points, _stem_is_shard_part, _shard_silence_cmd
from tests._require_e2e import skip_or_fail


def _put(p: Path, b: bytes = b"VID") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b); return p


class _Proc:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode = rc; self.stdout = stdout; self.stderr = stderr


def _ffprobe(duration: float, width: int = 1920, height: int = 1080):
    def handle(cmd, **kw):
        joined = " ".join(str(c) for c in cmd)
        if "codec_type" in joined:
            return _Proc(stdout="video\n")
        return _Proc(stdout=f"{width}\n{height}\n{duration}\n")
    return handle


def _ffmpeg_shard(*, fail_copy: bool = False, silence_stderr: str = ""):
    def handle(cmd, **kw):
        joined = " ".join(str(c) for c in cmd)
        if "silencedetect" in joined:
            return _Proc(stderr=silence_stderr)
        if fail_copy:
            return _Proc(rc=1, stderr="segment failed")
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"PART-" + out.name.encode())
        return _Proc()
    return handle


def _patch_os(mocker, *, ffprobe, ffmpeg=None, record=None):
    """One subprocess.run stub — ingest/media_probe/ingest_shard share the stdlib module."""
    def run(cmd, **kw):
        if record is not None:
            record.append(cmd)
        bin = Path(cmd[0]).name
        if bin == "ffprobe":
            return ffprobe(cmd, **kw)
        if bin == "ffmpeg":
            if ffmpeg is None:
                raise AssertionError(f"ffmpeg must not run: {cmd}")
            return ffmpeg(cmd, **kw)
        raise AssertionError(f"unexpected binary {cmd[0]}")
    return mocker.patch("subprocess.run", side_effect=run)


def test_source_shard_min_off_never_calls_ffmpeg(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_SOURCE_SHARD_MIN", "0")
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "long.mp4")
    cmds = []
    _patch_os(mocker, ffprobe=_ffprobe(7200.0), record=cmds)
    led, counts = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1 and counts.added == 1
    assert not any(Path(c[0]).name == "ffmpeg" for c in cmds)


def test_under_threshold_no_split(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "short.mp4", b"SHORT")
    cmds = []
    _patch_os(mocker, ffprobe=_ffprobe(1200.0), record=cmds)
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    src = next(iter(led.sources.values()))
    assert src.duration == 1200.0 and src.degraded_reason is None
    assert not any(Path(c[0]).name == "ffmpeg" for c in cmds)


def test_shard_points_snaps_to_silence(mocker):
    stderr = """
[silencedetect @ 0x] silence_start: 1480.0
[silencedetect @ 0x] silence_end: 1520.0 | silence_duration: 40.0
"""
    mocker.patch("subprocess.run",
                 return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=stderr))
    points = shard_points(Path("/fake/long.mp4"), 3000.0, target_s=1500.0)
    assert points == [1500.0]


def test_shard_points_hard_cut_when_silent_free(mocker):
    mocker.patch("subprocess.run",
                 return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    points = shard_points(Path("/fake/long.mp4"), 3000.0, target_s=1500.0)
    assert points == [1500.0]


def test_shard_file_fail_open(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "long.mp4", b"LONGVIDEO")
    _patch_os(mocker, ffprobe=_ffprobe(3600.0), ffmpeg=_ffmpeg_shard(fail_copy=True))
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    src = next(iter(led.sources.values()))
    assert src.degraded_reason == "shard_failed"


def test_parts_inherit_batch_and_origin(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_SOURCE_SHARD_MIN", "1")
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "long.mp4", b"LONGMASTER")
    _patch_os(mocker, ffprobe=_ffprobe(120.0), ffmpeg=_ffmpeg_shard())
    led, _ = ingest_drops(Ledger.load(cfg), cfg, origin="upload", batch_id="batch_named")
    assert len(led.sources) == 2
    for s in led.sources.values():
        assert s.source_origin == "upload"
        assert s.origin_kind == "native"
        assert s.batch_id == "batch_named"
    shas = {s.sha256 for s in led.sources.values()}
    assert len(shas) == 2


def test_third_party_never_sharded(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_SOURCE_SHARD_MIN", "1")
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "long.mp4", b"TP")
    cmds = []
    _patch_os(mocker, ffprobe=_ffprobe(3600.0), record=cmds)
    led, _ = ingest_drops(Ledger.load(cfg), cfg, origin_kind="third_party")
    assert len(led.sources) == 1
    assert not any(Path(c[0]).name == "ffmpeg" for c in cmds)


def test_part_stem_not_re_sharded(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_SOURCE_SHARD_MIN", "1")
    cfg = Config(root=tmp_path)
    _put(cfg.inbox / "foo-p01.mp4", b"PARTSTEM")
    cmds = []
    _patch_os(mocker, ffprobe=_ffprobe(3600.0), record=cmds)
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    assert len(led.sources) == 1
    assert led.sources[next(iter(led.sources))].duration == 3600.0
    assert not any(Path(c[0]).name == "ffmpeg" for c in cmds)


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
        skip_or_fail(f"ffmpeg unavailable: {r.stderr[:200]}")
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
