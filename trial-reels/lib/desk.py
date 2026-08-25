"""Constrained hook writer for trial reels.

Builds on-screen hooks by dropping and reordering attested transcript words only.
Never adds words, never invents facts, and rejects weak phrase-slicer spans.
"""

from __future__ import annotations

import re
from typing import Any

HOOKS = ["result_first", "mid_action", "direct_you", "bold_claim", "cold_proof"]

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

    return not any(_line_has_hookable_content(line_tokens, _detect_language(" ".join(t.word for t in line_tokens))) for line_tokens in by_line.values())



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
    language = _detect_language(tokens[0].word)
    if language == "ar":
        return " ".join(token.word for token in tokens)
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


def _reorder(tokens: list[_Token], order: list[int]) -> list[_Token]:
    picked: list[_Token] = []
    seen: set[int] = set()
    for idx in order:
        if 0 <= idx < len(tokens) and idx not in seen:
            picked.append(tokens[idx])
            seen.add(idx)
    return picked


def _window(content: list[_Token], start: int, size: int) -> list[_Token]:
    if not content:
        return []
    if len(content) <= size:
        return list(content)
    start = max(0, min(start, len(content) - size))
    return content[start : start + size]


def _prepare_pool(tokens: list[_Token], language: str) -> list[_Token]:
    content = _content_tokens(tokens, language)
    if len(content) >= 3:
        return content
    return list(tokens)


def _subset_for_hook(hook: str, pool: list[_Token], language: str) -> list[_Token] | None:
    size = min(7, len(pool))
    if size == 0:
        return None

    if hook == "result_first":
        chunk = _window(pool, max(0, len(pool) - size), size)
        if len(chunk) <= 1:
            return chunk
        return _reorder(chunk, [len(chunk) - 1, *range(len(chunk) - 1)])

    if hook == "mid_action":
        chunk = _window(pool, max(0, (len(pool) - size) // 2), size)
        if len(chunk) <= 2:
            return list(reversed(chunk))
        mid = len(chunk) // 2
        order = [mid]
        for offset in range(1, len(chunk)):
            left = mid - offset
            right = mid + offset
            if left >= 0:
                order.append(left)
            if right < len(chunk):
                order.append(right)
        return _reorder(chunk, order)

    if hook == "direct_you":
        markers = _direct_markers(language)
        hits = [idx for idx, token in enumerate(pool) if token.norm in markers]
        if not hits:
            return None
        anchor = hits[0]
        chunk = _window(pool, max(0, anchor - 1), size)
        anchor_idx = next((idx for idx, token in enumerate(chunk) if token.norm in markers), 0)
        rest = [idx for idx in range(len(chunk)) if idx != anchor_idx]
        return _reorder(chunk, [anchor_idx, *rest])

    if hook == "bold_claim":
        chunk = _window(pool, 0, size)
        ranked = sorted(
            range(len(chunk)),
            key=lambda idx: (-_letter_count(chunk[idx].norm), idx),
        )
        return _reorder(chunk, ranked)

    if hook == "cold_proof":
        chunk = _window(pool, 0, size)
        return list(reversed(chunk))

    raise ValueError(f"unknown hook: {hook}")


def _compose(hook: str, tokens: list[_Token], language: str) -> list[_Token] | None:
    pool = _prepare_pool(tokens, language)
    return _subset_for_hook(hook, pool, language)


def _validate_card(text: str, language: str, vocabulary: set[str]) -> bool:
    if _contains_forbidden_rewrite(text):
        return False
    if _is_weak_card(text, language):
        return False
    if not _words_attested(text, vocabulary):
        return False
    return True


def _build_card(hook: str, picked: list[_Token], language: str, vocabulary: set[str]) -> dict[str, Any] | None:
    text = _join_tokens(picked)
    if not _validate_card(text, language, vocabulary):
        return None
    return {
        "hook": hook,
        "text": text,
        "cite": _cite(picked),
    }


def _fallback_variants(tokens: list[_Token], language: str) -> list[list[_Token]]:
    pool = _prepare_pool(tokens, language)
    variants: list[list[_Token]] = []
    n = len(pool)
    if n == 0:
        return variants
    variants.append(list(pool))
    variants.append(list(reversed(pool)))
    if n >= 3:
        variants.append(_reorder(pool, [n - 1, 0, *range(1, n - 1)]))
        variants.append(_reorder(pool, [1, 2, 0, *range(3, n)]))
        variants.append(_reorder(pool, [n - 2, n - 1, *range(n - 2)]))
        if n >= 4:
            variants.append(pool[1:] + pool[:1])
            variants.append(pool[2:] + pool[:2])
    return variants


def write(transcript: dict[str, Any]) -> dict[str, Any]:
    """Write five constrained hooks from an attested transcript payload."""
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
    cards: list[dict[str, Any]] = []
    seen_texts: set[str] = set()

    for hook in HOOKS:
        picked = _compose(hook, line_tokens, language)
        if picked is None:
            continue
        card = _build_card(hook, picked, language, vocabulary)
        if card is None:
            continue
        norm_text = _normalize_word(card["text"])
        if norm_text in seen_texts:
            continue
        seen_texts.add(norm_text)
        cards.append(card)

    if len(cards) < len(HOOKS):
        for variant in _fallback_variants(line_tokens, language):
            if len(cards) >= len(HOOKS):
                break
            for hook in HOOKS:
                if any(card["hook"] == hook for card in cards):
                    continue
                card = _build_card(hook, variant, language, vocabulary)
                if card is None:
                    continue
                norm_text = _normalize_word(card["text"])
                if norm_text in seen_texts:
                    continue
                seen_texts.add(norm_text)
                cards.append(card)
                break

    cards = sorted(cards, key=lambda card: HOOKS.index(card["hook"]))

    cites = [card["cite"] for card in cards]
    ear = cards[0]["text"] if cards else ""

    if len(cards) < len(HOOKS):
        return {
            "mode": "blocked",
            "language": language,
            "source_line": source_line,
            "reason": "unable to produce five distinct attested hooks",
            "cites": cites,
            "cards": cards,
            "ear": ear,
        }

    return {
        "mode": "write",
        "language": language,
        "source_line": source_line,
        "reason": "five attested reorder hooks",
        "cites": cites,
        "cards": cards,
        "ear": ear,
    }
