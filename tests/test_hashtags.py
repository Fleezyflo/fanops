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


# --- per-account hashtag CORPUS (persona differentiation; tag_lean folded into corpus, M3) ---

def test_corpus_account_keeps_a_platform_discovery_tag():
    # a non-viral corpus ate all 4 slots with flavor tags and LOST its platform discovery tag
    # (#fyp/#reels/#foryou/#viral) — a real reach loss. Guarantee one discovery tag survives.
    from fanops.hashtags import _DISCOVERY, _DISCOVERY_DEFAULT
    disc = set(_DISCOVERY[Platform.tiktok]) | set(_DISCOVERY_DEFAULT)
    out = vet_hashtags(None, Platform.tiktok, "en", corpus=["#lyrics", "#bars", "#newmusic"])
    assert any(t in disc for t in out), f"a corpus account lost its discovery tag: {out}"


def test_corpus_none_is_byte_identical_to_default():
    cases = [(["#hiphop", "#bars"], Platform.tiktok, "en"),
             ([], Platform.instagram, "en"),
             (["#undergroundhiphop", "#hiphop"], Platform.tiktok, "ar")]
    for tags, plat, lang in cases:
        assert vet_hashtags(tags, plat, lang, corpus=None) == vet_hashtags(tags, plat, lang)


def test_tasteful_corpus_floats_craft_tag_ahead_of_mega():
    out = vet_hashtags(["#hiphop", "#bars"], Platform.tiktok, "en", corpus=["#lyrics", "#bars", "#newmusic"])
    assert out.index("#bars") < out.index("#hiphop")   # corpus trades mega reach for the craft tag


def test_bold_corpus_leads_viral():
    out = vet_hashtags(None, Platform.instagram, "en", corpus=["#viral", "#rapmusic", "#hiphop"])
    assert out[0] == "#viral"                        # bold/viral leads the discovery tag


def test_corpus_still_hard_caps_at_four():
    out = vet_hashtags(["#hiphop", "#rap", "#rapper", "#bars", "#newmusic"],
                       Platform.tiktok, "en", corpus=["#freestyle", "#undergroundhiphop", "#trap"])
    assert len(out) <= 4


def test_arabic_slot_survives_a_corpus():
    out = vet_hashtags(["#hiphop"], Platform.tiktok, "ar", corpus=["#viral", "#rapmusic", "#hiphop"])
    assert any("arab" in t for t in out)            # language/region reach floored even under a corpus


def test_arabic_floor_survives_even_when_model_fills_all_slots():
    # the model returns 4 vetted non-Arabic tags for an AR clip under a corpus -> kept fills before backfill;
    # the floor must STILL reserve a region slot (the HIGH the reviewer caught).
    out = vet_hashtags(["#viral", "#hiphop", "#rap", "#rapper"], Platform.tiktok, "ar", corpus=["#viral", "#rapmusic", "#hiphop"])
    assert len(out) == 4 and any("arab" in t for t in out)


def test_arabic_floor_noop_when_model_already_has_arabic_tag():
    out = vet_hashtags(["#arabicmusic", "#viral", "#hiphop", "#rap"], Platform.tiktok, "ar", corpus=["#viral", "#rapmusic", "#hiphop"])
    assert out.count("#arabicmusic") == 1 and len(out) == 4   # no double-add, no displacement


def test_arabic_floor_survives_when_model_returns_arabic_past_the_cap():
    # the audit residual: model returns 5+ tags incl. #arabicmusic; under a bold corpus it sorts PAST the
    # cap and the old floor check (vs `seen`) skipped -> dropped. The fix promotes it into the window.
    out = vet_hashtags(["#viral", "#hiphop", "#rap", "#rapper", "#arabicmusic"], Platform.tiktok, "ar", corpus=["#viral", "#rapmusic", "#hiphop"])
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


# --- MOL-174: niche-driven hashtag floor (not global rap hardcode) --------------------------------

def test_gossip_niche_not_backfilled_with_hiphop():
    out = vet_hashtags([], Platform.tiktok, "en", genre="gossip")
    assert "#hiphop" not in out and "#rapper" not in out
    assert any("gossip" in t or "celebrity" in t or "drama" in t for t in out)


def test_vetted_menu_is_niche_driven():
    from fanops.hashtags import vetted_menu
    assert "#hiphop" in vetted_menu(genre="rap")
    assert "#hiphop" not in vetted_menu(genre="gossip")
    assert any("gossip" in t or "celebrity" in t for t in vetted_menu(genre="gossip"))


def test_composition_backfill_niche_aware():
    from fanops.hashtags import _composition
    rap_fill = _composition(Platform.tiktok, "en", genre="rap")
    gossip_fill = _composition(Platform.tiktok, "en", genre="gossip")
    assert "#hiphop" in rap_fill and "#rapper" in rap_fill
    assert "#hiphop" not in gossip_fill and "#rapper" not in gossip_fill
    assert any("gossip" in t or "celebrity" in t for t in gossip_fill)


def test_rap_niche_still_works():
    out = vet_hashtags([], Platform.tiktok, "en", genre="hiphop")
    assert "#hiphop" in out
    assert any(t in out for t in ("#rapper", "#rap", "#bars"))
