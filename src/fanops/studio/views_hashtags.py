"""U11 — the Hashtags observatory read-model (pure; ZERO network on any call). Surfaces the platform
measurement cache, the derived per-persona corpora, and cross-account rotation health. Every projection
here is a LOCAL file + ledger read: `_store_status` reads the cache file, `_corpora_rows` reads
personas.json, `rotation_health` scans the ledger. The page spends no Graph call by construction.
Read-only: the cache is a measurement record; the operator's lever is the persona's declared niche.

The old "budget meter" section is gone with the fiction it displayed — there is no local allowance to
report. What the operator needs instead is COVERAGE: how much of the cache is measured and how fresh it
is, which is what StoreStatus now carries.

Mirrors views_results.py: dataclass rows, pure reads, fail-open with a breadcrumb."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.hashtags import (SIZE_FIELD, TREND_FIELD, _norm, load_measurements, ranked_tags,
                             size_rank_key, tag_size, tag_trend)
from fanops.personas import Personas
from fanops.studio.views_results import _EXPOSURE_STATES

# TWO different Instagram fields, shown separately and never merged into one "score" (MOL-692): SIZE is
# the tag's own lifetime post volume; TREND is the best plays on a Reel it carried in the last 7 days.
# The old single METRIC_LABEL is gone — it quoted a Top-post median as if it measured the tag.
SIZE_LABEL = f"size ({SIZE_FIELD} — posts carrying the tag)"
TREND_LABEL = f"7d Reels max ({TREND_FIELD})"


@dataclass
class CorpusRow:
    """Section 1: one persona's DERIVED corpus at a glance (read-only — there is nothing to edit; the
    persona's description is the lever)."""
    pid: str
    name: str
    size: int
    last_refreshed: Optional[str]      # Layer A `last_complete_pass` (last finished check), not tag sample age
    top3: list                         # the 3 biggest corpus tags (size-first), truncated
    edit_href: str = ""                # url_for('personas_view') — the "edit →" link


@dataclass
class StoreStatus:
    """Section 2: the measurement cache — its state, freshness, and the ranked chips."""
    state: str                         # "empty" (no cache yet) | "unreadable" (parse error) | "ok"
    size_label: str = SIZE_LABEL       # what the PRIMARY number is, in Instagram's own field name
    trend_label: str = TREND_LABEL     # what the SECONDARY number is
    age: Optional[str] = None          # cache file mtime, ISO (None when missing/unreadable)
    oldest: Optional[str] = None       # the stalest `measured_at` in the cache — the real freshness signal
    tags: list = field(default_factory=list)   # [{tag, size, trend}] size-first ranked (size_rank_key)


@dataclass
class RotationAccount:
    """Section 3: one account's rotation health — the last N tag lines + the consecutive-duplicate warn."""
    account: str
    warn: bool                         # True iff two adjacent (by created_at desc) posts shipped an identical tag line
    lines: list = field(default_factory=list)   # [[tag, ...], ...] the recent tag lines (most-recent first)


@dataclass
class HashtagsPage:
    """The whole /hashtags read-model. Pure; assembled with zero network."""
    corpora: list = field(default_factory=list)
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
    rows = [{"tag": t, "size": tag_size(m[t]), "trend": tag_trend(m[t])} for t in ranked_tags(m)]
    return StoreStatus(state="ok", age=age, oldest=(min(stamps) if stamps else None), tags=rows)


def rotation_health(led: Ledger, *, n: int = 5) -> list:
    """Section 3 read: per account, take the last `n` in-flight/shipped posts (by created_at desc) — the
    SAME posts tag_exposure counts (reuses _EXPOSURE_STATES) — and warn when any two ADJACENT posts shipped
    an identical FULL tag line (compared as normalized tuples). This is the exact pre-S06 failure the
    operator caught live. Observatory ONLY — it never calls vet_hashtags. Pure read; never raises."""
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


def _corpora_rows(cfg: Config, *, edit_href: str = "") -> list:
    """Section 1 read: one row per persona — corpus size, last Layer A complete pass, the 3 BIGGEST tags
    (`size_rank_key`). Byte-truth: personas.json + hashtags.json `last_complete_pass`."""
    from fanops.fanops_hashtags import _read_complete_pass
    m = load_measurements(cfg)
    last_check = _read_complete_pass(cfg)
    rows: list[CorpusRow] = []
    for per in Personas.load(cfg).all():
        corpus = [_norm(t) for t in (per.hashtag_corpus or []) if isinstance(t, str) and _norm(t)]
        top3 = sorted(corpus, key=lambda t: size_rank_key(t, m.get(t) or {}))[:3]
        rows.append(CorpusRow(pid=per.id, name=per.name or per.id, size=len(corpus),
                              last_refreshed=last_check, top3=top3,
                              edit_href=edit_href))
    return rows


def hashtags_page(cfg: Config, *, led: Optional[Ledger] = None, edit_href: str = "",
                  now: Optional[datetime] = None) -> HashtagsPage:
    """Assemble the whole /hashtags read-model with ZERO network. `led` is injected by the route (one
    Ledger.load); `edit_href` is url_for('personas_view'). Fail-open: each section is internally guarded,
    so the page never 500s. `now` is accepted for call-compat; nothing here is time-dependent."""
    if led is None:
        led = Ledger.load(cfg)
    return HashtagsPage(
        corpora=_corpora_rows(cfg, edit_href=edit_href),
        store=_store_status(cfg),
        rotation=rotation_health(led),
    )
