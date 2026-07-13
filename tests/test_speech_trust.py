# tests/test_speech_trust.py — segment_trusted / trusted_segments / window_has_trusted_speech / excerpt_for_window
from fanops.models import Source, SourceState
from fanops.transcribe import (segment_trusted, trusted_segments, window_has_trusted_speech,
                                excerpt_for_window, _segment_metadata_pass,
                                _trust_tier, _NO_SPEECH_MAX, _AVG_LOGPROB_MIN, _COMPRESSION_RATIO_MAX)
from tests.fixtures.speech_segments import (GOOD_AR, MUSIC_HALLUC, LATIN_JUNK_AR, CJK_JUNK_EN,
                                              LEGACY_EN, talk_seg)


def _seg(text, *, start=0.0, end=2.0, **kw):
    return {"start": start, "end": end, "text": text, **kw}


def test_trust_tier_matrix():
    """Plan C L2: six-case trust_tier / segment_trusted composition."""
    assert _trust_tier(GOOD_AR, src_lang="ar") == "full"
    assert segment_trusted(GOOD_AR, src_lang="ar") is True
    assert _trust_tier(MUSIC_HALLUC, src_lang="en") == "rejected"
    assert segment_trusted(MUSIC_HALLUC, src_lang="en") is False
    assert _trust_tier(LATIN_JUNK_AR, src_lang="ar") == "rejected"
    assert segment_trusted(LATIN_JUNK_AR, src_lang="ar") is False
    assert _trust_tier(CJK_JUNK_EN, src_lang="en") == "rejected"
    assert segment_trusted(CJK_JUNK_EN, src_lang="en") is False
    assert _trust_tier(LEGACY_EN, src_lang="en") == "degraded"
    assert segment_trusted(LEGACY_EN, src_lang="en") is False   # degraded ≠ full
    empty = _seg("  ")
    assert _trust_tier(empty, src_lang="en") == "rejected"
    assert segment_trusted(empty, src_lang="en") is False


def test_segment_trusted_accepts_clean_arabic_with_metadata():
    seg = _seg(" ورا الستارة", avg_logprob=-0.3, no_speech_prob=0.05, compression_ratio=1.5)
    assert segment_trusted(seg, src_lang="ar") is True


def test_segment_trusted_rejects_high_no_speech_prob():
    seg = _seg("background noise", no_speech_prob=0.9, avg_logprob=-0.2, compression_ratio=1.2)
    assert segment_trusted(seg, src_lang="en") is False


def test_segment_trusted_rejects_low_avg_logprob():
    seg = _seg("gibberish line", avg_logprob=-1.5, no_speech_prob=0.1, compression_ratio=1.2)
    assert segment_trusted(seg, src_lang="en") is False


def test_segment_trusted_rejects_high_compression_ratio():
    seg = _seg("repeat repeat repeat", avg_logprob=-0.3, no_speech_prob=0.1, compression_ratio=3.0)
    assert segment_trusted(seg, src_lang="en") is False


def test_segment_trusted_rejects_latin_junk_on_ar_source():
    seg = _seg("man shay khbar hada", src_lang="ar")  # no metadata — script heuristic only
    assert segment_trusted(seg, src_lang="ar") is False


def test_segment_trusted_rejects_cjk_on_en_source():
    seg = _seg("東西東西test", avg_logprob=-0.2, no_speech_prob=0.05, compression_ratio=1.2)
    assert segment_trusted(seg, src_lang="en") is False


def test_segment_trusted_uses_stamped_trust_tier():
    stamped = {**GOOD_AR, "trust_tier": "degraded", "trusted": False}
    assert segment_trusted(stamped, src_lang="ar") is False
    stamped["trust_tier"] = "full"
    assert segment_trusted(stamped, src_lang="ar") is True


