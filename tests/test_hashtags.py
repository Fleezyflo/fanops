"""vet_hashtags — the deterministic ≤4, reach-vetted hashtag selector. The model no longer
freely chooses tags: whatever it returns is filtered to the reach-vetted set, reach-ordered,
backfilled to a strong default, and HARD-capped at 4 (the operator rule). See the
fanops-hook-hashtag skill for the sourced reach data this encodes."""
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


# --- per-account tag LEAN (persona differentiation) ---

def test_lean_pools_are_subset_of_vetted():
    from fanops.hashtags import _LEANS
    for name, pool in _LEANS.items():
        assert all(t in VETTED for t in pool), f"{name} pool has a non-vetted tag"


def test_tag_leans_matches_pools():
    from fanops.hashtags import _LEANS, TAG_LEANS
    assert TAG_LEANS == frozenset(_LEANS)           # one source of truth, no drift


def test_lean_none_is_byte_identical_to_default():
    cases = [(["#hiphop", "#bars"], Platform.tiktok, "en"),
             ([], Platform.instagram, "en"),
             (["#undergroundhiphop", "#hiphop"], Platform.tiktok, "ar")]
    for tags, plat, lang in cases:
        assert vet_hashtags(tags, plat, lang, lean=None) == vet_hashtags(tags, plat, lang)


def test_tasteful_lean_floats_craft_tag_ahead_of_mega():
    out = vet_hashtags(["#hiphop", "#bars"], Platform.tiktok, "en", lean="tasteful")
    assert out.index("#bars") < out.index("#hiphop")   # lean trades mega reach for the craft tag


def test_bold_lean_leads_viral():
    out = vet_hashtags(None, Platform.instagram, "en", lean="bold")
    assert out[0] == "#viral"                        # bold/viral leads the discovery tag


def test_unknown_lean_is_treated_as_no_lean():
    assert (vet_hashtags(["#hiphop", "#bars"], Platform.tiktok, "en", lean="weird")
            == vet_hashtags(["#hiphop", "#bars"], Platform.tiktok, "en"))


def test_lean_still_hard_caps_at_four():
    out = vet_hashtags(["#hiphop", "#rap", "#rapper", "#bars", "#newmusic"],
                       Platform.tiktok, "en", lean="underground")
    assert len(out) <= 4


def test_arabic_slot_survives_a_lean():
    out = vet_hashtags(["#hiphop"], Platform.tiktok, "ar", lean="bold")
    assert any("arab" in t for t in out)            # language/region reach floored even under a lean


def test_arabic_floor_survives_even_when_model_fills_all_slots():
    # the model returns 4 vetted non-Arabic tags for an AR clip under a lean -> kept fills before backfill;
    # the floor must STILL reserve a region slot (the HIGH the reviewer caught).
    out = vet_hashtags(["#viral", "#hiphop", "#rap", "#rapper"], Platform.tiktok, "ar", lean="bold")
    assert len(out) == 4 and any("arab" in t for t in out)


def test_arabic_floor_noop_when_model_already_has_arabic_tag():
    out = vet_hashtags(["#arabicmusic", "#viral", "#hiphop", "#rap"], Platform.tiktok, "ar", lean="bold")
    assert out.count("#arabicmusic") == 1 and len(out) == 4   # no double-add, no displacement
