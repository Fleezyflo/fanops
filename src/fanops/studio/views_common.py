"""Shared read-model primitives for the Studio (no HTTP, no Flask): pagination, the terminology glossary,
account-universe extraction, the time helpers (imminence + the deterministic per-post suggestion) and the
batch-title lookup that several surfaces reuse. Imports ONLY fanops.* — never a sibling views_* module — so
every surface module AND the views.py facade can depend on it without an import cycle.

postiz_health_for_banner is snapshot-only (deps_health.json via read_dep_snapshot); it does not touch the
network."""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ClipState
from fanops.timeutil import parse_iso, operator_local_day, next_operator_local_midnight

_log = logging.getLogger("fanops.studio.views_common")

IMMINENT_THRESHOLD_MINUTES = 5     # spec §4: a post within this of now (or past) is edit-disabled
RECENT_WINDOW_HOURS = 24           # spec §6: "what just shipped" read-only context window
GRID_PAGE_SIZE = 24                # max cards rendered per surface page — rendering all 164 <video> at
                                   # once is a real perf + usability problem (the black-box-wall report);
                                   # the total stays VISIBLE with a show-more link, never silent truncation
REVIEW_FEED_SLICE = 12             # U6: initial per-account Review feed page — lazy-load reveals the rest


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
# operator-visible "never machine-gun" floor a bulk-approve must respect by construction.
# M2 (PRD: 'leaning jittered 2-3h for a human feel') widens the DEFAULT band when
# cfg.realistic_cadence is ON; the M4 30-min floor stays as the SAFE LOWER BOUND when it's OFF.
_BULK_APPROVE_MIN_GAP_MIN = 30
_BULK_APPROVE_JITTER_MAX_MIN = 7   # < _STEP so the per-account schedule stays strictly monotonic
_REALISTIC_MIN_GAP_MIN = 120       # M2: 2h floor on the human-cadence band
_REALISTIC_JITTER_MAX_MIN = 60     # M2: up to +1h jitter -> the band reaches ~3h (2-3h band)
# Operator product invariant: consecutive same-account slots must not drift more than 9h apart
# on the walk (step+jitter is clamped). Overnight after a full day still rolls to the next open.
_MAX_GAP_MIN = 540
# MOL-708: per-account DAILY ceiling, in OPERATOR-LOCAL calendar days. Cap is 3 — more than three
# queued/published posts on one operator-local day per account is not acceptable. A module
# constant, not an env knob (product invariant, not a per-run dial).
_DAILY_ACCOUNT_CAP = 3


def _cadence_for(cfg: Config) -> "tuple[int, int]":
    """M2: resolve (STEP, JITTER_MAX) from cfg. Realistic ON -> 2-3h band; default -> M4 30-min
    floor. Pure read. Honors the operator's FANOPS_REALISTIC_CADENCE product call."""
    if getattr(cfg, "realistic_cadence", False):
        return (_REALISTIC_MIN_GAP_MIN, _REALISTIC_JITTER_MAX_MIN)
    return (_BULK_APPROVE_MIN_GAP_MIN, _BULK_APPROVE_JITTER_MAX_MIN)


def _occupancy_by_day(occupied, cfg) -> "dict[tuple[str, str], int]":
    """MOL-710: {(account, operator-local day): slots already taken} for posts holding a slot OUTSIDE the
    batch being allocated. Counts DAYS only — it deliberately does not reserve individual minutes, since
    the pre-existing posts sit on their own cadence and the capacity question is about volume. A post with
    no/garbage scheduled_time occupies no day (operator_local_day -> None) and is skipped: an untimed post
    has not claimed a slot. Pure; tolerant of anything post-shaped (getattr, never an attribute error)."""
    out: "dict[tuple[str, str], int]" = {}
    for p in (occupied or ()):
        day = operator_local_day(getattr(p, "scheduled_time", None), cfg)
        if day is None:
            continue
        key = (getattr(p, "account", "") or "", day)
        out[key] = out.get(key, 0) + 1
    return out


