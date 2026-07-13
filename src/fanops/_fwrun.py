# src/fanops/_fwrun.py
"""Bounded faster-whisper (CTranslate2) runner, invoked as a SUBPROCESS by transcribe.py so a
wedged model stays killable at the timeout (transcribe_source holds the ledger lock — an in-process
call could not be reaped). The model is chosen by the caller via FANOPS_ASR_MODEL (default medium);
large-v3 is OFFERED because, on Demucs-isolated vocals, it is the proven music/rap accuracy winner
(clean coherent Arabic where turbo produced gibberish), and int8 makes even large-v3 practical on CPU
(~1-4min/clip vs 5-15 via the stock whisper CLI). Free, on-machine, NO API.

Writes whisper-compatible JSON ({language, segments:[{start,end,text[,words]}]}) named by the INPUT
stem, so transcribe.py's existing JSON parser + per-source .json lookup are unchanged. The model
load is behind _load_model so the JSON-shaping logic is testable without importing faster-whisper
(an optional [asr] extra). FAIL-LOUD: any failure exits nonzero so transcribe_source parks the
source as a RETRIABLE error — never a silent empty transcript. The fallback to the legacy whisper
CLI when faster-whisper is absent is decided in transcribe.py BEFORE this runs."""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

from fanops.config import certifi_ssl_env


def _certifi_env() -> None:
    certifi_ssl_env()  # in-process SSL defaults for HF model fetch (mirrors vocals._demucs_env)


def _load_model(model: str):
    """Load the faster-whisper model on CPU with int8 (the practical-on-CPU quantization). Isolated
    behind this function so transcribe_to_json's JSON shaping is unit-testable without the heavy
    optional dep (tests patch _load_model)."""
    from faster_whisper import WhisperModel
    return WhisperModel(model, device="cpu", compute_type="int8")


def _word(w) -> dict:
    """Serialize one faster-whisper word, None-guarding start/end (the runtime can emit null word
    timings — same case the overlay already tolerates; never float(None))."""
    return {"word": w.word, "start": (None if w.start is None else float(w.start)),
            "end": (None if w.end is None else float(w.end))}


def transcribe_to_json(audio: str, out_dir: str, model: str, language: str | None) -> str:
    """Transcribe `audio` with faster-whisper and write whisper-shaped JSON to
    <out_dir>/<audio-stem>.json; return that path. A comma-list `language` (e.g. "en,ar") enables
    per-segment detection (multilingual=True) so EN directing lines + AR verses in ONE source both
    transcribe — but NOTE: the listed candidates CANNOT be enforced (faster-whisper's per-segment
    detection ranges over ALL whisper languages; a sung/noisy segment can come back in a language
    nobody spoke — the burn layer's script scrub in overlay._scrub_caption_text is the enforcement
    point). A single value forces that language; ""/None -> whole-file auto-detect. word_timestamps
    drive the overlay's sync. vad_filter + condition_on_previous_text=False are the standard
    anti-hallucination controls (2026-07-13 incident: repetition loops + a CJK mash on song
    playback) — VAD drops non-speech windows, conditioning-off stops one bad segment from cascading."""
    wm = _load_model(model)
    langs = [x for x in (language or "").replace(",", " ").split() if x]
    multi = len(langs) > 1                                # >1 candidate -> per-segment language detection
    segments, info = wm.transcribe(audio, language=(None if multi else (langs[0] if langs else None)),
                                   multilingual=multi, word_timestamps=True, task="transcribe",
                                   vad_filter=True, condition_on_previous_text=False)
    out = []
    for s in segments:                                   # faster-whisper yields segments lazily
        seg = {"start": float(s.start), "end": float(s.end), "text": s.text}
        words = getattr(s, "words", None)
        if words: seg["words"] = [_word(w) for w in words]
        out.append(seg)
    js = Path(out_dir) / f"{Path(audio).stem}.json"
    js.parent.mkdir(parents=True, exist_ok=True)
    # M1: atomic write — write to <name>.json.tmp then os.replace. A reader that opens the JSON
    # while the producer is mid-write (the race a concurrent transcribe_source caller short-circuits
    # on AFTER stage_lock release) never sees a truncated file. Defense-in-depth atop stage_lock —
    # the lock prevents two writers, this prevents a partial write from being read.
    tmp = js.with_suffix(js.suffix + ".tmp")
    tmp.write_text(json.dumps({"language": info.language, "segments": out}, ensure_ascii=False))
    os.replace(str(tmp), str(js))
    return str(js)


def main(argv: list[str] | None = None) -> int:
    _certifi_env()
    p = argparse.ArgumentParser(prog="fanops._fwrun")
    p.add_argument("--model", required=True); p.add_argument("--language", default="")
    p.add_argument("--output_dir", required=True); p.add_argument("audio")
    a = p.parse_args(argv)
    transcribe_to_json(a.audio, a.output_dir, a.model, a.language or None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
