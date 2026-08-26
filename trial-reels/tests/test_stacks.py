"""Contract tests for trial-reels ffmpeg stacks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.hooks import drop_last_lyric, LyricEvent
from lib.stacks import (
    FFMPEG_BIN,
    STACK_NAMES,
    build_stack_graph,
    ffmpeg_cmd,
    graph_has_format_normalisation,
    graph_has_no_geq,
    graph_uses_loudnorm_in_a,
    punch_fits_cut,
    rehooks_for_stack,
    scaled_punch_duration,
    stack_gate_passes,
)

RECIPES = json.loads((Path(__file__).resolve().parents[1] / "recipes.json").read_text())


class TestRecipesManifest:
    def test_target_seconds(self):
        assert RECIPES["target_seconds"] == 30

    def test_hooks_list(self):
        assert RECIPES["hooks"] == [
            "result_first", "mid_action", "direct_you", "bold_claim", "cold_proof",
        ]

    def test_stacks_list(self):
        assert RECIPES["stacks"] == list(STACK_NAMES)

    def test_rehooks(self):
        assert RECIPES["rehooks_s"] == [3, 8]


class TestStackGates:
    @pytest.mark.parametrize("duration,expected", [
        (3.5, False),
        (3.6, True),
        (10.0, True),
    ])
    def test_punch_cuts_gate(self, duration, expected):
        assert stack_gate_passes("punch_cuts", duration) is expected

    def test_open_loop_shorter_than_full(self):
        g_full = build_stack_graph("punch_cuts", duration_s=8.0)
        g_open = build_stack_graph("open_loop", duration_s=8.0)
        assert "trim=" in g_open.filter_complex
        assert g_open.filter_complex != g_full.filter_complex


class TestLoudnormInAudioChain:
    @pytest.mark.parametrize("stack", STACK_NAMES)
    def test_loudnorm_inside_filter_complex_when_audio(self, stack):
        g = build_stack_graph(stack, duration_s=10.0, has_audio=True)
        assert g.gate_passes
        assert graph_uses_loudnorm_in_a(g.filter_complex), stack

    @pytest.mark.parametrize("stack", STACK_NAMES)
    def test_no_af_flag_when_filter_complex(self, stack):
        cmd = ffmpeg_cmd(
            stack,
            input_path="in.mp4",
            output_path="out.mp4",
            duration_s=10.0,
            has_audio=True,
        )
        assert "-filter_complex" in cmd
        assert "-af" not in cmd


class TestPunchCutsScaling:
    def test_fits_arbitrary_length_at_least_min(self):
        for src in (3.6, 5.0, 12.0, 30.0):
            cut = scaled_punch_duration(src)
            assert punch_fits_cut(src, cut)
            assert punch_fits_cut(src, cut + 1.0)

    def test_rejects_below_gate(self):
        assert not punch_fits_cut(3.0, 8.0)

    def test_three_jump_trims_in_graph(self):
        g = build_stack_graph("punch_cuts", duration_s=10.0)
        assert g.filter_complex.count("trim=") >= 3
        assert "concat=n=3" in g.filter_complex


class TestFakeOut:
    def test_uses_color_not_geq(self):
        g = build_stack_graph("fake_out", duration_s=10.0, width=1080, height=1920)
        assert "color=c=black" in g.filter_complex
        assert graph_has_no_geq(g.filter_complex)

    def test_setsar_normalised_before_concat(self):
        g = build_stack_graph("fake_out", duration_s=10.0, width=1080, height=1920)
        assert "setsar=1" in g.filter_complex

    def test_flash_duration(self):
        g = build_stack_graph("fake_out", duration_s=10.0)
        assert "d=0.150" in g.filter_complex or "d=0.15" in g.filter_complex


class TestEndLoop:
    def test_repeats_last_second(self):
        g = build_stack_graph("end_loop", duration_s=10.0)
        assert "concat=n=2" in g.filter_complex
        assert "trim=" in g.filter_complex


class TestOpenLoop:
    def test_no_rehooks(self):
        assert rehooks_for_stack("open_loop", RECIPES["rehooks_s"]) == ()

    def test_other_stacks_rehook(self):
        for stack in ("punch_cuts", "fake_out", "end_loop"):
            assert rehooks_for_stack(stack, RECIPES["rehooks_s"]) == (3, 8)

    def test_drop_last_lyric_unresolved(self):
        events = [
            LyricEvent(1.0, 2.0, "line one"),
            LyricEvent(3.0, 4.0, "line two"),
            LyricEvent(5.0, 6.0, "payoff"),
        ]
        dropped = drop_last_lyric(events)
        assert len(dropped) == 2
        assert dropped[-1].text == "line two"


class TestTenBitSafety:
    @pytest.mark.parametrize("stack", STACK_NAMES)
    def test_format_normalisation_present(self, stack):
        g = build_stack_graph(stack, duration_s=10.0)
        assert graph_has_format_normalisation(g.filter_complex)


class TestFfmpegBin:
    def test_default_path_is_ffmpeg_full(self):
        assert FFMPEG_BIN == "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"

    def test_cmd_uses_subtitles_when_ass_provided(self):
        g = build_stack_graph("end_loop", duration_s=8.0, sub_path="/tmp/hook.ass")
        assert "subtitles=" in g.filter_complex
        assert "drawtext" not in g.filter_complex
