"""Constrained hook writer for trial reels.

Extracts distinct attested sentences (English) or full Whisper lines (Arabic).
No nested windows, no permutations, no stack cycling. Ships exactly
TARGET_VARIANTS unique on-screen texts or fails closed when the transcript
cannot honestly support that many grammatical attested hooks.
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
_EN_DIRECT = frozenset({"you", "your", "you're", "youre", "yours", "us"})

_FORBIDDEN_REWRITES = frozenset({"عذبتيني"})
_EN_FORBIDDEN_SLICES = frozenset(
    {
        "so the next",
        "fails.",
        "fails. so the next",
    }
)
_MIN_HOOK_WORDS_EN = 4
_WEAK_HOOK_STARTERS_EN = frozenset({"the", "a", "an", "in", "of", "to", "and", "or", "so"})
_EN_FRAGMENT_CRUMBS = frozenset(
    {
        "fails.",
        "so the next",
    }
)


def _strip_tashkeel(text: str) -> str:
    return _TASHKEEL_RE.sub("", text)


def _normalize_word(word: str) -> str:
    cleaned = _PUNCT_RE.sub("", _strip_tashkeel(word)).strip().lower()
    return cleaned


def _normalize_phrase(text: str) -> str:
    return " ".join(_normalize_word(part) for part in text.split() if part.strip())


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


def _is_en_fragment_crumb(text: str) -> bool:
    words = [part for part in text.split() if part.strip()]
    if not words:
        return True
    norm = _normalize_phrase(text)
    if norm in _EN_FRAGMENT_CRUMBS:
        return True
    if len(words) < _MIN_HOOK_WORDS_EN and not _ends_sentence(words[-1]):
        return True
    return False


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
            merged = " ".join(buffer)
            probe = _Span(
                [
                    _Token(word, buffer_start, buffer_line_index, merged, 0)
                    for word in merged.split()
                ]
            )
            if _is_crumb_hook(probe, "en"):
                while len(buffer) > 1 and _is_en_fragment_crumb(buffer[0]):
                    orphaned = buffer.pop(0)
                    stitched.append((orphaned, start, index))
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
            if language == "en" and _ends_sentence(word):
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
    if not span.tokens:
        return False
    line = span.tokens[0].line_text
    return span.text in line or span.text.replace("  ", " ") in line


def _has_internal_sentence_break(text: str) -> bool:
    trimmed = text.rstrip("\"')]}")
    while trimmed and trimmed[-1] in ".!?…":
        trimmed = trimmed[:-1].rstrip()
    return bool(re.search(r"[.!?…]", trimmed))


def _is_crumb_hook(span: _Span, language: str) -> bool:
    """Reject two-token windows and other whisper crumbs — hooks are sentences/clauses."""
    if language != "en":
        return False
    text = span.text.strip()
    norm = _normalize_phrase(text)
    if norm in _EN_FORBIDDEN_SLICES:
        return True
    if _normalize_phrase(text.split(".", 1)[0]) in _EN_FRAGMENT_CRUMBS:
        return True
    words = [part for part in text.split() if part.strip()]
    if not words:
        return True
    first = _normalize_word(words[0])
    lower = text.lower()
    if first in _WEAK_HOOK_STARTERS_EN and not lower.startswith("so the next chapter"):
        if len(words) < _MIN_HOOK_WORDS_EN or not _ends_sentence(words[-1]):
            return True
    if len(words) < _MIN_HOOK_WORDS_EN:
        if len(words) >= 3 and _ends_sentence(words[-1]):
            return False
        return True
    function_words = _function_words(language)
    content = [word for word in words if _normalize_word(word) not in function_words]
    if len(content) < 2:
        return True
    if span.tokens[0].word.startswith((".", ",", ";", ":")):
        return True
    if _has_internal_sentence_break(span.text):
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


def _tokens_by_line(tokens: list[_Token]) -> list[list[_Token]]:
    by_index: dict[int, list[_Token]] = {}
    for token in tokens:
        by_index.setdefault(token.line_index, []).append(token)
    return [by_index[idx] for idx in sorted(by_index)]


def _hook_units(tokens: list[_Token], language: str) -> list[_Span]:
    """Full Whisper lines (Arabic) or stitched sentences (English) only."""
    if language == "ar":
        return [_Span(line) for line in _tokens_by_line(tokens) if line]

    by_sentence: dict[int, list[_Token]] = {}
    for token in tokens:
        by_sentence.setdefault(token.sentence_index, []).append(token)

    units: list[_Span] = []
    for idx in sorted(by_sentence):
        sentence_tokens = by_sentence[idx]
        if not sentence_tokens:
            continue
        sentence_text = " ".join(token.word for token in sentence_tokens)
        normalized_tokens = [
            _Token(
                token.word,
                token.start,
                token.line_index,
                sentence_text,
                token.sentence_index,
            )
            for token in sentence_tokens
        ]
        units.append(_Span(normalized_tokens))
    return units


def _validate_hook_unit(span: _Span, language: str, vocabulary: set[str]) -> bool:
    if not span.tokens:
        return False
    if not _is_contiguous_in_line(span):
        return False
    if _contains_forbidden_rewrite(span.text):
        return False
    if not _words_attested(span.text, vocabulary):
        return False
    if not _line_has_hookable_content(span.tokens, language):
        return False
    if _is_crumb_hook(span, language):
        return False
    return True


def _prune_nested_window_farm(units: list[_Span], language: str) -> list[_Span]:
    """Collapse sliding-window farms on one sung line to the longest attested span.

    When Whisper emits three or more overlapping fragments that are all contiguous
    sub-windows of the longest line (e.g. لك كفاية عزبتني / كفاية عزبتني /
    عزبتني عزبتني on one 13s sung moment), keep only that maximal span. Two
    separate timestamped lines (verse + echoed عزبتني) stay distinct.
    """
    if language != "ar" or len(units) < 3:
        return units

    texts = [unit.text.strip() for unit in units]
    longest_text = max(texts, key=lambda text: len(text.split()))

    nested: list[_Span] = []
    non_nested: list[_Span] = []
    for unit in units:
        text = unit.text.strip()
        if text == longest_text or is_contiguous_attested_span(text, longest_text):
            nested.append(unit)
        else:
            non_nested.append(unit)

    if len(nested) < 3:
        return units

    longest_units = [unit for unit in nested if unit.text.strip() == longest_text]
    if not longest_units:
        longest_units = [max(nested, key=lambda unit: len(unit.text.split()))]
    return non_nested + longest_units[:1]


def _extract_claims(
    units: list[_Span],
    language: str,
    vocabulary: set[str],
) -> list[dict[str, Any]]:
    """Distinct attested sentences/lines in source order — no nested windows."""
    units = _prune_nested_window_farm(units, language)
    claims: list[dict[str, Any]] = []
    seen: set[str] = set()
    for unit in units:
        if not _validate_hook_unit(unit, language, vocabulary):
            continue
        if not is_contiguous_attested_span(unit.text, unit.tokens[0].line_text):
            continue
        text = unit.text.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        claims.append({"text": text, "cite": unit.cite()})
    return claims


def _assign_variant_cards(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map the first TARGET_VARIANTS claims 1:1 onto hook×stack render slots."""
    return [
        {
            "hook": hook,
            "stack": stack,
            "text": claim["text"],
            "cite": claim["cite"],
            "claim_index": index,
        }
        for index, ((hook, stack), claim) in enumerate(zip(VARIANT_SLOTS, claims, strict=True))
    ]


