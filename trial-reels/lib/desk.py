"""Desk — maps attested hook treatments onto the hook×stack render grid.

Treatments are enumerated in lib.treatments (stitched full sentences for English,
full whisper lines for Arabic — no clause crumbs). Ships exactly TARGET_VARIANTS
distinct on-screen texts or fails closed.
"""

from __future__ import annotations

from typing import Any

from lib.stacks import STACK_NAMES
from lib.treatments import (
    MAX_TREATMENTS,
    TREATMENT_KINDS,
    collect_tokens,
    enumerate_treatments,
    is_contiguous_attested_span,
    is_nested_hook_text,
)

HOOKS = ["result_first", "mid_action", "direct_you", "bold_claim", "cold_proof"]
TARGET_VARIANTS = len(HOOKS) * len(STACK_NAMES)
VARIANT_SLOTS: list[tuple[str, str]] = [
    (hook, stack) for hook in HOOKS for stack in STACK_NAMES
]

__all__ = [
    "HOOKS",
    "TARGET_VARIANTS",
    "VARIANT_SLOTS",
    "collect_tokens",
    "contract_met",
    "expand_variant_slots",
    "is_contiguous_attested_span",
    "is_nested_hook_text",
    "write",
    "_collect_tokens",
    "_tokens_by_line",
    "_EN_FORBIDDEN_SLICES",
    "_MIN_HOOK_WORDS_EN",
]

# Re-exported for desk_swarm and tests.
_EN_FORBIDDEN_SLICES = frozenset({"so the next", "fails.", "fails. so the next"})
_MIN_HOOK_WORDS_EN = 4


def _tokens_by_line(tokens: list) -> list[list]:
    from lib.treatments import _Token  # noqa: PLC0415

    by_index: dict[int, list[_Token]] = {}
    for token in tokens:
        by_index.setdefault(token.line_index, []).append(token)
    return [by_index[idx] for idx in sorted(by_index)]


def _collect_tokens(transcript: dict[str, Any]):
    return collect_tokens(transcript)


def contract_met(desk: dict[str, Any]) -> bool:
    """True when desk shipped TARGET_VARIANTS distinct attested hook texts."""
    if desk.get("mode") != "write":
        return False
    texts = [str(item.get("text") or "").strip() for item in desk.get("cards") or []]
    return len(texts) == TARGET_VARIANTS and len(set(texts)) == TARGET_VARIANTS


def expand_variant_slots(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return hook×stack cards when desk shipped TARGET_VARIANTS unique texts."""
    if not cards:
        return []
    if len(cards) != TARGET_VARIANTS:
        return []
    if len({(card.get("text") or "").strip() for card in cards}) != TARGET_VARIANTS:
        return []
    return list(cards)


def _build_variant_cards(treatments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign the first TARGET_VARIANTS treatments 1:1 onto hook×stack slots."""
    if len(treatments) < TARGET_VARIANTS:
        return []
    cards: list[dict[str, Any]] = []
    for index, ((hook, stack), treatment) in enumerate(zip(VARIANT_SLOTS, treatments, strict=True)):
        cards.append(
            {
                "hook": hook,
                "stack": stack,
                "kind": treatment["kind"],
                "text": treatment["text"],
                "cite": treatment["cite"],
                "treatment_index": index,
            }
        )
    return cards


def write(transcript: dict[str, Any]) -> dict[str, Any]:
    """Enumerate attested hook treatments and map them to render slots."""
    payload = enumerate_treatments(transcript)
    treatments = list(payload.get("treatments") or [])
    cards = _build_variant_cards(treatments)
    cites = [card["cite"] for card in cards]
    claims = [{"text": t["text"], "cite": t["cite"]} for t in treatments[:TARGET_VARIANTS]]

    result = {
        **payload,
        "cards": cards,
        "claims": claims,
        "cites": cites,
        "target_variants": TARGET_VARIANTS,
        "treatment_kinds": list(TREATMENT_KINDS),
        "max_treatments": MAX_TREATMENTS,
        "contract_met": False,
    }
    if payload.get("mode") == "write" and treatments:
        unique = len({item["text"] for item in treatments[:TARGET_VARIANTS]})
        result["unique_texts"] = unique
        result["ceiling"] = unique
        result["contract_met"] = contract_met(result)
    return result
