"""Tests for fanops.text — the AI-tell sanitizer. The guarantee: no em/en-dash, curly quote,
or invisible character survives sanitize_generated_text, regardless of position or count, and
Arabic / hashtag text is untouched. This is the HARD guarantee behind the prompt instructions.
Special characters are written as \\u escapes so the intent is unambiguous in source."""
import pytest
from fanops.text import sanitize_generated_text, _ZEROWIDTH

# The four invisible code points _ZEROWIDTH must strip, written as \u escapes so the intent is
# unambiguous in source: zero-width space, zero-width non-joiner, zero-width joiner, BOM/ZWNBSP.
_ZW_CODEPOINTS = ["\u200B", "\u200C", "\u200D", "\uFEFF"]

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


# --- PKT-4 (MOL-110): _ZEROWIDTH raw literals -> explicit \uXXXX escapes ------------------------
# The compiled pattern must match EXACTLY the same character set before and after the escape swap,
# and a normal character must never match. Source must carry no raw zero-width literal (ruff PLE2515).

@pytest.mark.parametrize("ch", _ZW_CODEPOINTS)
def test_zerowidth_pattern_matches_each_invisible(ch):
    assert _ZEROWIDTH.fullmatch(ch), f"U+{ord(ch):04X} must be in the _ZEROWIDTH class"

def test_zerowidth_pattern_matched_set_is_exactly_the_four():
    # Enumerate the class over the full BMP and assert the matched set is exactly
    # {U+200B, U+200C, U+200D, U+FEFF} — pins the class content, catches any drift.
    matched = {c for c in map(chr, range(0x10000)) if _ZEROWIDTH.fullmatch(c)}
    assert matched == set(_ZW_CODEPOINTS)

@pytest.mark.parametrize("ch", ["a", "A", "0", " ", "-", "\t", "\u2014", "\u2013", "\u00A0", "\u2003"])
def test_zerowidth_pattern_rejects_normal_and_adjacent_chars(ch):
    assert _ZEROWIDTH.fullmatch(ch) is None, f"U+{ord(ch):04X} must NOT match _ZEROWIDTH"

@pytest.mark.parametrize("ch", _ZW_CODEPOINTS)
def test_sanitize_drops_each_zerowidth(ch):
    assert sanitize_generated_text(f"clip{ch}text") == "cliptext"

def test_text_module_source_has_no_raw_zerowidth_literal():
    # The whole point of PKT-4: the _ZEROWIDTH definition line must use escapes, not raw invisibles.
    import fanops.text as _t
    with open(_t.__file__, encoding="utf-8") as f:
        for line in f:
            if line.lstrip().startswith("_ZEROWIDTH"):
                for ch in line:
                    assert ord(ch) not in {0x200B, 0x200C, 0x200D, 0xFEFF}, (
                        f"raw U+{ord(ch):04X} literal in _ZEROWIDTH source line (ruff PLE2515)")
                break
        else:
            raise AssertionError("no _ZEROWIDTH definition line found in fanops/text.py")
