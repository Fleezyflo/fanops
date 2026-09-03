# src/fanops/fanops_hashtags.py
"""Hashtag measurement cache writer (00_control/hashtags.json).

Live tick: `_remesure_sidecar` measures sidecar pile ∪ lock via Safari
(`ig_web_scrape.open_web_session`). Caption membership is `hashtags.ship_from_lock`,
not this cache. `refresh_store()` without an injected client aborts `safari_only`.
instagrapi `open_client` is scrape-login envelope promote only.

Meters are Instagram's own fields (play_count preferred / like_count, plus media_count).
A tag with neither plays nor likes is UNMEASURED. Platform-stop arms `_freeze_for`
(auth death → indefinite hold)."""
from __future__ import annotations
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from fanops.config import Config
from fanops.log import get_logger
from fanops.hashtags import (RECORD_NUM_FIELDS, _norm, _metric, _num,
                             load_measurements, ranked_tags)
from fanops.controlio import write_json_atomic
from fanops.hashtag_scrape_policy import (
    _COOLDOWN_NAME,
    _OUTAGE_REMEDY,
    _account_rec,
    _charge_scrape_user,
    _clear_cooldown,
    _freeze_for,
    _healthy_scrape_users,
    _load_cooldown_blob,
    _outage_level,
    _persist_cooldown,
    _pick_healthy_scrape_user,
    _read_active_cooldown,
    _user_attempt_room,
    scrape_user_blocked,
)
# Backward-compat re-exports (tests / ig_web_scrape / doctor import via fanops_hashtags)
from fanops.hashtag_scrape_policy import (  # noqa: F401
    _AUTH_DEATH_DELAY_S,
    _AUTH_DEATH_NAMES,
    _AUTH_DEATH_REASON,
    _AUTH_HOLD_REASONS,
    _CHECKPOINT_DELAY_S,
    _COOLDOWN_DELAYS_S,
    _SCRAPE_COTAG_ENQUEUE_CAP,
    _SCRAPE_DAY_BUDGET,
    _SCRAPE_TRY_CAP,
    _block_view_for_rec,
    _cooldown_delay_s,
    _cooldown_path,
    _day_room,
    _day_used,
    _is_auth_hold,
    _is_frozen,
    _scrub_expired_accounts,
    _utc_day,
)

_MAX_AGE_DAYS = 90            # a measurement older than this is history, not evidence — pruned on write
_VOLUME_MAX_AGE_DAYS = 30     # `media_count` re-resolve age (aligned with remesure; MOL-855). Volume moves
                              # slowly — the 12h trend pass must not spend hashtag_info every tick (MOL-691).
_MEASURE_MAX_AGE_DAYS = 30    # remesure (medias_top) only when measured_at is older than this (MOL-855)
_COMPLETE_KEY = "last_complete_pass"   # sibling of tag records; gates the 12h tick (NOT file mtime)
_REFRESH_CADENCE_S = 12 * 60 * 60   # the tick's refresh window, and the yardstick an outage is measured in
_EXACT_NAME_QUOTA = 30              # remesure at most this many unique sidecar names / window (HV1-PR4)
_EXACT_NAME_WINDOW_DAYS = 7
# Scrape is ~5–7s/tag. Caps bound a pass so co-tag harvest cannot run unbounded; incomplete passes do NOT
# stamp last_complete_pass. Caps come from Config (env overrides); tests set FANOPS_HASHTAG_SCRAPE_*.
# MOL-854: try_cap is a small per-pass ceiling (25); the UTC day budget on the cooldown blob is the
# local governor (~40 request-units/day). Due-tiered queue (MOL-855) means the cap need not clear every
# cached tag each pass — only unmeasured anchors + aged volume + ≥30d remesure + co-tag headroom.
# FANOPS_HASHTAG_SCRAPE_PARALLEL read retained via cfg; fetch sequential (MOL-855/912).

_SAFARI_TICK_SLOT: str | None = None   # HT5: one Safari opener per daemon tick (lock OR remesure)


def reset_safari_tick_slot() -> None:
    """Clear the per-tick Safari slot. `_cmd_run_pass` calls this at tick start."""
    global _SAFARI_TICK_SLOT
    _SAFARI_TICK_SLOT = None


def mark_safari_tick_slot(consumer: str) -> None:
    """Record which path opened Safari this tick (`lock` or `remesure`)."""
    global _SAFARI_TICK_SLOT
    _SAFARI_TICK_SLOT = consumer


def safari_tick_slot_claimed() -> str | None:
    """Return the tick's Safari consumer, or None if the slot is still free."""
    return _SAFARI_TICK_SLOT


def _scrape_parallel() -> int:
    return Config().hashtag_scrape_parallel


