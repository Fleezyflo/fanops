# src/fanops/hashtags.py
"""Hashtag SELECTION — the gate that turns a persona's derived corpus + the platform measurement cache
into the <=4-tag line a post ships.

Visibility is settled OUTSIDE this module by PLATFORM fields Layer A wrote (ig_hashtag_scrape). RANK is
banded (`size_rank_key`, MOL-977): mid (10k–2M) first, then small, then mega/untrusted, then unknown.
INT32-saturated `media_count` is untrusted mega, not infinite gold. Within a band: Instagram's own
`media_count` DESC, then `current_top_reel_play_max_7d` as a tie-break. The Top-grid MEDIANS
(`play_count`/`like_count`) remain stored evidence and still admit a legacy row, but they are not the
cross-tag order. Nothing here blends the two axes into one invented score.
The frozen `_MEGA`/`_RELEVANCE`/`_RANK`/`VETTED` pools were DELETED.

What survives here is COMPOSITION, which is format rather than a reach claim: at most 4 tags, at most
one mega/untrusted slot (`MEGA_SLOT_MAX`), mid-band order that splits IG (size-then-trend) from TikTok
(trend-then-size), the persona's curated corpus leads but may not monopolise the line
(`_CORPUS_LEAD_MAX`), graded-LRU rotation, and a region tag on Arabic-language clips (`_ARABIC`).
`cfg is None` / no measurements skips mega cap and the platform split (byte-identical).

Membership is store ∪ corpus (content may LABEL a measured tag for provenance): invented tags die;
unmeasured non-corpus tags die; unmeasured corpus tags survive; empty store AND empty corpus
(and non-AR) → empty line."""
from __future__ import annotations
import json, re
from fanops.models import Platform

# The legacy Top-grid MEDIAN fields. Still stored, still enough to admit a row that predates volume
# collection, but NO LONGER the cross-tag rank — see `size_rank_key` (MOL-692).
RANK_FIELDS = ("play_count", "like_count")
METRIC_FIELD = "play_count"        # which median `_metric` prefers, for provenance/UI honesty only
SIZE_FIELD = "media_count"                          # within-band rank: Instagram's own tag volume
TREND_FIELD = "current_top_reel_play_max_7d"        # secondary rank: current Reels popularity
# Volume bands for `size_rank_key` (MOL-977). Compare via `tag_size` so 2147483647.0 matches.
MEGA_MEDIA_FLOOR = 2_000_000
MID_MEDIA_FLOOR = 10_000
INT32_MEDIA_COUNT = 2_147_483_647
MEGA_SLOT_MAX = 1                 # shipped line: at most one mega/untrusted (MOL-978)

# THE record shape Layer A persists (MOL-691). `refresh_store` seeds its working cache from
# `load_measurements` and then rewrites hashtags.json WHOLE, so a field absent from these tuples is
# written in pass N and silently stripped in pass N+1. New evidence MUST land here first.
RECORD_NUM_FIELDS = ("play_count", "like_count", "media_count",
                     "current_top_reel_play_max_7d", "top_reel_sample_n")
RECORD_STR_FIELDS = ("media_count_at",)

CAPTION_TAG_RE = re.compile(r"#[0-9A-Za-z_؀-ۿ]+")   # a hashtag in a caption: Latin + Arabic-block letters
HARVEST_CAP = 5000                 # upper bound on distinct co-tags per harvest — untrusted-UGC guard
# Rows ONE Layer A Top fetch pulls. 9 was Instagram's default grid page, not a measurement floor: far too
# thin to take a maximum over (MOL-691). 27 is instagrapi's own default for its v1/recent/reels reads —
# same endpoint, one more page, ~3x the sample. Lives here (not in ig_hashtag_scrape) because Layer B's
# `density()` denominator MUST be this same number, and Layer B does no network.
TOP_SAMPLE_N = 27

_ARABIC = ["#arabicmusic", "#arabtiktok", "#arabicmusiclovers"]        # AR language/region floor
# Max slots the curated corpus may LEAD in one line. The corpus is tier 0 and is seeded whole, so without
# this a corpus of >= max_tags takes every slot and the model's per-clip picks can never ship (the line
# becomes a pure function of the persona). 2-of-4 keeps the curated lead on every post while guaranteeing
# the clip always influences half the line. NOT a cap on how many corpus tags may ship: the surplus still
# backfills.
_CORPUS_LEAD_MAX = 3          # of max_tags=4; 2 was too stingy, ≥4 re-monopolises the line (H1)


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


