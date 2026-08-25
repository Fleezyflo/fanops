"""Constrained hook writer for trial reels.

Builds on-screen hooks from contiguous attested transcript spans only.
Words may be dropped from the ends of a line; order is never permuted.
"""

from __future__ import annotations

import re
from typing import Any

HOOKS = ["result_first", "mid_action", "direct_you", "bold_claim", "cold_proof"]

MIN_HOOKS = 15
MAX_HOOKS = 20
MIN_SOURCE_LINES = 3

_CREDIT_RE = re.compile(
    r"(ترجمة|translation|subtitle|subtitles|credits?\b|translated by)",
    re.IGNORECASE,
)
_TASHKEEL_RE = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")
_PUNCT_RE = re.compile(r"[^\w\s\u0600-\u06FF]", re.UNICODE)

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
    __slots__ = ("word", "norm", "start", "line_index", "line_text")

    def __init__(self, word: str, start: float, line_index: int, line_text: str) -> None:
        self.word = word
        self.norm = _normalize_word(word)
        self.start = start
        self.line_index = line_index
        self.line_text = line_text


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


def _collect_tokens(transcript: dict[str, Any]) -> tuple[list[_Token], str]:
    tokens: list[_Token] = []
    language = transcript.get("language")
    source_parts: list[str] = []

    for index, raw in enumerate(_parse_lines(transcript)):
        text = _line_text(raw)
        if not text:
            continue
        source_parts.append(text)
        if language is None:
            language = _detect_language(text)
        start = _line_start(raw)
        for word in text.split():
            if word.strip():
                tokens.append(_Token(word, start, index, text))

    resolved_language = language or "en"
    return tokens, resolved_language if resolved_language in {"ar", "en"} else _detect_language(
        " ".join(source_parts) or ""
    )


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


def _spans_nested(a: _Span, b: _Span) -> bool:
    """True when two spans from the same line overlap by containment."""
    if a.line_index != b.line_index:
        return False
    if a.text == b.text:
        return True
    shorter, longer = (a.text, b.text) if len(a.text.split()) <= len(b.text.split()) else (b.text, a.text)
    if shorter == longer:
        return False
    short_words = shorter.split()
    long_words = longer.split()
    if len(short_words) >= len(long_words):
        return False
    for start in range(len(long_words) - len(short_words) + 1):
        if long_words[start : start + len(short_words)] == short_words:
            return True
    return False


def _span_score(span: _Span, language: str) -> float:
    function_words = _function_words(language)
    words = span.text.split()
    content = [word for word in words if _normalize_word(word) not in function_words]
    score = float(len(content) * 10 + len(words))
    if any(token.norm in _direct_markers(language) for token in span.tokens):
        score += 5.0
    return score


def _dedupe_spans(spans: list[_Span]) -> list[_Span]:
    seen: set[str] = set()
    unique: list[_Span] = []
    for span in spans:
        key = _span_key(span)
        if key in seen:
            continue
        seen.add(key)
        unique.append(span)
    return unique


def _select_hook_inventory(
    spans: list[_Span],
    language: str,
    *,
    min_hooks: int = MIN_HOOKS,
    max_hooks: int = MAX_HOOKS,
    min_source_lines: int = MIN_SOURCE_LINES,
) -> list[_Span]:
    """Pick up to *max_hooks* non-nested distinct spans; fail when under *min_hooks*."""
    candidates = _dedupe_spans(spans)
    by_line: dict[int, list[_Span]] = {}
    for span in candidates:
        by_line.setdefault(span.line_index, []).append(span)

    if len(by_line) < min_source_lines:
        return []

    lengths = sorted({len(span.text.split()) for span in candidates})
    selected: list[_Span] = []
    for length in lengths:
        if length < 3:
            continue
        line_indices = sorted(by_line)
        per_length = {
            line_index: [span for span in by_line[line_index] if len(span.text.split()) == length]
            for line_index in line_indices
        }
        for line_index in line_indices:
            per_length[line_index].sort(key=lambda span: _span_score(span, language), reverse=True)
        max_per_line = max((len(per_length[line_index]) for line_index in line_indices), default=0)
        for round_index in range(max_per_line):
            for line_index in line_indices:
                if len(selected) >= max_hooks:
                    break
                pool = per_length[line_index]
                if round_index >= len(pool):
                    continue
                span = pool[round_index]
                if any(_spans_nested(span, chosen) for chosen in selected):
                    continue
                if span.text in {chosen.text for chosen in selected}:
                    continue
                selected.append(span)

    line_count = len({span.line_index for span in selected})
    if len(selected) < min_hooks or line_count < min_source_lines:
        return []
    return selected[:max_hooks]


def _build_card(hook: str, span: _Span, index: int) -> dict[str, Any]:
    return {
        "hook": hook,
        "index": index,
        "text": span.text,
        "cite": span.cite(),
    }


def write(transcript: dict[str, Any]) -> dict[str, Any]:
    """Write 15–20 distinct contiguous attested hooks when the transcript supports them."""
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

    inventory = _select_hook_inventory(_all_spans(tokens, language, vocabulary), language)
    cards: list[dict[str, Any]] = []
    for index, span in enumerate(inventory):
        if not is_contiguous_attested_span(span.text, span.tokens[0].line_text):
            continue
        cards.append(_build_card(HOOKS[index % len(HOOKS)], span, index))

    cites = [card["cite"] for card in cards]
    ear = cards[0]["text"] if cards else ""

    if len(cards) < MIN_HOOKS:
        return {
            "mode": "blocked",
            "language": language,
            "source_line": source_line,
            "reason": (
                f"unable to produce {MIN_HOOKS} distinct contiguous attested hooks "
                f"(got {len(cards)}; nested single-line spans do not count)"
            ),
            "cites": cites,
            "cards": cards,
            "ear": ear,
        }

    return {
        "mode": "write",
        "language": language,
        "source_line": source_line,
        "reason": f"{len(cards)} distinct contiguous attested spans",
        "cites": cites,
        "cards": cards,
        "ear": ear,
    }
