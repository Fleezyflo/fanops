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
import json, re
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

# NB (M3, 2026-06-27): the per-account tag LEAN was RETIRED. It was an invisible+duplicate lever — no editor
# control, and it co-owned the hashtag channel with `hashtag_corpus`. Its 3 disjoint flavor pools were folded
# into each persona's curated `hashtag_corpus` (non-lossy: the reach floors now fire on the corpus), so the
# corpus is the SOLE per-account hashtag differentiator. See docs / persona-lever-coherence M3.

def load_store(cfg) -> list[str] | None:
    """The dynamic reach-ranked tag store (00_control/hashtags.json `{"tags": [...]}`), normalized.
    Absent / corrupt / empty -> None so every caller falls back to the frozen pools (fail-open, like
    tuning.json). Never raises. The store is WRITTEN by fanops_hashtags.refresh_store from LIVE Meta Graph
    reach; this is the read side the caption path consumes."""
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

def load_store_reach(cfg) -> dict[str, float]:
    """The per-tag LIVE Graph reach map persisted alongside the store (00_control/hashtags.json `{"reach":
    {tag: score}}`, written by refresh_store). The Studio shows this number next to each curated tag — the
    honest 'why this tag' signal (its measured platform reach), NOT own-post reach. Absent / corrupt / no
    `reach` key -> {} (the number simply doesn't render). Never raises."""
    p = cfg.hashtags_path
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text())
        r = d.get("reach") if isinstance(d, dict) else None
        if not isinstance(r, dict):
            return {}
        return {_norm(k): float(v) for k, v in r.items()
                if isinstance(k, str) and _norm(k) and isinstance(v, (int, float)) and not isinstance(v, bool)}
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return {}

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

def _dedupe_norm(seq) -> list[str]:
    """Normalize + dedupe a tag sequence (corpus / content), preserving first-seen order. Non-str -> skipped."""
    out: list[str] = []; seen: set[str] = set()
    for t in (seq or []):
        n = _norm(t) if isinstance(t, str) else ""
        if n and n not in seen: seen.add(n); out.append(n)
    return out

# Per-clip CONTENT signal: the small stopword set + latin word token used by content_tag_candidates.
# A token is a latin word, 3-20 chars, starting with a letter (so '12'/'###'/Arabic yield nothing).
_STOPWORDS = frozenset(
    "a an and are as at be but by for from had has have he her his i in is it its me my no not of on or "
    "our out so that the their them they this to too up us was we what when where which who will with you "
    "your yours just got get like dont cant im "
    # URL/tech-adjacent tokens a transcript can surface as a high-frequency word — never a real hashtag,
    # and the content floor would otherwise force one into the posted line (code review MEDIUM).
    "http https www com org net mp3 mp4 wav png jpg jpeg gif url link".split())
_WORD = re.compile(r"[a-z][a-z0-9]{2,19}")

def content_tag_candidates(text: str | None, *, max_n: int = 6) -> list[str]:
    """Per-clip content signal: candidate hashtag tokens derived from THIS clip's transcript text.
    Deterministic + pure (NO NLP model): lowercase, latin word tokens (3-20 chars), drop stopwords,
    order by frequency then first-seen, normalize to '#tag', dedupe, cap at `max_n`. Blank / non-str /
    non-latin (Arabic) / numbers-only -> [] so a contentless/instrumental/Arabic clip stays byte-identical
    to today's tag selection. These are CANDIDATES the model may pick + that survive vetting; never
    invented junk in the posted line (the membership gate + the model's selection still apply)."""
    if not isinstance(text, str) or not text.strip():
        return []
    counts: dict[str, int] = {}; order: list[str] = []
    for tok in _WORD.findall(text.lower()):
        if tok in _STOPWORDS: continue
        if tok not in counts: order.append(tok)
        counts[tok] = counts.get(tok, 0) + 1
    first_idx = {t: i for i, t in enumerate(order)}
    order.sort(key=lambda t: (-counts[t], first_idx[t]))   # frequency desc, then first-seen -> deterministic
    out: list[str] = []; seen: set[str] = set()
    for t in order:
        n = _norm(t)
        if n and n not in seen:
            seen.add(n); out.append(n)
        if len(out) >= max_n: break
    return out

