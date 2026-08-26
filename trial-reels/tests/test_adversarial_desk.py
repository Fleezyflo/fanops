"""Adversarial desk tests — nested farms, crumbs, and stack cycling must fail closed."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))

from lib.desk import HOOKS, TARGET_VARIANTS, expand_variant_slots, write  # noqa: E402
from lib.desk_swarm import validate_desk_result  # noqa: E402
from lib.pipeline import score_run  # noqa: E402
from lib.runner import plan_variants  # noqa: E402
from lib.stacks import STACK_NAMES  # noqa: E402
from lib.treatments import (  # noqa: E402
    _is_crumb,
    enumerate_treatments,
    is_nested_hook_text,
)
from tests.test_desk import _load_fixture  # noqa: E402

EN_LIVE_SENTENCES = frozenset(
    {
        "inside a padded recording booth creates a powerful illusion, one that completely evaporates the second real-world leverage is required.",
        "Ross defines a true boss by the rare ability to execute moves the real streets actually accept.",
        "It's a test of genuine respect that the modern corporate industry completely fails.",
    }
)

EN_CRUMBS = (
    "Ross defines a true",
    "boss by the rare ability",
    "evaporates the second real-world leverage is required.",
    "So the next",
    "fails.",
    "Which brings us to the missing reality layer, behind-the-scenes power.",
    "inside a padded recording booth creates a powerful",
    "one that completely evaporates",
)

AR_NESTED_FARM = (
    "لك كفاية عزبتني عزبتني",
    "كفاية عزبتني عزبتني",
    "لك كفاية عزبتني",
    "كفاية عزبتني",
    "عزبتني عزبتني",
    "عزبتني",
)


def _fake_cards(texts: list[str], *, cite_line: str) -> list[dict]:
    slots = [(hook, stack) for hook in HOOKS for stack in STACK_NAMES]
    return [
        {
            "hook": hook,
            "stack": stack,
            "text": text,
            "cite": {"line": cite_line, "start": 0.0},
        }
        for (hook, stack), text in zip(slots, texts, strict=False)
    ]


def test_live_english_write_returns_only_complete_sentences() -> None:
    result = write(_load_fixture("clip_004ae6d9098a.json"))

    assert result["mode"] == "blocked"
    assert result["cards"] == []
    texts = {item["text"] for item in result.get("treatments") or []}
    assert texts == set(EN_LIVE_SENTENCES)
    for text in texts:
        assert text[-1] in ".!?"
        assert text not in EN_CRUMBS


def test_live_english_write_rejects_fourth_apposition_stitch() -> None:
    stitched = (
        "Which brings us to the missing reality layer, behind-the-scenes power."
    )
    result = write(_load_fixture("clip_004ae6d9098a.json"))
    texts = {item["text"] for item in result.get("treatments") or []}
    assert stitched not in texts
    assert _is_crumb(stitched, "en", tokens=[], is_full_whisper_line=True)


def test_english_crumbs_are_not_shipped() -> None:
    transcript = {
        "language": "en",
        "lines": [
            {"start": 0.0, "text": "Ross defines a true"},
            {"start": 1.0, "text": "boss by the rare ability to execute moves."},
            {"start": 2.0, "text": "evaporates the second real-world leverage is required."},
            {"start": 3.0, "text": "So the next"},
        ],
    }
    result = write(transcript)

    assert result["mode"] == "blocked"
    texts = {item["text"] for item in result.get("treatments") or []}
    for crumb in EN_CRUMBS:
        assert crumb not in texts
    assert result["cards"] == []


def test_arabic_nested_substrings_fail_closed() -> None:
    merged = {
        "language": "ar",
        "lines": [{"start": 0.0, "text": "لك كفاية عزبتني عزبتني"}],
    }
    result = write(merged)

    assert result["mode"] == "blocked"
    assert result["claims_found"] == 1
    texts = [item["text"] for item in result.get("treatments") or []]
    assert texts == ["لك كفاية عزبتني عزبتني"]
    for nested in AR_NESTED_FARM:
        if nested != texts[0]:
            assert nested not in texts
            assert is_nested_hook_text(nested, texts[0])


def test_arabic_two_line_fixture_keeps_maximal_line_only() -> None:
    result = write(_load_fixture("clip_5a92132dc6de.json"))

    assert result["mode"] == "blocked"
    texts = [item["text"] for item in result.get("treatments") or []]
    assert texts == ["لك كفاية عزبتني"]
    assert "عزبتني" not in texts
    assert "عذبتيني" not in " ".join(texts)


def test_twenty_file_cycling_of_four_unique_texts_fails_validation() -> None:
    base = [
        "Ross defines a true boss by the rare ability to execute moves the real streets actually accept.",
        "inside a padded recording booth creates a powerful illusion, one that completely evaporates the second real-world leverage is required.",
        "It's a test of genuine respect that the modern corporate industry completely fails.",
        "Which brings us to the missing reality layer, behind-the-scenes power.",
    ]
    cycled = [base[index % len(base)] for index in range(TARGET_VARIANTS)]
    fake = {
        "mode": "write",
        "language": "en",
        "cards": _fake_cards(cycled, cite_line=base[0]),
    }

    validation = validate_desk_result(fake)
    assert not validation["ok"]
    assert validation["unique_texts"] == 4
    assert expand_variant_slots(fake["cards"]) == []
    assert plan_variants(fake, clip_id="cycle", source_duration_s=60.0) == []


def test_pipeline_rejects_twenty_files_with_only_four_distinct_hooks() -> None:
    base = list(EN_LIVE_SENTENCES)
    cycled = [base[index % len(base)] for index in range(TARGET_VARIANTS)]
    fake_desk = {
        "mode": "write",
        "language": "en",
        "cards": _fake_cards(cycled, cite_line=base[0]),
    }
    score = score_run(
        clip_payloads=[{"clip_id": "en_cycle", "desk": fake_desk}],
        stacks_landed=TARGET_VARIANTS,
        file_count=TARGET_VARIANTS,
        require_cover=False,
    )
    assert not score.success
    assert score.distinct_verified_texts == 0
    assert "file count is not success" in score.message


def test_nested_arabic_window_farm_rejected_by_validator() -> None:
    cite = "لك كفاية عزبتني عزبتني"
    cycled = [AR_NESTED_FARM[index % len(AR_NESTED_FARM)] for index in range(TARGET_VARIANTS)]
    fake = {
        "mode": "write",
        "language": "ar",
        "cards": _fake_cards(cycled, cite_line=cite),
    }
    validation = validate_desk_result(fake)
    assert not validation["ok"]


def test_enumerate_never_emits_clause_windows_on_english_fixture() -> None:
    result = enumerate_treatments(_load_fixture("clip_004ae6d9098a.json"))
    texts = {item["text"] for item in result.get("treatments") or []}
    assert "Ross defines a true" not in texts
    assert "boss by the rare ability" not in texts
    assert "So the next" not in texts


def test_write_never_ships_nested_substring_pair() -> None:
    """Regression: two valid lines where one is a strict sub-span must fail closed."""
    transcript = {
        "language": "ar",
        "lines": [
            {"start": 0.0, "text": "لك كفاية عزبتني"},
            {"start": 1.0, "text": "عزبتني"},
        ],
    }
    result = write(transcript)

    assert result["mode"] == "blocked"
    assert result["cards"] == []
    texts = [item["text"] for item in result.get("treatments") or []]
    assert texts == ["لك كفاية عزبتني"]
    assert "عزبتني" not in texts
