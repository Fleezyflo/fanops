"""Constrained hook writer for trial reels.

Every on-screen hook is a contiguous, source-order span of the attested transcript.
No reordering, no permutations, no invented words.
"""

from __future__ import annotations

import re
from typing import Any, Callable

HOOKS = ["result_first", "mid_action", "direct_you", "bold_claim", "cold_proof"]

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

_MAX_HOOK_WORDS = 7


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
        sentence_index: int,
    ) -> None:
        self.word = word
        self.norm = _normalize_word(word)
        self.start = start
        self.line_index = line_index
        self.line_text = line_text
        self.sentence_index = sentence_index


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
    """Merge Whisper fragments into sentence-sized chunks in source order."""
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
        if language == "en" and _ends_sentence(text.split()[-1] if text.split() else text):
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
        _line_has_hookable_content(
            line_tokens,
            _detect_language(" ".join(t.word for t in line_tokens)),
        )
        for line_tokens in by_line.values()
    )


def _cite(tokens: list[_Token]) -> dict[str, Any]:
    if not tokens:
        return {"start": 0.0, "words": []}
    return {
        "start": min(token.start for token in tokens),
        "words": [token.word for token in tokens],
        "line": tokens[0].line_text,
    }


def _join_tokens(tokens: list[_Token]) -> str:
    if not tokens:
        return ""
    return " ".join(token.word for token in tokens)


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


def _is_source_order_contiguous(span: list[_Token], pool: list[_Token]) -> bool:
    if not span:
        return False
    start = pool.index(span[0])
    end = start + len(span)
    return pool[start:end] == span


def _has_internal_sentence_break(text: str) -> bool:
    trimmed = text.rstrip("\"')]}")
    while trimmed and trimmed[-1] in ".!?…":
        trimmed = trimmed[:-1].rstrip()
    return bool(re.search(r"[.!?…]", trimmed))


def _is_leftover_slice(span: list[_Token], language: str) -> bool:
    """Reject Whisper crumbs and cross-sentence leftovers."""
    if not span:
        return True
    text = _join_tokens(span)
    if language == "en":
        if _has_internal_sentence_break(text):
            return True
        if span[0].word.startswith((".", ",", ";", ":")):
            return True
    return False


def _contiguous_spans(pool: list[_Token], language: str) -> list[list[_Token]]:
    spans: list[list[_Token]] = []
    n = len(pool)
    for start in range(n):
        for size in range(1, min(_MAX_HOOK_WORDS, n - start) + 1):
            span = pool[start : start + size]
            if language == "en":
                sentence_ids = {token.sentence_index for token in span}
                if len(sentence_ids) > 1:
                    continue
            spans.append(span)
    return spans


def _span_content_score(span: list[_Token], language: str) -> int:
    content = _content_tokens(span, language)
    return len(content) * 10 + sum(_letter_count(token.norm) for token in content)


def _validate_span(
    span: list[_Token],
    pool: list[_Token],
    language: str,
    vocabulary: set[str],
) -> bool:
    if not _is_source_order_contiguous(span, pool):
        return False
    text = _join_tokens(span)
    if not _validate_card(text, language, vocabulary):
        return False
    if _is_leftover_slice(span, language):
        return False
    return True


def _validate_card(text: str, language: str, vocabulary: set[str]) -> bool:
    if _contains_forbidden_rewrite(text):
        return False
    if _is_weak_card(text, language):
        return False
    if not _words_attested(text, vocabulary):
        return False
    return True


def _pick_best_line(tokens: list[_Token], language: str) -> list[_Token]:
    by_line: dict[int, list[_Token]] = {}
    for token in tokens:
        by_line.setdefault(token.line_index, []).append(token)

    best: list[_Token] = []
    best_score = -1
    for line_tokens in by_line.values():
        content = _content_tokens(line_tokens, language)
        score = len(content) * 10 + sum(_letter_count(token.norm) for token in content)
        if score > best_score:
            best = line_tokens
            best_score = score
    return best


def _hook_scorer(hook: str, language: str) -> Callable[[list[_Token], list[_Token]], int]:
    markers = _direct_markers(language)

    def score(span: list[_Token], pool: list[_Token]) -> int:
        if not span:
            return -1
        n = len(pool)
        start = pool.index(span[0])
        end = start + len(span)
        content_score = _span_content_score(span, language)

        if hook == "result_first":
            tail_bonus = end == n
            return content_score + (100 if tail_bonus else end * 2)

        if hook == "mid_action":
            center = (n - 1) / 2
            span_center = (start + end - 1) / 2
            distance = abs(span_center - center)
            return content_score + max(0, 50 - int(distance * 10))

        if hook == "direct_you":
            if not any(token.norm in markers for token in span):
                return -1
            marker_bonus = 80
            compact = max(0, 20 - len(span))
            return content_score + marker_bonus + compact

        if hook == "bold_claim":
            # Longest contiguous claim — never shortest-letter leftover permutations.
            head_bonus = max(0, 30 - start * 3)
            return content_score + head_bonus + len(span) * 5

        if hook == "cold_proof":
            latter_bonus = start * 3
            return content_score + latter_bonus + len(span) * 2

        raise ValueError(f"unknown hook: {hook}")

    return score


def _assign_hooks(
    pool: list[_Token],
    language: str,
    vocabulary: set[str],
) -> list[dict[str, Any]]:
    candidates = [
        span
        for span in _contiguous_spans(pool, language)
        if _validate_span(span, pool, language, vocabulary)
    ]
    cards: list[dict[str, Any]] = []
    seen_texts: set[str] = set()
    used_spans: set[tuple[int, int]] = set()

    for hook in HOOKS:
        scorer = _hook_scorer(hook, language)
        ranked = sorted(
            candidates,
            key=lambda span: (
                scorer(span, pool),
                _span_content_score(span, language),
                -pool.index(span[0]),
            ),
            reverse=True,
        )
        for span in ranked:
            start = pool.index(span[0])
            key = (start, len(span))
            text = _join_tokens(span)
            norm_text = _normalize_word(text)
            if key in used_spans or norm_text in seen_texts:
                continue
            if scorer(span, pool) < 0:
                continue
            cards.append(
                {
                    "hook": hook,
                    "text": text,
                    "cite": _cite(span),
                }
            )
            seen_texts.add(norm_text)
            used_spans.add(key)
            break

    return sorted(cards, key=lambda card: HOOKS.index(card["hook"]))


def write(transcript: dict[str, Any]) -> dict[str, Any]:
    """Write attested contiguous hooks from a transcript payload."""
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

    line_tokens = _pick_best_line(tokens, language)
    cards = _assign_hooks(line_tokens, language, vocabulary)
    cites = [card["cite"] for card in cards]
    ear = cards[0]["text"] if cards else ""

    if not cards:
        return {
            "mode": "blocked",
            "language": language,
            "source_line": source_line,
            "reason": "no valid contiguous attested hooks",
            "cites": [],
            "cards": [],
            "ear": "",
        }

    count = len(cards)
    return {
        "mode": "write",
        "language": language,
        "source_line": source_line,
        "reason": f"{count} contiguous attested hook{'s' if count != 1 else ''}",
        "cites": cites,
        "cards": cards,
        "ear": ear,
    }
