# src/fanops/hashtags.py
"""Hashtag helpers — caption ship + measurement cache.

Posted tags: `ship_from_lock(picks, lock, n=4)` — picks ∩ source lock, pick
order, hard cap. No AR floor, no mega slot, no store ∪ corpus, no backfill.
"""
from __future__ import annotations
import json, re

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

def norm_tag(tag: str) -> str:
    """Canonicalise one tag: strip, lowercase, exactly one leading '#', no inner spaces. '' -> ''."""
    if not tag: return ""
    t = tag.strip().lower().lstrip("#").strip()
    return f"#{t}" if t else ""


_norm = norm_tag  # scrape stack + in-module callers; prefer norm_tag at new call sites


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


def play_rank_key(tag, rec) -> tuple:
    """Lock order: play_count DESC, then 7-day reel max DESC, then tag. Plays only — not `_metric`/likes/size."""
    rec = rec if isinstance(rec, dict) else {}
    plays = _num(rec.get("play_count")) or 0.0
    trend = _num(rec.get("current_top_reel_play_max_7d")) or 0.0
    return (-plays, -trend, _norm(tag) if isinstance(tag, str) else "")


def _scrape_number(rec) -> float | None:
    """Positive scrape meter: play_count or like_count. graph_metric is a separate axis."""
    if not isinstance(rec, dict):
        return None
    for k in RANK_FIELDS:
        v = _num(rec.get(k))
        if v is not None and v > 0:
            return v
    return None


def lock_from_pile(names, measurements, n=12) -> list[str]:
    """Top-n names with a positive play_count, ordered by play_rank_key. Unmeasured stay off the lock."""
    recs = measurements if isinstance(measurements, dict) else {}
    kept: list[str] = []
    for name in _dedupe_norm(names):
        rec = recs.get(name)
        if not isinstance(rec, dict):
            continue
        plays = _num(rec.get("play_count"))
        if plays is None or plays <= 0:
            continue
        kept.append(name)
    kept.sort(key=lambda t: play_rank_key(t, recs.get(t)))
    return kept[:n]


def lock_from_shortlist(names, measurements, n=12) -> list[str]:
    """Positive play_count admits, caller order, cap n. Does not re-sort."""
    recs = measurements if isinstance(measurements, dict) else {}
    out: list[str] = []
    for name in _dedupe_norm(names):
        rec = recs.get(name)
        if not isinstance(rec, dict):
            continue
        plays = _num(rec.get("play_count"))
        if plays is None or plays <= 0:
            continue
        out.append(name)
        if len(out) >= n:
            break
    return out


def ship_from_lock(picks, lock, n=4) -> list[str]:
    """Caption ship: picks ∩ lock, pick order, cap n. Empty lock → []. No floors, no backfill."""
    allowed = set(_dedupe_norm(lock))
    out: list[str] = []
    for t in _dedupe_norm(picks):
        if t in allowed:
            out.append(t)
        if len(out) >= n:
            break
    return out


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
    """Cache order via `size_rank_key` (band, then size, then 7-day trend, then tag)."""
    return sorted(measurements, key=lambda t: size_rank_key(t, measurements[t]))


# --- hashtag hygiene (structural gates for derived corpus tags) -----------------------------------
_RUN = re.compile(r"(.)\1{3,}")            # 4+ of the same char in a row: keysmash, never a real tag
_SHAPE = re.compile(r"^#[a-z0-9_]+$")      # post-norm_tag shape; anything else is malformed
_MAX_LEN = 30                              # 30 chars after '#'; longer is a sentence or a keysmash
_MIN_LEN = 2


def tag_defect(tag: str) -> str | None:
    """The STRUCTURAL defect in `tag`, or None if it is clean enough to be curated. Pure + deterministic
    — the same string always yields the same verdict, so a refusal is explainable and testable. Expects a
    raw tag; normalizes internally (so callers cannot bypass the gate by passing 'FYP' or ' #Love ')."""
    h = norm_tag(tag) if isinstance(tag, str) else ""
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
