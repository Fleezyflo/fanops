"""Content-aware hashtags: a clip's tags must derive from THAT clip's information (its transcript),
survive vetting, and carry a provenance `source` for every shipped tag. Captions stay hashtags-only.

These pin the pure hashtags.py seams (the extractor + the `content=` admit/slot + the traced provenance).
The `content=None` cases are the FIREWALL — they must be byte-identical to today's vet_hashtags."""
import pytest
from fanops.models import Platform
from fanops import hashtags as H
from fanops.hashtags import vet_hashtags, content_tag_candidates, vet_hashtags_traced


# ---- Task 1: the pure content extractor --------------------------------------------------------------
def test_content_candidates_extract_from_transcript():
    cands = content_tag_candidates("a fiery diss track about loyalty and loyalty forever")
    assert "#loyalty" in cands and "#diss" in cands and "#fiery" in cands
    assert "#loyalty" == cands[0]                      # frequency-first (appears twice)
    assert "#a" not in cands and "#and" not in cands   # stopwords / too-short dropped


@pytest.mark.parametrize("text", ["", None, "   ", "أغنية عربية فقط", "###", "12 34 5"])
def test_content_candidates_empty_for_blank_or_nonlatin(text):
    assert content_tag_candidates(text) == []          # nothing latin/usable -> [] -> byte-identity path


def test_content_candidates_are_bounded_and_normalized():
    cands = content_tag_candidates(" ".join(f"word{i}" for i in range(50)), max_n=6)
    assert len(cands) <= 6
    assert all(c.startswith("#") and c == c.lower() for c in cands)


# ---- Task 2: vet_hashtags(content=) joins membership + reserves a slot --------------------------------
def test_content_tag_survives_vetting():
    # a content tag the model picked is NOT in VETTED; today it is dropped. With content= it survives.
    assert "#diss" not in H.VETTED
    out = vet_hashtags(["#diss"], Platform.instagram, None, content=["#diss"])
    assert "#diss" in out


@pytest.mark.parametrize("lean", [None, "tasteful", "underground", "bold"])
@pytest.mark.parametrize("corpus", [None, ["#customtag"]])
def test_content_none_is_byte_identical(lean, corpus):
    # FIREWALL: content=None must reproduce today's output exactly, across lean/corpus combos.
    tags = ["#rap", "#bars", "#nonsense"]
    base = vet_hashtags(tags, Platform.tiktok, "en", lean=lean, corpus=corpus)
    withc = vet_hashtags(tags, Platform.tiktok, "en", lean=lean, corpus=corpus, content=None)
    assert base == withc


def test_content_floor_reserves_one_slot_when_reach_fills_four():
    # model fills all 4 with reach tags; a content tag still claims exactly one slot.
    out = vet_hashtags(["#hiphop", "#rap", "#bars", "#newmusic"], Platform.instagram, "en",
                       content=["#loyalty"])
    assert "#loyalty" in out and len(out) == 4


def test_arabic_region_floor_still_wins_over_content():
    # an Arabic clip under a lean keeps its region tag AND gets a content tag (both floors satisfied).
    out = vet_hashtags(["#hiphop", "#rap", "#bars", "#newmusic"], Platform.instagram, "ar",
                       lean="bold", content=["#loyalty"])
    assert any(t in set(H._ARABIC) for t in out)       # region reach preserved
    assert "#loyalty" in out


# ---- Task 3: provenance -- every shipped tag traces to a real signal ----------------------------------
def test_every_kept_tag_has_a_source():
    tags, sources = vet_hashtags_traced(["#diss", "#rap"], Platform.tiktok, "en",
                                        lean="bold", corpus=["#customtag"], content=["#diss"])
    assert set(sources) == set(tags)                   # one source per shipped tag
    assert all(sources[t] for t in tags)               # none empty/sourceless


def test_source_priority_content_over_reach():
    # a tag that is BOTH a content candidate AND a reach/genre tag is credited to content.
    tags, sources = vet_hashtags_traced(["#newmusic"], Platform.instagram, "en",
                                        content=["#newmusic"])
    assert sources.get("#newmusic") == "content"


def test_traced_list_matches_plain_vet():
    # DRY contract: the traced list == the plain list for identical inputs.
    kw = dict(lean="underground", corpus=["#customtag"], content=["#loyalty"])
    plain = vet_hashtags(["#diss"], Platform.tiktok, "en", **kw)
    traced, _ = vet_hashtags_traced(["#diss"], Platform.tiktok, "en", **kw)
    assert plain == traced
