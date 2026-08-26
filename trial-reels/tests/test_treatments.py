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
    is_nested_hook_text,
)


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_treatment_kinds_are_declared() -> None:
    assert "attested_clause" in TREATMENT_KINDS
    assert "source_order" in TREATMENT_KINDS
    assert len(TREATMENT_KINDS) == 8


def test_twenty_hook_fixture_writes_twenty() -> None:
    result = enumerate_treatments(_load_fixture("clip_twenty_hooks.json"))
    assert result["mode"] == "write"
    assert len(result["treatments"]) == MAX_TREATMENTS
    texts = [item["text"] for item in result["treatments"]]
    assert len(set(texts)) == MAX_TREATMENTS


def test_english_live_fixture_fails_closed_with_honest_claims() -> None:
    result = enumerate_treatments(_load_fixture("clip_004ae6d9098a.json"))
    assert result["mode"] == "blocked"
    assert 4 <= result["claims_found"] < MAX_TREATMENTS
    assert "need 20" in result["reason"]
    texts = [item["text"] for item in result["treatments"]]
    assert "So the next" not in texts
    for item in result["treatments"]:
        assert is_contiguous_attested_span(item["text"], item["cite"]["line"])


def test_arabic_fixture_honest_ceiling_two() -> None:
    result = enumerate_treatments(_load_fixture("clip_5a92132dc6de.json"))
    assert result["mode"] == "blocked"
    assert result["claims_found"] == 2
    assert len(result["treatments"]) == 2


def test_is_nested_hook_text_detects_subspan() -> None:
    line = "لك كفاية عزبتني"
    assert is_nested_hook_text("لك كفاية عزبتني", "عزبتني", line_text=line)
    assert not is_nested_hook_text("عزبتني", "لك كفاية عزبتني", line_text=line)
