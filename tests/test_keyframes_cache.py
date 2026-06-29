# tests/test_keyframes_cache.py
"""M2 — keyframes.extract_frames_grid grows a content-addressed cache so the same window over the
same source extracts ONCE, not 3× (the framing pipeline calls it from detect_window, speaker_track,
and motion_saliency on the same [start,end] — today each spawns its own ffmpeg).

The cache key hashes (source_id, t0, t1, fps, count) so a re-extract is impossible unless one of
those changes. The cache lives at <agent_io>/keyframes/<source_id>/<window_hash>/ — content-
addressed, mirroring clip._render_fingerprint's pattern.

Mutation-proof: removing the file-exists short-circuit in extract_frames_grid_cached makes
test_same_window_extracts_once fail (ffmpeg invoked twice on identical inputs)."""
import hashlib
import threading
import time
from pathlib import Path

from fanops.config import Config
from fanops.keyframes import extract_frames_grid, _window_cache_key, _cache_dir_for


def _fake_ffmpeg(captured: list, frames_to_emit: int = 4):
    """Build a fake subprocess.run that records the invocation, writes `frames_to_emit` jpgs to
    the pattern dir, and returns rc=0. Lets the test count real subprocess calls without spawning
    ffmpeg."""

    def fake_run(cmd, **kw):
        captured.append(list(cmd))
        # Mimic ffmpeg: write numbered jpgs matching the pattern arg (last token).
        pattern = Path(cmd[-1])
        out_dir = pattern.parent
        prefix = pattern.name.split("_%")[0] if "_%" in pattern.name else pattern.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, frames_to_emit + 1):
            (out_dir / f"{prefix}_{i:05d}.jpg").write_bytes(b"\xff\xd8\xff\xe0fake")

        class R:
            returncode = 0
            stderr = ""
            stdout = ""
        return R()
    return fake_run


def test_window_cache_key_is_content_addressed_and_stable():
    # Same (source, window, fps, width) -> same key. Any change -> different key. Stable across
    # processes (deterministic sha256 of a stable tuple, no salt).
    k1 = _window_cache_key(source_id="src_x", start=1.0, end=2.5, fps=4.0, width=960)
    k2 = _window_cache_key(source_id="src_x", start=1.0, end=2.5, fps=4.0, width=960)
    assert k1 == k2
    assert _window_cache_key(source_id="src_y", start=1.0, end=2.5, fps=4.0, width=960) != k1
    assert _window_cache_key(source_id="src_x", start=1.5, end=2.5, fps=4.0, width=960) != k1
    assert _window_cache_key(source_id="src_x", start=1.0, end=2.5, fps=2.0, width=960) != k1
    assert _window_cache_key(source_id="src_x", start=1.0, end=2.5, fps=4.0, width=480) != k1
    # Length sanity — sha256 hex is 64 chars.
    assert len(k1) == 64
    int(k1, 16)                                            # hex parse must succeed


def test_cache_dir_lives_under_agent_io_keyframes(tmp_path):
    cfg = Config(root=tmp_path)
    k = hashlib.sha256(b"k").hexdigest()
    d = _cache_dir_for(cfg, source_id="src_z", window_hash=k)
    assert d == cfg.agent_io / "keyframes" / "src_z" / k


def test_same_window_extracts_once(tmp_path, mocker):
    # Two calls with the SAME (source_id, start, end, fps, width) -> ONE ffmpeg subprocess; the
    # second call short-circuits on the cache dir written by the first. This is the bug the M2
    # cache closes: detect_window + speaker_track + motion_saliency all hit the same window today.
    cfg = Config(root=tmp_path)
    captured: list = []
    mocker.patch("fanops.keyframes.subprocess.run", side_effect=_fake_ffmpeg(captured))

    src_video = tmp_path / "src.mp4"
    src_video.write_bytes(b"")
    out_dir = tmp_path / "tmp_grid"

    frames_a = extract_frames_grid(str(src_video), 1.0, 2.0, fps=4.0,
                                   out_dir=str(out_dir), width=960,
                                   source_id="src_cache_a", cfg=cfg)
    frames_b = extract_frames_grid(str(src_video), 1.0, 2.0, fps=4.0,
                                   out_dir=str(out_dir), width=960,
                                   source_id="src_cache_a", cfg=cfg)
    assert len(captured) == 1, (
        f"expected ONE ffmpeg call (cache short-circuit), got {len(captured)} — keyframes cache "
        f"is not closing the re-extract loop")
    assert frames_a and frames_b
    # Second call returns the SAME paths the first emitted (same cache dir).
    assert frames_a == frames_b


