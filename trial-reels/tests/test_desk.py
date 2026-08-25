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
    return [item["text"] for item in result["treatments"]]


def _card_texts(result: dict) -> list[str]:
    return [card["text"] for card in result["cards"]]


def test_live_arabic_clip_honest_ceiling_is_two() -> None:
    result = write(_load_fixture("clip_5a92132dc6de.json"))

    assert result["mode"] == "write"
    assert result["ceiling"] == 2
    assert len(result["treatments"]) == 2
    assert set(_treatment_texts(result)) == {"لك كفاية عزبتني", "عزبتني"}
    validation = validate_desk_result(result)
    assert validation["ok"], validation["issues"]
    plans = plan_variants(result, clip_id="clip_5a92132dc6de", source_duration_s=30.0)
    assert len(plans) == TARGET_VARIANTS
    assert len({p.card["text"] for p in plans}) == 2


def test_live_english_clip_ships_many_clause_treatments_not_four_sentences() -> None:
    result = write(_load_fixture("clip_004ae6d9098a.json"))

    assert result["mode"] == "write"
    treatments = _treatment_texts(result)
    assert len(treatments) >= 8
    assert len(treatments) <= MAX_TREATMENTS
    assert len(set(treatments)) == len(treatments)
    assert len(set(treatments)) > 4
    assert "So the next" not in treatments
    assert all("framework for sustainable leadership" not in t for t in treatments)
    for item in result["treatments"]:
        assert item["kind"] in TREATMENT_KINDS
        assert is_contiguous_attested_span(item["text"], item["cite"]["line"])
    validation = validate_desk_result(result)
    assert validation["ok"], validation["issues"]
    plans = plan_variants(result, clip_id="clip_004ae6d9098a", source_duration_s=60.0)
    assert len(plans) == TARGET_VARIANTS
    assert len({p.card["text"] for p in plans}) == len(treatments)


def test_english_one_line_blob_still_yields_clause_treatments() -> None:
    fixture = _load_fixture("clip_004ae6d9098a.json")
    blob = " ".join(line["text"] for line in fixture["lines"])
    result = write({"language": "en", "lines": [{"start": 0.0, "text": blob}]})

    assert result["mode"] == "write"
    assert len(result["treatments"]) >= 7


def test_arabic_whisper_lines_stay_separate() -> None:
    from lib.desk import _collect_tokens, _tokens_by_line

    fixture = _load_fixture("clip_5a92132dc6de.json")
    tokens, _ = _collect_tokens(fixture)
    lines = _tokens_by_line(tokens)
    assert len(lines) == len(fixture["lines"])


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
    result = write(_load_fixture("clip_004ae6d9098a.json"))
    assert len(result["cards"]) == TARGET_VARIANTS
