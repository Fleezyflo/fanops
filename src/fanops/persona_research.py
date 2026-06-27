# src/fanops/persona_research.py
"""Per-persona hashtag CORPUS research + live Graph discovery (extracted from personas.py, audit #6 —
behavior byte-identical). research_corpus is the budget-free offline re-rank of what we already know;
discover_corpus is the live co-occurrence harvest that finds tags we have never named, FAIL-OPEN to the
offline re-rank. Both are re-exported from fanops.personas — and discover_corpus MUST stay patchable at
`fanops.personas.discover_corpus` (tests monkeypatch it there; fanops_hashtags imports it lazily)."""
from __future__ import annotations
from fanops.config import Config
from fanops.hashtags import _norm
from fanops.personas import Personas


def research_corpus(cfg: Config, pid: str, *, limit: int = 8) -> list[str]:
    """B3: propose the reach-best hashtags this persona doesn't yet carry — the bootstrap "research my
    corpus" step. Grounded in the reach-ranked store (own-reach + Graph trends, default-ON), minus its
    current corpus. INSTANT + budget-free: the store already encodes the Graph signal (refresh_store blends
    it), so no per-candidate Graph call is spent here. Returns an ordered list of candidate tags (most-reach
    first) the operator accepts into the corpus. Unknown id -> KeyError. (M3: tag_lean retired — the curated
    corpus itself is the persona's flavor; research re-ranks the reach universe against it.)"""
    from fanops.hashtags import vetted_menu, load_store   # _norm already imported at module scope
    per = Personas.load(cfg).get(pid)
    if per is None:
        raise KeyError(pid)
    have = {_norm(t) for t in per.hashtag_corpus if isinstance(t, str)}
    ranked = vetted_menu(load_store(cfg))                                # store (own-reach+trends) else frozen reach-order
    out: list[str] = []; seen: set[str] = set()
    for t in ranked:
        n = _norm(t)
        if n and n not in have and n not in seen:
            seen.add(n); out.append(n)
    return out[:limit]


def discover_corpus(cfg: Config, pid: str, *, limit: int = 8, measure_k: int = 0, get=None) -> list[dict]:
    """M3: LIVE per-persona discovery — the upgrade from research_corpus's re-rank-what-we-know to
    finding tags we have never named. Seeds the Graph co-occurrence harvest from the persona's category
    (its corpus + intake `genre`), DROPS what we already know (VETTED ∪ reach store ∪ corpus), and returns
    evidence-carrying proposals [{"tag","count","host_engagement",...}] reach-relevant first. FAIL-OPEN: no
    creds / nothing fresh / any Graph error -> today's offline research_corpus re-rank, wrapped as evidence-
    less {"tag": ...} dicts so the caller has ONE shape. measure_k defaults 0 (the free co-occurrence COUNT is
    the operator's evidence; per-tag reach stays the explicit 'Check reach' action) — the global refresh
    passes measure_k>0 to gate the menu on measured reach. Unknown id -> KeyError. (M3: tag_lean retired —
    the curated corpus is the seed flavor.)"""
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
    return [{"tag": t} for t in research_corpus(cfg, pid, limit=limit)]   # FAIL-OPEN to the offline re-rank
