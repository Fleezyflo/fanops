"""Tests for trial-reels/pipeline.py live door — fail closed below 20 hooks."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))

from lib.desk import TARGET_VARIANTS, write  # noqa: E402
from pipeline import EXIT_BLOCKED, _desk_hook_texts, main, run_inbox  # noqa: E402
from tests.test_desk import _load_fixture  # noqa: E402


@pytest.fixture
def minimal_clip_mp4(tmp_path: Path) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required")
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=30",
            "-f",
            "lavfi",
            "-i",
            "sine=f=440:d=30",
            "-shortest",
            str(clip),
        ],
        check=True,
    )
    return clip


def test_twenty_hook_fixture_fills_desk_json_texts() -> None:
    desk = write(_load_fixture("clip_twenty_hooks.json"))
    filled = _desk_hook_texts(desk)

    assert desk["mode"] == "write"
    assert len(filled) == TARGET_VARIANTS
    assert len(set(filled)) == TARGET_VARIANTS


def test_english_live_fixture_blocks_below_twenty() -> None:
    desk = write(_load_fixture("clip_004ae6d9098a.json"))
    filled = _desk_hook_texts(desk)

    assert desk["mode"] == "blocked"
    assert filled == []
    assert desk["claims_found"] == 4


def test_arabic_live_fixture_blocks_below_twenty() -> None:
    desk = write(_load_fixture("clip_5a92132dc6de.json"))
    filled = _desk_hook_texts(desk)

    assert desk["mode"] == "blocked"
    assert filled == []
    assert desk["claims_found"] == 1
    assert EXIT_BLOCKED == 1


def test_run_inbox_blocks_sparse_english_live_clip(
    tmp_path: Path,
    minimal_clip_mp4: Path,
) -> None:
    in_dir = tmp_path / "in"
    out_root = tmp_path / "out"
    in_dir.mkdir()
    shutil.copy(minimal_clip_mp4, in_dir / "clip.mp4")
    transcript = _load_fixture("clip_004ae6d9098a.json")

    summary = run_inbox(
        in_dir=in_dir,
        out_root=out_root,
        transcript_map={str((in_dir / "clip.mp4").resolve()): transcript},
        dry_run=True,
    )

    assert summary["desk_ok"] is False
    assert summary["unique_hook_texts"] == 0
    assert summary["shipped"] == 0
    desk_path = out_root / "clip" / "desk.json"
    assert desk_path.is_file()
    desk = json.loads(desk_path.read_text(encoding="utf-8"))
    assert desk["mode"] == "blocked"
    assert desk.get("cards") == []


def test_run_inbox_ships_twenty_distinct_hooks_dry_run(
    tmp_path: Path,
    minimal_clip_mp4: Path,
) -> None:
    in_dir = tmp_path / "in"
    out_root = tmp_path / "out"
    in_dir.mkdir()
    shutil.copy(minimal_clip_mp4, in_dir / "clip.mp4")
    transcript = _load_fixture("clip_twenty_hooks.json")

    summary = run_inbox(
        in_dir=in_dir,
        out_root=out_root,
        transcript_map={str((in_dir / "clip.mp4").resolve()): transcript},
        dry_run=True,
    )

    assert summary["desk_ok"] is True
    assert summary["unique_hook_texts"] == TARGET_VARIANTS
    assert summary["shipped"] == TARGET_VARIANTS
    desk_path = out_root / "clip" / "desk.json"
    desk = json.loads(desk_path.read_text(encoding="utf-8"))
    assert desk["mode"] == "write"
    assert desk["unique_texts"] == TARGET_VARIANTS
    assert len({c["text"] for c in desk["cards"]}) == TARGET_VARIANTS


def test_main_exit_blocked_for_sparse_arabic(
    tmp_path: Path,
    minimal_clip_mp4: Path,
) -> None:
    in_dir = tmp_path / "in"
    out_root = tmp_path / "out"
    in_dir.mkdir()
    shutil.copy(minimal_clip_mp4, in_dir / "clip.mp4")
    transcript_path = FIXTURES / "clip_5a92132dc6de.json"

    code = main(
        [
            "--in-dir",
            str(in_dir),
            "--out",
            str(out_root),
            "--transcript",
            str(transcript_path),
            "--dry-run",
        ]
    )
    assert code == EXIT_BLOCKED
