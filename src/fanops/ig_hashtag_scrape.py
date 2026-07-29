"""Hashtag Layer A network via instagrapi (Graph hashtag path deferred)."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional
from fanops.config import Config
from fanops.hashtags import CAPTION_TAG_RE, HARVEST_CAP, TOP_SAMPLE_N, _norm, _num

_REEL_TREND_DAYS = 7            # a Reel older than this is history, not "currently trending"
_REEL_PRODUCT_TYPE = "clips"    # Instagram's own product_type for a Reel


def _trunc(msg: object, n: int = 160) -> str:
    s = str(msg or "").replace("\n", " ").strip()
    return s[:n]


class ScrapeUnavailable(Exception):
    """instagrapi missing, no user, or login/session cannot open — Layer A cannot run."""


class ScrapeThrottled(Exception):
    """Instagram asked us to wait / rate-limited — end the pass, keep accrued evidence."""


class ScrapeCheckpoint(ScrapeUnavailable):
    """Instagram locked the account behind a NATIVE challenge (`lock: true`, no `step_name`): correct
    credentials, structurally intact session, and NO api path — only in-app confirmation clears it.
    A ScrapeUnavailable subclass so every existing abort path keeps working; the distinction exists so
    the operator is not told to re-run scrape-login, which cannot clear a lock and only adds login
    pressure to a checkpointed account."""


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


CHECKPOINT_HINT = "verify the login in the official Instagram app or web, then re-run scrape-login"

def _is_checkpoint(exc: BaseException) -> bool:
    """Account-lock detect (challenge / checkpoint), NOT an expired session. Narrow on purpose: a plain
    `login_required` is an expiry the operator fixes with scrape-login and must stay classified as such."""
    blob = f"{type(exc).__name__.lower()} {str(exc).lower()}"
    return any(k in blob for k in ("challenge", "checkpoint", "consent_required"))


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
        if _is_checkpoint(e):
            raise ScrapeCheckpoint(f"account checkpointed by Instagram — {CHECKPOINT_HINT} "
                                   f"({_trunc(e, 80)})") from e
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
    try:
        medias = client.hashtag_medias_top(name, amount=TOP_SAMPLE_N)
    except Exception as e:                                  # noqa: BLE001
        if isinstance(e, ScrapeRefused):
            raise
        if _is_throttle(e):
            raise ScrapeThrottled(_trunc(e)) from e
        raise ScrapeRefused(_trunc(e)) from e
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
