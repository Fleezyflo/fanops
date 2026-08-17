"""Hashtag Layer A network via instagrapi (Graph hashtag path deferred)."""
from __future__ import annotations
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from fanops.config import Config
from fanops.hashtags import CAPTION_TAG_RE, HARVEST_CAP, TOP_SAMPLE_N, _norm, _num
from fanops.log import get_logger

_log = logging.getLogger(__name__)

_REEL_TREND_DAYS = 7            # a Reel older than this is history, not "currently trending"
_REEL_PRODUCT_TYPE = "clips"    # Instagram's own product_type for a Reel


def _scrape_delay_range():
    """instagrapi delay_range via Config.hashtag_scrape_delay (sole getenv door; runtime fail-open)."""
    return Config().hashtag_scrape_delay


class ScrapeUnavailable(Exception):
    """OUR scrape state — unset user, no session, extra missing, user not listed, all frozen.
    Must stay importable without [igscrape]. Platform errors are never wrapped into this."""


_LEGACY_SESSION_USER = "perca.late"  # sole owner of control/ig_scrape_session.json (MOL-857)


def scrape_users(cfg: Config) -> list[str]:
    """Parse FANOPS_IG_SCRAPE_USER — comma-separated preference order (MOL-857). Empty when unset."""
    raw = (cfg.ig_scrape_user or "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def scrape_session_path(cfg: Config, user: str):
    """Per-user session under cfg.control. Legacy ig_scrape_session.json maps to perca.late only."""
    named = cfg.control / f"ig_scrape_session_{user}.json"
    if user != _LEGACY_SESSION_USER:
        return named
    return named if named.exists() else (cfg.control / "ig_scrape_session.json")


def _scrape_password_env_key(user: str) -> str:
    """Sanitize username → FANOPS_IG_SCRAPE_PASSWORD_<KEY> (uppercase; non-alnum → _)."""
    key = "".join(c if c.isalnum() else "_" for c in user.upper())
    return f"FANOPS_IG_SCRAPE_PASSWORD_{key}"


def scrape_password_for(user: str) -> str | None:
    """Per-user password env, then shared FANOPS_IG_SCRAPE_PASSWORD. Never logged.

    Per-user lookup uses `os.environ` membership + subscript (not `os.getenv(computed)`): the arch
    extractor treats a non-literal getenv key as UNKNOWN_IMPACT (MOL-857). Same secret resolution.
    """
    from fanops.secret_provider import resolve_secret
    specific_key = _scrape_password_env_key(user)
    raw = os.environ[specific_key] if specific_key in os.environ else None
    specific = resolve_secret(specific_key, raw.strip() if raw and raw.strip() else None)
    if specific:
        return specific
    return Config().ig_scrape_password


def scrape_user_usable(cfg: Config, user: str) -> bool:
    """True when this user has a session file or a resolvable password."""
    return scrape_session_path(cfg, user).exists() or bool(scrape_password_for(user))


def any_scrape_session(cfg: Config) -> bool:
    """True when any listed scrape user has a session file on disk (doctor soft-ok gate)."""
    return any(scrape_session_path(cfg, u).exists() for u in scrape_users(cfg))


def scrape_configured(cfg: Config) -> bool:
    """True when ANY listed scrape user has a session file OR a password (MOL-857)."""
    return any(scrape_user_usable(cfg, u) for u in scrape_users(cfg))


_SESSION_DEAD_NAMES = frozenset({
    "LoginRequired", "ClientLoginRequired",
    "ChallengeError", "ChallengeRequired", "CaptchaChallengeRequired",
})


def scrape_session_dead(exc: BaseException) -> bool:
    """True when this exception (or any base) means the loaded dump is rejected.

    Match the MRO so ChallengeRedirection / RecaptchaChallengeForm / etc. rotate
    instead of fail-opening to []. Never used as a password-login trigger for
    Challenge* — only LoginRequired restores.
    """
    return bool({c.__name__ for c in type(exc).__mro__} & _SESSION_DEAD_NAMES)


def scrape_session_needs_restore(exc: BaseException) -> bool:
    """Password restore is only for a rejected session dump, not throttle/network."""
    return bool({c.__name__ for c in type(exc).__mro__} & {"LoginRequired", "ClientLoginRequired"})


def _probe_scrape_session(client) -> None:
    """Work-shaped probe for scrape-login. search_hashtags is the lock-producer call.

    Fall back to account_info only when the client has no search surface (unit fakes).
    Never call login() here.
    """
    search = getattr(client, "search_hashtags", None)
    if callable(search):
        search("music")
        return
    info = getattr(client, "account_info", None)
    if callable(info):
        info()


def _clear_auth_keep_device(client) -> None:
    """Drop session auth/cookies so login() does not see a user_id and relogin=True.

    Keep uuids, device_settings, user_agent, mid — set_settings({}) resets those to
    library defaults and is the more suspicious fingerprint.
    """
    if hasattr(client, "authorization_data"):
        client.authorization_data = {}
    for sess_name in ("private", "public"):
        sess = getattr(client, sess_name, None)
        cookies = getattr(sess, "cookies", None)
        if cookies is not None and hasattr(cookies, "clear"):
            cookies.clear()
        headers = getattr(sess, "headers", None)
        if isinstance(headers, dict):
            headers.pop("Authorization", None)
    if hasattr(client, "last_login"):
        client.last_login = None


def _restore_uuids_and_login(client, user: str, password: str) -> None:
    """Password login that keeps the device envelope. Never login(..., relogin=True)."""
    _clear_auth_keep_device(client)
    client.login(user, password)


def _browser_sessionid_for(ds_user_id: str) -> str | None:
    """Read a live Instagram sessionid from local Chrome for this ds_user_id. Never logs it.

    Optional: browser_cookie3 may be absent. Fail closed to None.
    """
    if not ds_user_id:
        return None
    try:
        import browser_cookie3
    except ImportError:
        return None
    cookie_err = getattr(browser_cookie3, "BrowserCookieError", OSError)
    from pathlib import Path
    roots = [Path.home() / "Library/Application Support/Google/Chrome"]
    files: list = []
    for root in roots:
        if not root.is_dir():
            continue
        files.extend(root.glob("*/Cookies"))
        files.extend(root.glob("*/Network/Cookies"))
    seen: set[str] = set()
    for cookie_file in files:
        key = str(cookie_file)
        if key in seen:
            continue
        seen.add(key)
        try:
            jar = browser_cookie3.chrome(domain_name="instagram.com", cookie_file=key)
        except (OSError, ValueError, cookie_err):
            continue
        got: dict[str, str] = {}
        for c in jar:
            if "instagram" in (c.domain or "") and c.name in {"sessionid", "ds_user_id", "ds_user"}:
                if c.value:
                    got[c.name] = c.value
        ds = got.get("ds_user_id") or got.get("ds_user")
        sid = got.get("sessionid")
        if ds == ds_user_id and sid:
            return sid
    return None


def _inject_sessionid(client, sessionid: str, ds_user_id: str) -> None:
    """Put a browser sessionid onto the loaded client. Keep device/uuids."""
    auth = dict(getattr(client, "authorization_data", None) or {})
    auth["sessionid"] = sessionid
    if ds_user_id:
        auth["ds_user_id"] = ds_user_id
    client.authorization_data = auth
    private = getattr(client, "private", None)
    cookies = getattr(private, "cookies", None)
    if cookies is not None and hasattr(cookies, "set"):
        cookies.set("sessionid", sessionid)
        if ds_user_id:
            cookies.set("ds_user_id", ds_user_id)
    inject = getattr(client, "inject_sessionid_to_public", None)
    if callable(inject):
        inject()


def open_client(cfg: Config, *, client_factory=None, allow_reauth: bool = False, user: str | None = None,
                now: datetime | None = None):
    """Open an authenticated instagrapi Client, PACED. Lazy-imports; dumps session after success.
    Never echoes password or session contents. Raises ScrapeUnavailable on miss.

    `allow_reauth` defaults False: unattended open loads a session file and returns — no
    `account_info()` probe and never `login()`. The first real hashtag call validates the session.
    Unattended callers (Layer A tick, doctor) MUST leave this False — instagrapi>=2.18.12 escalates
    LoginRequired inside `login()` into a full password re-auth, which earned the 2026-07-29T22:01Z
    native checkpoint when the tick still called `login()` every pass.
    Only `fanops hashtags scrape-login` passes `allow_reauth=True` (search probe decides login).

    Multi-account (MOL-857/858): when `user` is omitted, pick via `_pick_healthy_scrape_user`
    (LRU among healthy peers; env order is tiebreak only). Unattended needs a session file;
    `allow_reauth=True` may use password-usable peers and ignores freeze. scrape-login passes
    an explicit `user` per account.

    `delay_range` is set before any network call, so every request this client ever makes — the
    operator probe / login included — carries the jitter (MOL-698); the whole Layer A pass runs on
    this one client."""
    users = scrape_users(cfg)
    if not users:
        raise ScrapeUnavailable("FANOPS_IG_SCRAPE_USER unset")
    if user is None:
        # Lazy import: fanops_hashtags imports open_client inside functions — no cycle at import time.
        from fanops.fanops_hashtags import _pick_healthy_scrape_user
        now = now or datetime.now(timezone.utc)
        chosen = _pick_healthy_scrape_user(cfg, now, allow_reauth=allow_reauth,
                                          require_budget_room=False)
        if chosen is None:
            if scrape_configured(cfg) and not allow_reauth:
                # Distinguish "all frozen" from "no session" when any session exists on disk.
                if any(scrape_session_path(cfg, u).exists() for u in users):
                    raise ScrapeUnavailable("all scrape accounts frozen")
                raise ScrapeUnavailable("no scrape session — run fanops hashtags scrape-login")
            raise ScrapeUnavailable("no scrape session or password for any FANOPS_IG_SCRAPE_USER")
        user = chosen
    elif user not in users:
        raise ScrapeUnavailable(f"scrape user {user!r} not in FANOPS_IG_SCRAPE_USER")
    try:
        if client_factory is None:
            from instagrapi import Client  # lazy: [igscrape] extra
            client_factory = Client
    except ImportError as e:
        raise ScrapeUnavailable("instagrapi not installed — pip install -e '.[igscrape]'") from e
    client = client_factory()
    client.delay_range = _scrape_delay_range()
    sess = scrape_session_path(cfg, user)
    # Always dump to the per-user named path (migrate legacy perca.late off ig_scrape_session.json).
    dump_sess = cfg.control / f"ig_scrape_session_{user}.json"
    if sess.exists():
        client.load_settings(str(sess))
        if allow_reauth:                                # operator scrape-login only: the probe DECIDES login()
            try:
                _probe_scrape_session(client)
            except Exception as e:                      # noqa: BLE001 — probe surface is opaque
                get_logger(cfg)("hashtags", user, "scrape_reauth", err=type(e).__name__)
                if not scrape_session_needs_restore(e):
                    raise
                ds = str((getattr(client, "authorization_data", None) or {}).get("ds_user_id")
                         or getattr(client, "user_id", "") or "")
                browser_sid = _browser_sessionid_for(ds)
                if browser_sid:
                    _inject_sessionid(client, browser_sid, ds)
                    try:
                        _probe_scrape_session(client)
                    except Exception as e2:             # noqa: BLE001 — browser dump may also be stale
                        get_logger(cfg)("hashtags", user, "scrape_reauth",
                                        err=type(e2).__name__, via="browser")
                        if not scrape_session_needs_restore(e2):
                            raise
                        _restore_uuids_and_login(client, user, scrape_password_for(user) or "")
                else:
                    # Failed login raises before dump_settings — the on-disk dump stays untouched.
                    _restore_uuids_and_login(client, user, scrape_password_for(user) or "")
        # Unattended: load_settings → dump → return (session validated by the first work call).
    else:
        if not allow_reauth:
            raise ScrapeUnavailable("no scrape session — run fanops hashtags scrape-login")
        pw = scrape_password_for(user) or ""
        client.login(user, pw)                          # cold-start: operator scrape-login only
    dump_sess.parent.mkdir(parents=True, exist_ok=True)
    client.dump_settings(str(dump_sess))
    setattr(client, "_fanops_scrape_user", user)            # Layer A persist/clear target (MOL-858); fakes OK
    return client


# `session_client` — a Client cloned from the dumped session, no login — lived here until MOL-698.
# It existed ONLY to fan Layer A out to _SCRAPE_PARALLEL workers, which meant N clients presenting the
# SAME device fingerprint in simultaneous private-API calls: the emission profile that earned the
# 2026-07-29 account lock. Layer A is single-client now, so the helper has no caller. Do not restore
# it to add concurrency; raise FANOPS_HASHTAG_SCRAPE_DELAY-paced throughput instead.


def _median(vals: list[float]) -> Optional[float]:
    """Median of a non-empty float list; None when empty. Platform numbers only — no invented blend."""
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _is_recent_reel(m, cutoff: datetime) -> bool:
    """True when this Top row is a Reel Instagram says was posted after `cutoff`. Missing / unparseable
    `taken_at` reads NOT recent: an undated row must not claim to be current evidence."""
    if getattr(m, "product_type", None) != _REEL_PRODUCT_TYPE:
        return False
    at = getattr(m, "taken_at", None)
    if not isinstance(at, datetime):
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at >= cutoff


def resolve_hashtag_scrape(client, tag: str) -> tuple[Optional[str], Optional[float]]:
    """Resolve '#tag' via instagrapi hashtag_info -> (id, media_count).
    `media_count` is Instagram's own tag volume when the private API serves it (None if absent)."""
    name = _norm(tag).lstrip("#")
    if not name:
        return None, None
    info = client.hashtag_info(name)
    hid = getattr(info, "id", None)
    if hid is None:
        return None, None
    s = str(hid).strip()
    if not s:
        return None, None
    return s, _num(getattr(info, "media_count", None))


def measure_and_harvest_scrape(client, tag: str, *, now=None) -> tuple[Optional[dict], dict[str, int]]:
    """ONE hashtag_medias_top fetch → platform metrics + caption co-tag harvest.

    Metrics (only fields Instagram put on the medias — never a blended 'reach'):
      like_count  = median of like_count across top medias that carry one
      play_count  = median of play_count across top medias that carry one (Reels/views when present)
      current_top_reel_play_max_7d = MAX play_count among rows Instagram marks `product_type=clips`
                                     and dates inside the last 7 days (MOL-691)
      top_reel_sample_n            = how many such rows carried a usable play_count — the honest
                                     denominator of that maximum, so a 1-Reel max is not read as a trend

    A tag with neither likes nor plays anywhere in the top grid is UNMEASURED (None, cotags)."""
    name = _norm(tag).lstrip("#")
    if not name:
        return None, {}
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=_REEL_TREND_DAYS)
    medias = client.hashtag_medias_top(name, amount=TOP_SAMPLE_N)
    if not medias:
        return None, {}
    likes: list[float] = []; plays: list[float] = []
    reel_plays: list[float] = []
    cotags: dict[str, int] = {}
    for m in medias:
        lv = _num(getattr(m, "like_count", None))
        if lv is not None:
            likes.append(lv)
        pv = _num(getattr(m, "play_count", None))
        if pv is not None:
            plays.append(pv)
            if pv > 0 and _is_recent_reel(m, cutoff):
                reel_plays.append(pv)
        caption = getattr(m, "caption_text", None) or ""
        for raw in CAPTION_TAG_RE.findall(caption):
            t = _norm(raw)
            if not t:
                continue
            if t not in cotags and len(cotags) >= HARVEST_CAP:
                continue
            cotags[t] = cotags.get(t, 0) + 1
    like_m = _median(likes); play_m = _median(plays)
    if like_m is None and play_m is None:
        return None, cotags
    metrics: dict = {}
    if like_m is not None:
        metrics["like_count"] = like_m
    if play_m is not None:
        metrics["play_count"] = play_m
    if reel_plays:
        metrics["current_top_reel_play_max_7d"] = float(max(reel_plays))
        metrics["top_reel_sample_n"] = float(len(reel_plays))
    return metrics, cotags


def search_hashtags_scrape(client, name) -> list[dict]:
    """One instagrapi search_hashtags page. Incomplete hits stay; never invent play_count.

    Fail-open: client error → []. Nameless hits are skipped. Cap is the search page.
    """
    query = _norm(name).lstrip("#") if isinstance(name, str) else ""
    if not query:
        return []
    try:
        hits = client.search_hashtags(query)
    except Exception as exc:
        if scrape_session_dead(exc):
            raise
        _log.warning("search_hashtags_scrape fail-open: %s: %s", type(exc).__name__, str(exc)[:200])
        return []
    out: list[dict] = []
    for h in hits or []:
        raw = getattr(h, "name", None)
        if not isinstance(raw, str) or not raw.strip():
            continue
        row: dict = {"name": raw}
        hid = getattr(h, "id", None)
        if hid is not None and hid != "":
            row["id"] = hid
        mc = getattr(h, "media_count", None)
        if mc is not None:
            row["media_count"] = mc
        out.append(row)
    return out
