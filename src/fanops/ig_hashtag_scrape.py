"""Hashtag Layer A network via instagrapi (Graph hashtag path deferred)."""
from __future__ import annotations
from typing import Optional
from fanops.config import Config
from fanops.hashtags import CAPTION_TAG_RE, HARVEST_CAP, _norm


def _trunc(msg: object, n: int = 160) -> str:
    s = str(msg or "").replace("\n", " ").strip()
    return s[:n]


class ScrapeUnavailable(Exception):
    """instagrapi missing, no user, or login/session cannot open — Layer A cannot run."""


class ScrapeThrottled(Exception):
    """Instagram asked us to wait / rate-limited — end the pass, keep accrued evidence."""


class ScrapeRefused(Exception):
    """A non-throttle Instagram refusal for one tag. `code` is optional; message is truncated."""
    def __init__(self, message: str, code=None):
        self.message = _trunc(message); self.code = code
        super().__init__(self.message)


def scrape_configured(cfg: Config) -> bool:
    """True when a scrape login can be attempted: user set AND (session file OR password)."""
    if not (cfg.ig_scrape_user or "").strip():
        return False
    if cfg.ig_scrape_session_path.exists():
        return True
    return bool(cfg.ig_scrape_password)


def _is_throttle(exc: BaseException) -> bool:
    """Fail-safe throttle detect: class name / message cues. Unknown errors are refusals, not invented throttles."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    blob = f"{name} {msg}"
    return any(k in blob for k in ("please_wait", "pleasewait", "rate", "feedback_required", "waitafewminutes"))


def open_client(cfg: Config, *, client_factory=None):
    """Open an authenticated instagrapi Client. Lazy-imports; dumps session after login.
    Never echoes password or session contents. Raises ScrapeUnavailable on miss."""
    user = (cfg.ig_scrape_user or "").strip()
    if not user:
        raise ScrapeUnavailable("FANOPS_IG_SCRAPE_USER unset")
    try:
        if client_factory is None:
            from instagrapi import Client  # lazy: [igscrape] extra
            client_factory = Client
    except ImportError as e:
        raise ScrapeUnavailable("instagrapi not installed — pip install -e '.[igscrape]'") from e
    client = client_factory()
    sess = cfg.ig_scrape_session_path
    try:
        if sess.exists():
            client.load_settings(str(sess))
        pw = cfg.ig_scrape_password or ""
        client.login(user, pw)
        sess.parent.mkdir(parents=True, exist_ok=True)
        client.dump_settings(str(sess))
    except ScrapeUnavailable:
        raise
    except Exception as e:                                  # noqa: BLE001 — login surface is opaque
        if _is_throttle(e):
            raise ScrapeThrottled(_trunc(e)) from e
        raise ScrapeUnavailable(f"scrape login failed: {_trunc(e)}") from e
    return client


def session_client(cfg: Config, *, client_factory=None):
    """Clone a Client from the dumped session ONLY (no login). For parallel Layer A workers.
    Raises ScrapeUnavailable when session missing / instagrapi missing / load fails."""
    sess = cfg.ig_scrape_session_path
    if not sess.exists():
        raise ScrapeUnavailable("ig scrape session missing — run fanops hashtags scrape-login")
    try:
        if client_factory is None:
            from instagrapi import Client
            client_factory = Client
    except ImportError as e:
        raise ScrapeUnavailable("instagrapi not installed — pip install -e '.[igscrape]'") from e
    client = client_factory()
    try:
        client.load_settings(str(sess))
    except Exception as e:                                  # noqa: BLE001
        raise ScrapeUnavailable(f"scrape session load failed: {_trunc(e)}") from e
    return client


def _median(vals: list[float]) -> Optional[float]:
    """Median of a non-empty float list; None when empty. Platform numbers only — no invented blend."""
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def resolve_hashtag_scrape(client, tag: str) -> tuple[Optional[str], Optional[float]]:
    """Resolve '#tag' via instagrapi hashtag_info -> (id, media_count).
    `media_count` is Instagram's own tag volume when the private API serves it (None if absent)."""
    name = _norm(tag).lstrip("#")
    if not name:
        return None, None
    try:
        info = client.hashtag_info(name)
    except Exception as e:                                  # noqa: BLE001
        if isinstance(e, ScrapeRefused):
            raise
        if _is_throttle(e):
            raise ScrapeThrottled(_trunc(e)) from e
        raise ScrapeRefused(_trunc(e)) from e
    hid = getattr(info, "id", None)
    if hid is None:
        return None, None
    s = str(hid).strip()
    if not s:
        return None, None
    mc = getattr(info, "media_count", None)
    media_count = float(mc) if isinstance(mc, (int, float)) and not isinstance(mc, bool) and mc >= 0 else None
    return s, media_count


def measure_and_harvest_scrape(client, tag: str) -> tuple[Optional[dict], dict[str, int]]:
    """ONE hashtag_medias_top fetch → platform metrics + caption co-tag harvest.

    Metrics (only fields Instagram put on the medias — never a blended 'reach'):
      like_count  = median of like_count across top medias that carry one
      play_count  = median of play_count across top medias that carry one (Reels/views when present)

    A tag with neither likes nor plays anywhere in the top grid is UNMEASURED (None, cotags)."""
    name = _norm(tag).lstrip("#")
    if not name:
        return None, {}
    try:
        medias = client.hashtag_medias_top(name, amount=9)
    except Exception as e:                                  # noqa: BLE001
        if isinstance(e, ScrapeRefused):
            raise
        if _is_throttle(e):
            raise ScrapeThrottled(_trunc(e)) from e
        raise ScrapeRefused(_trunc(e)) from e
    if not medias:
        return None, {}
    likes: list[float] = []; plays: list[float] = []
    cotags: dict[str, int] = {}
    for m in medias:
        lv = getattr(m, "like_count", None)
        if isinstance(lv, (int, float)) and not isinstance(lv, bool) and lv >= 0:
            likes.append(float(lv))
        pv = getattr(m, "play_count", None)
        if isinstance(pv, (int, float)) and not isinstance(pv, bool) and pv >= 0:
            plays.append(float(pv))
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
    return metrics, cotags
