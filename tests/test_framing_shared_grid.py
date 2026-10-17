"""C3-F01: detect_window and the speaker track share one grid cache key."""
from contextlib import contextmanager

from fanops.config import Config
from fanops.framing import _ASD_FPS, _DETECT_FPS, _FramingRuntime, _sample_detect_frames, detect_window


def test_sample_detect_frames_keeps_four_per_nine():
    frames = [f"f{i}" for i in range(9)]
    sampled = _sample_detect_frames(frames)
    assert len(sampled) == 4
    assert sampled[0] == "f0"
    assert sampled[-1] == frames[min(8, int(round(3 * _ASD_FPS / _DETECT_FPS)))]


def test_detect_window_extracts_at_track_fps(tmp_path, monkeypatch):
    seen = {}

    def fake(*_a, **k):
        seen["fps"] = k["fps"]
        return ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg", "f.jpg", "g.jpg", "h.jpg", "i.jpg"]

    @contextmanager
    def _nolock(*_a, **_k):
        yield

    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", fake)
    monkeypatch.setattr("fanops.stage_lock.stage_lock", _nolock)
    monkeypatch.setattr("fanops.framing._detect_faces", lambda *_a, **_k: [])

    class Src:
        id = "s1"
        source_path = "x.mp4"

    stats = detect_window(Config(root=tmp_path), Src(), start=0.0, end=2.0, _rt=_FramingRuntime(object(), object()))
    assert seen["fps"] == _ASD_FPS
    assert stats["fps"] == _DETECT_FPS
    assert len(stats["frames"]) == 4
