# src/fanops/speech_trust.py
"""Speech-trust (L1–L3): segment quality metadata, script coherence, and production gates.

Each segment gets a stamped trust_tier (full / degraded / rejected). Production gates (subs burn,
moment pick, hook excerpt, framing classify) consume only full-tier segments via trusted_segments /
window_has_trusted_speech / excerpt_for_window. No subprocess or ASR engine imports."""
from __future__ import annotations

_SEGMENT_QUALITY_KEYS = ("avg_logprob", "no_speech_prob", "compression_ratio")
# Required for L1 / cache adopt / full trust_tier. no_speech_prob remains optional passthrough —
# faster-whisper copies a window-level speech-vs-music prior onto every segment in the decode
# chunk, so rap over a beat scores 0.8–0.9 while avg_logprob still says the lyrics are confident.
# VAD already dropped silence; L1 here is "did the decoder commit to this text".
_SEGMENT_REQUIRED_QUALITY_KEYS = ("avg_logprob", "compression_ratio")
_AVG_LOGPROB_MIN = -1.0
_COMPRESSION_RATIO_MAX = 2.4

_SCRIPT_LATIN_ON_AR = 0.70          # Latin-majority segment on an ar source -> junk transliteration
_SCRIPT_CJK_ON_EN_AR = 0.30         # CJK-majority segment on en/ar source -> junk hallucination
_SPEECH_MIN_WORDS = 2               # mirrors framing._window_has_speech bar


def _segment_metadata_pass(seg: dict) -> bool:
    """L1: required decoder-quality keys present and in range. Partial required keys -> False.
    no_speech_prob is optional (passthrough when present) and is not thresholded."""
    if not all(k in seg for k in _SEGMENT_REQUIRED_QUALITY_KEYS):
        return False
    try:
        if float(seg["avg_logprob"]) < _AVG_LOGPROB_MIN: return False
        if float(seg["compression_ratio"]) > _COMPRESSION_RATIO_MAX: return False
    except (TypeError, ValueError):
        return False
    return True


