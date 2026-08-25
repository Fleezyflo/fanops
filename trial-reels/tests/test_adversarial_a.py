"""Tests for desk swarm validation and pipeline scoring."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.cover_qa import ocr_langs_for_language  # noqa: E402
from lib.desk import write  # noqa: E402
from lib.desk_swarm import validate_desk_result, write_and_validate  # noqa: E402
from lib.pipeline import score_run  # noqa: E402


def test_validate_rejects_permutation_anagrams() -> None:
    fake = {
        "mode": "write",
        "language": "ar",
        "cards": [
            {
                "hook": "result_first",
                "stack": "punch_cuts",
                "text": "عزبتني لك كفاية",
                "cite": {"line": "لك كفاية عزبتني"},
            },
            {
                "hook": "result_first",
                "stack": "open_loop",
                "text": "كفاية لك عزبتني",
                "cite": {"line": "لك كفاية عزبتني"},
            },
            {
                "hook": "result_first",
                "stack": "fake_out",
                "text": "لك كفاية عزبتني",
                "cite": {"line": "لك كفاية عزبتني"},
            },
            {
                "hook": "result_first",
                "stack": "end_loop",
                "text": "عزبتني كفاية لك",
                "cite": {"line": "لك كفاية عزبتني"},
            },
            {
                "hook": "mid_action",
                "stack": "punch_cuts",
                "text": "كفاية عزبتني لك",
                "cite": {"line": "لك كفاية عزبتني"},
            },
        ],
    }
    validation = validate_desk_result(fake)
    assert not validation["ok"]
    assert any("anagram" in issue for issue in validation["issues"])


def test_validate_rejects_nested_window_farm() -> None:
    cards = []
    nested_texts = [
        "لك كفاية عزبتني عزبتني",
        "كفاية عزبتني عزبتني",
        "لك كفاية عزبتني",
        "كفاية عزبتني",
        "عزبتني عزبتني",
    ]
    from lib.desk import VARIANT_SLOTS

    for (hook, stack), text in zip(VARIANT_SLOTS, nested_texts * 4):
        cards.append(
            {
                "hook": hook,
                "stack": stack,
                "text": text,
                "cite": {"line": "لك كفاية عزبتني عزبتني"},
            }
        )
    fake = {"mode": "write", "language": "ar", "cards": cards[:20]}
    validation = validate_desk_result(fake)
    assert not validation["ok"]
    assert any("nested" in issue for issue in validation["issues"])


def test_validate_rejects_non_contiguous_span() -> None:
    fake = {
        "mode": "write",
        "language": "en",
        "cards": [
            {
                "hook": "bold_claim",
                "stack": "punch_cuts",
                "text": "fails. So the next",
                "cite": {"line": "genuine street ties actually accept."},
            },
        ],
    }
    validation = validate_desk_result(fake)
    assert not validation["ok"]
    assert any("contiguous" in issue for issue in validation["issues"])


def test_write_and_validate_blocks_single_line_arabic() -> None:
    payload = write_and_validate(
        {
            "language": "ar",
            "lines": [{"start": 12.4, "text": "لك كفاية عزبتني"}],
        }
    )
    assert payload["desk"]["mode"] == "blocked"
    assert not payload["validation"]["ok"]


def test_ocr_langs_routes_english_to_eng() -> None:
    assert ocr_langs_for_language("en") == "eng"
    assert ocr_langs_for_language("ar") == "ara"


def test_pipeline_does_not_count_files_as_success() -> None:
    desk_blocked = write({"language": "ar", "lines": [{"start": 0, "text": "لك كفاية عزبتني"}]})
    score = score_run(
        clip_payloads=[{"clip_id": "ar_v01", "desk": desk_blocked}],
        stacks_landed=20,
        file_count=20,
        require_cover=False,
    )
    assert not score.success
    assert score.file_count == 20
    assert score.stacks_landed == 20
    assert score.shippable == 0
    assert "file count is not success" in score.message


def test_pipeline_marks_english_tess_eng() -> None:
    desk = write(
        {
            "language": "en",
            "lines": [{"start": 0, "text": "genuine street ties actually accept."}],
        }
    )
    score = score_run(
        clip_payloads=[{"clip_id": "en_v01", "desk": desk}],
        require_cover=False,
    )
    assert score.clip_scores[0].tess_langs == "eng"
