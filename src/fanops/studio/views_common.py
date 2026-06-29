"""Shared read-model primitives for the Studio (no HTTP, no Flask): pagination, the terminology glossary,
account-universe extraction, the time helpers (imminence + the deterministic per-post suggestion) and the
batch-title lookup that several surfaces reuse. Imports ONLY fanops.* — never a sibling views_* module — so
every surface module AND the views.py facade can depend on it without an import cycle."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ClipState
from fanops.timeutil import parse_iso

IMMINENT_THRESHOLD_MINUTES = 5     # spec §4: a post within this of now (or past) is edit-disabled
RECENT_WINDOW_HOURS = 24           # spec §6: "what just shipped" read-only context window
GRID_PAGE_SIZE = 24                # max cards rendered per surface page — rendering all 164 <video> at
                                   # once is a real perf + usability problem (the black-box-wall report);
                                   # the total stays VISIBLE with a show-more link, never silent truncation


@dataclass
class GridPage:
    """A paginated slice of a card/row list for the Review/Publish grids. `items` is the visible page;
    `total` is the full count (shown so nothing is silently truncated); `next_offset` is the offset for
    the show-more link, or None when this is the last page."""
    items: list
    total: int
    offset: int
    next_offset: Optional[int]


def paginate(rows: list, offset: int, *, page_size: int = GRID_PAGE_SIZE) -> "GridPage":
    """Slice `rows` to one page. Clamps a negative/oversize offset into range; next_offset is None when
    the page reaches the end. Pure — no I/O, trivially testable."""
    total = len(rows)
    off = max(0, min(offset, total))
    page = rows[off:off + page_size]
    nxt = off + page_size if off + page_size < total else None
    return GridPage(items=page, total=total, offset=off, next_offset=nxt)
# A clip is "prepared" (produced, awaiting crosspost) when it has NO posts yet and isn't held — these
# post-less clips used to vanish from Review entirely (the 57-clips-0-posts bug). Only actionable
# in-flight states qualify; retired/error/terminal clips are not surfaced as prepare-able.
PREPARABLE_STATES = (ClipState.rendered, ClipState.captions_requested, ClipState.captioned, ClipState.queued)


# S9 — the plain-language glossary for the insider terms the IA leans on. One frozen source of truth, rendered
# inline (keyboard-accessible) at each term's first use per surface via the _term.html macro + term_def().
TERM_DEFS = {
    "moment": "a worth-clipping window in the source video",
    "cast": "which accounts a moment is routed to (uncast = all)",
    "lever": "a per-persona dial shaping its clips, hooks, captions",
    "batch": "a named, account-targeted group of ingested footage",
    "surface": "one account-on-one-platform destination for a clip",
    "variant": "this account's own version of the clip (its hook/cut/caption)",
    "integration": "the Postiz channel a handle+platform publishes through",
}


def term_def(key) -> Optional[str]:
    """S9 — the plain-language definition for an insider term, or None for an unknown/non-string key (fail-soft:
    a typo in a template never 500s a surface). Pure read over the frozen TERM_DEFS."""
    return TERM_DEFS.get(key) if isinstance(key, str) else None


def accounts_in(rows) -> list[str]:
    """Distinct, sorted account handles present in a built read-model list — the per-surface chip UNIVERSE,
    derived from the POSTS in that list (never Accounts.active(), so a retired account's history stays
    filterable). Dual-shape (P5 R4): dataclass rows expose `.account`; publish_queue returns plain dicts
    with `r["account"]`. Review CARDS are not rows (a card has a list of `surfaces`, no scalar account) — do
    NOT pass cards here; collect their surface accounts with `{s.account for c in cards for s in c.surfaces}`."""
    return sorted({(r["account"] if isinstance(r, dict) else r.account) for r in rows})


def _imminent(scheduled_time: Optional[str], now: datetime,
              threshold_min: int = IMMINENT_THRESHOLD_MINUTES) -> bool:
    """True (edit-disabled) when the time is missing, unparseable, naive, already due, or within
    `threshold_min` of `now`. Fail-safe: any doubt -> imminent (read-only), never editable. `now`
    must be timezone-aware UTC."""
    if not scheduled_time:
        return True
    try:
        dt = parse_iso(scheduled_time)
    except (ValueError, TypeError):
        return True
    if dt.tzinfo is None:
        return True
    return dt <= now + timedelta(minutes=threshold_min)


def suggest_time(cfg: Config, post, *, now: datetime) -> str:
    """ONE deterministic, strictly-future ISO-Z suggestion for a single post (P1). REUSES crosspost's
    proven surface_time with index=0 — a single anchored near-future time, NEVER a 40-min stagger (the
    stagger only appears at index>0, reachable only via operator Reschedule-all). Depends solely on
    account/platform/parent_id (all on the Post) + lead_minutes, so it never resolves a clip/moment and
    survives broken lineage. Pure, lock-free, no ledger write. Local import keeps views->crosspost acyclic
    (mirrors reschedule_bucket). Anti-degenerate: a raw value <= now (seed%50==0 && jitter==0 with lead 0)
    gets the smallest deterministic +1s nudge so the suggestion is never == now (which would re-open the
    publish-now hole) — NOT a cadence rule, just 'never equal now'.

    NB: a BULK approve must NOT call this once per post — N stale posts collide on iso_z(now+1s) and the
    short-circuit branch produces a single identical minute for every post that hits it (the M4 bug:
    'the system schedules EVERYTHING on the same date and time'). The batch path is
    `suggest_times_for_batch` below — it owns the per-account spread invariant by construction."""
    from fanops.crosspost import surface_time
    from fanops.timeutil import iso_z
    raw = surface_time(now, post.account, post.platform.value, now.date().isoformat(), 0,
                       clip_id=post.parent_id or "", lead_minutes=cfg.publish_lead_minutes)
    if parse_iso(raw) <= now:
        return iso_z(now + timedelta(seconds=1))
    return raw


# M4: per-account approve-batch spread. The cadence floor is wider than the crosspost-mint stagger
# (which is per-(clip,surface) anti-collision, not a believable post cadence) — 30 min is the
# operator-visible "never machine-gun" floor a bulk-approve must respect by construction. A future
# milestone (M2 in the realistic-per-account-scheduling PRD) widens this to 2-3h jittered for the
# Reschedule path; the floor here is the SAFE LOWER BOUND that prevents the M4 collide regardless.
_BULK_APPROVE_MIN_GAP_MIN = 30
_BULK_APPROVE_JITTER_MAX_MIN = 7   # < _STEP so the per-account schedule stays strictly monotonic


def suggest_times_for_batch(cfg: Config, posts, *, now: datetime) -> dict[str, str]:
    """M4 — ONE batch-aware spread for N posts. Returns {post_id: ISO-Z}, strictly-future,
    pairwise-distinct across the whole batch, and obeying a per-account minimum gap.

    Why not call `suggest_time` per post: that produces an identical iso_z(now+1s) for every post
    whose `surface_time(...index=0)` falls <= now, AND for posts on the same (account, platform,
    clip_id) the SHA1 seed collapses to the same minute. Both make a bulk Approve land every post
    on the same wall-clock minute — the operator's verbatim 'schedules EVERYTHING on the same
    date and time'. The batch path owns the spread CONTRACT instead of reusing the single-post
    helper, so the bad path is unconstructable.

    Algorithm: group posts by account; within each group seed an account-local RNG from the
    account + date so two operators on the same day produce the same suggestion (no surprise),
    walk each post at `now + i*STEP + jitter` with STEP = _BULK_APPROVE_MIN_GAP_MIN, jitter
    bounded < STEP (monotonic-by-index, mirrors crosspost.surface_time's H1/H2 monotonicity
    proof). Inter-account anchors stagger by a small per-account hash offset so two accounts
    don't open the batch on the same minute either.

    Pure (no I/O, no ledger write). Pinned by tests/test_bulk_approve_spread.py."""
    import hashlib, random
    from fanops.timeutil import iso_z
    # Stable account order (deterministic across processes, no Python hash() salt).
    by_account: dict[str, list] = {}
    for p in posts:
        by_account.setdefault(p.account, []).append(p)
    accounts_sorted = sorted(by_account)
    date_str = now.date().isoformat()
    out: dict[str, str] = {}
    for ai, handle in enumerate(accounts_sorted):
        rng = random.Random(int(hashlib.sha1(f"{handle}|{date_str}".encode()).hexdigest()[:8], 16))
        # Per-account anchor offset: a small minute offset (< STEP) keyed on the account so two
        # accounts don't both open at minute 0. Bounded so the first slot stays near `now`.
        anchor_offset = rng.randint(0, _BULK_APPROVE_MIN_GAP_MIN - 1)
        # Deterministic order WITHIN the account (post id) so the same selection produces the same
        # times across runs / processes. The walk is CUMULATIVE — each slot is the previous slot
        # PLUS step PLUS jitter — so every consecutive gap is `STEP + jitter_i >= STEP` by
        # construction. A non-cumulative `i*STEP + jitter_i` formulation lets gaps dip to
        # `STEP - (JITTER_MAX - 1)` (the original M4 GREEN attempt failed exactly this way), which
        # would re-open the floor as a probabilistic property guarded by tests rather than an
        # invariant. The cumulative form makes the bad path unconstructable.
        cursor_min = anchor_offset + cfg.publish_lead_minutes
        for p in sorted(by_account[handle], key=lambda q: q.id):
            t = now + timedelta(minutes=cursor_min)
            if t <= now:                       # belt-and-braces (lead_minutes < 0 hand-edit)
                t = now + timedelta(seconds=1)
            out[p.id] = iso_z(t)
            jitter = rng.randint(0, _BULK_APPROVE_JITTER_MAX_MIN - 1)
            cursor_min += _BULK_APPROVE_MIN_GAP_MIN + jitter   # forward-only walk: gap >= STEP
    return out


def _batch_title(led: Ledger, bid: Optional[str]) -> Optional[str]:
    # Face 5: resolve a denormalized Post.batch_id to its Batch.name defensively — a dangling id (batch gone)
    # yields None (renders no label), never an AttributeError. Dict lookup, no I/O.
    b = led.get_batch(bid) if bid else None
    return b.name if b is not None else None
