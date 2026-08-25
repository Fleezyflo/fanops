"""Desk swarm — run the constrained hook writer across clips and validate output.

Ship only when desk.json carries TARGET_VARIANTS actually different attested
on-screen texts. No stack cycling, no nested windows, no invented words.
"""

from __future__ import annotations

from typing import Any

from lib.desk import HOOKS, TARGET_VARIANTS, VARIANT_SLOTS, is_contiguous_attested_span, write


def _card_is_contiguous(card: dict[str, Any]) -> bool:
    cite = card.get("cite") or {}
    line = cite.get("line") or ""
    text = (card.get("text") or "").strip()
    if not text or not line:
        return False
    return is_contiguous_attested_span(text, line)


def _is_permutation_fake(cards: list[dict[str, Any]]) -> bool:
    """True when all cards share the same word-bag (anagram permuter)."""
    if len(cards) < 2:
        return False
    bags = [frozenset((card.get("text") or "").split()) for card in cards]
    if len(set(bags)) > 1:
        return False
    texts = [(card.get("text") or "") for card in cards]
    return len(set(texts)) > 1


def _hook_ranges_on_line(cards: list[dict[str, Any]], line: str) -> list[tuple[int, int]]:
    words = line.split()
    ranges: list[tuple[int, int]] = []
    for card in cards:
        text = (card.get("text") or "").strip()
        hook_words = text.split()
        for start in range(len(words) - len(hook_words) + 1):
            if words[start : start + len(hook_words)] == hook_words:
                ranges.append((start, start + len(hook_words)))
                break
    return ranges


def _is_nested_window_farm(cards: list[dict[str, Any]]) -> bool:
    """True when multiple different hook texts are nested windows on one sung line."""
    if len(cards) < 2:
        return False
    texts = {(card.get("text") or "").strip() for card in cards}
    texts.discard("")
    if len(texts) < 2:
        return False

    lines = {(card.get("cite") or {}).get("line") or "" for card in cards}
    lines.discard("")
    if len(lines) != 1:
        return False

    line = next(iter(lines))
    ranges = _hook_ranges_on_line(cards, line)
    if len(ranges) < 2:
        return False

    for i, r1 in enumerate(ranges):
        for r2 in ranges[i + 1 :]:
            s1, e1 = r1
            s2, e2 = r2
            if r1 == r2:
                continue
            if (s1 <= s2 and e2 <= e1) or (s2 <= s1 and e1 <= e2):
                return True
    return False


def _is_forbidden_english_crumb(card: dict[str, Any], language: str) -> bool:
    if language != "en":
        return False
    from lib.desk import _EN_FORBIDDEN_SLICES, _MIN_HOOK_WORDS_EN, _normalize_phrase

    text = (card.get("text") or "").strip()
    norm = _normalize_phrase(text)
    if norm in _EN_FORBIDDEN_SLICES:
        return True
    words = text.split()
    return len(words) < _MIN_HOOK_WORDS_EN


def validate_desk_result(result: dict[str, Any]) -> dict[str, Any]:
    """Validate a desk write payload; return structured pass/fail with reasons."""
    cards = list(result.get("cards") or [])
    language = result.get("language") or "en"
    issues: list[str] = []

    if result.get("mode") != "write":
        issues.append(f"desk mode is {result.get('mode')!r}, not write")

    if len(cards) != TARGET_VARIANTS:
        issues.append(f"expected {TARGET_VARIANTS} cards, got {len(cards)}")

    texts = [(card.get("text") or "").strip() for card in cards]
    unique_texts = len({text for text in texts if text})
    if unique_texts != TARGET_VARIANTS:
        issues.append(
            f"expected {TARGET_VARIANTS} distinct on-screen texts, got {unique_texts}"
        )

    if len(set(texts)) != len(texts):
        issues.append("duplicate claim texts")

    expected_hooks = [hook for hook, _stack in VARIANT_SLOTS]
    hooks_seen = [card.get("hook") for card in cards]
    if hooks_seen != expected_hooks:
        issues.append(f"hook order mismatch: {hooks_seen}")

    expected_stacks = [stack for _hook, stack in VARIANT_SLOTS]
    stacks_seen = [card.get("stack") for card in cards]
    if stacks_seen != expected_stacks:
        issues.append(f"stack order mismatch: {stacks_seen}")

    for card in cards:
        if not _card_is_contiguous(card):
            issues.append(f"{card.get('hook')}: not a contiguous attested span")

    if _is_permutation_fake(cards):
        issues.append("cards are anagram permutations of the same word-bag")

    if _is_nested_window_farm(cards):
        issues.append("cards are nested windows on one sung line")

    for card in cards:
        if _is_forbidden_english_crumb(card, language):
            issues.append(f"{card.get('hook')}: leftover whisper slice or crumb hook")

    ok = not issues
    return {
        "ok": ok,
        "language": language,
        "mode": result.get("mode"),
        "card_count": len(cards),
        "claim_count": len(result.get("claims") or cards),
        "unique_texts": unique_texts,
        "issues": issues,
    }


def write_and_validate(transcript: dict[str, Any]) -> dict[str, Any]:
    """Write hooks then validate; returns both payloads."""
    result = write(transcript)
    validation = validate_desk_result(result)
    return {"desk": result, "validation": validation}


def swarm_write(transcripts: list[dict[str, Any]]) -> dict[str, Any]:
    """Run desk across multiple transcripts; aggregate validation stats."""
    results: list[dict[str, Any]] = []
    passed = 0
    for transcript in transcripts:
        payload = write_and_validate(transcript)
        if payload["validation"]["ok"]:
            passed += 1
        results.append(payload)

    total = len(transcripts)
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "all_ok": passed == total and total > 0,
        "results": results,
    }
