# tests/test_fwrun.py — JSON-shaping primitives of fanops._fwrun (real helpers, no _load_model patch).
# transcribe_to_json loads faster-whisper; unit CI does not install [asr]. The write-path of
# <stem>.json + ledger adopt is pinned through transcribe_source + subprocess argv in test_transcribe.py.
import fanops._fwrun as fwrun


class _FakeWord:
    def __init__(self, word, start, end): self.word = word; self.start = start; self.end = end

class _FakeSeg:
    def __init__(self, start, end, text, *, avg_logprob=None, no_speech_prob=None, compression_ratio=None):
        self.start = start; self.end = end; self.text = text
        self.avg_logprob = avg_logprob; self.no_speech_prob = no_speech_prob; self.compression_ratio = compression_ratio


def test_word_null_timestamps_are_preserved():
    # faster-whisper can emit a word with null start/end (mirrors the openai-whisper null-ts case the
    # overlay already None-guards). Never float(None).
    w = fwrun._word(_FakeWord("x", None, None))
    assert w == {"word": "x", "start": None, "end": None}


def test_word_serializes_numeric_timings():
    w = fwrun._word(_FakeWord(" الستارة", 0.5, 1.4))
    assert w == {"word": " الستارة", "start": 0.5, "end": 1.4}


def test_seg_quality_preserves_decoder_fields():
    q = fwrun._seg_quality(_FakeSeg(0.0, 2.0, "hi", avg_logprob=-0.42, no_speech_prob=0.08, compression_ratio=1.6))
    assert q == {"avg_logprob": -0.42, "no_speech_prob": 0.08, "compression_ratio": 1.6}


def test_seg_quality_omits_missing_fields():
    q = fwrun._seg_quality(_FakeSeg(0.0, 1.0, "hi"))
    assert q == {}
