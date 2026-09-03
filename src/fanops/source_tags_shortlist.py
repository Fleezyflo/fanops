# src/fanops/source_tags_shortlist.py
"""LLM shortlist, transcript prose, and hydrate-from-known lock repair."""
from __future__ import annotations
import json
from pathlib import Path

from fanops.hashtags import (_dedupe_norm, _norm, _scrape_number, load_measurements,
                             lock_from_pile, play_rank_key)
from fanops.source_tags_sidecar import (_LOCK_N, _hydrate_stamp, _researched,
                                        _union_lock_meters, load_source_tag_locks)

_RESEARCH_CAP = 12
_CATALOG_CAP = 30
_RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "keep": {"type": "array", "items": {"type": "string"}},
        "reject": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["keep"],
}


def _prose(source, excerpt=None) -> str:
    """Title/language stay on the source; this is transcript as PROSE — not ASR tokens."""
    if isinstance(excerpt, str) and excerpt.strip():
        return excerpt.strip()
    raw = getattr(source, "transcript", None)
    if isinstance(raw, str):
        return raw.strip()
    if not isinstance(raw, list):
        return ""
    parts: list[str] = []
    for seg in raw:
        t = seg.get("text") if isinstance(seg, dict) else None
        if isinstance(t, str) and t.strip():
            parts.append(t.strip())
    return " ".join(parts)


def _transcript_json_path(cfg, source):
    raw = getattr(source, "source_path", None) or ""
    if not raw:
        return None
    return cfg.agent_io / "transcripts" / f"{Path(raw).stem}.json"


def _transcript_file_prose(cfg, source) -> str:
    """Whisper JSON when the ledger transcript is not adopted yet."""
    p = _transcript_json_path(cfg, source)
    if p is None or not p.exists():
        return ""
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return ""
    segs = data.get("segments") if isinstance(data, dict) else None
    if not isinstance(segs, list):
        return ""
    parts: list[str] = []
    for seg in segs:
        t = seg.get("text") if isinstance(seg, dict) else None
        if isinstance(t, str) and t.strip():
            parts.append(t.strip())
    return " ".join(parts)


def shortlist_source_tags(source, excerpt, catalog) -> list[str]:
    """One LLM pass. Non-empty catalog: keep ∩ catalog. Empty catalog: name the pile."""
    from fanops.llm import claude_json_meta
    allowed = _dedupe_norm(catalog)[:_CATALOG_CAP]
    raw_title = getattr(source, "title", None)
    title = raw_title.strip() if isinstance(raw_title, str) and raw_title.strip() else ""
    title_line = f"title: {title}\n" if title else ""
    language = getattr(source, "language", None) or ""
    if allowed:
        prompt = (
            "You judge Instagram hashtags for THIS video for a fan account that reposts it.\n"
            "Choose ONLY from the catalog. keep = names a real person would search to find THIS clip "
            "(artist/subject that actually appear, genre, format, topic).\n"
            "reject = slogans, glued theses, unique compounds, sibling tracks, wallpaper padding.\n"
            "Do not invent a name that is not in the catalog.\n"
            f"{title_line}"
            f"language: {language}\n"
            f"transcript: {excerpt or ''}\n"
            f"catalog: {', '.join(allowed)}\n"
            "Return at most 12 keep names, catalog order unless a clearer fit comes first."
        )
    else:
        prompt = (
            "You name Instagram hashtags for THIS video for a fan account that reposts it.\n"
            "keep = real hashtag names a person would search to find THIS clip "
            "(artist/subject that actually appear, genre, format, topic).\n"
            "reject = slogans, glued theses, unique compounds, sibling tracks, wallpaper padding, #fyp.\n"
            "Do not invent a glued slogan. Names must be plausible Instagram hashtags.\n"
            f"{title_line}"
            f"language: {language}\n"
            f"transcript: {excerpt or ''}\n"
            "Return at most 12 keep names."
        )
    data, _model, _unread = claude_json_meta(prompt, _RESEARCH_SCHEMA)
    keep = data.get("keep") if isinstance(data, dict) else None
    if not isinstance(keep, list):
        return []
    allow = set(allowed) if allowed else None
    out: list[str] = []
    for raw in keep:
        if not isinstance(raw, str):
            continue
        n = _norm(raw)
        if not n or n in out:
            continue
        if allow is not None and n not in allow:
            continue
        out.append(n)
        if len(out) >= _RESEARCH_CAP:
            break
    return out


