"""Tests for desk swarm validation and pipeline scoring."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.cover_qa import ocr_langs_for_language  # noqa: E402
from lib.desk import HOOKS, write  # noqa: E402
from lib.desk_swarm import validate_desk_result, write_and_validate  # noqa: E402
from lib.pipeline import score_run  # noqa: E402
from tests.test_desk import EN_LIVE_SENTENCES, _load_fixture  # noqa: E402


def test_validate_rejects_permutation_anagrams() -> None:
    fake = {
        "mode": "write",
        "language": "ar",
        "cards": [
            {"hook": "result_first", "text": "عزبتني لك كفاية", "cite": {"line": "لك كفاية عزبتني"}},
            {"hook": "mid_action", "text": "كفاية لك عزبتني", "cite": {"line": "لك كفاية عزبتني"}},
            {"hook": "direct_you", "text": "لك كفاية عزبتني", "cite": {"line": "لك كفاية عزبتني"}},
            {"hook": "bold_claim", "text": "عزبتني كفاية لك", "cite": {"line": "لك كفاية عزبتني"}},
            {"hook": "cold_proof", "text": "كفاية عزبتني لك", "cite": {"line": "لك كفاية عزبتني"}},
        ],
    }
    validation = validate_desk_result(fake)
    assert not validation["ok"]
    assert any("anagram" in issue for issue in validation["issues"])


def test_validate_rejects_nested_window_farm() -> None:
    fake = {
        "mode": "write",
        "language": "ar",
        "cards": [
            {"hook": "result_first", "text": "لك كفاية عزبتني عزبتني", "cite": {"line": "لك كفاية عزبتني عزبتني"}},
            {"hook": "mid_action", "text": "كفاية عزبتني عزبتني", "cite": {"line": "لك كفاية عزبتني عزبتني"}},
            {"hook": "direct_you", "text": "لك كفاية عزبتني", "cite": {"line": "لك كفاية عزبتني عزبتني"}},
            {"hook": "bold_claim", "text": "كفاية عزبتني", "cite": {"line": "لك كفاية عزبتني عزبتني"}},
            {"hook": "cold_proof", "text": "عزبتني عزبتني", "cite": {"line": "لك كفاية عزبتني عزبتني"}},
        ],
    }
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
                "text": "fails. So the next",
                "cite": {"line": "genuine street ties actually accept."},
            },
        ],
    }
    validation = validate_desk_result(fake)
    assert not validation["ok"]
    assert any("contiguous" in issue for issue in validation["issues"])


def test_live_arabic_fixture_ships() -> None:
    payload = write_and_validate(_load_fixture("clip_5a92132dc6de.json"))
    assert payload["desk"]["mode"] == "write"
    assert payload["desk"]["unique_texts"] == 2
    assert payload["validation"]["ok"], payload["validation"]["issues"]


def test_live_english_fixture_ships_four_sentences() -> None:
    payload = write_and_validate(_load_fixture("clip_004ae6d9098a.json"))
    assert payload["desk"]["mode"] == "write"
    assert set(card["text"] for card in payload["desk"]["cards"]) == EN_LIVE_SENTENCES
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
    desk = write(_load_fixture("clip_004ae6d9098a.json"))
    score = score_run(
        clip_payloads=[{"clip_id": "en_v01", "desk": desk}],
        require_cover=False,
    )
    assert score.clip_scores[0].tess_langs == "eng"


def test_pipeline_counts_distinct_attested_without_cover_fails_below_twenty() -> None:
    desk = write(_load_fixture("clip_5a92132dc6de.json"))
    texts = tuple(card["text"] for card in desk["cards"])
    payloads = [
        {
            "clip_id": f"ar_{index}",
            "desk": desk,
            "attested_words": (text,),
        }
        for index, text in enumerate(texts)
    ]
    score = score_run(clip_payloads=payloads, stacks_landed=len(payloads), require_cover=False)
    assert not score.success
    assert not score.passes_bar
    assert score.distinct_verified_texts == 2
    assert "cycling stacks is not a pass" in score.message
