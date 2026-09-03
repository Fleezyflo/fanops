# src/fanops/source_tags_scrape.py
"""Safari scrape + Graph confirm orchestration for per-source tag locks."""
from __future__ import annotations
from datetime import datetime, timezone

from fanops.hashtags import (_dedupe_norm, _norm, _num, _scrape_number, load_measurements,
                             lock_from_shortlist)
from fanops.ig_hashtag_scrape import (ScrapeUnavailable, measure_and_harvest_scrape,
                                     scrape_session_dead, search_hashtags_scrape)
from fanops.log import get_logger
from fanops.meta_graph import (GraphQuotaExhausted, GraphRefused, GraphThrottled,
                               GraphUnreachable, measure_and_harvest, resolve_hashtag)
from fanops.source_tags_sidecar import (_LOCK_N, _has_catalog, _in_progress, _researched,
                                        _restore_meters, _stamp_source, _union_lock_meters,
                                        _write_in_progress, _cache_lookup, _cached_metric,
                                        _note_graph_id, _note_graph_metric,
                                        graph_search_quota_status, load_source_tag_locks)
from fanops.source_tags_shortlist import _CATALOG_CAP, _RESEARCH_CAP, _prose
from fanops.source_tags_walk import (_advance_lock_client, _charge_lock_tag,
                                     _iter_lock_clients, _remember_dead_dump)
from fanops.timeutil import iso_z


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


def _scrape_already_done(search_needed, pending, pile, measurements) -> bool:
    """True when a prior Safari walk already finished. Graph quota is not membership."""
    if search_needed:
        return False
    if len(lock_from_shortlist(pile, measurements, _LOCK_N)) >= _LOCK_N:
        return True
    return not pending


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


def _graph_attach_then_stamp(cfg, table, sid, pile, verified, measurements, *,
                             catalog, catalog_at, resolve_fn, measure_fn, log) -> None:
    """Graph may attach graph_metric; Graph death still stamps. Graph does not reorder."""
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
                spent, limit, exhausted = graph_search_quota_status(cfg)
                if exhausted:
                    log("source_tags", sid, "quota_exhausted", level="error", err="ration",
                        spent=spent, limit=limit)
                    break
                hid = resolve_fn(cfg, tag) if resolve_fn is not None else resolve_hashtag(cfg, tag)
                _note_graph_id(cfg, tag, hid)
                if hid:
                    _measure_graph_tag(cfg, tag, hid, measurements, measure_fn=measure_fn)
            except GraphQuotaExhausted as exc:
                spent, limit, _exhausted = graph_search_quota_status(cfg)
                log("source_tags", sid, "quota_exhausted", level="error", err=_graph_message(exc),
                    spent=spent, limit=limit)
                break
            except (GraphThrottled, GraphRefused, GraphUnreachable) as exc:
                log("source_tags", sid, "no_graph", level="error", err=_graph_message(exc))
                break
    _stamp_source(cfg, table, sid, pile, lock_from_shortlist(pile, measurements, _LOCK_N),
                  measurements, catalog=catalog, catalog_at=catalog_at)


def ensure_source_lock(cfg, source, *, excerpt=None, client=None, research_fn=None,
                       open_client_fn=None, resolve_fn=None, measure_fn=None) -> bool:
    """LLM names, Safari scrape completes lock. Graph may attach; death still stamps.

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
    log = get_logger(cfg)
    prior = table.get(sid) if isinstance(table.get(sid), dict) else {}
    if _researched(table, sid) and not _in_progress(prior):
        return False
    pile_prior = prior.get("pile") if isinstance(prior.get("pile"), list) else None
    llm_names = _dedupe_norm(pile_prior)[:_RESEARCH_CAP] if pile_prior else []
    catalog: list[str] = []
    catalog_at = ""
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
    if _has_catalog(prior):
        catalog = _dedupe_norm(prior["catalog"])[:_CATALOG_CAP]
        catalog_at = prior["catalog_at"]
        llm_names = _dedupe_norm(pile_prior)[:_RESEARCH_CAP] if pile_prior else []
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
    scrape_complete = bool(llm_names) and (
        (not pending)
        or _scrape_already_done(False if not pending else search_needed, pending,
                                llm_names, measurements)
    )
    if scrape_complete and _has_catalog(prior):
        _stamp_source(cfg, table, sid, llm_names,
                      lock_from_shortlist(llm_names, measurements, _LOCK_N), measurements,
                      catalog=catalog, catalog_at=catalog_at)
        return False
    walk = _iter_lock_clients(cfg, client=client, open_client_fn=open_client_fn)
    first = next(walk, None)
    if first is None:
        log("source_tags", sid, "no_scrape")
        return False
    already: dict[str, int] = {}
    spare = iter(walk)
    current = first
    stagger = client is None
    tags_this_walk = 0
    if not _has_catalog(prior):
        if research_fn is None:
            log("source_tags", sid, "research_fail", level="error", err="research_empty")
            return True
        try:
            raw_names = research_fn(source, _prose(source, excerpt))
        except Exception as exc:
            log("source_tags", sid, "research_fail", level="error", err=type(exc).__name__)
            return True
        llm_names = _dedupe_norm(raw_names if isinstance(raw_names, list) else [])[:_RESEARCH_CAP]
        catalog = list(llm_names)
        catalog_at = iso_z(datetime.now(timezone.utc))
        if not llm_names:
            log("source_tags", sid, "research_fail", level="error", err="research_empty")
            return True
        verified = []
        pending = list(llm_names)
        search_needed = True
    while pending:
        if len(lock_from_shortlist(llm_names, measurements, _LOCK_N)) >= _LOCK_N:
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
                if scrape_session_dead(exc) or isinstance(exc, ScrapeUnavailable):
                    log("source_tags", sid, "stop_dead", err=type(exc).__name__,
                        user=str(getattr(current, "_fanops_scrape_user", "") or "")[:40])
                    _remember_dead_dump(cfg, current, exc)
                    break
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
                if scrape_session_dead(exc) or isinstance(exc, ScrapeUnavailable):
                    log("source_tags", sid, "stop_dead", tag=tag, err=type(exc).__name__,
                        user=str(getattr(current, "_fanops_scrape_user", "") or "")[:40])
                    _remember_dead_dump(cfg, current, exc)
                    break
                log("source_tags", sid, "measure_fail", tag=tag, err=type(exc).__name__)
                pending.pop(0)
                tags_this_walk += 1
                continue
            _apply_scrape_metrics(measurements, tag, metrics)
        _apply_cached_graph(cfg, measurements, tag)
        pending.pop(0)
        tags_this_walk += 1
    scrape_done = (not pending) or len(lock_from_shortlist(llm_names, measurements, _LOCK_N)) >= _LOCK_N
    if not scrape_done:
        log("source_tags", sid, "scrape_unfinished")
        if verified:
            _write_in_progress(cfg, table, sid, pile=llm_names, verified=verified,
                               measurements=measurements, remaining=pending,
                               catalog=catalog, catalog_at=catalog_at)
        return True
    _graph_attach_then_stamp(cfg, table, sid, llm_names, verified, measurements,
                             catalog=catalog, catalog_at=catalog_at,
                             resolve_fn=resolve_fn, measure_fn=measure_fn, log=log)
    return True
