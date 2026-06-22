# tests/test_bands.py
"""Clip-length BANDS by content type. A song's hook/verse is a longer watchable unit than a spoken
beat, so the SONG band is wider and higher than the TALK default; band_for resolves a profile name
to its Band and falls back to TALK (today's behavior) for anything unknown."""
from fanops.bands import band_for, TALK, SONG

def test_talk_and_song_bands():
    assert (TALK.lo, TALK.hi) == (12.0, 22.0)
    assert (SONG.lo, SONG.hi) == (18.0, 35.0)
    assert SONG.lo > TALK.lo and SONG.hi > TALK.hi          # songs are a longer watchable unit

def test_band_span_is_midpoint():
    assert TALK.span == 17.0                                # midpoint: aim ~one clip per `span`s
    assert SONG.span == 26.5

def test_band_for_resolves_known_profiles():
    assert band_for("song") is SONG
    assert band_for("talk") is TALK

def test_band_for_is_case_and_whitespace_tolerant():
    assert band_for("  SONG\n") is SONG                     # a .env value may carry case/ws

def test_band_for_unknown_or_empty_defaults_to_talk():
    assert band_for("podcast") is TALK                      # unknown profile -> safe default
    assert band_for("") is TALK
    assert band_for(None) is TALK

def test_short_medium_long_are_distinct_new_tiers():
    # M2: three operator-facing length tiers, ADDED alongside the legacy content-type bands (NOT aliases).
    from fanops.bands import SHORT, MEDIUM, LONG
    assert (SHORT.lo, SHORT.hi) == (8.0, 15.0)
    assert (MEDIUM.lo, MEDIUM.hi) == (16.0, 26.0)
    assert (LONG.lo, LONG.hi) == (28.0, 45.0)
    assert SHORT.hi < MEDIUM.hi < LONG.hi                   # genuinely short < medium < long

def test_band_for_resolves_new_length_profiles():
    from fanops.bands import band_for, SHORT, MEDIUM, LONG
    assert band_for("short") is SHORT
    assert band_for("medium") is MEDIUM
    assert band_for("long") is LONG

def test_m2_is_additive_talk_song_byte_identical():
    # ADDITIVE (LOCKED D1): talk/song are NOT remapped or aliased to the new tiers, so every existing
    # deployment renders byte-identically (TALK still 12-22, SONG still 18-35). No normalize, no migration.
    assert band_for("talk") is TALK and (TALK.lo, TALK.hi) == (12.0, 22.0)
    assert band_for("song") is SONG and (SONG.lo, SONG.hi) == (18.0, 35.0)
