"""vet_hashtags — the deterministic <=4 selector. The model no longer freely chooses tags: whatever it
returns is filtered to the MEASUREMENT CACHE union the surface's corpus, ordered by the platform metric,
backfilled, and HARD-capped at 4 (the operator rule). `store` is the cache as an ordered menu
(`ranked_tags(load_measurements(cfg))`) — there is no frozen hand-ranked pool left to fall back on, so a
cold cache ships SHORT rather than padded (pinned in test_hashtag_platform_truth.py)."""
import json
from fanops.models import Platform
from fanops.hashtags import METRIC_FIELD, load_measurements, vet_hashtags

# A measured menu, metric-ranked (what ranked_tags returns). Membership + rank in one list.
STORE = ["#hiphop", "#rap", "#hiphopmusic", "#rapper", "#bars", "#newmusic", "#undergroundhiphop"]


def test_hard_caps_at_four():
    # six measured tags in -> never more than four out
    out = vet_hashtags(["#hiphop", "#rap", "#hiphopmusic", "#rapper", "#bars", "#newmusic"],
                       Platform.tiktok, "en", store=STORE)
    assert len(out) <= 4


def test_drops_random_ai_words_keeps_only_measured():
    out = vet_hashtags(["#hiphop", "#totallymadeup", "#xyzzy", "#vibes2026"], Platform.instagram, "en",
                       store=STORE)
    assert "#totallymadeup" not in out and "#xyzzy" not in out and "#vibes2026" not in out
    assert all(t in STORE or t == "#reels" for t in out)   # survivors: the cache + the one discovery slot


def test_store_order_is_the_rank():
    # the menu arrives metric-DESC, and that order is what the selector honours (no second ranking here)
    out = vet_hashtags(["#undergroundhiphop", "#hiphop"], Platform.tiktok, "en", store=STORE)
    assert out.index("#hiphop") < out.index("#undergroundhiphop")


def test_arabic_clip_gets_an_arabic_tag():
    out = vet_hashtags(["#hiphop"], Platform.tiktok, "ar", store=STORE)
    assert any("arab" in t for t in out)            # language/region floor for an AR clip


def test_english_clip_not_forced_arabic():
    out = vet_hashtags(["#hiphop", "#rap", "#rapper", "#newmusic"], Platform.tiktok, "en", store=STORE)
    assert not any("arab" in t for t in out)


def test_normalizes_and_dedupes_case_and_hash():
    out = vet_hashtags(["Rap", "#RAP", "rap"], Platform.tiktok, "en", store=STORE)
    assert out.count("#rap") == 1                   # one canonical form, no dupes


def test_empty_input_backfills_from_the_measured_menu():
    out = vet_hashtags([], Platform.tiktok, "en", store=STORE)
    assert len(out) == 4
    assert all(t in STORE or t == "#fyp" for t in out)   # never random — the cache + the discovery slot


def test_returns_all_lowercase_hash_prefixed():
    out = vet_hashtags(["HipHop", "rap"], Platform.instagram, "en", store=STORE)
    assert all(t.startswith("#") and t == t.lower() for t in out)


# --- the measurement cache reader ---------------------------------------------------------------

def _rec(value, *, gid="id-x", at="2026-07-20T00:00:00+00:00", frm=None):
    r = {"graph_id": gid, METRIC_FIELD: value, "measured_at": at}
    if frm: r["from"] = frm
    return r


def test_load_measurements_absent_corrupt_and_mis_shaped(tmp_path):
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    assert load_measurements(cfg) == {}                    # absent -> {} (selection then ships short)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text("{ corrupt")
    assert load_measurements(cfg) == {}                     # corrupt -> {}, never raises
    cfg.hashtags_path.write_text(json.dumps(["#a", "#b"]))
    assert load_measurements(cfg) == {}                     # a list is not the cache shape


def test_load_measurements_drops_half_records_and_the_legacy_shape(tmp_path):
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#Good": _rec(900.0, gid="id-good", frm={"#Anchor": "2"}),
        "#nogid": {METRIC_FIELD: 5.0, "measured_at": "2026-07-20T00:00:00+00:00"},
        "#nostamp": {"graph_id": "id-x", METRIC_FIELD: 5.0},
        "#legacy": {"graph_id": "id-l", "reach": 4200, "measured_at": "2026-07-20T00:00:00+00:00"},
        "#negative": _rec(-1.0),
    }))
    m = load_measurements(cfg)
    assert set(m) == {"#good"}                             # key normalized; every half-record dropped
    assert m["#good"][METRIC_FIELD] == 900.0
    assert m["#good"]["from"] == {"#anchor": 2}            # harvest attribution normalized + int-coerced