def _num(v) -> float | None:
    """One verbatim non-negative platform number, or None. Bools are never numbers here. THE coercion
    for every field in the record contract, so reader / writer / scrape cannot disagree on what counts."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        return None
    return float(v)


def _metric(rec) -> float | None:
    """Visibility sort key: first present RANK_FIELDS value (play_count, then like_count).
    Legacy invented `reach` sums carry neither and read UNMEASURED."""
    if not isinstance(rec, dict): return None
    for k in RANK_FIELDS:
        v = rec.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            continue
        if v > 0:
            return float(v)
    return None


def _rank_field(rec) -> str | None:
    """Which platform field `_metric` used — for UI honesty. None when unmeasured."""
    if not isinstance(rec, dict): return None
    for k in RANK_FIELDS:
        v = rec.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return k
    return None


def tag_size(rec) -> float | None:
    """SCALE: Instagram's own `media_count` — lifetime posts carrying the tag. Within-band rank
    after `size_band` (MOL-977). None when volume was never served; a volumeless tag is not "small",
    it is unmeasured on this axis."""
    if not isinstance(rec, dict): return None
    v = _num(rec.get("media_count"))
    return v if (v or 0) > 0 else None


def tag_trend(rec) -> float | None:
    """CURRENT REELS POPULARITY: max plays among Top rows Instagram dated inside 7 days. A strictly
    SECONDARY tie-break — it refines the size order WITHIN a band and cannot promote a smaller tag
    over a larger one in the same band. Different unit from `tag_size`; the two are never summed or blended."""
    if not isinstance(rec, dict): return None
    v = _num(rec.get("current_top_reel_play_max_7d"))
    return v if (v or 0) > 0 else None


def has_evidence(rec) -> bool:
    """THE admission predicate for the cache: Instagram gave us at least one positive number for this tag —
    scale, current Reels popularity, or the legacy Top-grid medians. Legacy invented `reach` sums carry
    none of the three and read UNMEASURED."""
    return tag_size(rec) is not None or tag_trend(rec) is not None or _metric(rec) is not None


def size_band(rec) -> str:
    """Volume band for one measurement record. Compare via `tag_size` so INT32 floats match.

    mid:        MID_MEDIA_FLOOR <= size < MEGA_MEDIA_FLOOR
    small:      0 < size < MID_MEDIA_FLOOR
    mega:       MEGA_MEDIA_FLOOR <= size < INT32_MEDIA_COUNT
    untrusted:  size >= INT32_MEDIA_COUNT
    unknown:    missing / unparseable
    """
    size = tag_size(rec)
    if size is None:
        return "unknown"
    if size >= INT32_MEDIA_COUNT:
        return "untrusted"
    if size >= MEGA_MEDIA_FLOOR:
        return "mega"
    if size >= MID_MEDIA_FLOOR:
        return "mid"
    return "small"


# mid first, then small, then mega/untrusted, then unknown. mega and untrusted share a rank.
_BAND_RANK = {"mid": 0, "small": 1, "mega": 2, "untrusted": 2, "unknown": 3}


def size_rank_key(tag: str, rec) -> tuple:
    """THE menu order (MOL-977): band first, then size-then-trend within the band — NOT a weighted score.

    1. band: mid, then small, then mega/untrusted, then unknown;
    2. a positive `media_count` still outranks a volumeless peer (plays cannot stand in for volume);
    3. `media_count` DESC — the dominant ordering inside a band;
    4. `current_top_reel_play_max_7d` DESC — a tie-break WITHIN equal size only;
    5. tag string, so the order is total and stable.

    INT32-saturated `media_count` is untrusted mega, not the largest gold. Volumeless-but-evidenced
    tags sort last (unknown) and use their 7-day plays only among themselves."""
    size = tag_size(rec) or 0.0
    return (_BAND_RANK[size_band(rec)], -size, -(tag_trend(rec) or 0.0), tag)


def load_measurements(cfg) -> dict[str, dict]:
    """THE reader for the platform measurement cache (00_control/hashtags.json). Retained keys are
    `graph_id`, `measured_at`, `from`, and the RECORD_NUM_FIELDS / RECORD_STR_FIELDS contract.

    `from` is harvest attribution. A record missing every RANK_FIELDS metric, graph id, or timestamp
    is dropped. Absent / corrupt / legacy file -> {}. Never raises."""
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
        if not tag or not isinstance(v, dict):
            continue
        gid = v.get("graph_id"); at = v.get("measured_at")
        if not isinstance(gid, str) or not gid or not isinstance(at, str):
            continue
        rec: dict = {"graph_id": gid, "measured_at": at}
        for fk in RECORD_NUM_FIELDS:
            fv = _num(v.get(fk))
            if fv is not None:
                rec[fk] = fv
        for fk in RECORD_STR_FIELDS:
            fv = v.get(fk)
            if isinstance(fv, str) and fv:
                rec[fk] = fv
        if not has_evidence(rec):
            continue
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
    """The cache as one ordered menu via `size_rank_key` (band, then size, then 7-day trend, then tag).
    `vet_hashtags` takes its whole metric rank from this order."""
    return sorted(measurements, key=lambda t: size_rank_key(t, measurements[t]))


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
    gate; production callers pass them via `content=` (request_captions → ingest / Studio regen)."""
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


