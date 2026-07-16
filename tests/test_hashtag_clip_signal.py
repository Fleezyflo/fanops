# tests/test_hashtag_clip_signal.py
# The clip must be able to reach its own hashtag line. Two structural defects made the shipped line a pure
# function of (persona, position-in-pass) — measured on the live ledger: 319/347 posts (91.9%) shipped their
# handle's corpus[0:4] verbatim, and all 21 distinct lines across 347 posts were pure corpus slices, with the
# model's picks appearing NOWHERE despite 0/347 caption failures.
#   H1 corpus monopoly  — corpus is tier 0 and seeded whole, so a corpus of >= max_tags took every slot and the
#                         model's (already vetted) per-clip picks could never reach the cap.
#   H2 rotation lock    — recency was a BOOLEAN membership flag; once pass_recent covered the corpus the
#                         tiebreak went constant and the sort collapsed to corpus rank, locking the line.
# Every test here fails on the pre-fix selector. The byte-identity guards pin the fail-open contract: no vetted
# picks, junk picks, or a corpus at/below the lead cap must reproduce today's line exactly.
import pytest
from fanops.hashtags import vet_hashtags, _CORPUS_LEAD_MAX
from fanops.models import Platform

UZ = ["#freestyle", "#undergroundhiphop", "#trap", "#methodman", "#wuwear", "#90shiphop",
      "#rza", "#wutang", "#ghostfacekillah", "#wutangclan", "#cappadonna", "#wutangbrand"]   # a live 12-tag corpus
CF = ["#podcast", "#interview", "#facts", "#science"]                                        # a live 4-tag corpus (5 of 8 personas)
STORE = UZ + CF + ["#hiphop", "#rap", "#bars", "#viral", "#fyp", "#reels"]


def _line(picks, corpus, recent=None, lang="en", store=STORE):
    return vet_hashtags(picks, Platform.instagram, lang, 4, store=store, corpus=corpus,
                        content=None, genre="rap", cfg=None, recent=recent or [])


# ---- H1: the corpus may not monopolise the line ------------------------------------------------------
def test_model_picks_reach_the_line_under_a_full_corpus():
    # THE defect: with |corpus| >= max_tags the model's vetted picks were unreachable. Pre-fix these two are
    # byte-identical; the model's clip work was a proven no-op.
    blind = _line([], UZ)
    picked = _line(["#podcast", "#interview"], UZ)
    assert picked != blind
    assert "#podcast" in picked and "#interview" in picked


def test_corpus_picks_lead_when_the_model_endorses_them():
    # caption_prompt SHOWS the model the corpus as its menu, so most picks ARE corpus tags — the common case.
    # A pick that is itself curated must still register as a clip signal and lead the untouched corpus tags.
    out = _line(["#wutang", "#rza", "#methodman", "#90shiphop"], UZ)
    assert out == ["#methodman", "#90shiphop", "#rza", "#wutang"]     # the model's own picks, in corpus order


def test_corpus_keeps_its_lead_and_the_clip_gets_the_rest():
    out = _line(["#podcast", "#interview", "#facts"], UZ)
    assert out[:_CORPUS_LEAD_MAX] == UZ[:_CORPUS_LEAD_MAX]            # curation still leads every post
    assert len([t for t in out if t not in UZ]) == 4 - _CORPUS_LEAD_MAX


def test_four_tag_corpus_yields_slots_to_the_clip():
    # |corpus| == max_tags had ZERO degrees of freedom: the line was a constant by construction.
    assert _line(["#hiphop", "#rap"], CF) != _line([], CF)
    assert _line([], CF) == CF                                        # unchanged when the model adds nothing


@pytest.mark.parametrize("picks", [[], ["#zzzz", "#qqqq"], None])
def test_no_vetted_picks_is_byte_identical(picks):
    # FIREWALL: nothing vetted to promote -> today's line, exactly. The lead cap must not reorder on its own.
    assert _line(picks, UZ) == ["#freestyle", "#undergroundhiphop", "#trap", "#methodman"]


@pytest.mark.parametrize("corpus", [["#podcast"], ["#podcast", "#interview"]])
def test_small_corpus_leads_and_the_clip_still_ships(corpus):
    # |corpus| <= _CORPUS_LEAD_MAX cannot monopolise 4 slots, so the lead cap never fires: the whole corpus
    # still leads and the model's picks fill the rest, exactly as before the cap existed.
    out = _line(["#hiphop", "#rap"], corpus)
    assert out[:len(corpus)] == corpus
    assert "#hiphop" in out and "#rap" in out


def test_hard_cap_still_four():
    assert len(_line(["#podcast", "#interview", "#facts", "#science", "#hiphop"], UZ)) == 4


# ---- H2: saturated recency rotates, it does not lock -------------------------------------------------
def test_saturated_recency_rotates_instead_of_locking():
    # Pre-fix: once `recent` covered the corpus every tag carried the same flag, the tiebreak went constant,
    # and the sort fell back to corpus rank -> corpus[0:4] forever. Reproduces the live 66/2/1 distribution.
    recent, shipped = list(UZ[:4]), []
    for _ in range(6):
        out = _line([], UZ, recent=recent)
        shipped.append(tuple(out))
        recent = recent + out                       # what pipeline.pass_recent does within one pass
    assert len(set(shipped[2:])) > 1, "locked on a single line once recency saturated"
    assert len(set(shipped)) == 3                   # a clean 3-cycle over a 12-tag corpus at 4 per line


def test_saturated_recency_reaches_every_corpus_tag():
    recent, seen = [], set()
    for _ in range(6):
        out = _line([], UZ, recent=recent)
        seen.update(out); recent = recent + out
    assert seen == set(UZ), "some curated tags could never ship"


def test_recent_empty_is_byte_identical():
    base = _line([], UZ, recent=[])
    assert _line([], UZ, recent=None) == base == ["#freestyle", "#undergroundhiphop", "#trap", "#methodman"]


def test_clip_signal_outranks_rotation():
    # The model's per-clip judgement is the point; anti-repetition must not override it.
    out = _line(["#wutang"], UZ, recent=["#wutang"])
    assert "#wutang" in out
