"""Tests for the trial-reels constrained hook writer."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.desk import HOOKS, TARGET_VARIANTS, is_contiguous_attested_span, write  # noqa: E402
from lib.desk_swarm import validate_desk_result  # noqa: E402
from lib.runner import plan_variants  # noqa: E402
from lib.stacks import STACK_NAMES  # noqa: E402


def _card_texts(result: dict) -> list[str]:
    return [card["text"] for card in result["cards"]]


AR_CLIP_5A92132DC6DE = {
    "clip_id": "clip_5a92132dc6de",
    "language": "ar",
    "lines": [{"start": 12.4, "text": "لك كفاية عزبتني عزبتني"}],
}

EN_CLIP_004AE6D9098A = {
    "clip_id": "clip_004ae6d9098a",
    "language": "en",
    "lines": [
        {"start": 0.0, "text": "Ross defines"},
        {"start": 0.8, "text": "the framework for sustainable leadership."},
        {"start": 3.0, "text": "Which brings us"},
        {"start": 3.8, "text": "to the central question of trust."},
        {"start": 6.0, "text": "It's a test"},
        {"start": 6.6, "text": "of whether your network truly matters."},
        {"start": 9.0, "text": "So the next"},
        {"start": 9.5, "text": "chapter walks through the evidence."},
    ],
}

AR_MULTILINE = {
    "clip_id": "clip_ar_multiline",
    "language": "ar",
    "lines": [
        {"start": 1.0, "text": "لك كفاية عزبتني يا حبيبي الغالي"},
        {"start": 5.0, "text": "قلبي مشتاق وحنيني كبير في الليل"},
        {"start": 10.0, "text": "يا حبيبي رجعت لك من جديد"},
    ],
}


def test_arabic_sung_line_ships_full_line_across_policies() -> None:
    result = write(AR_CLIP_5A92132DC6DE)

    assert result["mode"] == "write"
    assert len(result["cards"]) == len(HOOKS)
    assert result["unique_texts"] == 1
    assert all(card["text"] == "لك كفاية عزبتني عزبتني" for card in result["cards"])
    assert "عذبتيني" not in result["ear"]
    validation = validate_desk_result(result)
    assert validation["ok"], validation["issues"]
    plans = plan_variants(result, clip_id="clip_5a92132dc6de", source_duration_s=30.0)
    assert len(plans) == TARGET_VARIANTS


def test_arabic_sung_line_does_not_ship_nested_window_farm() -> None:
    result = write(AR_CLIP_5A92132DC6DE)
    texts = set(_card_texts(result))
    assert texts == {"لك كفاية عزبتني عزبتني"}
    for card in result["cards"]:
        assert is_contiguous_attested_span(card["text"], card["cite"]["line"])


def test_english_clip_ships_sentence_hooks_not_crumbs() -> None:
    result = write(EN_CLIP_004AE6D9098A)

    assert result["mode"] == "write"
    assert len(result["cards"]) == len(HOOKS)
    validation = validate_desk_result(result)
    assert validation["ok"], validation["issues"]

    texts = _card_texts(result)
    for text in texts:
        assert len(text.split()) >= 4
        assert text.lower() not in {"so the next", "ross defines", "which brings", "brings us", "it's a test", "a test of"}

    plans = plan_variants(result, clip_id="clip_004ae6d9098a", source_duration_s=60.0)
    assert len(plans) == TARGET_VARIANTS


def test_arabic_multiline_yields_distinct_line_hooks() -> None:
    result = write(AR_MULTILINE)

    assert result["mode"] == "write"
    assert len(result["cards"]) == len(HOOKS)
    assert result["unique_texts"] >= 3
    validation = validate_desk_result(result)
    assert validation["ok"], validation["issues"]
    for card in result["cards"]:
        assert is_contiguous_attested_span(card["text"], card["cite"]["line"])


def test_arabic_whisper_lines_stay_separate() -> None:
    from lib.desk import _collect_tokens, _tokens_by_line

    tokens, _ = _collect_tokens(AR_MULTILINE)
    lines = _tokens_by_line(tokens)
    assert len(lines) == len(AR_MULTILINE["lines"])
    assert lines[0][0].line_text == AR_MULTILINE["lines"][0]["text"]


def test_english_whisper_slices_do_not_ship() -> None:
    transcript = {
        "language": "en",
        "lines": [
            {"start": 0.0, "text": "fails."},
            {"start": 0.5, "text": "So the next"},
            {"start": 1.0, "text": "the respect that the modern corporate in"},
            {"start": 2.0, "text": "genuine street ties actually accept."},
        ],
    }

    result = write(transcript)

    assert result["mode"] == "blocked"
    for card in result["cards"]:
        assert "fails." not in card["text"].lower()
        assert card["text"].lower() != "so the next"


def test_english_booth_transcript_ships_full_sentence() -> None:
    transcript = {
        "language": "en",
        "lines": [
            {
                "start": 3.2,
                "text": (
                    "This is a padded recording booth built for vocal isolation "
                    "and clean takes in the studio"
                ),
            }
        ],
    }

    result = write(transcript)

    assert result["mode"] == "write"
    assert len(result["cards"]) == len(HOOKS)
    assert all(
        card["text"]
        == "This is a padded recording booth built for vocal isolation and clean takes in the studio"
        for card in result["cards"]
    )


def test_credit_only_arabic_transcript_blocks() -> None:
    transcript = {
        "language": "ar",
        "lines": [{"start": 0.0, "text": "ترجمة نانسي قنقر"}],
    }

    result = write(transcript)

    assert result["mode"] == "blocked"
    assert result["reason"] == "credit-only transcript"
    assert result["cards"] == []


def test_variant_slots_cover_hook_stack_grid() -> None:
    assert TARGET_VARIANTS == len(HOOKS) * len(STACK_NAMES)
