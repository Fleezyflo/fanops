"""Contract tests for trial-reels hook edit policies."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.hooks import (
    HOOK_POLICIES,
    LyricEvent,
    cut_spec,
    hook_window,
    policies_differ,
    policy_identity_key,
    stamp_lyric_events,
)

RECIPES = json.loads((Path(__file__).resolve().parents[1] / "recipes.json").read_text())

SAMPLE_LYRICS = [
    LyricEvent(10.0, 11.0, "opening bar"),
    LyricEvent(12.0, 13.0, "middle heat"),
    LyricEvent(14.0, 15.0, "did you catch that"),
    LyricEvent(16.0, 17.0, "closing proof"),
    LyricEvent(18.0, 19.0, "final line"),
]

CITE_START = 10.0
TOTAL = 30.0


class TestCutSpec:
    def test_starts_at_cite(self):
        start, length = cut_spec(CITE_START, TOTAL)
        assert start == CITE_START

    def test_length_capped_at_eight(self):
        _, length = cut_spec(5.0, 30.0)
        assert length == 8.0

    def test_length_remaining_when_shorter(self):
        _, length = cut_spec(28.0, 30.0)
        assert length == 2.0


class TestHookWindowsDiffer:
    def test_five_policies_are_distinct(self):
        assert policies_differ()

    def test_pairwise_identity_keys_unique(self):
        keys = [
            policy_identity_key(p, CITE_START, TOTAL)
            for p in HOOK_POLICIES
        ]
        assert len(keys) == len(set(keys))

    @pytest.mark.parametrize("policy", HOOK_POLICIES)
    def test_hook_out_after_in(self, policy):
        w = hook_window(policy, cite_start_s=CITE_START, total_duration_s=TOTAL)
        assert w.hook_out_s > w.hook_in_s
        assert w.cut_start_s == CITE_START
        assert w.cut_length_s == 8.0

    def test_result_first_early_window(self):
        w = hook_window("result_first", cite_start_s=CITE_START, total_duration_s=TOTAL)
        mid = hook_window("mid_action", cite_start_s=CITE_START, total_duration_s=TOTAL)
        assert w.hook_in_s < mid.hook_in_s

    def test_cold_proof_late_window(self):
        cold = hook_window("cold_proof", cite_start_s=CITE_START, total_duration_s=TOTAL)
        bold = hook_window("bold_claim", cite_start_s=CITE_START, total_duration_s=TOTAL)
        assert cold.hook_in_s > bold.hook_in_s


class TestLyricStamping:
    def test_result_first_stamps_first_half(self):
        stamped = stamp_lyric_events(
            "result_first", SAMPLE_LYRICS,
            cite_start_s=CITE_START, total_duration_s=TOTAL,
        )
        assert all(e.start_s < CITE_START + 4.0 for e in stamped)
        assert len(stamped) >= 1

    def test_mid_action_stamps_middle(self):
        stamped = stamp_lyric_events(
            "mid_action", SAMPLE_LYRICS,
            cite_start_s=CITE_START, total_duration_s=TOTAL,
        )
        assert any("middle" in e.text for e in stamped)

    def test_direct_you_filters_you_lines(self):
        stamped = stamp_lyric_events(
            "direct_you", SAMPLE_LYRICS,
            cite_start_s=CITE_START, total_duration_s=TOTAL,
        )
        assert all("you" in e.text.lower() for e in stamped)
        assert len(stamped) == 1

    def test_bold_claim_first_line_only(self):
        stamped = stamp_lyric_events(
            "bold_claim", SAMPLE_LYRICS,
            cite_start_s=CITE_START, total_duration_s=TOTAL,
        )
        assert len(stamped) == 1
        assert stamped[0].text == "opening bar"

    def test_cold_proof_closing_band(self):
        stamped = stamp_lyric_events(
            "cold_proof", SAMPLE_LYRICS,
            cite_start_s=CITE_START, total_duration_s=TOTAL,
        )
        assert any("proof" in e.text or "final" in e.text for e in stamped)

    def test_stamp_sets_differ_across_policies(self):
        sets = {
            p: tuple(e.text for e in stamp_lyric_events(
                p, SAMPLE_LYRICS, cite_start_s=CITE_START, total_duration_s=TOTAL,
            ))
            for p in HOOK_POLICIES
        }
        assert len(set(sets.values())) == len(HOOK_POLICIES)

    def test_open_loop_drop_last(self):
        stamped = stamp_lyric_events(
            "result_first", SAMPLE_LYRICS,
            cite_start_s=CITE_START, total_duration_s=TOTAL,
            drop_last=True,
        )
        full = stamp_lyric_events(
            "result_first", SAMPLE_LYRICS,
            cite_start_s=CITE_START, total_duration_s=TOTAL,
        )
        assert len(stamped) == len(full) - 1


class TestRecipesHooks:
    def test_recipes_hooks_match_policies(self):
        assert RECIPES["hooks"] == list(HOOK_POLICIES)
