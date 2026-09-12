# tests/test_keyframe_collision.py — WS4: extract_frames_grid cache dirs are keyed on (source, window)
# so two sources sharing a start, or one source with two ends, cannot clobber the same tmp path.
from pathlib import Path

from fanops.config import Config
from fanops.keyframes import extract_frames_grid


def _fake_grid(cmd, **kw):
    pattern = Path(cmd[-1])
    out_dir = pattern.parent
    prefix = pattern.name.split("_%")[0] if "_%" in pattern.name else pattern.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{prefix}_00001.jpg").write_bytes(b"\xff\xd8\xff\xe0fake")
    (out_dir / ".complete").write_text("1")

    class R:
        returncode = 0
        stderr = ""
        stdout = ""
    return R()


def test_keyframe_tmp_dir_is_unique_per_source(tmp_path, mocker):
    captured: list = []

    def run(cmd, **kw):
        captured.append(str(Path(cmd[-1]).parent))
        return _fake_grid(cmd, **kw)

    mocker.patch("fanops.keyframes.subprocess.run", side_effect=run)
    cfg = Config(root=tmp_path)
    src = tmp_path / "v.mp4"
    src.write_bytes(b"")
    extract_frames_grid(str(src), 0.0, 7.0, fps=4.0, out_dir=str(tmp_path / "unused"),
                        width=960, source_id="src_a", cfg=cfg)
    extract_frames_grid(str(src), 0.0, 7.0, fps=4.0, out_dir=str(tmp_path / "unused"),
                        width=960, source_id="src_b", cfg=cfg)
    assert len(captured) == 2
    assert captured[0] != captured[1], "two sources sharing a window get the SAME tmp dir -> concurrent clobber/unlink race"
    assert "src_a" in captured[0] and "src_b" in captured[1]


def test_keyframe_tmp_dir_is_unique_per_window(tmp_path, mocker):
    captured: list = []

    def run(cmd, **kw):
        captured.append(str(Path(cmd[-1]).parent))
        return _fake_grid(cmd, **kw)

    mocker.patch("fanops.keyframes.subprocess.run", side_effect=run)
    cfg = Config(root=tmp_path)
    src = tmp_path / "v.mp4"
    src.write_bytes(b"")
    extract_frames_grid(str(src), 0.0, 7.0, fps=4.0, out_dir=str(tmp_path / "unused"),
                        width=960, source_id="src_a", cfg=cfg)
    extract_frames_grid(str(src), 0.0, 12.0, fps=4.0, out_dir=str(tmp_path / "unused"),
                        width=960, source_id="src_a", cfg=cfg)
    assert captured[0] != captured[1], "same source, different windows must not share a tmp dir"
