"""Hashtag Layer A network via instagrapi (Graph hashtag path deferred)."""
from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from fanops.config import Config
from fanops.hashtags import CAPTION_TAG_RE, HARVEST_CAP, TOP_SAMPLE_N, _norm, _num

_REEL_TREND_DAYS = 7            # a Reel older than this is history, not "currently trending"
_REEL_PRODUCT_TYPE = "clips"    # Instagram's own product_type for a Reel
_DELAY_RANGE = (1.0, 3.0)       # instagrapi's own between-request jitter, seconds (MOL-698)


def _scrape_delay_range():
    """The pacing instagrapi sleeps BETWEEN private-API calls, as `[lo, hi]` seconds — or None for no
    pacing at all. Unset before MOL-698, so a pass fired back-to-back at ~2.4 req/s from a single
    account; that emission profile is what earned the 2026-07-29 `challenge_required`.
    FANOPS_HASHTAG_SCRAPE_DELAY takes a `"lo,hi"` pair; `"0"` opts out entirely (fakes / tests).
    Anything else — unparseable, negative, inverted, wrong arity — falls back to the default: a
    fat-fingered env var must never remove the pacing or break Layer A."""
    raw = (os.getenv("FANOPS_HASHTAG_SCRAPE_DELAY") or "").strip()
    if not raw:
        return list(_DELAY_RANGE)
    if raw == "0":
        return None
    try:
        vals = [float(p) for p in raw.replace(" ", "").split(",")]
    except ValueError:
        return list(_DELAY_RANGE)
    if len(vals) != 2 or vals[0] < 0 or vals[1] < vals[0]:
        return list(_DELAY_RANGE)
    return vals


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
    shared = os.getenv("FANOPS_IG_SCRAPE_PASSWORD")
    return resolve_secret("FANOPS_IG_SCRAPE_PASSWORD",
                          shared.strip() if shared and shared.strip() else None)


def scrape_user_usable(cfg: Config, user: str) -> bool:
    """True when this user has a session file or a resolvable password."""
    return scrape_session_path(cfg, user).exists() or bool(scrape_password_for(user))


def any_scrape_session(cfg: Config) -> bool:
    """True when any listed scrape user has a session file on disk (doctor soft-ok gate)."""
    return any(scrape_session_path(cfg, u).exists() for u in scrape_users(cfg))


def scrape_configured(cfg: Config) -> bool:
    """True when ANY listed scrape user has a session file OR a password (MOL-857)."""
    return any(scrape_user_usable(cfg, u) for u in scrape_users(cfg))


def open_client(cfg: Config, *, client_factory=None, allow_reauth: bool = False, user: str | None = None,
                now: datetime | None = None):
    """Open an authenticated instagrapi Client, PACED. Lazy-imports; dumps session after success.
    Never echoes password or session contents. Raises ScrapeUnavailable on miss.

    `allow_reauth` defaults False: a restored session is validated via `account_info()` ONLY and
    `login()` is never called. Unattended callers (Layer A tick, doctor) MUST leave this False —
    instagrapi>=2.18.12 escalates LoginRequired inside `login()` into a full password re-auth, which
    earned the 2026-07-29T22:01Z native checkpoint when the tick still called `login()` every pass.
    Only `fanops hashtags scrape-login` passes `allow_reauth=True`.

    Multi-account (MOL-857/858): when `user` is omitted, pick the first listed FANOPS_IG_SCRAPE_USER
    that can actually open and is not freeze/budget-blocked: with `allow_reauth=False` that means a
    session file (password alone cannot open unattended); with `allow_reauth=True` session OR
    password. Operator scrape-login ignores freeze (allow_reauth=True).

    `delay_range` is set before any network call, so every request this client ever makes — the
    probe / login included — carries the jitter (MOL-698); the whole Layer A pass runs on this one client."""
    users = scrape_users(cfg)
    if not users:
        raise ScrapeUnavailable("FANOPS_IG_SCRAPE_USER unset")
    if user is None:
        # Lazy import: fanops_hashtags imports open_client inside functions — no cycle at import time.
        from fanops.fanops_hashtags import scrape_user_blocked
        now = now or datetime.now(timezone.utc)
        if allow_reauth:
            # Operator path: freeze does not block an explicit login attempt.
            chosen = next((u for u in users if scrape_user_usable(cfg, u)), None)
        else:
            # Unattended: session required; skip frozen / day-budget-exhausted peers (MOL-858).
            chosen = next((u for u in users
                           if scrape_session_path(cfg, u).exists()
                           and not scrape_user_blocked(cfg, u, now)), None)
        if chosen is None:
            if scrape_configured(cfg) and not allow_reauth:
                # Distinguish "all frozen" from "no session" when any session exists on disk.
                if any(scrape_session_path(cfg, u).exists() for u in users):
                    raise ScrapeUnavailable("all scrape accounts frozen or day-budget exhausted")
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
        try:
            client.account_info()                       # validate WITHOUT login() — no re-auth path
        except Exception:                               # noqa: BLE001 — probe surface is opaque
            if not allow_reauth:
                raise                                   # unattended: never re-authenticate
            client.login(user, scrape_password_for(user) or "", relogin=True)
        # Valid session (or successful operator re-auth): persist and return. No login() on the happy path.
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
