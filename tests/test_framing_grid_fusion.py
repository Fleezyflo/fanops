# tests/test_framing_grid_fusion.py — C3-F01: detect_window, speaker_track (_compute_track),
# and motion_saliency share one extract_frames_grid per (source_id, window) at 9fps.
# The cache key hashes fps, so a 4fps detect pass and a 9fps track pass were two ffmpegs.
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from fanops import clip, framing, keyframes
from fanops.config import Config
from fanops.models import Source
from tests.test_keyframes_cache import _fake_ffmpeg

_START = 10.0
_END = 14.0


class _CV2:
    IMREAD_GRAYSCALE = 0

    def imread(self, _path, _flag=None):
        return None


class _Det:
    def setInputSize(self, _sz):
        return None

    def detect(self, _img):
        return 0, None


class _Frame:
    shape = (180, 320, 3)


class _FaceCV2:
    """Enough of cv2 for detect_window to turn YuNet rows into normalized faces."""
    IMREAD_GRAYSCALE = 0

    def imread(self, _path, _flag=None):
        return _Frame()


def _yunet(x: float, score: float = 0.9) -> list[float]:
    # [x, y, w, h, rEye, lEye, nose, rMouth, lMouth, score] — score is index 14.
    return [x, 40.0, 70.0, 80.0, x + 15, 55, x + 50, 55, x + 35, 70, x + 25, 100, x + 45, 100, score]


class _TwoFaceDet:
    """Equal left and right hosts. Two faces, not a PIP grid (_PIP_MIN_FACES is 3)."""

    def setInputSize(self, _sz):
        return None

    def detect(self, _img):
        return 2, [_yunet(30.0), _yunet(210.0)]


def _src(tmp_path: Path) -> Source:
    video = tmp_path / "src.mp4"
    video.write_bytes(b"")
    return Source(id="src_grid_fuse", source_path=str(video), width=1920, height=1080, duration=60.0)


def _watch(mocker):
    """Count ffmpeg subprocesses and the fps/width/source each extract_frames_grid call passes."""
    captured: list = []
    seen: list = []
    real = keyframes.extract_frames_grid

    def _spy(*args, **kwargs):
        seen.append((kwargs.get("fps"), kwargs.get("width"), kwargs.get("source_id"), kwargs.get("cfg")))
        return real(*args, **kwargs)

    mocker.patch("fanops.keyframes.subprocess.run", side_effect=_fake_ffmpeg(captured))
    mocker.patch("fanops.keyframes.extract_frames_grid", side_effect=_spy)
    return captured, seen


def _fuse(cfg, src, rt):
    stats = framing.detect_window(cfg, src, start=_START, end=_END, _rt=rt)
    framing.speaker_track(cfg, src, start=_START, end=_END, src_w=1920, src_h=1080, _rt=rt)
    framing.motion_saliency(cfg, src, start=_START, end=_END, _rt=rt)
    bare = keyframes.extract_frames_grid(
        src.source_path, _START, _END, fps=9.0,
        out_dir=str(cfg.agent_io / "bare_grid"), width=framing._KF_WIDTH,
        source_id=src.id, cfg=cfg)
    return stats, bare


def _assert_one_grid(captured, seen, cfg, src):
    want = (9.0, framing._KF_WIDTH, src.id, cfg)
    assert seen == [want, want, want, want], seen
    assert len(captured) == 1, (
        f"expected ONE grid extract for detect + track + saliency + bare 9fps, got {len(captured)}")
    assert _vf(captured[0]) == f"fps=9.0,scale={framing._KF_WIDTH}:-2"


def _vf(cmd: list) -> str:
    return cmd[cmd.index("-vf") + 1]


def test_cold_window_one_grid_extract(tmp_path, mocker):
    assert framing._FRAMING_GRID_FPS == framing._ASD_FPS == 9.0
    cfg = Config(root=tmp_path)
    src = _src(tmp_path)
    captured, seen = _watch(mocker)
    stats, bare = _fuse(cfg, src, framing._FramingRuntime(_CV2(), _Det()))
    assert stats is not None
    assert stats["fps"] == framing._FRAMING_GRID_FPS
    assert bare
    _assert_one_grid(captured, seen, cfg, src)


