"""Adversarial root-fix tests — nested farms, EN crumbs, stack cycling must fail closed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))

from lib.desk import HOOKS, TARGET_VARIANTS, write  # noqa: E402
from lib.desk_swarm import validate_desk_result  # noqa: E402
from lib.runner import plan_variants  # noqa: E402
from lib.stacks import STACK_NAMES  # noqa: E402
from lib.treatments import enumerate_treatments, is_nested_hook_text  # noqa: E402
from tests.test_desk import EN_LIVE_SENTENCES, _load_fixture  # noqa: E402

NESTED_AR_FARM = [
    "لك كفاية عزبتني عزبتني",
    "كفاية عزبتني عزبتني",
    "لك كفاية عزبتني",
    "كفاية عزبتني",
    "عزبتني عزبتني",
]

EN_CRUMB_LINES = [
    "Ross defines a true",
    "boss by the rare ability",
    "one that completely evaporates the second",
    "So the next",
    "evaporates the second real-world leverage is required.",
    "leftover So the next",
]


def test_arabic_nested_substring_farm_never_ships_twenty() -> None:
    """Merged-line nested windows cannot inflate to 20 write-mode hooks."""
    merged = {
        "language": "ar",
        "lines": [{"start": 12.2, "text": "لك كفاية عزبتني عزبتني"}],
    }
    result = write(merged)

    assert result["mode"] == "blocked"
    assert result["cards"] == []
    assert len(result.get("treatments") or []) == 1
    assert result["treatments"][0]["text"] == "لك كفاية عزبتني عزبتني"
    assert len({item["text"] for item in result.get("treatments") or []}) < TARGET_VARIANTS
    for shorter in NESTED_AR_FARM:
        for longer in NESTED_AR_FARM:
            if shorter != longer and is_nested_hook_text(shorter, longer):
                assert result["mode"] == "blocked"


def test_arabic_two_whisper_lines_drop_strict_substring() -> None:
    result = write(_load_fixture("clip_5a92132dc6de.json"))

    assert result["mode"] == "blocked"
    texts = {item["text"] for item in result.get("treatments") or []}
    assert texts == {"لك كفاية عزبتني"}
    assert "عزبتني" not in texts
    assert "عذبتيني" not in " ".join(texts)


def test_english_whisper_crumbs_never_become_treatments() -> None:
    for line in EN_CRUMB_LINES:
        payload = enumerate_treatments({"language": "en", "lines": [{"start": 0.0, "text": line}]})
        assert payload["mode"] == "blocked", f"crumb shipped as treatment: {line!r}"
        assert not payload.get("treatments"), f"crumb enumerated: {line!r}"


def test_live_english_write_returns_exact_four_sentences_not_crumbs() -> None:
    result = write(_load_fixture("clip_004ae6d9098a.json"))

    assert result["mode"] == "blocked"
    texts = {item["text"] for item in result.get("treatments") or []}
    assert texts == EN_LIVE_SENTENCES
    for crumb in ("Ross defines a true", "boss by the rare ability", "So the next", "fails."):
        assert crumb not in texts
    for text in texts:
        assert text.rstrip().endswith((".", "!", "?"))


def test_stack_cycling_four_texts_into_twenty_cards_fails_validation() -> None:
    """Hook×stack cycling of ≤4 unique texts must not pass desk validation."""
    treatments = enumerate_treatments(_load_fixture("clip_004ae6d9098a.json"))["treatments"]
    base_texts = [item["text"] for item in treatments]
    assert len(set(base_texts)) == 4

    cards = []
    for index in range(TARGET_VARIANTS):
        cards.append(
            {
                "hook": HOOKS[index % len(HOOKS)],
                "stack": STACK_NAMES[index % len(STACK_NAMES)],
                "text": base_texts[index % len(base_texts)],
                "cite": {"line": base_texts[index % len(base_texts)]},
            }
        )

    fake = {"mode": "write", "language": "en", "cards": cards, "claims": cards}
    validation = validate_desk_result(fake)

    assert not validation["ok"]
    assert any("distinct" in issue for issue in validation["issues"])
    assert any("duplicate" in issue for issue in validation["issues"])
    assert plan_variants(fake, clip_id="clip_004ae6d9098a", source_duration_s=60.0) == []


def test_blocked_english_live_fixture_plans_zero_renders() -> None:
    desk = write(_load_fixture("clip_004ae6d9098a.json"))
    assert desk["mode"] == "blocked"
    assert desk["claims_found"] == 4
    assert plan_variants(desk, clip_id="clip_004ae6d9098a", source_duration_s=60.0) == []


def test_forbidden_arabic_rewrite_blocks() -> None:
    payload = write({"language": "ar", "lines": [{"start": 0.0, "text": "عذبتيني كفاية"}]})
    assert payload["mode"] == "blocked"
    assert payload["cards"] == []
