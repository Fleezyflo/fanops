# src/fanops/fanops_hashtags.py
"""Layer A — the ONLY writer of the hashtag measurement cache (00_control/hashtags.json).

Network source is instagrapi (`ig_hashtag_scrape`); the Meta Graph hashtag path is deferred
(helpers remain in meta_graph for later — refresh never falls back to Graph).

One pass, per persona that actually posts:

  description -> terms -> anchor tags -> ONE medias_top fetch per tag -> {metric, co-occurring tags}

`persona_terms` returns operator `niche` plus durable LLM vocab seeds (MOL-637/MOL-644) — voice/levers
stay on captions+hooks, not Layer A search roots. Vocab expands territory without writing the corpus;
inbound-only membership still gates admission (MOL-643).

Visibility numbers are Instagram's own fields only (see ig_hashtag_scrape): Top-grid median
`play_count` (preferred) / `like_count`, plus `media_count` from hashtag_info when served.
A tag with neither plays nor likes in the Top grid is UNMEASURED and absent — measured tags only.

Missing scrape (no [igscrape] / no session / login fail) aborts LOUDLY (`written:False`, `aborted:no_scrape`)
— there is no silent Graph fallback. A throttle ends the pass but still writes accrued evidence."""
from __future__ import annotations
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from fanops.config import Config
from fanops.log import get_logger
from fanops.hashtags import (METRIC_FIELD, RECORD_NUM_FIELDS, _norm, _metric, _num,
                             load_measurements, ranked_tags)
from fanops.controlio import write_json_atomic

_MAX_AGE_DAYS = 90            # a measurement older than this is history, not evidence — pruned on write
_VOLUME_MAX_AGE_DAYS = 7      # `media_count` re-resolve age. Tag volume moves in months, plays in hours —
                              # the 12h trend pass must not spend a hashtag_info on every tag (MOL-691).
_CORPUS_MAX_AGE_HOURS = 24    # current corpus members remesure when measured_at is older than this (MOL-695)
_COMPLETE_KEY = "last_complete_pass"   # sibling of tag records; gates the 12h tick (NOT file mtime)
_COOLDOWN_NAME = ".hashtag_scrape_cooldown.json"  # Instagram ScrapeThrottled backoff (MOL-695); never sleep
_COOLDOWN_DELAYS_S = (30 * 60, 60 * 60, 2 * 60 * 60, 6 * 60 * 60)  # streak 1..N → 30m, 1h, 2h, 6h cap
# Scrape is ~5–7s/tag. Caps bound a pass so co-tag harvest cannot run unbounded; incomplete passes do NOT
# stamp last_complete_pass. Defaults sized so niches + a meaningful co-tag set fit one pass (F-4 / MOL-631).
# Tests may monkeypatch these module attrs; env overrides win when set.
# try_cap must clear the DUE queue (unmeasured anchors + volume-due + stale corpus + weekly long-tail —
# MOL-695; NOT every cached tag every pass), plus headroom for co-tag growth. 400 kept from MOL-686.
_SCRAPE_TRY_CAP = 400
_SCRAPE_COTAG_ENQUEUE_CAP = 40
_SCRAPE_PARALLEL = 4          # concurrent medias_top workers per wave (sequential was ~6s×N wall)


def _scrape_try_cap() -> int:
    raw = os.getenv("FANOPS_HASHTAG_SCRAPE_TRY_CAP")
    if raw is None:
        return _SCRAPE_TRY_CAP
    try:
        v = int(raw)
    except ValueError:
        return _SCRAPE_TRY_CAP
    return v if v >= 1 else _SCRAPE_TRY_CAP


def _scrape_cotag_enqueue_cap() -> int:
    raw = os.getenv("FANOPS_HASHTAG_SCRAPE_COTAG_ENQUEUE")
    if raw is None:
        return _SCRAPE_COTAG_ENQUEUE_CAP
    try:
        v = int(raw)
    except ValueError:
        return _SCRAPE_COTAG_ENQUEUE_CAP
    return v if v >= 0 else _SCRAPE_COTAG_ENQUEUE_CAP