def _composition(platform: Platform, language: str | None) -> list[str]:
    """The balanced default 4 (genre + relevance + language/region + discovery), reach-ordered.
    A mega tag for reach, a relevance tag for the right feed, an Arabic tag only when the clip is
    Arabic (else a second music-discovery tag), and one platform discovery tag. Backfill draws from
    this in order, so an empty/junk model answer still ships a strong, vetted, on-rule set."""
    disc = _DISCOVERY.get(platform, _DISCOVERY_DEFAULT)
    lang_slot = _ARABIC[:1] if (language or "").strip().lower().startswith("ar") else ["#newmusic"]
    return _MEGA[:1] + _RELEVANCE[:1] + lang_slot + disc[:1] + _MEGA[1:] + _RELEVANCE[1:] + disc[1:]

def _screen_content(content_norm: list[str], cfg=None) -> list[str]:
    """MOL-76: drop any content-derived candidate that trips brand_risk_flag — the SAME off-brand guard
    caption.py runs on the model's caption/hook — BEFORE it can join the vetted set, float, or win the
    content FLOOR. content_tag_candidates pulls the top token straight from raw, unscreened ASR transcript
    (routinely explicit on a rap/hip-hop catalogue), and the floor force-inserts it; without this screen a
    lyric slur / off-brand word ships as a live public hashtag, contradicting this module's own 'vetted,
    never invented' invariant. Function-local import: caption.py imports FROM hashtags.py, so a module-level
    import would cycle (moments.py gates the burned hook the same way for the same reason). corpus/store/
    frozen tags are NOT screened (only the raw-transcript floor is the gap); content=[] -> [] (byte-identical)."""
    if not content_norm:
        return content_norm
    from fanops.caption import brand_risk_flag       # function-local: caption imports hashtags -> no module cycle
    return [t for t in content_norm if not brand_risk_flag(t, cfg)]

def vet_hashtags(tags: list[str] | None, platform: Platform, language: str | None = None,
                 max_tags: int = 4, *, store: list[str] | None = None,
                 corpus: list[str] | None = None, content: list[str] | None = None,
                 cfg=None) -> list[str]:
    """Return at most `max_tags` reach-vetted hashtags. Keeps the model's VETTED tags (reach-ordered),
    then backfills the balanced default until full. Drops every non-vetted word, dedupes case/'#'
    variants, hard-caps the count. Deterministic; never empty (the default always fills). With a live
    `store` (M4), the store IS the vetted set + reach order (data-driven); store tags backfill first,
    the frozen composition is the last-resort fill. store=None -> today's frozen behavior, byte-identical.
    `corpus` (B1: the per-persona curated pool — the SOLE per-account hashtag differentiator since the
    tag_lean fold, M3) JOINS the vetted membership (so a curated tag the frozen set / store doesn't know
    SURVIVES) and floats AHEAD of the frozen rank; the corpus order is the curation order. A corpus-led
    account keeps a region tag (Arabic clips) + one platform discovery tag as reach FLOORS so its curated
    pool can't strip reach. corpus=None/empty -> byte-identical (no membership change, no float, no floor).
    `content` (per-clip content-derived tags, content_tag_candidates) ALSO joins the membership so a
    clip-specific tag the model picked SURVIVES, floats just behind the corpus (clip info ahead of reach),
    and RESERVES one slot so the clip's own information always reaches the line when present.
    content=None/empty -> byte-identical (no membership change, no float, no reserved slot)."""
    corpus_norm = _dedupe_norm(corpus)
    content_norm = _screen_content(_dedupe_norm(content), cfg)   # MOL-76: brand-risk screen the raw-transcript floor BEFORE it joins the gate/floats/floors
    vetted = (set(store) if store else set(VETTED)) | set(corpus_norm) | set(content_norm)   # corpus + content join the gate
    base_rank = {t: i for i, t in enumerate(store)} if store else dict(_RANK)
    # Preference float ahead of the frozen rank: corpus (operator curation) > content (clip info).
    preferred: list[str] = []
    for grp in (corpus_norm, content_norm):
        for t in grp:
            if t not in preferred: preferred.append(t)
    rank = {**base_rank, **{t: i - len(preferred) for i, t in enumerate(preferred)}}
    lang_floor = _ARABIC[:1] if (corpus_norm and (language or "").strip().lower().startswith("ar")) else []
    seen: set[str] = set()
    kept: list[str] = []
    for h in corpus_norm:                           # B1: seed the WHOLE curated corpus first (the reach-sort + the final
        if h not in seen: seen.add(h); kept.append(h)   # [:max_tags] truncate cap it) — so a corpus AR tag past the cap
                                                    # stays eligible for the AR-floor promotion below, not dropped early
    # NB: kept may exceed max_tags here (corpus + model picks); the sort + cap below enforce the bound.
    for t in (tags or []):                          # honour the model's choices, but ONLY vetted ones
        h = _norm(t)
        if h in vetted and h not in seen:
            seen.add(h); kept.append(h)
    kept.sort(key=lambda h: rank.get(h, 999))       # reach order (corpus, content, Graph-reach store, or frozen rank)
    # Reserved floors take the TAIL slots so the corpus/reach LEAD is preserved: region reach first
    # (non-negotiable under a corpus — a curated corpus must not strip AR reach), then ONE clip-content tag (the
    # operator's "tags based off information" ask). Each guarantees its signal reaches the <=max_tags line even
    # when the model already filled every slot. Detect against the CAP WINDOW, not `seen` (the model's own AR/
    # content tag may be in seen but sorted PAST the cap). M3: lang_floor fires on `corpus_norm` (the lean fold
    # made corpus the sole differentiator). No corpus + no content -> reserved empty -> byte-identical.
    arabic = set(_ARABIC); content_set = set(content_norm)
    reserved: list[str] = []
    if lang_floor and not any(h in arabic for h in kept[:max_tags]):
        reserved.append(next((h for h in kept if h in arabic), lang_floor[0]))
    if content_norm and not any(h in content_set for h in kept[:max_tags]):
        reserved.append(next((h for h in kept if h in content_set), content_norm[0]))
    if reserved:
        head = [h for h in kept if h not in reserved][:max_tags - len(reserved)]
        kept = head + reserved; seen = set(kept)
    # M3: a corpus-led account keeps one platform DISCOVERY tag (#fyp/#reels/…) — backfill it right after the
    # corpus pool so a curated corpus can't eat all 4 slots and lose its reach. Gated on `corpus_norm` -> no-
    # corpus backfill is byte-identical. An AR clip's region floor still wins the reserved last slot above, so
    # AR accounts prioritise region reach over discovery (acceptable).
    disc_floor = _DISCOVERY.get(platform, _DISCOVERY_DEFAULT)[:1] if corpus_norm else []
    # Backfill is REACH-first; content trails. The content FLOOR above already guarantees ONE content slot,
    # so a seed-fallback clip ships 1 content + reach (not all-content) — content adds more only if reach is
    # exhausted. content=[] -> identical tail -> byte-identical.
    for h in corpus_norm + disc_floor + (store or []) + _composition(platform, language) + content_norm:
        if len(kept) >= max_tags: break
        if h not in seen:
            seen.add(h); kept.append(h)
    return kept[:max_tags]                           # hard cap

