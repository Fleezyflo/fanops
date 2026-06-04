import json
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
