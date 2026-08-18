"""Instagram *web* scrape via the FanOps-owned Chrome profile.

Lock search + measure must use the same session the operator logged into
(scrape_chrome/<user>/). instagrapi's app API (i.instagram.com private)
rejects that web sessionid (403 LoginRequired). This module is the
matching consumer: www.instagram.com with the profile's cookies.

Never reads DevTools / system Chrome. Never dump_settings.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from fanops.config import Config
from fanops.hashtags import _norm
from fanops.ig_hashtag_scrape import ScrapeUnavailable, profile_instagram_cookies

_WEB_APP_ID = "936619743392459"
_WEB_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class LoginRequired(Exception):
    """Named so scrape_session_dead matches the MRO. Web 401/403."""


class IgWebSession:
    """Duck-types the two instagrapi calls the lock path uses."""

    def __init__(self, user: str, cookies: dict[str, str], *, get=None, post=None):
        if not user or not cookies.get("sessionid"):
            raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")
        self._fanops_scrape_user = user
        self._cookies = dict(cookies)
        self._get = get
        self._post = post

    def search_hashtags(self, query: str):
        q = (query or "").strip().lstrip("#")
        if not q:
            return []
        url = (
            "https://www.instagram.com/api/v1/web/search/topsearch/"
            f"?context=hashtag&query={quote(q)}"
        )
        data = self._json("GET", url)
        out = []
        for row in data.get("hashtags") or []:
            tag = row.get("hashtag") if isinstance(row, dict) else None
            if not isinstance(tag, dict):
                continue
            name = tag.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            out.append(_Hit(name=name, hid=tag.get("id"), media_count=tag.get("media_count")))
        return out

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
        medias = _collect_medias(data)
        return medias[: max(int(amount), 0)]

    def _json(self, method: str, url: str, *, body: str | None = None) -> dict:
        import requests
        headers = {
            "User-Agent": _WEB_UA,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "X-IG-App-ID": _WEB_APP_ID,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.instagram.com/",
            "Origin": "https://www.instagram.com",
        }
        token = self._cookies.get("csrftoken")
        if token:
            headers["X-CSRFToken"] = token
        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            if method == "GET":
                fn = self._get or requests.get
                resp = fn(url, headers=headers, cookies=self._cookies, timeout=30)
            else:
                fn = self._post or requests.post
                resp = fn(url, headers=headers, cookies=self._cookies, data=body or "", timeout=30)
        except Exception as exc:
            raise LoginRequired(str(exc)[:160]) from exc
        status = getattr(resp, "status_code", None)
        if status in (401, 403):
            raise LoginRequired(f"web {status}")
        if status is not None and int(status) >= 400:
            raise RuntimeError(f"instagram web {status}")
        try:
            payload = resp.json()
        except Exception as exc:
            raise RuntimeError("instagram web non-json") from exc
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
        if play is None and isinstance(raw.get("video_versions"), list):
            play = raw.get("play_count")
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


def open_web_session(cfg: Config, user: str | None = None, *, get=None, post=None) -> IgWebSession:
    """Open a web scrape session for one FanOps profile. Unattended: read only."""
    from fanops.ig_hashtag_scrape import scrape_users
    users = scrape_users(cfg)
    if not users:
        raise ScrapeUnavailable("FANOPS_IG_SCRAPE_USER unset")
    if user is None:
        from fanops.fanops_hashtags import _healthy_scrape_users
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for cand in _lock_web_users(cfg, now) or _healthy_scrape_users(cfg, now, require_budget_room=False):
            try:
                return open_web_session(cfg, cand, get=get, post=post)
            except ScrapeUnavailable:
                continue
        raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")
    if user not in users:
        raise ScrapeUnavailable(f"scrape user {user!r} not in FANOPS_IG_SCRAPE_USER")
    cookies = profile_instagram_cookies(cfg, user)
    if not cookies.get("sessionid"):
        raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")
    return IgWebSession(user, cookies, get=get, post=post)


def _lock_web_users(cfg: Config, now) -> list[str]:
    from fanops.fanops_hashtags import _account_rec, _is_frozen, _load_cooldown_blob
    from fanops.ig_hashtag_scrape import scrape_users
    blob = _load_cooldown_blob(cfg)
    out: list[str] = []
    for user in scrape_users(cfg):
        if _is_frozen(_account_rec(blob, user), now):
            continue
        if profile_instagram_cookies(cfg, user).get("sessionid"):
            out.append(user)
    return out
