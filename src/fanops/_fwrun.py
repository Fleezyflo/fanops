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
import argparse, json, sys
from pathlib import Path


def _certifi_env() -> None:
    """faster-whisper fetches the checkpoint from HF over https on first use; macOS framework Python
    often can't verify the cert. Point SSL_CERT_FILE/REQUESTS_CA_BUNDLE at certifi (no-op if already
    set or certifi absent). Mirrors vocals._demucs_env()."""
    import os
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except ImportError: pass    # ECC fix #6: certifi optional — only its absence is expected; don't mask other faults


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
    <out_dir>/<audio-stem>.json; return that path. A comma-list `language` (e.g. "en,ar") PINS multiple
    candidates -> per-segment detection (multilingual=True), so EN directing lines + AR verses in ONE
    source both transcribe; a single value forces that language; ""/None -> unconstrained auto-detect.
    word_timestamps drive the overlay's sync."""
    wm = _load_model(model)
    langs = [x for x in (language or "").replace(",", " ").split() if x]
    multi = len(langs) > 1                                # >1 candidate -> per-segment language detection
    segments, info = wm.transcribe(audio, language=(None if multi else (langs[0] if langs else None)),
                                   multilingual=multi, word_timestamps=True, task="transcribe")
    out = []
    for s in segments:                                   # faster-whisper yields segments lazily
        seg = {"start": float(s.start), "end": float(s.end), "text": s.text}
        words = getattr(s, "words", None)
        if words: seg["words"] = [_word(w) for w in words]
        out.append(seg)
    js = Path(out_dir) / f"{Path(audio).stem}.json"
    js.parent.mkdir(parents=True, exist_ok=True)
    js.write_text(json.dumps({"language": info.language, "segments": out}, ensure_ascii=False))
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
