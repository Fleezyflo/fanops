# tests/test_fwrun.py — the faster-whisper subprocess runner (fanops._fwrun). The model load is
# behind _load_model so these tests cover the JSON-SHAPING logic without importing faster-whisper
# (it's an optional [asr] extra; CI's unit job doesn't install it). The runner writes
# whisper-compatible JSON ({language, segments:[{start,end,text[,words]}]}) named by the INPUT stem,
# so transcribe.py's existing parser + .json lookup stay unchanged.
import json
from pathlib import Path
import fanops._fwrun as fwrun


class _FakeWord:
    def __init__(self, word, start, end): self.word = word; self.start = start; self.end = end

class _FakeSeg:
    def __init__(self, start, end, text, words=None):
        self.start = start; self.end = end; self.text = text; self.words = words

class _FakeInfo:
    def __init__(self, language): self.language = language

class _FakeModel:
    def __init__(self, segs, lang): self._segs = segs; self._lang = lang; self.calls = {}
    def transcribe(self, audio, **kw): self.calls = dict(kw, audio=audio); return iter(self._segs), _FakeInfo(self._lang)


def test_transcribe_to_json_writes_whisper_shaped_json(tmp_path, mocker):
    segs = [_FakeSeg(0.0, 2.0, " ورا الستارة",
                     words=[_FakeWord(" ورا", 0.0, 0.5), _FakeWord(" الستارة", 0.5, 1.4)])]
    model = _FakeModel(segs, "ar")
    mocker.patch("fanops._fwrun._load_model", return_value=model)
    js = fwrun.transcribe_to_json(str(tmp_path / "src_1.mp3"), str(tmp_path / "out"), "large-v3", None)
    assert Path(js).name == "src_1.json"                       # named by the INPUT stem (parser lookup)
    data = json.loads(Path(js).read_text())
    assert data["language"] == "ar"
    seg = data["segments"][0]
    assert seg["start"] == 0.0 and seg["end"] == 2.0 and seg["text"] == " ورا الستارة"
    assert seg["words"][1] == {"word": " الستارة", "start": 0.5, "end": 1.4}


def test_transcribe_to_json_auto_language_passes_none(tmp_path, mocker):
    # language="" / None -> auto-detect: WhisperModel.transcribe must be called with language=None,
    # word_timestamps=True, task="transcribe" (handles EN+AR per clip).
    model = _FakeModel([_FakeSeg(0.0, 1.0, "hi")], "en")
    mocker.patch("fanops._fwrun._load_model", return_value=model)
    fwrun.transcribe_to_json(str(tmp_path / "s.mp3"), str(tmp_path / "o"), "large-v3", "")
    assert model.calls["language"] is None
    assert model.calls["word_timestamps"] is True and model.calls["task"] == "transcribe"


def test_transcribe_to_json_null_word_timestamps_are_preserved(tmp_path, mocker):
    # faster-whisper can emit a word with null start/end (mirrors the openai-whisper null-ts case the
    # overlay already None-guards). The runner must serialize them as JSON null, never crash on float(None).
    segs = [_FakeSeg(0.0, 2.0, "x", words=[_FakeWord("x", None, None)])]
    mocker.patch("fanops._fwrun._load_model", return_value=_FakeModel(segs, "en"))
    js = fwrun.transcribe_to_json(str(tmp_path / "s.mp3"), str(tmp_path / "o"), "large-v3", None)
    w = json.loads(Path(js).read_text())["segments"][0]["words"][0]
    assert w == {"word": "x", "start": None, "end": None}


def test_transcribe_to_json_segment_without_words_omits_key(tmp_path, mocker):
    mocker.patch("fanops._fwrun._load_model", return_value=_FakeModel([_FakeSeg(0.0, 1.0, "hi", words=None)], "en"))
    js = fwrun.transcribe_to_json(str(tmp_path / "s.mp3"), str(tmp_path / "o"), "large-v3", None)
    assert "words" not in json.loads(Path(js).read_text())["segments"][0]


def test_main_parses_args_and_runs(tmp_path, mocker):
    spy = mocker.patch("fanops._fwrun.transcribe_to_json", return_value=str(tmp_path / "x.json"))
    rc = fwrun.main(["--model", "large-v3", "--language", "", "--output_dir", str(tmp_path), str(tmp_path / "a.mp3")])
    assert rc == 0
    spy.assert_called_once_with(str(tmp_path / "a.mp3"), str(tmp_path), "large-v3", None)
