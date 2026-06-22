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
import json
from fanops.models import Platform

# Reach-ranked pools (June 2026 research; counts in the skill). Lower index = higher reach.
_MEGA = ["#hiphop", "#hiphopmusic", "#rap"]                  # ~504M / ~113M / ~113M posts
# M3 (2026-06-22): widened with real high-reach rap tags so personas can draw from DISTINCT flavor
# vocabularies (was: 4 tags, so the 3 leans overlapped and produced near-identical lines). These are
# class-ranked (well-known massive rap hashtags), not live-counted — same disclaimer as the file header.
_RELEVANCE = ["#rapper", "#bars", "#undergroundhiphop", "#newmusic",
              "#lyrics", "#freestyle", "#trap", "#rapmusic"]           # targets the rap feed
_ARABIC = ["#arabicmusic", "#arabtiktok", "#arabicmusiclovers"]        # AR language/region reach
_DISCOVERY = {Platform.tiktok: ["#fyp", "#foryou", "#viral"],
              Platform.instagram: ["#reels", "#foryou", "#viral"]}
_DISCOVERY_DEFAULT = ["#foryou", "#viral"]                   # youtube/other -> platform-neutral

# Canonical reach rank across all pools (mega first), used to order the model's kept tags.
_RANK = {t: i for i, t in enumerate(_MEGA + _RELEVANCE + _ARABIC + ["#fyp", "#reels", "#foryou", "#viral"])}

# The membership set: a tag the model returns survives only if it is one of these.
VETTED = set(_MEGA) | set(_RELEVANCE) | set(_ARABIC) | {t for v in _DISCOVERY.values() for t in v}

# Per-account tag LEAN (persona differentiation). Each pool is an ORDERED preference drawn ONLY from
# VETTED — genre/relevance FLAVOR plus cross-platform #viral; NO #fyp/#reels (those stay platform-correct
# via _composition). When an account declares a lean, its pool floats ahead of the frozen rank for both
# the kept model tags and the backfill, so a tasteful account leads lyrical/craft tags and a bold one
# leads viral. lean=None / unknown -> no pool -> byte-identical to the frozen behavior.
# M3: DISJOINT flavor vocabularies — no shared tag, so each persona produces a visibly different line
# (not the same pool reordered). 3 tags each (leaving a slot for the platform-discovery floor below).
_LEANS = {"tasteful":    ["#lyrics", "#bars", "#newmusic"],            # lyrical / craft
          "underground": ["#freestyle", "#undergroundhiphop", "#trap"],# raw / scene
          "bold":        ["#viral", "#rapmusic", "#hiphop"]}           # mainstream / viral — all subset of VETTED
TAG_LEANS = frozenset(_LEANS)                  # the valid lean names — the write-boundary validates against this

def load_store(cfg) -> list[str] | None:
    """M4: the dynamic reach-ranked tag store (00_control/hashtags.json `{"tags": [...]}`), normalized.
    Absent / corrupt / empty -> None so every caller falls back to the frozen pools (fail-open, like
    tuning.json). Never raises. The store is WRITTEN by fanops_hashtags.refresh_store (own-reach,
    doctor-gated); this is the read side the caption path consumes."""
    p = cfg.hashtags_path
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        tags = d.get("tags") if isinstance(d, dict) else None
        out = [_norm(t) for t in tags if isinstance(t, str)] if isinstance(tags, list) else []
        return [t for t in out if t] or None
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None                                  # corrupt store -> frozen pools, never crash a run

def vetted_menu(store: list[str] | None = None) -> list[str]:
    """The vetted tags as one flat, reach-ordered, de-duplicated list — the MENU the caption prompt
    tells the model to pick from. With a live `store` (M4) it IS the menu; else the frozen pools. The
    code still hard-caps + filters via vet_hashtags, so this is a guide, not the enforcement."""
    if store:
        return list(store)
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
                 max_tags: int = 4, *, store: list[str] | None = None, lean: str | None = None) -> list[str]:
    """Return at most `max_tags` reach-vetted hashtags. Keeps the model's VETTED tags (reach-ordered),
    then backfills the balanced default until full. Drops every non-vetted word, dedupes case/'#'
    variants, hard-caps the count. Deterministic; never empty (the default always fills). With a live
    `store` (M4), the store IS the vetted set + reach order (data-driven); store tags backfill first,
    the frozen composition is the last-resort fill. store=None -> today's frozen behavior, byte-identical.
    `lean` (persona differentiation) floats that account's pool ahead of the frozen rank for BOTH the kept
    tags and the backfill; an Arabic clip keeps a language tag as a floor so the lean can't displace its
    region reach. lean=None / unknown -> empty pool -> byte-identical (no language floor, frozen rank)."""
    vetted = set(store) if store else VETTED
    base_rank = {t: i for i, t in enumerate(store)} if store else _RANK
    pool = _LEANS.get((lean or "").strip().lower(), [])              # unknown/None lean -> [] -> byte-identical
    rank = {**base_rank, **{t: i - len(pool) for i, t in enumerate(pool)}}   # lean tags float to the front
    lang_floor = _ARABIC[:1] if (pool and (language or "").strip().lower().startswith("ar")) else []
    seen: set[str] = set()
    kept: list[str] = []
    for t in (tags or []):                          # honour the model's choices, but ONLY vetted ones
        h = _norm(t)
        if h in vetted and h not in seen:
            seen.add(h); kept.append(h)
    kept.sort(key=lambda h: rank.get(h, 999))       # reach order (lean pool, own-reach store, or frozen rank)
    # Arabic floor under a lean: GUARANTEE one region tag survives the cap even when the model already filled
    # all max_tags slots (a flavor lean must not strip AR reach). Reserve the LAST slot for it (lean tags keep
    # the lead). No lean -> lang_floor empty -> floor None -> skipped -> byte-identical.
    arabic = set(_ARABIC)
    if lang_floor and not any(h in arabic for h in kept[:max_tags]):     # detect against the CAP WINDOW, not `seen` (the model's own AR tag may be in seen but sorted PAST the cap)
        promote = next((h for h in kept if h in arabic), lang_floor[0])  # promote the model's own AR tag, else the floor default
        kept = kept[:max_tags - 1] + [promote]; seen = set(kept)
    # M3: a leaned account keeps one platform DISCOVERY tag (#fyp/#reels/…) — backfill it right after the
    # lean pool so a flavor lean (e.g. tasteful) can't eat all 4 slots and lose its reach. Gated on `pool`
    # (leaned only) -> no-lean backfill is byte-identical. An AR clip's region floor still wins the reserved
    # last slot above, so AR accounts prioritise region reach over discovery (acceptable).
    disc_floor = _DISCOVERY.get(platform, _DISCOVERY_DEFAULT)[:1] if pool else []
    for h in pool + disc_floor + (store or []) + _composition(platform, language):   # lean, discovery floor, store, default
        if len(kept) >= max_tags: break
        if h not in seen:
            seen.add(h); kept.append(h)
    return kept[:max_tags]                           # hard cap
