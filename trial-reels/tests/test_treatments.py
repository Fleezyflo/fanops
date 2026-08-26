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


def test_english_fixture_yields_four_full_sentences_only() -> None:
    result = enumerate_treatments(_load_fixture("clip_004ae6d9098a.json"))
    assert result["mode"] == "blocked"
    treatments = result["treatments"]
    assert len(treatments) == 4
    texts = [item["text"] for item in treatments]
    assert len(set(texts)) == 4
    for item in treatments:
        assert item["kind"] in TREATMENT_KINDS
        assert item["is_full_line"]
        assert is_contiguous_attested_span(item["text"], item["cite"]["line"])
        assert item["text"].rstrip()[-1] in ".!?"


def test_arabic_fixture_drops_substring_whisper_line() -> None:
    result = enumerate_treatments(_load_fixture("clip_5a92132dc6de.json"))
    assert result["mode"] == "blocked"
    assert result["ceiling"] == 1
    assert len(result["treatments"]) == 1
    assert {item["text"] for item in result["treatments"]} == {"لك كفاية عزبتني"}


def test_enumeration_rejects_nested_window_farm_on_one_line() -> None:
    result = enumerate_treatments(
        {
            "language": "ar",
            "lines": [{"start": 12.4, "text": "لك كفاية عزبتني عزبتني"}],
        }
    )
    treatments = result["treatments"]
    assert len(treatments) == 1
    texts = [item["text"] for item in treatments]
    for index, shorter in enumerate(texts):
        for other_index, longer in enumerate(texts):
            if index != other_index:
                assert not is_nested_hook_text(shorter, longer)


def test_enumeration_rejects_english_clause_crumbs() -> None:
    result = enumerate_treatments(_load_fixture("clip_004ae6d9098a.json"))
    texts = [item["text"] for item in result["treatments"]]
    forbidden = {
        "Ross defines a true boss",
        "moves the real streets actually accept.",
        "It's a test of genuine respect",
        "that the modern corporate industry completely fails.",
        "inside a padded recording booth creates a powerful illusion,",
        "one that completely evaporates the second real-world leverage is required.",
        "the missing reality layer,",
        "behind-the-scenes power.",
    }
    assert not forbidden.intersection(texts)
