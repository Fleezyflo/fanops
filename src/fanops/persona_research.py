# src/fanops/persona_research.py
"""Per-persona hashtag CORPUS research + live Graph discovery (extracted from personas.py, audit #6 —
behavior byte-identical). research_corpus is the budget-free offline re-rank of what we already know;
discover_corpus is the live co-occurrence harvest that finds tags we have never named, FAIL-OPEN to the
offline re-rank. Both are re-exported from fanops.personas — and discover_corpus MUST stay patchable at
`fanops.personas.discover_corpus` (tests monkeypatch it there; fanops_hashtags imports it lazily)."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from fanops.config import Config
from fanops.hashtags import _norm, _screen_content, _strip_banned, load_bans, load_store_reach
from fanops.hashtag_hygiene import is_curatable
from fanops.personas import Personas


_EVIDENCE_MAX_AGE_DAYS = 90       # older than this is stale, not evidence — expiry, so a dead measurement cannot curate forever


def research_corpus(cfg: Config, pid: str, *, limit: int = 8, now: datetime | None = None) -> list[str]:
    """B3/R4: propose hashtags this persona doesn't carry, from MEASURED EVIDENCE ONLY — never from the
    store menu. Budget-free (it reads what refresh_store already bought). Unknown id -> KeyError.

    R4 — THIS FUNCTION IS THE CUT THAT BREAKS THE CIRCULARITY, so the filter below is load-bearing, not
    defensive dressing. It used to propose from `vetted_menu(load_store(cfg))`: the whole store, ranked.
    But `fanops_hashtags._seed_tags` BUILDS the store out of every persona's corpus, so the store's tags
    are mostly the corpora echoed back — and `refresh_persona_corpus` fed these proposals straight into
    the corpus as auto entries. corpus -> store -> corpus, closed, with no external evidence anywhere in
    it and nothing in the shape of the data to reveal that. Measured on the live control dir 2026-07-16:
    the store was BYTE-IDENTICAL to `seeds + frozen floor` — 53 tags, 0 discovered, `reach: {}` — while
    every proposal it made looked like ranked research.

    The fix is structural, not a heuristic: a tag may be proposed ONLY if it carries real Graph evidence
    (`source == 'graph-reach'`, a `measured_at`, a positive reach) that is not expired. A corpus tag
    echoed into the store as an unmeasured SEED carries none, so it can never be proposed back — the edge
    is severed by the data model, not by a rule someone must remember. No evidence -> [] (honest silence:
    the correct answer when nothing has been measured is 'nothing', not a re-ranked mirror)."""
    from fanops.hashtags import load_store_evidence
    per = Personas.load(cfg).get(pid)
    if per is None:
        raise KeyError(pid)
    have = {_norm(t) for t in per.hashtag_corpus if isinstance(t, str)}
    ev = load_store_evidence(cfg)
    ranked = sorted((t for t in ev if _is_evidence(ev[t], now=now)), key=lambda t: ev[t]["reach"], reverse=True)
    return [t for t in ranked if t not in have and is_curatable(t)][:limit]


def _is_evidence(rec: dict, *, now: datetime | None = None) -> bool:
    """True when an evidence record is a real, unexpired Graph MEASUREMENT — the promotion gate's core
    predicate. Demands provenance (`source == 'graph-reach'`), a parseable `measured_at`, a positive
    reach, and freshness within _EVIDENCE_MAX_AGE_DAYS. `source: 'unknown'` (a legacy bare number whose
    provenance we genuinely do not know) FAILS by design — unprovenanced data must not curate, and the
    migration marks it honestly instead of inventing a source for it."""
    if not isinstance(rec, dict) or rec.get("source") != "graph-reach":
        return False
    try:
        if float(rec.get("reach") or 0) <= 0:
            return False
        ts = datetime.fromisoformat(rec["measured_at"])
    except (KeyError, TypeError, ValueError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - ts <= timedelta(days=_EVIDENCE_MAX_AGE_DAYS)


def discover_corpus(cfg: Config, pid: str, *, limit: int = 8, measure_k: int = 0, get=None,
                    offline_fallback: bool = True) -> list[dict]:
    """M3: LIVE per-persona discovery — the upgrade from research_corpus's re-rank-what-we-know to
    finding tags we have never named. Seeds the Graph co-occurrence harvest from the persona's category
    (its corpus + intake `genre`), DROPS what we already know (VETTED ∪ reach store ∪ corpus), and returns
    evidence-carrying proposals [{"tag","count","host_engagement",...}] reach-relevant first. FAIL-OPEN: no
    creds / nothing fresh / any Graph error -> today's offline research_corpus re-rank, wrapped as evidence-
    less {"tag": ...} dicts so the caller has ONE shape. measure_k defaults 0 (the free co-occurrence COUNT is
    the operator's evidence; per-tag reach stays the explicit 'Check reach' action) — the global refresh
    passes measure_k>0 to gate the menu on measured reach. Unknown id -> KeyError. (M3: tag_lean retired —
    the curated corpus is the seed flavor.) offline_fallback=False (S12 auto-refresh): return [] instead of
    the offline re-rank when the Graph path yields nothing."""
    from fanops.hashtags import load_store, VETTED
    from fanops.meta_graph import discover_candidates
    per = Personas.load(cfg).get(pid)
    if per is None:
        raise KeyError(pid)
    corpus = [_norm(t) for t in per.hashtag_corpus if isinstance(t, str)]
    genre_seeds = [_norm("#" + w) for w in (per.intake.get("genre") or "").split() if w.strip()]   # `or ""`: a hand-edited "genre": null must not seed "#none"
    seeds = list(dict.fromkeys(corpus + genre_seeds))
    store = load_store(cfg) or []
    known = set(VETTED) | set(store) | set(corpus)
    try:
        cands = discover_candidates(cfg, seeds, known=known, measure_k=measure_k, get=get)
    except Exception:                                    # any Graph/transport error -> offline fallback
        cands = []
    if cands:
        return cands[:limit]
    if not offline_fallback:
        return []
    return [{"tag": t} for t in research_corpus(cfg, pid, limit=limit)]   # FAIL-OPEN to the offline re-rank


def _persona_row(cfg: Config, pid: str) -> dict | None:
    from fanops.persona_store import _load_raw
    _, plist = _load_raw(cfg.personas_path)
    return next((d for d in plist if isinstance(d, dict) and d.get("id") == pid), None)


def _partition_corpus(corpus: list[str], meta: dict) -> tuple[list[str], list[str]]:
    pinned: list[str] = []; auto: list[str] = []; seen: set[str] = set()
    for t in corpus:
        n = _norm(t) if isinstance(t, str) else ""
        if not n or n in seen: continue
        seen.add(n)
        if _is_pinned(meta, n): pinned.append(n)
        else: auto.append(n)
    return pinned, auto


def _is_pinned(meta: dict, tag: str) -> bool:
    m = meta.get(tag) if isinstance(meta.get(tag), dict) else None
    if m is None: return True
    return (m.get("source") or "pinned") == "pinned"


def _reach_key(tag: str, cand_by_tag: dict[str, dict], store_reach: dict[str, float],
               meta: dict | None = None) -> float:
    c = cand_by_tag.get(tag)
    if c and c.get("measured_engagement") is not None:
        try: return float(c["measured_engagement"])
        except (TypeError, ValueError): pass
    if meta:
        m = meta.get(tag) if isinstance(meta.get(tag), dict) else None
        if m and m.get("reach") is not None:
            try: return float(m["reach"])
            except (TypeError, ValueError): pass
    r = store_reach.get(tag)
    return float(r) if r is not None else -1.0


def refresh_persona_corpus(cfg: Config, pid: str, *, get=None, now=None) -> dict:
    """S12: one persona's automated corpus refresh — pinned tags preserved, auto slots filled/pruned to
    cfg.corpus_target by reach. Fail-open ladder on budget/creds; unknown id -> {changed: False}."""
    from fanops.meta_graph import budget_remaining
    from fanops.persona_store import apply_auto_corpus
    per = Personas.load(cfg).get(pid)
    if per is None:
        return {"changed": False, "reason": "unknown_persona"}
    row = _persona_row(cfg, pid) or {}
    meta = row.get("hashtag_corpus_meta") if isinstance(row.get("hashtag_corpus_meta"), dict) else {}
    corpus = [_norm(t) for t in (per.hashtag_corpus or []) if isinstance(t, str) and _norm(t)]
    pinned, auto = _partition_corpus(corpus, meta)
    bans = load_bans(cfg)                        # U11: the operator's global deny-list — ban BEATS pin, so a banned
    pinned = _strip_banned(pinned, bans)         # tag is dropped from `final` even when pinned (last explicit negative wins)
    auto = _strip_banned(auto, bans)             # (and a banned auto tag never survives the refresh); bans empty -> byte-identical
    target = cfg.corpus_target
    auto_slots = max(0, target - len(pinned))
    budget = budget_remaining(cfg, now=now)
    if budget is None:
        return {"changed": False, "reason": "budget_unreadable"}
    if budget == 0:
        return {"changed": False, "reason": "budget_exhausted"}
    have = set(corpus)
    corpus_has_ban = any(_norm(t) in bans for t in corpus)   # U11: a banned tag already in the corpus must be PURGED even when the corpus is at/over target with no creds (else the ban never takes)
    store_reach = load_store_reach(cfg)
    cands: list[dict] = []
    if cfg.meta_graph_token and cfg.meta_ig_user_id:
        gap = max(0, target - len(corpus))
        measure_k = min(gap + len(auto), target)
        cands = discover_corpus(cfg, pid, limit=max(auto_slots, gap) + len(auto), measure_k=measure_k,
                                get=get, offline_fallback=False)
    elif len(corpus) < target:
        # R4: was `research_corpus(...)` -> the store, re-ranked, promoted straight into the corpus as AUTO
        # entries. Since _seed_tags BUILDS the store from the corpora, that closed the loop and let an
        # unmeasured echo of our own curation return as "research". research_corpus is now evidence-only, so
        # this path yields tags that carry real Graph measurement or nothing at all. Kept (not deleted) so a
        # persona under target still fills from GENUINE evidence bought by an earlier funded refresh.
        cands = [{"tag": t} for t in research_corpus(cfg, pid, limit=target - len(corpus), now=now)]
    elif corpus_has_ban:
        cands = []          # nothing to ADD, but fall through so `final` (ban-stripped) is written -> the ban is purged
    else:
        return {"changed": False}
    screened = _screen_content([c["tag"] for c in cands if isinstance(c, dict) and c.get("tag")], cfg)
    # R4 promotion gate: a discovered tag may only become CURATED data if it is structurally clean. Junk that
    # reaches the corpus is near-permanent (it seeds the store, biases selection, and a human has to notice it
    # to remove it) — `#fypppppppppp…` got in exactly this way and shipped live. Deterministic, so promotion is
    # reviewable rather than a matter of taste at 3am on a daemon tick.
    screened = [t for t in screened if is_curatable(t)]
    cand_by_tag = {_norm(c["tag"]): c for c in cands if isinstance(c, dict) and _norm(c.get("tag", ""))}
    novel = _strip_banned([t for t in screened if _norm(t) not in have], bans)   # U11: a banned discovered tag never re-enters (store refresh must not resurrect a ban)
    pool = _strip_banned(list(dict.fromkeys(auto + novel)), bans)                 # belt-and-suspenders: no banned tag reaches new_auto/final
    pool.sort(key=lambda t: _reach_key(t, cand_by_tag, store_reach, meta), reverse=True)
    new_auto = pool[:auto_slots] if auto_slots else []
    final = pinned + new_auto
    if final == corpus:
        return {"changed": False}
    added = [t for t in final if t not in set(corpus)]
    removed = [t for t in corpus if t not in set(final)]
    now_iso = (now.isoformat() if isinstance(now, datetime) else None) or datetime.now(timezone.utc).isoformat()
    new_meta: dict[str, dict] = {}
    for t in new_auto:
        c = cand_by_tag.get(t, {})
        reach = c.get("measured_engagement")
        if reach is None: reach = store_reach.get(t)
        new_meta[t] = {"source": "auto", "reach": reach, "added": now_iso}
    apply_auto_corpus(cfg, pid, tags=final, meta=new_meta)
    return {"changed": True, "added": added, "removed": removed}


def refresh_corpora_if_due(cfg: Config, *, max_age_s: int = 43200, get=None, now=None) -> dict:
    """S12: constant-update hook the run loop calls — refresh every persona corpus at most once per
    max_age_s (default 12h), throttled by .corpora_refresh.json mtime. Gated on cfg.corpus_auto. FAIL-OPEN:
    never raises."""
    import time
    from fanops.controlio import write_json_atomic
    from fanops.errors import ControlFileError
    if not cfg.corpus_auto:
        return {"refreshed": False, "reason": "disabled"}
    marker = cfg.control / ".corpora_refresh.json"
    try:
        if marker.exists() and (time.time() - marker.stat().st_mtime) < max_age_s:
            return {"refreshed": False, "reason": "fresh"}
        try:
            personas = Personas.load(cfg).all()
        except ControlFileError as e:
            return {"refreshed": False, "aborted": "corrupt_personas", "reason": str(e)}
        changed = 0; added_n = 0; removed_n = 0
        from fanops.errors import fail_open
        for per in personas:
            r = {"changed": False}
            with fail_open(f"persona_research.refresh_corpora.{per.id}"):
                r = refresh_persona_corpus(cfg, per.id, get=get, now=now)
            if r.get("changed"):
                changed += 1; added_n += len(r.get("added") or []); removed_n += len(r.get("removed") or [])
        write_json_atomic(marker, {"ts": datetime.now(timezone.utc).isoformat(), "personas": len(personas),
                                   "changed": changed, "added": added_n, "removed": removed_n})
        return {"refreshed": True, "personas": len(personas), "changed": changed, "added": added_n, "removed": removed_n}
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("corpus", "-", "refresh_error", err=f"{type(exc).__name__}: {str(exc)[:120]}")
        return {"refreshed": False, "reason": f"error: {str(exc)[:120]}"}
