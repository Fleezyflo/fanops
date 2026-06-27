# tests/test_persona_lean_fold.py — M3b: the tag_lean -> hashtag_corpus fold is NON-LOSSY. Before tag_lean is
# retired (M3c), prove that a persona leaning X (empty corpus) ships the SAME reach as a persona with NO lean
# but a corpus seeded from X's pool. This is the property that lets M3c drop tag_lean without a reach regression.
#
# Exactness: with NO language (the operator-facing persona_facts read, language=None) the fold is BYTE-identical
# (ordered). On an AR clip the SET of tags is identical (no reach lost) but the region tag's POSITION can differ
# by one slot — a leaned persona has an empty corpus so the AR reserve leads, a folded persona seeds the corpus
# first so the AR reserve lands in the tail. Same tags, same reach; cosmetic order only. Documented, not hidden.
from fanops.hashtags import vet_hashtags, _LEANS
from fanops.models import Platform


def test_fold_is_byte_identical_with_no_language():
    # the operator-facing path (persona_facts passes language=None) — ordered, byte-identical.
    for lean, pool in _LEANS.items():
        leaned = vet_hashtags([], Platform.instagram, None, lean=lean)
        folded = vet_hashtags([], Platform.instagram, None, corpus=list(pool))
        assert leaned == folded, f"fold drift (no-language) for lean={lean}: {leaned} != {folded}"


def test_fold_is_byte_identical_for_non_arabic_clips():
    for lean, pool in _LEANS.items():
        for plat in (Platform.instagram, Platform.tiktok):
            leaned = vet_hashtags([], plat, "en", lean=lean)
            folded = vet_hashtags([], plat, "en", corpus=list(pool))
            assert leaned == folded, f"fold drift (en) for lean={lean} {plat}: {leaned} != {folded}"


def test_fold_preserves_the_tag_set_and_reach_on_arabic_clips():
    # AR: identical SET (no reach lost); the region tag's position may differ by one slot (corpus seeds first).
    for lean, pool in _LEANS.items():
        leaned = vet_hashtags([], Platform.instagram, "ar", lean=lean)
        folded = vet_hashtags([], Platform.instagram, "ar", corpus=list(pool))
        assert set(leaned) == set(folded), f"fold lost a tag (ar) for lean={lean}: {leaned} vs {folded}"
        assert any("arab" in t for t in folded)          # region reach still floored under the corpus


def test_seeded_corpora_still_differ_across_the_three_leans():
    # post-fold the 3 personas must still differ — the _LEANS pools are disjoint, so the seeded corpora are too.
    folded = {lean: tuple(vet_hashtags([], Platform.instagram, None, corpus=list(pool)))
              for lean, pool in _LEANS.items()}
    assert len(set(folded.values())) == 3, f"seeded corpora converged: {folded}"
