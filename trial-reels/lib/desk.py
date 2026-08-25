"""Constrained hook writer for trial reels.

Builds on-screen hooks from contiguous attested transcript spans only.
Words may be dropped from the ends of a line; order is never permuted.
"""

from __future__ import annotations

import re
from typing import Any

from lib.stacks import STACK_NAMES

HOOKS = ["result_first", "mid_action", "direct_you", "bold_claim", "cold_proof"]
TARGET_VARIANTS = len(HOOKS) * len(STACK_NAMES)
VARIANT_SLOTS: list[tuple[str, str]] = [
    (hook, stack) for hook in HOOKS for stack in STACK_NAMES
]

_CREDIT_RE = re.compile(
    r"(ترجمة|translation|subtitle|subtitles|credits?\b|translated by)",
    re.IGNORECASE,
)
_TASHKEEL_RE = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")
_PUNCT_RE = re.compile(r"[^\w\s\u0600-\u06FF]", re.UNICODE)
_SENTENCE_END_RE = re.compile(r"[.!?…][\"')\]]*$")

_EN_FUNCTION = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "it",
        "its",
        "it's",
        "one",
        "to",
        "of",
        "in",
        "at",
        "for",
        "and",
        "or",
        "but",
        "so",
        "as",
        "be",
        "was",
        "were",
        "are",
        "am",
        "i",
        "my",
        "me",
        "we",
        "our",
        "he",
        "she",
        "they",
        "them",
        "his",
        "her",
        "that",
        "this",
        "these",
        "those",
        "with",
        "from",
        "by",
        "on",
        "up",
        "out",
        "if",
        "not",
        "no",
        "yes",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "what",
        "when",
        "where",
        "who",
        "how",
        "why",
    }
)

_AR_FUNCTION = frozenset(
    {
        "و",
        "في",
        "من",
        "على",
        "إلى",
        "الى",
        "أن",
        "ان",
        "ما",
        "لا",
        "هل",
        "أو",
        "او",
        "ثم",
        "مع",
        "ب",
        "ل",
        "ه",
        "هذا",
        "هذه",
        "ذلك",
        "تلك",
    }
)

_AR_DIRECT = frozenset({"لك", "يا", "أنت", "انت", "لكم", "ك", "أنتم", "انتما"})
_EN_DIRECT = frozenset({"you", "your", "you're", "youre", "yours"})

_FORBIDDEN_REWRITES = frozenset({"عذبتيني"})


def _strip_tashkeel(text: str) -> str:
    return _TASHKEEL_RE.sub("", text)


def _normalize_word(word: str) -> str:
    cleaned = _PUNCT_RE.sub("", _strip_tashkeel(word)).strip().lower()
    return cleaned


def _letter_count(word: str) -> int:
    return sum(1 for ch in word if ch.isalpha() or ("\u0600" <= ch <= "\u06FF"))


def _detect_language(text: str) -> str:
    if re.search(r"[\u0600-\u06FF]", text):
        return "ar"
    return "en"


def _function_words(language: str) -> frozenset[str]:
    if language == "ar":
        return _AR_FUNCTION
    return _EN_FUNCTION


def _direct_markers(language: str) -> frozenset[str]:
    if language == "ar":
        return _AR_DIRECT
    return _EN_DIRECT


class _Token:
    __slots__ = ("word", "norm", "start", "line_index", "line_text", "sentence_index")

    def __init__(
        self,
        word: str,
        start: float,
        line_index: int,
        line_text: str,
        sentence_index: int = 0,
    ) -> None:
        self.word = word
        self.norm = _normalize_word(word)
        self.start = start
        self.line_index = line_index
        self.line_text = line_text
        self.sentence_index = sentence_index


class _Span:
    __slots__ = ("tokens", "text", "line_index", "start", "end")

    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.text = " ".join(token.word for token in tokens)
        self.line_index = tokens[0].line_index if tokens else -1
        self.start = 0
        self.end = len(tokens)

    def cite(self) -> dict[str, Any]:
        if not self.tokens:
            return {"start": 0.0, "words": []}
        return {
            "start": min(token.start for token in self.tokens),
            "words": [token.word for token in self.tokens],
            "line": self.tokens[0].line_text,
        }


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


