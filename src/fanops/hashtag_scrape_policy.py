# src/fanops/hashtag_scrape_policy.py
"""Instagram hashtag scrape policy: cooldown ladder, UTC day budget, freeze/auth-death, peer selection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fanops.config import Config, _SCRAPE_COTAG_ENQUEUE_DEFAULT, _SCRAPE_TRY_CAP_DEFAULT
from fanops.controlio import write_json_atomic

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
# MOL-858 nests budget+freeze under accounts[user].
_SCRAPE_DAY_BUDGET = 40        # request-units per UTC day per scrape account (accounts[user].used)
_SCRAPE_TRY_CAP = _SCRAPE_TRY_CAP_DEFAULT
_SCRAPE_COTAG_ENQUEUE_CAP = _SCRAPE_COTAG_ENQUEUE_DEFAULT

_DEFAULT_OUTAGE_CADENCE_S = 12 * 60 * 60  # matches fanops_hashtags._REFRESH_CADENCE_S


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
    del cfg, user, now
    return max(0, Config().hashtag_scrape_try_cap - max(int(already), 0))


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


def _outage_level(streak, stalled_s: float | None, cadence_s: float = _DEFAULT_OUTAGE_CADENCE_S) -> str:
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
