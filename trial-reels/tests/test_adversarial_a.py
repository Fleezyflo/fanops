"""Tests for desk swarm validation and pipeline scoring."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.cover_qa import ocr_langs_for_language  # noqa: E402
from lib.desk import expand_variant_slots, write  # noqa: E402
from lib.desk_swarm import validate_desk_result, write_and_validate  # noqa: E402
from lib.pipeline import score_run  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_validate_rejects_permutation_anagrams() -> None:
    fake = {
        "mode": "write",
        "language": "ar",
        "cards": [
            {"hook": "result_first", "text": "عزبتني لك كفاية", "cite": {"line": "لك كفاية عزبتني"}},
            {"hook": "mid_action", "text": "كفاية لك عزبتني", "cite": {"line": "لك كفاية عزبتني"}},
            {"hook": "direct_you", "text": "لك كفاية عزبتني", "cite": {"line": "لك كفاية عزبتني"}},
        ],
        "claims": [],
    }
    validation = validate_desk_result(fake)
    assert not validation["ok"]
    assert any("anagram" in issue for issue in validation["issues"])


def test_validate_rejects_non_contiguous_span() -> None:
    fake = {
        "mode": "write",
        "language": "en",
        "cards": [
            {
                "hook": "bold_claim",
                "text": "fails. So the next",
                "cite": {"line": "genuine street ties actually accept."},
            },
        ],
        "claims": [],
    }
    validation = validate_desk_result(fake)
    assert not validation["ok"]
    assert any("contiguous" in issue for issue in validation["issues"])


def test_write_and_validate_ships_live_arabic_clip() -> None:
    transcript = json.loads((FIXTURES / "clip_5a92132dc6de.json").read_text(encoding="utf-8"))
    payload = write_and_validate(transcript)
    assert payload["desk"]["mode"] == "write"
    assert payload["validation"]["ok"]
    assert len(expand_variant_slots(payload["desk"]["cards"])) == 20


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


def test_pipeline_marks_honest_subset_when_texts_below_target() -> None:
    desk = write(_load_fixture("clip_004ae6d9098a.json"))
    score = score_run(
        clip_payloads=[{"clip_id": "en_v01", "desk": desk, "attested_words": (desk["claims"][0]["text"],)}],
        stacks_landed=1,
        file_count=1,
        require_cover=False,
    )
    assert score.success
    assert score.distinct_verified_texts == 1
    assert score.target_variants == 20
    assert "honest subset" in score.message


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
