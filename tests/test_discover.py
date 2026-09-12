import json
import subprocess
from pathlib import Path
import fanops.discover as discover


def _put(p, b=b"V"):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)


class _Proc:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode = rc; self.stdout = stdout; self.stderr = stderr


def _ffprobe_ok(width=1080, height=1920, duration=12.5):
    def handle(cmd, **kw):
        joined = " ".join(str(c) for c in cmd)
        if "codec_type" in joined:
            return _Proc(stdout="video\n")
        return _Proc(stdout=f"{width}\n{height}\n{duration}\n")
    return handle


def _ffmpeg_thumb(cmd, **kw):
    Path(cmd[-1]).write_bytes(b"JPG")
    return _Proc()


def _patch_os(mocker, *, ffprobe=None, ffmpeg=None):
    """One subprocess.run stub — media_probe and discover share the stdlib module object."""
    def run(cmd, **kw):
        bin = Path(cmd[0]).name
        if bin == "ffprobe":
            return (ffprobe or _ffprobe_ok())(cmd, **kw)
        if bin == "ffmpeg":
            if ffmpeg is None:
                raise AssertionError(f"ffmpeg not stubbed: {cmd}")
            return ffmpeg(cmd, **kw)
        raise AssertionError(f"unexpected binary {cmd[0]}")
    return mocker.patch("subprocess.run", side_effect=run)


def test_candidate_meta_uses_cheap_probe_only(tmp_path, mocker):
    f = tmp_path / "a.mp4"; _put(f, b"VIDEO")
    _patch_os(mocker, ffprobe=_ffprobe_ok(1080, 1920, 12.5))
    m = discover.candidate_meta(f)
    assert m["bytes"] == 5 and m["width"] == 1080 and m["height"] == 1920 and m["duration"] == 12.5
    assert "mtime" in m


def test_candidate_meta_fail_soft_when_probe_fails(tmp_path, mocker):
    # Empty/garbled ffprobe stdout is zeros inside probe_dimensions, then None on the candidate —
    # the file is still listed. Do not patch probe_dimensions to raise; that path is not the OS edge.
    f = tmp_path / "a.mp4"; _put(f)
    mocker.patch("subprocess.run", return_value=_Proc(stdout=""))
    m = discover.candidate_meta(f)
    assert m["bytes"] > 0 and m["duration"] is None and m["width"] is None


