"""U11 — the Hashtags observatory read-model (pure; ZERO network on any call). Surfaces what S12 (automated
per-persona corpora) + U9 (per-persona display) leave homeless: the reach store, the Meta budget, cross-account
rotation health, and the operator's global ban lane. Every projection here is a LOCAL file + ledger read —
budget_meter wraps meta_graph.budget_remaining (which reads the counter FILE, no Graph call), _store_status
reads the store file, rotation_health scans the ledger. The page is budget-INERT by construction: nothing here
spends a Graph query. The ONLY mutation on the page is ban add/remove (studio/hashtags.py), never a GET.

Mirrors views_results.py: dataclass rows, pure reads, fail-open with a breadcrumb. Depends on hashtags (bans +
store reach), meta_graph (budget), personas/persona_research (corpora), views_results (_EXPOSURE_STATES reuse)."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.hashtags import _norm, load_bans, load_store, load_store_reach
from fanops.personas import Personas
from fanops.persona_research import _partition_corpus, _persona_row
from fanops.studio.views_results import _EXPOSURE_STATES

# U11: the EXACT fail-closed copy for the budget meter — the plain-language rendering of meta_graph.
# budget_remaining returning None (the counter is unreadable -> the code refuses every Graph query). Kept
# verbatim so the test pins the literal, no invented numbers on a corrupt/torn counter.
BUDGET_UNREADABLE_COPY = "budget file unreadable — querying nothing until it heals"


@dataclass
class CorpusRow:
    """Section 1: one persona's corpus at a glance (read-only — editing lives in U9/Personas)."""
    pid: str
    name: str
    size: int
    pinned: int
    auto: int
    last_refreshed: Optional[str]      # max `added` stamp across this persona's hashtag_corpus_meta (NOT the global marker)
    top3: list                         # corpus tags sorted by live store reach, truncated to 3
    edit_href: str = ""                # url_for('personas_view') — the "edit →" link (section 1 has NO edit controls)


@dataclass
class StoreStatus:
    """Section 2: the reach store — its state + the ranked chips (with a per-tag banned flag for the struck view)."""
    state: str                         # "frozen floor" (file missing) | "unreadable" (parse error) | "ok"
    provenance: str = ""               # "graph-reach" when a non-empty reach map is present, else "frozen floor"
    age: Optional[str] = None          # store file mtime, ISO (None when missing/unreadable)
    tags: list = field(default_factory=list)   # [{tag, reach, banned}] ranked by reach desc


@dataclass
class BudgetMeter:
    """Section 3: the Meta ig_hashtag_search budget (30 unique tags / 7 days). fail_closed names the code's
    actual behavior when the counter is unreadable — no invented used/remaining then."""
    fail_closed: bool
    copy: str = ""                     # the plain-language fail-closed line (only when fail_closed)
    limit: int = 0
    used: int = 0
    remaining: int = 0
    window_reset: Optional[str] = None  # oldest recorded query ts + 7d (None when no queries recorded / fail-closed)


@dataclass
class RotationAccount:
    """Section 4: one account's rotation health — the last N tag lines + the consecutive-duplicate warn."""
    account: str
    warn: bool                         # True iff two adjacent (by created_at desc) posts shipped an identical tag line
    lines: list = field(default_factory=list)   # [[tag, ...], ...] the recent tag lines (most-recent first)


@dataclass
class HashtagsPage:
    """The whole /hashtags read-model — the five sections. Pure; assembled with zero network."""
    corpora: list = field(default_factory=list)
    store: Optional[StoreStatus] = None
    budget: Optional[BudgetMeter] = None
    rotation: list = field(default_factory=list)
    bans: list = field(default_factory=list)   # the sorted ban list (display + remove targets)


def _store_status(cfg: Config) -> StoreStatus:
    """Section 2 read: distinguish THREE store states — missing file (frozen floor), parse error (unreadable
    + one log breadcrumb), or ok (ranked chips + mtime age + provenance). Banned tags are FLAGGED for the
    struck-through view but the store FILE is untouched (view-only). Never raises."""
    p = cfg.hashtags_path
    bans = load_bans(cfg)
    if not p.exists():
        return StoreStatus(state="frozen floor", provenance="frozen floor")
    try:
        import json
        raw = json.loads(p.read_text())
    except (OSError, ValueError, TypeError) as e:
        get_logger(cfg)("hashtags", "store", "store_unreadable", err=str(e)[:160])   # ONE breadcrumb, page never 500s
        return StoreStatus(state="unreadable")
    if not isinstance(raw, dict):
        get_logger(cfg)("hashtags", "store", "store_unreadable", err="expected a JSON object")
        return StoreStatus(state="unreadable")
    tags = load_store(cfg) or []
    reach = load_store_reach(cfg)
    provenance = "graph-reach" if reach else "frozen floor"
    try:
        age = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        age = None
    ranked = sorted(tags, key=lambda t: (-(reach.get(_norm(t), -1.0)), _norm(t)))
    rows = [{"tag": t, "reach": reach.get(_norm(t)), "banned": _norm(t) in bans} for t in ranked]
    return StoreStatus(state="ok", provenance=provenance, age=age, tags=rows)


