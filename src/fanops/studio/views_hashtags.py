"""Hashtags page read-model (pure; ZERO network). Posted tags are the per-source lock.
The measurement cache meters those names (play_rank_key order) — it is not the caption menu.
Every projection is a local file + ledger read."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.hashtags import (SIZE_FIELD, TREND_FIELD, _norm, load_measurements, play_rank_key,
                             tag_size, tag_trend)
from fanops.studio.views_results import _EXPOSURE_STATES

# TWO different Instagram fields, shown separately and never merged into one "score" (MOL-692): SIZE is
# the tag's own lifetime post volume; TREND is the best plays on a Reel it carried in the last 7 days.
# The old single METRIC_LABEL is gone — it quoted a Top-post median as if it measured the tag.
SIZE_LABEL = f"size ({SIZE_FIELD} — posts carrying the tag)"
TREND_LABEL = f"7d Reels max ({TREND_FIELD})"


@dataclass
class LockRow:
    """One native source's lock. `state` is ready | empty | in_progress | missing."""
    sid: str
    title: str
    n: int
    researched_at: Optional[str]
    tags: list = field(default_factory=list)
    state: str = "missing"


@dataclass
class StoreStatus:
    """Measurement cache: state, freshness, and play-ranked chips (same key as lock order)."""
    state: str                         # "empty" (no cache yet) | "unreadable" (parse error) | "ok"
    size_label: str = SIZE_LABEL
    trend_label: str = TREND_LABEL
    age: Optional[str] = None
    oldest: Optional[str] = None
    tags: list = field(default_factory=list)   # [{tag, size, trend}] play_rank_key order


@dataclass
class RotationAccount:
    """Section 3: one account's rotation health — the last N tag lines + the consecutive-duplicate warn."""
    account: str
    warn: bool                         # True iff two adjacent (by created_at desc) posts shipped an identical tag line
    lines: list = field(default_factory=list)   # [[tag, ...], ...] the recent tag lines (most-recent first)


@dataclass
class HashtagsPage:
    """The whole /hashtags read-model. Pure; assembled with zero network."""
    locks: list = field(default_factory=list)
    lock_ready: int = 0
    lock_total: int = 0
    store: Optional[StoreStatus] = None
    rotation: list = field(default_factory=list)


def _store_status(cfg: Config) -> StoreStatus:
    """Section 2 read: distinguish THREE cache states — no file yet (empty), parse error (unreadable + one
    log breadcrumb), or ok (ranked chips + mtime + the stalest measurement). Never raises."""
    p = cfg.hashtags_path
    if not p.exists():
        return StoreStatus(state="empty")
    try:
        import json
        raw = json.loads(p.read_text())
    except (OSError, ValueError, TypeError) as e:
        get_logger(cfg)("hashtags", "store", "store_unreadable", err=str(e)[:160])   # ONE breadcrumb, page never 500s
        return StoreStatus(state="unreadable")
    if not isinstance(raw, dict):
        get_logger(cfg)("hashtags", "store", "store_unreadable", err="expected a JSON object")
        return StoreStatus(state="unreadable")
    m = load_measurements(cfg)
    try:
        age = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        age = None
    stamps = [r.get("measured_at") for r in m.values() if isinstance(r.get("measured_at"), str)]
    order = sorted(m, key=lambda t: play_rank_key(t, m[t]))
    rows = [{"tag": t, "size": tag_size(m[t]), "trend": tag_trend(m[t])} for t in order]
    return StoreStatus(state="ok", age=age, oldest=(min(stamps) if stamps else None), tags=rows)


def rotation_health(led: Ledger, *, n: int = 5) -> list:
    """Per account, last `n` in-flight/shipped posts (created_at desc). Warn when two adjacent posts
    shipped an identical full tag line. Observatory only — ingest does not rotate. Pure read."""
    by_account: dict[str, list] = {}
    for p in led.posts.values():
        if p.state not in _EXPOSURE_STATES:
            continue
        by_account.setdefault(p.account, []).append(p)
    out: list[RotationAccount] = []
    for account in sorted(by_account):
        # created_at desc; None sorts last (a hand-built row without a birth stamp), stable by post id.
        posts = sorted(by_account[account], key=lambda p: (p.created_at or "", p.id), reverse=True)[:n]
        lines = [[t for t in (p.hashtags or [])] for p in posts]
        norm_lines = [tuple(_norm(t) for t in ln) for ln in lines]
        warn = any(norm_lines[i] and norm_lines[i] == norm_lines[i + 1] for i in range(len(norm_lines) - 1))
        out.append(RotationAccount(account=account, warn=warn, lines=lines))
    return out


def _lock_rows(cfg: Config, led: Ledger) -> list:
    """Per native source: lock membership. Missing sidecar / no researched_at is not a completed empty lock."""
    from fanops.source_tags import load_source_tag_locks
    table = load_source_tag_locks(cfg)
    rows: list[LockRow] = []
    for src in led.sources.values():
        if getattr(src, "origin_kind", "native") == "third_party":
            continue
        sid = str(getattr(src, "id", "") or "")
        if not sid:
            continue
        rec = table.get(sid) if isinstance(table.get(sid), dict) else {}
        at = rec.get("researched_at") if rec else None
        lock = [t for t in (rec.get("lock") or []) if isinstance(t, str)] if rec else []
        if isinstance(at, str) and at.strip():
            state = "empty" if not lock else "ready"
        elif rec:
            state = "in_progress"
        else:
            state = "missing"
        title = getattr(src, "title", None)
        title = title.strip() if isinstance(title, str) and title.strip() else sid
        rows.append(LockRow(sid=sid, title=title, n=len(lock), researched_at=at if isinstance(at, str) else None,
                            tags=lock, state=state))
    return rows


def hashtags_page(cfg: Config, *, led: Optional[Ledger] = None, edit_href: str = "",
                  now: Optional[datetime] = None) -> HashtagsPage:
    """Assemble the /hashtags read-model with ZERO network. `led` is injected by the route.
    `edit_href` / `now` are accepted for call-compat."""
    if led is None:
        led = Ledger.load(cfg)
    locks = _lock_rows(cfg, led)
    ready = sum(1 for r in locks if r.state in ("ready", "empty"))
    return HashtagsPage(
        locks=locks, lock_ready=ready, lock_total=len(locks),
        store=_store_status(cfg),
        rotation=rotation_health(led),
    )
