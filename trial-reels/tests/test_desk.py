"""Tests for the trial-reels constrained hook writer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))

from lib.desk import HOOKS, TARGET_VARIANTS, is_contiguous_attested_span, write  # noqa: E402
from lib.desk_swarm import validate_desk_result  # noqa: E402
from lib.runner import plan_variants  # noqa: E402
from lib.stacks import STACK_NAMES  # noqa: E402


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _card_texts(result: dict) -> list[str]:
    return [card["text"] for card in result["cards"]]


EN_LIVE_SENTENCES = frozenset(
    {
        "inside a padded recording booth creates a powerful illusion, one that completely evaporates the second real-world leverage is required.",
        "Which brings us to the missing reality layer, behind-the-scenes power.",
        "Ross defines a true boss by the rare ability to execute moves the real streets actually accept.",
        "It's a test of genuine respect that the modern corporate industry completely fails.",
    }
)


def test_live_arabic_clip_ships_two_attested_lines() -> None:
    result = write(_load_fixture("clip_5a92132dc6de.json"))

    assert result["mode"] == "write"
    assert len(result["claims"]) == 2
    assert result["unique_texts"] == 2
    assert set(_card_texts(result)) == {"لك كفاية عزبتني", "عزبتني"}
    validation = validate_desk_result(result)
    assert validation["ok"], validation["issues"]
    plans = plan_variants(result, clip_id="clip_5a92132dc6de", source_duration_s=30.0)
    assert len(plans) == TARGET_VARIANTS


def test_live_english_clip_ships_four_sentences_rejects_so_the_next() -> None:
    result = write(_load_fixture("clip_004ae6d9098a.json"))

    assert result["mode"] == "write"
    assert len(result["claims"]) == 4
    assert len(result["cards"]) == 4
    assert result["unique_texts"] == 4
    assert set(_card_texts(result)) == EN_LIVE_SENTENCES
    assert "So the next" not in _card_texts(result)
    assert all("framework for sustainable leadership" not in t for t in _card_texts(result))
    validation = validate_desk_result(result)
    assert validation["ok"], validation["issues"]
    plans = plan_variants(result, clip_id="clip_004ae6d9098a", source_duration_s=60.0)
    assert len(plans) == TARGET_VARIANTS
    assert set(p.card["text"] for p in plans) == EN_LIVE_SENTENCES


def test_english_one_line_blob_still_splits_into_sentences() -> None:
    fixture = _load_fixture("clip_004ae6d9098a.json")
    blob = " ".join(line["text"] for line in fixture["lines"])
    result = write({"language": "en", "lines": [{"start": 0.0, "text": blob}]})

    assert result["mode"] == "write"
    assert set(_card_texts(result)) == EN_LIVE_SENTENCES


def test_arabic_whisper_lines_stay_separate() -> None:
    from lib.desk import _collect_tokens, _tokens_by_line

    fixture = _load_fixture("clip_5a92132dc6de.json")
    tokens, _ = _collect_tokens(fixture)
    lines = _tokens_by_line(tokens)
    assert len(lines) == len(fixture["lines"])


def test_english_cite_timestamps_follow_whisper_lines() -> None:
    result = write(_load_fixture("clip_004ae6d9098a.json"))
    starts = sorted({card["cite"]["start"] for card in result["cards"]})
    assert starts == [0.0, 7.2, 12.6, 18.4]
    assert not result["contract_met"]


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
    for card in result.get("cards", []):
        assert "fails." not in card["text"].lower()
        assert card["text"].lower() != "so the next"


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
