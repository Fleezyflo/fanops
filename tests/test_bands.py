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
