# tests/test_keyframes.py — frames are the editor's EYES. The vision-grounded hook editor judges each
# clip against what is actually on screen, so it needs real frames from the SOURCE video in the
# moment's window (clips are not rendered yet when the hookedit gate opens). Bounded + fail-open like
# vocals.isolate_vocals: a missing/unspawnable ffmpeg returns [] (degrade to text-only), never crashes.
from pathlib import Path
from fanops.keyframes import extract_keyframes

def _fake_ok(written):
    def run(cmd, **kw):
        # the output path is the last arg; "create" it so the function sees a real file
        out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"JPG")
        written.append(cmd)
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    return run

def test_extracts_count_frames_within_window(tmp_path, mocker):
    written = []
    mocker.patch("fanops.keyframes.subprocess.run", side_effect=_fake_ok(written))
    out = extract_keyframes("/src/a.mp4", 10.0, 22.0, count=3, out_dir=tmp_path)
    assert len(out) == 3 and all(Path(p).exists() for p in out)
    # each call seeks with -ss to a time strictly inside (10,22), reads ONE frame from the source
    for cmd in written:
        assert cmd[0] == "ffmpeg" and "-frames:v" in cmd and cmd[cmd.index("-i") + 1] == "/src/a.mp4"
        t = float(cmd[cmd.index("-ss") + 1])
        assert 10.0 < t < 22.0

def test_absent_ffmpeg_returns_empty_not_crash(tmp_path, mocker):
    mocker.patch("fanops.keyframes.subprocess.run", side_effect=FileNotFoundError("ffmpeg"))
    assert extract_keyframes("/src/a.mp4", 0.0, 12.0, out_dir=tmp_path) == []   # fail-open

def test_timeout_returns_empty_not_crash(tmp_path, mocker):
    import subprocess
    mocker.patch("fanops.keyframes.subprocess.run",
                 side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30))
    assert extract_keyframes("/src/a.mp4", 0.0, 12.0, out_dir=tmp_path) == []

def test_partial_failure_keeps_the_frames_that_worked(tmp_path, mocker):
    calls = {"n": 0}
    def run(cmd, **kw):
        calls["n"] += 1
        if calls["n"] == 2:                          # second frame fails (rc!=0, no file)
            class R: returncode = 1; stderr = "boom"; stdout = ""
            return R()
        out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"JPG")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.keyframes.subprocess.run", side_effect=run)
    out = extract_keyframes("/src/a.mp4", 0.0, 30.0, count=3, out_dir=tmp_path)
    assert len(out) == 2                              # the one that failed is skipped, not fatal

def test_zero_or_inverted_window_is_safe(tmp_path, mocker):
    mocker.patch("fanops.keyframes.subprocess.run", side_effect=_fake_ok([]))
    assert extract_keyframes("/src/a.mp4", 5.0, 5.0, out_dir=tmp_path) == []    # no positive window
    assert extract_keyframes("/src/a.mp4", 9.0, 3.0, out_dir=tmp_path) == []    # end<start