def _stitch_line_texts(lines: list[dict[str, Any]], language: str) -> list[tuple[str, float, int]]:
    """Merge Whisper fragments into sentence-sized chunks in source order.

    English fragments stitch until a sentence boundary. Other languages keep each
    Whisper line separate so span enumeration cannot collapse a verse into one
    mega-line of nested windows.
    """
    if language != "en":
        stitched: list[tuple[str, float, int]] = []
        for index, raw in enumerate(lines):
            text = _line_text(raw)
            if text:
                stitched.append((text, _line_start(raw), index))
        return stitched

    stitched: list[tuple[str, float, int]] = []
    buffer: list[str] = []
    buffer_start = 0.0
    buffer_line_index = 0

    def flush() -> None:
        nonlocal buffer, buffer_start, buffer_line_index
        if not buffer:
            return
        stitched.append((" ".join(buffer), buffer_start, buffer_line_index))
        buffer = []

    for index, raw in enumerate(lines):
        text = _line_text(raw)
        if not text:
            continue
        start = _line_start(raw)
        if not buffer:
            buffer_start = start
            buffer_line_index = index
        buffer.append(text)
        if _ends_sentence(text.split()[-1] if text.split() else text):
            flush()

    flush()
    return stitched


def _tokenize_stitched(
    stitched: list[tuple[str, float, int]],
    language: str,
) -> list[_Token]:
    tokens: list[_Token] = []
    sentence_index = 0
    for text, start, line_index in stitched:
        words = [word for word in text.split() if word.strip()]
        if not words:
            continue
        for word in words:
            tokens.append(_Token(word, start, line_index, text, sentence_index))
        if language == "en" and _ends_sentence(words[-1]):
            sentence_index += 1
    return tokens


def _collect_tokens(transcript: dict[str, Any]) -> tuple[list[_Token], str]:
    raw_lines = _parse_lines(transcript)
    language = transcript.get("language")
    if language is None:
        for raw in raw_lines:
            text = _line_text(raw)
            if text:
                language = _detect_language(text)
                break
    resolved_language = language or "en"
    if resolved_language not in {"ar", "en"}:
        joined = " ".join(_line_text(raw) for raw in raw_lines if _line_text(raw))
        resolved_language = _detect_language(joined)

    stitched = _stitch_line_texts(raw_lines, resolved_language)
    tokens = _tokenize_stitched(stitched, resolved_language)
    return tokens, resolved_language


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


def _is_credit_only(transcript: dict[str, Any], tokens: list[_Token]) -> bool:
    if not tokens:
        return True
    joined = " ".join(token.word for token in tokens)
    if not _CREDIT_RE.search(joined):
        return False

    by_line: dict[int, list[_Token]] = {}
    for token in tokens:
        by_line.setdefault(token.line_index, []).append(token)

    return not any(
        _line_has_hookable_content(line_tokens, _detect_language(" ".join(t.word for t in line_tokens)))
        for line_tokens in by_line.values()
    )


def _content_tokens(tokens: list[_Token], language: str) -> list[_Token]:
    function_words = _function_words(language)
    return [token for token in tokens if token.norm not in function_words]


def _is_weak_card(text: str, language: str) -> bool:
    words = [part for part in text.split() if part.strip()]
    if not words:
        return True
    function_words = _function_words(language)
    content = [word for word in words if _normalize_word(word) not in function_words]
    if not content:
        return True
    if len(words) <= 2 and len(content) <= 1:
        return True
    return False


def _contains_forbidden_rewrite(text: str) -> bool:
    normalized = _strip_tashkeel(text)
    for forbidden in _FORBIDDEN_REWRITES:
        if forbidden in normalized:
            return True
    return False


def _words_attested(text: str, vocabulary: set[str]) -> bool:
    for word in text.split():
        norm = _normalize_word(word)
        if not norm:
            continue
        if _letter_count(norm) >= 3 and norm not in vocabulary:
            return False
    return True


def _is_contiguous_in_line(span: _Span) -> bool:
    """Span tokens must be a contiguous slice of their source line."""
    if not span.tokens:
        return False
    line = span.tokens[0].line_text
    return span.text in line or span.text.replace("  ", " ") in line


def _has_internal_sentence_break(text: str) -> bool:
    trimmed = text.rstrip("\"')]}")
    while trimmed and trimmed[-1] in ".!?…":
        trimmed = trimmed[:-1].rstrip()
    return bool(re.search(r"[.!?…]", trimmed))


def _is_leftover_slice(span: _Span, language: str) -> bool:
    """Reject Whisper crumbs and cross-sentence leftovers."""
    if not span.tokens:
        return True
    if language == "en":
        if _has_internal_sentence_break(span.text):
            return True
        if span.tokens[0].word.startswith((".", ",", ";", ":")):
            return True
        sentence_ids = {token.sentence_index for token in span.tokens}
        if len(sentence_ids) > 1:
            return True
    return False


