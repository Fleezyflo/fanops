# src/fanops/source_tags.py
"""Source hashtag lock producer.

Safari scrape completes the per-source lock. Graph may cache/confirm/rank; Graph
never vetoes membership or withholds researched_at after scrape finished. Empty
lock = scrape finished with zero admits. Caption waits on researched_at.

LLM names THIS video (mild→provocative). Search verifies the exact name (no
siblings on the pile). Lock is (1) this source's already-used tags that have
scrape meters — not the persona store ∪ corpus — then (2) scrape-admitted pile
names, Graph-present first, LLM order within tier, cap 15. Sidecar is
cfg.control / source_tag_locks.json — not a Config field, not hashtags.json.

Graph node id + graph_metric cache by tag name lives in a dedicated sidecar
(graph_hashtag_cache.json). Never mix with scrape `graph_id` in hashtags.json.
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fanops.controlio import write_json_atomic
from fanops.errors import fail_open
from fanops.hashtags import (_dedupe_norm, _norm, _num, _scrape_number,
                             load_measurements, lock_from_pile, play_rank_key)
from fanops.ig_hashtag_scrape import (ScrapeUnavailable, measure_and_harvest_scrape,
                                     scrape_session_dead, search_hashtags_scrape)
from fanops.ig_web_scrape import open_web_session
from fanops.log import get_logger
from fanops.meta_graph import (GraphQuotaExhausted, GraphRefused, GraphThrottled,
                               GraphUnreachable, measure_and_harvest, resolve_hashtag)
from fanops.timeutil import iso_z

SOURCE_TAG_LOCKS_NAME = "source_tag_locks.json"
GRAPH_TAG_CACHE_NAME = "graph_hashtag_cache.json"
_LOCK_N = 15
_RESEARCH_CAP = 20
_SEARCH_QUOTA = 30
_SEARCH_WINDOW_DAYS = 7
_METER_KEYS = ("play_count", "like_count", "media_count",
               "current_top_reel_play_max_7d", "top_reel_sample_n", "graph_metric")
_RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {"names": {"type": "array", "items": {"type": "string"}}},
    "required": ["names"],
}


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


def _quota_exhausted_unexpired(cache: dict, now: datetime) -> bool:
    ts = _parse_iso(cache.get("quota_exhausted_at") if isinstance(cache, dict) else None)
    if ts is None:
        return False
    return now - ts < timedelta(days=_SEARCH_WINDOW_DAYS)


def graph_search_quota_status(cfg, *, now=None) -> tuple[int, int, bool]:
    """(unique IDs spent in 7d, limit 30, exhausted). File read only — no Graph HTTP."""
    now = now or datetime.now(timezone.utc)
    cache = load_graph_tag_cache(cfg)
    spent = _unique_searches_in_window(cache, now)
    exhausted = spent >= _SEARCH_QUOTA or _quota_exhausted_unexpired(cache, now)
    return spent, _SEARCH_QUOTA, exhausted


def _write_graph_cache(cfg, cache: dict) -> None:
    write_json_atomic(graph_tag_cache_path(cfg), cache)


def _mark_quota_exhausted(cfg) -> None:
    cache = load_graph_tag_cache(cfg)
    cache["quota_exhausted_at"] = iso_z(datetime.now(timezone.utc))
    _write_graph_cache(cfg, cache)


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


def _write_in_progress(cfg, table, sid, *, pile, verified, measurements, remaining) -> None:
    table[sid] = {
        "pile": list(pile),
        "verified": list(verified),
        "measurements": _snapshot_meters(measurements, list(verified) or list(pile)),
        "remaining": list(remaining),
        "lock": [],
    }
    write_json_atomic(source_tag_locks_path(cfg), table)


def _unsearched_remaining(verified, remaining) -> list[str]:
    """Pile names not yet verified. leftover remaining==verified is not unfinished."""
    vset = set(verified)
    out: list[str] = []
    if not isinstance(remaining, list):
        return out
    for raw in remaining:
        n = _norm(raw) if isinstance(raw, str) else ""
        if n and n not in vset and n not in out:
            out.append(n)
    return out


def _scrape_already_done(search_needed, pending, verified, measurements) -> bool:
    """True when a prior Safari walk already finished. Graph quota is not membership."""
    if search_needed:
        return False
    if len(lock_from_pile(verified, measurements, _LOCK_N)) >= _LOCK_N:
        return True
    return not pending


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


def _default_research(source, excerpt) -> list[str]:
    from fanops.llm import claude_json_meta
    from fanops.models import source_display_title
    raw_title = getattr(source, "title", None)
    if isinstance(raw_title, str) and raw_title.strip():
        title = raw_title.strip()
    else:
        title = source_display_title(source)
    language = getattr(source, "language", None) or ""
    prompt = (
        "Propose Instagram hashtag names for THIS video only.\n"
        "Range from mild to provocative. Do not name sibling tracks or other videos.\n"
        f"title: {title}\n"
        f"language: {language}\n"
        f"transcript: {excerpt or ''}\n"
        "Return about 20 names. Names only."
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


def _call_opener(opener, cfg, user=None):
    """Call opener(cfg, user=...) when walking, else opener(cfg). Test openers take user=."""
    if user is not None:
        return opener(cfg, user=user)
    return opener(cfg)


def _remember_dead_dump(cfg, client, exc) -> None:
    """Freeze this identity so the next source does not re-hit a rejected dump."""
    user = getattr(client, "_fanops_scrape_user", None)
    if not isinstance(user, str) or not user:
        return
    from fanops.fanops_hashtags import _persist_cooldown
    _persist_cooldown(cfg, datetime.now(timezone.utc), reason=type(exc).__name__, user=user)


def _iter_lock_clients(cfg, *, client, open_client_fn, now=None):
    """Yield clients to try for this lock. Empty picker → stop (no opener(cfg) fallthrough)."""
    if client is not None:
        yield client
        return
    opener = open_client_fn or open_web_session
    from fanops.ig_web_scrape import _lock_web_users
    now = now or datetime.now(timezone.utc)
    for user in _lock_web_users(cfg, now):
        try:
            cli = _call_opener(opener, cfg, user=user)
        except ScrapeUnavailable:
            continue
        if cli is not None:
            if not getattr(cli, "_fanops_scrape_user", None):
                setattr(cli, "_fanops_scrape_user", user)
            yield cli


def _graph_ready(cfg, resolve_fn) -> bool:
    if resolve_fn is not None:
        return True
    return bool(getattr(cfg, "meta_graph_token", None) and getattr(cfg, "meta_ig_user_id", None))


def _graph_message(exc) -> str:
    if isinstance(exc, (GraphRefused, GraphQuotaExhausted)):
        return (exc.message or str(exc))[:160]
    if isinstance(exc, GraphUnreachable):
        return (exc.reason or str(exc))[:160]
    return str(exc)[:160]


def _lock_identity(cli) -> str | None:
    user = getattr(cli, "_fanops_scrape_user", None)
    return user if isinstance(user, str) and user else None


def _advance_lock_client(cfg, current, spare, already):
    """Next client with remaining try_cap room, or None."""
    from fanops.fanops_hashtags import _user_attempt_room
    cand = current
    while cand is not None:
        user = _lock_identity(cand)
        if _user_attempt_room(cfg, user, already=already.get(user or "", 0)) > 0:
            return cand
        cand = next(spare, None)
    return None


def _charge_lock_tag(cfg, cli, already) -> None:
    """Count this tag against try_cap. Wire spend is charged per live XHR in IgWebSession._json."""
    key = _lock_identity(cli) or ""
    already[key] = already.get(key, 0) + 1


def _graph_confirmed_n(names, measurements) -> int:
    recs = measurements if isinstance(measurements, dict) else {}
    n = 0
    for name in names:
        rec = recs.get(name)
        if _scrape_number(rec) is None:
            continue
        gm = _num(rec.get("graph_metric"))
        if gm is not None and gm > 0:
            n += 1
    return n


def _exact_hit(hits, want: str) -> bool:
    return any(_norm(hit.get("name") if isinstance(hit, dict) else "") == want for hit in hits)


def _apply_scrape_metrics(measurements: dict, tag: str, metrics) -> None:
    if not isinstance(metrics, dict):
        return
    prev = measurements.get(tag)
    merged = dict(prev) if isinstance(prev, dict) else {}
    merged.update(metrics)
    measurements[tag] = merged


def _set_graph_metric(measurements: dict, tag: str, metric) -> None:
    rec = measurements.get(tag)
    rec = dict(rec) if isinstance(rec, dict) else {}
    v = _num(metric)
    if v is not None and v > 0:
        rec["graph_metric"] = v
    measurements[tag] = rec


def _apply_cached_graph(cfg, measurements: dict, tag: str) -> None:
    gm = _cached_metric(_cache_lookup(cfg, tag))
    if gm is not None:
        _set_graph_metric(measurements, tag, gm)


def _measure_graph_tag(cfg, tag, hid, measurements, *, measure_fn) -> None:
    raw = measure_fn(cfg, hid) if measure_fn is not None else measure_and_harvest(cfg, hid)
    gmetric = raw[0] if isinstance(raw, tuple) and raw else raw
    _set_graph_metric(measurements, tag, gmetric)
    _note_graph_metric(cfg, tag, gmetric)


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


def _union_lock_meters(table: dict, measurements: dict) -> None:
    for rec in table.values():
        if isinstance(rec, dict):
            _restore_meters(measurements, rec.get("measurements"))


def known_lock(names, measurements, used, n=15, keep=None) -> list[str]:
    """This-source used∩measured (play then 7d reel), then keep, then scrape-admitted pile."""
    seen: set[str] = set()
    out: list[str] = []
    recs = measurements if isinstance(measurements, dict) else {}
    used_n = [t for t in _dedupe_norm(used) if _scrape_number(recs.get(t)) is not None]
    used_n.sort(key=lambda t: play_rank_key(t, recs.get(t)))
    for t in used_n:
        if t not in seen:
            seen.add(t)
            out.append(t)
    for t in _dedupe_norm(keep):
        if t not in seen:
            seen.add(t)
            out.append(t)
    for t in lock_from_pile(names, recs, n):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _stamp_source(cfg, table, sid, pile, lock, measurements=None) -> None:
    row = {
        "pile": list(pile),
        "lock": list(lock),
        "researched_at": iso_z(datetime.now(timezone.utc)),
    }
    if isinstance(measurements, dict):
        snap = _snapshot_meters(measurements, list(lock) or list(pile))
        if snap:
            row["measurements"] = snap
    table[sid] = row
    write_json_atomic(source_tag_locks_path(cfg), table)


def hydrate_locks_from_known(cfg, led) -> int:
    """Stamp/merge locks from this-source used tags that already have scrape meters.

    Zero network. Never the persona 80-pile. Does not recaption. Returns rows written.
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
        if _researched(table, sid) and list(prior.get("lock") or []) == lock:
            continue
        _stamp_source(cfg, table, sid, pile, lock, measurements)
        n += 1
    return n


