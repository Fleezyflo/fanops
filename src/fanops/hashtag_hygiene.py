# src/fanops/hashtag_hygiene.py
"""Deterministic STRUCTURAL gates for a hashtag entering a derived corpus.

Only defects a machine can actually decide are refused here — a keysmash (`#fypppppppppp…`, 73 p's,
which shipped live), a tag that is really a sentence, a digits-only tag, anything that does not survive
normalization. These are properties of the STRING, so a refusal is always explainable and testable.

What this module deliberately does NOT do is judge worth. The old editorial denylists (engagement bait,
platform discovery tags) encoded a taste claim — "#love can only pad the line" — that the platform now
answers directly: a tag's worth is its measured `like_count`, and a broad high-reach tag co-occurring
with a persona's niche is exactly the versatility the corpus is supposed to have. Semantic fit ("is
`#taylorswift` right for THIS artist") stays unattempted for the same reason it always was: it is
unbounded guesswork dressed as a rule. Relevance is now enforced upstream instead, by anchoring
discovery in the persona's own description, and the operator's ban list remains the explicit veto."""
from __future__ import annotations
import re

_RUN = re.compile(r"(.)\1{3,}")            # 4+ of the same char in a row: keysmash, never a real tag
_SHAPE = re.compile(r"^#[a-z0-9_]+$")      # post-_norm shape; anything else is malformed
_MAX_LEN = 30                              # 30 chars after '#'; longer is a sentence or a keysmash
_MIN_LEN = 2


def tag_defect(tag: str) -> str | None:
    """The STRUCTURAL defect in `tag`, or None if it is clean enough to be curated. Pure + deterministic
    — the same string always yields the same verdict, so a refusal is explainable and testable. Expects a
    raw tag; normalizes internally (so callers cannot bypass the gate by passing 'FYP' or ' #Love ')."""
    from fanops.hashtags import _norm
    h = _norm(tag) if isinstance(tag, str) else ""
    if not h or h == "#":
        return "empty"
    body = h[1:]
    if len(body) < _MIN_LEN:
        return f"too short (<{_MIN_LEN} chars)"
    # Keysmash is checked BEFORE length: `#fypppppppppp…` is both over-long and a keysmash, and "keysmash" is
    # the more specific, more actionable diagnosis. The reason string is operator-facing (the migration prints
    # it), so the most precise true statement wins.
    if _RUN.search(body):
        return "malformed (4+ repeated characters — keysmash)"
    if len(body) > _MAX_LEN:
        return f"too long (>{_MAX_LEN} chars) — a tag, not a sentence"
    if not _SHAPE.match(h):
        return "malformed (only a-z, 0-9 and _ survive normalization)"
    if body.isdigit():
        return "digits only — cannot describe content"
    return None


def is_curatable(tag: str) -> bool:
    """True when `tag` may enter a derived corpus. Sugar over tag_defect for call sites that only branch."""
    return tag_defect(tag) is None
