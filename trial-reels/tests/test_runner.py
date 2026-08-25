"""Integration tests for the trial-reels runner."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lib.desk import TARGET_VARIANTS
from lib.runner import (
    build_ass_events,
    load_or_transcribe,
    run_clip,
)
from lib.hooks import LyricEvent, hook_window

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")


AR_MULTILINE_TRANSCRIPT = {
    "language": "ar",
    "segments": [
        {"start": 1.0, "end": 4.0, "text": "لك كفاية عزبتني يا حبيبي الغالي"},
        {"start": 5.0, "end": 9.0, "text": "قلبي مشتاق وحنيني كبير في الليل"},
        {"start": 10.0, "end": 14.0, "text": "يا حبيبي رجعت لك من جديد"},
        {"start": 15.0, "end": 19.0, "text": "والله ما نسيتك يا غالي يا روحي"},
    ],
}

EN_MULTILINE_TRANSCRIPT = {
    "language": "en",
    "segments": [
        {
            "start": 0.0,
            "end": 4.0,
            "text": "genuine street ties actually accept the moves from behind the scenes",
        },
        {
            "start": 5.0,
            "end": 9.0,
            "text": "execute moves on the streets with genuine respect and power",
        },
        {
            "start": 10.0,
            "end": 14.0,
            "text": "the modern corporate world fails so the next layer matters",
        },
        {
            "start": 15.0,
            "end": 19.0,
            "text": "you see the streets accept genuine power when you listen",
        },
    ],
}


def _make_source_video(path: Path, duration_s: float = 30.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
            f"color=c=0x6B00A8:s=1280x720:d={duration_s:.3f}",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=44100:cl=mono:d={duration_s:.3f}",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )


def test_build_ass_events_includes_hook_and_rehooks() -> None:
    cite = 10.0
    window = hook_window("result_first", cite_start_s=cite, total_duration_s=30.0)
    events = build_ass_events(
        hook_text="لك كفاية عزبتني",
        hook_in_s=window.hook_in_s,
        hook_out_s=window.hook_out_s,
        cite_start_s=cite,
        cut_length_s=8.0,
        lyric_events=[LyricEvent(11.0, 12.0, "لك كفاية عزبتني")],
        stack="punch_cuts",
        rehooks_s=(3, 8),
    )
    texts = [e["text"] for e in events]
    assert "لك كفاية عزبتني" in texts
    assert any(e["start"] == 3.0 for e in events)


def test_load_transcript_from_work_dir(tmp_path: Path) -> None:
    src = tmp_path / "clip.mp4"
    _make_source_video(src, duration_s=5.0)
    work = tmp_path / "work"
    work.mkdir()
    transcript = {"language": "en", "segments": [{"start": 0, "end": 1, "text": "hello world"}]}
    (work / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
    loaded = load_or_transcribe(src, work, allow_asr=False)
    assert loaded["language"] == "en"


@pytest.mark.parametrize("expected_min", [TARGET_VARIANTS])
def test_run_clip_ships_vertical_variants(
    tmp_path: Path,
    expected_min: int,
) -> None:
    transcript = AR_MULTILINE_TRANSCRIPT
    src = tmp_path / "source.mp4"
    _make_source_video(src, duration_s=30.0)
    work = tmp_path / "work"
    out = tmp_path / "out"
    transcript_path = work / "transcript.json"
    work.mkdir()
    transcript_path.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")

    recipes = json.loads((Path(__file__).resolve().parents[1] / "recipes.json").read_text())
    result = run_clip(
        src,
        out_dir=out,
        work_dir=work,
        recipes=recipes,
        transcript_path=transcript_path,
        run_cover_qa=False,
    )

    assert not result.blocked
    assert result.validation["ok"]
    ok_variants = [v for v in result.variants if v.ok]
    assert len(ok_variants) >= expected_min
    assert len(ok_variants) <= 20

    sample = ok_variants[0]
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(sample.output_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    width, height = probe.stdout.strip().split(",")
    assert width == "1080"
    assert height == "1920"

    hook_texts = [variant.hook_text for variant in ok_variants]
    assert len(ok_variants) == TARGET_VARIANTS
    if transcript["language"] == "ar":
        assert all("عذبتيني" not in text for text in hook_texts)
    else:
        assert all(len(text.split()) >= 4 or text.endswith(".") for text in set(hook_texts))


def test_run_clip_ships_single_line_arabic_via_stack_expansion(tmp_path: Path) -> None:
    src = tmp_path / "source.mp4"
    _make_source_video(src, duration_s=30.0)
    work = tmp_path / "work"
    out = tmp_path / "out"
    work.mkdir()
    transcript = {
        "language": "ar",
        "segments": [{"start": 12.4, "end": 14.0, "text": "لك كفاية عزبتني"}],
    }
    transcript_path = work / "transcript.json"
    transcript_path.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
    recipes = json.loads((Path(__file__).resolve().parents[1] / "recipes.json").read_text())

    result = run_clip(
        src,
        out_dir=out,
        work_dir=work,
        recipes=recipes,
        transcript_path=transcript_path,
        run_cover_qa=False,
    )

    assert not result.blocked
    assert result.validation["ok"]
    ok_variants = [v for v in result.variants if v.ok]
    assert len(ok_variants) == TARGET_VARIANTS
    assert {v.hook_text for v in ok_variants} == {"لك كفاية عزبتني"}
    assert len({v.stack for v in ok_variants}) >= 2


def test_run_clip_blocks_credit_only(tmp_path: Path) -> None:
    src = tmp_path / "source.mp4"
    _make_source_video(src, duration_s=30.0)
    work = tmp_path / "work"
    out = tmp_path / "out"
    work.mkdir()
    transcript = {
        "language": "ar",
        "segments": [{"start": 0.0, "end": 1.0, "text": "ترجمة نانسي قنقر"}],
    }
    transcript_path = work / "transcript.json"
    transcript_path.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
    recipes = json.loads((Path(__file__).resolve().parents[1] / "recipes.json").read_text())

    result = run_clip(
        src,
        out_dir=out,
        work_dir=work,
        recipes=recipes,
        transcript_path=transcript_path,
        run_cover_qa=False,
    )

    assert result.blocked
    assert sum(1 for v in result.variants if v.ok) == 0


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract required")
def test_run_clip_verifies_each_rendered_cover_against_card_text(tmp_path: Path) -> None:
    """Each rendered variant's hook card is OCR-checked on its actual cover frame."""
    src = tmp_path / "source.mp4"
    _make_source_video(src, duration_s=30.0)
    work = tmp_path / "work"
    out = tmp_path / "out"
    work.mkdir()
    transcript = {
        "language": "ar",
        "segments": [{"start": 12.4, "end": 14.0, "text": "لك كفاية عزبتني"}],
    }
    transcript_path = work / "transcript.json"
    transcript_path.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
    recipes = json.loads((Path(__file__).resolve().parents[1] / "recipes.json").read_text())

    result = run_clip(
        src,
        out_dir=out,
        work_dir=work,
        recipes=recipes,
        transcript_path=transcript_path,
        run_cover_qa=True,
    )

    checked = [v for v in result.variants if v.cover_ok is not None]
    assert len(checked) == TARGET_VARIANTS
    assert all(v.output_path.is_file() for v in checked)
    assert all(v.hook_text == "لك كفاية عزبتني" for v in checked)
    cover_pass = sum(1 for v in checked if v.cover_ok)
    assert cover_pass == TARGET_VARIANTS, (
        f"cover OCR {cover_pass}/{len(checked)} — "
        + ", ".join(v.output_path.name for v in checked if not v.cover_ok)
    )
    assert sum(1 for v in result.variants if v.ok) == TARGET_VARIANTS
    assert result.score is not None
    assert result.score.cover_pass == TARGET_VARIANTS
    assert result.score.cover_checked == TARGET_VARIANTS
    assert not any(v.ok and v.cover_ok is False for v in result.variants)
