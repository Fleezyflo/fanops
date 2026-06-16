# src/fanops/hashtags.py
"""Reach-vetted hashtag selection. The model used to freestyle 5-15 hashtags (random words);
the operator rule is MAX 4, HARD, and every tag must come from a set vetted for real post
volume — never invented. vet_hashtags() is that gate: it normalises the model's tags, keeps
ONLY vetted ones (reach-ordered), backfills a balanced default so a junk/empty answer still
ships strong reach tags, and truncates to 4. The reach data + sources live in the
fanops-hook-hashtag skill (.claude/skills/fanops-hook-hashtag/SKILL.md); these constants are
seeded from it. Re-verify counts before trusting them as current — this is a class ranking,
not a live API."""
from __future__ import annotations
from fanops.models import Platform

# Reach-ranked pools (June 2026 research; counts in the skill). Lower index = higher reach.
_MEGA = ["#hiphop", "#hiphopmusic", "#rap"]                  # ~504M / ~113M / ~113M posts
_RELEVANCE = ["#rapper", "#bars", "#undergroundhiphop", "#newmusic"]   # targets the rap feed
_ARABIC = ["#arabicmusic", "#arabtiktok", "#arabicmusiclovers"]        # AR language/region reach
_DISCOVERY = {Platform.tiktok: ["#fyp", "#foryou", "#viral"],
              Platform.instagram: ["#reels", "#foryou", "#viral"]}
_DISCOVERY_DEFAULT = ["#foryou", "#viral"]                   # youtube/other -> platform-neutral

# Canonical reach rank across all pools (mega first), used to order the model's kept tags.
_RANK = {t: i for i, t in enumerate(_MEGA + _RELEVANCE + _ARABIC + ["#fyp", "#reels", "#foryou", "#viral"])}

# The membership set: a tag the model returns survives only if it is one of these.
VETTED = set(_MEGA) | set(_RELEVANCE) | set(_ARABIC) | {t for v in _DISCOVERY.values() for t in v}

def vetted_menu() -> list[str]:
    """The vetted tags as one flat, reach-ordered, de-duplicated list — the MENU the caption prompt
    tells the model to pick from. The code still hard-caps + filters via vet_hashtags, so this is a
    guide, not the enforcement."""
    seen: set[str] = set(); out: list[str] = []
    for t in _MEGA + _RELEVANCE + _ARABIC + _DISCOVERY[Platform.tiktok] + _DISCOVERY[Platform.instagram]:
        if t not in seen: seen.add(t); out.append(t)
    return out

def _norm(tag: str) -> str:
    """Canonicalise one tag: strip, lowercase, exactly one leading '#', no inner spaces. '' -> ''."""
    if not tag: return ""
    t = tag.strip().lower().lstrip("#").strip()
    return f"#{t}" if t else ""

def _composition(platform: Platform, language: str | None) -> list[str]:
    """The balanced default 4 (genre + relevance + language/region + discovery), reach-ordered.
    A mega tag for reach, a relevance tag for the right feed, an Arabic tag only when the clip is
    Arabic (else a second music-discovery tag), and one platform discovery tag. Backfill draws from
    this in order, so an empty/junk model answer still ships a strong, vetted, on-rule set."""
    disc = _DISCOVERY.get(platform, _DISCOVERY_DEFAULT)
    lang_slot = _ARABIC[:1] if (language or "").strip().lower().startswith("ar") else ["#newmusic"]
    return _MEGA[:1] + _RELEVANCE[:1] + lang_slot + disc[:1] + _MEGA[1:] + _RELEVANCE[1:] + disc[1:]

def vet_hashtags(tags: list[str] | None, platform: Platform, language: str | None = None,
                 max_tags: int = 4) -> list[str]:
    """Return at most `max_tags` reach-vetted hashtags. Keeps the model's VETTED tags (reach-ordered),
    then backfills the balanced default until full. Drops every non-vetted word, dedupes case/'#'
    variants, hard-caps the count. Deterministic; never empty (the default always fills)."""
    seen: set[str] = set()
    kept: list[str] = []
    for t in (tags or []):                          # honour the model's choices, but ONLY vetted ones
        h = _norm(t)
        if h in VETTED and h not in seen:
            seen.add(h); kept.append(h)
    kept.sort(key=lambda h: _RANK.get(h, 999))      # reach order (mega before niche)
    for h in _composition(platform, language):      # backfill a balanced, vetted default
        if len(kept) >= max_tags: break
        if h not in seen:
            seen.add(h); kept.append(h)
    return kept[:max_tags]                           # hard cap
