# tests/test_speech_trust.py — segment_trusted / trusted_segments / window_has_trusted_speech
from fanops.models import Source, SourceState, Batch
from fanops.config import Config
from fanops.transcribe import (segment_trusted, trusted_segments, window_has_trusted_speech,
                                resolve_speech_trust)


def _seg(text, *, start=0.0, end=2.0, **kw):
    return {"start": start, "end": end, "text": text, **kw}


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


def test_segment_trusted_legacy_segment_uses_script_heuristic_only():
    good = _seg("they slept on me")
    bad = _seg("xyz abc def ghi")  # latin on ar
    assert segment_trusted(good, src_lang="en") is True
    assert segment_trusted(bad, src_lang="ar") is False


def test_trusted_segments_filters_list():
    segs = [_seg("good line", avg_logprob=-0.2, no_speech_prob=0.05, compression_ratio=1.2),
            _seg("noise", no_speech_prob=0.95, avg_logprob=-0.2, compression_ratio=1.2)]
    out = trusted_segments(segs, src_lang="en")
    assert len(out) == 1 and out[0]["text"] == "good line"


def test_window_has_trusted_speech_requires_overlap_and_word_count():
    src = Source(id="s1", source_path="/x.mp4", state=SourceState.transcribed, language="en",
                 transcript=[_seg("they slept on me here", start=1.0, end=4.0,
                                  avg_logprob=-0.2, no_speech_prob=0.05, compression_ratio=1.2),
                             _seg("noise", start=10.0, end=12.0, no_speech_prob=0.95)])
    assert window_has_trusted_speech(src, 0.0, 5.0) is True
    assert window_has_trusted_speech(src, 10.0, 13.0) is False
    assert window_has_trusted_speech(src, 5.0, 9.0) is False


def test_resolve_speech_trust_batch_override(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_SPEECH_TRUST", raising=False)
    cfg = Config(root=tmp_path)
    assert resolve_speech_trust(cfg, None) is False
    assert resolve_speech_trust(cfg, Batch(id="b", name="b", speech_trust=True)) is True
    assert resolve_speech_trust(cfg, Batch(id="b", name="b", speech_trust=False)) is False
    assert resolve_speech_trust(cfg, Batch(id="b", name="b")) is False
    monkeypatch.setenv("FANOPS_SPEECH_TRUST", "1")
    cfg_on = Config(root=tmp_path)
    assert resolve_speech_trust(cfg_on, Batch(id="b", name="b", speech_trust=False)) is False
    assert resolve_speech_trust(cfg_on, Batch(id="b", name="b")) is True
