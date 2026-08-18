# src/fanops/source_tags.py
"""Source hashtag lock producer.

Source-entry run: whisper, scrape, and Graph are required. LLM names THIS video
(mild→provocative). Search verifies the exact name (no siblings on the pile).
Every name is dual-measured; lock is the first 15 that clear both meters, LLM order.
Caption / regen / recaption read the sidecar; they do not produce it.
Sidecar is cfg.control / source_tag_locks.json — not a Config field, not hashtags.json.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

from fanops.controlio import write_json_atomic
from fanops.errors import fail_open
from fanops.hashtags import _dedupe_norm, _norm, _num, load_measurements, lock_from_pile
from fanops.ig_hashtag_scrape import (ScrapeUnavailable, measure_and_harvest_scrape, open_client,
                                     scrape_session_dead, search_hashtags_scrape)
from fanops.log import get_logger
from fanops.meta_graph import (GraphRefused, GraphThrottled, GraphUnreachable,
                               measure_and_harvest, resolve_hashtag)
from fanops.timeutil import iso_z

SOURCE_TAG_LOCKS_NAME = "source_tag_locks.json"
_LOCK_N = 15
_RESEARCH_CAP = 20
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


def _iter_lock_clients(cfg, *, client, open_client_fn):
    """Yield clients to try for this lock, one unfrozen session at a time."""
    if client is not None:
        yield client
        return
    opener = open_client_fn or open_client
    from fanops.fanops_hashtags import _healthy_scrape_users
    now = datetime.now(timezone.utc)
    peers = _healthy_scrape_users(cfg, now, require_budget_room=False)
    if not peers:
        try:
            cli = _call_opener(opener, cfg)
        except ScrapeUnavailable:
            return
        if cli is not None:
            yield cli
        return
    for user in peers:
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
    if isinstance(exc, GraphRefused):
        return (exc.message or str(exc))[:160]
    if isinstance(exc, GraphUnreachable):
        return (exc.reason or str(exc))[:160]
    return str(exc)[:160]


def _search_verify(cli, names: list[str]) -> tuple[list[str] | None, Exception | None]:
    """Exact-name verify. Returns (verified, dead_exc). dead_exc set → rotate."""
    verified: list[str] = []
    seen: set[str] = set()
    for q in names:
        try:
            hits = search_hashtags_scrape(cli, q)
        except Exception as exc:
            if scrape_session_dead(exc):
                return None, exc
            raise
        want = _norm(q)
        if any(_norm(hit.get("name") if isinstance(hit, dict) else "") == want for hit in hits):
            if want and want not in seen:
                seen.add(want)
                verified.append(want)
    return verified, None


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


def ensure_source_lock(cfg, source, *, excerpt=None, client=None, research_fn=None,
                       open_client_fn=None, resolve_fn=None, measure_fn=None) -> None:
    """Research → exact-name verify → dual scrape+Graph measure → lock. Abort source on tool death."""
    sid = str(getattr(source, "id", "") or "")
    if not sid:
        return
    table = load_source_tag_locks(cfg)
    if _researched(table, sid):
        return
    log = get_logger(cfg)
    walk = _iter_lock_clients(cfg, client=client, open_client_fn=open_client_fn)
    first = next(walk, None)
    if first is None:
        log("source_tags", sid, "no_scrape", level="error")
        return
    if not _graph_ready(cfg, resolve_fn):
        log("source_tags", sid, "no_graph", level="error")
        return
    try:
        raw_names = (research_fn or _default_research)(source, _prose(source, excerpt))
    except Exception as exc:
        log("source_tags", sid, "research_fail", level="error", err=type(exc).__name__)
        return
    llm_names = _dedupe_norm(raw_names if isinstance(raw_names, list) else [])[:_RESEARCH_CAP]
    if not llm_names:
        log("source_tags", sid, "research_fail", level="error", err="research_empty")
        return
    verified: list[str] | None = None
    cli = None

    def _clients():
        yield first
        yield from walk

    for cand in _clients():
        got, dead_exc = _search_verify(cand, llm_names)
        if dead_exc is not None:
            log("source_tags", sid, "rotate_dead", err=type(dead_exc).__name__,
                user=str(getattr(cand, "_fanops_scrape_user", "") or "")[:40])
            _remember_dead_dump(cfg, cand, dead_exc)
            continue
        verified = got
        cli = cand
        break
    if verified is None or cli is None:
        log("source_tags", sid, "no_scrape", level="error", err="all_sessions_dead")
        return
    measurements = dict(load_measurements(cfg))
    remaining = list(verified)
    spare = iter(walk)
    current = cli
    while remaining:
        tag = remaining[0]
        try:
            metrics, _cotags = measure_and_harvest_scrape(current, tag)
        except Exception as exc:
            if scrape_session_dead(exc):
                log("source_tags", sid, "rotate_dead", tag=tag, err=type(exc).__name__,
                    user=str(getattr(current, "_fanops_scrape_user", "") or "")[:40])
                _remember_dead_dump(cfg, current, exc)
                nxt = next(spare, None)
                if nxt is None:
                    log("source_tags", sid, "no_scrape", level="error", err="measure_dead")
                    return
                current = nxt
                continue
            log("source_tags", sid, "measure_fail", tag=tag, err=type(exc).__name__)
            remaining.pop(0)
            continue
        _apply_scrape_metrics(measurements, tag, metrics)
        try:
            hid = resolve_fn(cfg, tag) if resolve_fn is not None else resolve_hashtag(cfg, tag)
            if hid:
                raw = measure_fn(cfg, hid) if measure_fn is not None else measure_and_harvest(cfg, hid)
                gmetric = raw[0] if isinstance(raw, tuple) and raw else raw
                _set_graph_metric(measurements, tag, gmetric)
        except GraphThrottled as exc:
            log("source_tags", sid, "no_graph", level="error", err=_graph_message(exc))
            return
        except (GraphRefused, GraphUnreachable) as exc:
            log("source_tags", sid, "no_graph", level="error", err=_graph_message(exc))
            return
        remaining.pop(0)
    lock = lock_from_pile(verified, measurements, _LOCK_N)
    table[sid] = {
        "pile": llm_names,
        "lock": lock,
        "researched_at": iso_z(datetime.now(timezone.utc)),
    }
    write_json_atomic(source_tag_locks_path(cfg), table)
