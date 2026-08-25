"""Constrained hook writer for trial reels.

A hook is a contiguous attested sentence or real clause — never a permuted bag,
never an invented word, never a cross-sentence whisper crumb or n-gram window.

Desk returns the distinct claims the transcript actually supports. The runner
expands those claims across hook×stack slots (15–20 cuts) when fewer claims exist.
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

_EN_MIN_CLAUSE_WORDS = 4
_EN_MIN_CLAUSE_CONTENT = 3


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


_UTTERANCE_MIN_WORDS = 8


def _fragment_continues(words: list[str]) -> bool:
    if not words:
        return False
    last = words[-1].rstrip("\"')]}")
    return last.endswith(",") or last.endswith(";")


def _stitch_line_texts(lines: list[dict[str, Any]], language: str) -> list[tuple[str, float, int]]:
    """Merge English Whisper crumbs into sentences; keep Arabic lines separate."""
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
        nonlocal buffer
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
        words = text.split()
        if not words:
            continue
        if _ends_sentence(words[-1]):
            flush()
        elif not _fragment_continues(words) and len(words) >= _UTTERANCE_MIN_WORDS:
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
            for word in words:
                tokens.append(_Token(word, start, line_index, unit, sentence_index))
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


def _has_internal_sentence_break(text: str) -> bool:
    trimmed = text.rstrip("\"')]}")
    while trimmed and trimmed[-1] in ".!?…":
        trimmed = trimmed[:-1].rstrip()
    return bool(re.search(r"[.!?…]", trimmed))


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


def _is_complete_sentence(text: str, language: str) -> bool:
    if language != "en":
        return True
    words = text.split()
    if not words:
        return False
    return _ends_sentence(words[-1])


def _is_whisper_crumb(text: str, language: str) -> bool:
    words = [part for part in text.split() if part.strip()]
    if not words:
        return True
    lowered = " ".join(words).lower()
    if lowered in {"so the next", "fails.", "so the next move"}:
        return True
    if words[0].startswith((".", ",", ";", ":")):
        return True
    if _has_internal_sentence_break(text):
        return True
    if language == "en":
        if not _is_complete_sentence(text, language):
            return True
        content = [word for word in words if _normalize_word(word) not in _EN_FUNCTION]
        if len(words) == 1 and _ends_sentence(words[0]) and _letter_count(_normalize_word(words[0])) < 5:
            return True
        if len(words) < _EN_MIN_CLAUSE_WORDS and len(content) < _EN_MIN_CLAUSE_CONTENT:
            return True
    return False


def _validate_claim(text: str, tokens: list[_Token], language: str, vocabulary: set[str]) -> bool:
    if _contains_forbidden_rewrite(text):
        return False
    if _is_weak_card(text, language):
        return False
    if not _words_attested(text, vocabulary):
        return False
    if _is_whisper_crumb(text, language):
        return False
    if language == "en":
        sentence_ids = {token.sentence_index for token in tokens}
        if len(sentence_ids) > 1:
            return False
    line_text = tokens[0].line_text if tokens else ""
    return is_contiguous_attested_span(text, line_text)


def _sentence_token_groups(tokens: list[_Token], language: str) -> list[list[_Token]]:
    if language == "ar":
        by_line: dict[int, list[_Token]] = {}
        for token in tokens:
            by_line.setdefault(token.line_index, []).append(token)
        return [by_line[index] for index in sorted(by_line)]

    by_sentence: dict[int, list[_Token]] = {}
    for token in tokens:
        by_sentence.setdefault(token.sentence_index, []).append(token)
    return [by_sentence[index] for index in sorted(by_sentence)]


def _extract_claims(tokens: list[_Token], language: str, vocabulary: set[str]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    seen: set[str] = set()

    for sentence_tokens in _sentence_token_groups(tokens, language):
        text = _join_tokens(sentence_tokens)
        norm = _normalize_word(text)
        if norm in seen:
            continue
        if not _validate_claim(text, sentence_tokens, language, vocabulary):
            continue
        seen.add(norm)
        claims.append({"text": text, "cite": _cite(sentence_tokens), "tokens": sentence_tokens})

    return claims


def _assign_hooks(claims: list[dict[str, Any]], language: str) -> list[dict[str, Any]]:
    return [
        {"hook": HOOKS[index % len(HOOKS)], "text": claim["text"], "cite": claim["cite"]}
        for index, claim in enumerate(claims)
    ]


def expand_variant_slots(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map attested claim cards onto hook×stack render slots."""
    if not cards:
        return []
    expanded: list[dict[str, Any]] = []
    for index, (hook, stack) in enumerate(VARIANT_SLOTS):
        claim = cards[index % len(cards)]
        expanded.append(
            {
                "hook": hook,
                "stack": stack,
                "text": claim["text"],
                "cite": claim["cite"],
                "claim_index": index % len(cards),
            }
        )
    return expanded


def write(transcript: dict[str, Any]) -> dict[str, Any]:
    """Return attested sentence/clause claims; fail only on empty or credit-only input."""
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
            "claims": [],
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
            "claims": [],
            "ear": "",
        }

    claims = _extract_claims(tokens, language, vocabulary)
    cards = _assign_hooks(claims, language)
    cites = [card["cite"] for card in cards]
    ear = cards[0]["text"] if cards else ""

    if not cards:
        return {
            "mode": "blocked",
            "language": language,
            "source_line": source_line,
            "reason": "no valid contiguous attested claims",
            "cites": [],
            "cards": [],
            "claims": [],
            "ear": "",
        }

    return {
        "mode": "write",
        "language": language,
        "source_line": source_line,
        "reason": f"{len(claims)} attested claim{'s' if len(claims) != 1 else ''}",
        "cites": cites,
        "cards": cards,
        "claims": [{"text": claim["text"], "cite": claim["cite"]} for claim in claims],
        "ear": ear,
    }
