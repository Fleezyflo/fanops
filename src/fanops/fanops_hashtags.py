# src/fanops/fanops_hashtags.py
"""Layer A — the ONLY writer of the hashtag measurement cache (00_control/hashtags.json).

Network source is instagrapi (`ig_hashtag_scrape`); the Meta Graph hashtag path is deferred
(helpers remain in meta_graph for later — refresh never falls back to Graph).

One pass, per persona that actually posts:

  description -> terms -> anchor tags -> ONE medias_top fetch per tag -> {metric, co-occurring tags}

`persona_terms` returns the operator's declared `niche` and nothing else (MOL-637/MOL-719) — voice/levers
stay on captions+hooks, and durable LLM vocab no longer seeds search (46 of 72 generated terms did not
exist on Instagram; 106 of 107 admissions attributed to a niche root). Territory still expands, from the
platform: measuring a root enqueues its novel co-tags below, and inbound-only membership gates admission
(MOL-643).

Visibility numbers are Instagram's own fields only (see ig_hashtag_scrape): Top-grid median
`play_count` (preferred) / `like_count`, plus `media_count` from hashtag_info when served.
A tag with neither plays nor likes in the Top grid is UNMEASURED and absent — measured tags only.

Missing scrape (no [igscrape] / no user / no session file) aborts LOUDLY (`written:False`,
`aborted:no_scrape`) — there is no silent Graph fallback. An EXPIRED session aborts as `login_required`
and arms the backoff ladder, so a dead session is not re-probed every tick (MOL-727). A throttle ends the
pass but still writes accrued evidence."""
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
_VOLUME_MAX_AGE_DAYS = 30     # `media_count` re-resolve age (aligned with remesure; MOL-855). Volume moves
                              # slowly — the 12h trend pass must not spend hashtag_info every tick (MOL-691).
_MEASURE_MAX_AGE_DAYS = 30    # remesure (medias_top) only when measured_at is older than this (MOL-855)
_COMPLETE_KEY = "last_complete_pass"   # sibling of tag records; gates the 12h tick (NOT file mtime)
_COOLDOWN_NAME = ".hashtag_scrape_cooldown.json"  # Instagram ScrapeThrottled backoff (MOL-695); never sleep
_COOLDOWN_DELAYS_S = (30 * 60, 60 * 60, 2 * 60 * 60, 6 * 60 * 60)  # streak 1..N → 30m, 1h, 2h, 6h cap
_CHECKPOINT_DELAY_S = 12 * 60 * 60  # a LOCK is not a rate limit: one long freeze, never the ladder (MOL-699)
_REFRESH_CADENCE_S = 12 * 60 * 60   # the tick's refresh window, and the yardstick an outage is measured in
# Scrape is ~5–7s/tag. Caps bound a pass so co-tag harvest cannot run unbounded; incomplete passes do NOT
# stamp last_complete_pass. Tests may monkeypatch these module attrs; env overrides win when set.
# MOL-854: try_cap is a small per-pass ceiling (25); the UTC day budget on the cooldown blob is the
# local governor (~40 request-units/day). Due-tiered queue (MOL-855) means the cap need not clear every
# cached tag each pass — only unmeasured anchors + aged volume + ≥30d remesure + co-tag headroom.
# MOL-858 nests budget+freeze under accounts[user].
_SCRAPE_TRY_CAP = 25
_SCRAPE_DAY_BUDGET = 40        # request-units per UTC day per scrape account (accounts[user].used)
_SCRAPE_COTAG_ENQUEUE_CAP = 40
_SCRAPE_PARALLEL = 1          # legacy env knob (FANOPS_HASHTAG_SCRAPE_PARALLEL). MOL-855 fetch is sequential;
                              # this no longer sizes a wave. Pace with FANOPS_HASHTAG_SCRAPE_DELAY.


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
    """Layer B at the END of a Layer A pass — ONCE, and only when the pass measured something (MOL-694).

    A derive is a whole-store recompute per persona, so riding every mid-pass flush ran it ~measures/5
    times for one usable result (a 235-measure pass: 47 rounds). The flush keeps its job — durable
    measurement — and this runs once at the pass end, complete or early-stopped.

    Fail-open: a derive miss must never abort measurement. Uses posting personas only (same gate as
    discovery)."""
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


