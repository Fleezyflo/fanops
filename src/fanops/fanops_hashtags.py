# src/fanops/fanops_hashtags.py
"""Layer A — the ONLY writer of the hashtag measurement cache (00_control/hashtags.json).

Runtime network source is Safari web (`ig_web_scrape.open_web_session`); instagrapi
(`ig_hashtag_scrape.open_client`) is scrape-login envelope promote only. The Meta Graph
hashtag path is deferred (helpers remain in meta_graph for later — refresh never falls back to Graph).

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
`aborted:no_scrape`) — there is no silent Graph fallback. Platform exceptions flow through untouched
and arm cooldown via `_freeze_for` (auth death → indefinite hold; else class-name reason on the ladder)."""
from __future__ import annotations
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from fanops.config import Config, _SCRAPE_COTAG_ENQUEUE_DEFAULT, _SCRAPE_TRY_CAP_DEFAULT
from fanops.log import get_logger
from fanops.hashtags import (RECORD_NUM_FIELDS, _norm, _metric, _num,
                             load_measurements, ranked_tags)
from fanops.controlio import write_json_atomic

_MAX_AGE_DAYS = 90            # a measurement older than this is history, not evidence — pruned on write
_VOLUME_MAX_AGE_DAYS = 30     # `media_count` re-resolve age (aligned with remesure; MOL-855). Volume moves
                              # slowly — the 12h trend pass must not spend hashtag_info every tick (MOL-691).
_MEASURE_MAX_AGE_DAYS = 30    # remesure (medias_top) only when measured_at is older than this (MOL-855)
_COMPLETE_KEY = "last_complete_pass"   # sibling of tag records; gates the 12h tick (NOT file mtime)
_COOLDOWN_NAME = ".hashtag_scrape_cooldown.json"  # Instagram platform-stop backoff (MOL-695); never sleep
_COOLDOWN_DELAYS_S = (30 * 60, 60 * 60, 2 * 60 * 60, 6 * 60 * 60)  # streak 1..N → 30m, 1h, 2h, 6h cap
_CHECKPOINT_DELAY_S = 12 * 60 * 60  # legacy flat delay; auth death no longer uses this (HT3)
# Auth death (login / challenge cousins): indefinite hold until scrape-login — never the 30m→6h ladder.
_AUTH_DEATH_REASON = "auth_death"
_AUTH_DEATH_DELAY_S = 100 * 365 * 24 * 3600  # far future until; scrape-login is the only clear
_AUTH_DEATH_NAMES = frozenset({
    "LoginRequired", "ClientLoginRequired", "ClientUnauthorizedError",
    "ChallengeError", "ChallengeRequired", "CaptchaChallengeRequired",
})
_AUTH_HOLD_REASONS = frozenset({
    _AUTH_DEATH_REASON, "operator_hold", "checkpoint",
    "LoginRequired", "ClientLoginRequired", "ClientUnauthorizedError",
    "ChallengeRequired", "ChallengeError", "CaptchaChallengeRequired",
    "login_required",
})
_REFRESH_CADENCE_S = 12 * 60 * 60   # the tick's refresh window, and the yardstick an outage is measured in
_EXACT_NAME_QUOTA = 30              # remesure at most this many unique sidecar names / window (HV1-PR4)
_EXACT_NAME_WINDOW_DAYS = 7
# Scrape is ~5–7s/tag. Caps bound a pass so co-tag harvest cannot run unbounded; incomplete passes do NOT
# stamp last_complete_pass. Caps come from Config (env overrides); tests set FANOPS_HASHTAG_SCRAPE_*.
# MOL-854: try_cap is a small per-pass ceiling (25); the UTC day budget on the cooldown blob is the
# local governor (~40 request-units/day). Due-tiered queue (MOL-855) means the cap need not clear every
# cached tag each pass — only unmeasured anchors + aged volume + ≥30d remesure + co-tag headroom.
# MOL-858 nests budget+freeze under accounts[user].
_SCRAPE_DAY_BUDGET = 40        # request-units per UTC day per scrape account (accounts[user].used)
_SCRAPE_TRY_CAP = _SCRAPE_TRY_CAP_DEFAULT
_SCRAPE_COTAG_ENQUEUE_CAP = _SCRAPE_COTAG_ENQUEUE_DEFAULT
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


def _is_auth_hold(rec: dict) -> bool:
    """True when reason is auth death / operator hold — scrape-login clears; clock never does."""
    return isinstance(rec, dict) and rec.get("reason") in _AUTH_HOLD_REASONS


def _day_used(rec: dict, now: datetime) -> int:
    today = _utc_day(now)
    if rec.get("day") == today and isinstance(rec.get("used"), (int, float)):
        return max(int(rec["used"]), 0)
    return 0


