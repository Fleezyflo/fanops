# src/fanops/hashtags.py
"""Hashtag SELECTION — the gate that turns a persona's derived corpus + the platform measurement cache
into the <=4-tag line a post ships.

The reach question is settled OUTSIDE this module and it is settled by the PLATFORM: `METRIC_FIELD` is
Meta's own `like_count`, read verbatim off one `top_media` item (fanops_hashtags/meta_graph write the
cache; this module only reads it). Nothing here ranks a tag by a number we computed, and nothing here
carries a hand-researched reach claim — the frozen `_MEGA`/`_RELEVANCE`/`_RANK`/`VETTED` pools that used
to be this file's authority were DELETED: they asserted reach from June-2026 desk research, which is
exactly the manufactured assessment the system must not make.

What survives here is COMPOSITION, which is format rather than a reach claim: at most 4 tags, the
persona's curated corpus leads but may not monopolise the line (`_CORPUS_LEAD_MAX`), graded-LRU rotation,
a region tag on Arabic-language clips (`_ARABIC`), and the operator's global ban list as a hard veto.

Membership is the cache UNION the surface's corpus (minus bans): a tag the model invents cannot ship, and
a tag nobody measured cannot ship either. A cold cache therefore yields an empty line, not a padded one."""
from __future__ import annotations
import json, re
from fanops.models import Platform

# The platform's own field name for the reach/visibility signal. Probed live 2026-07-26: the IG Hashtag
# node serves ONLY `id` and `name` — `media_count` returns "(#100) Tried accessing nonexisting field", so
# post volume is genuinely unavailable and `like_count` on the hashtag's own top media is the visibility
# datum Meta actually publishes. Stored under Meta's name, never renamed to "reach".
METRIC_FIELD = "like_count"

_ARABIC = ["#arabicmusic", "#arabtiktok", "#arabicmusiclovers"]        # AR language/region floor
# Max slots the curated corpus may LEAD in one line. The corpus is tier 0 and is seeded whole, so without
# this a corpus of >= max_tags takes every slot and the model's per-clip picks can never ship (the line
# becomes a pure function of the persona). 2-of-4 keeps the curated lead on every post while guaranteeing
# the clip always influences half the line. NOT a cap on how many corpus tags may ship: the surplus still
# backfills.
_CORPUS_LEAD_MAX = 2


def _norm(tag: str) -> str:
    """Canonicalise one tag: strip, lowercase, exactly one leading '#', no inner spaces. '' -> ''."""
    if not tag: return ""
    t = tag.strip().lower().lstrip("#").strip()
    return f"#{t}" if t else ""


def _dedupe_norm(seq) -> list[str]:
    """Normalize + dedupe a tag sequence (corpus / content / cache), preserving first-seen order."""
    out: list[str] = []; seen: set[str] = set()
    for t in (seq or []):
        n = _norm(t) if isinstance(t, str) else ""
        if n and n not in seen: seen.add(n); out.append(n)
    return out


def _metric(rec) -> float | None:
    """One record's platform metric as a float, or None when the record carries no platform measurement.
    A legacy record (the invented likes+comments sum written under a `reach` key) has no METRIC_FIELD and
    therefore reads as UNMEASURED — inadmissible by construction, shed on the next write. That is the
    intended cutover: an invented number must not survive as evidence just because it is on disk."""
    if not isinstance(rec, dict): return None
    v = rec.get(METRIC_FIELD)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0: return None
    return float(v)


def load_measurements(cfg) -> dict[str, dict]:
    """THE reader for the platform measurement cache (00_control/hashtags.json):
    `{tag: {graph_id, <METRIC_FIELD>, measured_at, from: {anchor: n}}}`.

    `from` is the harvest attribution — which anchor tag's top media surfaced this tag — and it is what
    lets corpus derivation run with ZERO network. A record missing the platform metric, the graph id or
    the timestamp is dropped rather than repaired: a half-record is not a measurement. Absent / corrupt /
    legacy file -> {} (the selection layer then ships short, honestly). Never raises."""
    p = cfg.hashtags_path
    if not p.exists(): return {}
    try:
        raw = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict): return {}
    out: dict[str, dict] = {}
    for k, v in raw.items():
        tag = _norm(k) if isinstance(k, str) else ""
        val = _metric(v)
        gid = v.get("graph_id") if isinstance(v, dict) else None
        at = v.get("measured_at") if isinstance(v, dict) else None
        if not tag or val is None or not isinstance(gid, str) or not gid or not isinstance(at, str):
            continue
        rec = {"graph_id": gid, METRIC_FIELD: val, "measured_at": at}
        src = v.get("from")
        if isinstance(src, dict):
            frm: dict[str, int] = {}
            for sk, sv in src.items():
                n = _norm(sk) if isinstance(sk, str) else ""
                if not n: continue
                try: frm[n] = int(sv)
                except (TypeError, ValueError): continue
            if frm: rec["from"] = frm
        out[tag] = rec
    return out