def used_tags_for_source(led, sid: str) -> list[str]:
    """Hashtags already on THIS source's clips/posts. Not the global store."""
    counts: dict[str, int] = {}
    if led is None or not sid:
        return []
    clip_ids: set[str] = set()
    for clip in getattr(led, "clips", {}).values():
        mom = getattr(led, "moments", {}).get(getattr(clip, "parent_id", None))
        if mom is None or str(getattr(mom, "parent_id", "") or "") != sid:
            continue
        cid = str(getattr(clip, "id", "") or "")
        if cid:
            clip_ids.add(cid)
        meta = getattr(clip, "meta_captions", None)
        if not isinstance(meta, dict):
            continue
        for rec in meta.values():
            if not isinstance(rec, dict):
                continue
            for raw in rec.get("hashtags") or []:
                n = _norm(raw) if isinstance(raw, str) else ""
                if n:
                    counts[n] = counts.get(n, 0) + 1
    for post in getattr(led, "posts", {}).values():
        if str(getattr(post, "parent_id", "") or "") not in clip_ids:
            continue
        for raw in getattr(post, "hashtags", None) or []:
            n = _norm(raw) if isinstance(raw, str) else ""
            if n:
                counts[n] = counts.get(n, 0) + 1
    return sorted(counts, key=lambda t: (-counts[t], t))


def known_lock(names, measurements, used, n=12, keep=None) -> list[str]:
    """This-source used tags (measured first, play then 7d reel), then keep, then play-ranked pile.

    Unmeasured used tags still belong — they already shipped on this video. Cap at n (12).
    The global store does not belong.
    """
    seen: set[str] = set()
    out: list[str] = []
    recs = measurements if isinstance(measurements, dict) else {}
    used_n = _dedupe_norm(used)
    used_n.sort(key=lambda t: (0 if _scrape_number(recs.get(t)) is not None else 1,
                               play_rank_key(t, recs.get(t))))
    for t in used_n:
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= n:
            return out[:n]
    for t in _dedupe_norm(keep):
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= n:
            return out[:n]
    for t in lock_from_pile(names, recs, n):
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= n:
            return out[:n]
    return out[:n]


def hydrate_locks_from_known(cfg, led) -> int:
    """Merge locks from tags this source already used. Zero network. Never researched_at.

    Does not open the caption gate. Does not recaption. Returns rows written.
    Not called from lock_ready_sources — optional / repair only.
    """
    if cfg is None or led is None:
        return 0
    table = load_source_tag_locks(cfg)
    measurements = dict(load_measurements(cfg))
    _union_lock_meters(table, measurements)
    n = 0
    for source in getattr(led, "sources", {}).values():
        if getattr(source, "origin_kind", "native") == "third_party":
            continue
        sid = str(getattr(source, "id", "") or "")
        if not sid:
            continue
        if _researched(table, sid):
            continue
        used = used_tags_for_source(led, sid)
        if not used:
            continue
        prior = table.get(sid) if isinstance(table.get(sid), dict) else {}
        pile = _dedupe_norm(prior.get("pile") if isinstance(prior.get("pile"), list) else [])
        verified = prior.get("verified") if isinstance(prior.get("verified"), list) else None
        names = _dedupe_norm(verified if verified else (prior.get("lock") or pile))
        keep = prior.get("lock") if _researched(table, sid) else []
        lock = known_lock(names, measurements, used, _LOCK_N, keep=keep)
        if not lock:
            continue
        if list(prior.get("lock") or []) == lock and (
            _researched(table, sid) or isinstance(prior.get("hydrated_at"), str)
        ):
            continue
        _hydrate_stamp(cfg, table, sid, pile, lock, measurements, prior=prior)
        n += 1
    return n
