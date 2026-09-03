# src/fanops/persona_research.py
"""Persona niche terms. Caption hashtags are the source lock (`ship_from_lock`), not a derived corpus."""
from __future__ import annotations
from fanops.hashtags import is_curatable


def _seed_token(raw) -> str | None:
    """Normalize one candidate to a tag body, or None if empty / not structurally curatable."""
    if not isinstance(raw, str):
        return None
    t = raw.strip().lstrip("#").lower().replace("-", "").replace(" ", "")
    if not t or not is_curatable("#" + t):
        return None
    return t


def niche_terms(per) -> list[str]:
    """Operator-declared niche bodies. Order preserved. Not caption-tag membership."""
    out: list[str] = []; seen: set[str] = set()
    for n in getattr(per, "niche", None) or []:
        t = _seed_token(n)
        if t and t not in seen:
            seen.add(t); out.append(t)
    return out


def persona_terms(per, cfg=None) -> list[str]:
    """Declared `niche` bodies. Caption hashtags are the source lock, not these terms.

    `cfg` is retained for call-site compatibility and is deliberately unread.
    Voice / content_focus / hook_angle / intensity stay on the persona for caption+hook directives —
    they are NOT Instagram search roots (MOL-637)."""
    return niche_terms(per)
