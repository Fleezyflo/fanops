# src/fanops/fanops_hashtags.py
"""Layer A — the ONLY code that touches the Graph for hashtags, and the only writer of the measurement
cache (00_control/hashtags.json).

One pass, per persona that actually posts:

  description -> terms -> anchor tags -> ONE top_media fetch per tag -> {metric, co-occurring tags}

`persona_terms` returns the persona's declared `niche` and NOTHING else. The corpus is never an input:
it used to seed this pass, which made the store a re-ranked echo of the corpora it then fed — a closed
loop with no external evidence anywhere in it (measured live 2026-07-16: the store was byte-identical
to seeds + the frozen floor, 0 discovered, `reach: {}`, while every proposal it made looked like
research). Rooting discovery in the declared niche severs that edge structurally.

The metric is Meta's own `like_count`, verbatim (see meta_graph.measure_and_harvest). A tag Meta gives no
number for is UNMEASURED and simply absent — the cache holds measured tags only, so the menu it feeds is
100% evidence rather than 21 measurements padded with 1,683 guesses.

Nothing here meters Meta. Known tags re-measure by cached `graph_id` (no search spent), novel tags are
searched, and the pass runs until Meta throttles — at which point it stops and keeps what it accrued."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from fanops.config import Config
from fanops.log import get_logger
from fanops.hashtags import METRIC_FIELD, _norm, load_measurements, ranked_tags
from fanops.controlio import write_json_atomic

_MAX_AGE_DAYS = 90            # a measurement older than this is history, not evidence — pruned on write


def _posting_persona_ids(cfg: Config) -> set[str]:
    """The persona ids linked to an ACTIVE account — i.e. the personas that actually post. Empty set means
    "unknown, do not narrow" (absent/corrupt accounts.json, or no active account carries a persona), so the
    caller falls back to every persona. Never raises: the cache must not depend on accounts.json being
    readable, and a torn control file must not shrink discovery."""
    try:
        from fanops.accounts import Accounts
        return {pid for a in Accounts.load(cfg).active() if (pid := (a.persona_id or "").strip())}
    except Exception as e:                                 # noqa: BLE001 — fail-open by design, but say so once
        get_logger(cfg)("hashtags", "terms", "accounts_unreadable", err=str(e)[:120])
        return set()


def _posting_personas(cfg: Config) -> list:
    """The persona RECORDS whose descriptions root discovery. Narrowed to personas linked to an active
    account: discovery seeded from personas that post nothing is wrong by construction, and it was not
    theoretical — five DORMANT personas put `#science`, `#gossip`, `#celebritygossip` and `#drama` into a
    Syrian rapper's menu through their `niche`. FAIL-OPEN: no readable accounts.json, or no active
    account carrying a persona -> every persona (a missing control file must not silently stop discovery).
    A CORRUPT personas.json raises ControlFileError, which refresh_store turns into a loud abort."""
    from fanops.personas import Personas
    personas = Personas.load(cfg).all()                    # corrupt personas.json -> ControlFileError propagates
    live = _posting_persona_ids(cfg)
    if live:
        personas = [p for p in personas if p.id in live] or personas   # `or personas`: never derive from nothing
    return personas


def _fresh(rec: dict, cutoff: datetime) -> bool:
    """True when a record's measurement is inside the retention window. An unparseable stamp is KEPT (we
    do not delete data we cannot judge); the corpus gate rejects it on its own freshness check."""
    at = rec.get("measured_at")
    try:
        ts = datetime.fromisoformat(at) if isinstance(at, str) else None
    except ValueError:
        return True
    if ts is None:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= cutoff


def refresh_store(cfg: Config, *, get=None, now=None) -> dict:
    """Run one measurement pass and rewrite the cache. Returns a summary dict.

    Order of work: every persona's anchors first (they are the niche roots and the freshest thing worth
    knowing), then everything already measured, stalest `measured_at` first, then the novel tags this
    pass discovered. Co-occurring tags are harvested from ANCHORS only — a co-tag's own co-tags would
    drift away from the persona's niche within two hops.

    ABORTS without writing when personas.json is CORRUPT: a bad hand-edit to a control file must not
    clobber the cache. A throttle ends the pass but still writes — evidence already bought is kept."""
    from fanops.errors import ControlFileError
    from fanops.meta_graph import (GraphRefused, GraphThrottled, GraphUnreachable,
                                   measure_and_harvest, resolve_hashtag)
    from fanops.persona_research import persona_terms
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    try:
        personas = _posting_personas(cfg)
    except ControlFileError as e:                          # corrupt personas.json: ABORT, cache UNTOUCHED
        return {"written": False, "aborted": "corrupt_personas", "reason": str(e)}
    cache: dict[str, dict] = dict(load_measurements(cfg))
    ids: dict[str, str] = {t: r["graph_id"] for t, r in cache.items()}
    attribution: dict[str, dict] = {t: dict(r.get("from") or {}) for t, r in cache.items()}
    anchors: list[str] = []
    for per in personas:
        for term in persona_terms(per):
            a = _norm("#" + term)
            if a and a not in anchors:
                anchors.append(a)
    anchor_set = set(anchors)
    known = sorted((t for t in cache if t not in anchor_set),
                   key=lambda t: cache[t].get("measured_at") or "")     # stalest first
    queue: list[str] = anchors + known
    queued: set[str] = set(queue)
    measured = 0; discovered = 0; throttled = False; tried = 0
    unresolved: list[dict] = []
    i = 0
    while i < len(queue):
        tag = queue[i]; i += 1
        tried += 1
        metric = None; cotags: dict[str, int] = {}; hid = None
        try:
            hid = ids.get(tag) or resolve_hashtag(cfg, tag, get=get)
            if not hid:
                unresolved.append({"tag": tag, "reason": "no_match"})  # Meta 200 + empty data only
                continue
            ids[tag] = hid
            metric, cotags = measure_and_harvest(cfg, hid, get=get)
        except GraphThrottled:
            throttled = True                               # Meta said stop — the only governor there is
            break
        except GraphRefused as e:
            unresolved.append({"tag": tag, "reason": "refused", "code": e.code,
                               "subcode": e.subcode, "type": e.type, "message": e.message})
            continue
        except GraphUnreachable as e:
            unresolved.append({"tag": tag, "reason": "unreachable", "message": e.reason})
            continue
        if tag in anchor_set:
            for co, n in cotags.items():
                if co in anchor_set:
                    continue                               # an anchor is its own root, not its own discovery
                attribution.setdefault(co, {})
                attribution[co][tag] = attribution[co].get(tag, 0) + n
                if co not in queued:
                    queued.add(co); queue.append(co); discovered += 1
        if metric is None:
            continue                                       # Meta published no number -> UNMEASURED -> absent
        rec = {"graph_id": hid, METRIC_FIELD: float(metric), "measured_at": stamp}
        frm = attribution.get(tag)
        if frm:
            rec["from"] = {k: int(v) for k, v in frm.items()}
        cache[tag] = rec; measured += 1
    cutoff = now - timedelta(days=_MAX_AGE_DAYS)
    fresh = {t: cache[t] for t in ranked_tags(cache) if _fresh(cache[t], cutoff)}
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(cfg.hashtags_path, fresh)
    return {"written": True, "measured": measured, "discovered": discovered,
            "total": len(fresh), "throttled": throttled, "tried": tried, "unresolved": unresolved}


def refresh_store_if_due(cfg: Config, *, max_age_s: int = 43200, get=None, now=None) -> dict:
    """The constant-update hook the run loop calls each tick: refresh at most once per `max_age_s`
    (default 12h), throttled by the cache file's mtime so a 10-minute publish cadence doesn't hammer the
    Graph. Needs Meta creds (else a clean no-op — the cache is a platform artifact). FAIL-OPEN: any error
    -> a reason, NEVER raises; it must not crash the unattended run. A corrupt personas.json is NOT a
    refresh: refresh_store aborts (cache untouched) and this REPORTS the abort so the tick never logs a
    false success on a broken control file."""
    import time
    if not (cfg.meta_graph_token and cfg.meta_ig_user_id):
        return {"refreshed": False, "reason": "no Meta creds"}
    try:
        p = cfg.hashtags_path
        if p.exists() and (time.time() - p.stat().st_mtime) < max_age_s:
            return {"refreshed": False, "reason": "fresh"}
        r = refresh_store(cfg, get=get, now=now)
        if not r.get("written"):                              # corrupt-personas abort: preserve, report
            return {"refreshed": False, **r}
        return {"refreshed": True, **r}
    except Exception as exc:                                  # noqa: BLE001 — the tick must never die here
        return {"refreshed": False, "reason": f"error: {str(exc)[:120]}"}


def cmd_hashtags_refresh(cfg: Config) -> int:
    """`fanops hashtags refresh` — run a measurement pass now. Writes ONLY the cache; needs no ledger.
    Without Meta creds the pass measures nothing and the cache stands (exit 0). The ONE non-zero exit is a
    CORRUPT personas.json: refresh_store aborts (the cache is preserved), so this says why and exits 2."""
    r = refresh_store(cfg)
    if not r.get("written"):
        get_logger(cfg)("hashtags", "-", "refresh_aborted", level="error",
                        aborted=r.get("aborted", "unknown"), reason=r.get("reason", ""),
                        detail="00_control/hashtags.json left untouched; fix personas.json")
        return 2
    unresolved = r.get("unresolved") or []
    codes = sorted({u.get("code") for u in unresolved if u.get("code") is not None})
    get_logger(cfg)("hashtags", "-", "refreshed", measured=r["measured"], discovered=r["discovered"],
                    total=r["total"], throttled=r["throttled"], tried=r.get("tried", 0),
                    unresolved=len(unresolved), refusal_codes=",".join(str(c) for c in codes),
                    path=str(cfg.hashtags_path))
    for u in unresolved[:20]:                                    # cap log blast; full list is in the return
        get_logger(cfg)("hashtags", u.get("tag") or "-", "unresolved",
                        reason=u.get("reason"), code=u.get("code"), subcode=u.get("subcode"),
                        type=u.get("type"), message=(u.get("message") or "")[:160])
    return 0


def cmd_hashtags_discover(cfg: Config) -> int:
    """`fanops hashtags discover` — the periodic "what does each persona's niche look like right now"
    report. READ-ONLY and ZERO NETWORK: it projects the cache that refresh already bought, so it can be
    scheduled freely (launchd/cron) without spending a single Graph call. Always exits 0."""
    from fanops.persona_research import derived_report
    log = get_logger(cfg)
    try:
        personas = _posting_personas(cfg)
    except Exception as exc:                                  # noqa: BLE001 — a report must never break a schedule
        log("hashtags", "-", "discover_skipped", level="warning", err=str(exc)[:160]); return 0
    if not personas:
        log("hashtags", "-", "no_personas", level="warning",
            hint="add one in the Studio Personas tab first"); return 0
    for per in personas:
        r = derived_report(cfg, per.id)
        log("hashtags", per.id, "niche", terms=", ".join(r["terms"][:8]), measured=r["measured"],
            top=", ".join(f"{t}({int(v)})" for t, v in r["top"]))
    log("hashtags", "-", "discover_done", field=METRIC_FIELD,
        hint="numbers are Meta's own like_count on each tag's top media")
    return 0
