# src/fanops/hashtag_hygiene.py
"""R4 — deterministic quality gates for a hashtag ENTERING the curated corpus.

The split this module encodes (and the reason it is not just "one more denylist"):

  * STRUCTURAL defects are machine-decidable and are refused here, at the write boundary — malformed
    keysmash (`#fypppppppppp…`, 73 p's, which shipped live), generic engagement bait (`#love`,
    `#instagood`, `#explore`), and platform DISCOVERY tags (`#fyp`, `#reels`) that `vet_hashtags`
    already floors per-platform and which therefore duplicate a lever the corpus does not own.
  * SEMANTIC fit — "is `#taylorswift` right for THIS artist" — is NOT machine-decidable and is
    deliberately NOT attempted. An off-catalogue-tag denylist is unbounded and would be guesswork
    dressed as a rule. That judgement is the operator's, which is precisely why the curated corpus is
    human-governed and why `migrate_corpora` proposes rather than infers.

So: this module makes junk structurally unable to enter a corpus; a human keeps owning the taste.
Every check is pure, deterministic and reason-carrying, so a refusal can always say WHY."""
from __future__ import annotations
import re

# Engagement bait + platform-generic filler. These are reach-chasing tags with no relation to any
# clip's content: they cannot describe a video, so they can only pad the line. Every one of these was
# live in a persona corpus on 2026-07-16 and shipped on a rap/hip-hop catalogue.
_GENERIC_ENGAGEMENT = frozenset({
    "#love", "#instagood", "#instadaily", "#art", "#explore", "#explorepage", "#highlights",
    "#post", "#trending", "#photooftheday", "#picoftheday", "#bestoftheday", "#followme", "#follow",
    "#like4like", "#likeforlike", "#follow4follow", "#l4l", "#f4f", "#tags4likes", "#instalike",
    "#spotify", "#missviralchallenge", "#viralpost", "#viralvideo", "#trend", "#trendingnow",
})

# Platform DISCOVERY tags. Real and useful — but `vet_hashtags` already backfills exactly one per
# platform (`_DISCOVERY` / `disc_floor`), so a copy in the corpus is a duplicate lever that burns a
# curated slot to buy reach the selector grants for free. The floor owns these; the corpus does not.
_DISCOVERY_OWNED = frozenset({"#fyp", "#foryou", "#foryoupage", "#viral", "#reels", "#reel", "#tiktok", "#exploremore"})

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
    if len(body) > _MAX_LEN:
        return f"too long (>{_MAX_LEN} chars) — a tag, not a sentence"
    if not _SHAPE.match(h):
        return "malformed (only a-z, 0-9 and _ survive normalization)"
    if body.isdigit():
        return "digits only — cannot describe content"
    if _RUN.search(body):
        return "malformed (4+ repeated characters — keysmash)"
    if h in _GENERIC_ENGAGEMENT:
        return "generic engagement bait — describes no clip, only pads the line"
    if h in _DISCOVERY_OWNED:
        return "platform discovery tag — vet_hashtags floors one per platform; a corpus copy is a duplicate lever"
    return None


def is_curatable(tag: str) -> bool:
    """True when `tag` may enter a curated corpus. Sugar over tag_defect for call sites that only branch."""
    return tag_defect(tag) is None


def screen_corpus(tags) -> tuple[list[str], dict[str, str]]:
    """Split a raw corpus into (clean, {rejected_tag: reason}) — normalized + first-seen deduped.
    The migration's workhorse: it reports WHY every dropped tag went, so the change is reviewable
    rather than a silent rewrite. Non-str entries are skipped as 'empty'."""
    from fanops.hashtags import _norm
    clean: list[str] = []; rejected: dict[str, str] = {}; seen: set[str] = set()
    for t in (tags or []):
        d = tag_defect(t) if isinstance(t, str) else "empty"
        h = _norm(t) if isinstance(t, str) else ""
        if d:
            rejected[h or repr(t)] = d
            continue
        if h not in seen:
            seen.add(h); clean.append(h)
    return clean, rejected
