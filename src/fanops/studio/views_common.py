"""Shared read-model primitives for the Studio (no HTTP, no Flask): pagination, the terminology glossary,
account-universe extraction, the time helpers (imminence + the deterministic per-post suggestion) and the
batch-title lookup that several surfaces reuse. Imports ONLY fanops.* — never a sibling views_* module — so
every surface module AND the views.py facade can depend on it without an import cycle.

Exception (D13b): postiz_health_for_banner is the ONE read-model here that touches the network (a single
cheap GET, cached ~30s) — the Postiz backend health probe the Studio banner derives from. Kept here (not a
new module) because it's a global-strip read like the others, and it imports only fanops.post.postiz."""
from __future__ import annotations
import logging
import re as _re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ClipState
from fanops.timeutil import parse_iso

_log = logging.getLogger("fanops.studio.views_common")

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
# operator-visible "never machine-gun" floor a bulk-approve must respect by construction.
# M2 (PRD: 'leaning jittered 2-3h for a human feel') widens the DEFAULT band when
# cfg.realistic_cadence is ON; the M4 30-min floor stays as the SAFE LOWER BOUND when it's OFF.
_BULK_APPROVE_MIN_GAP_MIN = 30
_BULK_APPROVE_JITTER_MAX_MIN = 7   # < _STEP so the per-account schedule stays strictly monotonic
_REALISTIC_MIN_GAP_MIN = 120       # M2: 2h floor on the human-cadence band
_REALISTIC_JITTER_MAX_MIN = 60     # M2: up to +1h jitter -> the band reaches ~3h (2-3h band)


def _cadence_for(cfg: Config) -> "tuple[int, int]":
    """M2: resolve (STEP, JITTER_MAX) from cfg. Realistic ON -> 2-3h band; default -> M4 30-min
    floor. Pure read. Honors the operator's FANOPS_REALISTIC_CADENCE product call."""
    if getattr(cfg, "realistic_cadence", False):
        return (_REALISTIC_MIN_GAP_MIN, _REALISTIC_JITTER_MAX_MIN)
    return (_BULK_APPROVE_MIN_GAP_MIN, _BULK_APPROVE_JITTER_MAX_MIN)


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
    walk each post at `now + i*STEP + jitter` with STEP and JITTER_MAX from `_cadence_for(cfg)`
    (M4 30-min floor by default; M2 2-3h band when cfg.realistic_cadence is on). The walk is
    CUMULATIVE — each gap is `STEP + jitter_i >= STEP` by construction.

    M7: when cfg.account_window(handle) returns (open_h, close_h), slot hours are kept within
    that band — a candidate that falls outside is rolled forward to the next open hour. Window
    is in OPERATOR-LOCAL hours (cfg.operator_tz); None == 24h open.

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
        for p in sorted(by_account[handle], key=lambda q: q.id):
            t = now + timedelta(minutes=cursor_min)
            if t <= now:                       # belt-and-braces (lead_minutes < 0 hand-edit)
                t = now + timedelta(seconds=1)
            t = _roll_into_window(t, window, cfg)    # M7: roll forward to the next open hour if outside
            out[p.id] = iso_z(t)
            jitter = rng.randint(0, jitter_max - 1)
            cursor_min += step + jitter   # forward-only walk: gap >= STEP
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
            # outside the band -> jump to today's open if it's still ahead, else tomorrow's open
            local_open = local.replace(hour=open_h, minute=0, second=0, microsecond=0)
            if h >= close_h:
                local_open = local_open + timedelta(days=1)
            t = local_open.astimezone(t.tzinfo)
        else:                                    # window crosses midnight (e.g. 22 -> 4)
            if h >= open_h or h < close_h:
                return t
            local_open = local.replace(hour=open_h, minute=0, second=0, microsecond=0)
            t = local_open.astimezone(t.tzinfo)
        # safety break: at most one iteration is ever needed
        return t


def _batch_title(led: Ledger, bid: Optional[str]) -> Optional[str]:
    # Face 5: resolve a denormalized Post.batch_id to its Batch.name defensively — a dangling id (batch gone)
    # yields None (renders no label), never an AttributeError. Dict lookup, no I/O.
    b = led.get_batch(bid) if bid else None
    return b.name if b is not None else None


# D13b: the Postiz-down banner read-model. A Studio render hits build_system_strip on EVERY page, so a
# raw probe-per-render would slam Postiz — cache the typed probe result for _POSTIZ_HEALTH_TTL_S. Keyed by
# postiz_url so a URL change re-probes immediately. Process-local (a Studio worker); a stale-by-30s outage
# signal is fine (the banner is informational, self-clears within the TTL of a Postiz recovery).
_POSTIZ_HEALTH_TTL_S = 30.0
_postiz_health_cache: "dict[str, tuple[float, object]]" = {}


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
        _log.debug("postiz-route check failed (banner suppressed): %s", e)
    return False


