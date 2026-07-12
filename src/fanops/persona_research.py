# src/fanops/persona_research.py
"""Per-persona hashtag CORPUS research + live Graph discovery (extracted from personas.py, audit #6 —
behavior byte-identical). research_corpus is the budget-free offline re-rank of what we already know;
discover_corpus is the live co-occurrence harvest that finds tags we have never named, FAIL-OPEN to the
offline re-rank. Both are re-exported from fanops.personas — and discover_corpus MUST stay patchable at
`fanops.personas.discover_corpus` (tests monkeypatch it there; fanops_hashtags imports it lazily)."""
from __future__ import annotations
from datetime import datetime, timezone
from fanops.config import Config
from fanops.hashtags import _norm, _screen_content, load_store_reach
from fanops.personas import Personas


def research_corpus(cfg: Config, pid: str, *, limit: int = 8) -> list[str]:
    """B3: propose the reach-best hashtags this persona doesn't yet carry — the bootstrap "research my
    corpus" step. Grounded in the reach-ranked store (LIVE Meta Graph reach), minus its current corpus.
    INSTANT + budget-free: the store already encodes the Graph signal (refresh_store built it), so no
    per-candidate Graph call is spent here. Returns an ordered list of candidate tags (most-reach
    first) the operator accepts into the corpus. Unknown id -> KeyError. (M3: tag_lean retired — the curated
    corpus itself is the persona's flavor; research re-ranks the reach universe against it.)"""
    from fanops.hashtags import vetted_menu, load_store   # _norm already imported at module scope
    per = Personas.load(cfg).get(pid)
    if per is None:
        raise KeyError(pid)
    have = {_norm(t) for t in per.hashtag_corpus if isinstance(t, str)}
    ranked = vetted_menu(load_store(cfg))                                # store (live Graph reach) else frozen reach-order
    out: list[str] = []; seen: set[str] = set()
    for t in ranked:
        n = _norm(t)
        if n and n not in have and n not in seen:
            seen.add(n); out.append(n)
    return out[:limit]


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
    target = cfg.corpus_target
    auto_slots = max(0, target - len(pinned))
    budget = budget_remaining(cfg, now=now)
    if budget is None:
        return {"changed": False, "reason": "budget_unreadable"}
    if budget == 0:
        return {"changed": False, "reason": "budget_exhausted"}
    have = set(corpus)
    store_reach = load_store_reach(cfg)
    cands: list[dict] = []
    if cfg.meta_graph_token and cfg.meta_ig_user_id:
        gap = max(0, target - len(corpus))
        measure_k = min(gap + len(auto), target)
        cands = discover_corpus(cfg, pid, limit=max(auto_slots, gap) + len(auto), measure_k=measure_k,
                                get=get, offline_fallback=False)
    elif len(corpus) < target:
        cands = [{"tag": t} for t in research_corpus(cfg, pid, limit=target - len(corpus))]
    else:
        return {"changed": False}
    screened = _screen_content([c["tag"] for c in cands if isinstance(c, dict) and c.get("tag")], cfg)
    cand_by_tag = {_norm(c["tag"]): c for c in cands if isinstance(c, dict) and _norm(c.get("tag", ""))}
    novel = [t for t in screened if _norm(t) not in have]
    pool = list(dict.fromkeys(auto + novel))
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
        for per in personas:
            try:
                r = refresh_persona_corpus(cfg, per.id, get=get, now=now)
            except Exception:
                continue
            if r.get("changed"):
                changed += 1; added_n += len(r.get("added") or []); removed_n += len(r.get("removed") or [])
        write_json_atomic(marker, {"ts": datetime.now(timezone.utc).isoformat(), "personas": len(personas),
                                   "changed": changed, "added": added_n, "removed": removed_n})
        return {"refreshed": True, "personas": len(personas), "changed": changed, "added": added_n, "removed": removed_n}
    except Exception as exc:
        return {"refreshed": False, "reason": f"error: {str(exc)[:120]}"}
