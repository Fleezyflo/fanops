# src/fanops/persona_research.py
"""Persona niche terms. Caption hashtags are the source lock (`ship_from_lock`), not a derived corpus."""
from __future__ import annotations
from fanops.hashtag_hygiene import is_curatable


def _seed_token(raw) -> str | None:
    """Normalize one candidate to a tag body, or None if empty / not structurally curatable."""
    if not isinstance(raw, str):
        return None
    t = raw.strip().lstrip("#").lower().replace("-", "").replace(" ", "")
    if not t or not is_curatable("#" + t):
        return None
    return t


def niche_terms(per) -> list[str]:
    """Operator-declared niche bodies (Layer B unconditional seats + relatedness). Order preserved."""
    out: list[str] = []; seen: set[str] = set()
    for n in getattr(per, "niche", None) or []:
        t = _seed_token(n)
        if t and t not in seen:
            seen.add(t); out.append(t)
    return out


def persona_terms(per, cfg=None) -> list[str]:
    """Layer A search roots: the operator's declared `niche`, and nothing else (MOL-719).

    Durable LLM vocab used to widen this set (MOL-644). It no longer does. Measured on the live control
    files 2026-07-30: of 72 generated terms across 3 posting personas, 46 did not exist on Instagram at
    all (`throwndrinkonstage`, `crowdrushesstage`, …) and 13 echoed the persona's own niche back, while
    106 of 107 corpus admissions already attributed to a niche root. A generated root cannot be validated
    before it is searched, so an off-territory one launders pollution into every corpus downstream.

    Discovery does not depend on the root list being wide: measuring a root enqueues its novel co-tags
    (`fanops_hashtags._refresh_pass`), so Instagram supplies the vocabulary while niche sets the
    direction. `cfg` is retained for call-site compatibility and is deliberately unread.

    Voice / content_focus / hook_angle / intensity stay on the persona for caption+hook directives —
    they are NOT Instagram search roots (MOL-637)."""
    return niche_terms(per)


def relatedness_terms(per, cfg=None) -> list[str]:
    """Declared niche bodies. Same set as `persona_terms`."""
    return niche_terms(per)
