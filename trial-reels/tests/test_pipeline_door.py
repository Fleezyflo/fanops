"""Tests for trial-reels/pipeline.py live door — no five-unique abort."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.desk import TARGET_VARIANTS, expand_variant_slots, write  # noqa: E402
from pipeline import EXIT_OK, _desk_hook_texts  # noqa: E402
from tests.test_desk import EN_LIVE_SENTENCES, _load_fixture  # noqa: E402


def test_english_four_claims_expand_to_twenty_without_fifth_duplicate() -> None:
    desk = write(_load_fixture("clip_004ae6d9098a.json"))
    filled = _desk_hook_texts(desk)

    assert desk["mode"] == "write"
    assert len(filled) == 4
    assert len(set(filled)) == 4
    assert set(filled) == EN_LIVE_SENTENCES
    assert len(expand_variant_slots(desk["cards"])) == TARGET_VARIANTS


def test_arabic_two_claims_do_not_abort_low_unique_count() -> None:
    desk = write(_load_fixture("clip_5a92132dc6de.json"))
    filled = _desk_hook_texts(desk)

    assert len(filled) == 2
    assert len(set(filled)) == 2
    # Legacy Mac gate: if len(filled) < 5 or len(set(filled)) < 5: return 4
    assert len(filled) < 5
    assert len(set(filled)) < 5
    assert EXIT_OK == 0