def ranked_tags(measurements: dict[str, dict]) -> list[str]:
    """The cache as one ordered menu: platform metric DESC, ties by tag string (deterministic)."""
    return sorted(measurements, key=lambda t: (-(measurements[t].get(METRIC_FIELD) or 0.0), t))


# Word tokenizer for the per-clip content signal. A token is a latin word, 3-20 chars, starting with a
# letter (so '12'/'###'/Arabic yield nothing). (persona_terms no longer tokenizes — it returns niche.)
_STOPWORDS = frozenset(
    "a an and are as at be but by for from had has have he her his i in is it its me my no not of on or "
    "our out so that the their them they this to too up us was we what when where which who will with you "
    "your yours just got get like dont cant im "
    # URL/tech-adjacent tokens a transcript can surface as a high-frequency word — never a real hashtag.
    "http https www com org net mp3 mp4 wav png jpg jpeg gif url link".split())
_WORD = re.compile(r"[a-z][a-z0-9]{2,19}")


def content_tag_candidates(text: str | None, *, max_n: int = 6) -> list[str]:
    """Per-clip content signal: candidate hashtag tokens derived from THIS clip's transcript text.
    Deterministic + pure (NO NLP model): lowercase, latin word tokens (3-20 chars), drop stopwords,
    order by frequency then first-seen, normalize to '#tag', dedupe, cap at `max_n`. Blank / non-str /
    non-latin (Arabic) / numbers-only -> []. These are CANDIDATES that must still pass the membership
    gate; the channel is currently dormant (no production caller passes `content=`)."""
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


def _screen_content(content_norm: list[str], cfg=None) -> list[str]:
    """MOL-76: drop any content-derived candidate that trips brand_risk_flag — the SAME off-brand guard
    caption.py runs on the model's caption/hook — BEFORE it can join the membership or win the content
    FLOOR. content_tag_candidates pulls tokens straight from raw, unscreened ASR transcript (routinely
    explicit on a rap catalogue). Function-local import: caption.py imports FROM hashtags.py, so a
    module-level import would cycle. content=[] -> [] (byte-identical)."""
    if not content_norm:
        return content_norm
    from fanops.caption import brand_risk_flag       # function-local: caption imports hashtags -> no module cycle
    return [t for t in content_norm if not brand_risk_flag(t, cfg)]


