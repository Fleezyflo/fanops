"""Tests for the trial-reels constrained hook writer."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.desk import HOOKS, write  # noqa: E402


def _card_texts(result: dict) -> list[str]:
    return [card["text"] for card in result["cards"]]


def test_arabic_sung_line_keeps_spelling_and_yields_five_distinct_hooks() -> None:
    transcript = {
        "clip_id": "clip_5a92132dc6de",
        "language": "ar",
        "lines": [{"start": 12.4, "text": "لك كفاية عزبتني"}],
    }

    result = write(transcript)

    assert result["mode"] == "write"
    assert result["language"] == "ar"
    assert len(result["cards"]) == 5
    assert [card["hook"] for card in result["cards"]] == HOOKS

    texts = _card_texts(result)
    assert len(set(texts)) == 5
    assert all("عزبتني" in text for text in texts)
    assert all("عذبتيني" not in text for text in texts)

    for card in result["cards"]:
        assert card["cite"]["start"] == 12.4
        assert card["cite"]["words"]
        assert all(word in transcript["lines"][0]["text"] for word in card["cite"]["words"])


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

    assert result["mode"] == "write"
    assert len(result["cards"]) == 5

    weak = {"it's a", "it is a", "one", "this is", "a padded", "is a"}
    texts = {text.lower() for text in _card_texts(result)}
    assert texts.isdisjoint(weak)

    for card in result["cards"]:
        words = card["text"].split()
        assert len(words) >= 3
        assert card["cite"]["start"] == 3.2


def test_credit_only_arabic_transcript_blocks() -> None:
    transcript = {
        "language": "ar",
        "lines": [{"start": 0.0, "text": "ترجمة نانسي قنقر"}],
    }

    result = write(transcript)

    assert result["mode"] == "blocked"
    assert result["reason"] == "credit-only transcript"
    assert result["cards"] == []