def _scrape_try_cap() -> int:
    return Config().hashtag_scrape_try_cap


def _scrape_cotag_enqueue_cap() -> int:
    return Config().hashtag_scrape_cotag_enqueue


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


def _age_s(stamp: str | None, now: datetime) -> float | None:
    """Seconds since an ISO stamp, or None when it is absent or unparseable — "how old" is unknown, which
    is NOT the same as zero and must never read as fresh."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        ts = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds()


def _complete_pass_age_s(cfg: Config, now: datetime) -> float | None:
    """Seconds since the last COMPLETE pass. None = no stamp we can read, so the tick is due."""
    return _age_s(_read_complete_pass(cfg), now)


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
    """True when a cached tag must enter the queue for missing / aged Instagram VOLUME (`media_count`).

    Volume was previously fetched only on a tag's very first pass: once `graph_id` was cached the
    hashtag_info call was skipped forever, so 131 of 300 live records carried no `media_count` at all
    and could never acquire one (MOL-691). Queue membership ages on its OWN `media_count_at` stamp — a
    legacy row falls back to `measured_at`. Once a tag is due (this tier or remesure), `_fetch` always
    runs hashtag_info + medias_top together (MOL-856) — there is no volume-only / medias_top-only split."""
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


def _sidecar_tick_names(cfg: Config) -> list[str]:
    """Union of sidecar pile ∪ lock names, first-seen order. Missing/corrupt sidecar → []."""
    from fanops.source_tags import load_source_tag_locks
    table = load_source_tag_locks(cfg)
    out: list[str] = []
    seen: set[str] = set()
    for sid in sorted(k for k in table if isinstance(k, str)):
        rec = table.get(sid)
        if not isinstance(rec, dict):
            continue
        for key in ("pile", "lock"):
            raw = rec.get(key)
            if not isinstance(raw, list):
                continue
            for t in raw:
                n = _norm(t) if isinstance(t, str) else ""
                if n and n not in seen:
                    seen.add(n)
                    out.append(n)
    return out


def _quota_sidecar_names(names: list[str], cache: dict, now: datetime, *,
                         limit: int = _EXACT_NAME_QUOTA,
                         window_days: int = _EXACT_NAME_WINDOW_DAYS) -> list[str]:
    """Names still allowed under the exact-name quota (≤limit unique / window). Oldest first."""
    cutoff = now - timedelta(days=window_days)
    recent = 0
    rest: list[str] = []
    for n in names:
        rec = cache.get(n)
        at = rec.get("measured_at") if isinstance(rec, dict) else None
        ts = None
        if isinstance(at, str) and at:
            try:
                ts = datetime.fromisoformat(at)
            except ValueError:
                ts = None
        if ts is not None:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                recent += 1
                continue
        rest.append(n)
    slots = max(0, int(limit) - recent)
    rest.sort(key=lambda t: _staleness_sort_key(t, cache))
    return rest[:slots]


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


def _refresh_pass(cfg: Config, *, scrape_client=None, now=None, known_names=None) -> dict:
    """Run one measurement pass and rewrite the cache. Returns a summary dict.

    `known_names` is the HV1-PR4 tick remesure path: measure exactly those names, no
    persona_terms / cotag harvest / Layer B. Default None is the manual Layer A pass.

    Order of work (MOL-855 due tiers — NOT every cached tag every pass):
      1. unmeasured anchors (niche roots never measured)
      2. records missing / due `media_count` (rare volume backfill; `_VOLUME_MAX_AGE_DAYS`)
      3. remesure only when `measured_at` is older than `_MEASURE_MAX_AGE_DAYS` (oldest first)
      4. novel (uncached) co-tags inserted mid-pass ahead of remaining remesure (existing cotag cap)
    Within each pre-built tier: oldest `measured_at` then tag. A co-tag already in the cache is left
    to the due tiers, never re-measured just for appearing on an anchor's Top.
    Co-tags harvested from ANCHOR Tops are ENQUEUED for measurement only (discovery) — they must NOT
    write membership edges. Membership `from` is INBOUND only: a measured tag whose own Top captions
    mention a live niche anchor. Outbound-into-`from` was the megatag magnet (one caption hit × huge plays).

    Network work is STRICTLY SEQUENTIAL (MOL-855): one client, one tag at a time, no ThreadPool /
    Lock / wave batching. Pace with the client's own `delay_range`. Concurrent private-API calls from
    clone-clients that share one device fingerprint earned the 2026-07-29 lock (MOL-698); instagrapi
    is not thread-safe.

    Live multi-account (MOL-900): scrape identity is not bound for the whole due queue. Qualifying
    users from `_healthy_scrape_users` (LRU; env list = membership/tiebreak) each open alone, measure
    one tag on the unattended tick (`delay_range` on each request), then the next user
    continues the same queue cursor. Account platform stop (via `_freeze_for`) stops that user only —
    peers keep the pass. Injected `scrape_client` keeps the single-client path. `_pick_healthy_scrape_user`
    (LRU head) remains for cooldown gates.

    All runtime network opens via `open_web_session(cfg, user=u)` — Safari profile map as lock.
    Default harvest without injected client aborts `safari_only`; operator refresh remesures sidecar.

    Layer B runs ONCE, when the pass ENDS (complete or early-stopped) and only when `measured>0`
    (MOL-694). A mid-pass flush is measurement durability alone — deriving on each one recomputed every
    posting persona ~measures/5 times per pass. A measured==0 pass whose write projection equals the tag
    map ALREADY ON DISK skips the write too (byte/mtime-identical; prior `last_complete_pass` kept); a
    projection that differs — orphan eviction, dead-`from` prune, 90d expiry — still WRITES at
    measured==0, but does not derive: the moved file changes the input fingerprint that
    `persona_research.refresh_corpora_if_due` gates on, so the next tick derives from it.

    ABORTS without writing when personas.json is CORRUPT, or when scrape cannot open (`no_scrape`).
    `last_complete_pass` advances ONLY when the due queue was finished (throttled is False).

    Every platform failure that means "stop touching this account" arms that account's cooldown via
    `_freeze_for` + `_persist_cooldown` (HT3/MOL-858): auth death → indefinite hold; else the
    exception class name on the ladder. OUR-state `ScrapeUnavailable` stays `no_scrape` (no freeze).

    PROGRESS and the STOP SIGNAL are INDEPENDENT (MOL-727): measuring a tag resets the streak, but a
    same-pass platform stop still arms a fresh cooldown from streak 1 for that user."""
    from fanops.errors import ControlFileError
    from fanops.ig_hashtag_scrape import (ScrapeUnavailable, measure_and_harvest_scrape,
                                          resolve_hashtag_scrape, scrape_users)
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    harvest = known_names is None
    if harvest and scrape_client is None:
        return {"written": False, "aborted": "safari_only",
                "reason": ("Layer A instagrapi discovery removed — hashtag network is Safari web only; "
                           "operator `fanops hashtags refresh` remesures sidecar names via Safari"),
                "backend": "safari"}
    if harvest:
        from fanops.persona_research import persona_terms
        try:
            personas = _posting_personas(cfg)
        except ControlFileError as e:                      # corrupt personas.json: ABORT, cache UNTOUCHED
            return {"written": False, "aborted": "corrupt_personas", "reason": str(e)}
    else:
        personas = []

    injected = scrape_client is not None
    listed: list[str] = []
    if not injected:
        listed = scrape_users(cfg)
        if listed and _pick_healthy_scrape_user(cfg, now) is None:
            # Only short-circuit when a freeze/budget actually blocks every peer. A listed user
            # with password but no session file is NOT "frozen" — opener must still classify
            # platform errors / no_scrape (MOL-858 + MOL-699 tests).
            cool = _read_active_cooldown(cfg, now)
            if cool is not None:
                return {"written": False, "aborted": "cooldown",
                        "reason": cool.get("reason") or "cooldown",
                        "backend": "scrape", "cooldown_until": cool.get("until"),
                        "cooldown_streak": cool.get("streak")}

    prev_complete = _read_complete_pass(cfg)
    cache: dict[str, dict] = dict(load_measurements(cfg))
    ids: dict[str, str] = {t: r["graph_id"] for t, r in cache.items()}
    attribution: dict[str, dict] = {t: dict(r.get("from") or {}) for t, r in cache.items()}
    anchors: list[str] = []
    if harvest:
        for per in personas:
            for term in persona_terms(per, cfg):
                a = _norm("#" + term)
                if a and a not in anchors:
                    anchors.append(a)
        # MOL-739: Layer A discovery MUST only proceed if niche seeds exist.
        if not anchors:
            get_logger(cfg)("hashtags", "-", "discovery_skip_no_niche", level="info")
            return {"written": False, "aborted": "discovery_skip_no_niche",
                    "reason": "no personas have a declared niche"}
    anchor_set = set(anchors)
    volume_cutoff = now - timedelta(days=_VOLUME_MAX_AGE_DAYS)
    measure_cutoff = now - timedelta(days=_MEASURE_MAX_AGE_DAYS)
    pre_write = {t: dict(r) for t, r in cache.items()}
    if harvest:
        unmeasured_anchors = [t for t in anchors if t not in cache]
        queue: list[str] = list(unmeasured_anchors)
        queued: set[str] = set(queue)

        def _extend_tier(candidates: list[str]) -> None:
            for t in sorted(candidates, key=lambda x: _staleness_sort_key(x, cache)):
                if t not in queued:
                    queued.add(t); queue.append(t)

        _extend_tier([t for t in cache if _volume_due(cache[t], volume_cutoff)])
        _extend_tier([t for t in cache if _measured_due(cache[t], measure_cutoff)])
    else:
        queue = [_norm(t) for t in (known_names or []) if _norm(t)]
        queued = set(queue)

    def _persist_anchors() -> set[str]:
        if harvest:
            return anchor_set
        return set(cache) | set(queue)
    measured = 0; discovered = 0; throttled = False; tried = 0; cotag_enqueued = 0
    platform_stop = False
    stop_reason_word: str | None = None
    unresolved: list[dict] = []
    log = get_logger(cfg)
    try_cap = _scrape_try_cap(); cotag_cap = _scrape_cotag_enqueue_cap()
    _ = _scrape_parallel()  # retain env read (MOL-912); value unused — fetch is sequential
    client = scrape_client
    scrape_user = None
    cooldown = None
    i = 0

    def _fetch(tag: str):
        """Resolve+measure one tag. Returns (status, tag, hid|None, media_count|None, metrics|None, cotags|exc).

        MOL-856: a due visit always spends BOTH hashtag_info (volume) and medias_top (visibility).
        Fresh tags stay off the queue (MOL-855); there is no path that remesures Top while skipping
        volume, and no volume-only remesure split."""
        try:
            hid, media_count = resolve_hashtag_scrape(client, tag)
            if not hid:
                return ("no_match", tag, None, None, None, {})
            metrics, cotags = measure_and_harvest_scrape(client, tag, now=now)
            return ("ok", tag, hid, media_count, metrics, cotags)
        except Exception as e:                                  # noqa: BLE001 — platform errors flow as payload
            get_logger(cfg)("hashtags", tag, "unresolved", reason="error", message=str(e), tried=tried)
            return ("error", tag, None, None, None, e)

    def _measure_slice(user_cap: int) -> tuple[int, BaseException | None]:
        """Measure up to user_cap tags from shared queue cursor. Returns (user_tried, stop_exc|None).

        Continue-on-tag only for ClientNotFoundError (lazy isinstance); any other platform error stops."""
        nonlocal i, tried, measured, discovered, cotag_enqueued
        try:
            from instagrapi.exceptions import ClientNotFoundError
        except ImportError:
            ClientNotFoundError = ()  # fail closed: never continue-classify without the lib
        user_tried = 0
        while i < len(queue) and user_tried < user_cap:
            tag = queue[i]
            i += 1
            user_tried += 1
            tried += 1
            status, tag, hid, media_count, metrics, payload = _fetch(tag)
            if status == "error":
                e = payload
                unresolved.append({"tag": tag, "reason": "refused", "code": getattr(e, "code", None),
                                   "message": str(e)})
                if isinstance(payload, ClientNotFoundError):
                    continue
                return user_tried, e
            if status == "no_match":
                unresolved.append({"tag": tag, "reason": "no_match"})
                continue
            ids[tag] = hid
            cotags = payload if isinstance(payload, dict) else {}
            if harvest and tag in anchor_set:
                for co, n in cotags.items():
                    if co in anchor_set or co in cache:
                        continue
                    if co not in queued and cotag_enqueued < cotag_cap:
                        queued.add(co); queue.insert(i, co); discovered += 1; cotag_enqueued += 1
            if harvest:
                for co, n in cotags.items():
                    if co in anchor_set and co != tag:
                        attribution.setdefault(tag, {})
                        attribution[tag][co] = attribution[tag].get(co, 0) + n
            if isinstance(metrics, dict) and _metric(metrics) is not None:
                prev = cache.get(tag) or {}
                rec = {"graph_id": hid, "measured_at": stamp}
                for fk in RECORD_NUM_FIELDS:
                    if fk == "media_count":
                        continue
                    fv = _num(metrics.get(fk))
                    if fv is not None:
                        rec[fk] = fv
                if "current_top_reel_play_max_7d" not in rec:
                    for fk in ("current_top_reel_play_max_7d", "top_reel_sample_n"):
                        fv = _num(prev.get(fk))
                        if fv is not None:
                            rec[fk] = fv
                vol = _num(media_count)
                if vol is not None:
                    rec["media_count"] = vol; rec["media_count_at"] = stamp
                else:
                    pv = _num(prev.get("media_count"))
                    if pv is not None:
                        rec["media_count"] = pv
                        rec["media_count_at"] = prev.get("media_count_at") or prev.get("measured_at") or stamp
                if harvest:
                    frm = attribution.get(tag)
                    if frm:
                        rec["from"] = {k: int(v) for k, v in frm.items()}
                elif isinstance(prev.get("from"), dict) and prev["from"]:
                    rec["from"] = prev["from"]
                cache[tag] = rec; measured += 1
                if measured % 5 == 0:
                    mid = _records_for_write(cache, anchor_set=_persist_anchors(),
                                             cutoff=now - timedelta(days=_MAX_AGE_DAYS))
                    if prev_complete:
                        mid[_COMPLETE_KEY] = prev_complete
                    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
                    write_json_atomic(cfg.hashtags_path, mid)
                if tried == 1 or tried % 5 == 0 or measured % 5 == 0:
                    log("hashtags", tag, "measured", tried=tried, measured=measured,
                        queue_left=len(queue) - i, visibility=_metric(rec),
                        rank_field=next((k for k in ("play_count", "like_count") if k in rec), None),
                        media_count=rec.get("media_count"))
        return user_tried, None

    def _charge_user(user: str | None, user_tried: int, stop_exc: BaseException | None) -> dict | None:
        nonlocal platform_stop, stop_reason_word
        n = user_tried if harvest else 0
        cd = _charge_scrape_user(cfg, user, n, now=now, stop_exc=stop_exc)
        if stop_exc is not None:
            platform_stop = True
            reason, _delay_s = _freeze_for(stop_exc)
            stop_reason_word = reason
            log("hashtags", "-", "scrape_cooldown",
                level=_outage_level((cd or {}).get("streak"), _age_s(prev_complete, now)),
                reason=reason, streak=(cd or {}).get("streak"), until=(cd or {}).get("until"),
                user=(user or "")[:40])
        return cd

    def _open_pass_client(user=None):
        """Safari web session only. Never instagrapi / Chrome cookie inject."""
        from fanops.ig_web_scrape import open_web_session
        return open_web_session(cfg) if user is None else open_web_session(cfg, user=user)

    def _open_single_fallback() -> tuple[str, str] | None:
        """Open one client when no session-ready walk list (inject-like / password-only classify)."""
        nonlocal client, scrape_user, cooldown
        scrape_user = None
        try:
            client = _open_pass_client()
            scrape_user = getattr(client, "_fanops_scrape_user", None)
        except ScrapeUnavailable as e:
            return ("no_scrape", str(e))
        except Exception as e:                                  # noqa: BLE001 — platform errors from the opener
            reason, delay_s = _freeze_for(e)
            cd = _persist_cooldown(cfg, now, reason=reason, delay_s=delay_s, user=scrape_user)
            cooldown = cd
            get_logger(cfg)("hashtags", "-", "scrape_cooldown", level="error", reason=reason,
                            until=cd.get("until"), user=(scrape_user or "")[:40], err=str(e))
            return (reason, str(e))
        return None

    if injected:
        client = scrape_client
        scrape_user = getattr(scrape_client, "_fanops_scrape_user", None)
        user_tried, stop_exc = _measure_slice(try_cap)
        cooldown = _charge_user(scrape_user, user_tried, stop_exc)
        throttled = (i < len(queue)) or (stop_exc is not None)
        if stop_exc is None and user_tried >= try_cap and i < len(queue):
            throttled = True
            log("hashtags", "-", "pass_try_cap", tried=tried, queue_left=len(queue) - i, cap=try_cap)
    else:
        walk: list[tuple[str, int]] = []
        # Harvest needs an envelope on disk. Tick remesure walks FANOPS_IG_SCRAPE_USER
        # (Safari profile map, #1029) and does not require ig_scrape_session_*.json.
        if harvest:
            peers = _healthy_scrape_users(cfg, now, allow_reauth=False)
        else:
            peers = [u for u in listed if not scrape_user_blocked(cfg, u, now)]
        for u in peers:
            cap = _user_attempt_room(cfg, u, now=now)
            if not harvest:
                cap = min(cap, 1)
            if cap <= 0:
                continue
            walk.append((u, cap))
        if not walk:
            err = _open_single_fallback()
            if err is not None:
                kind, detail = err
                out = {"written": False, "aborted": kind, "reason": detail, "backend": "scrape"}
                if kind != "no_scrape":
                    raw = _load_cooldown_blob(cfg)
                    rec = _account_rec(raw, scrape_user) if scrape_user else raw
                    out["cooldown_until"] = rec.get("until"); out["cooldown_streak"] = rec.get("streak")
                return out
            user_tried, stop_exc = _measure_slice(try_cap)
            cooldown = _charge_user(scrape_user, user_tried, stop_exc)
            throttled = (i < len(queue)) or (stop_exc is not None)
            if stop_exc is None and user_tried >= try_cap and i < len(queue):
                throttled = True
                log("hashtags", "-", "pass_try_cap", tried=tried, queue_left=len(queue) - i, cap=try_cap)
        else:
            opened_any = False
            last_open_abort: tuple[str, str] | None = None
            for u, user_cap in walk:
                if i >= len(queue):
                    break
                try:
                    client = _open_pass_client(u)
                except ScrapeUnavailable as e:
                    last_open_abort = ("no_scrape", str(e))
                    continue
                except Exception as e:                          # noqa: BLE001 — platform errors from the opener
                    reason, delay_s = _freeze_for(e)
                    cd = _persist_cooldown(cfg, now, reason=reason, delay_s=delay_s, user=u)
                    cooldown = cd
                    last_open_abort = (reason, str(e))
                    get_logger(cfg)("hashtags", "-", "scrape_cooldown", level="error",
                                    reason=reason, until=cd.get("until"), user=u[:40], err=str(e))
                    continue
                opened_any = True
                scrape_user = u
                user_tried, stop_exc = _measure_slice(user_cap)
                cd = _charge_user(u, user_tried, stop_exc)
                if cd is not None:
                    cooldown = cd
            if not opened_any:
                if last_open_abort:
                    kind, detail = last_open_abort
                    out = {"written": False, "aborted": kind, "reason": detail, "backend": "scrape"}
                    if cooldown is not None:
                        out["cooldown_until"] = cooldown.get("until")
                        out["cooldown_streak"] = cooldown.get("streak")
                    return out
                if queue:  # due work remained; never opened anyone
                    return {"written": False, "aborted": "no_scrape", "reason": "no scrape session",
                            "backend": "scrape"}
            throttled = (i < len(queue)) or platform_stop
            if throttled and tried > 0 and not platform_stop:
                log("hashtags", "-", "pass_try_cap", tried=tried, queue_left=len(queue) - i,
                    cap=try_cap)

    cutoff = now - timedelta(days=_MAX_AGE_DAYS)
    fresh = _records_for_write(cache, anchor_set=_persist_anchors(), cutoff=cutoff)
    tag_mutated = fresh != pre_write
    if measured == 0 and not tag_mutated and tried > 0:
        # platform_stop → aborted = _freeze_for reason word; else honest zero-measured (MOL-912).
        # tried==0 (empty due queue) falls through so last_complete_pass can advance.
        if platform_stop:
            reason = stop_reason_word or "platform_stop"
            out = {"written": False, "measured": 0, "discovered": discovered,
                   "total": len(pre_write), "throttled": throttled, "tried": tried,
                   "unresolved": unresolved, "backend": "scrape",
                   "reason": reason, "aborted": reason}
        else:
            reason = "zero measured"
            out = {"written": False, "measured": 0, "discovered": discovered,
                   "total": len(pre_write), "throttled": throttled, "tried": tried,
                   "unresolved": unresolved, "backend": "scrape",
                   "reason": reason}
        if cooldown is not None:
            out["cooldown_until"] = cooldown.get("until"); out["cooldown_streak"] = cooldown.get("streak")
        return out
    if not throttled:
        fresh[_COMPLETE_KEY] = stamp
    elif prev_complete:
        fresh[_COMPLETE_KEY] = prev_complete
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(cfg.hashtags_path, fresh)
    out = {"written": True, "measured": measured, "discovered": discovered,
           "total": len([t for t in fresh if t != _COMPLETE_KEY]), "throttled": throttled,
           "tried": tried, "unresolved": unresolved, "backend": "scrape"}
    if cooldown is not None:
        out["cooldown_until"] = cooldown.get("until"); out["cooldown_streak"] = cooldown.get("streak")
    return out




def _remesure_sidecar(cfg: Config, *, scrape_client=None, now=None, names: list[str]) -> dict:
    """Tick remesure: measure `names` only. Same lease as refresh_store. No Layer A discovery."""
    with _pass_lease(cfg) as held:
        if not held:
            get_logger(cfg)("hashtags", "-", "pass_busy", reason="another measurement pass holds the lease")
            return {"written": False, "aborted": "busy",
                    "reason": "another Layer A measurement pass holds 00_control/hashtags.lock"}
        return _refresh_pass(cfg, scrape_client=scrape_client, now=now, known_names=names)


def refresh_store_if_due(cfg: Config, *, max_age_s: int = _REFRESH_CADENCE_S, scrape_client=None,
                         now=None) -> dict:
    """The constant-update hook the run loop calls each tick: remesure sidecar pile∪lock names
    at most once per `max_age_s` (default 12h), gated on `last_complete_pass` inside the cache —
    NOT file mtime. Queue is NEVER `persona_terms` (HV1-PR4). Empty sidecar is a clean no-op
    (`refreshed: False`), not `discovery_skip_no_niche`. Exact-name quota ≤30 unique / 7 days.

    Configured = FANOPS_IG_SCRAPE_USER listed. Password / Chrome dumps / envelope json do not
    count. Safari authority is the opener (`open_web_session`), not this gate — probing Safari
    on every tick would hit Instagram when the cache is still fresh. FAIL-OPEN: any error -> a
    reason, NEVER raises. Instagram platform-stop cooldown (MOL-695) is checked BEFORE opening
    scrape — never sleeps.
    MOL-858: the gate is global only when EVERY scrape peer is frozen or day-budget-exhausted.
    A skip under a freeze that has outlived the cadence also emits `scrape_outage` at
    `_outage_level` (MOL-794). Manual `fanops hashtags refresh` still runs Layer A via
    `refresh_store` — this tick does not call that path."""
    from fanops.ig_hashtag_scrape import scrape_users
    if scrape_client is None and not scrape_users(cfg):
        return {"refreshed": False, "reason": "no scrape session"}
    try:
        now_dt = now or datetime.now(timezone.utc)
        cool = _read_active_cooldown(cfg, now_dt)
        if cool is not None:
            reason = cool.get("reason") or "throttle"
            stalled = _complete_pass_age_s(cfg, now_dt)
            level = _outage_level(cool.get("streak"), stalled, max_age_s)
            if level != "info":
                # A routine skipped tick stays quiet; a freeze that has outlived the cadence says so ONCE
                # PER TICK at its own severity. The tick's own `store_refresh_skipped` line is a fact about
                # this tick — this is the fact about the OUTAGE, which is what an alert has to see.
                get_logger(cfg)("hashtags", "-", "scrape_outage", level=level, reason=reason,
                                streak=cool.get("streak"), until=cool.get("until"),
                                stalled_h=("unknown" if stalled is None else round(stalled / 3600.0, 1)),
                                detail="Layer A measurement stalled — " + _OUTAGE_REMEDY.get(
                                    reason, "inspect 00_control/" + _COOLDOWN_NAME))
            return {"refreshed": False, "reason": "cooldown", "level": level, "cooldown_reason": reason,
                    "until": cool.get("until"), "streak": cool.get("streak")}
        names = _sidecar_tick_names(cfg)
        if not names:
            return {"refreshed": False}
        age = _complete_pass_age_s(cfg, now_dt)               # None = no readable stamp -> due
        if age is not None and age < max_age_s:
            return {"refreshed": False, "reason": "fresh"}
        visit = _quota_sidecar_names(names, load_measurements(cfg), now_dt)
        if not visit:
            return {"refreshed": False, "reason": "quota"}
        if safari_tick_slot_claimed():
            return {"refreshed": False, "reason": "safari_tick_slot"}
        mark_safari_tick_slot("remesure")
        r = _remesure_sidecar(cfg, scrape_client=scrape_client, now=now_dt, names=visit)
        if not r.get("written"):                              # no_scrape/no_progress: preserve, report
            return {"refreshed": False, **r}
        return {"refreshed": True, **r}
    except Exception as exc:                                  # noqa: BLE001 — the tick must never die here
        get_logger(cfg)("hashtags", "-", "refresh_error", err=str(exc)[:160])
        return {"refreshed": False, "reason": f"error: {str(exc)}"}


def cmd_hashtags_refresh(cfg: Config) -> int:
    """`fanops hashtags refresh` — Safari remesure of sidecar pile∪lock names. No instagrapi discovery.

    Without FANOPS_IG_SCRAPE_USER or with no sidecar names / quota exhausted, aborts loudly (exit 2)."""
    from fanops.ig_hashtag_scrape import scrape_users
    if not scrape_users(cfg):
        get_logger(cfg)("hashtags", "-", "refresh_aborted", level="error",
                        aborted="no_scrape", reason="FANOPS_IG_SCRAPE_USER unset",
                        detail=("set FANOPS_IG_SCRAPE_USER and run fanops hashtags scrape-login"))
        return 2
    names = _sidecar_tick_names(cfg)
    if not names:
        get_logger(cfg)("hashtags", "-", "refresh_aborted", level="error",
                        aborted="no_sidecar", reason="no sidecar pile∪lock names to remesure",
                        detail=("populate source_tag_locks.json with pile or lock names first"))
        return 2
    now_dt = datetime.now(timezone.utc)
    visit = _quota_sidecar_names(names, load_measurements(cfg), now_dt)
    if not visit:
        get_logger(cfg)("hashtags", "-", "refresh_aborted", level="error",
                        aborted="quota", reason="exact-name quota exhausted for this window",
                        detail=("wait for the 7-day quota window or rely on the unattended tick"))
        return 2
    r = _remesure_sidecar(cfg, now=now_dt, names=visit)
    if not r.get("written"):
        get_logger(cfg)("hashtags", "-", "refresh_aborted", level="error",
                        aborted=r.get("aborted", "unknown"), reason=r.get("reason", ""),
                        detail=("00_control/hashtags.json left untouched; "
                                "fanops hashtags scrape-login for Safari session"))
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
                        message=(u.get("message") or ""))
    return 0


def cmd_hashtags_scrape_login(cfg: Config) -> int:
    """`fanops hashtags scrape-login` — open Safari on Instagram, promote the envelope.

    The operator escape hatch: it deliberately IGNORES an active cooldown (an explicit human act, run
    after clearing a challenge in the app — the freeze exists to stop the unattended pump, not the
    operator) and CLEARS it on success, so a fixed account resumes on the next tick instead of sitting
    out the remaining 12h (MOL-699).

    Sole `allow_reauth=True` call site. Opens Safari to instagram.com (never Google Chrome —
    a FanOps Chrome instance hijacks the Dock). Waits until Safari's Instagram tab is
    logged in, then best-effort promotes the device envelope. Lock scrape fetch()es inside
    that Safari tab. Never password login.

    Multi-account (MOL-857/858): loop every FANOPS_IG_SCRAPE_USER, promote each envelope. Clears THAT
    user's freeze on success — peers keep their own cooldown."""
    from fanops.ig_hashtag_scrape import (
        ensure_scrape_chrome, open_client, scrape_chrome_profile_dir,
        scrape_session_path, scrape_users, wait_for_scrape_profile_auth,
    )
    users = scrape_users(cfg)
    if not users:
        get_logger(cfg)("hashtags", "-", "scrape_login_failed", level="error",
                        reason="FANOPS_IG_SCRAPE_USER unset")
        return 2
    ok_n = 0
    for user in users:
        profile = scrape_chrome_profile_dir(cfg, user)
        profile.mkdir(parents=True, exist_ok=True)
        if not ensure_scrape_chrome(cfg, user, restart=True):
            get_logger(cfg)("hashtags", "-", "scrape_login_failed", level="error",
                            user=user[:40], reason="safari-missing")
            continue
        if wait_for_scrape_profile_auth(cfg, user) is None:
            get_logger(cfg)("hashtags", "-", "scrape_login_failed", level="error",
                            user=user[:40], reason="no profile session")
            continue
        try:
            open_client(cfg, allow_reauth=True, user=user)
        except Exception as e:                                  # noqa: BLE001 — envelope is best-effort
            get_logger(cfg)("hashtags", "-", "scrape_login_envelope",
                            user=user[:40], reason=str(e)[:160])
        _clear_cooldown(cfg, user=user)                    # MOL-858: clear THIS user only
        ok_n += 1
        get_logger(cfg)("hashtags", "-", "scrape_login_ok", user=user[:40],
                        session=str(scrape_session_path(cfg, user)), cooldown="cleared")
    return 0 if ok_n else 2