def _rank_then_stamp(cfg, table, sid, pile, verified, measurements, *,
                     resolve_fn, measure_fn, log) -> None:
    """Graph ranks scrape admits when it can; Graph death still stamps."""
    if _graph_ready(cfg, resolve_fn):
        for tag in verified:
            if _graph_confirmed_n(verified, measurements) >= _LOCK_N:
                break
            if _scrape_number(measurements.get(tag)) is None:
                continue
            cached = _cache_lookup(cfg, tag)
            gm = _cached_metric(cached)
            if gm is not None:
                _set_graph_metric(measurements, tag, gm)
                continue
            try:
                if isinstance(cached, dict) and "id" in cached:
                    hid = cached.get("id")
                    if not (isinstance(hid, str) and hid):
                        continue
                    _measure_graph_tag(cfg, tag, hid, measurements, measure_fn=measure_fn)
                    continue
                if graph_search_quota_status(cfg)[2]:
                    _mark_quota_exhausted(cfg)
                    log("source_tags", sid, "quota_exhausted", level="error", err="ration",
                        spent=graph_search_quota_status(cfg)[0], limit=_SEARCH_QUOTA)
                    break
                hid = resolve_fn(cfg, tag) if resolve_fn is not None else resolve_hashtag(cfg, tag)
                _note_graph_id(cfg, tag, hid)
                if hid:
                    _measure_graph_tag(cfg, tag, hid, measurements, measure_fn=measure_fn)
            except GraphQuotaExhausted as exc:
                _mark_quota_exhausted(cfg)
                log("source_tags", sid, "quota_exhausted", level="error", err=_graph_message(exc),
                    spent=graph_search_quota_status(cfg)[0], limit=_SEARCH_QUOTA)
                break
            except (GraphThrottled, GraphRefused, GraphUnreachable) as exc:
                log("source_tags", sid, "no_graph", level="error", err=_graph_message(exc))
                break
    _stamp_source(cfg, table, sid, pile, lock_from_pile(verified, measurements, _LOCK_N),
                  measurements)


