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
    assert "Ross defines a true" not in texts
    assert "boss by the rare ability" not in texts
    assert "Which brings us to the missing reality layer," not in texts
    assert "Which brings us to the missing reality layer, behind-the-scenes power." in texts
    for item in treatments:
        assert item["kind"] in TREATMENT_KINDS
        assert is_contiguous_attested_span(item["text"], item["cite"]["line"])


def test_arabic_fixture_honest_ceiling_one_maximal_line() -> None:
    result = enumerate_treatments(_load_fixture("clip_5a92132dc6de.json"))
    assert result["mode"] == "blocked"
    assert result["ceiling"] == 1
    assert len(result["treatments"]) == 1
    assert result["treatments"][0]["text"] == "لك كفاية عزبتني"


def test_english_fragment_crumbs_never_enumerate() -> None:
    transcript = {
        "language": "en",
        "lines": [
            {
                "start": 0.0,
                "text": (
                    "Ross defines a true boss by the rare ability to execute moves "
                    "the real streets actually accept."
                ),
            },
        ],
    }
    result = enumerate_treatments(transcript)
    texts = {item["text"] for item in result.get("treatments") or []}
    assert "Ross defines a true" not in texts
    assert "boss by the rare ability" not in texts
    assert "moves the real streets actually accept." not in texts
    assert transcript["lines"][0]["text"] in texts


def test_nested_hook_text_detects_subspan() -> None:
    assert is_nested_hook_text("عزبتني", "لك كفاية عزبتني")
    assert not is_nested_hook_text("لك كفاية عزبتني", "عزبتني")