def _scrape_parallel() -> int:
    raw = os.getenv("FANOPS_HASHTAG_SCRAPE_PARALLEL")
    if raw is None:
        return _SCRAPE_PARALLEL
    try:
        v = int(raw)
    except ValueError:
        return _SCRAPE_PARALLEL
    return v if v >= 1 else _SCRAPE_PARALLEL


def _rederive_posting_corpora(cfg: Config, *, now=None) -> None:
    """Layer B on the Layer A write path — corpora track the store as it lands, not after the pass ends.

    Fail-open: a derive miss must never abort measurement. Uses posting personas only (same gate as
    discovery). Called after every durable hashtags.json write (mid-pass flush + final)."""
    from fanops.errors import fail_open
    from fanops.persona_research import derive_corpus
    try:
        personas = _posting_personas(cfg)
    except Exception as e:                                 # noqa: BLE001 — corrupt/absent: skip derive
        get_logger(cfg)("hashtags", "-", "rederive_skip", err=str(e)[:120])
        return
    for per in personas:
        with fail_open(f"fanops_hashtags.rederive.{getattr(per, 'id', '?')}"):
            derive_corpus(cfg, per.id, now=now)


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


def _volume_due(rec, cutoff: datetime) -> bool:
    """True when Instagram's own tag VOLUME (`media_count`) must be re-resolved via hashtag_info.

    Volume was previously fetched only on a tag's very first pass: once `graph_id` was cached the
    hashtag_info call was skipped forever, so 131 of 300 live records carried no `media_count` at all
    and could never acquire one (MOL-691). Volume now ages on its OWN `media_count_at` stamp — a legacy
    row falls back to `measured_at`, so the first pass after this ships does not re-resolve all 300 tags
    at once. Volume moves far slower than plays; the 12h trend pass must not drag it along."""
    if not isinstance(rec, dict):
        return True
    if _num(rec.get("media_count")) is None:
        return True                                        # never measured -> backfill
    at = rec.get("media_count_at") or rec.get("measured_at")
    try:
        ts = datetime.fromisoformat(at) if isinstance(at, str) else None
    except ValueError:
        return True
    if ts is None:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts < cutoff


def _measured_due(rec, cutoff: datetime) -> bool:
    """True when `measured_at` is missing, unparseable, or older than cutoff (MOL-695 due tiers)."""
    if not isinstance(rec, dict):
        return True
    at = rec.get("measured_at")
    try:
        ts = datetime.fromisoformat(at) if isinstance(at, str) else None
    except ValueError:
        return True
    if ts is None:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts < cutoff


def _staleness_sort_key(tag: str, cache: dict) -> tuple:
    """Oldest measured_at first, then tag — deterministic within a due tier (MOL-695)."""
    rec = cache.get(tag) if isinstance(cache.get(tag), dict) else {}
    return (rec.get("measured_at") or "", tag)


def _cooldown_path(cfg: Config):
    return cfg.control / _COOLDOWN_NAME


def _cooldown_delay_s(streak: int) -> int:
    i = min(max(int(streak), 1), len(_COOLDOWN_DELAYS_S)) - 1
    return _COOLDOWN_DELAYS_S[i]


def _read_active_cooldown(cfg: Config, now: datetime) -> dict | None:
    """Return the cooldown blob when `until` is still in the future; else None.

    Corrupt / unreadable / unparseable → fail OPEN (no cooldown). Never sleeps."""
    p = _cooldown_path(cfg)
    if not p.exists():
        return None
    try:
        import json
        raw = json.loads(p.read_text())
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    until = raw.get("until")
    try:
        ts = datetime.fromisoformat(until) if isinstance(until, str) else None
    except ValueError:
        return None
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if now < ts:
        return raw
    return None


def _persist_throttle_cooldown(cfg: Config, now: datetime) -> dict:
    """On Instagram ScrapeThrottled: bump consecutive streak and write until (30m→1h→2h→6h cap)."""
    import json
    p = _cooldown_path(cfg)
    streak = 0
    if p.exists():
        try:
            prev = json.loads(p.read_text())
            if isinstance(prev, dict) and isinstance(prev.get("streak"), (int, float)):
                streak = int(prev["streak"])
        except (OSError, ValueError, TypeError):
            streak = 0
    streak = max(streak, 0) + 1
    until = (now + timedelta(seconds=_cooldown_delay_s(streak))).isoformat()
    blob = {"streak": streak, "until": until, "updated_at": now.isoformat()}
    cfg.control.mkdir(parents=True, exist_ok=True)
    write_json_atomic(p, blob)
    return blob


