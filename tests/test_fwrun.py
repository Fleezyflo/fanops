# tests/test_fwrun.py — JSON-shaping primitives of fanops._fwrun.
# transcribe_to_json loads faster-whisper; unit CI does not install [asr]. The write-path of
# <stem>.json + ledger adopt is pinned through transcribe_source + subprocess argv in test_transcribe.py.
# The PyAV 19 compat is tested with a fake av module so the suite never imports faster-whisper.
from pathlib import Path

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


class _Av19:
    """PyAV 19: av.open rejects the metadata_errors kwarg faster-whisper 1.2.1 still passes."""
    __version__ = "19.0.1"

    def __init__(self):
        self.seen = None

    def open(self, *args, **kwargs):
        if "metadata_errors" in kwargs:
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
        self.seen = (args, kwargs)
        return "container"


class _Av18:
    __version__ = "18.1.0"

    def __init__(self):
        self.seen = None

    def open(self, *args, **kwargs):
        self.seen = (args, kwargs)
        return "container"


def test_pyav19_open_compat_strips_removed_metadata_errors():
    # The nightly failure mode: faster-whisper 1.2.1 calls av.open(..., metadata_errors="ignore")
    # and PyAV 19.0.1 raises TypeError. The subprocess then exits 1 with no JSON.
    av = _Av19()
    try:
        av.open("clip.wav", mode="r", metadata_errors="ignore")
        raised = False
    except TypeError:
        raised = True
    assert raised
    fwrun._install_pyav19_open_compat(av)
    assert av.open("clip.wav", mode="r", metadata_errors="ignore") == "container"
    assert av.seen == (("clip.wav",), {"mode": "r"})


def test_pyav18_open_compat_keeps_metadata_errors():
    av = _Av18()
    fwrun._install_pyav19_open_compat(av)
    av.open("clip.wav", mode="r", metadata_errors="ignore")
    assert av.seen[1]["metadata_errors"] == "ignore"


def test_transcribe_to_json_installs_pyav_compat_before_decode(tmp_path, monkeypatch):
    order = []

    class _Info:
        language = "en"

    class _Model:
        def transcribe(self, *args, **kwargs):
            order.append("transcribe")
            return iter([_FakeSeg(0.0, 1.0, " hello there")]), _Info()

    monkeypatch.setattr(fwrun, "_load_model", lambda model: (order.append("load"), _Model())[1])
    monkeypatch.setattr(fwrun, "_install_pyav19_open_compat", lambda av_module=None: order.append("compat"))
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF")
    out = fwrun.transcribe_to_json(str(audio), str(tmp_path), "tiny", "en")
    assert order == ["load", "compat", "transcribe"]
    assert Path(out).read_text().startswith("{")