def _cooldown_path(cfg: Config):
    return cfg.control / _COOLDOWN_NAME


def _cooldown_delay_s(streak: int) -> int:
    i = min(max(int(streak), 1), len(_COOLDOWN_DELAYS_S)) - 1
    return _COOLDOWN_DELAYS_S[i]


def _load_cooldown_blob(cfg: Config) -> dict:
    """Raw cooldown JSON or {}. Corrupt / missing → {} (fail open)."""
    p = _cooldown_path(cfg)
    if not p.exists():
        return {}
    try:
        import json
        raw = json.loads(p.read_text())
    except (OSError, ValueError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _utc_day(now: datetime) -> str:
    return now.astimezone(timezone.utc).date().isoformat()


def _utc_day_end(now: datetime) -> datetime:
    u = now.astimezone(timezone.utc)
    return datetime(u.year, u.month, u.day, tzinfo=timezone.utc) + timedelta(days=1)


def _block_view_for_rec(rec: dict, now: datetime) -> dict | None:
    """Return a freeze/budget view for one account record, or None when that account is healthy."""
    if not isinstance(rec, dict):
        return None
    until = rec.get("until")
    try:
        ts = datetime.fromisoformat(until) if isinstance(until, str) else None
    except ValueError:
        ts = None
    if ts is not None:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if now < ts:
            return dict(rec)
    today = _utc_day(now)
    used = rec.get("used")
    if rec.get("day") == today and isinstance(used, (int, float)) and int(used) >= _SCRAPE_DAY_BUDGET:
        out = dict(rec)
        out["reason"] = "budget"
        out["until"] = _utc_day_end(now).isoformat()
        return out
    return None


def _account_rec(blob: dict, user: str) -> dict:
    """accounts[user] when present; else legacy top-level fields when accounts{} is still empty."""
    accounts = blob.get("accounts") if isinstance(blob.get("accounts"), dict) else {}
    rec = accounts.get(user)
    if isinstance(rec, dict):
        return rec
    if accounts:
        return {}
    # Pre-MOL-858 global blob — treat as this user's state until nested writes replace it.
    if any(k in blob for k in ("until", "streak", "reason", "day", "used")):
        return {k: blob[k] for k in ("until", "streak", "reason", "day", "used", "updated_at")
                if k in blob}
    return {}


def scrape_user_blocked(cfg: Config, user: str, now: datetime | None = None) -> bool:
    """True when this scrape user is frozen or day-budget-exhausted (MOL-858). Fail-open."""
    now = now or datetime.now(timezone.utc)
    return _block_view_for_rec(_account_rec(_load_cooldown_blob(cfg), user), now) is not None


def _pick_healthy_scrape_user(cfg: Config, now: datetime, *, allow_reauth: bool = False) -> str | None:
    """First FANOPS_IG_SCRAPE_USER that can open and is not frozen/budget-exhausted."""
    from fanops.ig_hashtag_scrape import scrape_session_path, scrape_user_usable, scrape_users
    for user in scrape_users(cfg):
        if scrape_user_blocked(cfg, user, now):
            continue
        if allow_reauth:
            if scrape_user_usable(cfg, user):
                return user
        elif scrape_session_path(cfg, user).exists():
            return user
    return None


def _read_active_cooldown(cfg: Config, now: datetime) -> dict | None:
    """Return a blocking cooldown view only when NO healthy under-budget scrape peer remains.

    Per-account freeze lives under accounts[user]={until,streak,reason,day,used} (MOL-858). A single
    dead account must not idle the tick while a peer can still scrape. With no scrape-user list,
    fall back to the pre-MOL-858 top-level until/budget gate (injected-client / single-budget tests).
    Corrupt / unreadable → fail OPEN. Never sleeps."""
    raw = _load_cooldown_blob(cfg)
    if not raw:
        return None
    from fanops.ig_hashtag_scrape import scrape_users
    users = scrape_users(cfg)
    if users:
        # Healthy = under freeze/budget AND openable unattended (session on disk). A password-only
        # peer without a freeze must not clear the gate while every session-bearing peer is dead.
        if _pick_healthy_scrape_user(cfg, now) is not None:
            return None
        for user in users:
            view = _block_view_for_rec(_account_rec(raw, user), now)
            if view is not None:
                return view
        return None
    return _block_view_for_rec(raw, now)


def _persist_cooldown(cfg: Config, now: datetime, *, reason: str = "throttle",
                      delay_s: int | None = None, used_delta: int = 0,
                      user: str | None = None) -> dict:
    """Arm the read-and-skip freeze that refresh_store_if_due checks BEFORE opening scrape: bump the
    consecutive streak and write `until` from the ladder (30m→1h→2h→6h cap), or from `delay_s` when the
    failure is not a rate limit and the ladder would be wrong (a checkpoint LOCK — MOL-699).

    When `user` is set (MOL-858), nest under accounts[user]={until,streak,reason,day,used} so a peer
    can keep scraping. Without `user`, keep the legacy top-level shape (day/used/accounts) for
    injected-client tests and single-budget callers."""
    p = _cooldown_path(cfg)
    today = _utc_day(now)
    prev = _load_cooldown_blob(cfg)
    accounts: dict = dict(prev["accounts"]) if isinstance(prev.get("accounts"), dict) else {}
    # Drop non-dict junk entries so a corrupt accounts value cannot poison the write.
    accounts = {k: dict(v) for k, v in accounts.items() if isinstance(k, str) and isinstance(v, dict)}
    if user:
        rec = _account_rec(prev, user)
        streak = int(rec["streak"]) if isinstance(rec.get("streak"), (int, float)) else 0
        used = int(rec["used"]) if rec.get("day") == today and isinstance(rec.get("used"), (int, float)) else 0
        streak = max(streak, 0) + 1
        used = max(used, 0) + max(int(used_delta), 0)
        delay = _cooldown_delay_s(streak) if delay_s is None else int(delay_s)
        until = (now + timedelta(seconds=delay)).isoformat()
        accounts[user] = {"streak": streak, "until": until, "updated_at": now.isoformat(),
                          "reason": reason, "day": today, "used": used}
        # Per-account write: strip legacy top-level freeze so one dead user cannot global-block.
        blob = {"accounts": accounts, "updated_at": now.isoformat()}
        cfg.control.mkdir(parents=True, exist_ok=True)
        write_json_atomic(p, blob)
        return accounts[user]
    streak = int(prev["streak"]) if isinstance(prev.get("streak"), (int, float)) else 0
    used = int(prev["used"]) if prev.get("day") == today and isinstance(prev.get("used"), (int, float)) else 0
    streak = max(streak, 0) + 1
    used = max(used, 0) + max(int(used_delta), 0)
    delay = _cooldown_delay_s(streak) if delay_s is None else int(delay_s)
    until = (now + timedelta(seconds=delay)).isoformat()
    blob = {"streak": streak, "until": until, "updated_at": now.isoformat(), "reason": reason,
            "day": today, "used": used, "accounts": accounts}
    cfg.control.mkdir(parents=True, exist_ok=True)
    write_json_atomic(p, blob)
    return blob


def _clear_cooldown(cfg: Config, *, now: datetime | None = None, used_delta: int = 0,
                    user: str | None = None) -> None:
    """Clear streak/until/reason. Preserve day/used so a clean success cannot wipe the UTC day
    budget (MOL-854). When `user` is set (MOL-858), clear THAT account's freeze only — peers keep
    theirs. Operator scrape-login lands here per successful user. When `now` is given, `used_delta`
    request-units are charged to today's budget before the streak fields drop."""
    p = _cooldown_path(cfg)
    prev = _load_cooldown_blob(cfg)
    accounts: dict = dict(prev["accounts"]) if isinstance(prev.get("accounts"), dict) else {}
    accounts = {k: dict(v) for k, v in accounts.items() if isinstance(k, str) and isinstance(v, dict)}
    if user:
        rec = dict(_account_rec(prev, user))
        today = _utc_day(now) if now is not None else (rec.get("day") if isinstance(rec.get("day"), str) else None)
        used = 0
        if today is not None and rec.get("day") == today and isinstance(rec.get("used"), (int, float)):
            used = int(rec["used"])
        if now is not None:
            today = _utc_day(now)
            used = max(used, 0) + max(int(used_delta), 0)
        kept_rec: dict = {}
        if today is not None:
            kept_rec["day"] = today
            kept_rec["used"] = max(used, 0)
        accounts[user] = kept_rec
        # Drop legacy top-level freeze keys; keep peer accounts.
        blob: dict = {"accounts": accounts}
        if now is not None:
            blob["updated_at"] = now.isoformat()
        try:
            cfg.control.mkdir(parents=True, exist_ok=True)
            write_json_atomic(p, blob)
        except OSError:
            pass
        return
    today = None
    used = 0
    if now is not None:
        today = _utc_day(now)
        if prev.get("day") == today and isinstance(prev.get("used"), (int, float)):
            used = int(prev["used"])
        used = max(used, 0) + max(int(used_delta), 0)
    elif isinstance(prev.get("day"), str):
        today = prev["day"]
        if isinstance(prev.get("used"), (int, float)):
            used = int(prev["used"])
    kept: dict = {}
    if today is not None:
        kept["day"] = today
        kept["used"] = max(used, 0)
        kept["accounts"] = accounts
    elif accounts:
        kept["accounts"] = accounts
    try:
        if kept:
            cfg.control.mkdir(parents=True, exist_ok=True)
            write_json_atomic(p, kept)
        elif p.exists():
            p.unlink()
    except OSError:
        pass


_OUTAGE_REMEDY = {"login_required": "run fanops hashtags scrape-login",
                  "checkpoint": "verify in the Instagram app, then run fanops hashtags scrape-login",
                  "throttle": "Instagram is rate-limiting; the ladder clears it, no operator action does",
                  "budget": "local UTC day scrape budget exhausted; waits for next UTC day"}


def _outage_level(streak, stalled_s: float | None, cadence_s: float = _REFRESH_CADENCE_S) -> str:
    """How loud an ONGOING scrape freeze is, derived from state ALREADY on disk: the cooldown `streak`
    and the age of `last_complete_pass`. One skipped tick is routine (`info`); a freeze that has outlived
    its own refresh cadence is a `warning`; one that has outlived it twice over — or whose streak has
    reached the ladder cap, where the backoff stopped decaying and is merely repeating — is an `error`.

    This ends the severity INVERSION (MOL-794): arming logged `error` while the daily CONSEQUENCE logged
    `info`, so a five-day outage got quieter the longer it lasted. Severity now only ever rises with the
    outage; nothing here lowers a level a caller already hard-codes."""
    try:
        s = int(streak)
    except (TypeError, ValueError):                            # absent/garbage streak: judge on stall alone
        s = 0
    age = stalled_s if isinstance(stalled_s, (int, float)) else None
    if s >= len(_COOLDOWN_DELAYS_S) or (age is not None and age >= 2 * cadence_s):
        return "error"
    if s >= 2 or (age is not None and age >= cadence_s):
        return "warning"
    return "info"


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

    Order of work (MOL-855 due tiers — NOT every cached tag every pass):
      1. never-measured anchors (must discover)
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

    Layer B runs ONCE, when the pass ENDS (complete or early-stopped) and only when `measured>0`
    (MOL-694). A mid-pass flush is measurement durability alone — deriving on each one recomputed every
    posting persona ~measures/5 times per pass. A measured==0 pass whose write projection equals the tag
    map ALREADY ON DISK skips the write too (byte/mtime-identical; prior `last_complete_pass` kept); a
    projection that differs — orphan eviction, dead-`from` prune, 90d expiry — still WRITES at
    measured==0, but does not derive: the moved file changes the input fingerprint that
    `persona_research.refresh_corpora_if_due` gates on, so the next tick derives from it.

    ABORTS without writing when personas.json is CORRUPT, or when scrape cannot open (`no_scrape`).
    An Instagram throttle ends the pass, writes accrued evidence when mutated, and persists a cooldown
    (30m→1h→2h→6h). `last_complete_pass` advances ONLY when throttled is False (the 12h tick gates on
    that stamp) — try_cap incompleteness and a dead session set throttled too, because an unfinished
    queue is an unfinished queue; of the three only try_cap writes no Instagram cooldown.

    Every failure class that means "stop touching this account" arms that ONE cooldown file, tagged with
    its reason (MOL-699): `throttle` and `login_required` on the ladder, `checkpoint` on a flat 12h freeze
    because a lock is not a rate limit. An expiry raised while OPENING the client arms it too
    (`ScrapeSessionExpired`) — not only an in-pass refusal (MOL-727). Only `no_scrape` (never configured)
    and a corrupt control file arm nothing — there is no account to protect.

    PROGRESS and the STOP SIGNAL are INDEPENDENT (MOL-727): measuring a tag resets the streak, but a
    same-pass throttle / login_required still arms a fresh cooldown from streak 1, so a partial pass
    cannot reopen scrape on the very next tick."""
    from fanops.errors import ControlFileError
    from fanops.ig_hashtag_scrape import (ScrapeCheckpoint, ScrapeRefused, ScrapeSessionExpired,
                                          ScrapeThrottled, ScrapeUnavailable, measure_and_harvest_scrape,
                                          open_client, resolve_hashtag_scrape)
    from fanops.persona_research import persona_terms
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    try:
        personas = _posting_personas(cfg)
    except ControlFileError as e:                          # corrupt personas.json: ABORT, cache UNTOUCHED
        return {"written": False, "aborted": "corrupt_personas", "reason": str(e)}
    scrape_user = None
    if scrape_client is None:
        from fanops.ig_hashtag_scrape import scrape_users as _scrape_users
        listed = _scrape_users(cfg)
        if listed:
            scrape_user = _pick_healthy_scrape_user(cfg, now)
            if scrape_user is None:
                # Only short-circuit when a freeze/budget actually blocks every peer. A listed user
                # with password but no session file is NOT "frozen" — open_client must still run so
                # checkpoint / login_required / no_scrape classify (MOL-858 + MOL-699 tests).
                cool = _read_active_cooldown(cfg, now)
                if cool is not None:
                    return {"written": False, "aborted": "cooldown",
                            "reason": cool.get("reason") or "cooldown",
                            "backend": "scrape", "cooldown_until": cool.get("until"),
                            "cooldown_streak": cool.get("streak")}
                for u in listed:
                    if not scrape_user_blocked(cfg, u, now):
                        scrape_user = u
                        break
        try:
            scrape_client = (open_client(cfg, user=scrape_user, now=now) if scrape_user
                            else open_client(cfg, now=now))
            if scrape_user is None:
                # open_client chose; mirror that choice for per-account persist (MOL-858).
                scrape_user = getattr(scrape_client, "_fanops_scrape_user", None)
        except ScrapeUnavailable as e:                     # ScrapeCheckpoint (a LOCK) is a distinct abort
            if isinstance(e, ScrapeCheckpoint):
                # FREEZE (MOL-699). Without this the abort wrote nothing, so the next due tick logged in
                # again against a locked account — repeated failed logins are what deepen a lock. Only
                # in-app verification (or scrape-login, which clears this) can end it.
                cd = _persist_cooldown(cfg, now, reason="checkpoint", delay_s=_CHECKPOINT_DELAY_S,
                                       user=scrape_user)
                get_logger(cfg)("hashtags", "-", "scrape_cooldown", level="error", reason="checkpoint",
                                until=cd.get("until"), user=(scrape_user or "")[:40],
                                err=str(e)[:160])
                return {"written": False, "aborted": "checkpoint", "reason": str(e), "backend": "scrape",
                        "cooldown_until": cd.get("until"), "cooldown_streak": cd.get("streak")}
            if isinstance(e, ScrapeSessionExpired):
                # Same ladder the IN-PASS login_required refusal gets. Without this an expiry returned
                # `no_scrape` with NO backoff, so every due tick re-probed a dead session forever — the
                # exact every-tick hammering MOL-699 armed the ladder to stop, just one seam earlier.
                cd = _persist_cooldown(cfg, now, reason="login_required", user=scrape_user)
                get_logger(cfg)("hashtags", "-", "scrape_cooldown", level="error", reason="login_required",
                                until=cd.get("until"), user=(scrape_user or "")[:40],
                                err=str(e)[:160])
                return {"written": False, "aborted": "login_required", "reason": str(e), "backend": "scrape",
                        "cooldown_until": cd.get("until"), "cooldown_streak": cd.get("streak")}
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

    # MOL-739: hashtag discovery MUST only proceed if niche seeds exist.
    if not anchors:
        get_logger(cfg)("hashtags", "-", "discovery_skip_no_niche", level="info")
        return {"written": False, "aborted": "discovery_skip_no_niche", "reason": "no personas have a declared niche"}
    volume_cutoff = now - timedelta(days=_VOLUME_MAX_AGE_DAYS)
    measure_cutoff = now - timedelta(days=_MEASURE_MAX_AGE_DAYS)
    # Snapshot the tag map AS IT SITS ON DISK before the pass, so zero-progress can prove the file would
    # not change. Snapshotting the WRITE PROJECTION instead hid exactly the mutations that MUST still
    # write: orphan eviction and dead-`from` prune are performed BY that projection, so both sides carried
    # them and a pass that evicts an orphan looked byte-identical.
    pre_write = {t: dict(r) for t, r in cache.items()}
    unmeasured_anchors = [t for t in anchors if t not in cache]
    queue: list[str] = list(unmeasured_anchors)
    queued: set[str] = set(queue)

    def _extend_tier(candidates: list[str]) -> None:
        for t in sorted(candidates, key=lambda x: _staleness_sort_key(x, cache)):
            if t not in queued:
                queued.add(t); queue.append(t)

    _extend_tier([t for t in cache if _volume_due(cache[t], volume_cutoff)])
    _extend_tier([t for t in cache if _measured_due(cache[t], measure_cutoff)])
    measured = 0; discovered = 0; throttled = False; ig_throttled = False; tried = 0; cotag_enqueued = 0
    login_dead = False
    unresolved: list[dict] = []
    log = get_logger(cfg)
    try_cap = _scrape_try_cap(); cotag_cap = _scrape_cotag_enqueue_cap()
    parallel = 1                                               # sequential fetch (MOL-855); retained in summary

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
        tag = queue[i]
        i += 1
        tried += 1
        status, tag, hid, media_count, metrics, payload = _fetch(tag)
        stop = False
        if status == "throttle":
            throttled = True; ig_throttled = True; stop = True
        elif status == "no_match":
            unresolved.append({"tag": tag, "reason": "no_match"})
        elif status == "refused":
            e = payload
            msg = (getattr(e, "message", None) or str(e) or "")
            unresolved.append({"tag": tag, "reason": "refused", "code": getattr(e, "code", None),
                               "message": getattr(e, "message", str(e))})
            log("hashtags", tag, "unresolved", reason="refused",
                message=msg[:120], tried=tried)
            # Session can open/login while hashtag_info still returns login_required (MOL-696).
            # Abort the pass; the laddered cooldown is armed at the end of the pass (MOL-699).
            if "login_required" in msg.lower():
                # `throttled` means INCOMPLETE PASS (its other writers: Instagram throttle, try_cap).
                # A dead session stops the queue mid-way, so the pass must NOT stamp
                # last_complete_pass and buy 12h of silence off the few tags it got (MOL-727).
                login_dead = True; stop = True; throttled = True
                log("hashtags", "-", "pass_login_required", level="error", tried=tried,
                    detail="Layer A abort — run fanops hashtags scrape-login")
        else:
            # status == ok
            ids[tag] = hid
            cotags = payload if isinstance(payload, dict) else {}
            # Outbound discovery: measuring an ANCHOR enqueues novel co-tags — NEVER writes `from`.
            if tag in anchor_set:
                # NOVEL means UNCACHED. A co-tag already in the cache is the due tiers' business; enqueuing
                # it here re-measured every fresh long-tail tag on every pass — the spend tiers exist to
                # stop (MOL-695).
                for co, n in cotags.items():
                    if co in anchor_set or co in cache:
                        continue
                    if co not in queued and cotag_enqueued < cotag_cap:
                        queued.add(co); queue.insert(i, co); discovered += 1; cotag_enqueued += 1
            # Inbound membership: niche anchors appearing on THIS tag's Top → `from` (corpus admission).
            for co, n in cotags.items():
                if co in anchor_set and co != tag:
                    attribution.setdefault(tag, {})
                    attribution[tag][co] = attribution[tag].get(co, 0) + n
            if isinstance(metrics, dict) and _metric(metrics) is not None:
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
                else:                                               # hashtag_info served no volume: carry prior
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
                    write_json_atomic(cfg.hashtags_path, mid)        # measurement durability ONLY — no derive here
                if tried == 1 or tried % 5 == 0 or measured % 5 == 0:
                    log("hashtags", tag, "measured", tried=tried, measured=measured,
                        queue_left=len(queue) - i, visibility=_metric(rec),
                        rank_field=next((k for k in ("play_count", "like_count") if k in rec), None),
                        media_count=rec.get("media_count"), parallel=parallel)
        if stop:
            break
    cooldown = None
    # MOL-854: NEVER clear when ig_throttled / login_dead — clear-then-rearm reset the streak to 1 and
    # wiped day-budget keys (cooldown sawtooth). Clean success / scrape-login remain the only clears.
    if ig_throttled:
        # MOL-854/858: NEVER clear-then-rearm — that sawtooth reset streak to 1. Persist only.
        cooldown = _persist_cooldown(cfg, now, reason="throttle", used_delta=tried, user=scrape_user)
        log("hashtags", "-", "scrape_cooldown",
            level=_outage_level(cooldown.get("streak"), _age_s(prev_complete, now)),   # re-arming gets LOUDER
            reason="throttle", streak=cooldown.get("streak"), until=cooldown.get("until"),
            user=(scrape_user or "")[:40])
    elif login_dead:
        # A dead session was re-probed EVERY tick before MOL-699. An expiry IS transient, so it gets the
        # ladder (unlike a lock) — a decaying retry instead of one login attempt per tick, forever.
        cooldown = _persist_cooldown(cfg, now, reason="login_required", used_delta=tried, user=scrape_user)
        log("hashtags", "-", "scrape_cooldown",
            level=_outage_level(cooldown.get("streak"), _age_s(prev_complete, now)),   # re-arming gets LOUDER
            reason="login_required", streak=cooldown.get("streak"), until=cooldown.get("until"),
            user=(scrape_user or "")[:40])
    elif measured > 0:
        _clear_cooldown(cfg, now=now, used_delta=tried, user=scrape_user)  # clean progress only
    elif tried > 0:
        _clear_cooldown(cfg, now=now, used_delta=tried, user=scrape_user)  # charge day budget
    cutoff = now - timedelta(days=_MAX_AGE_DAYS)
    fresh = _records_for_write(cache, anchor_set=anchor_set, cutoff=cutoff)
    tag_mutated = fresh != pre_write
    if measured == 0 and not tag_mutated:
        # Zero-progress: leave hashtags.json byte/mtime-identical; do not rederive; keep prior stamp.
        reason = "login_required" if login_dead else "no personas have a declared niche"
        out = {"written": False, "measured": 0, "discovered": discovered,
               "total": len(pre_write), "throttled": throttled, "tried": tried,
               "unresolved": unresolved, "backend": "scrape", "parallel": parallel,
               "reason": reason}
        if login_dead:
            out["aborted"] = "login_required"
        if cooldown is not None:
            out["cooldown_until"] = cooldown.get("until"); out["cooldown_streak"] = cooldown.get("streak")
        return out
    if not throttled:
        fresh[_COMPLETE_KEY] = stamp                          # only a finished pass buys the 12h silence
    elif prev_complete:
        fresh[_COMPLETE_KEY] = prev_complete                  # preserve; never slide forward on a cut-off
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(cfg.hashtags_path, fresh)
    if measured > 0:
        _rederive_posting_corpora(cfg, now=now)                  # the pass END — exactly one derive round
    out = {"written": True, "measured": measured, "discovered": discovered,
           "total": len([t for t in fresh if t != _COMPLETE_KEY]), "throttled": throttled,
           "tried": tried, "unresolved": unresolved, "backend": "scrape", "parallel": parallel}
    if cooldown is not None:
        out["cooldown_until"] = cooldown.get("until"); out["cooldown_streak"] = cooldown.get("streak")
    return out


def refresh_store_if_due(cfg: Config, *, max_age_s: int = _REFRESH_CADENCE_S, scrape_client=None,
                         now=None) -> dict:
    """The constant-update hook the run loop calls each tick: refresh at most once per `max_age_s`
    (default 12h), gated on `last_complete_pass` inside the cache — NOT file mtime, because a throttled
    pass still writes and would otherwise buy twelve hours of silence for almost no work. Needs a scrape
    session (else a clean no-op — the cache is a platform artifact). FAIL-OPEN: any error -> a reason,
    NEVER raises; it must not crash the unattended run. A corrupt personas.json / no_scrape abort is NOT
    a refresh: refresh_store aborts (cache untouched) and this REPORTS the abort so the tick never logs a
    false success on a broken control file.

    Instagram ScrapeThrottled cooldown (MOL-695) is checked BEFORE opening scrape — never sleeps.
    MOL-858: the gate is global only when EVERY scrape peer is frozen or day-budget-exhausted; otherwise
    `refresh_store` rotates to the next healthy under-budget account. A skip under a freeze that has
    outlived the cadence also emits `scrape_outage` at `_outage_level` (MOL-794), and returns that
    `level`: the skip is what an ongoing outage LOOKS like from here, so it cannot stay quieter than
    the arming it repeats."""
    from fanops.ig_hashtag_scrape import scrape_configured
    if not scrape_configured(cfg) and scrape_client is None:
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
        age = _complete_pass_age_s(cfg, now_dt)               # None = no readable stamp -> due
        if age is not None and age < max_age_s:
            return {"refreshed": False, "reason": "fresh"}
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
    """`fanops hashtags scrape-login` — open instagrapi, login, dump session. Never prints the password.

    The operator escape hatch: it deliberately IGNORES an active cooldown (an explicit human act, run
    after clearing a challenge in the app — the freeze exists to stop the unattended pump, not the
    operator) and CLEARS it on success, so a fixed account resumes on the next tick instead of sitting
    out the remaining 12h (MOL-699).

    Sole `allow_reauth=True` call site: the unattended tick validates a restored session via
    `account_info()` and NEVER calls `login()` (a full password re-auth on a stale session earned the
    2026-07-29T22:01Z native checkpoint). Only this verb may re-authenticate.

    Multi-account (MOL-857/858): loop every FANOPS_IG_SCRAPE_USER, dump each session. Clears THAT
    user's freeze on success — peers keep their own cooldown."""
    from fanops.ig_hashtag_scrape import (ScrapeCheckpoint, ScrapeUnavailable, open_client,
                                          scrape_session_path, scrape_users)
    users = scrape_users(cfg)
    if not users:
        get_logger(cfg)("hashtags", "-", "scrape_login_failed", level="error",
                        reason="FANOPS_IG_SCRAPE_USER unset")
        return 2
    ok_n = 0
    for user in users:
        try:
            open_client(cfg, allow_reauth=True, user=user)
        except ScrapeCheckpoint as e:                      # a lock: retrying the login only deepens it
            get_logger(cfg)("hashtags", "-", "scrape_login_checkpoint", level="error",
                            user=user[:40], reason=str(e)[:200])
            continue
        except ScrapeUnavailable as e:
            get_logger(cfg)("hashtags", "-", "scrape_login_failed", level="error",
                            user=user[:40], reason=str(e)[:160])
            continue
        _clear_cooldown(cfg, user=user)                    # MOL-858: clear THIS user only
        ok_n += 1
        get_logger(cfg)("hashtags", "-", "scrape_login_ok", user=user[:40],
                        session=str(scrape_session_path(cfg, user)), cooldown="cleared")
    return 0 if ok_n else 2


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
