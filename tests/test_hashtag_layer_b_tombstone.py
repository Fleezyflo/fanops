"""Layer B selector / corpus writer / vocab expander stay deleted."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_layer_b_selector_stays_deleted():
    import fanops.hashtags as h
    for name in ("vet_hashtags", "vet_hashtags_traced", "content_tag_candidates",
                 "_ARABIC", "MEGA_SLOT_MAX", "_CORPUS_LEAD_MAX"):
        assert not hasattr(h, name), name


def test_corpus_writer_stays_deleted():
    import fanops.persona_research as pr
    for name in ("derive_corpus", "refresh_corpora_if_due", "_aligned_pool", "derived_report"):
        assert not hasattr(pr, name), name


def test_hashtag_vocab_module_stays_deleted():
    assert not (ROOT / "src/fanops/hashtag_vocab.py").exists()


def test_tag_outcomes_module_stays_deleted():
    assert not (ROOT / "src/fanops/tag_outcomes.py").exists()


def test_corpus_rederive_stays_deleted():
    import fanops.fanops_hashtags as fh
    assert not hasattr(fh, "_rederive_posting_corpora")
