import json
import subprocess
from pathlib import Path
import fanops.discover as discover

def _put(p, b=b"V"):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)

def test_candidate_meta_uses_cheap_probe_only(tmp_path, mocker):
    f = tmp_path / "a.mp4"; _put(f, b"VIDEO")
    mocker.patch("fanops.discover.probe_dimensions", return_value=(1080, 1920, 12.5))
    m = discover.candidate_meta(f)
    assert m["bytes"] == 5 and m["width"] == 1080 and m["height"] == 1920 and m["duration"] == 12.5
    assert "mtime" in m

def test_candidate_meta_fail_soft_when_probe_fails(tmp_path, mocker):
    # ffprobe choking must NOT drop the candidate — list it with duration/dims None-ish.
    f = tmp_path / "a.mp4"; _put(f)
    mocker.patch("fanops.discover.probe_dimensions", side_effect=Exception("ffprobe boom"))
    m = discover.candidate_meta(f)
    assert m["bytes"] > 0 and m["duration"] is None and m["width"] is None

def test_make_thumbnail_builds_ffmpeg_cmd(tmp_path, mocker):
    src = tmp_path / "a.mp4"; _put(src)
    out = tmp_path / "a.jpg"
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd; Path(cmd[-1]).write_bytes(b"JPG")
        class R: returncode = 0; stderr = ""
        return R()
    mocker.patch("fanops.discover.subprocess.run", side_effect=fake_run)
    ok = discover.make_thumbnail(src, out)
    assert ok is True and out.exists()
    assert captured["cmd"][0] == "ffmpeg" and "-frames:v" in captured["cmd"] and captured["cmd"][-1] == str(out)

def test_make_thumbnail_fail_open_when_ffmpeg_fails(tmp_path, mocker):
    src = tmp_path / "a.mp4"; _put(src); out = tmp_path / "a.jpg"
    def boom(cmd, **kw): raise FileNotFoundError(2, "no ffmpeg", "ffmpeg")
    mocker.patch("fanops.discover.subprocess.run", side_effect=boom)
    assert discover.make_thumbnail(src, out) is False     # fail-open: no raise, no thumbnail
    assert not out.exists()

