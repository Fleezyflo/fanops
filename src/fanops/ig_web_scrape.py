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

import json
import random
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from fanops.config import Config
from fanops.hashtags import _norm
from fanops.ig_hashtag_scrape import ScrapeUnavailable

_WEB_APP_ID = "936619743392459"


class LoginRequired(Exception):
    """Named so scrape_session_dead matches the MRO. Page fetch hit login/401/403."""


class IgWebSession:
    """Duck-types instagrapi search_hashtags / hashtag_info / hashtag_medias_top."""

    def __init__(self, user: str, *, fetch=None, safari: bool = False):
        if not user or (fetch is None and not safari):
            raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")
        self._fanops_scrape_user = user
        self._fetch = fetch
        self._safari = safari

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
            _pace_live_safari()
            payload = _safari_fetch(method, url, body, user=self._fanops_scrape_user)
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
    """Open a web scrape session. Tick may open Safari. Never launches Chrome."""
    from fanops.ig_hashtag_scrape import ensure_scrape_safari, scrape_users
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
        return IgWebSession(user, fetch=fetch)
    if not ensure_scrape_safari(cfg, user):
        raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")
    if not safari_logged_in(user):
        raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")
    return IgWebSession(user, safari=True)


def _pace_live_safari() -> None:
    """Sleep FANOPS_HASHTAG_SCRAPE_DELAY between live Safari XHRs. Injected _fetch does not call this."""
    delay = Config().hashtag_scrape_delay
    if not delay:
        return
    lo, hi = float(delay[0]), float(delay[-1])
    time.sleep(lo if hi <= lo else random.uniform(lo, hi))


def _lock_web_users(cfg: Config, now) -> list[str]:
    """LRU scrape users with day-budget room. Safari lock: no envelope json required."""
    from fanops.fanops_hashtags import _healthy_scrape_users
    return _healthy_scrape_users(cfg, now, require_budget_room=True, require_session=False)


def safari_logged_in(user: str) -> bool:
    """True when THIS account's Safari Instagram tab can resolve a real tag."""
    try:
        data = _safari_fetch(
            "GET", "https://www.instagram.com/api/v1/tags/music/info/", user=user,
        )
    except (LoginRequired, ScrapeUnavailable, RuntimeError, OSError, TimeoutError):
        return False
    return isinstance(data, dict) and bool(data.get("id") or data.get("name"))


def safari_profile_auth(cfg: Config, user: str) -> tuple[str, str] | None:
    """Auth tuple for wait_for_scrape_profile_auth. Values are not cookies."""
    del cfg
    if not safari_logged_in(user):
        return None
    return ("safari", user or "")


def _safari_fetch(method: str, url: str, body: str | None = None, user: str | None = None) -> dict:
    from fanops.ig_hashtag_scrape import safari_eval
    headers = [
        f"xhr.setRequestHeader('X-IG-App-ID', {_WEB_APP_ID!r});",
        "xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');",
        "var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);",
        "if (m) xhr.setRequestHeader('X-CSRFToken', m[1]);",
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
        raw = safari_eval(expr, user)
    except (OSError, TimeoutError, RuntimeError) as exc:
        raise ScrapeUnavailable("safari scrape not ready — run fanops hashtags scrape-login") from exc
    try:
        wrapped = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("instagram safari non-json wrapper") from exc
    if not isinstance(wrapped, dict):
        raise RuntimeError("instagram safari bad wrapper")
    status = wrapped.get("status")
    loc = str(wrapped.get("url") or "")
    if "accounts/login" in loc or status in (401, 403):
        raise LoginRequired(f"web {status or 'login'}")
    if status is not None and int(status) >= 400:
        raise RuntimeError(f"instagram web {status}")
    try:
        payload = json.loads(wrapped.get("text") or "")
    except json.JSONDecodeError as exc:
        raise RuntimeError("instagram web non-json") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("instagram web bad payload")
    return payload
