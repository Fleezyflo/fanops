"""Tests for the trial-reels constrained hook writer."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.desk import HOOKS, is_contiguous_attested_span, write  # noqa: E402


def _card_texts(result: dict) -> list[str]:
    return [card["text"] for card in result["cards"]]


def test_arabic_sung_line_blocks_instead_of_permuting() -> None:
    transcript = {
        "clip_id": "clip_5a92132dc6de",
        "language": "ar",
        "lines": [{"start": 12.4, "text": "لك كفاية عزبتني"}],
    }

    result = write(transcript)

    assert result["mode"] == "blocked"
    assert "contiguous" in result["reason"]
    assert len(result["cards"]) < 5

    for card in result["cards"]:
        assert "عزبتني" in card["text"]
        assert "عذبتيني" not in card["text"]
        assert is_contiguous_attested_span(card["text"], card["cite"]["line"])


def test_arabic_multiline_yields_five_contiguous_hooks() -> None:
    transcript = {
        "clip_id": "clip_ar_multiline",
        "language": "ar",
        "lines": [
            {"start": 1.0, "text": "لك كفاية عزبتني يا حبيبي الغالي"},
            {"start": 5.0, "text": "قلبي مشتاق وحنيني كبير في الليل"},
            {"start": 10.0, "text": "يا حبيبي رجعت لك من جديد"},
        ],
    }

    result = write(transcript)

    assert result["mode"] == "write"
    assert len(result["cards"]) == 5
    texts = _card_texts(result)
    assert len(set(texts)) == 5
    for card in result["cards"]:
        assert is_contiguous_attested_span(card["text"], card["cite"]["line"])


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
        words = card["text"].split()
        assert len(words) >= 3
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
