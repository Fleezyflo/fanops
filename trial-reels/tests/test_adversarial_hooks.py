"""Adversarial desk/treatment tests — nested farms, crumbs, stack cycling."""

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
from lib.treatments import (  # noqa: E402
    enumerate_treatments,
    is_nested_hook_text,
)
from tests.test_desk import _load_fixture  # noqa: E402

EN_LIVE_COMPLETE_SENTENCES = frozenset(
    {
        "inside a padded recording booth creates a powerful illusion, one that completely evaporates the second real-world leverage is required.",
        "Which brings us to the missing reality layer, behind-the-scenes power.",
        "Ross defines a true boss by the rare ability to execute moves the real streets actually accept.",
        "It's a test of genuine respect that the modern corporate industry completely fails.",
    }
)

EN_FORBIDDEN_CRUMBS = frozenset(
    {
        "Ross defines a true",
        "boss by the rare ability",
        "evaporates the second",
        "So the next",
        "fails.",
        "fails. So the next",
        "the respect that the modern corporate in",
    }
)

AR_NESTED_FARM = frozenset(
    {
        "لك كفاية عزبتني عزبتني",
        "كفاية عزبتني عزبتني",
        "لك كفاية عزبتني",
        "كفاية عزبتني",
        "عزبتني عزبتني",
        "عزبتني",
    }
)


def test_arabic_concat_line_never_emits_nested_window_farm() -> None:
    transcript = {
        "language": "ar",
        "lines": [{"start": 12.2, "text": "لك كفاية عزبتني عزبتني"}],
    }
    result = enumerate_treatments(transcript)
    texts = {item["text"] for item in result["treatments"]}
    assert result["mode"] == "blocked"
    assert texts <= {"لك كفاية عزبتني عزبتني"}
    assert not (texts & AR_NESTED_FARM - {"لك كفاية عزبتني عزبتني"})


def test_arabic_dual_line_drops_nested_shorter_line() -> None:
    result = write(_load_fixture("clip_5a92132dc6de.json"))
    texts = [item["text"] for item in result.get("treatments") or []]
    assert result["mode"] == "blocked"
    assert texts == ["لك كفاية عزبتني"]
    assert is_nested_hook_text("عزبتني", "لك كفاية عزبتني")


def test_english_live_returns_only_complete_stitched_sentences() -> None:
    result = write(_load_fixture("clip_004ae6d9098a.json"))
    texts = {item["text"] for item in result.get("treatments") or []}

    assert result["mode"] == "blocked"
    assert result["claims_found"] == 4
    assert texts == EN_LIVE_COMPLETE_SENTENCES
    assert not (texts & EN_FORBIDDEN_CRUMBS)
    for sentence in texts:
        assert sentence.rstrip().endswith((".", "!", "?"))


def test_english_whisper_crumbs_never_ship() -> None:
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
    texts = {item["text"] for item in result.get("treatments") or []}

    assert result["mode"] == "blocked"
    assert result["cards"] == []
    assert not (texts & EN_FORBIDDEN_CRUMBS)


def test_four_unique_texts_cannot_cycle_to_twenty_cards() -> None:
    """Stack cycling: repeating ≤4 hooks across 20 hook×stack slots must fail validation."""
    four_texts = list(EN_LIVE_COMPLETE_SENTENCES)
    fake_cards = []
    for index, (hook, stack) in enumerate(
        [(h, s) for h in HOOKS for s in STACK_NAMES]
    ):
        text = four_texts[index % len(four_texts)]
        fake_cards.append(
            {
                "hook": hook,
                "stack": stack,
                "text": text,
                "cite": {"line": text, "start": 0.0},
            }
        )

    fake_desk = {
        "mode": "write",
        "language": "en",
        "cards": fake_cards,
        "claims": [{"text": c["text"], "cite": c["cite"]} for c in fake_cards],
    }
    validation = validate_desk_result(fake_desk)
    assert not validation["ok"]
    assert validation["unique_texts"] == 4
    plans = plan_variants(fake_desk, clip_id="cycle_attack", source_duration_s=60.0)
    assert plans == []


def test_write_blocks_before_building_twenty_mp4_plan_from_sparse_transcript() -> None:
    desk = write(_load_fixture("clip_5a92132dc6de.json"))
    plans = plan_variants(desk, clip_id="clip_5a92132dc6de", source_duration_s=30.0)
    assert desk["mode"] == "blocked"
    assert desk["cards"] == []
    assert plans == []


def test_english_clause_splits_stay_non_nested_siblings_only() -> None:
    """Comma clause siblings on one stitched sentence must not include strict sub-spans."""
    transcript = {
        "language": "en",
        "lines": [
            {
                "start": 0.0,
                "text": "Ross defines a true boss, by the rare ability to execute moves the real streets actually accept.",
            }
        ],
    }
    result = enumerate_treatments(transcript)
    texts = [item["text"] for item in result["treatments"]]
    for shorter in texts:
        for longer in texts:
            if shorter != longer and is_nested_hook_text(shorter, longer):
                raise AssertionError(f"nested pair shipped: {shorter!r} inside {longer!r}")