def test_different_window_extracts_separately(tmp_path, mocker):
    # Different window -> different cache key -> separate extract; the cache is per-window, not
    # per-source.
    cfg = Config(root=tmp_path)
    captured: list = []
    mocker.patch("fanops.keyframes.subprocess.run", side_effect=_fake_ffmpeg(captured))
    src_video = tmp_path / "src.mp4"
    src_video.write_bytes(b"")
    out_dir = tmp_path / "tmp_grid"
    extract_frames_grid(str(src_video), 1.0, 2.0, fps=4.0, out_dir=str(out_dir),
                        width=960, source_id="src_window_x", cfg=cfg)
    extract_frames_grid(str(src_video), 5.0, 6.0, fps=4.0, out_dir=str(out_dir),
                        width=960, source_id="src_window_x", cfg=cfg)
    assert len(captured) == 2


def test_no_source_id_is_backward_compatible(tmp_path, mocker):
    # When the caller doesn't pass source_id, behaviour is byte-identical to the pre-M2 path:
    # one ffmpeg per call, no cache lookup, no short-circuit. The cache is OPT-IN.
    captured: list = []
    mocker.patch("fanops.keyframes.subprocess.run", side_effect=_fake_ffmpeg(captured))
    src_video = tmp_path / "src.mp4"
    src_video.write_bytes(b"")
    out_dir = tmp_path / "tmp_grid"
    extract_frames_grid(str(src_video), 1.0, 2.0, fps=4.0, out_dir=str(out_dir), width=960)
    extract_frames_grid(str(src_video), 1.0, 2.0, fps=4.0, out_dir=str(out_dir), width=960)
    assert len(captured) == 2, "no source_id should mean no cache (back-compat)"


def test_concurrent_callers_extract_once(tmp_path, mocker):
    # Two threads racing the same (source, window) -> still ONE ffmpeg subprocess. The cache is
    # the data side of the M2 contract; the stage_lock provides the producer-side mutex (a slow
    # ffmpeg is bracketed by the lock so the second producer waits and finds the cache).
    cfg = Config(root=tmp_path)
    captured: list = []
    call_lock = threading.Lock()

    def slow_fake(cmd, **kw):
        with call_lock:
            captured.append(list(cmd))
        time.sleep(0.3)
        pattern = Path(cmd[-1])
        out_dir = pattern.parent
        prefix = pattern.name.split("_%")[0]
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, 5):
            (out_dir / f"{prefix}_{i:05d}.jpg").write_bytes(b"\xff\xd8\xff\xe0fake")

        class R:
            returncode = 0
            stderr = ""
            stdout = ""
        return R()

    mocker.patch("fanops.keyframes.subprocess.run", side_effect=slow_fake)
    src_video = tmp_path / "src.mp4"
    src_video.write_bytes(b"")
    out_dir = tmp_path / "tmp_grid"

    results: dict[int, list[str]] = {}

    def race(tid):
        results[tid] = extract_frames_grid(str(src_video), 1.0, 2.0, fps=4.0,
                                           out_dir=str(out_dir), width=960,
                                           source_id="src_concurrent", cfg=cfg)

    t1 = threading.Thread(target=race, args=(1,))
    t2 = threading.Thread(target=race, args=(2,))
    t1.start()
    time.sleep(0.05)
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)
    assert len(captured) == 1, (
        f"concurrent same-window callers spawned {len(captured)} ffmpegs — keyframes stage_lock "
        f"is not mutexing the producers")
    assert results[1] == results[2]