def is_contiguous_attested_span(text: str, line_text: str) -> bool:
    """Return True when *text* is a contiguous word subsequence of *line_text*."""
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


def _validate_span(span: _Span, language: str, vocabulary: set[str]) -> bool:
    if not _is_contiguous_in_line(span):
        return False
    if _is_leftover_slice(span, language):
        return False
    if _contains_forbidden_rewrite(span.text):
        return False
    if _is_weak_card(span.text, language):
        return False
    if not _words_attested(span.text, vocabulary):
        return False
    return True


def _tokens_by_line(tokens: list[_Token]) -> list[list[_Token]]:
    by_index: dict[int, list[_Token]] = {}
    for token in tokens:
        by_index.setdefault(token.line_index, []).append(token)
    return [by_index[idx] for idx in sorted(by_index)]


def _substantive_lines(tokens: list[_Token], language: str) -> list[list[_Token]]:
    lines = _tokens_by_line(tokens)
    return [line for line in lines if _line_has_hookable_content(line, language)]


def _span_range_in_line(span: _Span, line_tokens: list[_Token]) -> tuple[int, int]:
    start = line_tokens.index(span.tokens[0])
    end = line_tokens.index(span.tokens[-1]) + 1
    return start, end


def _ranges_nested(r1: tuple[int, int], r2: tuple[int, int]) -> bool:
    if r1 == r2:
        return False
    s1, e1 = r1
    s2, e2 = r2
    return (s1 <= s2 and e2 <= e1) or (s2 <= s1 and e1 <= e2)


def _content_word_count(span: _Span, language: str) -> int:
    function_words = _function_words(language)
    return sum(1 for token in span.tokens if token.norm not in function_words)


def _select_variant_spans(
    spans: list[_Span],
    tokens: list[_Token],
    language: str,
    *,
    count: int = TARGET_VARIANTS,
) -> list[_Span]:
    """Pick *count* spans with unique text and no same-line nested windows."""
    by_line = {line[0].line_index: line for line in _tokens_by_line(tokens) if line}
    buckets: dict[int, list[_Span]] = {}
    for span in spans:
        buckets.setdefault(span.tokens[0].line_index, []).append(span)
    for line_index, line_spans in buckets.items():
        line_spans.sort(
            key=lambda span: (
                len(span.tokens),
                -_content_word_count(span, language),
                span.text,
            )
        )

    selected: list[_Span] = []
    ranges_by_line: dict[int, list[tuple[int, int]]] = {}
    seen_texts: set[str] = set()
    line_indices = sorted(buckets)

    while len(selected) < count:
        added = False
        for line_index in line_indices:
            line_tokens = by_line.get(line_index)
            if line_tokens is None:
                continue
            for span in buckets[line_index]:
                text = span.text
                if text in seen_texts:
                    continue
                token_range = _span_range_in_line(span, line_tokens)
                if any(
                    _ranges_nested(token_range, existing)
                    for existing in ranges_by_line.get(line_index, [])
                ):
                    continue
                selected.append(span)
                seen_texts.add(text)
                ranges_by_line.setdefault(line_index, []).append(token_range)
                added = True
                break
            if len(selected) >= count:
                break
        if not added:
            break
    return selected


def _contiguous_spans(line_tokens: list[_Token], language: str, vocabulary: set[str]) -> list[_Span]:
    """Enumerate every contiguous word window on one line that passes validation."""
    spans: list[_Span] = []
    n = len(line_tokens)
    for start in range(n):
        for end in range(start + 1, n + 1):
            span = _Span(line_tokens[start:end])
            if _validate_span(span, language, vocabulary):
                spans.append(span)
    return spans


def _all_spans(tokens: list[_Token], language: str, vocabulary: set[str]) -> list[_Span]:
    spans: list[_Span] = []
    for line in _tokens_by_line(tokens):
        spans.extend(_contiguous_spans(line, language, vocabulary))
    return spans


def _span_key(span: _Span) -> str:
    return _normalize_word(span.text)


def _suffix_span(
    line_tokens: list[_Token],
    size: int,
    language: str,
    vocabulary: set[str],
    *,
    min_length: int = 1,
) -> _Span | None:
    n = len(line_tokens)
    for length in range(min(size, n), min_length - 1, -1):
        span = _Span(line_tokens[n - length : n])
        if _validate_span(span, language, vocabulary):
            return span
    return None


