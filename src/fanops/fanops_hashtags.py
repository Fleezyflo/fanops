# src/fanops/fanops_hashtags.py
"""Layer A — the ONLY writer of the hashtag measurement cache (00_control/hashtags.json).

Network source is instagrapi (`ig_hashtag_scrape`); the Meta Graph hashtag path is deferred
(helpers remain in meta_graph for later — refresh never falls back to Graph).

One pass, per persona that actually posts:

  description -> terms -> anchor tags -> ONE medias_top fetch per tag -> {metric, co-occurring tags}

`persona_terms` returns the persona's declared `niche` and NOTHING else. The corpus is never an input:
it used to seed this pass, which made the store a re-ranked echo of the corpora it then fed — a closed
loop with no external evidence anywhere in it (measured live 2026-07-16: the store was byte-identical
to seeds + the frozen floor, 0 discovered, `reach: {}`, while every proposal it made looked like
research). Rooting discovery in the declared niche severs that edge structurally.

The metric is Instagram's own `like_count`, verbatim (see ig_hashtag_scrape.measure_and_harvest_scrape).
A tag with no number is UNMEASURED and simply absent — the cache holds measured tags only.

Missing scrape (no [igscrape] / no session / login fail) aborts LOUDLY (`written:False`, `aborted:no_scrape`)
— there is no silent Graph fallback. A throttle ends the pass but still writes accrued evidence."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from fanops.config import Config
from fanops.log import get_logger
from fanops.hashtags import METRIC_FIELD, _norm, load_measurements, ranked_tags
from fanops.controlio import write_json_atomic

_MAX_AGE_DAYS = 90            # a measurement older than this is history, not evidence — pruned on write
_COMPLETE_KEY = "last_complete_pass"   # sibling of tag records; gates the 12h tick (NOT file mtime)
# Scrape is ~5–7s/tag. Unbounded co-tag enqueue (one anchor can harvest 80+) turns a 24-niche pass into
# hundreds of calls with no mid-pass write — looks hung. Cap tries + novel co-tag measures per pass;
# incomplete passes do NOT stamp last_complete_pass (same as throttle) so the next tick continues.
_SCRAPE_TRY_CAP = 40          # max tags attempted per refresh_store call (~4–7 min at live scrape latency)
_SCRAPE_COTAG_ENQUEUE_CAP = 10  # max NEW co-tags measured this pass (anchors first; discovery is incremental)


def _read_complete_pass(cfg: Config) -> str | None:
    """The ISO stamp of the last NON-throttled pass, or None. Fail-open on any read/parse miss."""
    p = cfg.hashtags_path
    if not p.exists():
        return None
    try:
        import json
        raw = json.loads(p.read_text())
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    v = raw.get(_COMPLETE_KEY)
    return v if isinstance(v, str) and v else None


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


def refresh_store(cfg: Config, *, scrape_client=None, now=None) -> dict:
    """Run one measurement pass and rewrite the cache. Returns a summary dict.

    Order of work: never-measured anchors first (must discover), then every previously-measured tag
    (anchors + known) ordered stalest `measured_at` first so a throttle cannot starve the tail, then
    novel co-tags this pass discovered. Co-occurring tags are harvested from ANCHORS only — a co-tag's
    own co-tags would drift away from the persona's niche within two hops.

    ABORTS without writing when personas.json is CORRUPT, or when scrape cannot open (`no_scrape`).
    A throttle ends the pass but still writes — evidence already bought is kept.
    `last_complete_pass` advances ONLY when throttled is False (the 12h tick gates on that stamp)."""
    from fanops.errors import ControlFileError
    from fanops.ig_hashtag_scrape import (ScrapeRefused, ScrapeThrottled, ScrapeUnavailable,
                                          measure_and_harvest_scrape, open_client,
                                          resolve_hashtag_scrape)
    from fanops.persona_research import persona_terms
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    try:
        personas = _posting_personas(cfg)
    except ControlFileError as e:                          # corrupt personas.json: ABORT, cache UNTOUCHED
        return {"written": False, "aborted": "corrupt_personas", "reason": str(e)}
    if scrape_client is None:
        try:
            scrape_client = open_client(cfg)
        except ScrapeUnavailable as e:
            return {"written": False, "aborted": "no_scrape", "reason": str(e), "backend": "scrape"}
    client = scrape_client
    prev_complete = _read_complete_pass(cfg)
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
    unmeasured_anchors = [t for t in anchors if t not in cache]
    remeasure = sorted((t for t in list(anchors) + [t for t in cache if t not in anchor_set] if t in cache),
                       key=lambda t: cache[t].get("measured_at") or "")   # stalest first
    # Scrape is ~5–10s/tag. When niches are still dark, spend the pass ONLY on them — do not let
    # re-measure of the old Graph cache (28+) consume the try budget before craft/burner niches land.
    if unmeasured_anchors:
        queue: list[str] = list(unmeasured_anchors)
    else:
        queue = list(remeasure)
    queued: set[str] = set(queue)
    measured = 0; discovered = 0; throttled = False; tried = 0; cotag_enqueued = 0
    unresolved: list[dict] = []
    log = get_logger(cfg)
    i = 0
    while i < len(queue):
        if tried >= _SCRAPE_TRY_CAP:
            throttled = True                               # budget, not Instagram — same incomplete-pass stamp
            log("hashtags", "-", "pass_try_cap", tried=tried, queue_left=len(queue) - i,
                cap=_SCRAPE_TRY_CAP)
            break
        tag = queue[i]; i += 1
        tried += 1
        metric = None; cotags: dict[str, int] = {}; hid = None
        try:
            hid = ids.get(tag) or resolve_hashtag_scrape(client, tag)
            if not hid:
                unresolved.append({"tag": tag, "reason": "no_match"})
                continue
            ids[tag] = hid
            metric, cotags = measure_and_harvest_scrape(client, tag)
        except ScrapeThrottled:
            throttled = True                               # Instagram said stop — the only governor there is
            break
        except ScrapeRefused as e:
            unresolved.append({"tag": tag, "reason": "refused", "code": e.code,
                               "message": e.message})
            log("hashtags", tag, "unresolved", reason="refused", message=(e.message or "")[:120],
                tried=tried)
            continue
        if tag in anchor_set:
            for co, n in cotags.items():
                if co in anchor_set:
                    continue                               # an anchor is its own root, not its own discovery
                attribution.setdefault(co, {})
                attribution[co][tag] = attribution[co].get(tag, 0) + n
                if co not in queued and cotag_enqueued < _SCRAPE_COTAG_ENQUEUE_CAP:
                    queued.add(co); queue.append(co); discovered += 1; cotag_enqueued += 1
        if metric is None:
            continue                                       # no number -> UNMEASURED -> absent
        rec = {"graph_id": hid, METRIC_FIELD: float(metric), "measured_at": stamp}
        frm = attribution.get(tag)
        if frm:
            rec["from"] = {k: int(v) for k, v in frm.items()}
        cache[tag] = rec; measured += 1
        if tried == 1 or tried % 5 == 0:
            log("hashtags", tag, "measured", tried=tried, measured=measured, queue_left=len(queue) - i,
                like_count=float(metric))
    cutoff = now - timedelta(days=_MAX_AGE_DAYS)
    fresh = {t: cache[t] for t in ranked_tags(cache) if _fresh(cache[t], cutoff)}
    if not throttled:
        fresh[_COMPLETE_KEY] = stamp                          # only a finished pass buys the 12h silence
    elif prev_complete:
        fresh[_COMPLETE_KEY] = prev_complete                  # preserve; never slide forward on a cut-off
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(cfg.hashtags_path, fresh)
    return {"written": True, "measured": measured, "discovered": discovered,
            "total": len([t for t in fresh if t != _COMPLETE_KEY]), "throttled": throttled,
            "tried": tried, "unresolved": unresolved, "backend": "scrape"}


def refresh_store_if_due(cfg: Config, *, max_age_s: int = 43200, scrape_client=None, now=None) -> dict:
    """The constant-update hook the run loop calls each tick: refresh at most once per `max_age_s`
    (default 12h), gated on `last_complete_pass` inside the cache — NOT file mtime, because a throttled
    pass still writes and would otherwise buy twelve hours of silence for almost no work. Needs a scrape
    session (else a clean no-op — the cache is a platform artifact). FAIL-OPEN: any error -> a reason,
    NEVER raises; it must not crash the unattended run. A corrupt personas.json / no_scrape abort is NOT
    a refresh: refresh_store aborts (cache untouched) and this REPORTS the abort so the tick never logs a
    false success on a broken control file."""
    from fanops.ig_hashtag_scrape import scrape_configured
    if not scrape_configured(cfg) and scrape_client is None:
        return {"refreshed": False, "reason": "no scrape session"}
    try:
        now_dt = now or datetime.now(timezone.utc)
        complete = _read_complete_pass(cfg)
        if complete:
            try:
                ts = datetime.fromisoformat(complete)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if (now_dt - ts).total_seconds() < max_age_s:
                    return {"refreshed": False, "reason": "fresh"}
            except ValueError:
                pass                                          # unparseable stamp -> treat as due
        r = refresh_store(cfg, scrape_client=scrape_client, now=now_dt)
        if not r.get("written"):                              # corrupt/no_scrape abort: preserve, report
            return {"refreshed": False, **r}
        return {"refreshed": True, **r}
    except Exception as exc:                                  # noqa: BLE001 — the tick must never die here
        return {"refreshed": False, "reason": f"error: {str(exc)[:120]}"}


def cmd_hashtags_refresh(cfg: Config) -> int:
    """`fanops hashtags refresh` — run a measurement pass now. Writes ONLY the cache; needs no ledger.
    Without scrape session the pass aborts loudly (exit 2). Corrupt personas.json also exits 2."""
    r = refresh_store(cfg)
    if not r.get("written"):
        get_logger(cfg)("hashtags", "-", "refresh_aborted", level="error",
                        aborted=r.get("aborted", "unknown"), reason=r.get("reason", ""),
                        detail=("00_control/hashtags.json left untouched; "
                                "fanops hashtags scrape-login / fix personas.json"))
        return 2
    unresolved = r.get("unresolved") or []
    codes = sorted({u.get("code") for u in unresolved if u.get("code") is not None})
    get_logger(cfg)("hashtags", "-", "refreshed", measured=r["measured"], discovered=r["discovered"],
                    total=r["total"], throttled=r["throttled"], tried=r.get("tried", 0),
                    unresolved=len(unresolved), refusal_codes=",".join(str(c) for c in codes),
                    backend=r.get("backend", "scrape"), path=str(cfg.hashtags_path))
    for u in unresolved[:20]:                                    # cap log blast; full list is in the return
        get_logger(cfg)("hashtags", u.get("tag") or "-", "unresolved",
                        reason=u.get("reason"), code=u.get("code"),
                        message=(u.get("message") or "")[:160])
    return 0


def cmd_hashtags_scrape_login(cfg: Config) -> int:
    """`fanops hashtags scrape-login` — open instagrapi, login, dump session. Never prints the password."""
    from fanops.ig_hashtag_scrape import ScrapeUnavailable, open_client
    try:
        open_client(cfg)
    except ScrapeUnavailable as e:
        get_logger(cfg)("hashtags", "-", "scrape_login_failed", level="error", reason=str(e)[:160])
        return 2
    get_logger(cfg)("hashtags", "-", "scrape_login_ok", user=(cfg.ig_scrape_user or "")[:40],
                    session=str(cfg.ig_scrape_session_path))
    return 0


def cmd_hashtags_discover(cfg: Config) -> int:
    """`fanops hashtags discover` — the periodic "what does each persona's niche look like right now"
    report. READ-ONLY and ZERO NETWORK: it projects the cache that refresh already bought, so it can be
    scheduled freely (launchd/cron) without spending a single scrape call. Always exits 0."""
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
        hint="numbers are Instagram like_count on top posts (instagrapi)")
    return 0