def _block_view_for_rec(rec: dict, now: datetime) -> dict | None:
    """Block view for auth hold, live freeze, or day-budget exhaustion; else None (healthy)."""
    if not isinstance(rec, dict):
        return None
    if _is_auth_hold(rec):
        return dict(rec)
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
    used = _day_used(rec, now)
    if used >= _SCRAPE_DAY_BUDGET:
        view = {"reason": "budget", "day": _utc_day(now), "used": used}
        if isinstance(rec.get("streak"), (int, float)):
            view["streak"] = int(rec["streak"])
        return view
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


def _scrub_expired_accounts(blob: dict, now: datetime) -> bool:
    """Drop until/reason once the clock has passed. Auth holds never auto-clear (HT3)."""
    accounts = blob.get("accounts") if isinstance(blob, dict) else None
    if not isinstance(accounts, dict):
        return False
    changed = False
    for rec in accounts.values():
        if not isinstance(rec, dict) or rec.get("until") is None:
            continue
        if _is_auth_hold(rec):
            continue
        if _is_frozen(rec, now):
            continue
        for k in ("until", "reason"):
            if k in rec:
                rec.pop(k, None)
                changed = True
    return changed


def _is_frozen(rec: dict, now: datetime) -> bool:
    """True on auth hold or future until. Budget is not a freeze."""
    if not isinstance(rec, dict):
        return False
    if _is_auth_hold(rec):
        return True
    until = rec.get("until")
    try:
        ts = datetime.fromisoformat(until) if isinstance(until, str) else None
    except ValueError:
        return False
    if ts is None:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return now < ts


def scrape_user_blocked(cfg: Config, user: str, now: datetime | None = None) -> bool:
    """True when frozen, auth-held, or day-budget-exhausted (`_day_room` ≤ 0). Fail-open."""
    now = now or datetime.now(timezone.utc)
    return _block_view_for_rec(_account_rec(_load_cooldown_blob(cfg), user), now) is not None


def _healthy_scrape_users(cfg: Config, now: datetime, *, allow_reauth: bool = False,
                          require_budget_room: bool = True,
                          require_session: bool = True) -> list[str]:
    """Healthy scrape peers, LRU-oldest accounts[user].updated_at first; env order tiebreak.

    Lock picker: `require_budget_room=True, require_session=False` (Safari; no envelope json).
    Harvest remesure keeps `require_session=True`. `require_budget_room=False` skips only a live
    freeze / auth hold — not day budget (Safari lock / remesure path).
    """
    from fanops.ig_hashtag_scrape import scrape_session_path, scrape_user_usable, scrape_users
    users = scrape_users(cfg)
    blob = _load_cooldown_blob(cfg)
    if _scrub_expired_accounts(blob, now):
        try:
            cfg.control.mkdir(parents=True, exist_ok=True)
            write_json_atomic(_cooldown_path(cfg), blob)
        except OSError:
            pass
    eligible: list[str] = []
    for user in users:
        rec = _account_rec(blob, user)
        if require_budget_room:
            if scrape_user_blocked(cfg, user, now):
                continue
        elif _is_frozen(rec, now):
            continue
        if require_session:
            if allow_reauth:
                if scrape_user_usable(cfg, user):
                    eligible.append(user)
            elif scrape_session_path(cfg, user).exists():
                eligible.append(user)
        else:
            eligible.append(user)

    def _lru_key(user: str) -> tuple[str, int]:
        at = _account_rec(blob, user).get("updated_at")
        stamp = at if isinstance(at, str) else ""
        return (stamp, users.index(user))

    return sorted(eligible, key=_lru_key)


def _pick_healthy_scrape_user(cfg: Config, now: datetime, *, allow_reauth: bool = False,
                              require_budget_room: bool = True,
                              require_session: bool = True) -> str | None:
    """LRU-oldest healthy scrape peer, or None."""
    peers = _healthy_scrape_users(cfg, now, allow_reauth=allow_reauth,
                                  require_budget_room=require_budget_room,
                                  require_session=require_session)
    return peers[0] if peers else None


def _day_room(cfg: Config, user: str | None = None, now: datetime | None = None) -> int:
    """Remaining UTC-day request-units for this scrape identity. Lock and remesure share this."""
    now = now or datetime.now(timezone.utc)
    blob = _load_cooldown_blob(cfg)
    if user:
        rec = _account_rec(blob, user)
    elif isinstance(blob.get("accounts"), dict) and blob["accounts"]:
        rec = {}
    else:
        rec = {k: blob[k] for k in ("day", "used") if k in blob}
    today = _utc_day(now)
    used = int(rec["used"]) if rec.get("day") == today and isinstance(rec.get("used"), (int, float)) else 0
    return max(0, _SCRAPE_DAY_BUDGET - max(used, 0))


def _user_attempt_room(cfg: Config, user: str | None, *, already: int = 0,
                       now: datetime | None = None) -> int:
    """try_cap minus tags already walked this pass. Wire spend is per XHR (delay_range)."""
    return max(0, _scrape_try_cap() - max(int(already), 0))


