# src/fanops/source_tags.py
"""Source hashtag lock producer (HV1-PR2).

One research call → search_hashtags pile → measure → lock_from_pile (plays, ≤12).
Sidecar is cfg.control / source_tag_locks.json — not a Config field, not hashtags.json.
Never raises. Caption still reads the 80-pile until PR3.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

from fanops.controlio import write_json_atomic
from fanops.errors import fail_open
from fanops.hashtags import _dedupe_norm, _norm, _num, load_measurements, lock_from_pile
from fanops.ig_hashtag_scrape import (ScrapeUnavailable, measure_and_harvest_scrape, open_client,
                                     search_hashtags_scrape)
from fanops.log import get_logger
from fanops.timeutil import iso_z

SOURCE_TAG_LOCKS_NAME = "source_tag_locks.json"
_LOCK_N = 12
_RESEARCH_CAP = 30
_RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {"names": {"type": "array", "items": {"type": "string"}}},
    "required": ["names"],
}


def source_tag_locks_path(cfg):
    """Sidecar path. Not a Config field — callers must not add one."""
    return cfg.control / SOURCE_TAG_LOCKS_NAME


def load_source_tag_locks(cfg) -> dict:
    """Read the sidecar. Missing / corrupt / unreadable → {}. Never raises."""
    table: dict = {}
    with fail_open("source_tags.load"):
        if cfg is None:
            return {}
        p = source_tag_locks_path(cfg)
        if not p.exists():
            return {}
        raw = json.loads(p.read_text())
        table = raw if isinstance(raw, dict) else {}
    return table


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


def _default_research(source, excerpt) -> list[str]:
    from fanops.llm import claude_json_meta
    title = getattr(source, "title", None) or ""
    language = getattr(source, "language", None) or ""
    prompt = (
        "Propose Instagram hashtag names for this video source.\n"
        f"title: {title}\n"
        f"language: {language}\n"
        f"transcript: {excerpt or ''}\n"
        "Return at most 30 names. Names only."
    )
    data, _model, _unread = claude_json_meta(prompt, _RESEARCH_SCHEMA)
    names = data.get("names") if isinstance(data, dict) else None
    if not isinstance(names, list):
        return []
    return [n for n in names if isinstance(n, str)]


def _researched(table, sid: str) -> bool:
    rec = table.get(sid)
    if not isinstance(rec, dict):
        return False
    at = rec.get("researched_at")
    return isinstance(at, str) and bool(at.strip())


def ensure_source_lock(cfg, source, *, excerpt=None, client=None, research_fn=None,
                       open_client_fn=None) -> None:
    """Idempotent: research → search all hits onto pile → measure → lock. Never raises."""
    with fail_open("source_tags.ensure"):
        sid = str(getattr(source, "id", "") or "")
        if not sid:
            return
        table = load_source_tag_locks(cfg)
        if _researched(table, sid):
            return
        log = get_logger(cfg)
        # Client first: no_scrape writes nothing, so skip the LLM when scrape is down.
        cli = client
        if cli is None:
            opener = open_client_fn or open_client
            try:
                cli = opener(cfg)
            except ScrapeUnavailable:
                log("source_tags", sid, "no_scrape")
                return
            except Exception as exc:
                log("source_tags", sid, "no_scrape", err=type(exc).__name__)
                return
        if cli is None:
            log("source_tags", sid, "no_scrape")
            return
        try:
            raw_names = (research_fn or _default_research)(source, _prose(source, excerpt))
        except Exception as exc:
            log("source_tags", sid, "research_fail", err=type(exc).__name__)
            return
        names = _dedupe_norm(raw_names if isinstance(raw_names, list) else [])[:_RESEARCH_CAP]
        if not names:
            log("source_tags", sid, "research_empty")
            return
        pile: list[str] = []
        seen: set[str] = set()
        for q in names:
            for hit in search_hashtags_scrape(cli, q):
                n = _norm(hit.get("name") if isinstance(hit, dict) else "")
                if n and n not in seen:
                    seen.add(n)
                    pile.append(n)
        measurements = dict(load_measurements(cfg))
        for tag in pile:
            rec = measurements.get(tag)
            plays = _num(rec.get("play_count")) if isinstance(rec, dict) else None
            if plays is not None and plays > 0:
                continue
            try:
                metrics, _cotags = measure_and_harvest_scrape(cli, tag)
            except Exception as exc:
                log("source_tags", sid, "measure_fail", tag=tag, err=type(exc).__name__)
                continue
            if isinstance(metrics, dict):
                prev = rec if isinstance(rec, dict) else {}
                merged = dict(prev)
                merged.update(metrics)
                measurements[tag] = merged
        table[sid] = {
            "pile": pile,
            "lock": lock_from_pile(pile, measurements, _LOCK_N),
            "researched_at": iso_z(datetime.now(timezone.utc)),
        }
        write_json_atomic(source_tag_locks_path(cfg), table)
