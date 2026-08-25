"""Tests for the trial-reels runner orchestration."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lib.desk import write
from lib.runner import (
    build_hook_ass_events,
    load_recipes,
    normalize_transcript,
    plan_variants,
    run_one_clip,
)

ROOT = Path(__file__).resolve().parents[1]

MULTILINE_AR = {
    "language": "ar",
    "lines": [
        {"start": 1.0, "text": "لك كفاية عزبتني يا حبيبي الغالي"},
        {"start": 5.0, "text": "قلبي مشتاق وحنيني كبير في الليل"},
        {"start": 10.0, "text": "يا حبيبي رجعت لك من جديد"},
    ],
}

SINGLE_LINE_AR = {
    "language": "ar",
    "lines": [{"start": 12.4, "text": "لك كفاية عزبتني"}],
}


def test_normalize_transcript_from_segments():
    payload = normalize_transcript(
        {
            "language": "en",
            "segments": [{"start": 1.0, "text": "hello world"}],
        }
    )
    assert payload["lines"][0]["text"] == "hello world"


def test_plan_variants_yields_up_to_twenty():
    desk = write(MULTILINE_AR)
    assert desk["mode"] == "write"
    recipes = load_recipes()
    plans = plan_variants(desk, total_duration_s=30.0, recipes=recipes, clip_stem="clip")
    assert len(plans) == 20
    assert len({p.output_name for p in plans}) == 20


def test_plan_variants_empty_when_desk_blocked():
    desk = write(SINGLE_LINE_AR)
    assert desk["mode"] == "blocked"
    plans = plan_variants(desk, total_duration_s=30.0, recipes=load_recipes(), clip_stem="clip")
    assert plans == []


def test_build_hook_ass_events_uses_card_text_only():
    card = {"text": "لك كفاية عزبتني", "cite": {"start": 1.0}}
    events = build_hook_ass_events(
        card,
        cut_start_s=1.0,
        cut_length_s=8.0,
        hook_in_s=1.0,
        hook_out_s=2.44,
        stack="punch_cuts",
        rehooks_s=(3, 8),
    )
    assert events
    assert all(ev["text"] == "لك كفاية عزبتني" for ev in events)
    assert "عذبتيني" not in json.dumps(events, ensure_ascii=False)


def test_blocked_desk_does_not_render(tmp_path: Path):
    video = tmp_path / "src.mp4"
    _make_test_video(video, duration_s=15.0)
    result = run_one_clip(
        video,
        transcript=SINGLE_LINE_AR,
        out_dir=tmp_path / "out",
    )
    assert result.blocked
    assert result.variants_rendered == 0
    assert result.outputs == []
    assert not list((tmp_path / "out").glob("*.mp4"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_run_one_clip_renders_variants(tmp_path: Path):
    video = tmp_path / "src.mp4"
    _make_test_video(video, duration_s=30.0)
    result = run_one_clip(
        video,
        transcript=MULTILINE_AR,
        out_dir=tmp_path / "out",
    )
    assert not result.blocked, result.message
    assert result.variants_rendered >= 15
    assert result.variants_rendered <= 20
    for path in result.outputs:
        assert path.exists()
        assert path.stat().st_size > 0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_cli_dry_run(tmp_path: Path):
    video = tmp_path / "clip.mp4"
    transcript = tmp_path / "transcript.json"
    video.write_bytes(b"x")  # overwritten below
    _make_test_video(video, duration_s=30.0)
    transcript.write_text(json.dumps(MULTILINE_AR), encoding="utf-8")

    cmd = [
        sys.executable,
        str(ROOT / "__main__.py"),
        "run",
        "--file",
        str(video),
        "--transcript",
        str(transcript),
        "--out",
        str(tmp_path / "out"),
        "--dry-run",
    ]
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "planned=20" in proc.stdout


def _make_test_video(path: Path, *, duration_s: float) -> None:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x6B00A8:s=1080x1920:d={duration_s:.3f}",
        "-f",
        "lavfi",
        "-i",
        f"sine=f=440:d={duration_s:.3f}",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(path),
    ]
    subprocess.run(cmd, check=True)
