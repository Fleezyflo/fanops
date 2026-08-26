"""Adversarial root-fix tests — nested farms, crumbs, and stack cycling must fail."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.desk import HOOKS, TARGET_VARIANTS, write  # noqa: E402
from lib.desk_swarm import validate_desk_result  # noqa: E402
from lib.pipeline import score_run  # noqa: E402
from lib.runner import plan_variants  # noqa: E402
from lib.treatments import enumerate_treatments, is_nested_hook_text  # noqa: E402
from tests.test_desk import EN_LIVE_SENTENCES, _load_fixture  # noqa: E402

_AR_MERGED_LINE = "لك كفاية عزبتني عزبتني"
_AR_NESTED_FARM = (
    "لك كفاية عزبتني عزبتني",
    "كفاية عزبتني عزبتني",
    "لك كفاية عزبتني",
    "كفاية عزبتني",
    "عزبتني عزبتني",
)

_EN_CRUMBS = (
    "Ross defines a true",
    "boss by the rare ability",
    "evaporates the second real-world leverage is required.",
    "So the next",
    "fails.",
)


def test_arabic_merged_whisper_line_does_not_nested_farm() -> None:
    """Concatenated Arabic whisper lines must not yield nested substring hooks."""
    transcript = {
        "language": "ar",
        "lines": [
            {"start": 12.2, "text": "لك كفاية عزبتني"},
            {"start": 14.0, "text": "عزبتني"},
        ],
    }
    result = enumerate_treatments(transcript)

    assert result["mode"] == "blocked"
    assert result["claims_found"] == 1
    texts = [item["text"] for item in result["treatments"]]
    assert texts == ["لك كفاية عزبتني"]
    forbidden = (
        "لك كفاية عزبتني عزبتني",
        "كفاية عزبتني عزبتني",
        "كفاية عزبتني",
        "عزبتني عزبتني",
        "عزبتني",
    )
    for nested in forbidden:
        assert nested not in texts


def test_arabic_nested_substrings_rejected_by_validation() -> None:
    """Desk validation must reject the live Mac nested-window farm pattern."""
    cards = [
        {
            "hook": HOOKS[i % len(HOOKS)],
            "stack": stack,
            "text": text,
            "cite": {"line": _AR_MERGED_LINE},
        }
        for i, (stack, text) in enumerate(
            zip(
                ("punch_cuts", "open_loop", "fake_out", "end_loop", "punch_cuts"),
                _AR_NESTED_FARM,
                strict=True,
            )
        )
    ]
    fake = {"mode": "write", "language": "ar", "cards": cards}
    validation = validate_desk_result(fake)
    assert not validation["ok"]
    assert any("nested" in issue for issue in validation["issues"])


def test_english_whisper_crumbs_never_ship() -> None:
    """EN partial lines and leftover tails must not become treatments."""
    result = write(_load_fixture("clip_004ae6d9098a.json"))

    assert result["mode"] == "blocked"
    texts = {item["text"] for item in result.get("treatments") or []}
    assert texts == EN_LIVE_SENTENCES
    for crumb in _EN_CRUMBS:
        assert crumb not in texts
        assert not any(crumb in text for text in texts if text not in EN_LIVE_SENTENCES)


def test_english_crumb_transcript_blocks_without_clause_farm() -> None:
    transcript = {
        "language": "en",
        "lines": [
            {"start": 0.0, "text": "Ross defines a true"},
            {"start": 1.0, "text": "boss by the rare ability"},
            {"start": 2.0, "text": "evaporates the second real-world leverage is required."},
            {"start": 3.0, "text": "So the next"},
        ],
    }
    result = write(transcript)

    assert result["mode"] == "blocked"
    assert result["cards"] == []
    texts = {item["text"] for item in result.get("treatments") or []}
    assert "Ross defines a true" not in texts
    assert "boss by the rare ability" not in texts
    assert "So the next" not in texts


def test_four_unique_texts_cannot_cycle_to_twenty_files() -> None:
    """Four honest treatments must block — no 20-file stack cycling path."""
    result = write(_load_fixture("clip_004ae6d9098a.json"))

    assert result["mode"] == "blocked"
    assert result["claims_found"] == 4
    assert result["cards"] == []
    assert plan_variants(result, clip_id="clip_004ae6d9098a", source_duration_s=60.0) == []

    score = score_run(
        clip_payloads=[{"clip_id": "clip_004ae6d9098a", "desk": result}],
        stacks_landed=20,
        file_count=20,
        require_cover=False,
    )
    assert not score.success
    assert score.shippable == 0
    assert "file count is not success" in score.message


def test_twenty_file_fake_desk_with_four_unique_texts_fails_validation() -> None:
    """Simulated stack cycling: 20 cards repeating 4 texts must fail desk validation."""
    base_texts = list(EN_LIVE_SENTENCES)
    from lib.desk import VARIANT_SLOTS

    cards = []
    for index, (hook, stack) in enumerate(VARIANT_SLOTS):
        text = base_texts[index % len(base_texts)]
        cards.append(
            {
                "hook": hook,
                "stack": stack,
                "text": text,
                "cite": {"line": text},
            }
        )
    fake = {"mode": "write", "language": "en", "cards": cards}
    validation = validate_desk_result(fake)
    assert not validation["ok"]
    assert validation["unique_texts"] == 4
    assert any("distinct" in issue for issue in validation["issues"])


def test_nested_hook_text_catches_arabic_repeat_word() -> None:
    assert is_nested_hook_text("عزبتني", "لك كفاية عزبتني")
    assert is_nested_hook_text("كفاية عزبتني", "لك كفاية عزبتني عزبتني")
    assert not is_nested_hook_text(
        "لك كفاية عزبتني",
        "It's a test of genuine respect that the modern corporate industry completely fails.",
    )