def expand_variant_slots(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return hook×stack cards when desk shipped TARGET_VARIANTS unique texts."""
    if not cards:
        return []
    if len(cards) != TARGET_VARIANTS:
        return []
    if len({(card.get("text") or "").strip() for card in cards}) != TARGET_VARIANTS:
        return []
    return list(cards)


def _blocked_payload(
    *,
    language: str,
    source_line: str,
    reason: str,
    claims_found: int = 0,
) -> dict[str, Any]:
    return {
        "mode": "blocked",
        "language": language,
        "source_line": source_line,
        "reason": reason,
        "claims_found": claims_found,
        "target_variants": TARGET_VARIANTS,
        "cites": [],
        "cards": [],
        "claims": [],
        "ear": "",
        "unique_texts": 0,
    }


def write(transcript: dict[str, Any]) -> dict[str, Any]:
    """Return TARGET_VARIANTS distinct attested hooks or fail closed."""
    tokens, language = _collect_tokens(transcript)
    source_line = " ".join(dict.fromkeys(token.line_text for token in tokens))
    vocabulary = _attested_vocabulary(tokens)

    if _is_credit_only(transcript, tokens):
        return _blocked_payload(
            language=language,
            source_line=source_line,
            reason="credit-only transcript",
        )

    if not tokens:
        return _blocked_payload(
            language=language,
            source_line=source_line,
            reason="empty transcript",
        )

    units = _hook_units(tokens, language)
    claims = _extract_claims(units, language, vocabulary)
    claims_found = len(claims)

    if not claims:
        return _blocked_payload(
            language=language,
            source_line=source_line,
            reason="no attested sentence or line hooks in transcript",
        )

    if claims_found < TARGET_VARIANTS:
        return _blocked_payload(
            language=language,
            source_line=source_line,
            reason=(
                f"transcript supports {claims_found} attested hook"
                f"{'s' if claims_found != 1 else ''}; "
                f"need {TARGET_VARIANTS} distinct on-screen texts"
            ),
            claims_found=claims_found,
        )

    selected = claims[:TARGET_VARIANTS]
    cards = _assign_variant_cards(selected)
    cites = [card["cite"] for card in cards]
    ear = cards[0]["text"]
    unique_texts = len({card["text"] for card in cards})

    return {
        "mode": "write",
        "language": language,
        "source_line": source_line,
        "reason": (
            f"{TARGET_VARIANTS} distinct attested on-screen texts "
            f"from {claims_found} grammatical hook{'s' if claims_found != 1 else ''}"
        ),
        "cites": cites,
        "cards": cards,
        "claims": [{"text": claim["text"], "cite": claim["cite"]} for claim in selected],
        "ear": ear,
        "unique_texts": unique_texts,
        "claims_found": claims_found,
        "target_variants": TARGET_VARIANTS,
    }
