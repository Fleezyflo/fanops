"""Constrained hook writer for trial reels.

Enumerates grammatical attested spans at the honest ceiling:
- Arabic: one candidate per Whisper line (nested sub-lines dropped).
- English: one candidate per Whisper line when the transcript carries enough
  standalone lines; otherwise clause-boundary spans inside stitched sentences
  (comma and major marker splits — never sliding n-gram windows).

Ships exactly TARGET_VARIANTS unique on-screen texts or fails closed.
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
_MIN_CONTENT_WORDS_EN = 2
_WEAK_HOOK_STARTERS_EN = frozenset({"the", "a", "an", "in", "of", "to", "and", "or", "so", "by", "at", "for", "with", "from", "on", "moves"})
_WEAK_HOOK_ENDINGS_EN = frozenset({"by", "to", "that", "and", "or", "a", "an", "of", "in", "for", "its", "is", "one", "us", "the"})
_EN_RELATIVE_STARTERS = frozenset({"one", "which", "who", "what", "it's", "its"})
_EN_FRAGMENT_CRUMBS = frozenset(
    {
        "fails.",
        "so the next",
    }
)
# Clause splits inside one English sentence — never cross a sentence boundary.
_EN_SPLIT_MARKERS = (" that ", " by ", " to ", " moves ")


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


def _fragment_continues(text: str) -> bool:
    words = text.split()
    if not words:
        return False
    last = words[-1].rstrip("\"')]}")
    return last.endswith(",") or last.endswith(";")


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
    if first in {"a", "an"} and len(words) >= _MIN_HOOK_WORDS_EN:
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
    return last.endswith(",") or _ends_sentence(last)


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
    """Reject whisper crumbs — hooks are grammatical sentences or clause siblings."""
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
    if not _starts_grammatically(text):
        return True
    if not _ends_grammatically(text):
        return True
    if len(words) < _MIN_HOOK_WORDS_EN:
        if len(words) >= 3 and _ends_sentence(words[-1]):
            return False
        return True
    if _content_word_count(text, language) < _MIN_CONTENT_WORDS_EN:
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


def is_nested_hook_text(shorter: str, longer: str) -> bool:
    """True when *shorter* is a strict contiguous word sub-span of *longer*."""
    a = shorter.strip()
    b = longer.strip()
    if not a or not b or a == b:
        return False
    return is_contiguous_attested_span(a, b)


def _drop_nested_substring_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop strict sub-spans when a longer attested sibling is already kept."""
    if not claims:
        return []
    ordered = sorted(
        enumerate(claims),
        key=lambda item: (-len(str(item[1].get("text") or "").split()), item[0]),
    )
    kept: list[dict[str, Any]] = []
    kept_texts: list[str] = []
    for _index, claim in ordered:
        text = str(claim.get("text") or "").strip()
        if not text:
            continue
        if any(is_nested_hook_text(text, other) for other in kept_texts):
            continue
        kept.append(claim)
        kept_texts.append(text)
    return kept


def _tokens_by_line(tokens: list[_Token]) -> list[list[_Token]]:
    by_index: dict[int, list[_Token]] = {}
    for token in tokens:
        by_index.setdefault(token.line_index, []).append(token)
    return [by_index[idx] for idx in sorted(by_index)]


def _comma_split_indices(words: list[str]) -> list[int]:
    indices = [0]
    for index, word in enumerate(words):
        if word.rstrip().endswith(","):
            indices.append(index + 1)
    indices.append(len(words))
    return sorted(set(indices))


def _marker_split_indices(line_text: str, words: list[str]) -> list[int]:
    indices = set(_comma_split_indices(words))
    lowered = line_text.lower()
    for marker in _EN_SPLIT_MARKERS:
        search_from = 0
        while True:
            pos = lowered.find(marker, search_from)
            if pos < 0:
                break
            prefix_words = len(line_text[:pos].split())
            if 0 < prefix_words < len(words):
                left_len = prefix_words
                right_len = len(words) - prefix_words
                if left_len >= _MIN_HOOK_WORDS_EN and right_len >= _MIN_HOOK_WORDS_EN:
                    indices.add(prefix_words)
            search_from = pos + len(marker)
    return sorted(indices)


