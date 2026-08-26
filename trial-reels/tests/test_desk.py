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


def test_live_arabic_clip_fails_closed_with_two_hooks() -> None:
    result = write(_load_fixture("clip_5a92132dc6de.json"))

    assert result["mode"] == "blocked"
    assert result["claims_found"] == 1
    assert result["target_variants"] == TARGET_VARIANTS
    assert "need 20 distinct on-screen texts" in result["reason"]
    assert result["cards"] == []
    validation = validate_desk_result(result)
    assert not validation["ok"]
    plans = plan_variants(result, clip_id="clip_5a92132dc6de", source_duration_s=30.0)
    assert plans == []


def test_live_english_clip_fails_closed_below_twenty_hooks() -> None:
    result = write(_load_fixture("clip_004ae6d9098a.json"))

    assert result["mode"] == "blocked"
    assert result["claims_found"] == 5
    assert result["cards"] == []
    assert "need 20 distinct on-screen texts" in result["reason"]
    validation = validate_desk_result(result)
    assert not validation["ok"]
    plans = plan_variants(result, clip_id="clip_004ae6d9098a", source_duration_s=60.0)
    assert plans == []


def test_english_rejects_pr1073_mid_sentence_crumbs() -> None:
    """Sliding-window crumbs must not ship."""
    result = write(_load_fixture("clip_004ae6d9098a.json"))
    texts = _card_texts(result) if result.get("cards") else [c["text"] for c in result.get("claims", [])]
    forbidden = {
        "Ross defines a true",
        "boss by the rare ability",
        "the modern corporate industry completely",
        "by the rare ability",
        "one that completely evaporates",
        "the missing reality layer,",
        "the real streets actually accept.",
        "moves the real streets actually accept.",
    }
    assert not forbidden.intersection(texts)


def test_english_live_transcript_still_finds_clause_claims_before_blocking() -> None:
    fixture = _load_fixture("clip_004ae6d9098a.json")
    blob = " ".join(line["text"] for line in fixture["lines"])
    result = write({"language": "en", "lines": [{"start": 0.0, "text": blob}]})

    assert result["mode"] == "blocked"
    assert result["claims_found"] == 5
    assert "So the next" not in _card_texts(result)


def test_arabic_whisper_lines_stay_separate() -> None:
    from lib.desk import _attested_vocabulary, _collect_tokens, _hook_units, _tokens_by_line

    fixture = _load_fixture("clip_5a92132dc6de.json")
    tokens, language = _collect_tokens(fixture)
    lines = _tokens_by_line(tokens)
    units = _hook_units(tokens, language, fixture, _attested_vocabulary(tokens))
    assert len(lines) == len(fixture["lines"])
    assert {unit.text for unit in units} == {"لك كفاية عزبتني", "عزبتني"}


def test_arabic_nested_whisper_farm_counts_one_distinct_hook() -> None:
    transcript = {
        "language": "ar",
        "lines": [
            {"start": 12.0, "text": "لك كفاية"},
            {"start": 13.0, "text": "كفاية عزبتني"},
            {"start": 14.0, "text": "لك كفاية عزبتني"},
            {"start": 15.0, "text": "عزبتني عزبتني"},
            {"start": 16.0, "text": "لك كفاية عزبتني عزبتني"},
        ],
    }

    result = write(transcript)

    assert result["mode"] == "blocked"
    assert result["claims_found"] == 1
    assert "need 20 distinct on-screen texts" in result["reason"]


def test_validate_rejects_nested_substring_hooks() -> None:
    fake = {
        "mode": "write",
        "language": "ar",
        "cards": [
            {
                "hook": HOOKS[i % len(HOOKS)],
                "stack": STACK_NAMES[i % len(STACK_NAMES)],
                "text": text,
                "cite": {"line": "لك كفاية عزبتني عزبتني"},
            }
            for i, text in enumerate(
                [
                    "لك كفاية عزبتني عزبتني",
                    "كفاية عزبتني عزبتني",
                    "لك كفاية عزبتني",
                    "كفاية عزبتني",
                    "عزبتني عزبتني",
                ]
            )
        ],
    }
    validation = validate_desk_result(fake)
    assert not validation["ok"]
    assert any("nested substring" in issue for issue in validation["issues"])


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
