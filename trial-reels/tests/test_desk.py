"""Tests for the trial-reels contiguous claim-lock writer."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.desk import HOOKS, write  # noqa: E402


def _card_texts(result: dict) -> list[str]:
    return [card["text"] for card in result["cards"]]


def _words_in_source_order(cite_words: list[str], source: str) -> bool:
    cursor = 0
    for word in cite_words:
        idx = source.find(word, cursor)
        if idx < 0:
            return False
        cursor = idx + len(word)
    return True


def test_arabic_sung_line_keeps_spelling_and_does_not_permute_for_five() -> None:
    source = "لك كفاية عزبتني"
    transcript = {
        "clip_id": "clip_5a92132dc6de",
        "language": "ar",
        "lines": [{"start": 12.4, "text": source}],
    }

    result = write(transcript)

    assert result["mode"] == "write"
    assert result["language"] == "ar"
    assert len(result["cards"]) < len(HOOKS)
    assert len(result["cards"]) >= 1

    texts = _card_texts(result)
    assert len(set(texts)) == len(texts)
    assert "لك كفاية عزبتني" in texts
    assert all("عذبتيني" not in text for text in texts)

    for card in result["cards"]:
        assert card["cite"]["start"] == 12.4
        assert card["cite"]["words"]
        assert _words_in_source_order(card["cite"]["words"], source)
        assert card["text"] == " ".join(card["cite"]["words"])


def test_arabic_three_word_line_has_no_anagram_hooks() -> None:
    source = "لك كفاية عزبتني"
    transcript = {
        "language": "ar",
        "lines": [{"start": 1.0, "text": source}],
    }

    result = write(transcript)
    texts = _card_texts(result)

    assert "لك كفاية عزبتني" in texts
    assert "عزبتني لك كفاية" not in texts
    assert "كفاية عزبتني لك" not in texts
    assert len(texts) <= 3


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
    assert len(result["cards"]) >= 4

    weak = {"it's a", "it is a", "one", "this is", "a padded", "is a"}
    texts = {text.lower() for text in _card_texts(result)}
    assert texts.isdisjoint(weak)

    for card in result["cards"]:
        words = card["text"].split()
        assert len(words) >= 3
        assert card["cite"]["start"] == 3.2
        assert card["text"] == " ".join(card["cite"]["words"])


def test_english_whisper_fragments_reject_leftover_slices() -> None:
    transcript = {
        "language": "en",
        "lines": [
            {"start": 0.0, "text": "The boss has a rare ability."},
            {"start": 2.4, "text": "So the next"},
            {"start": 3.1, "text": "move is to execute moves on the streets."},
        ],
    }

    result = write(transcript)

    assert result["mode"] == "write"
    texts = _card_texts(result)
    assert "fails. So the next" not in texts
    assert "So the next" not in texts
    for card in result["cards"]:
        assert card["text"] == " ".join(card["cite"]["words"])
        assert "." not in card["text"][:-1] or card["text"].endswith(".")


def test_credit_only_arabic_transcript_blocks() -> None:
    transcript = {
        "language": "ar",
        "lines": [{"start": 0.0, "text": "ترجمة نانسي قنقر"}],
    }

    result = write(transcript)

    assert result["mode"] == "blocked"
    assert result["reason"] == "credit-only transcript"
    assert result["cards"] == []