def suggest_times_for_batch(cfg: Config, posts, *, now: datetime, occupied=None) -> dict[str, str]:
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
    walk each post at `now + i*STEP + jitter` with STEP and JITTER_MAX from `_cadence_for(cfg)`
    (M4 30-min floor by default; M2 2-3h band when cfg.realistic_cadence is on). The walk is
    CUMULATIVE — each gap is `STEP + jitter_i >= STEP` by construction.

    M7: when cfg.account_window(handle) returns (open_h, close_h), slot hours are kept within
    that band — a candidate that falls outside is rolled forward to the next open hour. Window
    is in OPERATOR-LOCAL hours (cfg.operator_tz); None == 24h open.

    MOL-708: at most `_DAILY_ACCOUNT_CAP` slots land on any one OPERATOR-LOCAL day per account. The
    cadence floor bounds the GAP, not the VOLUME, so before this an approve of a large backlog walked
    through midnight at cadence and piled 47/43/16 on three days. When a local day is full the cursor
    jumps to the next local midnight and the candidate is re-tested (reusing the same window roll), so
    the overflow spills onto following days instead of being dropped: every post still gets a slot,
    gaps stay >= STEP, timestamps stay pairwise distinct, and accounts stay independent (`day_used` is
    per-account, inside the per-account loop).

    MOL-710: `occupied` is the posts already holding a slot OUTSIDE this batch — without it the cap only
    ever bounds the batch in hand, so approving batch A (3/day) then batch B (3/day) put 6 on one day.
    It seeds each account's day tally, so an already-full day is skipped rather than refilled. Default
    None keeps every existing caller byte-identical. The caller supplies it because this function is pure
    and lock-free: making it load the ledger would put I/O and a second lock acquisition inside the three
    open transactions that call it. `accept_suggested_account` passes nothing by design — its batch is
    ALREADY every queued post for the account, so its own posts would be double-counted as occupancy.

    Pure (no I/O beyond cfg.account_window which is a JSON read at the seam). Pinned by
    tests/test_bulk_approve_spread.py + tests/test_operator_timezone_cadence_window.py."""
    import hashlib, random
    from fanops.timeutil import iso_z
    step, jitter_max = _cadence_for(cfg)
    # Stable account order (deterministic across processes, no Python hash() salt).
    by_account: dict[str, list] = {}
    for p in posts:
        by_account.setdefault(p.account, []).append(p)
    accounts_sorted = sorted(by_account)
    date_str = now.date().isoformat()
    occ = _occupancy_by_day(occupied, cfg)        # MOL-710: slots taken outside this batch
    out: dict[str, str] = {}
    for ai, handle in enumerate(accounts_sorted):
        rng = random.Random(int(hashlib.sha1(f"{handle}|{date_str}".encode(), usedforsecurity=False).hexdigest()[:8], 16))
        # Per-account anchor offset: a small minute offset (< STEP) keyed on the account so two
        # accounts don't both open at minute 0. Bounded so the first slot stays near `now`.
        anchor_offset = rng.randint(0, step - 1)
        # M7: read the per-account daily window. None -> 24h open (default-open seam).
        window = cfg.account_window(handle) if hasattr(cfg, "account_window") else None
        # Deterministic order WITHIN the account (post id) so the same selection produces the same
        # times across runs / processes. The walk is CUMULATIVE — each slot is the previous slot
        # PLUS step PLUS jitter — so every consecutive gap is `STEP + jitter_i >= STEP` by
        # construction. A non-cumulative `i*STEP + jitter_i` formulation lets gaps dip to
        # `STEP - (JITTER_MAX - 1)` (the original M4 GREEN attempt failed exactly this way), which
        # would re-open the floor as a probabilistic property guarded by tests rather than an
        # invariant. The cumulative form makes the bad path unconstructable.
        cursor_min = anchor_offset + cfg.publish_lead_minutes
        # MOL-708 slots laid per operator-local day, SEEDED (MOL-710) with this account's out-of-batch load.
        day_used: dict[str, int] = {d: n for (h, d), n in occ.items() if h == handle}
        for p in sorted(by_account[handle], key=lambda q: q.id):
            while True:
                t = now + timedelta(minutes=cursor_min)
                if t <= now:                   # belt-and-braces (lead_minutes < 0 hand-edit)
                    t = now + timedelta(seconds=1)
                t = _roll_into_window(t, window, cfg)    # M7: roll forward to the next open hour if outside
                day = operator_local_day(t, cfg)
                if day is None or day_used.get(day, 0) < _DAILY_ACCOUNT_CAP:
                    break
                # MOL-708: this local day is FULL -> jump the cursor to the next local midnight and
                # re-test (the window roll may push it further still, and that day may be full too).
                # Terminates because each jump is strictly forward past the current candidate.
                nxt = next_operator_local_midnight(t, cfg)
                if nxt is None:
                    break                      # unresolvable tz -> keep today's behaviour, never spin
                # Re-apply the per-account anchor at the new day's open, for the SAME reason it exists
                # at the batch's open: without it every account's first post-rollover slot lands on the
                # identical minute (midnight+1), which would break the pairwise-distinctness contract
                # across accounts. anchor_offset < step, so the slot stays inside the new local day.
                cursor_min = int((nxt - now).total_seconds() // 60) + 1 + anchor_offset
            out[p.id] = iso_z(t)
            if day is not None:
                day_used[day] = day_used.get(day, 0) + 1
            # Window / day-cap roll can snap `t` far ahead of cursor_min (e.g. midnight → 09:00
            # open). Without syncing, the next N candidates stay pre-open and all collapse onto
            # the same local open minute — Re-spread's overflow-day 09:00 pile.
            cursor_min = max(cursor_min, int((t - now).total_seconds() // 60))
            jitter = rng.randint(0, jitter_max - 1)
            # gap >= STEP by construction; also gap <= _MAX_GAP_MIN (operator: never more than 9h apart)
            cursor_min += min(step + jitter, _MAX_GAP_MIN)
    return out


def _roll_into_window(t: datetime, window, cfg) -> datetime:
    """M7: roll `t` forward into the account's [open_h, close_h) operator-local hour band. None
    window -> unchanged (24h open). Honors cfg.operator_tz for the local-hour read. Pure."""
    if window is None:
        return t
    from fanops.timeutil import _operator_zone
    zone = _operator_zone(cfg)
    if zone is None:
        return t                                 # back-compat: no operator tz -> skip the rollover
    open_h, close_h = window
    # Read the operator-local hour at t.
    while True:
        local = t.astimezone(zone)
        h = local.hour
        if open_h <= close_h:                    # window does NOT cross midnight
            if open_h <= h < close_h:
                return t
            # outside the band -> jump to today's open if it's still ahead, else tomorrow's open.
            # Keep the candidate's minute/second: zeroing them wiped per-account anchors and made
            # every handle land on the identical open second after a day-cap rollover (Re-spread
            # calendar: four accounts all at 09:00:00).
            local_open = local.replace(hour=open_h, microsecond=0)
            if h >= close_h:
                local_open = local_open + timedelta(days=1)
            t = local_open.astimezone(t.tzinfo)
        else:                                    # window crosses midnight (e.g. 22 -> 4)
            if h >= open_h or h < close_h:
                return t
            local_open = local.replace(hour=open_h, microsecond=0)
            t = local_open.astimezone(t.tzinfo)
        # safety break: at most one iteration is ever needed
        return t


def _batch_title(led: Ledger, bid: Optional[str]) -> Optional[str]:
    # Face 5: resolve a denormalized Post.batch_id to its Batch.name defensively — a dangling id (batch gone)
    # yields None (renders no label), never an AttributeError. Dict lookup, no I/O.
    b = led.get_batch(bid) if bid else None
    return b.name if b is not None else None


# D13b: the Postiz-down banner read-model. Snapshot-only — reads deps_health.json; never probes.


def _any_channel_routes_to_postiz(cfg: Config) -> bool:
    """True when at least one ACTIVE account channel's effective provider is postiz (intent, not creds —
    a down Postiz is exactly when creds-readiness is moot). Fail-open False: an unreadable registry never
    raises here (the banner just doesn't show)."""
    try:
        from fanops.accounts import load_accounts_safe
        accounts, err = load_accounts_safe(cfg)
        if err:
            return False
        for a in accounts.active():
            for p in a.platforms:
                if accounts.effective_provider(a.handle, p) == "postiz":
                    return True
    except Exception as e:
        _log.warning("postiz-route check failed (banner suppressed): %s", e)
    return False


def postiz_health_for_banner(cfg: Config, *, now: "float | None" = None) -> dict:
    """D13b read-model for the Studio Postiz-down banner. Returns {show, danger, status, hint}. Snapshot-only
    (deps_health.json); no network. `danger` is True ONLY when the postiz row is unhealthy AND at least one
    due postiz-routed post is waiting — a reaper-idle stack with nothing to publish is muted idle, not a stall.
    `show` is True for danger OR the muted idle hint when a channel routes to postiz and the row is down. No
    banner when healthy, snapshot missing, no postiz row, or no postiz channel. Fail-open: any error ->
    {show: False} (must never block a page). `now` is unused (kept so callers that pass now= don't TypeError)."""
    if not _any_channel_routes_to_postiz(cfg):
        return {"show": False, "danger": False, "status": None, "hint": ""}
    try:
        from fanops.health import read_dep_snapshot
        snap = read_dep_snapshot(cfg)
        if not isinstance(snap, dict):
            return {"show": False, "danger": False, "status": None, "hint": ""}
        row = next((d for d in (snap.get("deps") or [])
                    if isinstance(d, dict) and d.get("name") == "postiz"), None)
        if row is None:
            return {"show": False, "danger": False, "status": None, "hint": ""}
        status = row.get("status_code")
        if row.get("ok"):
            return {"show": False, "danger": False, "status": status, "hint": ""}
        postiz_due = 0
        try:
            from fanops.studio.views_results import due_publish_plan
            postiz_due = due_publish_plan(cfg).postiz_due
        except Exception as e:
            _log.warning("due_publish_plan failed in banner read (treat as idle): %s", e)
        if postiz_due <= 0:
            if _postiz_local_autostart(cfg):
                hint = "Postiz idle (starts on publish)"
            else:
                where = f" (status: {status})" if status is not None else ""
                hint = f"Postiz API unreachable{where}"
            return {"show": True, "danger": False, "status": status, "hint": hint}
        where = f" (status: {status})" if status is not None else ""
        return {"show": True, "danger": True, "status": status,
                "hint": (f"Postiz API unhealthy{where} — publishes via Postiz are stalled. The container's "
                         "health check is nginx-only and can lie; check `docker logs postiz` (see "
                         "docs/POSTIZ_OPS.md).")}
    except Exception as e:
        _log.warning("postiz banner snapshot read failed (suppressing banner): %s", e)
        return {"show": False, "danger": False, "status": None, "hint": ""}


def _postiz_local_autostart(cfg: Config) -> bool:
    """True when local Postiz would auto-start on publish (presentation gate — no docker/script probe)."""
    if not cfg.postiz_autostart:
        return False
    from fanops.postiz_lifecycle import _backend_is_postiz, _is_local
    if not _backend_is_postiz(cfg):
        return False
    return _is_local(cfg.postiz_url or "")


def postiz_autostart_hint(cfg: Config, *, now: "float | None" = None) -> dict:
    """S10 golive/strip presentation: parked local Postiz (reaper-idle) vs a real publish stall.
    Returns {parked, hint, danger, block_alert, show, status}. Reuses postiz_health_for_banner (snapshot-only)."""
    banner = postiz_health_for_banner(cfg, now=now)
    local_autostart = _postiz_local_autostart(cfg)
    danger = bool(banner.get("danger"))
    parked = bool(banner.get("show") and not danger and local_autostart)
    if parked:
        hint = "Postiz idle (starts on publish)"
    elif banner.get("show") and not danger:
        sc = banner.get("status")
        where = f" (status: {sc})" if sc is not None else ""
        hint = f"Postiz API unreachable{where}"
    else:
        hint = banner.get("hint") or ""
    return {"parked": parked, "hint": hint, "danger": danger, "block_alert": danger,
            "show": bool(banner.get("show")), "status": banner.get("status")}


# MOL-781: transient classification reads Post.error_kind — never substring-scans error_reason.
# MOL-812: daemon retry count is Post.daemon_transient_retry (int field), not an error_reason prefix.

def is_transient_failure(post) -> bool:
    """True when the failure site stamped ErrorKind.transient (Studio recovery + daemon re-queue)."""
    from fanops.models import ErrorKind
    kind = getattr(post, "error_kind", None)
    return kind is ErrorKind.transient


def lineage_maps(led: Ledger) -> tuple[dict, dict, dict]:
    """One-pass moment/clip/post bucket maps for O(1) lineage lookups (Review/Library pattern)."""
    moms: dict = {}
    for m in led.moments.values():
        moms.setdefault(m.parent_id, []).append(m)
    clips_bm: dict = {}
    for c in led.clips.values():
        clips_bm.setdefault(c.parent_id, []).append(c)
    posts_bc: dict = {}
    for p in led.posts.values():
        posts_bc.setdefault(p.parent_id, []).append(p)
    return moms, clips_bm, posts_bc


def account_color_hue(handle: str) -> int:
    """U7: deterministic HSL hue 0–359 from account handle (SHA1). Pure."""
    import hashlib
    h = hashlib.sha1((handle or "").encode(), usedforsecurity=False).hexdigest()
    return int(h[:8], 16) % 360


def clip_source_of(led: Ledger, clip_id: str) -> Optional[str]:
    """Resolve clip -> moment -> source id for Schedule/Posted source= filter."""
    clip = led.clips.get(clip_id)
    if clip is None:
        return None
    mom = led.moments.get(clip.parent_id)
    return mom.parent_id if mom is not None else None


def source_universe_for_clips(led: Ledger, rows) -> list[tuple[str, str]]:
    """Distinct (source_id, basename) pairs from row clip_ids — Schedule/Posted chip universe."""
    seen: dict[str, str] = {}
    for r in rows:
        cid = getattr(r, "clip_id", None)
        sid = clip_source_of(led, cid) if cid else None
        if not sid or sid in seen:
            continue
        src = led.sources.get(sid)
        seen[sid] = Path(src.source_path).name if src and src.source_path else sid
    return sorted(seen.items(), key=lambda kv: kv[1].lower())