def test_segment_metadata_pass_matrix():
    assert _segment_metadata_pass(GOOD_AR) is True
    assert _segment_metadata_pass(MUSIC_HALLUC) is False                    # high no_speech_prob
    assert _segment_metadata_pass({**GOOD_AR, "avg_logprob": -1.5}) is False
    assert _segment_metadata_pass({**GOOD_AR, "compression_ratio": 3.0}) is False
    partial = {k: v for k, v in GOOD_AR.items() if k != "compression_ratio"}
    assert _segment_metadata_pass(partial) is False
    assert _segment_metadata_pass({**GOOD_AR, "no_speech_prob": _NO_SPEECH_MAX}) is True
    assert _segment_metadata_pass({**GOOD_AR, "avg_logprob": _AVG_LOGPROB_MIN}) is True
    assert _segment_metadata_pass({**GOOD_AR, "compression_ratio": _COMPRESSION_RATIO_MAX}) is True


def test_trusted_segments_filters_full_tier_only():
    segs = [_seg("good line", avg_logprob=-0.2, no_speech_prob=0.05, compression_ratio=1.2),
            _seg("noise", no_speech_prob=0.95, avg_logprob=-0.2, compression_ratio=1.2),
            LEGACY_EN,
            {**GOOD_AR, "trust_tier": "full", "trusted": True}]
    out = trusted_segments(segs, src_lang="en")
    texts = [s["text"] for s in out]
    assert "good line" in texts
    assert "noise" not in texts
    assert LEGACY_EN["text"] not in texts                              # degraded, not full
    assert GOOD_AR["text"] in texts                                    # stamped full tier


def test_window_has_trusted_speech_requires_full_tier_overlap_and_word_count():
    src = Source(id="s1", source_path="/x.mp4", state=SourceState.transcribed, language="en",
                 transcript=[_seg("they slept on me here", start=1.0, end=4.0,
                                  avg_logprob=-0.2, no_speech_prob=0.05, compression_ratio=1.2),
                             _seg("noise", start=10.0, end=12.0, no_speech_prob=0.95),
                             {**LEGACY_EN, "start": 5.0, "end": 8.0}])   # degraded — no overlap credit
    assert window_has_trusted_speech(src, 0.0, 5.0) is True
    assert window_has_trusted_speech(src, 10.0, 13.0) is False
    assert window_has_trusted_speech(src, 5.0, 9.0) is False           # legacy degraded only


def test_window_has_trusted_speech_fixture_matrix():
    """Plan D L3: full-tier overlap only; rejected/degraded/one-word -> False."""
    def _src(*segs, lang="en"):
        return Source(id="s1", source_path="/x.mp4", state=SourceState.transcribed, language=lang,
                      transcript=list(segs))
    ar = _src({**GOOD_AR, "start": 0.0, "end": 2.0}, lang="ar")
    assert window_has_trusted_speech(ar, 0.0, 2.5) is True
    hall = _src({**MUSIC_HALLUC, "start": 0.0, "end": 2.0})
    assert window_has_trusted_speech(hall, 0.0, 2.5) is False
    deg = _src({**LEGACY_EN, "start": 0.0, "end": 2.0})
    assert window_has_trusted_speech(deg, 0.0, 2.5) is False
    one = _src(talk_seg("hello", start=0.0, end=2.0))
    assert window_has_trusted_speech(one, 0.0, 2.5) is False


def test_excerpt_for_window_joins_full_tier_only():
    src = Source(id="s1", source_path="/x.mp4", state=SourceState.transcribed, language="en",
                 transcript=[talk_seg("first trusted line", start=0.0, end=2.0),
                             talk_seg("second trusted bit", start=2.0, end=4.0),
                             {**MUSIC_HALLUC, "start": 1.0, "end": 3.0, "text": "noise junk"},
                             {**LEGACY_EN, "start": 3.0, "end": 5.0}])
    assert excerpt_for_window(src, 0.0, 5.0) == "first trusted line second trusted bit"
    assert excerpt_for_window(src, 0.0, 1.5) == "first trusted line"
    long = "word " * 80
    src2 = Source(id="s2", source_path="/y.mp4", state=SourceState.transcribed, language="en",
                  transcript=[talk_seg(long.strip(), start=0.0, end=10.0)])
    assert len(excerpt_for_window(src2, 0.0, 10.0, max_chars=240)) == 240
