"""Tests for attested hook treatment enumeration."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))

from lib.treatments import (  # noqa: E402
    MAX_TREATMENTS,
    TREATMENT_KINDS,
    enumerate_treatments,
    is_contiguous_attested_span,
)


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_treatment_kinds_are_declared() -> None:
    assert "attested_clause" in TREATMENT_KINDS
    assert "source_order" in TREATMENT_KINDS
    assert len(TREATMENT_KINDS) == 8


def test_english_fixture_yields_many_non_nested_treatments() -> None:
    result = enumerate_treatments(_load_fixture("clip_004ae6d9098a.json"))
    assert result["mode"] == "write"
    treatments = result["treatments"]
    assert 8 <= len(treatments) <= MAX_TREATMENTS
    texts = [item["text"] for item in treatments]
    assert len(set(texts)) == len(texts)
    for item in treatments:
        assert is_contiguous_attested_span(item["text"], item["cite"]["line"])


def test_arabic_fixture_honest_ceiling_two() -> None:
    result = enumerate_treatments(_load_fixture("clip_5a92132dc6de.json"))
    assert result["mode"] == "write"
    assert result["ceiling"] == 2
    assert len(result["treatments"]) == 2