def ensure_source_lock(cfg, source, *, excerpt=None, client=None, research_fn=None,
                       open_client_fn=None, resolve_fn=None, measure_fn=None) -> bool:
    """Research → Safari scrape completes lock. Graph ranks; Graph death still stamps.

    Charge + rotate via try_cap. A tick with no injected client does one tag
    (instagrapi: delay_range on each XHR, spread work). All peers at cap / dead /
    missing → no researched_at unless a prior walk already finished. Empty finished
    scrape → lock [] + researched_at. Graph quota never withholds a finished scrape.
    Returns True when this call used a scrape client (one live attempt).
    """
    sid = str(getattr(source, "id", "") or "")
    if not sid:
        return False
    table = load_source_tag_locks(cfg)
    if _researched(table, sid):
        return False
    log = get_logger(cfg)
    prior = table.get(sid) if isinstance(table.get(sid), dict) else {}
    pile_prior = prior.get("pile") if isinstance(prior.get("pile"), list) else None
    llm_names = _dedupe_norm(pile_prior)[:_RESEARCH_CAP] if pile_prior else []
    measurements = dict(load_measurements(cfg))
    _restore_meters(measurements, prior.get("measurements"))
    verified_prior = prior.get("verified") if isinstance(prior.get("verified"), list) else None
    if verified_prior:
        verified = _dedupe_norm(verified_prior)
        pending = [t for t in verified if _scrape_number(measurements.get(t)) is None]
        extra = _unsearched_remaining(verified, prior.get("remaining"))
        if extra:
            pending.extend(extra)
            search_needed = True
        else:
            search_needed = False
    else:
        verified = []
        pending = list(llm_names)
        search_needed = True
    for tag in verified:
        _apply_cached_graph(cfg, measurements, tag)
    _union_lock_meters(table, measurements)
    still: list[str] = []
    for raw in pending:
        tag = _norm(raw) if isinstance(raw, str) else ""
        if tag and _scrape_number(measurements.get(tag)) is not None:
            if tag not in verified:
                verified.append(tag)
            _apply_cached_graph(cfg, measurements, tag)
        elif tag:
            still.append(tag)
    pending = still
    if llm_names and (
        (not pending)
        or _scrape_already_done(False if not pending else search_needed, pending,
                                verified, measurements)
    ):
        _stamp_source(cfg, table, sid, llm_names, lock_from_pile(verified, measurements, _LOCK_N),
                      measurements)
        return False
    walk = _iter_lock_clients(cfg, client=client, open_client_fn=open_client_fn)
    first = next(walk, None)
    if first is None:
        log("source_tags", sid, "no_scrape")
        return False
    if not llm_names:
        try:
            raw_names = (research_fn or _default_research)(source, _prose(source, excerpt))
        except Exception as exc:
            log("source_tags", sid, "research_fail", level="error", err=type(exc).__name__)
            return True
        llm_names = _dedupe_norm(raw_names if isinstance(raw_names, list) else [])[:_RESEARCH_CAP]
        if not llm_names:
            log("source_tags", sid, "research_fail", level="error", err="research_empty")
            return True
        pending = list(llm_names)
        search_needed = True
    already: dict[str, int] = {}
    spare = iter(walk)
    current = first
    stagger = client is None
    tags_this_walk = 0
    while pending:
        if len(lock_from_pile(verified, measurements, _LOCK_N)) >= _LOCK_N:
            break
        raw = pending[0]
        tag = _norm(raw) if isinstance(raw, str) else ""
        if tag and _scrape_number(measurements.get(tag)) is not None:
            if tag not in verified:
                verified.append(tag)
            _apply_cached_graph(cfg, measurements, tag)
            pending.pop(0)
            continue
        if stagger and tags_this_walk >= 1:
            break
        current = _advance_lock_client(cfg, current, spare, already)
        if current is None:
            break
        _charge_lock_tag(cfg, current, already)
        if search_needed and tag and tag not in verified:
            try:
                hits = search_hashtags_scrape(current, raw)
            except Exception as exc:
                if scrape_session_dead(exc):
                    log("source_tags", sid, "rotate_dead", err=type(exc).__name__,
                        user=str(getattr(current, "_fanops_scrape_user", "") or "")[:40])
                    _remember_dead_dump(cfg, current, exc)
                    current = next(spare, None)
                    continue
                raise
            if not _exact_hit(hits, tag):
                pending.pop(0)
                tags_this_walk += 1
                continue
            verified.append(tag)
        if tag and _scrape_number(measurements.get(tag)) is None:
            try:
                metrics, _cotags = measure_and_harvest_scrape(current, tag)
            except Exception as exc:
                if scrape_session_dead(exc):
                    log("source_tags", sid, "rotate_dead", tag=tag, err=type(exc).__name__,
                        user=str(getattr(current, "_fanops_scrape_user", "") or "")[:40])
                    _remember_dead_dump(cfg, current, exc)
                    current = next(spare, None)
                    continue
                log("source_tags", sid, "measure_fail", tag=tag, err=type(exc).__name__)
                pending.pop(0)
                tags_this_walk += 1
                continue
            _apply_scrape_metrics(measurements, tag, metrics)
        _apply_cached_graph(cfg, measurements, tag)
        pending.pop(0)
        tags_this_walk += 1
    scrape_done = (not pending) or len(lock_from_pile(verified, measurements, _LOCK_N)) >= _LOCK_N
    if not scrape_done:
        log("source_tags", sid, "scrape_unfinished")
        if verified:
            _write_in_progress(cfg, table, sid, pile=llm_names, verified=verified,
                               measurements=measurements, remaining=pending)
        return True
    _rank_then_stamp(cfg, table, sid, llm_names, verified, measurements,
                     resolve_fn=resolve_fn, measure_fn=measure_fn, log=log)
    return True


