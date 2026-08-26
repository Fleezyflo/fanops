"""Tests for trial-reels/pipeline.py live door — fail closed below 20 hooks."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.desk import TARGET_VARIANTS, write  # noqa: E402
from pipeline import EXIT_BLOCKED, _desk_hook_texts  # noqa: E402
from tests.test_desk import _load_fixture  # noqa: E402


def test_twenty_hook_fixture_fills_desk_json_texts() -> None:
    desk = write(_load_fixture("clip_twenty_hooks.json"))
    filled = _desk_hook_texts(desk)

    assert desk["mode"] == "write"
    assert len(filled) == TARGET_VARIANTS
    assert len(set(filled)) == TARGET_VARIANTS


def test_english_live_fixture_blocks_below_twenty() -> None:
    desk = write(_load_fixture("clip_004ae6d9098a.json"))
    filled = _desk_hook_texts(desk)

    assert desk["mode"] == "blocked"
    assert filled == []
    assert desk["claims_found"] == 4


def test_arabic_live_fixture_blocks_below_twenty() -> None:
    desk = write(_load_fixture("clip_5a92132dc6de.json"))
    filled = _desk_hook_texts(desk)

    assert desk["mode"] == "blocked"
    assert filled == []
    assert desk["claims_found"] == 2
    assert EXIT_BLOCKED == 1
