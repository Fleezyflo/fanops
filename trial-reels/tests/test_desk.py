"""Tests for the trial-reels constrained hook writer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.desk import HOOKS, TARGET_VARIANTS, expand_variant_slots, is_contiguous_attested_span, plan_variants, write  # noqa: E402
from lib.desk_swarm import validate_desk_result  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

LIVE_EN_SENTENCES = (
    "inside a padded recording booth creates a powerful illusion, one that completely evaporates "
    "the second real-world leverage is required.",
    "Which brings us to the missing reality layer, behind-the-scenes power.",
    "Ross defines a true boss by the rare ability to execute moves the real streets actually accept.",
    "It's a test of genuine respect that the modern corporate industry completely fails.",
)


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


def test_live_english_clip_004ae6d9098a_ships_four_sentences() -> None:
    transcript = _load_fixture("clip_004ae6d9098a.json")
    result = write(transcript)

    assert result["mode"] == "write"
    texts = [claim["text"] for claim in result["claims"]]
    assert len(texts) == 4
    assert texts == list(LIVE_EN_SENTENCES)
    assert result["unique_texts"] == 4
    assert result["target_variants"] == TARGET_VARIANTS
    assert "So the next" not in texts
    assert "street ties" not in " ".join(texts).lower()
    assert "vocal isolation" not in " ".join(texts).lower()
    assert len(result["cards"]) == 4
    for card in result["cards"]:
        assert card["text"] == " ".join(card["cite"]["words"])
        assert is_contiguous_attested_span(card["text"], card["cite"]["line"])
    validation = validate_desk_result(result)
    assert validation["ok"], validation["issues"]
    assert len(expand_variant_slots(result["cards"])) == TARGET_VARIANTS


def test_director_brief_sparse_english_plans_twenty_treatments() -> None:
    """Four attested sentences; hook×stack still plans 20 cuts with honest text ceiling."""
    result = write(_load_fixture("clip_004ae6d9098a.json"))
    planned = plan_variants(result, clip_id="clip_004ae6d9098a", source_duration_s=60.0)

    assert result["unique_texts"] == 4
    assert result["unique_texts"] < TARGET_VARIANTS
    assert len(planned) == TARGET_VARIANTS
    assert len({slot["text"] for slot in planned}) == 4
    assert len({(slot["hook"], slot["stack"]) for slot in planned}) == TARGET_VARIANTS


def test_arabic_sung_line_keeps_spelling_and_does_not_permute() -> None:
    source = "لك كفاية عزبتني"
    transcript = {
        "clip_id": "clip_5a92132dc6de",
        "language": "ar",
        "lines": [{"start": 12.4, "text": source}],
    }

    result = write(transcript)

    assert result["mode"] == "write"
    assert len(result["cards"]) == 1
    texts = _card_texts(result)
    assert texts == [source]
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


def test_credit_only_arabic_transcript_blocks() -> None:
    transcript = {
        "language": "ar",
        "lines": [{"start": 0.0, "text": "ترجمة نانسي قنقر"}],
    }

    result = write(transcript)

    assert result["mode"] == "blocked"
    assert result["reason"] == "credit-only transcript"
    assert result["cards"] == []
    assert result["unique_texts"] == 0


def test_live_fixtures_cannot_honestly_yield_twenty_distinct_texts() -> None:
    """Physical ceiling: live clips attest 4 EN sentences or 1 AR line — not 20 texts."""
    en = write(_load_fixture("clip_004ae6d9098a.json"))
    ar = write(_load_fixture("clip_5a92132dc6de.json"))
    assert en["unique_texts"] == 4
    assert ar["unique_texts"] == 1
    assert en["unique_texts"] < TARGET_VARIANTS
    assert ar["unique_texts"] < TARGET_VARIANTS
    assert len(plan_variants(en, clip_id="clip_004ae6d9098a", source_duration_s=60.0)) == TARGET_VARIANTS
    assert len(plan_variants(ar, clip_id="clip_5a92132dc6de", source_duration_s=30.0)) == TARGET_VARIANTS


def test_variant_slots_cover_hook_stack_grid() -> None:
    assert TARGET_VARIANTS == len(HOOKS) * 4