def _state_value(source) -> str:
    state = getattr(source, "state", None)
    return str(getattr(state, "value", state) or "")


def lock_ready_sources(cfg, *, client=None, research_fn=None, open_client_fn=None,
                       resolve_fn=None, measure_fn=None) -> None:
    """After produce, before reduce. One source per real lock attempt. Never raises.

    Missing whisper JSON on a produce-eligible source logs no_transcript and continues
    (must not starve a later ready source). Graph quota does not skip a source —
    leftover scrape-complete rows stamp; unfinished scrape waits for a Safari seat.
    A source that cannot progress (no seat) does not consume the one-attempt slot.
    Used∩measured hydrate is zero-network and may stamp many sources in one tick.
    """
    log = get_logger(cfg)
    try:
        from fanops.ledger import Ledger
        led = Ledger.load(cfg)
        hydrate_locks_from_known(cfg, led)
        table = load_source_tag_locks(cfg)
        for source in led.sources.values():
            if getattr(source, "origin_kind", "native") == "third_party":
                continue
            sid = str(getattr(source, "id", "") or "")
            if not sid or _researched(table, sid):
                continue
            jp = _transcript_json_path(cfg, source)
            has_json = bool(jp and jp.exists())
            if not has_json:
                if _state_value(source) in ("pending", "discovered", "retired"):
                    continue
                log("source_tags", sid, "no_transcript", level="error")
                continue
            excerpt = _transcript_file_prose(cfg, source) or _prose(source)
            walked = False
            try:
                walked = bool(ensure_source_lock(cfg, source, excerpt=excerpt, client=client,
                                                 research_fn=research_fn, open_client_fn=open_client_fn,
                                                 resolve_fn=resolve_fn, measure_fn=measure_fn))
            except Exception as exc:
                log("source_tags", sid, "error", level="error",
                    err=f"{type(exc).__name__}: {exc}"[:160])
                return
            table = load_source_tag_locks(cfg)
            if _researched(table, sid) or walked:
                return
    except Exception as exc:
        log("source_tags", "-", "error", level="error",
            err=f"{type(exc).__name__}: {exc}"[:160])
