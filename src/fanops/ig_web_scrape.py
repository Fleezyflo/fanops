"""Instagram *web* scrape via the FanOps-owned Chrome page.

A Chrome web login is not an instagrapi private-API session (403 / login
redirect). Exporting the cookie into Python (requests / curl_cffi) also
redirects to /accounts/login — Instagram accepts the session only from the
browser that holds it.

Lock search + measure therefore run as `fetch()` inside
`scrape_chrome/<user>/`, on a FanOps-owned localhost port (9331–9399).
Never 9222/9223. Never system Chrome. Never dump_settings.
"""
from __future__ import annotations

import json
import os
import socket
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from fanops.config import Config
from fanops.hashtags import _norm
from fanops.ig_hashtag_scrape import ScrapeUnavailable

_WEB_APP_ID = "936619743392459"


class LoginRequired(Exception):
    """Named so scrape_session_dead matches the MRO. Page fetch hit login/401/403."""


class IgWebSession:
    """Duck-types the two instagrapi calls the lock path uses."""

    def __init__(self, user: str, *, fetch=None, endpoint: str | None = None):
        if not user or (fetch is None and not endpoint):
            raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")
        self._fanops_scrape_user = user
        self._fetch = fetch
        self._endpoint = endpoint

    def search_hashtags(self, query: str):
        """Exact-name resolve. Typeahead is siblings — lock verify must not accept those."""
        q = (query or "").strip().lstrip("#")
        if not q:
            return []
        data = self._json("GET", f"https://www.instagram.com/api/v1/tags/{quote(q)}/info/")
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            return []
        hid = data.get("id")
        media_count = data.get("media_count")
        if hid in (None, "") and not (isinstance(media_count, (int, float)) and media_count > 0):
            return []
        return [_Hit(name=name, hid=hid, media_count=media_count)]

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
            payload = _cdp_fetch(self._endpoint, method, url, body)
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
    """Open a web scrape session for one FanOps profile. Tick may relaunch THAT profile's Chrome."""
    from fanops.ig_hashtag_scrape import (
        cdp_alive, ensure_scrape_chrome, scrape_cdp_endpoint, scrape_users,
    )
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
    if not ensure_scrape_chrome(cfg, user):
        raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")
    if not cdp_alive(cfg, user) or not cdp_profile_auth(cfg, user):
        raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")
    endpoint = scrape_cdp_endpoint(cfg, user)
    if not endpoint:
        raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")
    return IgWebSession(user, endpoint=endpoint)


def _lock_web_users(cfg: Config, now) -> list[str]:
    """Unfrozen scrape users. Opener decides whether that Chrome is actually logged in."""
    from fanops.fanops_hashtags import _account_rec, _is_frozen, _load_cooldown_blob
    from fanops.ig_hashtag_scrape import scrape_users
    blob = _load_cooldown_blob(cfg)
    return [user for user in scrape_users(cfg) if not _is_frozen(_account_rec(blob, user), now)]


def cdp_profile_auth(cfg: Config, user: str) -> tuple[str, str] | None:
    """(sessionid, ds_user_id) from the live FanOps Chrome. None if CDP is down or not logged in."""
    from fanops.ig_hashtag_scrape import scrape_cdp_endpoint
    endpoint = scrape_cdp_endpoint(cfg, user)
    if not endpoint:
        return None
    try:
        cookies = _cdp_cookies(endpoint)
    except (OSError, TimeoutError, RuntimeError):
        return None
    sid = cookies.get("sessionid") or ""
    if not sid:
        return None
    return sid, cookies.get("ds_user_id") or cookies.get("ds_user") or ""


def _http_json(url: str, *, method: str = "GET", timeout: float = 5) -> Any:
    req = Request(url, method=method)
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if not raw:
        return None
    return json.loads(raw.decode())


