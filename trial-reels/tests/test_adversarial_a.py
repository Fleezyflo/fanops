"""Tests for desk swarm validation and pipeline scoring."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.cover_qa import ocr_langs_for_language  # noqa: E402
from lib.desk import HOOKS, TARGET_VARIANTS, write  # noqa: E402
from lib.desk_swarm import validate_desk_result, write_and_validate  # noqa: E402
from lib.pipeline import score_run  # noqa: E402
from tests.test_desk import _load_fixture  # noqa: E402


def test_validate_rejects_permutation_anagrams() -> None:
    fake = {
        "mode": "write",
        "language": "ar",
        "cards": [
            {"hook": HOOKS[0], "stack": "punch_cuts", "text": "عزبتني لك كفاية", "cite": {"line": "لك كفاية عزبتني"}},
            {"hook": HOOKS[1], "stack": "open_loop", "text": "كفاية لك عزبتني", "cite": {"line": "لك كفاية عزبتني"}},
            {"hook": HOOKS[2], "stack": "fake_out", "text": "لك كفاية عزبتني", "cite": {"line": "لك كفاية عزبتني"}},
            {"hook": HOOKS[3], "stack": "end_loop", "text": "عزبتني كفاية لك", "cite": {"line": "لك كفاية عزبتني"}},
            {"hook": HOOKS[4], "stack": "punch_cuts", "text": "كفاية عزبتني لك", "cite": {"line": "لك كفاية عزبتني"}},
        ],
    }
    validation = validate_desk_result(fake)
    assert not validation["ok"]
    assert any("distinct" in issue or "cards" in issue for issue in validation["issues"])


def test_validate_rejects_nested_window_farm() -> None:
    fake = {
        "mode": "write",
        "language": "ar",
        "cards": [
            {"hook": HOOKS[0], "stack": "punch_cuts", "text": "لك كفاية عزبتني عزبتني", "cite": {"line": "لك كفاية عزبتني عزبتني"}},
            {"hook": HOOKS[1], "stack": "open_loop", "text": "كفاية عزبتني عزبتني", "cite": {"line": "لك كفاية عزبتني عزبتني"}},
            {"hook": HOOKS[2], "stack": "fake_out", "text": "لك كفاية عزبتني", "cite": {"line": "لك كفاية عزبتني عزبتني"}},
            {"hook": HOOKS[3], "stack": "end_loop", "text": "كفاية عزبتني", "cite": {"line": "لك كفاية عزبتني عزبتني"}},
            {"hook": HOOKS[4], "stack": "punch_cuts", "text": "عزبتني عزبتني", "cite": {"line": "لك كفاية عزبتني عزبتني"}},
        ],
    }
    validation = validate_desk_result(fake)
    assert not validation["ok"]


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
    assert any("contiguous" in issue or "cards" in issue for issue in validation["issues"])


def test_live_arabic_fixture_fails_closed() -> None:
    payload = write_and_validate(_load_fixture("clip_5a92132dc6de.json"))
    assert payload["desk"]["mode"] == "blocked"
    assert payload["desk"]["claims_found"] == 2
    assert not payload["validation"]["ok"]


def test_live_english_fixture_fails_closed() -> None:
    payload = write_and_validate(_load_fixture("clip_004ae6d9098a.json"))
    assert payload["desk"]["mode"] == "blocked"
    assert payload["desk"]["claims_found"] == 4
    assert not payload["validation"]["ok"]


def test_twenty_hook_fixture_passes_validation() -> None:
    payload = write_and_validate(_load_fixture("clip_twenty_hooks.json"))
    assert payload["desk"]["mode"] == "write"
    assert payload["desk"]["unique_texts"] == TARGET_VARIANTS
    assert payload["validation"]["ok"], payload["validation"]["issues"]


def test_ocr_langs_routes_english_to_eng() -> None:
    assert ocr_langs_for_language("en") == "eng"
    assert ocr_langs_for_language("ar") == "ara"


def test_pipeline_does_not_count_files_as_success() -> None:
    desk_blocked = write({"language": "ar", "lines": [{"start": 0, "text": "ترجمة نانسي قنقر"}]})
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
    desk = write(_load_fixture("clip_twenty_hooks.json"))
    score = score_run(
        clip_payloads=[{"clip_id": "en_v01", "desk": desk}],
        require_cover=False,
    )
    assert score.clip_scores[0].tess_langs == "eng"
