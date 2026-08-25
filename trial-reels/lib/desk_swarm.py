"""Desk swarm — validate attested claims before the runner expands them."""

from __future__ import annotations

from typing import Any

from lib.desk import is_contiguous_attested_span, write


def _card_is_contiguous(card: dict[str, Any]) -> bool:
    cite = card.get("cite") or {}
    line = cite.get("line") or ""
    text = (card.get("text") or "").strip()
    if not text or not line:
        return False
    return is_contiguous_attested_span(text, line)


def _is_permutation_fake(cards: list[dict[str, Any]]) -> bool:
    """True when cards share the same word-bag with different order (anagram permuter)."""
    if len(cards) < 2:
        return False
    bags = [frozenset((card.get("text") or "").split()) for card in cards]
    if len(set(bags)) > 1:
        return False
    texts = [(card.get("text") or "") for card in cards]
    return len(set(texts)) > 1


def _is_ngram_window_farm(cards: list[dict[str, Any]], language: str) -> bool:
    """True when every claim is a short sliding window on one source line."""
    if len(cards) < 2 or language != "en":
        return False
    lines = {(card.get("cite") or {}).get("line") or "" for card in cards}
    lines.discard("")
    if len(lines) != 1:
        return False
    line = next(iter(lines))
    words = line.split()
    if all(len((card.get("text") or "").split()) <= 3 for card in cards):
        return True
    ranges: list[tuple[int, int]] = []
    for card in cards:
        hook_words = (card.get("text") or "").split()
        for start in range(len(words) - len(hook_words) + 1):
            if words[start : start + len(hook_words)] == hook_words:
                ranges.append((start, start + len(hook_words)))
                break
        else:
            return False
    for index, first in enumerate(ranges):
        for second in ranges[index + 1 :]:
            s1, e1 = first
            s2, e2 = second
            if (s1 <= s2 and e2 <= e1) or (s2 <= s1 and e1 <= e2):
                return True
    return False


def validate_desk_result(result: dict[str, Any]) -> dict[str, Any]:
    """Validate a desk write payload; return structured pass/fail with reasons."""
    cards = list(result.get("cards") or [])
    language = result.get("language") or "en"
    issues: list[str] = []

    if result.get("mode") != "write":
        issues.append(f"desk mode is {result.get('mode')!r}, not write")

    if not cards:
        issues.append("no attested claim cards")

    texts = [(card.get("text") or "").strip() for card in cards]
    if len(set(texts)) != len(texts):
        issues.append("duplicate claim texts")

    for card in cards:
        if not _card_is_contiguous(card):
            issues.append(f"{card.get('hook')}: not a contiguous attested span")

    if _is_permutation_fake(cards):
        issues.append("cards are anagram permutations of the same word-bag")

    if _is_ngram_window_farm(cards, language):
        issues.append("cards are n-gram windows on one line")

    for card in cards:
        text = (card.get("text") or "").strip()
        if language == "en" and _is_whisper_crumb_card(text):
            issues.append(f"{card.get('hook')}: leftover whisper slice")

    ok = not issues
    return {
        "ok": ok,
        "language": language,
        "mode": result.get("mode"),
        "card_count": len(cards),
        "claim_count": len(result.get("claims") or []),
        "issues": issues,
    }


def _is_whisper_crumb_card(text: str) -> bool:
    lowered = text.lower()
    crumbs = {
        "fails.",
        "so the next",
        "fails. so the",
        "fails. so the next",
        "required. which brings",
        "behind-the-scenes power. ross",
        "inside a padded",
    }
    return lowered in crumbs or (
        len(text.split()) <= 3 and ("." in text[:-1] if text else False)
    )


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
