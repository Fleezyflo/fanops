"""Desk swarm — run the constrained hook writer across clips and validate output.

Rejects permutation fakes and nested single-line span fakes. Requires 15–20 distinct
attested hook texts before a clip is marked shippable.
"""

from __future__ import annotations

from typing import Any

from lib.desk import (
    MAX_HOOKS,
    MIN_HOOKS,
    MIN_SOURCE_LINES,
    is_contiguous_attested_span,
    write,
)


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


def _source_line_count(cards: list[dict[str, Any]]) -> int:
    lines: set[str] = set()
    for card in cards:
        cite = card.get("cite") or {}
        line = cite.get("line") or ""
        if line:
            lines.add(line)
    return len(lines)


def validate_desk_result(result: dict[str, Any]) -> dict[str, Any]:
    """Validate a desk write payload; return structured pass/fail with reasons."""
    cards = list(result.get("cards") or [])
    language = result.get("language") or "en"
    issues: list[str] = []

    if result.get("mode") != "write":
        issues.append(f"desk mode is {result.get('mode')!r}, not write")

    if not (MIN_HOOKS <= len(cards) <= MAX_HOOKS):
        issues.append(f"expected {MIN_HOOKS}–{MAX_HOOKS} cards, got {len(cards)}")

    texts = [(card.get("text") or "").strip() for card in cards]
    if len(set(texts)) != len(texts):
        issues.append("duplicate hook texts")

    if _source_line_count(cards) < MIN_SOURCE_LINES:
        issues.append(f"hooks must span at least {MIN_SOURCE_LINES} source lines")

    for card in cards:
        if not _card_is_contiguous(card):
            issues.append(f"{card.get('hook')}: not a contiguous attested span")

    if _is_permutation_fake(cards):
        issues.append("cards are anagram permutations of the same word-bag")

    for card in cards:
        text = (card.get("text") or "").lower()
        if language == "en" and text in {"fails.", "so the next", "fails. so the next"}:
            issues.append(f"{card.get('hook')}: leftover whisper slice")

    ok = not issues
    return {
        "ok": ok,
        "language": language,
        "mode": result.get("mode"),
        "card_count": len(cards),
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
