"""Tests for trial-reels/pipeline.py live door — no five-unique abort."""

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
from pipeline import EXIT_BLOCKED, EXIT_OK, _desk_hook_texts, main, run_inbox  # noqa: E402
from tests.test_desk import _load_fixture  # noqa: E402


def test_english_clause_treatments_expand_to_twenty_cards() -> None:
    desk = write(_load_fixture("clip_004ae6d9098a.json"))
    filled = _desk_hook_texts(desk)

    assert desk["mode"] == "write"
    assert len(filled) >= 8
    assert len(set(filled)) == len(filled)
    assert len(desk["cards"]) == TARGET_VARIANTS
    assert len({card["text"] for card in desk["cards"]}) == len(filled)


def test_arabic_two_claims_do_not_abort_low_unique_count() -> None:
    desk = write(_load_fixture("clip_5a92132dc6de.json"))
    filled = _desk_hook_texts(desk)

    assert len(filled) == 2
    assert len(set(filled)) == 2
    assert len(filled) < 5
    assert len(set(filled)) < 5
    assert EXIT_OK == 0


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


def test_run_inbox_ships_sparse_arabic_without_five_unique_gate(
    tmp_path: Path,
    minimal_clip_mp4: Path,
) -> None:
    in_dir = tmp_path / "in"
    out_root = tmp_path / "out"
    in_dir.mkdir()
    clip = in_dir / "clip.mp4"
    clip.write_bytes(minimal_clip_mp4.read_bytes())

    transcript = json.loads((FIXTURES / "clip_5a92132dc6de.json").read_text(encoding="utf-8"))
    summary = run_inbox(
        in_dir=in_dir,
        out_root=out_root,
        transcript_map={str(clip.resolve()): transcript},
        dry_run=True,
    )

    assert summary["clips"] == 1
    assert summary["shipped"] == TARGET_VARIANTS
    assert summary["unique_hook_texts"] == 2
    assert summary["honest_ship"]
    assert (out_root / "clip" / "desk.json").exists()


def test_pipeline_main_exits_ok_for_sparse_arabic(
    tmp_path: Path,
    minimal_clip_mp4: Path,
) -> None:
    in_dir = tmp_path / "in"
    out_root = tmp_path / "out"
    in_dir.mkdir()
    clip = in_dir / "clip.mp4"
    clip.write_bytes(minimal_clip_mp4.read_bytes())
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

    assert code == EXIT_OK


def test_pipeline_main_blocks_credit_only(tmp_path: Path, minimal_clip_mp4: Path) -> None:
    in_dir = tmp_path / "in"
    out_root = tmp_path / "out"
    in_dir.mkdir()
    clip = in_dir / "clip.mp4"
    clip.write_bytes(minimal_clip_mp4.read_bytes())
    transcript_path = tmp_path / "credit.json"
    transcript_path.write_text(
        json.dumps({"language": "ar", "lines": [{"start": 0.0, "text": "ترجمة نانسي قنقر"}]}),
        encoding="utf-8",
    )

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
