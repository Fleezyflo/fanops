# src/fanops/fanops_hashtags.py
"""Hashtag store builder — the ONLY judge of a hashtag is its LIVE platform reach via the Meta Graph API
(operator 2026-06-27: a tag's worth is how active/reaching it is on the platform NOW, never whether a post
that happened to use it did well — post outcomes attribute to the hook/clip/account, not the hashtag).

refresh_store harvests co-occurring candidate tags from our niche seeds (the persona corpora + genre),
measures their live Graph reach within the 30/7-day ig_hashtag_search budget, ranks by reach, and writes
the reach-ranked 00_control/hashtags.json store. No ledger, no learn-doctor gate — the store does not depend
on any published post. FAIL-OPEN: no Meta creds / a fetch miss -> the frozen reach-ranked seed stands (the
store is never empty and never raises). cmd_hashtags_discover REPORTS fresh per-persona discoveries and NEVER
writes the caption menu (curation stays operator-gated in the Studio)."""
from __future__ import annotations
import json
from fanops.config import Config
from fanops.hashtags import _norm, vetted_menu


def _seed_tags(cfg: Config) -> list[str]:
    """The niche anchor seeds the Graph harvest reads from: every persona's curated corpus + its intake
    `genre` words, normalized + deduped. FAIL-OPEN: an unreadable personas.json -> [] (the frozen seed still
    drives the store). These are the categories whose currently-winning posts we mine for co-occurring tags."""
    from fanops.personas import Personas
    seeds: list[str] = []
    try:
        for per in Personas.load(cfg).all():
            seeds += [t for t in (per.hashtag_corpus or []) if isinstance(t, str)]
            seeds += ["#" + w for w in (per.intake.get("genre") or "").split() if w.strip()]
    except Exception:
        return []
    out: list[str] = []; seen: set[str] = set()
    for s in seeds:
        n = _norm(s) if isinstance(s, str) else ""
        if n and n not in seen:
            seen.add(n); out.append(n)
    return out


def refresh_store(cfg: Config, *, get=None, now=None) -> dict:
    """Recompute + write the reach-ranked tag store from LIVE Meta Graph reach. Harvest co-occurring
    candidates from the niche seeds, measure their Graph reach within the 30/7-day budget, rank by measured
    reach (desc), and write 00_control/hashtags.json — measured tags first, then the rest of the relevance-
    ordered universe so the store is never narrow, with the frozen seed as the cold-start floor. No ledger,
    no learn-doctor gate (the store is independent of any published post). FAIL-OPEN: no creds / fetch miss
    -> measured is empty -> the frozen seed order stands. Returns a summary dict; never raises on a clean run."""
    from fanops.meta_graph import harvest_cooccurring, sample_trends
    seed = vetted_menu()                                  # frozen reach-ranked cold-start floor (never empty)
    seeds = _seed_tags(cfg)
    harvested = harvest_cooccurring(cfg, seeds, get=get, now=now) if seeds else {}
    by_count = [t for t, _ in sorted(harvested.items(),   # discovered co-occurring tags, most-relevant first
                                     key=lambda kv: (kv[1]["count"], kv[1]["host_engagement"]), reverse=True)]
    universe: list[str] = []; useen: set[str] = set()     # candidates to MEASURE: discovered, then niche seeds, then frozen
    for t in by_count + seeds + seed:
        if t not in useen: useen.add(t); universe.append(t)
    measured = sample_trends(cfg, universe, get=get, now=now)   # {tag: live Graph reach}, budget-bounded, fail-open
    merged: list[str] = []; seen: set[str] = set()
    for t in sorted(measured, key=lambda k: measured[k], reverse=True):   # PRIMARY: live Graph reach
        if t not in seen: seen.add(t); merged.append(t)
    for t in universe:                                    # unmeasured tags keep relevance order so the store stays broad
        if t not in seen: seen.add(t); merged.append(t)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    reach = {t: round(measured[t]) for t in measured}     # the per-tag LIVE Graph reach, persisted for the Studio surface
    cfg.hashtags_path.write_text(json.dumps({"tags": merged, "reach": reach}, indent=2))
    return {"written": True, "measured": len(measured), "harvested": len(harvested), "total": len(merged)}


def refresh_store_if_due(cfg: Config, *, max_age_s: int = 43200, get=None, now=None) -> dict:
    """Constant-update hook the autonomous run loop calls each tick: refresh the Graph-reach store at most once
    per `max_age_s` (default 12h). Needs Meta creds (else a clean no-op — the store is a Graph artifact). Throttled
    by the store file's mtime so a 10-minute publish cadence does not hammer the 30/7-day ig_hashtag_search budget;
    across ticks the budget window rolls, so candidates rotate. FAIL-OPEN: any error -> a reason, NEVER raises — it
    must never crash the unattended run (independent of the publish backend; not gated on is_live_backend)."""
    import time
    if not (cfg.meta_graph_token and cfg.meta_ig_user_id):
        return {"refreshed": False, "reason": "no Meta creds"}
    try:
        p = cfg.hashtags_path
        if p.exists() and (time.time() - p.stat().st_mtime) < max_age_s:
            return {"refreshed": False, "reason": "fresh"}
        return {"refreshed": True, **refresh_store(cfg, get=get, now=now)}
    except Exception as exc:
        return {"refreshed": False, "reason": f"error: {str(exc)[:120]}"}


def cmd_hashtags_refresh(cfg: Config) -> int:
    """`fanops hashtags refresh` — rebuild the reach-ranked store from LIVE Meta Graph reach (harvest ->
    measure -> rank). Writes ONLY 00_control/hashtags.json; needs no ledger and no learn-doctor verdict.
    FAIL-OPEN without Meta creds (the frozen seed stands). Always exits 0."""
    r = refresh_store(cfg)
    print(f"hashtags store refreshed from live Graph reach: {r['measured']} measured + "
          f"{r['harvested']} harvested -> {r['total']} tags (00_control/hashtags.json)")
    return 0


def cmd_hashtags_discover(cfg: Config) -> int:
    """`fanops hashtags discover` — run LIVE Graph co-occurrence discovery for EVERY persona and REPORT the
    fresh hashtags their categories' currently-winning posts use. The periodic "what's new in our niches"
    check (schedule it via launchd/cron). READ-ONLY w.r.t. the caption path: it proposes, it NEVER writes the
    menu — curation stays operator-gated in the Studio Personas tab (the operator ACCEPTS a discovered tag into
    a corpus). Needs Meta creds; without them each persona reports nothing (fail-open). Always exits 0."""
    from fanops.personas import Personas, discover_corpus
    try:
        personas = Personas.load(cfg).all()
    except Exception as exc:
        print(f"hashtags discover SKIPPED: personas.json unreadable ({exc})"); return 0
    if not personas:
        print("hashtags discover: no personas — add one in the Studio Personas tab first."); return 0
    for per in personas:
        try:
            props = discover_corpus(cfg, per.id)
        except Exception as exc:
            print(f"  {per.id}: discovery error ({exc})"); continue
        if props:
            tags = ", ".join(p["tag"] + (f"({p['count']})" if p.get("count") else "") for p in props)
            print(f"  {per.id}: {len(props)} fresh — {tags}")
        else:
            print(f"  {per.id}: no fresh tags (corpus covers the live winners, or no Meta creds)")
    print("review + curate in the Studio Personas tab → Research corpus (nothing was written to the menu).")
    return 0