def _charge_scrape_user(cfg: Config, user: str | None, n: int, *, now=None,
                        stop_exc: BaseException | None = None) -> dict | None:
    """Charge +n live requests onto accounts[user].used (legacy blob when user is None)."""
    now = now or datetime.now(timezone.utc)
    n = max(int(n), 0)
    if stop_exc is not None:
        reason, delay_s = _freeze_for(stop_exc)
        return _persist_cooldown(cfg, now, reason=reason, delay_s=delay_s,
                                 used_delta=n, user=user)
    if n > 0:
        _clear_cooldown(cfg, now=now, used_delta=n, user=user)
    return None


def _read_active_cooldown(cfg: Config, now: datetime) -> dict | None:
    """Return a blocking cooldown view only when NO healthy scrape peer remains.

    Per-account freeze lives under accounts[user]={until,streak,reason,day,used}. A single
    dead account must not idle the tick while a peer can still scrape. used is an XHR
    counter, not a skip gate. With no scrape-user list, fall back to the top-level until
    freeze. Corrupt / unreadable → fail OPEN. Never sleeps."""
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
        # No under-budget peer. An unfrozen session at the day cap is budget, not the first
        # frozen peer's LoginRequired — that label idled the fleet while perca.late still answered.
        from fanops.ig_hashtag_scrape import scrape_session_path
        budget_view = None
        frozen_view = None
        unfrozen_sess = False
        for user in users:
            rec = _account_rec(raw, user)
            view = _block_view_for_rec(rec, now)
            if scrape_session_path(cfg, user).exists() and not _is_frozen(rec, now):
                unfrozen_sess = True
                if view is not None and budget_view is None:
                    budget_view = view
            elif view is not None and frozen_view is None and _is_frozen(rec, now):
                frozen_view = view
        if unfrozen_sess:
            return budget_view
        return frozen_view
    return _block_view_for_rec(raw, now)


def _persist_cooldown(cfg: Config, now: datetime, *, reason: str = "throttle",
                      delay_s: int | None = None, used_delta: int = 0,
                      user: str | None = None) -> dict:
    """Arm the read-and-skip freeze that refresh_store_if_due checks BEFORE opening scrape: bump the
    consecutive streak and write `until` from the ladder (30m→1h→2h→6h cap), or from `delay_s` when the
    failure is auth death / not a rate limit (HT3 indefinite hold; MOL-699).

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
                          "last_request_at": now.isoformat(),
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
        # Keep/bump per-account updated_at — do not wipe what _persist_cooldown wrote.
        if now is not None:
            kept_rec["updated_at"] = now.isoformat()
            kept_rec["last_request_at"] = now.isoformat()
        elif isinstance(rec.get("updated_at"), str) and rec["updated_at"]:
            kept_rec["updated_at"] = rec["updated_at"]
            if isinstance(rec.get("last_request_at"), str) and rec["last_request_at"]:
                kept_rec["last_request_at"] = rec["last_request_at"]
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


_OUTAGE_REMEDY = {  # class-name keys post-909; login_required/throttle = legacy blob aliases
    "auth_death": "run fanops hashtags scrape-login after fixing login/challenge in the app",
    "operator_hold": "operator hold — run fanops hashtags scrape-login when ready to resume",
    "LoginRequired": "run fanops hashtags scrape-login (FanOps Chrome profile, not system Chrome)",
    "login_required": "run fanops hashtags scrape-login (FanOps Chrome profile, not system Chrome)",
    "checkpoint": "verify in the Instagram app, then run fanops hashtags scrape-login",
    "RateLimitError": "Instagram is rate-limiting; the ladder clears it, no operator action does",
    "PleaseWaitFewMinutes": "Instagram is rate-limiting; the ladder clears it, no operator action does",
    "FeedbackRequired": "Instagram blocked the action; the ladder clears it, no operator action does",
    "WebThrottled": "Instagram is rate-limiting; the ladder clears it, no operator action does",
    "SentryBlock": "Instagram blocked this IP/session; the ladder clears it, inspect in the app",
    "throttle": "Instagram is rate-limiting; the ladder clears it, no operator action does",
    "budget": "local UTC day scrape budget exhausted; waits for next UTC day"}


def _freeze_for(exc: BaseException) -> tuple[str, int | None]:
    """Map a platform stop onto (cooldown reason, optional flat delay_s).

    Auth death (LoginRequired / Challenge* cousins) → indefinite `auth_death` until scrape-login —
    never the 30m→6h ladder. Everything else labels with its class name and rides the ladder.
    """
    name = type(exc).__name__
    mro = {c.__name__ for c in type(exc).__mro__}
    if mro & _AUTH_DEATH_NAMES or "Challenge" in name:
        return (_AUTH_DEATH_REASON, _AUTH_DEATH_DELAY_S)
    return (name, None)


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
    if measured > 0 and harvest:
        _rederive_posting_corpora(cfg, now=now)
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