def _prefix_span(
    line_tokens: list[_Token],
    size: int,
    language: str,
    vocabulary: set[str],
    *,
    min_length: int = 1,
) -> _Span | None:
    n = len(line_tokens)
    for length in range(min(size, n), min_length - 1, -1):
        span = _Span(line_tokens[:length])
        if _validate_span(span, language, vocabulary):
            return span
    return None


def _middle_span(line_tokens: list[_Token], size: int, language: str, vocabulary: set[str]) -> _Span | None:
    n = len(line_tokens)
    if n == 0:
        return None
    target = min(size, n)
    center = n // 2
    for offset in range(n):
        for start in (center - offset, center + offset):
            if start < 0 or start + target > n:
                continue
            span = _Span(line_tokens[start : start + target])
            if _validate_span(span, language, vocabulary):
                return span
    return None


def _direct_span(line_tokens: list[_Token], language: str, vocabulary: set[str]) -> _Span | None:
    markers = _direct_markers(language)
    hits = [idx for idx, token in enumerate(line_tokens) if token.norm in markers]
    if not hits:
        return None
    anchor = hits[0]
    n = len(line_tokens)
    for length in range(min(7, n), 0, -1):
        for start in range(max(0, anchor - length + 1), min(anchor, n - length) + 1):
            end = start + length
            if start <= anchor < end:
                span = _Span(line_tokens[start:end])
                if _validate_span(span, language, vocabulary):
                    return span
    return None


def _pick_span_for_hook(
    hook: str,
    substantive: list[list[_Token]],
    language: str,
    vocabulary: set[str],
) -> _Span | None:
    if not substantive:
        return None

    first = substantive[0]
    last = substantive[-1]
    middle = substantive[len(substantive) // 2]

    if hook == "result_first":
        return _suffix_span(last, 7, language, vocabulary)

    if hook == "mid_action":
        return _middle_span(middle, 5, language, vocabulary)

    if hook == "direct_you":
        for line in substantive:
            span = _direct_span(line, language, vocabulary)
            if span is not None:
                return span
        return None

    if hook == "bold_claim":
        return _prefix_span(first, 5, language, vocabulary)

    if hook == "cold_proof":
        return _suffix_span(last, 4, language, vocabulary, min_length=3)

    raise ValueError(f"unknown hook: {hook}")


def _build_card(hook: str, stack: str, span: _Span) -> dict[str, Any]:
    return {
        "hook": hook,
        "stack": stack,
        "text": span.text,
        "cite": span.cite(),
    }


def write(transcript: dict[str, Any]) -> dict[str, Any]:
    """Write twenty constrained hooks (five policies × four stacks) from attested transcript."""
    tokens, language = _collect_tokens(transcript)
    source_line = " ".join(dict.fromkeys(token.line_text for token in tokens))
    vocabulary = _attested_vocabulary(tokens)

    if _is_credit_only(transcript, tokens):
        return {
            "mode": "blocked",
            "language": language,
            "source_line": source_line,
            "reason": "credit-only transcript",
            "cites": [],
            "cards": [],
            "ear": "",
        }

    if not tokens:
        return {
            "mode": "blocked",
            "language": language,
            "source_line": source_line,
            "reason": "empty transcript",
            "cites": [],
            "cards": [],
            "ear": "",
        }

    all_spans = _all_spans(tokens, language, vocabulary)
    selected = _select_variant_spans(all_spans, tokens, language, count=TARGET_VARIANTS)
    cards: list[dict[str, Any]] = []
    for (hook, stack), span in zip(VARIANT_SLOTS, selected):
        if not is_contiguous_attested_span(span.text, span.tokens[0].line_text):
            continue
        cards.append(_build_card(hook, stack, span))

    cites = [card["cite"] for card in cards]
    ear = cards[0]["text"] if cards else ""

    if len(cards) < TARGET_VARIANTS:
        return {
            "mode": "blocked",
            "language": language,
            "source_line": source_line,
            "reason": (
                f"unable to produce {TARGET_VARIANTS} distinct contiguous attested hooks"
            ),
            "cites": cites,
            "cards": cards,
            "ear": ear,
        }

    return {
        "mode": "write",
        "language": language,
        "source_line": source_line,
        "reason": f"{TARGET_VARIANTS} distinct contiguous attested spans",
        "cites": cites,
        "cards": cards,
        "ear": ear,
    }