_ARABIC_SET = set(_ARABIC)
_DISCOVERY_SET = {t for v in _DISCOVERY.values() for t in v}

def _tag_source(tag: str, *, content_set: set, corpus_set: set, store_set: set) -> str:
    """The provenance label for ONE shipped tag — the real signal it traces to. Priority (highest first):
    content > corpus > region > graph-reach > discovery > genre-floor. Never empty (genre-floor is the
    catch-all for a frozen-pool backfill tag), so a sourceless tag — pure theater — cannot ship. `graph-reach`
    means the tag traces to the live Meta Graph reach store (the SOLE judge of a hashtag — refresh_store ranks
    the store by platform reach, never by a post that used the tag). (M3: the `lean` source was retired.)"""
    if tag in content_set: return "content"
    if tag in corpus_set: return "corpus"
    if tag in _ARABIC_SET: return "region"
    if store_set and tag in store_set: return "graph-reach"
    if tag in _DISCOVERY_SET: return "discovery"
    return "genre-floor"

def vet_hashtags_traced(tags: list[str] | None, platform: Platform, language: str | None = None,
                        max_tags: int = 4, *, store: list[str] | None = None,
                        corpus: list[str] | None = None,
                        content: list[str] | None = None, cfg=None) -> tuple[list[str], dict[str, str]]:
    """vet_hashtags + a provenance `source` per shipped tag. SAME selection as vet_hashtags (DRY — it
    calls it), then labels each kept tag by the signal it traces to (content|corpus|region|graph-reach|
    discovery|genre-floor). This proves every shipped tag is evidence-backed — the hashtag-axis instance
    of the operator's 'every knob real, no theater' rule."""
    out = vet_hashtags(tags, platform, language, max_tags,
                       store=store, corpus=corpus, content=content, cfg=cfg)
    content_set = set(_dedupe_norm(content)); corpus_set = set(_dedupe_norm(corpus))
    store_set = set(store) if store else set()
    sources = {t: _tag_source(t, content_set=content_set, corpus_set=corpus_set, store_set=store_set) for t in out}
    return out, sources