def _mid_rank_key(tag: str, rec, platform: Platform) -> tuple:
    """Within the mid band: IG keeps size-then-trend; TikTok prefers TREND_FIELD then size."""
    if platform is Platform.tiktok:
        return (-(tag_trend(rec) or 0.0), -(tag_size(rec) or 0.0), tag)
    return size_rank_key(tag, rec)


def _compose_shipped(candidates, platform: Platform, measurements: dict, max_tags: int) -> list[str]:
    """Slot hygiene after membership/sort/corpus-lead: mega/untrusted ≤ MEGA_SLOT_MAX; leftover
    slots prefer mid. Mid-band order is platform-split. No semantic tag-name list — `#love` is
    limited because it is INT32/mega, not because it is named `#love`."""
    def rec(t): return measurements.get(t) or {}
    def band(t): return size_band(rec(t))
    uniq: list[str] = []; seen: set[str] = set()
    for t in candidates:
        if t and t not in seen:
            seen.add(t); uniq.append(t)
    mid_pos = [i for i, t in enumerate(uniq) if band(t) == "mid"]
    mids = sorted((uniq[i] for i in mid_pos), key=lambda t: _mid_rank_key(t, rec(t), platform))
    for i, t in zip(mid_pos, mids):
        uniq[i] = t
    out: list[str] = []; leftover: list[str] = []; mega_n = 0
    for t in uniq:
        magnet = band(t) in ("mega", "untrusted")
        if len(out) < max_tags and not (magnet and mega_n >= MEGA_SLOT_MAX):
            out.append(t)
            if magnet: mega_n += 1
        else:
            leftover.append(t)
    if len(out) < max_tags:
        prefer = [t for t in leftover if band(t) == "mid"]
        rest = [t for t in leftover if band(t) not in ("mid", "mega", "untrusted")]
        magnets = [t for t in leftover if band(t) in ("mega", "untrusted")]
        for t in prefer + rest + (magnets if mega_n < MEGA_SLOT_MAX else []):
            if len(out) >= max_tags: break
            if t in out: continue
            if band(t) in ("mega", "untrusted"):
                if mega_n >= MEGA_SLOT_MAX: continue
                mega_n += 1
            out.append(t)
    return out[:max_tags]


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
    cache entry expires between derivation and selection) and leads the line. Invented tags die;
    unmeasured non-corpus tags die; unmeasured corpus tags survive.

    Order: corpus tier, then the clip's own picks, then graded LRU (`recent`, oldest-first — never-used
    leads), then the platform metric rank. After sort + corpus-lead cap: when `cfg` has measurements, at most `MEGA_SLOT_MAX` mega/untrusted tags occupy the shipped line and leftover slots prefer mid; within mid, IG keeps size-then-trend and TikTok prefers `TREND_FIELD` then size. `cfg is None` / empty measurements skip that layer (byte-identical). Floors take the TAIL slots so the corpus/metric lead survives:
    one `_ARABIC` tag on an Arabic-language clip. Backfill is corpus -> the measured menu (content is preference only).

    Empty store AND empty corpus (and non-AR) -> an empty line. That is the honest floor: there is no
    hand-ranked pool and no discovery pad left to invent reach with."""
    corpus_norm = _dedupe_norm(corpus)
    store_norm = _dedupe_norm(store)                   # the measured menu, already metric-ranked
    # MOL-635 residual / MOL-642 tighten: content is a FIT signal among MEASURED tags only
    # (store ∪ corpus). Unmeasured ASR tokens must not pad a cold/empty line (no content floor).
    measured = set(store_norm) | set(corpus_norm)
    content_norm = [t for t in _screen_content(_dedupe_norm(content), cfg) if t in measured]
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
    # Backfill measured material only — content is already a subset of measured (preference, not pad).
    for h in corpus_norm + store_norm:
        if len(kept) >= max_tags: break
        if h not in seen:
            seen.add(h); kept.append(h)
    # MOL-978: mega ≤ 1 and IG/TT mid split. Fail-open when cfg/measurements are missing so
    # clip-signal tests (cfg=None) stay byte-identical. `platform` is read here.
    # load_measurements never raises; OSError covers monkeypatched/unreadable cache (persona_facts).
    try:
        measurements = {} if cfg is None else (load_measurements(cfg) or {})
    except OSError:
        measurements = {}
    if not measurements:
        return kept[:max_tags]
    pool = list(kept)
    seen_p = set(pool)
    for h in corpus_norm + store_norm:
        if h not in seen_p:
            seen_p.add(h); pool.append(h)
    return _compose_shipped(pool, platform, measurements, max_tags)


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