def _clear_cooldown(cfg: Config) -> None:
    """Any pass with measured>0 resets the Instagram throttle streak."""
    p = _cooldown_path(cfg)
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass


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



def _records_for_write(cache: dict, *, anchor_set: set[str], cutoff) -> dict:
    """Persist anchors + inbound-aligned tags only. Prune dead `from` keys; drop outbound-only orphans.

    `from` is membership evidence (niche seen on THIS tag's Top). Discovery enqueue must not leave edges.
    Non-anchor tags with no live-anchor `from` are evicted so remesure cannot burn budget on punchlines
    orphans / one-hit outbound megatags."""
    out: dict = {}
    for t in ranked_tags(cache):
        rec = cache.get(t)
        if not isinstance(rec, dict) or not _fresh(rec, cutoff):
            continue
        raw_from = rec.get("from") if isinstance(rec.get("from"), dict) else {}
        frm = {k: int(v) for k, v in raw_from.items()
               if k in anchor_set and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0}
        if t not in anchor_set and not frm:
            continue
        clean = {k: v for k, v in rec.items() if k != "from"}
        if frm:
            clean["from"] = frm
        out[t] = clean
    return out


@contextmanager
def _pass_lease(cfg: Config):
    """Exclusive NON-BLOCKING writer lease on the cache, yielding True when held and False when another
    pass owns it. Same primitive as stage_lock / reframe_apply: an fcntl.flock the KERNEL releases if the
    holder dies, so a crash can never wedge measurement. The lockfile carries no data."""
    import fcntl
    path = cfg.control / "hashtags.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False; return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def refresh_store(cfg: Config, *, scrape_client=None, now=None) -> dict:
    """One measurement pass under an EXCLUSIVE writer lease. See `_refresh_pass` for the pass itself.

    The cache is rewritten whole from an in-memory snapshot, so two concurrent passes (a run-loop tick and
    an operator `fanops hashtags refresh`, or two daemons) each overwrite the other's tags — minutes of
    scrape bought and silently discarded. A pass already in flight ABORTS this one (cache untouched, no
    fetch spent) rather than waiting: the work is idempotent and the next tick picks it up."""
    with _pass_lease(cfg) as held:
        if not held:
            get_logger(cfg)("hashtags", "-", "pass_busy", reason="another measurement pass holds the lease")
            return {"written": False, "aborted": "busy",
                    "reason": "another Layer A measurement pass holds 00_control/hashtags.lock"}
        return _refresh_pass(cfg, scrape_client=scrape_client, now=now)


