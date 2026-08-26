"""Dual-ear Whisper transcription with cross-ear merge.

Primary ear: large-v3. Secondary ear: large-v3-turbo.
Merge goes beyond low-confidence takeover — when the primary ear is
confident-but-wrong (e.g. forced-language Arabic hallucination on English VO),
the secondary ear wins on script/language disagreement.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

WHISPER_BIN = Path(".venv/bin/whisper")
FFMPEG_BIN = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")

PRIMARY_EAR = "large-v3"
SECONDARY_EAR = "large-v3-turbo"

LOW_CONF_LOGPROB = -0.5
HIGH_NO_SPEECH_PROB = 0.6
CONFIDENT_DISAGREE_MARGIN = 0.15

_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# Known Whisper Arabic hallucination boilerplate on non-Arabic audio.
_HALLUCINATION_MARKERS = (
    "ترجمة",
    "نانسي قنقر",
    "subtitles by",
    "subtitle",
)


def _device() -> str:
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def _normalize_text(text: str) -> str:
    collapsed = " ".join(text.split())
    return unicodedata.normalize("NFKC", collapsed).strip().casefold()


def _script_family(text: str) -> str:
    has_ar = bool(_ARABIC_RE.search(text))
    has_lat = bool(_LATIN_RE.search(text))
    if has_ar and has_lat:
        return "mixed"
    if has_ar:
        return "arabic"
    if has_lat:
        return "latin"
    return "other"


def _script_for_language(lang: str) -> str:
    if lang and lang.startswith("ar"):
        return "arabic"
    if lang and lang.startswith("en"):
        return "latin"
    return "other"


def _looks_like_hallucination(text: str, *, ear_lang: str) -> bool:
    if not text:
        return False
    script = _script_family(text)
    expected = _script_for_language(ear_lang)
    if expected == "latin" and script == "arabic":
        return True
    if expected == "arabic" and script == "latin":
        return True
    lowered = text.casefold()
    return any(marker.casefold() in lowered for marker in _HALLUCINATION_MARKERS)


def _segment_low_conf(seg: dict[str, Any]) -> bool:
    logprob = seg.get("avg_logprob")
    no_speech = seg.get("no_speech_prob")
    if logprob is not None and logprob < LOW_CONF_LOGPROB:
        return True
    if no_speech is not None and no_speech > HIGH_NO_SPEECH_PROB:
        return True
    return False


def _pick_better_on_disagree(
    primary_seg: dict[str, Any],
    secondary_seg: dict[str, Any],
    *,
    primary_lang: str,
    secondary_lang: str,
) -> str:
    p_text = str(primary_seg.get("text", "")).strip()
    s_text = str(secondary_seg.get("text", "")).strip()
    if not p_text:
        return s_text
    if not s_text:
        return p_text

    p_script = _script_family(p_text)
    s_script = _script_family(s_text)

    if p_script != s_script:
        if s_script == _script_for_language(secondary_lang):
            return s_text
        if p_script == _script_for_language(primary_lang):
            return p_text

    if _looks_like_hallucination(p_text, ear_lang=primary_lang):
        return s_text
    if _looks_like_hallucination(s_text, ear_lang=secondary_lang):
        return p_text

    p_lp = float(primary_seg.get("avg_logprob", -999))
    s_lp = float(secondary_seg.get("avg_logprob", -999))
    if s_lp > p_lp + CONFIDENT_DISAGREE_MARGIN:
        return s_text
    if p_lp > s_lp + CONFIDENT_DISAGREE_MARGIN:
        return p_text

    if primary_lang != secondary_lang:
        return s_text

    if primary_lang.startswith("ar") and secondary_lang.startswith("ar"):
        return s_text

    return p_text


def _choose_segment(
    primary_seg: dict[str, Any],
    secondary_seg: dict[str, Any] | None,
    *,
    primary_lang: str,
    secondary_lang: str,
) -> tuple[str, bool, bool]:
    p_text = str(primary_seg.get("text", "")).strip()
    if secondary_seg is None:
        return p_text, _segment_low_conf(primary_seg), False

    s_text = str(secondary_seg.get("text", "")).strip()
    dual_agreed = bool(p_text and s_text and _normalize_text(p_text) == _normalize_text(s_text))
    low_conf = _segment_low_conf(primary_seg)

    if dual_agreed:
        return p_text, low_conf, True

    if low_conf:
        if _segment_low_conf(secondary_seg):
            chosen = _pick_better_on_disagree(
                primary_seg,
                secondary_seg,
                primary_lang=primary_lang,
                secondary_lang=secondary_lang,
            )
        else:
            chosen = s_text or p_text
        return chosen, True, False

    if p_text and s_text and _normalize_text(p_text) != _normalize_text(s_text):
        chosen = _pick_better_on_disagree(
            primary_seg,
            secondary_seg,
            primary_lang=primary_lang,
            secondary_lang=secondary_lang,
        )
        return chosen, False, False

    return p_text or s_text, low_conf, False


def _align_segments(
    primary_segments: list[dict[str, Any]],
    secondary_segments: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    if not secondary_segments:
        return [(seg, None) for seg in primary_segments]

    sec_by_start = {round(float(seg.get("start", 0.0)), 2): seg for seg in secondary_segments}
    pairs: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    used_secondary: set[float] = set()

    for p_seg in primary_segments:
        key = round(float(p_seg.get("start", 0.0)), 2)
        s_seg = sec_by_start.get(key)
        if s_seg is not None:
            used_secondary.add(key)
        else:
            p_start = float(p_seg.get("start", 0.0))
            p_end = float(p_seg.get("end", p_start))
            best: dict[str, Any] | None = None
            best_overlap = 0.0
            for s in secondary_segments:
                s_start = float(s.get("start", 0.0))
                s_end = float(s.get("end", s_start))
                overlap = max(0.0, min(p_end, s_end) - max(p_start, s_start))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best = s
            s_seg = best
        pairs.append((p_seg, s_seg))

    primary_starts = {round(float(seg.get("start", 0.0)), 2) for seg in primary_segments}
    for key, s_seg in sec_by_start.items():
        if key not in used_secondary and key not in primary_starts:
            pairs.append(({"start": s_seg.get("start"), "end": s_seg.get("end"), "text": ""}, s_seg))

    pairs.sort(key=lambda pair: float(pair[0].get("start", pair[1].get("start", 0.0) if pair[1] else 0.0)))
    return pairs


def merge_ears(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    """Merge two ear transcripts. Pure function — testable with fixture JSON."""
    primary_lang = str(primary.get("language", ""))
    secondary_lang = str(secondary.get("language", ""))
    primary_segments = list(primary.get("segments") or [])
    secondary_segments = list(secondary.get("segments") or [])

    merged_segments: list[dict[str, Any]] = []
    for p_seg, s_seg in _align_segments(primary_segments, secondary_segments):
        base = dict(p_seg if p_seg.get("text") else (s_seg or p_seg))
        text, low_conf, dual_agreed = _choose_segment(
            p_seg,
            s_seg,
            primary_lang=primary_lang,
            secondary_lang=secondary_lang,
        )
        merged_segments.append(
            {
                "start": base.get("start"),
                "end": base.get("end"),
                "text": text,
                "avg_logprob": base.get("avg_logprob"),
                "no_speech_prob": base.get("no_speech_prob"),
                "low_conf": low_conf,
                "dual_agreed": dual_agreed,
            }
        )

    merged_text = " ".join(seg["text"] for seg in merged_segments if seg.get("text")).strip()
    return {
        "text": merged_text,
        "segments": merged_segments,
        "language": secondary_lang or primary_lang,
        "ear": f"{PRIMARY_EAR}+{SECONDARY_EAR}",
    }


def _cloud_asr_enabled() -> bool:
    return bool(os.environ.get("TRIAL_REELS_CLOUD_ASR", "").strip())


def _dialect_model_path() -> Path | None:
    raw = os.environ.get("TRIAL_REELS_DIALECT_MODEL", "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def _wipe_whisper_cache(work: Path) -> None:
    cache = work / ".whisper_cache"
    if cache.exists():
        shutil.rmtree(cache)


def _whisper_json_path(wav: Path, work: Path, model: str) -> Path:
    return work / f"{wav.stem}.{model}.json"


def _parse_whisper_json(payload: dict[str, Any], *, ear: str) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    for seg in payload.get("segments") or []:
        segments.append(
            {
                "start": seg.get("start"),
                "end": seg.get("end"),
                "text": str(seg.get("text", "")).strip(),
                "avg_logprob": seg.get("avg_logprob"),
                "no_speech_prob": seg.get("no_speech_prob"),
            }
        )
    return {
        "text": str(payload.get("text", "")).strip(),
        "segments": segments,
        "language": str(payload.get("language", "")),
        "ear": ear,
    }


def _run_whisper(wav: Path, work: Path, model: str) -> dict[str, Any]:
    if not WHISPER_BIN.is_file():
        raise FileNotFoundError(f"Whisper CLI not found at {WHISPER_BIN}")

    out_json = _whisper_json_path(wav, work, model)
    work.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["WHISPER_FFMPEG"] = str(FFMPEG_BIN)

    cmd = [
        str(WHISPER_BIN),
        str(wav),
        "--model",
        model,
        "--device",
        _device(),
        "--output_dir",
        str(work),
        "--output_format",
        "json",
        "--verbose",
        "False",
    ]
    dialect = _dialect_model_path()
    if dialect is not None:
        cmd.extend(["--model_dir", str(dialect.parent)])

    subprocess.run(cmd, check=True, env=env, capture_output=True, text=True)

    default_json = work / f"{wav.stem}.json"
    source_json = out_json if out_json.is_file() else default_json
    if not source_json.is_file():
        raise FileNotFoundError(f"Whisper did not produce JSON for {wav} ({model})")

    payload = json.loads(source_json.read_text(encoding="utf-8"))
    return _parse_whisper_json(payload, ear=model)


def transcribe(wav: Path, work: Path, fresh: bool = False) -> dict[str, Any]:
    """Transcribe *wav* with dual ears and merge.

    Returns ``{text, segments, language, ear}``. Never forces ``--language``.
    Cloud ASR stays closed unless ``TRIAL_REELS_CLOUD_ASR`` is set.
    """
    wav = Path(wav)
    work = Path(work)

    if fresh:
        _wipe_whisper_cache(work)

    if _cloud_asr_enabled():
        raise RuntimeError("TRIAL_REELS_CLOUD_ASR is set but cloud ASR is not implemented in this lane")

    primary = _run_whisper(wav, work, PRIMARY_EAR)
    secondary = _run_whisper(wav, work, SECONDARY_EAR)
    return merge_ears(primary, secondary)
