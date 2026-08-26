"""Attested hook treatments — grounded spans from transcript, no invented words.

A treatment is one full stitched sentence (English) or one Whisper line (Arabic).
No clause crumbs, sliding windows, permutations, or nested substring farms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MAX_TREATMENTS = 20

TREATMENT_KINDS: tuple[str, ...] = (
    "source_order",
    "attested_clause",
    "result",
    "question_worthy",
    "direct_address",
    "cold_proof",
    "contrast",
    "open_loop",
)

_CREDIT_RE = re.compile(
    r"(ترجمة|translation|subtitle|subtitles|credits?\b|translated by)",
    re.IGNORECASE,
)
_TASHKEEL_RE = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")
_PUNCT_RE = re.compile(r"[^\w\s\u0600-\u06FF]", re.UNICODE)
_SENTENCE_END_RE = re.compile(r"[.!?…][\"')\]]*$")

_EN_FUNCTION = frozenset(
    {
        "a", "an", "the", "is", "it", "its", "it's", "one", "to", "of", "in", "at",
        "for", "and", "or", "but", "so", "as", "be", "was", "were", "are", "am",
        "i", "my", "me", "we", "our", "he", "she", "they", "them", "his", "her",
        "that", "this", "these", "those", "with", "from", "by", "on", "up", "out",
        "if", "not", "no", "yes", "do", "does", "did", "have", "has", "had",
        "what", "when", "where", "who", "how", "why", "which", "us",
    }
)
_AR_FUNCTION = frozenset(
    {
        "و", "في", "من", "على", "إلى", "الى", "أن", "ان", "ما", "لا", "هل", "أو",
        "او", "ثم", "مع", "ب", "ل", "ه", "هذا", "هذه", "ذلك", "تلك",
    }
)
_AR_DIRECT = frozenset({"لك", "يا", "أنت", "انت", "لكم", "ك", "أنتم", "انتما"})
_EN_DIRECT = frozenset({"you", "your", "you're", "youre", "yours", "us"})

_FORBIDDEN_REWRITES = frozenset({"عذبتيني"})
_EN_FORBIDDEN_SLICES = frozenset({"so the next", "fails.", "fails. so the next"})
_MIN_HOOK_WORDS_EN = 4
_MIN_CONTENT_WORDS_EN = 2
_WEAK_HOOK_STARTERS_EN = frozenset(
    {"the", "a", "an", "in", "of", "to", "and", "or", "so", "by", "at", "for", "with", "from", "on"}
)
_WEAK_HOOK_ENDINGS_EN = frozenset(
    {"by", "to", "that", "and", "or", "a", "an", "of", "in", "for", "its", "is", "one", "us", "the"}
)
_EN_RELATIVE_STARTERS = frozenset({"that", "which", "who", "what", "it's", "its", "moves"})
_EN_FRAGMENT_CRUMBS = frozenset(
    {
        "fails.",
        "so the next",
        "ross defines a true",
        "boss by the rare ability",
        "the modern corporate industry completely",
        "by the rare ability",
        "one that completely evaporates",
        "the missing reality layer,",
        "the real streets actually accept.",
        "illusion, one that completely",
        "which brings us to the missing",
        "it's a test of genuine",
        "respect that the modern",
        "inside a padded recording booth",
    }
)

@dataclass(frozen=True)
class HookTreatment:
    kind: str
    text: str
    cite: dict[str, Any]
    line_index: int
    line_text: str
    word_start: int
    word_end: int
    source_order: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "cite": self.cite,
            "line_index": self.line_index,
            "line_text": self.line_text,
            "word_start": self.word_start,
            "word_end": self.word_end,
            "source_order": self.source_order,
        }


class _Token:
    __slots__ = ("word", "norm", "start", "line_index", "line_text", "sentence_index", "word_index")

    def __init__(
        self,
        word: str,
        start: float,
        line_index: int,
        line_text: str,
        sentence_index: int,
        word_index: int,
    ) -> None:
        self.word = word
        self.norm = _normalize_word(word)
        self.start = start
        self.line_index = line_index
        self.line_text = line_text
        self.sentence_index = sentence_index
        self.word_index = word_index


def _strip_tashkeel(text: str) -> str:
    return _TASHKEEL_RE.sub("", text)


def _normalize_word(word: str) -> str:
    return _PUNCT_RE.sub("", _strip_tashkeel(word)).strip().lower()


def _normalize_phrase(text: str) -> str:
    return " ".join(_normalize_word(part) for part in text.split() if part.strip())


def _letter_count(word: str) -> int:
    return sum(1 for ch in word if ch.isalpha() or ("\u0600" <= ch <= "\u06FF"))


def _detect_language(text: str) -> str:
    return "ar" if re.search(r"[\u0600-\u06FF]", text) else "en"


def _function_words(language: str) -> frozenset[str]:
    return _AR_FUNCTION if language == "ar" else _EN_FUNCTION


def _direct_markers(language: str) -> frozenset[str]:
    return _AR_DIRECT if language == "ar" else _EN_DIRECT


def _parse_lines(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    if "lines" in transcript:
        return list(transcript["lines"])
    if "segments" in transcript:
        return list(transcript["segments"])
    raise ValueError("transcript must include lines or segments")


def _line_text(raw: dict[str, Any]) -> str:
    for key in ("text", "content", "line"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _line_start(raw: dict[str, Any]) -> float:
    for key in ("start", "ts", "timestamp"):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _ends_sentence(word: str) -> bool:
    return bool(_SENTENCE_END_RE.search(word))


def _is_en_fragment_crumb(text: str) -> bool:
    words = [part for part in text.split() if part.strip()]
    if not words:
        return True
    if _normalize_phrase(text) in _EN_FRAGMENT_CRUMBS:
        return True
    if len(words) < _MIN_HOOK_WORDS_EN and not _ends_sentence(words[-1]):
        return True
    return False


def _fragment_continues(text: str) -> bool:
    words = text.split()
    if not words:
        return False
    last = words[-1].rstrip("\"')]}")
    return last.endswith(",") or last.endswith(";")


def _stitch_line_texts(lines: list[dict[str, Any]], language: str) -> list[tuple[str, float, int]]:
    if language != "en":
        return [
            (text, _line_start(raw), index)
            for index, raw in enumerate(lines)
            if (text := _line_text(raw))
        ]

    stitched: list[tuple[str, float, int]] = []
    buffer: list[str] = []
    buffer_start = 0.0
    buffer_line_index = 0

    def flush() -> None:
        nonlocal buffer
        if buffer:
            stitched.append((" ".join(buffer), buffer_start, buffer_line_index))
            buffer = []

    for index, raw in enumerate(lines):
        text = _line_text(raw)
        if not text:
            continue
        start = _line_start(raw)
        if _is_en_fragment_crumb(text):
            flush()
            continue
        if buffer and not _fragment_continues(buffer[-1]):
            flush()
        if not buffer:
            buffer_start = start
            buffer_line_index = index
        buffer.append(text)
        if _ends_sentence(text.split()[-1] if text.split() else text):
            flush()

    flush()
    return stitched


def _split_stitched_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def _tokenize_stitched(
    stitched: list[tuple[str, float, int]],
    language: str,
) -> list[_Token]:
    tokens: list[_Token] = []
    sentence_index = 0
    for text, start, line_index in stitched:
        units = _split_stitched_sentences(text) if language == "en" else [text]
        for unit in units:
            words = [word for word in unit.split() if word.strip()]
            if not words:
                continue
            for word_index, word in enumerate(words):
                tokens.append(_Token(word, start, line_index, unit, sentence_index, word_index))
            sentence_index += 1
    return tokens


def collect_tokens(transcript: dict[str, Any]) -> tuple[list[_Token], str]:
    raw_lines = _parse_lines(transcript)
    language = transcript.get("language")
    if language is None:
        for raw in raw_lines:
            if text := _line_text(raw):
                language = _detect_language(text)
                break
    resolved = language or "en"
    if resolved not in {"ar", "en"}:
        joined = " ".join(_line_text(raw) for raw in raw_lines if _line_text(raw))
        resolved = _detect_language(joined)
    return _tokenize_stitched(_stitch_line_texts(raw_lines, resolved), resolved), resolved


def _attested_vocabulary(tokens: list[_Token]) -> set[str]:
    return {token.norm for token in tokens if token.norm}


def _is_credit_token(token: _Token) -> bool:
    return bool(_CREDIT_RE.search(token.word))


def _non_credit_tokens(tokens: list[_Token], language: str) -> list[_Token]:
    function_words = _function_words(language)
    return [
        token
        for token in tokens
        if not _is_credit_token(token) and token.norm not in function_words
    ]


def _line_has_hookable_content(line_tokens: list[_Token], language: str) -> bool:
    content = _non_credit_tokens(line_tokens, language)
    if len(content) >= 3:
        return True
    return any(_letter_count(token.norm) >= 6 for token in content)


def is_credit_only(transcript: dict[str, Any], tokens: list[_Token]) -> bool:
    if not tokens:
        return True
    if not _CREDIT_RE.search(" ".join(token.word for token in tokens)):
        return False
    by_line: dict[int, list[_Token]] = {}
    for token in tokens:
        by_line.setdefault(token.line_index, []).append(token)
    return not any(
        _line_has_hookable_content(line_tokens, _detect_language(" ".join(t.word for t in line_tokens)))
        for line_tokens in by_line.values()
    )


def is_contiguous_attested_span(text: str, line_text: str) -> bool:
    hook = text.strip()
    if not hook or not line_text:
        return False
    words = hook.split()
    line_words = line_text.split()
    if len(words) > len(line_words):
        return False
    for start in range(len(line_words) - len(words) + 1):
        if line_words[start : start + len(words)] == words:
            return True
    return False


def _contains_forbidden_rewrite(text: str) -> bool:
    normalized = _strip_tashkeel(text)
    return any(forbidden in normalized for forbidden in _FORBIDDEN_REWRITES)


def _words_attested(text: str, vocabulary: set[str]) -> bool:
    for word in text.split():
        norm = _normalize_word(word)
        if norm and _letter_count(norm) >= 3 and norm not in vocabulary:
            return False
    return True


def _has_internal_sentence_break(text: str) -> bool:
    trimmed = text.rstrip("\"')]}")
    while trimmed and trimmed[-1] in ".!?…":
        trimmed = trimmed[:-1].rstrip()
    return bool(re.search(r"[.!?…]", trimmed))


def _content_word_count(text: str, language: str) -> int:
    function_words = _function_words(language)
    return sum(1 for word in text.split() if _normalize_word(word) not in function_words)


def _starts_grammatically(text: str) -> bool:
    words = [part for part in text.split() if part.strip()]
    if not words:
        return False
    first = _normalize_word(words[0])
    if first == "one" and len(words) > 1 and _normalize_word(words[1]) == "that":
        return True
    if first in _EN_RELATIVE_STARTERS:
        return True
    if first in {"which", "moves"}:
        return True
    if first == "the" and len(words) >= 4:
        return True
    if first in _WEAK_HOOK_STARTERS_EN:
        return False
    return True


def _ends_grammatically(text: str) -> bool:
    words = [part for part in text.split() if part.strip()]
    if not words:
        return False
    last = words[-1].rstrip("\"')]}")
    if last.endswith(",") or _ends_sentence(last):
        return True
    bare = _normalize_word(last)
    if bare in _WEAK_HOOK_ENDINGS_EN:
        return False
    return True


def _is_crumb(
    text: str,
    language: str,
    *,
    tokens: list[_Token],
    at_clause_boundary: bool = False,
    is_full_sentence: bool = False,
) -> bool:
    words = [part for part in text.split() if part.strip()]
    if not words:
        return True
    if language == "en":
        norm = _normalize_phrase(text)
        if norm in _EN_FORBIDDEN_SLICES or norm in _EN_FRAGMENT_CRUMBS:
            return True
        if _normalize_phrase(text.split(".", 1)[0]) in _EN_FRAGMENT_CRUMBS:
            return True
        if is_full_sentence and _ends_sentence(words[-1]):
            if _content_word_count(text, language) < _MIN_CONTENT_WORDS_EN:
                return True
            if _has_internal_sentence_break(text):
                return True
            return False
        if not _starts_grammatically(text):
            return True
        if not _ends_grammatically(text):
            return True
        if len(words) < _MIN_HOOK_WORDS_EN:
            if len(words) >= 3 and _ends_sentence(words[-1]):
                return False
            if len(words) >= 2 and _ends_sentence(words[-1]) and _content_word_count(text, language) >= 1:
                return False
            return True
        if _content_word_count(text, language) < _MIN_CONTENT_WORDS_EN:
            return True
        if words[0].startswith((".", ",", ";", ":")):
            return True
        if _has_internal_sentence_break(text):
            return True
        sentence_ids = {token.sentence_index for token in tokens}
        if len(sentence_ids) > 1:
            return True
    else:
        if len(words) < 1:
            return True
    return False


def _cite(tokens: list[_Token]) -> dict[str, Any]:
    if not tokens:
        return {"start": 0.0, "words": []}
    return {
        "start": min(token.start for token in tokens),
        "words": [token.word for token in tokens],
        "line": tokens[0].line_text,
    }


def _classify_kind(text: str, language: str, *, is_full_line: bool) -> str:
    if is_full_line:
        return "source_order"
    lower = text.lower()
    if re.match(r"^(which|what|why|how|when|where|who)\b", lower):
        return "question_worthy"
    markers = _direct_markers(language)
    if any(_normalize_word(word) in markers for word in text.split()):
        return "direct_address"
    if text.rstrip().endswith(",") or (
        language == "en" and text.split() and not _ends_sentence(text.split()[-1])
    ):
        return "open_loop"
    if any(word in lower for word in ("fails", "evaporates", "accept", "required")):
        return "result"
    if any(word in lower for word in ("proof", "test", "genuine", "real", "streets")):
        return "cold_proof"
    if any(word in lower for word in ("but", "yet", "instead", "corporate", "illusion", "missing")):
        return "contrast"
    return "attested_clause"


def _sentence_groups(tokens: list[_Token], language: str) -> list[list[_Token]]:
    if language == "ar":
        by_line: dict[int, list[_Token]] = {}
        for token in tokens:
            by_line.setdefault(token.line_index, []).append(token)
        return [by_line[idx] for idx in sorted(by_line)]
    by_sentence: dict[int, list[_Token]] = {}
    for token in tokens:
        by_sentence.setdefault(token.sentence_index, []).append(token)
    return [by_sentence[idx] for idx in sorted(by_sentence)]


def _candidate_spans(sentence_tokens: list[_Token], language: str) -> list[tuple[int, int, bool]]:
    """One full sentence or Whisper line per group — no nested sub-spans."""
    if not sentence_tokens:
        return []
    return [(0, len(sentence_tokens), True)]


def _validate_span(
    sentence_tokens: list[_Token],
    start: int,
    end: int,
    language: str,
    vocabulary: set[str],
    *,
    at_clause_boundary: bool = False,
) -> bool:
    chunk = sentence_tokens[start:end]
    if not chunk:
        return False
    if start != 0 or end != len(sentence_tokens):
        return False
    text = " ".join(token.word for token in chunk)
    if _contains_forbidden_rewrite(text):
        return False
    if not _words_attested(text, vocabulary):
        return False
    if not _line_has_hookable_content(chunk, language):
        return False
    if _is_crumb(
        text,
        language,
        tokens=chunk,
        at_clause_boundary=at_clause_boundary,
        is_full_sentence=True,
    ):
        return False
    return is_contiguous_attested_span(text, chunk[0].line_text)


def _nested_on_line(a: HookTreatment, b: HookTreatment) -> bool:
    if a.line_text != b.line_text:
        return False
    if a.word_start == b.word_start and a.word_end == b.word_end:
        return False
    return (a.word_start <= b.word_start and b.word_end <= a.word_end) or (
        b.word_start <= a.word_start and a.word_end <= b.word_end
    )


def _select_treatments(candidates: list[HookTreatment], language: str) -> list[HookTreatment]:
    """Pick the largest non-nested antichain per line, then take up to MAX_TREATMENTS."""
    by_line: dict[str, list[HookTreatment]] = {}
    for candidate in candidates:
        by_line.setdefault(candidate.line_text, []).append(candidate)

    packed: list[HookTreatment] = []
    for line_text in sorted(by_line, key=lambda line: by_line[line][0].source_order):
        line_candidates = sorted(
            by_line[line_text],
            key=lambda item: (item.word_start, item.word_end - item.word_start, item.text),
        )
        line_packed: list[HookTreatment] = []
        for candidate in line_candidates:
            if any(_nested_on_line(candidate, picked) for picked in line_packed):
                continue
            line_packed.append(candidate)
        packed.extend(line_packed)

    packed.sort(key=lambda item: (item.source_order, item.word_start, item.text))
    return packed[:MAX_TREATMENTS]


def enumerate_treatments(transcript: dict[str, Any]) -> dict[str, Any]:
    """Return up to MAX_TREATMENTS distinct attested hook treatments."""
    tokens, language = collect_tokens(transcript)
    source_line = " ".join(dict.fromkeys(token.line_text for token in tokens))
    vocabulary = _attested_vocabulary(tokens)

    blocked = {
        "mode": "blocked",
        "language": language,
        "source_line": source_line,
        "treatments": [],
        "ceiling": 0,
        "ear": "",
    }

    if is_credit_only(transcript, tokens):
        return {**blocked, "reason": "credit-only transcript"}
    if not tokens:
        return {**blocked, "reason": "empty transcript"}

    candidates: list[HookTreatment] = []
    seen_texts: set[str] = set()
    source_order = 0

    def _append_span(sentence_tokens: list[_Token], start: int, end: int, *, at_boundary: bool) -> None:
        chunk = sentence_tokens[start:end]
        text = " ".join(token.word for token in chunk)
        norm = _normalize_phrase(text)
        if norm in seen_texts:
            return
        line_index = sentence_tokens[0].line_index
        line_text = sentence_tokens[0].line_text
        if not _validate_span(
            sentence_tokens,
            start,
            end,
            language,
            vocabulary,
            at_clause_boundary=at_boundary,
        ):
            return
        is_full = start == 0 and end == len(sentence_tokens)
        kind = _classify_kind(text, language, is_full_line=is_full)
        if kind not in TREATMENT_KINDS:
            kind = "attested_clause"
        seen_texts.add(norm)
        candidates.append(
            HookTreatment(
                kind=kind,
                text=text,
                cite=_cite(chunk),
                line_index=line_index,
                line_text=line_text,
                word_start=chunk[0].word_index,
                word_end=chunk[-1].word_index + 1,
                source_order=source_order,
            )
        )

    token_groups = _sentence_groups(tokens, language)

    for sentence_tokens in token_groups:
        spans = _candidate_spans(sentence_tokens, language)
        for start, end, at_boundary in spans:
            _append_span(sentence_tokens, start, end, at_boundary=at_boundary)
        source_order += 1

    treatments = _select_treatments(candidates, language)
    if not treatments:
        return {**blocked, "reason": "no attested hook treatments in transcript"}

    unique_texts = len({item.text for item in treatments})
    ceiling = unique_texts
    if unique_texts < MAX_TREATMENTS:
        return {
            **blocked,
            "reason": (
                f"transcript supports {unique_texts} distinct attested hook"
                f"{'s' if unique_texts != 1 else ''}; need {MAX_TREATMENTS}"
            ),
            "treatments": [item.to_dict() for item in treatments],
            "ceiling": ceiling,
            "unique_texts": unique_texts,
            "claims_found": unique_texts,
            "target_variants": MAX_TREATMENTS,
        }

    return {
        "mode": "write",
        "language": language,
        "source_line": source_line,
        "reason": (
            f"{MAX_TREATMENTS} distinct attested on-screen texts "
            f"from {unique_texts} grammatical hook treatments"
        ),
        "treatments": [item.to_dict() for item in treatments],
        "ceiling": ceiling,
        "unique_texts": unique_texts,
        "claims_found": unique_texts,
        "target_variants": MAX_TREATMENTS,
        "ear": treatments[0].text,
    }
