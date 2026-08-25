"""Tests for the trial-reels runner (planning + desk integration; ffmpeg optional)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.desk import TARGET_VARIANTS, write  # noqa: E402
from lib.desk_swarm import validate_desk_result  # noqa: E402
from lib.runner import (  # noqa: E402
    build_ass_events,
    plan_variants,
    transcript_from_segments,
)
from tests.test_desk import AR_CLIP_5A92132DC6DE, AR_MULTILINE, EN_CLIP_004AE6D9098A  # noqa: E402


EN_NOTEBOOK = {
    "language": "en",
    "lines": [
        {"start": 0.0, "text": "fails."},
        {"start": 0.5, "text": "So the next"},
        {"start": 1.0, "text": "the respect that the modern corporate in"},
        {"start": 2.0, "text": "genuine street ties actually accept."},
    ],
}


def test_plan_variants_yields_twenty_for_arabic_sung_line() -> None:
    desk = write(AR_CLIP_5A92132DC6DE)
    assert desk["mode"] == "write"
    plans = plan_variants(desk, clip_id="clip_5a92132dc6de", source_duration_s=30.0)
    assert len(plans) == TARGET_VARIANTS
    assert len({p.hook for p in plans}) == 5
    assert len({p.stack for p in plans}) == 4
    assert len({p.card["text"] for p in plans}) == 1


def test_plan_variants_yields_twenty_for_english_clip() -> None:
    desk = write(EN_CLIP_004AE6D9098A)
    assert desk["mode"] == "write"
    plans = plan_variants(desk, clip_id="clip_004ae6d9098a", source_duration_s=60.0)
    assert len(plans) == TARGET_VARIANTS
    assert all(len(p.card["text"].split()) >= 4 for p in plans)


def test_plan_variants_empty_when_desk_blocked() -> None:
    desk = write(EN_NOTEBOOK)
    assert desk["mode"] == "blocked"
    plans = plan_variants(desk, clip_id="en_notebook", source_duration_s=30.0)
    assert plans == []


def test_english_whisper_slices_block_instead_of_shipping_crumbs() -> None:
    desk = write(EN_NOTEBOOK)
    assert desk["mode"] == "blocked"
    validation = validate_desk_result(desk)
    assert not validation["ok"]


def test_ass_events_burn_only_hook_card_text() -> None:
    desk = write(AR_MULTILINE)
    card = next(c for c in desk["cards"] if c["hook"] == "result_first")
    events = build_ass_events(
        card,
        policy="result_first",
        cite_start_s=float(card["cite"]["start"]),
        cut_length_s=8.0,
    )
    assert len(events) == 1
    assert events[0]["text"] == card["text"]
    assert "عذبتيني" not in str(events[0]["text"])


def test_transcript_from_segments_roundtrip() -> None:
    payload = transcript_from_segments(
        [{"start": 1.0, "end": 2.0, "text": "hello world"}],
        language="en",
    )
    assert payload["language"] == "en"
    assert payload["lines"][0]["text"] == "hello world"


def test_runner_json_summary_shape(tmp_path: Path) -> None:
    from lib.runner import RunResult

    result = RunResult(
        clip_id="demo",
        desk={"mode": "blocked", "reason": "test"},
        validation={"ok": False, "issues": []},
        variants_planned=0,
        variants_rendered=0,
        success=False,
        message="desk blocked: test",
    )
    blob = json.dumps({"clip_id": result.clip_id, "success": result.success})
    assert "demo" in blob