def _span_from_tokens(sentence_tokens: list[_Token], start: int, end: int) -> _Span:
    chunk = sentence_tokens[start:end]
    line_text = sentence_tokens[0].line_text
    normalized = [
        _Token(
            token.word,
            token.start,
            token.line_index,
            line_text,
            token.sentence_index,
        )
        for token in chunk
    ]
    return _Span(normalized)


def _ends_at_boundary(text: str, end: int, total_words: int, boundaries: set[int]) -> bool:
    if end >= total_words:
        return True
    if end in boundaries:
        return True
    last = text.split()[-1] if text.split() else ""
    return last.rstrip().endswith(",") or _ends_sentence(last)


def _en_hook_spans_for_sentence(
    sentence_tokens: list[_Token],
    language: str,
    vocabulary: set[str],
) -> list[_Span]:
    """Sibling clause spans when the sentence splits cleanly; else the full sentence."""
    adjacent = [
        span
        for span in _en_clause_spans(sentence_tokens)
        if _validate_hook_unit(span, language, vocabulary)
    ]
    full = _span_from_tokens(sentence_tokens, 0, len(sentence_tokens))
    if _validate_hook_unit(full, language, vocabulary):
        if len(adjacent) >= 2:
            return adjacent
        return [full]
    return adjacent


def _en_clause_spans(sentence_tokens: list[_Token]) -> list[_Span]:
    """Adjacent clause-boundary spans inside one stitched sentence."""
    if not sentence_tokens:
        return []
    words = [token.word for token in sentence_tokens]
    line_text = sentence_tokens[0].line_text
    boundaries = _marker_split_indices(line_text, words)
    boundary_set = set(boundaries)
    spans: list[_Span] = []

    for left in range(len(boundaries) - 1):
        start, end = boundaries[left], boundaries[left + 1]
        if end <= start:
            continue
        if start not in boundary_set:
            continue
        span = _span_from_tokens(sentence_tokens, start, end)
        if not _ends_at_boundary(span.text, end, len(sentence_tokens), boundary_set):
            continue
        spans.append(span)
    return spans


def _sentence_groups(tokens: list[_Token], language: str) -> list[list[_Token]]:
    if language == "ar":
        return _tokens_by_line(tokens)
    by_sentence: dict[int, list[_Token]] = {}
    for token in tokens:
        by_sentence.setdefault(token.sentence_index, []).append(token)
    return [by_sentence[idx] for idx in sorted(by_sentence)]


def _whisper_line_spans(transcript: dict[str, Any], language: str) -> list[_Span]:
    spans: list[_Span] = []
    for index, raw in enumerate(_parse_lines(transcript)):
        text = _line_text(raw)
        if not text:
            continue
        if language == "en" and _is_en_fragment_crumb(text):
            continue
        words = [word for word in text.split() if word.strip()]
        if not words:
            continue
        start = _line_start(raw)
        tokens = [
            _Token(word, start, index, text, 0)
            for word in words
        ]
        spans.append(_Span(tokens))
    return spans


def _hook_units(
    tokens: list[_Token],
    language: str,
    transcript: dict[str, Any],
    vocabulary: set[str],
) -> list[_Span]:
    """Enumerate attested hook spans at the honest grammatical ceiling."""
    if language == "ar":
        return [_Span(line) for line in _tokens_by_line(tokens) if line]

    line_spans = _whisper_line_spans(transcript, language)
    line_units = [
        span
        for span in line_spans
        if not _is_en_fragment_crumb(span.text)
    ]
    if len(line_units) >= TARGET_VARIANTS:
        return line_units

    units: list[_Span] = []
    for sentence_tokens in _sentence_groups(tokens, language):
        if not sentence_tokens:
            continue
        units.extend(_en_hook_spans_for_sentence(sentence_tokens, language, vocabulary))
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


def _extract_claims(
    units: list[_Span],
    language: str,
    vocabulary: set[str],
) -> list[dict[str, Any]]:
    """Distinct attested sentences/lines in source order — no nested windows."""
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
    return _drop_nested_substring_claims(claims)


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

    units = _hook_units(tokens, language, transcript, vocabulary)
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
