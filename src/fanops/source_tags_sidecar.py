# src/fanops/source_tags_sidecar.py
"""Sidecar I/O for source tag locks and Graph tag cache."""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone

from fanops.controlio import write_json_atomic
from fanops.errors import fail_open
from fanops.hashtags import _norm, _num
from fanops.timeutil import iso_z

SOURCE_TAG_LOCKS_NAME = "source_tag_locks.json"
GRAPH_TAG_CACHE_NAME = "graph_hashtag_cache.json"
_LOCK_N = 12
_SEARCH_QUOTA = 30
_SEARCH_WINDOW_DAYS = 7
_METER_KEYS = ("play_count", "like_count", "media_count",
               "current_top_reel_play_max_7d", "top_reel_sample_n", "graph_metric")


def source_tag_locks_path(cfg):
    """Sidecar path. Not a Config field — callers must not add one."""
    return cfg.control / SOURCE_TAG_LOCKS_NAME


def graph_tag_cache_path(cfg):
    """Global Graph node-id + graph_metric cache. Not a Config field, not hashtags.json."""
    return cfg.control / GRAPH_TAG_CACHE_NAME


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


def load_graph_tag_cache(cfg) -> dict:
    """Read the Graph tag cache. Missing / corrupt / unreadable → {}. Never raises."""
    cache: dict = {}
    with fail_open("source_tags.graph_cache"):
        if cfg is None:
            return {}
        p = graph_tag_cache_path(cfg)
        if not p.exists():
            return {}
        raw = json.loads(p.read_text())
        cache = raw if isinstance(raw, dict) else {}
    return cache


def _parse_iso(raw) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _cache_tags(cache: dict) -> dict:
    tags = cache.get("tags") if isinstance(cache, dict) else None
    return tags if isinstance(tags, dict) else {}


def _unique_searches_in_window(cache: dict, now: datetime) -> int:
    cutoff = now - timedelta(days=_SEARCH_WINDOW_DAYS)
    seen: set[str] = set()
    searches = cache.get("searches") if isinstance(cache, dict) else None
    if isinstance(searches, list):
        for row in searches:
            if not isinstance(row, dict):
                continue
            raw = row.get("tag")
            tag = _norm(raw) if isinstance(raw, str) else ""
            ts = _parse_iso(row.get("at"))
            if tag and ts is not None and ts >= cutoff:
                seen.add(tag)
    for name, rec in _cache_tags(cache).items():
        if not isinstance(name, str) or not isinstance(rec, dict):
            continue
        tag = _norm(name)
        ts = _parse_iso(rec.get("resolved_at"))
        if tag and ts is not None and ts >= cutoff:
            seen.add(tag)
    return len(seen)


def graph_search_quota_status(cfg, *, now=None) -> tuple[int, int, bool]:
    """(unique IDs spent in 7d, limit 30, exhausted). File read only — no Graph HTTP."""
    now = now or datetime.now(timezone.utc)
    cache = load_graph_tag_cache(cfg)
    spent = _unique_searches_in_window(cache, now)
    exhausted = spent >= _SEARCH_QUOTA
    return spent, _SEARCH_QUOTA, exhausted


def _write_graph_cache(cfg, cache: dict) -> None:
    cache.pop("quota_exhausted_at", None)
    write_json_atomic(graph_tag_cache_path(cfg), cache)


def _cache_lookup(cfg, tag: str) -> dict | None:
    rec = _cache_tags(load_graph_tag_cache(cfg)).get(_norm(tag))
    return rec if isinstance(rec, dict) else None


def _cached_metric(rec) -> float | None:
    if not isinstance(rec, dict):
        return None
    v = _num(rec.get("graph_metric"))
    return v if v is not None and v > 0 else None


def _note_graph_id(cfg, tag: str, hid) -> None:
    n = _norm(tag)
    if not n:
        return
    now = datetime.now(timezone.utc)
    cache = load_graph_tag_cache(cfg)
    tags = dict(_cache_tags(cache))
    prev = tags.get(n)
    rec = dict(prev) if isinstance(prev, dict) else {}
    rec["id"] = hid if isinstance(hid, str) and hid else None
    rec["resolved_at"] = iso_z(now)
    tags[n] = rec
    cache["tags"] = tags
    searches = cache.get("searches")
    searches = list(searches) if isinstance(searches, list) else []
    cutoff = now - timedelta(days=_SEARCH_WINDOW_DAYS)
    already = False
    for row in searches:
        if not isinstance(row, dict):
            continue
        raw = row.get("tag")
        if (_norm(raw) if isinstance(raw, str) else "") != n:
            continue
        ts = _parse_iso(row.get("at"))
        if ts is not None and ts >= cutoff:
            already = True
            break
    if not already:
        searches.append({"tag": n, "at": iso_z(now)})
    cache["searches"] = searches
    _write_graph_cache(cfg, cache)


