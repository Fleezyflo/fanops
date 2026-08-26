"""Tests for the trial-reels constrained hook writer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))

from lib.desk import HOOKS, TARGET_VARIANTS, expand_variant_slots, is_contiguous_attested_span, write  # noqa: E402
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


def test_twenty_hook_fixture_ships_twenty_distinct_cards() -> None:
    result = write(_load_fixture("clip_twenty_hooks.json"))

    assert result["mode"] == "write"
    assert result["unique_texts"] == TARGET_VARIANTS
    assert len(result["cards"]) == TARGET_VARIANTS
    assert len(result["claims"]) == TARGET_VARIANTS
    assert len(set(_card_texts(result))) == TARGET_VARIANTS
    validation = validate_desk_result(result)
    assert validation["ok"], validation["issues"]
    plans = plan_variants(result, clip_id="clip_twenty_hooks", source_duration_s=120.0)
    assert len(plans) == TARGET_VARIANTS
    assert len({p.card["text"] for p in plans}) == TARGET_VARIANTS
    assert len(expand_variant_slots(result["cards"])) == TARGET_VARIANTS


def test_live_arabic_clip_fails_closed_with_one_maximal_hook() -> None:
    result = write(_load_fixture("clip_5a92132dc6de.json"))

    assert result["mode"] == "blocked"
    assert result["claims_found"] == 1
    assert result["target_variants"] == TARGET_VARIANTS
    assert "need 20" in result["reason"]
    assert result["cards"] == []
    validation = validate_desk_result(result)
    assert not validation["ok"]
    plans = plan_variants(result, clip_id="clip_5a92132dc6de", source_duration_s=30.0)
    assert plans == []


def test_live_english_clip_fails_closed_below_twenty() -> None:
    result = write(_load_fixture("clip_004ae6d9098a.json"))

    assert result["mode"] == "blocked"
    assert result["claims_found"] == 4
    assert result["cards"] == []
    assert "need 20" in result["reason"]
    validation = validate_desk_result(result)
    assert not validation["ok"]
    plans = plan_variants(result, clip_id="clip_004ae6d9098a", source_duration_s=60.0)
    assert plans == []
    texts = {item["text"] for item in result.get("treatments") or []}
    assert texts == EN_LIVE_SENTENCES
    assert "So the next" not in texts
    assert "fails." not in texts


def test_english_live_transcript_still_finds_full_sentences_before_blocking() -> None:
    fixture = _load_fixture("clip_004ae6d9098a.json")
    blob = " ".join(line["text"] for line in fixture["lines"])
    result = write({"language": "en", "lines": [{"start": 0.0, "text": blob}]})

    assert result["mode"] == "blocked"
    assert result["claims_found"] == 4
    assert {item["text"] for item in result.get("treatments") or []} == EN_LIVE_SENTENCES
    assert "So the next" not in {item["text"] for item in result.get("treatments") or []}


def test_english_mid_sentence_crumbs_never_emitted() -> None:
    fixture = _load_fixture("clip_004ae6d9098a.json")
    result = write(fixture)
    crumbs = {
        "Ross defines a true",
        "boss by the rare ability",
        "So the next",
        "fails.",
        "creates a powerful illusion,",
        "genuine respect that the modern",
    }
    texts = {item["text"] for item in result.get("treatments") or []}
    assert texts.isdisjoint(crumbs)


def test_four_hooks_never_cycles_into_twenty_cards() -> None:
    result = write(_load_fixture("clip_004ae6d9098a.json"))
    assert result["mode"] == "blocked"
    assert result["cards"] == []
    assert result.get("claims_found") == 4


def test_arabic_whisper_lines_stay_separate() -> None:
    from lib.desk import _collect_tokens, _tokens_by_line

    fixture = _load_fixture("clip_5a92132dc6de.json")
    tokens, language = _collect_tokens(fixture)
    lines = _tokens_by_line(tokens)
    assert len(lines) == len(fixture["lines"])
    line_texts = {" ".join(token.word for token in line) for line in lines}
    assert line_texts == {"لك كفاية عزبتني", "عزبتني"}


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


def test_is_contiguous_attested_span_matches_subsequence() -> None:
    line = "Ross defines a true boss by the rare ability."
    assert is_contiguous_attested_span("true boss by", line)
    assert not is_contiguous_attested_span("boss Ross", line)
