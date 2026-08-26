"""Tests for dual-ear merge logic (fixture JSON, no model weights)."""
from __future__ import annotations

import json
from pathlib import Path

from lib.ears import merge_ears

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_credit_primary_english_secondary_keeps_english():
    fx = _load_fixture("credit_primary_english_secondary.json")
    merged = merge_ears(fx["primary"], fx["secondary"])

    assert fx["expected_text"] in merged["text"]
    assert fx["forbidden"] not in merged["text"]
    assert merged["segments"][0]["dual_agreed"] is False
    assert merged["language"] == "en"


def test_arabic_dual_agree_keeps_azabtini():
    fx = _load_fixture("arabic_dual_agree.json")
    merged = merge_ears(fx["primary"], fx["secondary"])

    assert merged["text"] == fx["expected_text"]
    assert fx["forbidden"] not in merged["text"]
    assert merged["segments"][0]["dual_agreed"] is True

    wrong = merge_ears(fx["wrong_primary"], fx["secondary"])
    assert fx["expected_text"] in wrong["text"]
    assert wrong["segments"][0]["dual_agreed"] is False


def test_merge_marks_low_conf_segments():
    fx = _load_fixture("credit_primary_english_secondary.json")
    merged = merge_ears(fx["primary"], fx["secondary"])
    seg = merged["segments"][0]
    assert "low_conf" in seg
    assert "dual_agreed" in seg