def _note_graph_metric(cfg, tag: str, metric) -> None:
    n = _norm(tag)
    v = _num(metric)
    if not n or v is None:
        return
    cache = load_graph_tag_cache(cfg)
    tags = dict(_cache_tags(cache))
    prev = tags.get(n)
    rec = dict(prev) if isinstance(prev, dict) else {}
    rec["graph_metric"] = v
    rec["measured_at"] = iso_z(datetime.now(timezone.utc))
    tags[n] = rec
    cache["tags"] = tags
    _write_graph_cache(cfg, cache)


def _snapshot_meters(measurements: dict, names: list[str]) -> dict:
    out: dict = {}
    for name in names:
        rec = measurements.get(name)
        if not isinstance(rec, dict):
            continue
        snap = {}
        for k in _METER_KEYS:
            v = rec.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                snap[k] = v
        if snap:
            out[name] = snap
    return out


def _restore_meters(measurements: dict, snap) -> None:
    if not isinstance(snap, dict):
        return
    for name, rec in snap.items():
        n = _norm(name) if isinstance(name, str) else ""
        if not n or not isinstance(rec, dict):
            continue
        prev = measurements.get(n)
        merged = dict(prev) if isinstance(prev, dict) else {}
        for k in _METER_KEYS:
            v = rec.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                merged[k] = v
        measurements[n] = merged


def _write_in_progress(cfg, table, sid, *, pile, verified, measurements, remaining,
                       catalog, catalog_at) -> None:
    prior = table.get(sid) if isinstance(table.get(sid), dict) else {}
    lock = prior.get("lock")
    row = {
        "pile": list(pile),
        "verified": list(verified),
        "measurements": _snapshot_meters(measurements, list(verified) or list(pile)),
        "remaining": list(remaining),
        "lock": list(lock) if isinstance(lock, list) else [],
        "catalog": list(catalog),
        "catalog_at": catalog_at,
    }
    at = prior.get("researched_at")
    if isinstance(at, str) and at.strip():
        row["researched_at"] = at
    table[sid] = row
    write_json_atomic(source_tag_locks_path(cfg), table)


def _researched(table, sid: str) -> bool:
    rec = table.get(sid)
    if not isinstance(rec, dict):
        return False
    at = rec.get("researched_at")
    return isinstance(at, str) and bool(at.strip())


def _has_catalog(rec) -> bool:
    if not isinstance(rec, dict):
        return False
    at = rec.get("catalog_at")
    if not isinstance(at, str) or not at.strip():
        return False
    return isinstance(rec.get("catalog"), list)


def _in_progress(rec) -> bool:
    return isinstance(rec, dict) and isinstance(rec.get("remaining"), list)


def _union_lock_meters(table: dict, measurements: dict) -> None:
    for rec in table.values():
        if isinstance(rec, dict):
            _restore_meters(measurements, rec.get("measurements"))


def _stamp_source(cfg, table, sid, pile, lock, measurements=None, *, catalog, catalog_at) -> None:
    row = {
        "pile": list(pile),
        "lock": list(lock),
        "researched_at": iso_z(datetime.now(timezone.utc)),
        "catalog": list(catalog),
        "catalog_at": catalog_at,
    }
    if isinstance(measurements, dict):
        snap = _snapshot_meters(measurements, list(lock) or list(pile))
        if snap:
            row["measurements"] = snap
    table[sid] = row
    write_json_atomic(source_tag_locks_path(cfg), table)


def _hydrate_stamp(cfg, table, sid, pile, lock, measurements=None, *, prior=None) -> None:
    """Write lock from used tags. Never sets researched_at (caption gate stays closed)."""
    prior = prior if isinstance(prior, dict) else {}
    row = {
        "pile": list(pile),
        "lock": list(lock),
        "hydrated_at": iso_z(datetime.now(timezone.utc)),
    }
    at = prior.get("researched_at")
    if isinstance(at, str) and at.strip():
        row["researched_at"] = at
    if isinstance(measurements, dict):
        snap = _snapshot_meters(measurements, list(lock) or list(pile))
        if snap:
            row["measurements"] = snap
    table[sid] = row
    write_json_atomic(source_tag_locks_path(cfg), table)