def test_make_thumbnail_fail_open_on_timeout(tmp_path, mocker):
    # Discovery is CHEAP by design (module docstring) — one hung candidate must not stall a whole
    # scan, so the one-frame thumbnail gets a TIGHT bound (60s, not the render-grade 600s) and a
    # timeout fails open like the absent branch: False, no thumb, candidate still listed from meta.
    src = tmp_path / "a.mp4"; _put(src); out = tmp_path / "a.jpg"
    seen = {}
    def hung(cmd, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
    mocker.patch("fanops.discover.subprocess.run", side_effect=hung)
    assert discover.make_thumbnail(src, out) is False     # fail-open: no raise, no thumbnail
    assert not out.exists()
    assert seen.get("timeout") == 60.0                    # the bound is actually wired

def test_discover_writes_thumbnails_and_manifest(tmp_path, mocker):
    from fanops.config import Config
    src_dir = tmp_path / "bank"; src_dir.mkdir()
    _put(src_dir / "good1.mp4", b"GOOD1"); _put(src_dir / "good2.mp4", b"GOOD2")  # distinct content -> distinct entries
    _put(src_dir / "passport scan.jpg")          # PII-named -> excluded by is_excluded
    _put(src_dir / "notes.txt")                  # non-media -> ignored
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.discover.probe_dimensions", return_value=(1080, 1920, 8.0))
    mocker.patch("fanops.discover.make_thumbnail", side_effect=lambda p, o, **k: (o.write_bytes(b"JPG") or True))
    summary = discover.discover(cfg, [src_dir])
    assert summary["found"] == 2 and summary["new"] == 2          # only the 2 media, PII excluded
    manifest = json.loads((cfg.review / "manifest.json").read_text())
    assert len(manifest) == 2
    # each entry resolves back to a real source_path + carries cheap meta
    paths = {e["source_path"] for e in manifest.values()}
    assert paths == {str(src_dir / "good1.mp4"), str(src_dir / "good2.mp4")}
    assert all("bytes" in e and "duration" in e for e in manifest.values())
    assert len(list(cfg.review.glob("*.jpg"))) == 2               # a thumbnail per candidate

def test_discover_dedupes_already_seen_content(tmp_path, mocker):
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    src_dir = tmp_path / "bank"; src_dir.mkdir()
    f = src_dir / "dup.mp4"; _put(f, b"SAME")
    cfg = Config(root=tmp_path)
    # pre-seed the ledger with a Source of this exact content sha
    from fanops.ingest import sha256_of
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s_dup", source_path="/x.mp4", state=SourceState.catalogued, sha256=sha256_of(f)))
    mocker.patch("fanops.discover.probe_dimensions", return_value=(0, 0, 0.0))
    mocker.patch("fanops.discover.make_thumbnail", return_value=True)
    summary = discover.discover(cfg, [src_dir])
    assert summary["found"] == 1 and summary["new"] == 0 and summary["skipped"] == 1   # already in ledger

def test_intake_copies_only_approved_originals_to_inbox(tmp_path, mocker):
    from fanops.config import Config
    src_dir = tmp_path / "bank"; src_dir.mkdir()
    keep = src_dir / "keep.mp4"; _put(keep, b"KEEP")
    drop = src_dir / "drop.mp4"; _put(drop, b"DROP")
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.discover.probe_dimensions", return_value=(0, 0, 0.0))
    mocker.patch("fanops.discover.make_thumbnail", side_effect=lambda p, o, **k: (o.write_bytes(b"J") or True))
    discover.discover(cfg, [src_dir])
    # operator approves ONLY keep.mp4 by MOVING its thumbnail entry into approved/
    from fanops.ingest import sha256_of
    keep_eid = sha256_of(keep)[:16]
    (cfg.review / "approved").mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{keep_eid}.jpg").rename(cfg.review / "approved" / f"{keep_eid}.jpg")
    summary = discover.intake(cfg)
    assert summary["intaken"] == 1
    inbox_files = {p.name for p in cfg.inbox.glob("*") if p.is_file()}
    assert "keep.mp4" in inbox_files and "drop.mp4" not in inbox_files   # only the approved original

def test_intake_is_idempotent_and_reports_missing(tmp_path, mocker):
    from fanops.config import Config
    src_dir = tmp_path / "bank"; src_dir.mkdir()
    f = src_dir / "x.mp4"; _put(f, b"X")
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.discover.probe_dimensions", return_value=(0, 0, 0.0))
    mocker.patch("fanops.discover.make_thumbnail", side_effect=lambda p, o, **k: (o.write_bytes(b"J") or True))
    discover.discover(cfg, [src_dir])
    from fanops.ingest import sha256_of
    eid = sha256_of(f)[:16]
    (cfg.review / "approved").mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{eid}.jpg").rename(cfg.review / "approved" / f"{eid}.jpg")
    assert discover.intake(cfg)["intaken"] == 1
    # second run: nothing new to intake (idempotent — not re-copied)
    assert discover.intake(cfg)["intaken"] == 0
    # a missing original is reported, not a crash
    f.unlink()
    # re-approve a fresh entry pointing at the now-missing file
    discover.discover(cfg, [src_dir])   # f is gone so nothing new; simulate a stale approved entry:
    (cfg.review / "approved" / "deadbeefdeadbeef.jpg").write_bytes(b"J")
    out = discover.intake(cfg)
    assert out["missing"] >= 1

def test_intake_keyless_manifest_entry_counts_missing_not_crash(tmp_path):
    # A manifest entry that EXISTS but lacks source_path (hand-edit / schema drift) must be reported
    # as missing — exactly like an absent entry — not crash intake() with a raw KeyError. intake is a
    # CLI verb with NO pipeline quarantine, so a KeyError here is a traceback + exit 1 over one bad
    # entry, stranding every other approved original behind it.
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    (cfg.review / "approved").mkdir(parents=True, exist_ok=True)
    (cfg.review / "manifest.json").write_text(json.dumps({"abc": {"width": 1080}}))   # no source_path
    (cfg.review / "approved" / "abc.jpg").write_bytes(b"J")
    out = discover.intake(cfg)
    assert out["missing"] == 1 and out["intaken"] == 0

def test_discover_corrupt_manifest_raises_typed_control_error(tmp_path):
    # Stage-6 audit: a truncated/hand-mangled manifest.json escaped as a raw JSONDecodeError
    # traceback (discover/intake are CLI verbs with no pipeline quarantine, and JSONDecodeError is
    # not in cli.main's catch ladder). Every other control-file reader (ledger/accounts) raises the
    # typed ControlFileError -> one clean operator line + exit 2. discover must match.
    import pytest
    from fanops.config import Config
    from fanops.errors import ControlFileError
    cfg = Config(root=tmp_path)
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / "manifest.json").write_text("{truncated")
    roots = tmp_path / "roots"; roots.mkdir()
    with pytest.raises(ControlFileError, match="manifest.json"):
        discover.discover(cfg, [roots])

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