def budget_meter(cfg: Config, *, now: Optional[datetime] = None) -> BudgetMeter:
    """Section 3 read: wrap meta_graph.budget_remaining (a FILE read, no Graph call). None -> fail-closed with
    the EXACT plain-language copy (no invented numbers). Else used = 30 - remaining, and the window reset =
    oldest recorded query ts + 7d (from the raw counter file). Never raises / never spends budget."""
    from fanops.meta_graph import budget_remaining, _read_queries, _BUDGET_LIMIT, _BUDGET_WINDOW_DAYS
    now = now or datetime.now(timezone.utc)
    remaining = budget_remaining(cfg, now=now)
    if remaining is None:
        return BudgetMeter(fail_closed=True, copy=BUDGET_UNREADABLE_COPY, limit=_BUDGET_LIMIT)
    reset = None
    q = _read_queries(cfg) or []
    stamps: list[datetime] = []
    for e in q:
        try:
            ts = datetime.fromisoformat(e["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        stamps.append(ts)
    if stamps:
        reset = (min(stamps) + timedelta(days=_BUDGET_WINDOW_DAYS)).isoformat()
    return BudgetMeter(fail_closed=False, limit=_BUDGET_LIMIT, used=_BUDGET_LIMIT - remaining,
                       remaining=remaining, window_reset=reset)


def rotation_health(led: Ledger, *, n: int = 5) -> list:
    """Section 4 read: per account, take the last `n` in-flight/shipped posts (by created_at desc) — the SAME
    posts tag_exposure counts (reuses _EXPOSURE_STATES) — and warn when any two ADJACENT posts shipped an
    identical FULL tag line (compared as normalized tuples). This is the exact pre-S06 failure the operator
    caught live. Observatory ONLY — it never calls vet_hashtags. Pure read; never raises."""
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
    """Section 1 read: one row per persona from Personas.load — size, pinned/auto split (via _partition_corpus
    + the raw hashtag_corpus_meta), last_refreshed = max `added` across that persona's meta, top3 by store
    reach. Read-only (the edit_href points at U9/Personas). Byte-truth: counts come straight from personas.json."""
    reach = load_store_reach(cfg)
    rows: list[CorpusRow] = []
    for per in Personas.load(cfg).all():
        row = _persona_row(cfg, per.id) or {}
        meta = row.get("hashtag_corpus_meta") if isinstance(row.get("hashtag_corpus_meta"), dict) else {}
        corpus = [_norm(t) for t in (per.hashtag_corpus or []) if isinstance(t, str) and _norm(t)]
        pinned, auto = _partition_corpus(corpus, meta)
        stamps = [m.get("added") for m in meta.values() if isinstance(m, dict) and isinstance(m.get("added"), str)]
        last = max(stamps) if stamps else None
        top3 = sorted(corpus, key=lambda t: (-(reach.get(_norm(t), -1.0)), _norm(t)))[:3]
        rows.append(CorpusRow(pid=per.id, name=per.name or per.id, size=len(corpus),
                              pinned=len(pinned), auto=len(auto), last_refreshed=last, top3=top3,
                              edit_href=edit_href))
    return rows


def hashtags_page(cfg: Config, *, led: Optional[Ledger] = None, edit_href: str = "",
                  now: Optional[datetime] = None) -> HashtagsPage:
    """Assemble the whole /hashtags read-model — the five sections — with ZERO network. `led` is injected by
    the route (one Ledger.load); `edit_href` is url_for('personas_view'). Fail-open: any section that trips is
    already internally guarded (store/budget/rotation each swallow + degrade), so the page never 500s."""
    if led is None:
        led = Ledger.load(cfg)
    return HashtagsPage(
        corpora=_corpora_rows(cfg, edit_href=edit_href),
        store=_store_status(cfg),
        budget=budget_meter(cfg, now=now),
        rotation=rotation_health(led),
        bans=sorted(load_bans(cfg)),
    )
