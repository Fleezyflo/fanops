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


def test_leans_are_disjoint_flavor_vocabularies():
    # M3 (the operator's complaint: personas barely differentiate hashtags). The lean pools must draw
    # from GENUINELY DIFFERENT flavor vocabularies, not the same few tags reordered — so two personas
    # produce visibly different lines, not the same 14 shuffled.
    from fanops.hashtags import _LEANS
    pools = list(_LEANS.values())
    for i in range(len(pools)):
        for j in range(i + 1, len(pools)):
            assert set(pools[i]).isdisjoint(pools[j]), "lean pools overlap -> personas look the same"


def test_leaned_account_keeps_a_platform_discovery_tag():
    # M3 bug: a non-viral lean (e.g. 'tasteful') ate all 4 slots with flavor tags and LOST its platform
    # discovery tag (#fyp/#reels/#foryou/#viral) — a real reach loss. Guarantee one discovery tag survives.
    from fanops.hashtags import _DISCOVERY, _DISCOVERY_DEFAULT
    disc = set(_DISCOVERY[Platform.tiktok]) | set(_DISCOVERY_DEFAULT)
    out = vet_hashtags(None, Platform.tiktok, "en", lean="tasteful")
    assert any(t in disc for t in out), f"a leaned account lost its discovery tag: {out}"


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


def test_arabic_floor_survives_when_model_returns_arabic_past_the_cap():
    # the audit residual: model returns 5+ tags incl. #arabicmusic; under a bold lean it sorts PAST the
    # cap and the old floor check (vs `seen`) skipped -> dropped. The fix promotes it into the window.
    out = vet_hashtags(["#viral", "#hiphop", "#rap", "#rapper", "#arabicmusic"], Platform.tiktok, "ar", lean="bold")
    assert len(out) == 4 and any("arab" in t for t in out)


# ---- M3a: the reach floors fire on CORPUS, not just lean (so a corpus-led persona keeps region+discovery
# reach once tag_lean is folded into corpus). Additive — a leaned account is byte-identical (proven below). ----
def test_corpus_only_ar_clip_reserves_the_region_floor_even_when_corpus_fills_all_slots():
    # a corpus that fills all 4 slots on an AR clip, NO lean: the region floor must still RESERVE a tail slot
    # (mirrors the lean reservation test). Today the AR floor gates on `pool` only -> corpus-led personas lose
    # region reach. M3a widens the floor to corpus. (A free-slot AR tag comes from _composition anyway; this
    # forces the RESERVATION path, the part that actually depends on the widen.)
    out = vet_hashtags([], Platform.instagram, "ar", corpus=["#alpha", "#beta", "#gamma", "#delta"])
    assert len(out) == 4 and any("arab" in t for t in out)   # region reach floored under a CORPUS, not just a lean

def test_corpus_only_keeps_a_platform_discovery_tag():
    # the discovery floor (#reels/#fyp) likewise fires on a corpus, so a corpus-led persona keeps reach
    # instead of letting curated tags eat all 4 slots.
    out = vet_hashtags([], Platform.instagram, "en", corpus=["#myscene", "#another", "#third"])
    assert "#reels" in out

def test_corpus_floors_are_additive_leaned_account_byte_identical():
    # the widen must NOT change a leaned account's output (pool truthy -> `pool or corpus_norm` == pool).
    for lean in ("tasteful", "underground", "bold"):
        with_widen = vet_hashtags([], Platform.instagram, "ar", lean=lean)
        # the leaned AR output already had region+discovery floors; corpus=None keeps it identical
        assert any("arab" in t for t in with_widen) and len(with_widen) <= 4
