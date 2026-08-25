"""Tests for the trial-reels constrained hook writer."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.desk import HOOKS, TARGET_VARIANTS, is_contiguous_attested_span, write  # noqa: E402
from lib.desk_swarm import validate_desk_result  # noqa: E402
from lib.stacks import STACK_NAMES  # noqa: E402


def _card_texts(result: dict) -> list[str]:
    return [card["text"] for card in result["cards"]]


AR_MULTILINE = {
    "clip_id": "clip_ar_multiline",
    "language": "ar",
    "lines": [
        {"start": 1.0, "text": "لك كفاية عزبتني يا حبيبي الغالي"},
        {"start": 5.0, "text": "قلبي مشتاق وحنيني كبير في الليل"},
        {"start": 10.0, "text": "يا حبيبي رجعت لك من جديد"},
        {"start": 15.0, "text": "الدنيا ما بتساوي شي بدون حبك"},
        {"start": 20.0, "text": "كل لحظة بعيد عنك بتمر علي"},
        {"start": 25.0, "text": "رجعتلك وما رح اتركك ابدا"},
    ],
}


def test_arabic_sung_line_blocks_instead_of_permuting() -> None:
    transcript = {
        "clip_id": "clip_5a92132dc6de",
        "language": "ar",
        "lines": [{"start": 12.4, "text": "لك كفاية عزبتني"}],
    }

    result = write(transcript)

    assert result["mode"] == "blocked"
    assert "contiguous" in result["reason"]
    assert len(result["cards"]) < TARGET_VARIANTS

    for card in result["cards"]:
        assert "عذبتيني" not in card["text"]
        assert is_contiguous_attested_span(card["text"], card["cite"]["line"])


def test_arabic_repeated_line_blocks_nested_window_farm() -> None:
    transcript = {
        "language": "ar",
        "lines": [{"start": 12.4, "text": "لك كفاية عزبتني عزبتني"}],
    }

    result = write(transcript)

    assert result["mode"] == "blocked"
    assert len(result["cards"]) < TARGET_VARIANTS


def test_arabic_multiline_yields_twenty_distinct_hooks() -> None:
    result = write(AR_MULTILINE)

    assert result["mode"] == "write"
    assert len(result["cards"]) == TARGET_VARIANTS
    texts = _card_texts(result)
    assert len(set(texts)) == TARGET_VARIANTS
    assert len({(c["hook"], c["stack"]) for c in result["cards"]}) == TARGET_VARIANTS
    validation = validate_desk_result(result)
    assert validation["ok"], validation["issues"]
    for card in result["cards"]:
        assert is_contiguous_attested_span(card["text"], card["cite"]["line"])


def test_arabic_whisper_lines_stay_separate() -> None:
    """Arabic must not stitch every Whisper line into one mega-line."""
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
        assert is_contiguous_attested_span(card["text"], card["cite"]["line"])
        assert "fails." not in card["text"].lower()


def test_english_booth_transcript_rejects_function_word_cards() -> None:
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

    assert result["mode"] == "blocked"
    assert len(result["cards"]) >= 3

    weak = {"it's a", "it is a", "one", "this is", "a padded", "is a"}
    texts = {text.lower() for text in _card_texts(result)}
    assert texts.isdisjoint(weak)

    for card in result["cards"]:
        assert is_contiguous_attested_span(card["text"], card["cite"]["line"])


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