# --- per-account hashtag CORPUS (persona differentiation) ---------------------------------------

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
        assert (vet_hashtags(tags, plat, lang, store=STORE, corpus=None)
                == vet_hashtags(tags, plat, lang, store=STORE))


def test_corpus_floats_its_tag_ahead_of_the_metric_rank():
    out = vet_hashtags(["#hiphop", "#bars"], Platform.tiktok, "en", store=STORE,
                       corpus=["#lyrics", "#bars", "#newmusic"])
    assert out.index("#bars") < out.index("#hiphop")   # the corpus tier leads the measured rank


def test_corpus_leads_the_line():
    out = vet_hashtags(None, Platform.instagram, "en", store=STORE,
                       corpus=["#viral", "#rapmusic", "#hiphop"])
    assert out[0] == "#viral"                        # corpus order leads the discovery/metric backfill


def test_corpus_still_hard_caps_at_four():
    out = vet_hashtags(["#hiphop", "#rap", "#rapper", "#bars", "#newmusic"],
                       Platform.tiktok, "en", store=STORE,
                       corpus=["#freestyle", "#undergroundhiphop", "#trap"])
    assert len(out) <= 4


def test_arabic_slot_survives_a_corpus():
    out = vet_hashtags(["#hiphop"], Platform.tiktok, "ar", store=STORE,
                       corpus=["#viral", "#rapmusic", "#hiphop"])
    assert any("arab" in t for t in out)            # language/region floor holds under a corpus


def test_arabic_floor_survives_even_when_model_fills_all_slots():
    # the model returns 4 measured non-Arabic tags for an AR clip under a corpus -> kept fills before
    # backfill; the floor must STILL reserve a region slot (the HIGH the reviewer caught).
    out = vet_hashtags(["#viral", "#hiphop", "#rap", "#rapper"], Platform.tiktok, "ar",
                       store=STORE + ["#viral"], corpus=["#viral", "#rapmusic", "#hiphop"])
    assert len(out) == 4 and any("arab" in t for t in out)


def test_arabic_floor_noop_when_model_already_has_arabic_tag():
    out = vet_hashtags(["#arabicmusic", "#viral", "#hiphop", "#rap"], Platform.tiktok, "ar",
                       store=STORE + ["#viral", "#arabicmusic"], corpus=["#viral", "#rapmusic", "#hiphop"])
    assert out.count("#arabicmusic") == 1 and len(out) == 4   # no double-add, no displacement


def test_arabic_floor_survives_when_model_returns_arabic_past_the_cap():
    # the audit residual: model returns 5+ tags incl. #arabicmusic; under a bold corpus it sorts PAST the
    # cap and the old floor check (vs `seen`) skipped -> dropped. The fix promotes it into the window.
    out = vet_hashtags(["#viral", "#hiphop", "#rap", "#rapper", "#arabicmusic"], Platform.tiktok, "ar",
                       store=STORE + ["#viral", "#arabicmusic"], corpus=["#viral", "#rapmusic", "#hiphop"])
    assert len(out) == 4 and any("arab" in t for t in out)


# ---- the floors fire on CORPUS too, so a corpus-led persona keeps region + discovery reach ----
def test_corpus_only_ar_clip_reserves_the_region_floor_even_when_corpus_fills_all_slots():
    # a corpus that fills all 4 slots on an AR clip: the region floor must still RESERVE a tail slot.
    out = vet_hashtags([], Platform.instagram, "ar", corpus=["#alpha", "#beta", "#gamma", "#delta"])
    assert len(out) == 4 and any("arab" in t for t in out)


def test_corpus_only_keeps_a_platform_discovery_tag():
    # the discovery floor (#reels/#fyp) likewise fires on a corpus, so a corpus-led persona keeps reach
    # instead of letting curated tags eat all 4 slots.
    out = vet_hashtags([], Platform.instagram, "en", corpus=["#myscene", "#another", "#third"])
    assert "#reels" in out