def _refresh_pass(cfg: Config, *, scrape_client=None, now=None) -> dict:
    """Run one measurement pass and rewrite the cache. Returns a summary dict.

    Order of work (MOL-695 due tiers — NOT every cached tag every pass):
      1. never-measured anchors (must discover)
      2. records missing / due `media_count` (volume backfill; measured anchors land here when due)
      3. current posting-persona corpus members with `measured_at` older than 24h
      4. remaining cache only when `measured_at` older than 7d
    Within each tier: oldest `measured_at` then tag. Novel co-tags harvested from anchors are inserted
    ahead of the remaining remesure so try_cap buys expansion.
    Co-tags harvested from ANCHOR Tops are ENQUEUED for measurement only (discovery) — they must NOT
    write membership edges. Membership `from` is INBOUND only: a measured tag whose own Top captions
    mention a live niche anchor. Outbound-into-`from` was the megatag magnet (one caption hit × huge plays).

    Network work runs in WAVES of `_scrape_parallel()` (default 4) concurrent fetches — wall time should
    track wave count, not one-tag-at-a-time. Injected `scrape_client` (tests) shares one client under a
    lock so fakes stay deterministic.

    Every durable `hashtags.json` write (mid-pass flush + final) re-derives posting-persona corpora
    immediately — Layer B does not wait for the pass to finish. A measured==0 pass that did not mutate
    tag records skips the write and rederive (byte/mtime-identical; prior `last_complete_pass` kept).

    ABORTS without writing when personas.json is CORRUPT, or when scrape cannot open (`no_scrape`).
    An Instagram throttle ends the pass, writes accrued evidence when mutated, and persists a cooldown
    (30m→1h→2h→6h). `last_complete_pass` advances ONLY when throttled is False (the 12h tick gates on
    that stamp). try_cap incompleteness also sets throttled but does NOT write the Instagram cooldown."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from fanops.errors import ControlFileError
    from fanops.ig_hashtag_scrape import (ScrapeRefused, ScrapeThrottled, ScrapeUnavailable,
                                          measure_and_harvest_scrape, open_client, session_client,
                                          resolve_hashtag_scrape)
    from fanops.persona_research import persona_terms
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    try:
        personas = _posting_personas(cfg)
    except ControlFileError as e:                          # corrupt personas.json: ABORT, cache UNTOUCHED
        return {"written": False, "aborted": "corrupt_personas", "reason": str(e)}
    injected = scrape_client is not None
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
        for term in persona_terms(per, cfg):
            a = _norm("#" + term)
            if a and a not in anchors:
                anchors.append(a)
    anchor_set = set(anchors)
    corpus_set: set[str] = set()
    for per in personas:
        for raw in (getattr(per, "hashtag_corpus", None) or []):
            t = _norm(raw) if isinstance(raw, str) else ""
            if t:
                corpus_set.add(t)
    volume_cutoff = now - timedelta(days=_VOLUME_MAX_AGE_DAYS)
    corpus_cutoff = now - timedelta(hours=_CORPUS_MAX_AGE_HOURS)
    weekly_cutoff = now - timedelta(days=_VOLUME_MAX_AGE_DAYS)
    # Snapshot the write-shaped tag map BEFORE the pass so zero-progress can prove no mutation
    # (orphan eviction / from-prune can mutate without measured>0 — those MUST still write).
    pre_cutoff = now - timedelta(days=_MAX_AGE_DAYS)
    pre_write = _records_for_write(cache, anchor_set=anchor_set, cutoff=pre_cutoff)
    unmeasured_anchors = [t for t in anchors if t not in cache]
    queue: list[str] = list(unmeasured_anchors)
    queued: set[str] = set(queue)

    def _extend_tier(candidates: list[str]) -> None:
        for t in sorted(candidates, key=lambda x: _staleness_sort_key(x, cache)):
            if t not in queued:
                queued.add(t); queue.append(t)

    _extend_tier([t for t in cache if _volume_due(cache[t], volume_cutoff)])
    _extend_tier([t for t in cache if t in corpus_set and _measured_due(cache[t], corpus_cutoff)])
    _extend_tier([t for t in cache if _measured_due(cache[t], weekly_cutoff)])
    measured = 0; discovered = 0; throttled = False; ig_throttled = False; tried = 0; cotag_enqueued = 0
    unresolved: list[dict] = []
    log = get_logger(cfg)
    try_cap = _scrape_try_cap(); cotag_cap = _scrape_cotag_enqueue_cap()
    parallel = 1 if injected else _scrape_parallel()
    workers = [client]
    client_lock = threading.Lock()                         # shared client (tests / single worker)
    if parallel > 1:
        for _ in range(parallel - 1):
            try:
                workers.append(session_client(cfg))
            except ScrapeUnavailable:
                break
        if len(workers) > 1:
            client_lock = None                             # one client per worker — no lock
            parallel = len(workers)
        else:
            parallel = 1

    def _fetch(tag: str, worker):
        """Resolve+measure one tag. Returns (status, tag, hid|None, media_count|None, metrics|None, cotags|exc)."""
        def _go():
            # A cached graph_id skips the hashtag_info call, but VOLUME only ever arrives on that call —
            # so a tag whose id was already known could never acquire a media_count (MOL-691). Spend the
            # extra resolve when volume is missing or older than its own 7-day stamp, never every pass.
            if tag in ids and not _volume_due(cache.get(tag), volume_cutoff):
                hid, media_count = ids[tag], None
            else:
                hid, media_count = resolve_hashtag_scrape(worker, tag)
            if not hid:
                return ("no_match", tag, None, None, None, {})
            metrics, cotags = measure_and_harvest_scrape(worker, tag, now=now)
            return ("ok", tag, hid, media_count, metrics, cotags)
        try:
            if client_lock is not None:
                with client_lock:
                    return _go()
            return _go()
        except ScrapeThrottled:
            return ("throttle", tag, None, None, None, {})
        except ScrapeRefused as e:
            return ("refused", tag, None, None, None, e)

    i = 0
    while i < len(queue):
        if tried >= try_cap:
            throttled = True                               # budget, not Instagram — same incomplete-pass stamp
            log("hashtags", "-", "pass_try_cap", tried=tried, queue_left=len(queue) - i,
                cap=try_cap)
            break
        batch_n = min(parallel, try_cap - tried, len(queue) - i)
        batch = queue[i:i + batch_n]
        i += batch_n
        tried += batch_n
        # Run the wave; apply results in QUEUE order so cotag insert priority stays deterministic.
        ordered: list[tuple] = []
        with ThreadPoolExecutor(max_workers=max(1, len(batch))) as pool:
            futs = [pool.submit(_fetch, tag, workers[j % len(workers)]) for j, tag in enumerate(batch)]
            by_tag = {batch[j]: futs[j] for j in range(len(batch))}
            for tag in batch:
                ordered.append(by_tag[tag].result())
        stop = False
        if any(st == "throttle" for st, *_ in ordered):
            throttled = True; ig_throttled = True; stop = True
        for status, tag, hid, media_count, metrics, payload in ordered:
            if status == "throttle":
                continue                                   # apply sibling successes in this wave, then stop
            if status == "no_match":
                unresolved.append({"tag": tag, "reason": "no_match"}); continue
            if status == "refused":
                e = payload
                unresolved.append({"tag": tag, "reason": "refused", "code": getattr(e, "code", None),
                                   "message": getattr(e, "message", str(e))})
                log("hashtags", tag, "unresolved", reason="refused",
                    message=(getattr(e, "message", "") or "")[:120], tried=tried)
                continue
            # status == ok
            ids[tag] = hid
            cotags = payload if isinstance(payload, dict) else {}
            # Outbound discovery: measuring an ANCHOR enqueues novel co-tags — NEVER writes `from`.
            if tag in anchor_set:
                for co, n in cotags.items():
                    if co in anchor_set:
                        continue
                    if co not in queued and cotag_enqueued < cotag_cap:
                        queued.add(co); queue.insert(i, co); discovered += 1; cotag_enqueued += 1
            # Inbound membership: niche anchors appearing on THIS tag's Top → `from` (corpus admission).
            for co, n in cotags.items():
                if co in anchor_set and co != tag:
                    attribution.setdefault(tag, {})
                    attribution[tag][co] = attribution[tag].get(co, 0) + n
            if not isinstance(metrics, dict) or _metric(metrics) is None:
                continue
            prev = cache.get(tag) or {}
            rec = {"graph_id": hid, "measured_at": stamp}
            for fk in RECORD_NUM_FIELDS:
                if fk == "media_count":
                    continue                                    # volume ages on its own stamp, below
                fv = _num(metrics.get(fk))
                if fv is not None:
                    rec[fk] = fv
            # An empty / Reel-less Top sample must not ERASE trend evidence a previous pass bought:
            # Instagram serves a photo-only grid transiently, and that is not proof of no Reels.
            if "current_top_reel_play_max_7d" not in rec:
                for fk in ("current_top_reel_play_max_7d", "top_reel_sample_n"):
                    fv = _num(prev.get(fk))
                    if fv is not None:
                        rec[fk] = fv
            vol = _num(media_count)
            if vol is not None:
                rec["media_count"] = vol; rec["media_count_at"] = stamp
            else:                                               # resolve skipped / served nothing: carry
                pv = _num(prev.get("media_count"))
                if pv is not None:
                    rec["media_count"] = pv
                    rec["media_count_at"] = prev.get("media_count_at") or prev.get("measured_at") or stamp
            frm = attribution.get(tag)
            if frm:
                rec["from"] = {k: int(v) for k, v in frm.items()}
            cache[tag] = rec; measured += 1
            if measured % 5 == 0:                                 # mid-pass durable flush — crash loses ≤4 tags
                mid = _records_for_write(cache, anchor_set=anchor_set,
                                         cutoff=now - timedelta(days=_MAX_AGE_DAYS))
                if prev_complete:
                    mid[_COMPLETE_KEY] = prev_complete
                cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
                write_json_atomic(cfg.hashtags_path, mid)
                _rederive_posting_corpora(cfg, now=now)          # Layer B rides the flush — no end-of-pass wait
            if tried == 1 or tried % 5 == 0 or measured % 5 == 0:
                log("hashtags", tag, "measured", tried=tried, measured=measured,
                    queue_left=len(queue) - i, visibility=_metric(rec),
                    rank_field=next((k for k in ("play_count", "like_count") if k in rec), None),
                    media_count=rec.get("media_count"), parallel=parallel)
        if stop:
            break
    cooldown = None
    if measured > 0:
        _clear_cooldown(cfg)                               # any progress resets the Instagram streak
    elif ig_throttled:
        cooldown = _persist_throttle_cooldown(cfg, now)
        log("hashtags", "-", "scrape_cooldown", streak=cooldown.get("streak"),
            until=cooldown.get("until"))
    cutoff = now - timedelta(days=_MAX_AGE_DAYS)
    fresh = _records_for_write(cache, anchor_set=anchor_set, cutoff=cutoff)
    tag_mutated = fresh != pre_write
    if measured == 0 and not tag_mutated:
        # Zero-progress: leave hashtags.json byte/mtime-identical; do not rederive; keep prior stamp.
        out = {"written": False, "measured": 0, "discovered": discovered,
               "total": len(pre_write), "throttled": throttled, "tried": tried,
               "unresolved": unresolved, "backend": "scrape", "parallel": parallel,
               "reason": "no_progress"}
        if cooldown is not None:
            out["cooldown_until"] = cooldown.get("until"); out["cooldown_streak"] = cooldown.get("streak")
        return out
    if not throttled:
        fresh[_COMPLETE_KEY] = stamp                          # only a finished pass buys the 12h silence
    elif prev_complete:
        fresh[_COMPLETE_KEY] = prev_complete                  # preserve; never slide forward on a cut-off
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(cfg.hashtags_path, fresh)
    _rederive_posting_corpora(cfg, now=now)                      # final store write → corpora catch up
    out = {"written": True, "measured": measured, "discovered": discovered,
           "total": len([t for t in fresh if t != _COMPLETE_KEY]), "throttled": throttled,
           "tried": tried, "unresolved": unresolved, "backend": "scrape", "parallel": parallel}
    if cooldown is not None:
        out["cooldown_until"] = cooldown.get("until"); out["cooldown_streak"] = cooldown.get("streak")
    return out


def refresh_store_if_due(cfg: Config, *, max_age_s: int = 43200, scrape_client=None, now=None) -> dict:
    """The constant-update hook the run loop calls each tick: refresh at most once per `max_age_s`
    (default 12h), gated on `last_complete_pass` inside the cache — NOT file mtime, because a throttled
    pass still writes and would otherwise buy twelve hours of silence for almost no work. Needs a scrape
    session (else a clean no-op — the cache is a platform artifact). FAIL-OPEN: any error -> a reason,
    NEVER raises; it must not crash the unattended run. A corrupt personas.json / no_scrape abort is NOT
    a refresh: refresh_store aborts (cache untouched) and this REPORTS the abort so the tick never logs a
    false success on a broken control file.

    Instagram ScrapeThrottled cooldown (MOL-695) is checked BEFORE opening scrape — never sleeps."""
    from fanops.ig_hashtag_scrape import scrape_configured
    if not scrape_configured(cfg) and scrape_client is None:
        return {"refreshed": False, "reason": "no scrape session"}
    try:
        now_dt = now or datetime.now(timezone.utc)
        cool = _read_active_cooldown(cfg, now_dt)
        if cool is not None:
            return {"refreshed": False, "reason": "cooldown",
                    "until": cool.get("until"), "streak": cool.get("streak")}
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
        if not r.get("written"):                              # corrupt/no_scrape/no_progress: preserve, report
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
        hint="numbers are Top-grid median play_count (else like_count); media_count stored when served")
    return 0
