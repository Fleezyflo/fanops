"""Desk swarm — validate attested hook treatments across clips."""

from __future__ import annotations

from typing import Any

from lib.desk import TARGET_VARIANTS, VARIANT_SLOTS, _EN_FORBIDDEN_SLICES, _MIN_HOOK_WORDS_EN, is_contiguous_attested_span, write
from lib.treatments import MAX_TREATMENTS, TREATMENT_KINDS


def _card_is_contiguous(card: dict[str, Any]) -> bool:
    cite = card.get("cite") or {}
    line = cite.get("line") or ""
    text = (card.get("text") or "").strip()
    if not text or not line:
        return False
    return is_contiguous_attested_span(text, line)


def _is_permutation_fake(items: list[dict[str, Any]]) -> bool:
    if len(items) < 2:
        return False
    bags = [frozenset((item.get("text") or "").split()) for item in items]
    if len(set(bags)) > 1:
        return False
    texts = [(item.get("text") or "") for item in items]
    return len(set(texts)) > 1


def _hook_ranges_on_line(items: list[dict[str, Any]], line: str) -> list[tuple[int, int]]:
    words = line.split()
    ranges: list[tuple[int, int]] = []
    for item in items:
        text = (item.get("text") or "").strip()
        hook_words = text.split()
        for start in range(len(words) - len(hook_words) + 1):
            if words[start : start + len(hook_words)] == hook_words:
                ranges.append((start, start + len(hook_words)))
                break
    return ranges


def _is_nested_window_farm(items: list[dict[str, Any]]) -> bool:
    if len(items) < 2:
        return False
    texts = {(item.get("text") or "").strip() for item in items}
    texts.discard("")
    if len(texts) < 2:
        return False

    lines = {(item.get("cite") or {}).get("line") or "" for item in items}
    lines.discard("")
    if len(lines) != 1:
        return False

    line = next(iter(lines))
    ranges = _hook_ranges_on_line(items, line)
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


def _is_forbidden_english_crumb(item: dict[str, Any], language: str) -> bool:
    if language != "en":
        return False
    from lib.treatments import _ends_sentence, _normalize_phrase  # noqa: PLC0415

    text = (item.get("text") or "").strip()
    norm = _normalize_phrase(text)
    if norm in _EN_FORBIDDEN_SLICES:
        return True
    words = text.split()
    if len(words) >= _MIN_HOOK_WORDS_EN:
        return False
    if len(words) >= 2 and words and _ends_sentence(words[-1]):
        return False
    return len(words) < _MIN_HOOK_WORDS_EN


def _treatment_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    return list(result.get("treatments") or [])


def validate_desk_result(result: dict[str, Any]) -> dict[str, Any]:
    """Validate a desk write payload; return structured pass/fail with reasons."""
    treatments = _treatment_items(result)
    cards = list(result.get("cards") or [])
    language = result.get("language") or "en"
    issues: list[str] = []

    if result.get("mode") != "write":
        issues.append(f"desk mode is {result.get('mode')!r}, not write")

    if len(treatments) < TARGET_VARIANTS:
        issues.append(f"expected at least {TARGET_VARIANTS} treatments, got {len(treatments)}")

    if len(treatments) > MAX_TREATMENTS:
        issues.append(f"expected at most {MAX_TREATMENTS} treatments, got {len(treatments)}")

    unique_treatments = len({(item.get("text") or "").strip() for item in treatments})
    if unique_treatments != TARGET_VARIANTS:
        issues.append(
            f"expected {TARGET_VARIANTS} distinct on-screen texts, got {unique_treatments}"
        )

    for item in treatments:
        kind = item.get("kind")
        if kind not in TREATMENT_KINDS:
            issues.append(f"unknown treatment kind: {kind!r}")

    for item in treatments:
        if not _card_is_contiguous(item):
            issues.append(f"{item.get('kind')}: not a contiguous attested span")

    if _is_permutation_fake(treatments):
        issues.append("treatments are anagram permutations of the same word-bag")

    if _is_nested_window_farm(treatments):
        issues.append("treatments are nested windows on one sung line")

    for item in treatments:
        if _is_forbidden_english_crumb(item, language):
            issues.append(f"{item.get('kind')}: leftover whisper slice or crumb hook")

    if len(cards) != TARGET_VARIANTS:
        issues.append(f"expected {TARGET_VARIANTS} variant cards, got {len(cards)}")

    card_texts = [(card.get("text") or "").strip() for card in cards]
    unique_cards = len({text for text in card_texts if text})
    if unique_cards != TARGET_VARIANTS:
        issues.append(
            f"expected {TARGET_VARIANTS} distinct card texts, got {unique_cards}"
        )

    if len(set(card_texts)) != len(card_texts):
        issues.append("duplicate card texts")

    expected_hooks = [hook for hook, _stack in VARIANT_SLOTS]
    hooks_seen = [card.get("hook") for card in cards]
    if hooks_seen != expected_hooks:
        issues.append(f"hook order mismatch: {hooks_seen}")

    expected_stacks = [stack for _hook, stack in VARIANT_SLOTS]
    stacks_seen = [card.get("stack") for card in cards]
    if stacks_seen != expected_stacks:
        issues.append(f"stack order mismatch: {stacks_seen}")

    ok = not issues
    return {
        "ok": ok,
        "language": language,
        "mode": result.get("mode"),
        "treatment_count": len(treatments),
        "card_count": len(cards),
        "unique_treatments": unique_treatments,
        "unique_texts": unique_cards,
        "ceiling": result.get("ceiling", unique_treatments),
        "issues": issues,
    }


def write_and_validate(transcript: dict[str, Any]) -> dict[str, Any]:
    result = write(transcript)
    validation = validate_desk_result(result)
    return {"desk": result, "validation": validation}


def swarm_write(transcripts: list[dict[str, Any]]) -> dict[str, Any]:
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
