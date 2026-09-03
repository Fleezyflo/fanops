"""Instagram *web* scrape via Safari.

A web login is not an instagrapi private-API session. Exporting cookies into
Python redirects to /accounts/login — Instagram accepts the session only from
the browser that holds it.

Google Chrome is the operator's daily / DevTools browser. Lock scrape must
never launch it (a custom --user-data-dir instance hijacks the Dock icon).
Search + measure run as a synchronous XHR inside Safari's Instagram tab.

Never dump_settings. Never 9222/9223. Never system Chrome.
"""
from __future__ import annotations

import time  # noqa: F401 — tests monkeypatch time.sleep via ig_web_scrape facade
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from fanops.config import Config
from fanops.hashtags import _norm
from fanops.ig_hashtag_scrape import ScrapeUnavailable, scrape_users
from fanops.ig_safari_shell import (
    ChallengeRequired,
    FeedbackRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
    RateLimitError,
    SentryBlock,
    WebThrottled,
    _LAST_REQUEST_MONO,  # noqa: F401 — tests patch/clear via ig_web_scrape
    pace_live_safari,
    safari_fetch,
    safari_logged_in,
    safari_profile_auth,
    safari_xhr,
)

# Back-compat aliases — existing callers and tests use the underscore names.
_safari_xhr = safari_xhr
_safari_fetch = safari_fetch
_pace_live_safari = pace_live_safari


class IgWebSession:
    """Duck-types instagrapi search_hashtags / hashtag_info / hashtag_medias_top."""

    def __init__(self, user: str, *, fetch=None, safari: bool = False, cfg=None):
        if not user or (fetch is None and not safari):
            raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")
        self._fanops_scrape_user = user
        self._fetch = fetch
        self._safari = safari
        self._cfg = cfg

    def _tag_info(self, q: str) -> dict:
        """GET /api/v1/tags/{q}/info/ — lock search and remesure hashtag_info share this."""
        return self._json("GET", f"https://www.instagram.com/api/v1/tags/{quote(q)}/info/")

    def search_hashtags(self, query: str):
        """Exact-name resolve. Typeahead is siblings — lock verify must not accept those."""
        q = (query or "").strip().lstrip("#")
        if not q:
            return []
        data = self._tag_info(q)
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            return []
        hid = data.get("id")
        media_count = data.get("media_count")
        if hid in (None, "") and not (isinstance(media_count, (int, float)) and media_count > 0):
            return []
        return [_Hit(name=name, hid=hid, media_count=media_count)]

    def hashtag_info(self, name: str):
        """Duck-type instagrapi hashtag_info so resolve_hashtag_scrape runs on Safari."""
        q = (name or "").strip().lstrip("#")
        if not q:
            return _Hit(name="", hid=None, media_count=None)
        data = self._tag_info(q)
        raw = data.get("name")
        nm = raw.strip() if isinstance(raw, str) and raw.strip() else q
        return _Hit(name=nm, hid=data.get("id"), media_count=data.get("media_count"))

    def hashtag_medias_top(self, name: str, amount: int = 9):
        tag = _norm(name).lstrip("#")
        if not tag:
            return []
        url = f"https://www.instagram.com/api/v1/tags/{quote(tag)}/sections/"
        data = self._json(
            "POST",
            url,
            body="include_persistent=0&max_id=&page=0&surface=grid&tab=recent",
        )
        return _collect_medias(data)[: max(int(amount), 0)]

    def _json(self, method: str, url: str, *, body: str | None = None) -> dict:
        if self._fetch is not None:
            payload = self._fetch(method, url, body)
        else:
            payload = _safari_fetch(
                method, url, body, user=self._fanops_scrape_user, cfg=self._cfg,
            )
        if not isinstance(payload, dict):
            raise RuntimeError("instagram web bad payload")
        return payload


class _Hit:
    def __init__(self, name: str, hid=None, media_count=None):
        self.name = name
        self.id = hid
        self.media_count = media_count


class _Media:
    def __init__(self, raw: dict):
        self.like_count = raw.get("like_count")
        play = raw.get("play_count")
        if play is None:
            play = raw.get("view_count")
        self.play_count = play
        self.product_type = raw.get("product_type")
        cap = raw.get("caption")
        if isinstance(cap, dict):
            self.caption_text = cap.get("text") or ""
        elif isinstance(cap, str):
            self.caption_text = cap
        else:
            self.caption_text = ""
        taken = raw.get("taken_at")
        if isinstance(taken, (int, float)):
            self.taken_at = datetime.fromtimestamp(taken, tz=timezone.utc)
        else:
            self.taken_at = None


def _collect_medias(blob: Any) -> list[_Media]:
    found: list[_Media] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            media = node.get("media") if isinstance(node.get("media"), dict) else None
            cand = media or node
            if _looks_like_media(cand):
                key = str(cand.get("pk") or cand.get("id") or id(cand))
                if key not in seen:
                    seen.add(key)
                    found.append(_Media(cand))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(blob)
    return found


def _looks_like_media(d: dict) -> bool:
    if not isinstance(d, dict):
        return False
    if d.get("like_count") is None and d.get("play_count") is None and d.get("view_count") is None:
        return False
    return "pk" in d or "id" in d or "code" in d or "taken_at" in d


def open_web_session(cfg: Config, user: str | None = None, *, fetch=None) -> IgWebSession:
    """Open a web scrape session. Restores a missing Safari profile window. Never Chrome."""
    from fanops.ig_hashtag_scrape import ensure_scrape_safari
    users = scrape_users(cfg)
    if not users:
        raise ScrapeUnavailable("FANOPS_IG_SCRAPE_USER unset")
    if user is None:
        now = datetime.now(timezone.utc)
        for cand in _lock_web_users(cfg, now):
            try:
                return open_web_session(cfg, cand, fetch=fetch)
            except ScrapeUnavailable:
                continue
        raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")
    if user not in users:
        raise ScrapeUnavailable(f"scrape user {user!r} not in FANOPS_IG_SCRAPE_USER")
    if fetch is not None:
        return IgWebSession(user, fetch=fetch, cfg=cfg)
    if not ensure_scrape_safari(cfg, user, navigate=False):
        raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")
    if not safari_logged_in(user):
        raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")
    return IgWebSession(user, safari=True, cfg=cfg)


def _lock_web_users(cfg: Config, now) -> list[str]:
    """LRU scrape users with day-budget room. Safari lock: no envelope json required."""
    from fanops.fanops_hashtags import _healthy_scrape_users
    return _healthy_scrape_users(cfg, now, require_budget_room=True, require_session=False)


__all__ = [
    "ChallengeRequired",
    "FeedbackRequired",
    "IgWebSession",
    "LoginRequired",
    "PleaseWaitFewMinutes",
    "RateLimitError",
    "SentryBlock",
    "WebThrottled",
    "_LAST_REQUEST_MONO",
    "open_web_session",
    "safari_logged_in",
    "safari_profile_auth",
]