def vet_hashtags(tags: list[str] | None, platform: Platform, language: str | None = None,
                 max_tags: int = 4, *, store: list[str] | None = None,
                 corpus: list[str] | None = None, content: list[str] | None = None,
                 cfg=None, recent: list[str] | None = None) -> list[str]:
    """Return at most `max_tags` tags for one surface, composed from PLATFORM-MEASURED material only.

    `store` is the measurement cache as an ordered menu (`ranked_tags(load_measurements(cfg))`) — it is
    both the membership set and the rank. `corpus` is the surface's derived per-persona pool; it JOINS the
    membership (a corpus tag is by construction already measured, but joining keeps the gate honest if a
    cache entry expires between derivation and selection) and leads the line. A tag the model invented is
    in neither set and dies here.

    Order: corpus tier, then the clip's own picks, then graded LRU (`recent`, oldest-first — never-used
    leads), then the platform metric rank. Floors take the TAIL slots so the corpus/metric lead survives:
    one `_ARABIC` tag on an Arabic-language clip. Backfill is corpus -> the measured menu -> content.

    Cold cache + no corpus -> an empty line. That is the honest floor: there is no hand-ranked pool and no
    discovery pad left to invent reach with."""
    corpus_norm = _dedupe_norm(corpus)
    content_norm = _screen_content(_dedupe_norm(content), cfg)
    store_norm = _dedupe_norm(store)                   # the measured menu, already metric-ranked
    vetted = set(store_norm) | set(corpus_norm) | set(content_norm)
    base_rank = {t: i for i, t in enumerate(store_norm)}
    # Preference float ahead of the metric rank: corpus (the persona's derived pool) > content (clip info).
    preferred: list[str] = []
    for grp in (corpus_norm, content_norm):
        for t in grp:
            if t not in preferred: preferred.append(t)
    rank = {**base_rank, **{t: i - len(preferred) for i, t in enumerate(preferred)}}
    lang_floor = _ARABIC[:1] if (language or "").strip().lower().startswith("ar") else []
    seen: set[str] = set()
    kept: list[str] = []
    for h in corpus_norm:                           # seed the WHOLE corpus first (the sort + cap bound it),
        if h not in seen: seen.add(h); kept.append(h)   # so a corpus AR tag past the cap stays eligible for
                                                    # the AR-floor promotion below rather than being dropped early
    for t in (tags or []):                          # honour the model's choices, but ONLY vetted ones
        h = _norm(t)
        if h in vetted and h not in seen:
            seen.add(h); kept.append(h)
    # Recency is a GRADED LRU rank, not a membership flag. `recent` arrives oldest-first, so a tag's LAST
    # occurrence is its most-recent use: never-used (-1) leads, then least-recently-used. As a BOOLEAN the
    # tiebreak went CONSTANT once `recent` covered the corpus, locking the line from clip 3 onward.
    recent_pos: dict[str, int] = {}
    for i, t in enumerate(recent or []):
        h = _norm(t) if isinstance(t, str) else ""
        if h: recent_pos[h] = i                      # last write wins == the tag's most-recent use
    # The model's VETTED picks, INCLUDING corpus ones: the seed loop above appends only `h not in seen`, so
    # a pick that IS a corpus tag leaves no trace there. Recomputing here is what lets the clip signal order
    # tier 0 — the common case, since the prompt shows the model the corpus as its menu.
    picked = {h for h in (_norm(t) for t in (tags or []) if isinstance(t, str)) if h and h in vetted}

    def _tier(h):
        if h in corpus_norm: return 0
        if h in content_norm: return 1
        return 2                                     # the measured cache — the only other membership source

    kept.sort(key=lambda h: (_tier(h), 0 if h in picked else 1, recent_pos.get(h, -1), rank.get(h, 999)))
    # The corpus may not occupy EVERY slot: tier 0 + the whole-corpus seed means a corpus of >= max_tags
    # monopolises the line and the shipped tags become a pure function of the persona, with the video not an
    # input. Cap the corpus LEAD; surplus corpus tags keep their order behind the picks and still backfill.
    if len(corpus_norm) > _CORPUS_LEAD_MAX:
        cset = set(corpus_norm)
        c_kept = [h for h in kept if h in cset]; o_kept = [h for h in kept if h not in cset]
        kept = c_kept[:_CORPUS_LEAD_MAX] + o_kept + c_kept[_CORPUS_LEAD_MAX:]
    # Reserved floors take the TAIL slots so the corpus/metric LEAD is preserved. Detect against the CAP
    # WINDOW, not `seen` (the model's own AR tag may be in seen but sorted PAST the cap).
    arabic = set(_ARABIC)
    reserved: list[str] = []
    if lang_floor and not any(h in arabic for h in kept[:max_tags]):
        reserved.append(next((h for h in kept if h in arabic), lang_floor[0]))
    if reserved:
        head = [h for h in kept if h not in reserved][:max_tags - len(reserved)]
        kept = head + reserved; seen = set(kept)
    for h in corpus_norm + store_norm + content_norm:
        if len(kept) >= max_tags: break
        if h not in seen:
            seen.add(h); kept.append(h)
    return kept[:max_tags]


_ARABIC_SET = set(_ARABIC)


def _tag_source(tag: str, *, content_set: set, corpus_set: set, store_set: set) -> str:
    """The provenance label for ONE shipped tag — the real signal it traces to. Priority (highest first):
    content > corpus > region > graph-reach. TOTAL by construction: membership is the cache UNION corpus
    UNION content, and the only tag added outside that is the AR region floor. `graph-reach` means the tag
    carries a live platform measurement in the cache — never a post that used it (attribution severance)."""
    if tag in content_set: return "content"
    if tag in corpus_set: return "corpus"
    if tag in _ARABIC_SET: return "region"
    if tag in store_set: return "graph-reach"
    raise AssertionError(f"unattributed hashtag {tag!r}")  # unreachable: discovery floor deleted


def vet_hashtags_traced(tags: list[str] | None, platform: Platform, language: str | None = None,
                        max_tags: int = 4, *, store: list[str] | None = None,
                        corpus: list[str] | None = None, content: list[str] | None = None,
                        cfg=None, recent: list[str] | None = None) -> tuple[list[str], dict[str, str]]:
    """vet_hashtags + a provenance `source` per shipped tag. SAME selection (DRY — it calls it), then
    labels each kept tag by the signal it traces to (content|corpus|region|graph-reach). This proves every
    shipped tag is evidence-backed — no tag can ride a claim we invented."""
    out = vet_hashtags(tags, platform, language, max_tags,
                       store=store, corpus=corpus, content=content, cfg=cfg, recent=recent)
    content_set = set(_dedupe_norm(content)); corpus_set = set(_dedupe_norm(corpus))
    store_set = set(_dedupe_norm(store))
    sources = {t: _tag_source(t, content_set=content_set, corpus_set=corpus_set, store_set=store_set) for t in out}
    return out, sources