def postiz_health_for_banner(cfg: Config, *, now: "float | None" = None) -> dict:
    """D13b read-model for the Studio Postiz-down banner. Returns {show, danger, status, hint}. `danger` is True
    ONLY when the probe is unhealthy AND at least one due postiz-routed post is waiting — a reaper-idle stack
    with nothing to publish is muted idle, not a stall. `show` is True for danger OR the muted idle hint when
    a channel routes to postiz and the probe is down. No banner when healthy or no postiz channel. The probe
    result is cached ~30s (keyed by postiz_url). Fail-open: any error -> {show: False} (must never block a page).
    `now` is injected for deterministic cache tests; defaults to time.monotonic()."""
    if not _any_channel_routes_to_postiz(cfg):
        return {"show": False, "danger": False, "status": None, "hint": ""}
    key = cfg.postiz_url or ""
    t = now if now is not None else time.monotonic()
    cached = _postiz_health_cache.get(key)
    if cached is not None and (t - cached[0]) < _POSTIZ_HEALTH_TTL_S:
        health = cached[1]
    else:
        from fanops.post.postiz import postiz_health_probe
        try:
            health = postiz_health_probe(cfg)
        except Exception as e:                       # postiz_health_probe never raises, but stay defensive
            _log.warning("postiz_health_probe raised in banner read (suppressing banner): %s", e)
            return {"show": False, "danger": False, "status": None, "hint": ""}
        _postiz_health_cache[key] = (t, health)
    if health.healthy:
        return {"show": False, "danger": False, "status": health.status_code, "hint": ""}
    status = health.status_code
    postiz_due = 0
    try:
        from fanops.studio.views_results import due_publish_plan
        postiz_due = due_publish_plan(cfg).postiz_due
    except Exception as e:
        _log.debug("due_publish_plan failed in banner read (treat as idle): %s", e)
    if postiz_due <= 0:
        return {"show": True, "danger": False, "status": status,
                "hint": "Postiz idle (starts on publish)"}
    where = f" (status: {status})" if status is not None else ""
    return {"show": True, "danger": True, "status": status,
            "hint": (f"Postiz API unhealthy{where} — publishes via Postiz are stalled. The container's "
                     "health check is nginx-only and can lie; check `docker logs postiz` (see "
                     "docs/POSTIZ_OPS.md).")}


# MOL-125: shared transient-network failure classifier for publish error_reason strings (Studio recovery +
# daemon re-queue). Distinct from run._is_transient_publish_error (Exception-typed publish path).

_TRANSIENT_DAEMON_PREFIX = _re.compile(r"^transient_daemon_retry=(\d+)/(\d+)\|", _re.I)

def transient_daemon_retry_count(error_reason: str | None) -> int:
    """How many daemon-level transient re-queue cycles this post has consumed (0 when unset)."""
    m = _TRANSIENT_DAEMON_PREFIX.match((error_reason or "").strip())
    return int(m.group(1)) if m else 0


def strip_transient_daemon_prefix(error_reason: str | None) -> str:
    er = (error_reason or "").strip()
    return _TRANSIENT_DAEMON_PREFIX.sub("", er, count=1) if er else ""


def is_transient_failure_reason(error_reason: str | None) -> bool:
    """True for DNS/read-timeout/connection blips in a stored error_reason (failed-tab recovery + daemon).
    Permanent 4xx/auth/validation -> False. Poll errors are reconcile-column, not publish transients."""
    er = strip_transient_daemon_prefix(error_reason).lower()
    if not er:
        return False
    if "publish transient error" in er:
        return True
    if "reconcile poll error" in er or "poll error" in er:
        return False
    if any(x in er for x in ("401", "403", "unauthorized", "auth rejected", "credentials rejected")):
        return False
    if any(x in er for x in ("413", "oversize", "too large", "entity too large")):
        return False
    if any(x in er for x in ("400", "bad request", "bad media", "invalid media", "422")):
        return False
    if "429" in er or "rate limit" in er or "too many requests" in er:
        return False
    if any(x in er for x in ("nameresolution", "name resolution", "failed to resolve",
                             "read timed out", "timed out", "timeout", "connection refused",
                             "connection error", "connection reset", "max retries exceeded",
                             "network error", "unreachable", "connection aborted")):
        return True
    m = _re.search(r'\((\d{3})\)', er)
    if m:
        code = int(m.group(1))
        if 500 <= code < 600:
            return True
        if 400 <= code < 500:
            return False
    return False


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
