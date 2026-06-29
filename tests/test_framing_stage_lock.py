# tests/test_framing_stage_lock.py
"""M2 — framing.detect_window is bracketed by a per-(stage='framing', source_id) stage_lock so two
concurrent calls for the same source run the detection ONCE. Before M2 both callers raced past the
cache check (no sidecar on disk yet), both ran the OpenCV detection, the last writer won the
sidecar write, and the first run's compute was silently lost.

The fix mirrors M1's transcribe lock: enter the stage_lock, re-check the in-memory cache + on-disk
sidecar inside the lock, return early on hit. The slow OpenCV pass only ever runs once per
(source, window).

Mutation-proof: removing the stage_lock acquire from detect_window makes
test_concurrent_detect_window_runs_once fail (two detection passes observed)."""
import threading
import time

from fanops.config import Config
from fanops import framing
from fanops import keyframes as kfmod


class _Src:
    """Minimal stand-in for fanops.models.Source — detect_window only reads .id + .source_path."""

    def __init__(self, id_, source_path):
        self.id = id_
        self.source_path = source_path


def _install_stub_detector(monkeypatch, frames_emitted: int = 4):
    """Bypass cv2 / YuNet so the test runs without the [framing] extra. detect_window's contract
    is unchanged: returns {"fps": ..., "frames": [...]} stats."""
    monkeypatch.setattr(framing, "_cv2", lambda: object())                # cv2 module stub (truthy)
    monkeypatch.setattr(framing, "_detector", lambda _cv2: object())     # detector stub (truthy)
    # _detect_faces is called PER FRAME — return one centered face. The frames list comes from
    # the stubbed extract_frames_grid below.
    monkeypatch.setattr(framing, "_detect_faces",
                        lambda _cv2, _det, _fp: [(0.5, 0.5, 0.30, 0.40)])


def test_concurrent_detect_window_runs_once(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    src = _Src("src_lock_race", str(tmp_path / "src.mp4"))
    (tmp_path / "src.mp4").write_bytes(b"")

    _install_stub_detector(monkeypatch)

    # Count grid extractions — one per detect_window invocation. With the stage_lock the second
    # caller short-circuits on the sidecar the first wrote inside the critical section.
    grid_calls: list = []
    call_lock = threading.Lock()

    def slow_grid(video, start, end, *, fps, out_dir, width, source_id=None, cfg=None):
        with call_lock:
            grid_calls.append((video, start, end, fps))
        time.sleep(0.3)
        # detect_window expects a non-empty list of jpg paths to iterate _detect_faces over.
        from pathlib import Path
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)
        out = []
        for i in range(4):
            fp = p / f"grid_{i}.jpg"
            fp.write_bytes(b"")
            out.append(str(fp))
        return out

    monkeypatch.setattr(kfmod, "extract_frames_grid", slow_grid)

    results: dict[int, dict | None] = {}

    def race(tid):
        results[tid] = framing.detect_window(cfg, src, start=1.0, end=2.0)

    t1 = threading.Thread(target=race, args=(1,))
    t2 = threading.Thread(target=race, args=(2,))
    t1.start()
    time.sleep(0.05)
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    assert len(grid_calls) == 1, (
        f"concurrent detect_window for the same source ran {len(grid_calls)} detection passes — "
        f"the framing stage_lock is not closing the race")
    assert results[1] is not None and results[1] == results[2]


def test_sidecar_short_circuits_in_lock_path(tmp_path, monkeypatch):
    # A pre-existing sidecar (one window cached) makes the second-window detect_window run still
    # only spawn extraction ONCE (for the new window). Pins the in-lock re-check.
    cfg = Config(root=tmp_path)
    src = _Src("src_warm_window", str(tmp_path / "src.mp4"))
    (tmp_path / "src.mp4").write_bytes(b"")
    _install_stub_detector(monkeypatch)

    calls: list = []

    def grid(video, start, end, *, fps, out_dir, width, source_id=None, cfg=None):
        calls.append((start, end))
        from pathlib import Path
        p = Path(out_dir); p.mkdir(parents=True, exist_ok=True)
        out = []
        for i in range(2):
            fp = p / f"grid_{i}.jpg"; fp.write_bytes(b""); out.append(str(fp))
        return out

    monkeypatch.setattr(kfmod, "extract_frames_grid", grid)

    # First window populates the sidecar.
    stats_a = framing.detect_window(cfg, src, start=1.0, end=2.0)
    assert stats_a is not None
    # Second identical call — must NOT re-extract (sidecar hit).
    stats_b = framing.detect_window(cfg, src, start=1.0, end=2.0)
    assert stats_b == stats_a
    assert len(calls) == 1, f"sidecar re-check inside lock failed: {len(calls)} extractions"

    # Different window -> one more extraction.
    framing.detect_window(cfg, src, start=3.0, end=4.0)
    assert len(calls) == 2
