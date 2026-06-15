"""Tests for fanops.text — the AI-tell sanitizer. The guarantee: no em/en-dash, curly quote,
or invisible character survives sanitize_generated_text, regardless of position or count, and
Arabic / hashtag text is untouched. This is the HARD guarantee behind the prompt instructions.
Special characters are written as \\u escapes so the intent is unambiguous in source."""
from fanops.text import sanitize_generated_text

def test_em_dash_becomes_comma_space():
    assert sanitize_generated_text("Hometown hero just snapped — Moh Flow") == "Hometown hero just snapped, Moh Flow"

def test_en_dash_becomes_comma_space():
    assert sanitize_generated_text("Top tier – no debate") == "Top tier, no debate"

def test_figure_dash_and_horizontal_bar():
    assert sanitize_generated_text("Heat‒check") == "Heat, check"
    assert sanitize_generated_text("bars―bars") == "bars, bars"

def test_curly_single_quotes_straightened():
    assert sanitize_generated_text("‘don’t sleep’") == "'don't sleep'"

def test_curly_double_quotes_straightened():
    assert sanitize_generated_text("“fire”") == '"fire"'

def test_leading_dash_no_leading_comma():
    assert sanitize_generated_text("— Moh Flow bars") == "Moh Flow bars"

def test_trailing_dash_no_trailing_comma():
    assert sanitize_generated_text("bars —") == "bars"

def test_many_dashes_all_gone():
    out = sanitize_generated_text("a — b – c ‒ d ― e")
    assert out == "a, b, c, d, e"
    assert not any(d in out for d in "—–‒―")

def test_nbsp_becomes_space_and_zerowidth_removed():
    assert sanitize_generated_text("clean text here﻿") == "clean text here"
    assert sanitize_generated_text("zero​width") == "zerowidth"

def test_double_space_collapsed():
    assert "  " not in sanitize_generated_text("a   b")

def test_none_returns_none():
    assert sanitize_generated_text(None) is None

def test_idempotent():
    once = sanitize_generated_text("snapped — Moh Flow — again")
    assert sanitize_generated_text(once) == once

def test_arabic_passthrough_unmodified():
    ar = "بطل الحي"
    assert sanitize_generated_text(ar) == ar

def test_hashtag_caption_unmodified():
    cap = "#mohflow #hiphop #fyp #bars"
    assert sanitize_generated_text(cap) == cap

def test_max_words_trims_overlong_hook():
    out = sanitize_generated_text("one two three four five six seven eight nine", max_words=7)
    assert out.split() == ["one", "two", "three", "four", "five", "six", "seven"]

def test_max_words_keeps_short_hook():
    assert sanitize_generated_text("short hook", max_words=7) == "short hook"

def test_max_words_after_dash_strip():
    # the dash collapse must happen BEFORE the word trim, so the 7 kept words are real words
    out = sanitize_generated_text("snapped — one two three four five six", max_words=7)
    assert len(out.split()) == 7 and "—" not in out

def test_max_words_trim_leaves_no_dangling_comma():
    # a trim boundary landing on a dash-replacement comma must NOT burn a trailing comma on-screen
    assert sanitize_generated_text("top — bar", max_words=1) == "top"
