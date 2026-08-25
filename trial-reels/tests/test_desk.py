"""Tests for the trial-reels constrained hook writer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.desk import HOOKS, TARGET_VARIANTS, expand_variant_slots, is_contiguous_attested_span, write  # noqa: E402
from lib.desk_swarm import validate_desk_result  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


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


def test_live_arabic_clip_5a92132dc6de_ships_one_claim() -> None:
    result = write(_load_fixture("clip_5a92132dc6de.json"))

    assert result["mode"] == "write"
    assert result["language"] == "ar"
    assert len(result["claims"]) == 1
    assert result["claims"][0]["text"] == "لك كفاية عزبتني"
    assert "عذبتيني" not in result["claims"][0]["text"]
    assert len(expand_variant_slots(result["cards"])) == TARGET_VARIANTS
    validation = validate_desk_result(result)
    assert validation["ok"], validation["issues"]


def test_live_english_clip_004ae6d9098a_rejects_window_farm() -> None:
    result = write(_load_fixture("clip_004ae6d9098a.json"))

    assert result["mode"] == "write"
    texts = [claim["text"] for claim in result["claims"]]
    assert len(texts) >= 1
    assert "fails. So the" not in texts
    assert "So the next" not in texts
    assert "required. Which brings" not in texts
    assert "behind-the-scenes power. Ross" not in texts
    assert "inside a padded" not in texts
    for text in texts:
        assert len(text.split()) >= 4 or text.endswith(".")
    validation = validate_desk_result(result)
    assert validation["ok"], validation["issues"]


def test_arabic_sung_line_keeps_spelling_and_does_not_permute() -> None:
    source = "لك كفاية عزبتني"
    transcript = {
        "clip_id": "clip_5a92132dc6de",
        "language": "ar",
        "lines": [{"start": 12.4, "text": source}],
    }

    result = write(transcript)

    assert result["mode"] == "write"
    assert len(result["cards"]) <= len(HOOKS)
    assert len(result["cards"]) >= 1
    texts = _card_texts(result)
    assert "لك كفاية عزبتني" in texts
    assert "عزبتني لك كفاية" not in texts
    for card in result["cards"]:
        assert _words_in_source_order(card["cite"]["words"], source)


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
    assert "So the next" not in texts
    for card in result["cards"]:
        assert card["text"] == " ".join(card["cite"]["words"])
        assert is_contiguous_attested_span(card["text"], card["cite"]["line"])


def test_english_booth_transcript_yields_real_clauses() -> None:
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
    assert len(result["cards"]) >= 1
    weak = {"it's a", "it is a", "one", "this is", "a padded", "is a"}
    texts = {text.lower() for text in _card_texts(result)}
    assert texts.isdisjoint(weak)


def test_credit_only_arabic_transcript_blocks() -> None:
    transcript = {
        "language": "ar",
        "lines": [{"start": 0.0, "text": "ترجمة نانسي قنقر"}],
    }

    result = write(transcript)

    assert result["mode"] == "blocked"
    assert result["reason"] == "credit-only transcript"
    assert result["cards"] == []
