"""Tests for the trial-reels hook treatment desk."""

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
from lib.treatments import MAX_TREATMENTS, TREATMENT_KINDS  # noqa: E402


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _treatment_texts(result: dict) -> list[str]:
    return [item["text"] for item in result.get("treatments", [])]


def _card_texts(result: dict) -> list[str]:
    return [card["text"] for card in result.get("cards", [])]


def test_twenty_hook_fixture_ships_twenty_distinct_cards() -> None:
    result = write(_load_fixture("clip_twenty_hooks.json"))

    assert result["mode"] == "write"
    assert result["unique_texts"] == TARGET_VARIANTS
    assert len(result["treatments"]) == TARGET_VARIANTS
    assert len(result["cards"]) == TARGET_VARIANTS
    assert len(set(_card_texts(result))) == TARGET_VARIANTS
    validation = validate_desk_result(result)
    assert validation["ok"], validation["issues"]
    plans = plan_variants(result, clip_id="clip_twenty_hooks", source_duration_s=120.0)
    assert len(plans) == TARGET_VARIANTS
    assert len({p.card["text"] for p in plans}) == TARGET_VARIANTS


def test_live_arabic_clip_fails_closed_with_two_hooks() -> None:
    result = write(_load_fixture("clip_5a92132dc6de.json"))

    assert result["mode"] == "blocked"
    assert result["claims_found"] == 2
    assert result["target_variants"] == TARGET_VARIANTS
    assert "need 20" in result["reason"]
    assert result["cards"] == []
    validation = validate_desk_result(result)
    assert not validation["ok"]
    plans = plan_variants(result, clip_id="clip_5a92132dc6de", source_duration_s=30.0)
    assert plans == []


def test_arabic_merged_line_does_not_ship_nested_windows() -> None:
    result = write(
        {
            "language": "ar",
            "lines": [{"start": 12.4, "text": "لك كفاية عزبتني عزبتني"}],
        }
    )

    assert result["mode"] == "blocked"
    treatments = _treatment_texts(result)
    assert treatments == ["لك كفاية عزبتني عزبتني"]
    assert "عذبتيني" not in " ".join(treatments)


def test_live_english_clip_fails_closed_below_twenty() -> None:
    result = write(_load_fixture("clip_004ae6d9098a.json"))

    assert result["mode"] == "blocked"
    assert result["claims_found"] >= 8
    assert result["claims_found"] < TARGET_VARIANTS
    assert result["cards"] == []
    assert "So the next" not in _treatment_texts(result)
    validation = validate_desk_result(result)
    assert not validation["ok"]
    plans = plan_variants(result, clip_id="clip_004ae6d9098a", source_duration_s=60.0)
    assert plans == []


def test_english_rejects_pr1073_mid_sentence_crumbs() -> None:
    result = write(_load_fixture("clip_004ae6d9098a.json"))
    treatments = _treatment_texts(result)
    forbidden = {
        "Ross defines a true",
        "boss by the rare ability",
        "the modern corporate industry completely",
        "by the rare ability",
        "one that completely evaporates",
        "the missing reality layer,",
        "the real streets actually accept.",
    }
    assert not forbidden.intersection(treatments)


def test_english_one_line_blob_still_yields_clause_treatments() -> None:
    fixture = _load_fixture("clip_004ae6d9098a.json")
    blob = " ".join(line["text"] for line in fixture["lines"])
    result = write({"language": "en", "lines": [{"start": 0.0, "text": blob}]})

    assert result["mode"] == "blocked"
    assert result["claims_found"] >= 8


def test_arabic_whisper_lines_stay_separate() -> None:
    from lib.desk import _collect_tokens, _tokens_by_line
    from lib.treatments import enumerate_treatments

    fixture = _load_fixture("clip_5a92132dc6de.json")
    tokens, _ = _collect_tokens(fixture)
    lines = _tokens_by_line(tokens)
    assert len(lines) == len(fixture["lines"])
    units = enumerate_treatments(fixture)["treatments"]
    assert {item["text"] for item in units} == {"لك كفاية عزبتني", "عزبتني"}


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

    texts = _treatment_texts(result) if result.get("mode") == "write" else []
    assert "So the next" not in texts
    assert "fails." not in " ".join(texts).lower()
    for item in result.get("treatments", []):
        assert item["text"].lower() != "so the next"


def test_credit_only_arabic_transcript_blocks() -> None:
    transcript = {
        "language": "ar",
        "lines": [{"start": 0.0, "text": "ترجمة نانسي قنقر"}],
    }

    result = write(transcript)

    assert result["mode"] == "blocked"
    assert result["reason"] == "credit-only transcript"
    assert result["treatments"] == []


def test_variant_slots_cover_hook_stack_grid() -> None:
    assert TARGET_VARIANTS == len(HOOKS) * len(STACK_NAMES)
    result = write(_load_fixture("clip_twenty_hooks.json"))
    assert len(result["cards"]) == TARGET_VARIANTS


def test_is_contiguous_attested_span_matches_subsequence() -> None:
    line = "Ross defines a true boss by the rare ability."
    assert is_contiguous_attested_span("true boss by", line)
    assert not is_contiguous_attested_span("boss Ross", line)
