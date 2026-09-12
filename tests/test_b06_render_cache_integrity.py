# tests/test_b06_render_cache_integrity.py — B06: render race + keyframes/framing cache integrity.
"""Regression bundle for H06/M10/M11/M12/M13: stage_lock on render, .complete grid marker,
frame persistence after detect_window, no-None sidecar writes, kf filename collision fix,
and a mutation-proof grep guard on framing grid cleanup."""
import re
import threading
import time
from pathlib import Path
import pytest

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, MomentState, Fmt
from fanops.clip import render_moment
from fanops.keyframes import extract_frames_grid, extract_keyframes, _window_cache_key, _cache_dir_for


@pytest.fixture(autouse=True)
def _hermetic_render_env(monkeypatch):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")


def _seed_moment(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=10, end=28, reason="r", state=MomentState.decided, hook=None))
    return cfg, led


def test_concurrent_render_moment_runs_one_ffmpeg(tmp_path, mocker):
    # Two threads racing the same content-addressed cid -> ONE ffmpeg subprocess. stage_lock must
    # mutex the render producers; the second acquirer re-checks inside the lock and adopts.
    cfg, led = _seed_moment(tmp_path)
    captured: list = []
    call_lock = threading.Lock()

    def slow_fake(cmd, **kw):
        with call_lock:
            captured.append(list(cmd))
        time.sleep(0.3)
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"CLIP")
        class R:
            returncode = 0
            stderr = ""
            stdout = ""
        return R()

    mocker.patch("fanops.clip.subprocess.run", side_effect=slow_fake)
    results: dict[int, object] = {}

    def race(tid):
        _, c = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
        results[tid] = c

    t1 = threading.Thread(target=race, args=(1,))
    t2 = threading.Thread(target=race, args=(2,))
    t1.start()
    time.sleep(0.05)
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)
    assert len(captured) == 1, (
        f"concurrent render_moment spawned {len(captured)} ffmpegs — render stage_lock "
        f"is not mutexing the producers")
    assert results[1].id == results[2].id


def test_partial_grid_without_complete_marker_reextracts(tmp_path, mocker):
    # An incomplete grid dir (jpgs present but no .complete) must NOT cache-hit — ffmpeg re-runs.
    cfg = Config(root=tmp_path)
    captured: list = []

    def fake_run(cmd, **kw):
        captured.append(list(cmd))
        pattern = Path(cmd[-1])
        out_dir = pattern.parent
        prefix = pattern.name.split("_%")[0]
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, 4):
            (out_dir / f"{prefix}_{i:05d}.jpg").write_bytes(b"\xff\xd8\xff\xe0fake")
        (out_dir / ".complete").write_text("1")
        class R:
            returncode = 0
            stderr = ""
            stdout = ""
        return R()

    mocker.patch("fanops.keyframes.subprocess.run", side_effect=fake_run)
    src_video = tmp_path / "src.mp4"
    src_video.write_bytes(b"")
    whash = _window_cache_key(source_id="src_partial", start=1.0, end=2.0, fps=4.0, width=960)
    cache_dir = _cache_dir_for(cfg, source_id="src_partial", window_hash=whash)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "grid_100_00001.jpg").write_bytes(b"\xff\xd8\xff\xe0partial")
    # deliberately NO .complete marker

    extract_frames_grid(str(src_video), 1.0, 2.0, fps=4.0, out_dir=str(tmp_path / "unused"),
                        width=960, source_id="src_partial", cfg=cfg)
    assert len(captured) == 1, "partial grid without .complete must re-extract, not cache-hit"
    assert (cache_dir / ".complete").exists()


def test_keyframe_filename_includes_end_to_avoid_collision(tmp_path, mocker):
    captured: list = []

    def fake_run(cmd, **kw):
        captured.append(cmd[-1])
        dst = Path(cmd[-1])
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"\xff\xd8\xff\xe0fake")
        class R:
            returncode = 0
            stderr = ""
            stdout = ""
        return R()

    mocker.patch("fanops.keyframes.subprocess.run", side_effect=fake_run)
    out_dir = tmp_path / "kf_out"
    paths_a = extract_keyframes(str(tmp_path / "v.mp4"), 0.0, 7.0, count=1, out_dir=out_dir)
    paths_b = extract_keyframes(str(tmp_path / "v.mp4"), 0.0, 12.0, count=1, out_dir=out_dir)
    assert paths_a and paths_b
    assert paths_a[0] != paths_b[0], "same start different end must produce disjoint kf filenames"
    assert "kf_0_700_0.jpg" in paths_a[0]
    assert "kf_0_1200_0.jpg" in paths_b[0]


def test_framing_py_has_no_grid_unlink():
    # Mutation-proof guard: M11 deletes all grid_* cleanup in framing.py — re-adding it breaks CI.
    text = Path("src/fanops/framing.py").read_text(encoding="utf-8")
    assert not re.search(r"unlink.*grid_|grid_.*unlink", text), (
        "framing.py must not unlink grid_* frames — they belong in the keyframes cache (M11)")

