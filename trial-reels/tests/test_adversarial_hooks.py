"""Adversarial tests — nested farms, EN crumbs, and stack-cycling must never ship."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))

from lib.desk import HOOKS, TARGET_VARIANTS, expand_variant_slots, write  # noqa: E402
from lib.desk_swarm import validate_desk_result  # noqa: E402
from lib.stacks import STACK_NAMES  # noqa: E402
from lib.treatments import is_nested_hook_text  # noqa: E402


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


EN_CRUMBS = frozenset(
    {
        "Ross defines a true",
        "boss by the rare ability",
        "evaporates the second",
        "So the next",
        "fails.",
        "inside a padded recording booth",
        "by the rare ability",
    }
)

AR_NESTED_FARM = frozenset(
    {
        "لك كفاية عزبتني عزبتني",
        "كفاية عزبتني عزبتني",
        "كفاية عزبتني",
        "عزبتني عزبتني",
        "عزبتني",
    })


def test_arabic_write_never_emits_nested_substring_farm() -> None:
    result = write(_load_fixture("clip_5a92132dc6de.json"))

    assert result["mode"] == "blocked"
    assert result["cards"] == []
    texts = {item["text"] for item in result.get("treatments") or []}
    assert texts == {"لك كفاية عزبتني"}
    assert texts.isdisjoint(AR_NESTED_FARM)
    for shorter in texts:
        for longer in texts:
            if shorter != longer:
                assert not is_nested_hook_text(shorter, longer)


def test_english_write_rejects_whisper_crumbs() -> None:
    result = write(_load_fixture("clip_004ae6d9098a.json"))

    assert result["mode"] == "blocked"
    assert result["cards"] == []
    texts = {item["text"] for item in result.get("treatments") or []}
    assert len(texts) == 4
    assert texts.isdisjoint(EN_CRUMBS)
    for crumb in EN_CRUMBS:
        assert crumb not in texts


def test_four_unique_treatments_cannot_expand_to_twenty_cards() -> None:
    """Stack cycling ≤4 unique texts into 20 hook×stack slots must fail closed."""
    result = write(_load_fixture("clip_004ae6d9098a.json"))

    assert result["mode"] == "blocked"
    assert result.get("claims_found") == 4
    assert len(result.get("cards") or []) == 0
    assert expand_variant_slots(result.get("cards") or []) == []

    four_sentences = [
        "Ross defines a true boss by the rare ability to execute moves the real streets actually accept.",
        "inside a padded recording booth creates a powerful illusion, one that completely evaporates the second real-world leverage is required.",
        "It's a test of genuine respect that the modern corporate industry completely fails.",
        "Which brings us to the missing reality layer, behind-the-scenes power.",
    ]
    fake_cycled = {
        "mode": "write",
        "language": "en",
        "cards": [
            {
                "hook": HOOKS[i % len(HOOKS)],
                "stack": STACK_NAMES[i % len(STACK_NAMES)],
                "text": text,
                "cite": {"line": text},
            }
            for i, text in enumerate(four_sentences * 5)
            if i < TARGET_VARIANTS
        ],
    }
    validation = validate_desk_result(fake_cycled)
    assert not validation["ok"]
    assert any("distinct" in issue for issue in validation["issues"])
