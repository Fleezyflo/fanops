"""Safari browser shell: XHR plumbing, pacing, login probe, tick-slot gate.

AppleScript window/tab management lives in ``ig_hashtag_scrape``; this module is
the in-tab XHR layer and per-tick Safari opener coordination.
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone

from fanops.config import Config
from fanops.ig_hashtag_scrape import ScrapeUnavailable, scrape_session_dead

_WEB_APP_ID = "936619743392459"

_LAST_REQUEST_MONO: dict[str, float] = {}

_SAFARI_TICK_SLOT: str | None = None   # HT5: one Safari opener per daemon tick (lock OR remesure)


class LoginRequired(Exception):
    """Named so scrape_session_dead matches the MRO. Page fetch hit login/401/403."""


class WebThrottled(Exception):
    """HTTP 429. instagrapi: stop the burst, freeze, do not retry in a tight loop."""


class PleaseWaitFewMinutes(WebThrottled):
    """Named for scrape_session_dead. Instagram 200-body wait — same freeze as 429."""


class FeedbackRequired(WebThrottled):
    """Named for scrape_session_dead. Instagram 200-body action block."""


class RateLimitError(WebThrottled):
    """Named for scrape_session_dead. Instagram error_type rate_limit_error on HTTP 200."""


class SentryBlock(WebThrottled):
    """Named for scrape_session_dead. Instagram sentry_block — IP/session rejected."""


class ChallengeRequired(Exception):
    """Named for scrape_session_dead. Checkpoint in JSON. Not LoginRequired — no password restore."""


def reset_safari_tick_slot() -> None:
    """Clear the per-tick Safari slot. ``_cmd_run_pass`` calls this at tick start."""
    global _SAFARI_TICK_SLOT
    _SAFARI_TICK_SLOT = None


def mark_safari_tick_slot(consumer: str) -> None:
    """Record which path opened Safari this tick (``lock`` or ``remesure``)."""
    global _SAFARI_TICK_SLOT
    _SAFARI_TICK_SLOT = consumer


def safari_tick_slot_claimed() -> str | None:
    """Return the tick's Safari consumer, or None if the slot is still free."""
    return _SAFARI_TICK_SLOT


def pace_since_last(cfg: Config | None, user: str | None) -> None:
    """instagrapi delay_range: wait until [lo,hi] seconds have passed since THIS user's last XHR.

    First request has no wait. Shared clock (cooldown last_request_at + process monotonic)
    so opening a new IgWebSession cannot start a burst."""
    src = cfg if cfg is not None else Config()
    delay = src.hashtag_scrape_delay
    if not delay:
        return
    key = user or ""
    elapsed = None
    last_m = _LAST_REQUEST_MONO.get(key)
    if last_m is not None:
        elapsed = time.monotonic() - last_m
    elif cfg is not None and user:
        from fanops.fanops_hashtags import _account_rec, _load_cooldown_blob
        from fanops.timeutil import parse_iso
        raw = _account_rec(_load_cooldown_blob(cfg), user).get("last_request_at")
        if isinstance(raw, str) and raw:
            try:
                last = parse_iso(raw)
            except (ValueError, TypeError):
                last = None
            if last is not None:
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - last).total_seconds()
    if elapsed is None:
        return
    lo, hi = float(delay[0]), float(delay[-1])
    need = lo if hi <= lo else random.uniform(lo, hi)
    wait = need - elapsed
    if wait > 0:
        time.sleep(wait)


def pace_live_safari() -> None:
    """Back-compat name. Unattended live XHRs use pace_since_last per user."""
    pace_since_last(None, "")


def safari_logged_in(user: str) -> bool:
    """True when THIS account's Safari Instagram tab is not a login wall.

    sessionid is HttpOnly — document.cookie cannot see it. Do not call
    /api/v1/tags/... here; that private-API probe ran once per unfinished
    source and is what Instagram treated as a session kill. A dead session
    still on instagram.com/ fails on the first real tag XHR."""
    from fanops.ig_hashtag_scrape import safari_eval
    try:
        raw = safari_eval(
            "(function(){"
            "var u=location.href||'';"
            "if(u.indexOf('accounts/login')>=0)return 'login';"
            "if(document.querySelector('input[name=\"username\"], input[name=\"password\"]'))"
            "return 'login';"
            "return 'ok';"
            "})()",
            user,
        )
    except (ScrapeUnavailable, RuntimeError, OSError, TimeoutError):
        return False
    return (raw or "").strip() == "ok"


def safari_profile_auth(cfg: Config, user: str) -> tuple[str, str] | None:
    """Auth tuple for wait_for_scrape_profile_auth. Values are not cookies."""
    del cfg
    if not safari_logged_in(user):
        return None
    return ("safari", user or "")


