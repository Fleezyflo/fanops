"""Tests for attested hook treatment enumeration."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))

from lib.desk import TARGET_VARIANTS  # noqa: E402
from lib.treatments import (  # noqa: E402
    MAX_TREATMENTS,
    TREATMENT_KINDS,
    enumerate_treatments,
    is_contiguous_attested_span,
    is_nested_hook_text,
)


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_treatment_kinds_are_declared() -> None:
    assert "attested_clause" in TREATMENT_KINDS
    assert "source_order" in TREATMENT_KINDS
    assert len(TREATMENT_KINDS) == 8


def test_twenty_hook_fixture_yields_twenty_treatments() -> None:
    result = enumerate_treatments(_load_fixture("clip_twenty_hooks.json"))
    assert result["mode"] == "write"
    treatments = result["treatments"]
    assert len(treatments) == TARGET_VARIANTS
    texts = [item["text"] for item in treatments]
    assert len(set(texts)) == TARGET_VARIANTS
    for item in treatments:
        assert is_contiguous_attested_span(item["text"], item["cite"]["line"])


def test_english_fixture_yields_stitched_sentence_hooks() -> None:
    result = enumerate_treatments(_load_fixture("clip_004ae6d9098a.json"))
    assert result["mode"] == "blocked"
    treatments = result["treatments"]
    texts = [item["text"] for item in treatments]
    assert len(texts) == 4
    assert len(set(texts)) == 4
    assert "So the next" not in texts
    assert "inside a padded recording booth creates a powerful illusion," in " ".join(texts)
    for item in treatments:
        assert item["kind"] in TREATMENT_KINDS
        assert is_contiguous_attested_span(item["text"], item["cite"]["line"])


def test_arabic_fixture_honest_ceiling_one_maximal_line() -> None:
    result = enumerate_treatments(_load_fixture("clip_5a92132dc6de.json"))
    assert result["mode"] == "blocked"
    assert result["ceiling"] == 1
    assert len(result["treatments"]) == 1
    assert result["treatments"][0]["text"] == "لك كفاية عزبتني"


def test_nested_hook_text_detects_subspan() -> None:
    assert is_nested_hook_text("عزبتني", "لك كفاية عزبتني")
    assert not is_nested_hook_text("لك كفاية عزبتني", "عزبتني")


def test_single_arabic_line_never_emits_nested_window_farm() -> None:
    """One sung line must not fan into nested sub-window treatments (5 hooks × 4 stacks)."""
    result = enumerate_treatments(
        {
            "language": "ar",
            "lines": [{"start": 12.2, "text": "لك كفاية عزبتني عزبتني"}],
        }
    )
    texts = [item["text"] for item in result["treatments"]]
    assert len(texts) == 1
    assert texts[0] == "لك كفاية عزبتني عزبتني"
    assert result["mode"] == "blocked"
    for left, right in zip(texts, texts, strict=True):
        for other in texts:
            if left != other:
                assert not is_nested_hook_text(left, other)


def test_english_enumeration_rejects_whisper_crumbs() -> None:
    result = enumerate_treatments(
        {
            "language": "en",
            "lines": [
                {"start": 0.0, "text": "fails."},
                {"start": 0.5, "text": "So the next"},
                {"start": 1.0, "text": "the respect that the modern corporate in"},
            ],
        }
    )
    texts = [item["text"] for item in result.get("treatments") or []]
    assert result["mode"] == "blocked"
    assert texts == []
    assert "fails." not in texts
    assert "So the next" not in texts