def _segment(s: dict) -> dict:
    """One transcript segment: {start,end,text}, plus `words` ([{word,start,end}]) when whisper
    emitted word timestamps (--word_timestamps). Optional quality keys (avg_logprob, no_speech_prob,
    compression_ratio) pass through when present — additive, old JSON without them is unchanged."""
    seg = {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
    words = s.get("words")
    if isinstance(words, list) and words and all(isinstance(w, dict) and "word" in w for w in words):
        seg["words"] = [{"word": w["word"], "start": w.get("start"), "end": w.get("end")} for w in words]
    for k in _SEGMENT_QUALITY_KEYS:
        if k in s:
            try: seg[k] = float(s[k])
            except (TypeError, ValueError): pass
    return seg


def _transcript_schema(segments: list[dict]) -> int:
    """Sidecar schema version: 2 when any segment carries ASR quality metadata."""
    for seg in segments or []:
        if any(k in seg for k in _SEGMENT_QUALITY_KEYS):
            return 2
    return 1


def _cache_is_quality_complete(data: dict) -> bool:
    """True when every non-empty cached segment carries required ASR quality keys."""
    for s in data.get("segments") or []:
        text = (s.get("text") or "").strip()
        if not text: continue
        if not all(k in s for k in _SEGMENT_REQUIRED_QUALITY_KEYS):
            return False
    return True


def _lang_base(tag: str | None) -> str | None:
    if not tag: return None
    base = tag.strip().lower().replace("_", "-").split("-", 1)[0]
    return base or None


def _script_counts(text: str) -> tuple[int, int, int]:
    """Return (alpha, latin, cjk) char counts for script-coherence checks."""
    alpha = latin = cjk = 0
    for c in text or "":
        if not c.isalpha(): continue
        alpha += 1
        o = ord(c)
        if '\u0600' <= c <= '\u06ff': pass                          # Arabic — not latin
        elif o < 128 or (0x00C0 <= o <= 0x024F): latin += 1         # Basic Latin + Latin-1 supplement
        elif 0x4E00 <= o <= 0x9FFF or 0x3040 <= o <= 0x30FF: cjk += 1
    return alpha, latin, cjk


def _segment_script_coherent(text: str, *, src_lang: str | None) -> bool:
    """L2: reject obvious script flaps (Arabic-as-Latin junk, CJK on en/ar sources). Fail-open when unknown."""
    base = _lang_base(src_lang)
    alpha, latin, cjk = _script_counts(text)
    if not alpha: return False
    if base == "ar" and latin / alpha > _SCRIPT_LATIN_ON_AR: return False
    if base in ("en", "ar") and cjk / alpha > _SCRIPT_CJK_ON_EN_AR: return False
    return True


def _trust_tier(seg: dict, *, src_lang: str | None = None) -> str:
    """Compose L1 metadata + L2 script coherence into full / degraded / rejected."""
    text = (seg.get("text") or "").strip()
    if not text:
        return "rejected"
    if not _segment_script_coherent(text, src_lang=src_lang):
        return "rejected"
    if all(k in seg for k in _SEGMENT_REQUIRED_QUALITY_KEYS):
        if not _segment_metadata_pass(seg):
            return "rejected"
        return "full"
    return "degraded"


def _finalize_segments(raw: list[dict], src_lang: str | None) -> list[dict]:
    """Normalize raw whisper segments and stamp trust_tier + trusted on each."""
    out: list[dict] = []
    for s in raw or []:
        seg = _segment(s)
        tier = _trust_tier(seg, src_lang=src_lang)
        seg["trust_tier"] = tier
        seg["trusted"] = tier == "full"
        out.append(seg)
    return out


def segment_trusted(seg: dict, *, src_lang: str | None = None) -> bool:
    """True only for full-trust segments (L1 metadata pass + L2 script coherence).
    Always recomputes from quality keys — a stored trust_tier is a snapshot, not authority
    (a stale rejected stamp from a previous formula must not freeze live lyrics as junk)."""
    return _trust_tier(seg, src_lang=src_lang) == "full"


def trusted_segments(transcript: list[dict] | None, *, src_lang: str | None = None) -> list[dict]:
    """Filter to full-trust segments only; None/[] -> []. Recomputes; ignores stored trust_tier."""
    return [s for s in (transcript or []) if segment_trusted(s, src_lang=src_lang)]


def window_has_trusted_speech(src, start: float, end: float) -> bool:
    """True when trusted transcript segments overlap [start,end) with >= _SPEECH_MIN_WORDS tokens."""
    lang = getattr(src, "language", None)
    words = 0
    for seg in trusted_segments(getattr(src, "transcript", None) or [], src_lang=lang):
        try:
            s, e = seg.get("start"), seg.get("end")
            if not isinstance(s, (int, float)) or not isinstance(e, (int, float)): continue
            if e <= start or s >= end: continue
            words += sum(1 for tok in (seg.get("text") or "").split() if any(c.isalpha() for c in tok))
        except (AttributeError, TypeError):
            continue
    return words >= _SPEECH_MIN_WORDS


def excerpt_for_window(src, start: float, end: float, *, max_chars: int = 240) -> str:
    """Join full-trust segment text overlapping [start,end); truncate to max_chars."""
    lang = getattr(src, "language", None)
    parts: list[str] = []
    for seg in trusted_segments(getattr(src, "transcript", None) or [], src_lang=lang):
        try:
            s, e = seg.get("start"), seg.get("end")
            if not isinstance(s, (int, float)) or not isinstance(e, (int, float)): continue
            if e <= start or s >= end: continue
            text = (seg.get("text") or "").strip()
            if text: parts.append(text)
        except (AttributeError, TypeError):
            continue
    joined = " ".join(parts)
    return joined if len(joined) <= max_chars else joined[:max_chars]


def real_transcript_signal(transcript: list[dict]) -> bool:
    """True iff `transcript` is proof that REAL whisper ran on REAL audio — NOT that any one
    specific word survived (CI-2). E2E-only: do NOT use for subs, hooks, framing, or moment gates —
    use trusted_segments / window_has_trusted_speech / excerpt_for_window instead. Used by the
    real-tooling E2E in place of a brittle single-token check that bet on macOS `say`'s acoustics
    and failed under the Linux CI's espeak vocoder.

    The contract has two parts, both required, so the check is robust across TTS engines yet still
    rejects a fake/empty/stub transcript (the v1 bug this E2E guards against — "false safety is
    worse than honest absence"):
      1. STRUCTURE — at least one segment with whisper's real shape: numeric start/end and
         end > start (a fabricated string with no timing is not whisper output).
      2. SUBSTANCE — the joined text has >= 4 alphabetic word tokens (a one-word stub, which a
         naive `len(text) > 0` would wrongly accept, is rejected).
    A robust *content* anchor (the word "anymore", which survives both `say` and espeak in the
    real run logs) is asserted by the E2E/its unit guard directly against the text, not here, so
    this helper stays vocoder-agnostic.
    """
    import re
    has_real_segment = any(
        isinstance(seg.get("start"), (int, float))
        and isinstance(seg.get("end"), (int, float))
        and seg["end"] > seg["start"]
        for seg in transcript
    )
    if not has_real_segment:
        return False
    joined = " ".join(str(seg.get("text", "")) for seg in transcript)
    words = re.findall(r"[^\W\d_]+", joined)             # alphabetic tokens (Unicode-aware: EN+AR)
    return len(words) >= 4