def _body_stop(payload: dict) -> BaseException | None:
    """instagrapi stop classes from Instagram JSON. HTTP 200 still carries these.

    Generic `status: fail` (missing tag) is not a freeze — only named anti-abuse signals.
    """
    if not isinstance(payload, dict):
        return None
    msg = str(payload.get("message") or "")
    err = str(payload.get("error_type") or payload.get("error_title") or "")
    challenge = payload.get("challenge") if isinstance(payload.get("challenge"), dict) else {}
    blob = " ".join((
        msg, err,
        str(payload.get("feedback_message") or ""),
        str(payload.get("feedback_title") or ""),
        str(payload.get("checkpoint_url") or ""),
        str(challenge.get("url") or ""),
    )).lower()
    if (payload.get("require_login") is True or payload.get("logout_reason") is not None
            or "login_required" in blob or "logged out" in blob):
        return LoginRequired("web login_required")
    if (payload.get("two_factor_info") or "checkpoint" in blob or "challenge_required" in blob
            or payload.get("checkpoint_url") or challenge.get("url")):
        return ChallengeRequired("web checkpoint")
    if payload.get("feedback_required") or payload.get("spam") is True or "feedback_required" in blob:
        return FeedbackRequired("web feedback_required")
    if "please wait" in blob or "few minutes" in blob or "please_wait" in blob:
        return PleaseWaitFewMinutes("web please_wait")
    if err.lower() == "rate_limit_error" or "rate_limit" in blob:
        return RateLimitError("web rate_limit")
    if err.lower() == "sentry_block" or "sentry_block" in blob:
        return SentryBlock("web sentry_block")
    return None


def _text_stop(text: str) -> BaseException:
    """Tag XHR that is not JSON is a wall (login HTML / empty / garbage), not a missing tag."""
    blob = (text or "").lower()
    if ("accounts/login" in blob or "login_required" in blob or "logged out" in blob
            or 'name="username"' in blob or 'name="password"' in blob
            or "<html" in blob or "<!doctype" in blob):
        return LoginRequired("web html login")
    if "checkpoint" in blob or "challenge_required" in blob:
        return ChallengeRequired("web html checkpoint")
    if "feedback_required" in blob:
        return FeedbackRequired("web html feedback")
    if "please wait" in blob or "few minutes" in blob or "rate_limit" in blob:
        return PleaseWaitFewMinutes("web text wait")
    return WebThrottled("web non-json")


def safari_xhr(method: str, url: str, body: str | None = None, user: str | None = None) -> str:
    """AppleScript XHR only. No delay, no charge. Callers go through safari_fetch."""
    from fanops.ig_hashtag_scrape import safari_eval
    headers = [
        f"xhr.setRequestHeader('X-IG-App-ID', {_WEB_APP_ID!r});",
        "xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');",
        "var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);",
        "if (m) xhr.setRequestHeader('X-CSRFToken', m[1]);",
        "var c = sessionStorage.getItem('www-claim-v2');",
        "xhr.setRequestHeader('X-IG-WWW-Claim', c || '0');",
    ]
    if method == "POST":
        headers.append("xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');")
    send = json.dumps(body) if body is not None else "null"
    expr = (
        "(function(){"
        "var xhr=new XMLHttpRequest();"
        f"xhr.open({json.dumps(method)}, {json.dumps(url)}, false);"
        + "".join(headers)
        + f"xhr.send({send});"
        "return JSON.stringify({status:xhr.status,url:xhr.responseURL||'',text:xhr.responseText||''});"
        "})()"
    )
    try:
        return safari_eval(expr, user)
    except (OSError, TimeoutError, RuntimeError) as exc:
        raise ScrapeUnavailable("safari scrape not ready — run fanops hashtags scrape-login") from exc


def safari_fetch(method: str, url: str, body: str | None = None, user: str | None = None,
                 cfg: Config | None = None) -> dict:
    """THE Instagram web request. Delay, count, freeze, then XHR. No bypass."""
    from fanops.fanops_hashtags import (
        _charge_scrape_user, _day_room, scrape_user_blocked,
    )
    now = datetime.now(timezone.utc)
    if cfg is not None and user:
        # scrape_user_blocked includes day-budget exhaustion (HT3); _day_room is belt-and-suspenders.
        if scrape_user_blocked(cfg, user, now):
            raise ScrapeUnavailable("scrape account frozen or day budget exhausted")
        if _day_room(cfg, user, now) <= 0:
            raise ScrapeUnavailable("scrape day budget exhausted")
    pace_since_last(cfg, user)
    raw = safari_xhr(method, url, body, user=user)
    _LAST_REQUEST_MONO[user or ""] = time.monotonic()
    stop_exc: BaseException | None = None
    status = None
    loc = ""
    try:
        wrapped = json.loads(raw)
    except json.JSONDecodeError as exc:
        stop_exc = WebThrottled("web wrapper")
        if cfg is not None and user:
            _charge_scrape_user(cfg, user, 1, now=now, stop_exc=stop_exc)
        raise stop_exc from exc
    if not isinstance(wrapped, dict):
        stop_exc = WebThrottled("web wrapper")
        if cfg is not None and user:
            _charge_scrape_user(cfg, user, 1, now=now, stop_exc=stop_exc)
        raise stop_exc
    status = wrapped.get("status")
    loc = str(wrapped.get("url") or "")
    text = str(wrapped.get("text") or "")
    payload: dict | None = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            payload = parsed
    except json.JSONDecodeError:
        payload = None
    if "accounts/login" in loc or status in (401, 403):
        stop_exc = LoginRequired(f"web {status or 'login'}")
    elif status == 429:
        stop_exc = WebThrottled("web 429")
    elif payload is not None:
        stop_exc = _body_stop(payload)
        if stop_exc is None and status is not None:
            try:
                if int(status) >= 400:
                    stop_exc = WebThrottled(f"web {status}")
            except (TypeError, ValueError):
                pass
    else:
        stop_exc = _text_stop(text)
    if cfg is not None and user:
        freeze = stop_exc if stop_exc is not None and scrape_session_dead(stop_exc) else None
        _charge_scrape_user(cfg, user, 1, now=now, stop_exc=freeze)
    if stop_exc is not None:
        raise stop_exc
    if payload is None:
        raise WebThrottled("web non-json")
    return payload
