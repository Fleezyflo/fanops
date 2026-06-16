"""vet_hashtags — the deterministic ≤4, reach-vetted hashtag selector. The model no longer
freely chooses tags: whatever it returns is filtered to the reach-vetted set, reach-ordered,
backfilled to a strong default, and HARD-capped at 4 (the operator rule). See the
fanops-hook-hashtag skill for the sourced reach data this encodes."""
import pytest
from fanops.models import Platform
from fanops.hashtags import vet_hashtags, VETTED


def test_hard_caps_at_four():
    # six vetted tags in -> never more than four out
    out = vet_hashtags(["#hiphop", "#rap", "#hiphopmusic", "#rapper", "#bars", "#newmusic"],
                       Platform.tiktok, "en")
    assert len(out) <= 4


def test_drops_random_ai_words_keeps_only_vetted():
    out = vet_hashtags(["#hiphop", "#totallymadeup", "#xyzzy", "#vibes2026"], Platform.instagram, "en")
    assert "#totallymadeup" not in out and "#xyzzy" not in out and "#vibes2026" not in out
    assert all(t in VETTED for t in out)            # every survivor is from the vetted set


def test_reach_ordered_mega_before_niche():
    out = vet_hashtags(["#undergroundhiphop", "#hiphop"], Platform.tiktok, "en")
    assert out.index("#hiphop") < out.index("#undergroundhiphop")   # mega outranks niche


def test_arabic_clip_gets_an_arabic_tag():
    out = vet_hashtags(["#hiphop"], Platform.tiktok, "ar")
    assert any("arab" in t for t in out)            # language/region reach for an AR clip


def test_english_clip_not_forced_arabic():
    out = vet_hashtags(["#hiphop", "#rap", "#rapper", "#newmusic"], Platform.tiktok, "en")
    assert not any("arab" in t for t in out)


def test_normalizes_and_dedupes_case_and_hash():
    out = vet_hashtags(["Rap", "#RAP", "rap"], Platform.tiktok, "en")
    assert out.count("#rap") == 1                   # one canonical form, no dupes


def test_empty_input_backfills_to_strong_default():
    out = vet_hashtags([], Platform.tiktok, "en")
    assert 1 <= len(out) <= 4
    assert all(t in VETTED for t in out)            # never empty, never random — a vetted default


def test_returns_all_lowercase_hash_prefixed():
    out = vet_hashtags(["HipHop", "rap"], Platform.instagram, "en")
    assert all(t.startswith("#") and t == t.lower() for t in out)