def test_stale_v3_detect_sidecar_recomputes_on_one_grid(tmp_path, mocker):
    assert framing._DETECT_V == 4
    cfg = Config(root=tmp_path)
    src = _src(tmp_path)
    key = f"{round(_START, 2)}-{round(_END, 2)}"
    stale = {"fps": 4.0, "stale": True, "frames": [[[0.2, 0.3, 0.2, 0.25, 0.9, 0.1]]]}
    path = framing._detect_sidecar(cfg, src.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"v": 3, "windows": {key: stale}}))
    assert framing._load_detect_cache(path) == {}
    captured, seen = _watch(mocker)
    stats, bare = _fuse(cfg, src, framing._FramingRuntime(_CV2(), _Det()))
    assert stats is not None and "stale" not in stats
    assert stats["fps"] == framing._FRAMING_GRID_FPS
    assert stats["frames"] and all(fr == [] for fr in stats["frames"])
    on_disk = json.loads(path.read_text())
    assert on_disk["v"] == framing._DETECT_V
    assert on_disk["windows"][key]["fps"] == framing._FRAMING_GRID_FPS
    assert bare
    _assert_one_grid(captured, seen, cfg, src)


def test_ct_multi_resolve_extracts_one_grid(tmp_path, mocker, monkeypatch):
    """Production path: clip._resolve_framing → framing._resolve → CT_MULTI → speaker_track.

    The direct _fuse harness calls the three extractors itself. This one lets the router
    do it, and counts fanops.keyframes.subprocess.run the same way _watch does.
    """
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "1")
    cfg = Config(root=tmp_path)
    assert cfg.smart_framing
    src = _src(tmp_path)
    captured, seen = _watch(mocker)
    mocker.patch(
        "fanops.framing._framing_runtime_or_raise",
        return_value=framing._FramingRuntime(_FaceCV2(), _TwoFaceDet()),
    )
    mocker.patch("fanops.framing._window_has_speech", return_value=True)
    classified: list[str] = []
    real_classify = framing.classify_window

    def _spy_classify(*args, **kwargs):
        ct = real_classify(*args, **kwargs)
        classified.append(ct)
        return ct

    tracked: list[int] = []
    real_track = framing.speaker_track

    def _spy_track(*args, **kwargs):
        tracked.append(1)
        return real_track(*args, **kwargs)

    mocker.patch("fanops.framing.classify_window", side_effect=_spy_classify)
    mocker.patch("fanops.framing.speaker_track", side_effect=_spy_track)
    clip._resolve_framing(cfg, src, _START, _END)
    assert classified == [framing.CT_MULTI], classified
    assert tracked == [1]
    assert seen, "extract_frames_grid was not called"
    assert all(row[0] == framing._FRAMING_GRID_FPS == 9.0 for row in seen), seen
    assert all(row[1] == framing._KF_WIDTH and row[2] == src.id and row[3] is cfg for row in seen), seen
    assert len(captured) == 1, (
        f"CT_MULTI owed one ffmpeg for detect + track, got {len(captured)}")
    assert "fps=9.0" in _vf(captured[0])
    assert "fps=4" not in _vf(captured[0])


def _real_multi_src(tmp_path: Path) -> Source:
    """A decodable clip long enough for the 10s–14s window. Faces come from the stubbed detector."""
    video = tmp_path / "multi.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:d=20:r=30",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
        check=True, capture_output=True, text=True, timeout=60,
    )
    return Source(id="src_grid_real", source_path=str(video), width=640, height=360, duration=20.0)


@pytest.mark.integration
def test_ct_multi_resolve_one_real_ffmpeg(tmp_path, mocker, monkeypatch):
    """Same CT_MULTI router as the stub count, with the real ffmpeg binary.

    `test_ct_multi_resolve_extracts_one_grid` replaces `subprocess.run`. This one lets that
    call reach ffmpeg, then requires a single `fps=9.0` grid for detect and speaker_track.
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH")
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "1")
    cfg = Config(root=tmp_path)
    src = _real_multi_src(tmp_path)
    captured: list = []
    seen: list = []
    real_run = keyframes.subprocess.run
    real_extract = keyframes.extract_frames_grid

    def _count(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "ffmpeg":
            captured.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    def _spy(*args, **kwargs):
        seen.append((kwargs.get("fps"), kwargs.get("width"), kwargs.get("source_id"), kwargs.get("cfg")))
        return real_extract(*args, **kwargs)

    mocker.patch("fanops.keyframes.subprocess.run", side_effect=_count)
    mocker.patch("fanops.keyframes.extract_frames_grid", side_effect=_spy)
    mocker.patch(
        "fanops.framing._framing_runtime_or_raise",
        return_value=framing._FramingRuntime(_FaceCV2(), _TwoFaceDet()),
    )
    mocker.patch("fanops.framing._window_has_speech", return_value=True)
    clip._resolve_framing(cfg, src, _START, _END)
    assert seen, "extract_frames_grid was not called"
    assert all(row == (framing._FRAMING_GRID_FPS, framing._KF_WIDTH, src.id, cfg) for row in seen), seen
    assert len(captured) == 1, captured
    assert "fps=9.0" in _vf(captured[0])
    assert "fps=4" not in _vf(captured[0])
    assert captured[0][0] == "ffmpeg"