def _instagram_page_ws(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    pages = _http_json(f"{base}/json/list") or []
    for page in pages if isinstance(pages, list) else []:
        if not isinstance(page, dict) or page.get("type") != "page":
            continue
        url = str(page.get("url") or "")
        ws = page.get("webSocketDebuggerUrl")
        if "instagram.com" in url and isinstance(ws, str) and ws.startswith("ws://127.0.0.1"):
            return ws
    created = _http_json(f"{base}/json/new?https://www.instagram.com/")
    if isinstance(created, dict):
        ws = created.get("webSocketDebuggerUrl")
        if isinstance(ws, str) and ws.startswith("ws://127.0.0.1"):
            return ws
    raise ScrapeUnavailable("scrape chrome has no Instagram tab")


def _cdp_cookies(endpoint: str) -> dict[str, str]:
    ws_url = _instagram_page_ws(endpoint)
    cdp = _Cdp(ws_url)
    try:
        cdp.call("Network.enable")
        raw = cdp.call("Network.getAllCookies")
    finally:
        cdp.close()
    got: dict[str, str] = {}
    for row in (raw or {}).get("cookies") or []:
        if not isinstance(row, dict):
            continue
        domain = str(row.get("domain") or "")
        name = row.get("name")
        value = row.get("value")
        if "instagram" not in domain or not isinstance(name, str) or not isinstance(value, str):
            continue
        if value:
            got[name] = value
    return got


def _cdp_fetch(endpoint: str | None, method: str, url: str, body: str | None) -> dict:
    if not endpoint:
        raise ScrapeUnavailable("no scrape profile session — run fanops hashtags scrape-login")
    try:
        ws_url = _instagram_page_ws(endpoint)
    except (OSError, TimeoutError) as exc:
        raise ScrapeUnavailable("scrape chrome not running — run fanops hashtags scrape-login") from exc
    expr = (
        "(async () => {"
        f"const r = await fetch({json.dumps(url)}, {{"
        f"method: {json.dumps(method)}, credentials: 'include',"
        "headers: {"
        f"'X-IG-App-ID': {_WEB_APP_ID!r}, 'X-Requested-With': 'XMLHttpRequest',"
        "'X-CSRFToken': (document.cookie.match(/(?:^|; )csrftoken=([^;]+)/) || [])[1] || ''"
        "},"
        f"body: {json.dumps(body) if body is not None else 'undefined'}"
        "});"
        "const text = await r.text();"
        "let data = null; try { data = JSON.parse(text); } catch (e) {}"
        "return {status: r.status, login: (r.url || '').includes('/accounts/login'), json: data};"
        "})()"
    )
    cdp = _Cdp(ws_url)
    try:
        _ensure_instagram_origin(cdp)
        result = cdp.call(
            "Runtime.evaluate",
            {"expression": expr, "awaitPromise": True, "returnByValue": True},
        )
    except (OSError, TimeoutError) as exc:
        raise ScrapeUnavailable("scrape chrome not running — run fanops hashtags scrape-login") from exc
    finally:
        cdp.close()
    if result.get("exceptionDetails"):
        raise RuntimeError("instagram web evaluate failed")
    value = ((result.get("result") or {}) if isinstance(result, dict) else {}).get("value")
    if not isinstance(value, dict):
        raise RuntimeError("instagram web bad payload")
    status = value.get("status")
    if value.get("login") or status in (401, 403):
        raise LoginRequired(f"web {status or 'login'}")
    if status is not None and int(status) >= 400:
        raise RuntimeError(f"instagram web {status}")
    payload = value.get("json")
    if not isinstance(payload, dict):
        raise RuntimeError("instagram web non-json")
    return payload


def _ensure_instagram_origin(cdp: "_Cdp") -> None:
    loc = cdp.call("Runtime.evaluate", {"expression": "location.hostname", "returnByValue": True})
    host = ((loc.get("result") or {}) if isinstance(loc, dict) else {}).get("value")
    if isinstance(host, str) and "instagram.com" in host:
        return
    cdp.call("Page.enable")
    cdp.call("Page.navigate", {"url": "https://www.instagram.com/"})
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        loc = cdp.call("Runtime.evaluate", {"expression": "location.hostname", "returnByValue": True})
        host = ((loc.get("result") or {}) if isinstance(loc, dict) else {}).get("value")
        if isinstance(host, str) and "instagram.com" in host:
            return
        time.sleep(0.25)
    raise ScrapeUnavailable("scrape chrome did not open Instagram")


class _Cdp:
    """One page-target CDP session. Localhost only."""

    def __init__(self, ws_url: str):
        self._ws = _Ws.connect(ws_url)
        self._n = 0

    def call(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        self._n += 1
        mid = self._n
        self._ws.send_text(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = json.loads(self._ws.recv_text(deadline - time.monotonic()))
            if not isinstance(msg, dict) or msg.get("id") != mid:
                continue
            if msg.get("error"):
                raise RuntimeError(str(msg["error"])[:160])
            raw = msg.get("result")
            return raw if isinstance(raw, dict) else {}
        raise TimeoutError(method)

    def close(self) -> None:
        self._ws.close()


class _Ws:
    def __init__(self, sock: socket.socket, tail: bytes = b""):
        self._s = sock
        self._buf = tail

    @classmethod
    def connect(cls, url: str, timeout: float = 8) -> "_Ws":
        import base64
        u = urlparse(url)
        if u.scheme != "ws" or u.hostname != "127.0.0.1":
            raise RuntimeError("cdp refused: not localhost")
        port = u.port or 80
        if port in (9222, 9223) or not (9331 <= port <= 9399):
            raise RuntimeError("cdp refused: not a FanOps scrape port")
        path = u.path + (("?" + u.query) if u.query else "")
        sock = socket.create_connection((u.hostname, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "\r\n"
            ).encode()
        )
        buf = b""
        sock.settimeout(timeout)
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                sock.close()
                raise RuntimeError("cdp handshake eof")
            buf += chunk
        head, rest = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            sock.close()
            raise RuntimeError("cdp handshake refused")
        return cls(sock, rest)

    def send_text(self, text: str) -> None:
        payload = text.encode()
        mask = os.urandom(4)
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header.extend(n.to_bytes(2, "big"))
        else:
            header.append(0x80 | 127)
            header.extend(n.to_bytes(8, "big"))
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._s.sendall(header + masked)

    def recv_text(self, timeout: float) -> str:
        deadline = time.monotonic() + max(timeout, 0.05)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("cdp ws")
            self._s.settimeout(remaining)
            opcode, payload = self._frame()
            if opcode == 0x1:
                return payload.decode()
            if opcode == 0x8:
                raise RuntimeError("cdp ws closed")
            if opcode == 0x9:
                self._pong(payload)

    def _need(self, n: int) -> None:
        while len(self._buf) < n:
            chunk = self._s.recv(max(n - len(self._buf), 4096))
            if not chunk:
                raise RuntimeError("cdp ws eof")
            self._buf += chunk

    def _frame(self) -> tuple[int, bytes]:
        self._need(2)
        b0, b1 = self._buf[0], self._buf[1]
        self._buf = self._buf[2:]
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        if length == 126:
            self._need(2)
            length = int.from_bytes(self._buf[:2], "big")
            self._buf = self._buf[2:]
        elif length == 127:
            self._need(8)
            length = int.from_bytes(self._buf[:8], "big")
            self._buf = self._buf[8:]
        mask = b""
        if masked:
            self._need(4)
            mask = self._buf[:4]
            self._buf = self._buf[4:]
        self._need(length)
        payload = self._buf[:length]
        self._buf = self._buf[length:]
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def _pong(self, payload: bytes) -> None:
        mask = os.urandom(4)
        header = bytearray([0x8A, 0x80 | len(payload)])
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._s.sendall(header + masked)

    def close(self) -> None:
        try:
            self._s.close()
        except OSError:
            pass