def test_candidate_meta_logs_when_ffprobe_absent(tmp_path, mocker, caplog):
    import logging
    f = tmp_path / "a.mp4"; _put(f)
    def absent(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", "ffprobe")
    mocker.patch("subprocess.run", side_effect=absent)
    with caplog.at_level(logging.WARNING, logger="fanops.discover"):
        m = discover.candidate_meta(f)
    assert m["width"] is None and m["bytes"] > 0
    assert any("ffprobe absent" in r.getMessage() and str(f) in r.getMessage() for r in caplog.records)


def test_make_thumbnail_builds_ffmpeg_cmd(tmp_path, mocker):
    src = tmp_path / "a.mp4"; _put(src)
    out = tmp_path / "a.jpg"
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd; Path(cmd[-1]).write_bytes(b"JPG")
        return _Proc()
    mocker.patch("subprocess.run", side_effect=fake_run)
    ok = discover.make_thumbnail(src, out)
    assert ok is True and out.exists()
    assert captured["cmd"][0] == "ffmpeg" and "-frames:v" in captured["cmd"] and captured["cmd"][-1] == str(out)


def test_make_thumbnail_fail_open_when_ffmpeg_fails(tmp_path, mocker):
    src = tmp_path / "a.mp4"; _put(src); out = tmp_path / "a.jpg"
    def boom(cmd, **kw): raise FileNotFoundError(2, "no ffmpeg", "ffmpeg")
    mocker.patch("subprocess.run", side_effect=boom)
    assert discover.make_thumbnail(src, out) is False
    assert not out.exists()


def test_make_thumbnail_fail_open_on_timeout(tmp_path, mocker):
    src = tmp_path / "a.mp4"; _put(src); out = tmp_path / "a.jpg"
    seen = {}
    def hung(cmd, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
    mocker.patch("subprocess.run", side_effect=hung)
    assert discover.make_thumbnail(src, out) is False
    assert not out.exists()
    assert seen.get("timeout") == 60.0


def test_discover_writes_thumbnails_and_manifest(tmp_path, mocker):
    from fanops.config import Config
    src_dir = tmp_path / "bank"; src_dir.mkdir()
    _put(src_dir / "good1.mp4", b"GOOD1"); _put(src_dir / "good2.mp4", b"GOOD2")
    _put(src_dir / "passport scan.jpg")
    _put(src_dir / "notes.txt")
    cfg = Config(root=tmp_path)
    _patch_os(mocker, ffprobe=_ffprobe_ok(1080, 1920, 8.0), ffmpeg=_ffmpeg_thumb)
    summary = discover.discover(cfg, [src_dir])
    assert summary["found"] == 2 and summary["new"] == 2
    manifest = json.loads((cfg.review / "manifest.json").read_text())
    assert len(manifest) == 2
    paths = {e["source_path"] for e in manifest.values()}
    assert paths == {str(src_dir / "good1.mp4"), str(src_dir / "good2.mp4")}
    assert all("bytes" in e and "duration" in e for e in manifest.values())
    assert all(e["width"] == 1080 and e["duration"] == 8.0 for e in manifest.values())
    assert len(list(cfg.review.glob("*.jpg"))) == 2


def test_discover_dedupes_already_seen_content(tmp_path):
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    from fanops.ingest import sha256_of
    src_dir = tmp_path / "bank"; src_dir.mkdir()
    f = src_dir / "dup.mp4"; _put(f, b"SAME")
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s_dup", source_path="/x.mp4", state=SourceState.catalogued, sha256=sha256_of(f)))
    summary = discover.discover(cfg, [src_dir])
    assert summary["found"] == 1 and summary["new"] == 0 and summary["skipped"] == 1
    assert json.loads((cfg.review / "manifest.json").read_text()) == {}


def test_intake_copies_only_approved_originals_to_inbox(tmp_path, mocker):
    from fanops.config import Config
    from fanops.ingest import sha256_of
    src_dir = tmp_path / "bank"; src_dir.mkdir()
    keep = src_dir / "keep.mp4"; _put(keep, b"KEEP")
    drop = src_dir / "drop.mp4"; _put(drop, b"DROP")
    cfg = Config(root=tmp_path)
    _patch_os(mocker, ffprobe=_ffprobe_ok(0, 0, 0.0), ffmpeg=_ffmpeg_thumb)
    discover.discover(cfg, [src_dir])
    keep_eid = sha256_of(keep)[:16]
    (cfg.review / "approved").mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{keep_eid}.jpg").rename(cfg.review / "approved" / f"{keep_eid}.jpg")
    summary = discover.intake(cfg)
    assert summary["intaken"] == 1
    inbox_files = {p.name for p in cfg.inbox.glob("*") if p.is_file()}
    assert "keep.mp4" in inbox_files and "drop.mp4" not in inbox_files
    assert (cfg.inbox / "keep.mp4").read_bytes() == b"KEEP"


def test_intake_is_idempotent_and_reports_missing(tmp_path, mocker):
    from fanops.config import Config
    from fanops.ingest import sha256_of
    src_dir = tmp_path / "bank"; src_dir.mkdir()
    f = src_dir / "x.mp4"; _put(f, b"X")
    cfg = Config(root=tmp_path)
    _patch_os(mocker, ffprobe=_ffprobe_ok(0, 0, 0.0), ffmpeg=_ffmpeg_thumb)
    discover.discover(cfg, [src_dir])
    eid = sha256_of(f)[:16]
    (cfg.review / "approved").mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{eid}.jpg").rename(cfg.review / "approved" / f"{eid}.jpg")
    assert discover.intake(cfg)["intaken"] == 1
    assert discover.intake(cfg)["intaken"] == 0
    f.unlink()
    discover.discover(cfg, [src_dir])
    (cfg.review / "approved" / "deadbeefdeadbeef.jpg").write_bytes(b"J")
    out = discover.intake(cfg)
    assert out["missing"] >= 1


def test_intake_keyless_manifest_entry_counts_missing_not_crash(tmp_path):
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    (cfg.review / "approved").mkdir(parents=True, exist_ok=True)
    (cfg.review / "manifest.json").write_text(json.dumps({"abc": {"width": 1080}}))
    (cfg.review / "approved" / "abc.jpg").write_bytes(b"J")
    out = discover.intake(cfg)
    assert out["missing"] == 1 and out["intaken"] == 0


def test_discover_corrupt_manifest_raises_typed_control_error(tmp_path):
    import pytest
    from fanops.config import Config
    from fanops.errors import ControlFileError
    cfg = Config(root=tmp_path)
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / "manifest.json").write_text("{truncated")
    roots = tmp_path / "roots"; roots.mkdir()
    with pytest.raises(ControlFileError, match="manifest.json"):
        discover.discover(cfg, [roots])


def test_intake_copy_stages_via_part_then_atomic_replace(tmp_path, mocker):
    from fanops.config import Config
    from fanops.ingest import sha256_of
    src_dir = tmp_path / "bank"; src_dir.mkdir()
    f = src_dir / "keep.mp4"; _put(f, b"KEEP")
    cfg = Config(root=tmp_path)
    _patch_os(mocker, ffprobe=_ffprobe_ok(0, 0, 0.0), ffmpeg=_ffmpeg_thumb)
    discover.discover(cfg, [src_dir])
    eid = sha256_of(f)[:16]
    (cfg.review / "approved").mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{eid}.jpg").rename(cfg.review / "approved" / f"{eid}.jpg")
    discover.intake(cfg)
    dest = cfg.inbox / "keep.mp4"
    assert dest.read_bytes() == b"KEEP"
    assert not dest.with_name(dest.name + ".part").exists()


def test_intake_corrupt_intaken_raises_typed_control_error(tmp_path):
    import pytest
    from fanops.config import Config
    from fanops.errors import ControlFileError
    cfg = Config(root=tmp_path)
    (cfg.review / "approved").mkdir(parents=True, exist_ok=True)
    (cfg.review / "manifest.json").write_text("{}")
    (cfg.review / "intaken.json").write_text("[truncated")
    with pytest.raises(ControlFileError, match="intaken.json"):
        discover.intake(cfg)