def cmd_hashtags_discover(cfg: Config) -> int:
    """`fanops hashtags discover` — each native source's lock. READ-ONLY, ZERO NETWORK."""
    from fanops.ledger import Ledger
    from fanops.source_tags import load_source_tag_locks
    log = get_logger(cfg)
    try:
        led = Ledger.load(cfg)
    except Exception as exc:                                  # noqa: BLE001 — a report must never break a schedule
        get_logger(cfg)("hashtags", "-", "discover_skipped", level="warning", err=str(exc)[:160]); return 0
    table = load_source_tag_locks(cfg)
    n = 0
    for src in led.sources.values():
        if getattr(src, "origin_kind", "native") == "third_party":
            continue
        sid = str(getattr(src, "id", "") or "")
        if not sid:
            continue
        rec = table.get(sid) if isinstance(table.get(sid), dict) else {}
        lock = [t for t in (rec.get("lock") or []) if isinstance(t, str)]
        at = rec.get("researched_at")
        if isinstance(at, str) and at.strip():
            state = "empty" if not lock else "ready"
        elif rec:
            state = "in_progress"
        else:
            state = "missing"
        log("hashtags", sid, "lock", state=state, n=len(lock), tags=", ".join(lock[:12]))
        n += 1
    if not n:
        log("hashtags", "-", "no_sources", level="warning",
            hint="ingest a native source first"); return 0
    log("hashtags", "-", "discover_done", sources=n,
        hint="posted tags = lock intersect model picks, cap 4")
    return 0
