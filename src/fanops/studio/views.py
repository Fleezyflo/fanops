"""Pure read-model builders for the Studio (no HTTP, no Flask). Each request re-loads the ledger
(lock-free) and assembles these dataclasses; templates render them. Mutations live in actions.py."""
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fanops.config import Config
from fanops.accounts import Accounts
from fanops.ledger import Ledger
from fanops.models import LIFT_SCORE, ClipState, PostState
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


@dataclass
class SurfacePost:
    post_id: str
    account: str
    platform: str
    persona: Optional[str]
    caption: str
    hashtags: list[str]
    scheduled_time: Optional[str]
    media_url: str
    state: str
    imminent: bool
    editable: bool


@dataclass
class ReviewCard:
    clip_id: str
    preview_url: str
    source_name: str
    label: str                  # operator-facing clip name (timecode-based), never the content-addressed id
    moment_window: str
    reason: str
    language: Optional[str]
    subtitles_burned: bool
    held: bool
    held_reason: Optional[str]
    transcript_excerpt: Optional[str]
    surfaces: list[SurfacePost]
    bucket: str
    clip_state: Optional[str] = None     # the clip's own state — shown on a post-less 'prepared' card


@dataclass
class ScheduleRow:
    post_id: str
    scheduled_time: Optional[str]
    account: str
    platform: str
    clip_id: str
    state: str
    imminent: bool
    editable: bool


@dataclass
class GoLiveChannel:
    platform: str
    integration_id: str        # effective current id: the per-platform integrations[platform], else the
                               # shared account_id fallback, else "" (unmapped). NEVER a secret.


@dataclass
class GoLiveAccount:
    handle: str
    persona: Optional[str]
    channels: list[GoLiveChannel]    # one per platform this handle posts to


@dataclass
class GoLiveStatus:
    mode: str
    is_live: bool
    postiz_url: Optional[str]
    key_set: bool              # BOOL only — the POSTIZ_API_KEY value is NEVER carried in this read-model
    accounts: list[GoLiveAccount]
    checks: list[dict]
    notes: list[str]
    learning_validated: bool = False   # M3: cutover.json metrics_confirmed — the loop is unfrozen on this backend


@dataclass
class LiftRow:
    variant_hook: Optional[str]
    account: str
    platform: str
    lift_score: float
    loop_state: str
    amplify_state: Optional[str] = None


@dataclass
class LiftView:
    variant_rows: list[LiftRow]
    variant_empty_reason: Optional[str]
    amplify_present: bool
    amplify_rows: list[LiftRow]
    amplify_empty_reason: Optional[str]


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


def _personas(accounts: Accounts) -> dict:
    return {a.handle: a.persona for a in accounts.accounts}

def _timecode(seconds: float) -> str:
    """Whole-second m:ss timecode for an operator-facing clip label (e.g. 73 -> '1:13'). Non-finite
    (inf/nan) degrades to 0:00 — Moment's validator already rejects these, this is belt-and-suspenders."""
    import math
    s = max(0, int(seconds)) if math.isfinite(seconds) else 0
    return f"{s // 60}:{s % 60:02d}"

def _lineage_for_clip(led: Ledger, clip):
    """Return (source_name, label, moment_window, reason, language, transcript_excerpt) for a clip,
    walking clip -> moment -> source. Missing links degrade to safe '—'/None. `label` is the
    operator-facing clip name — a timecode window, never the content-addressed source/clip id."""
    mom = led.moments.get(clip.parent_id)
    src = led.sources.get(mom.parent_id) if mom is not None else None
    source_name = Path(src.source_path).name if (src and src.source_path) else "—"
    if mom is not None:
        moment_window = f"{int(mom.start)}–{int(mom.end)}"                      # en dash (raw seconds)
        label = f"{_timecode(mom.start)}–{_timecode(mom.end)} clip"            # human label
    else:
        moment_window = "—"; label = "Clip"
    reason = mom.reason if (mom and mom.reason) else "—"
    language = src.language if src else None
    excerpt = mom.transcript_excerpt if mom else None
    return source_name, label, moment_window, reason, language, excerpt

def _surface(post, *, persona, now: datetime) -> SurfacePost:
    imm = _imminent(post.scheduled_time, now)
    state = post.state.value
    return SurfacePost(
        post_id=post.id, account=post.account, platform=post.platform.value, persona=persona,
        caption=post.caption, hashtags=list(post.hashtags or []),
        scheduled_time=post.scheduled_time, media_url=f"/media/{post.id}",
        state=state, imminent=imm, editable=(state == PostState.queued.value and not imm))

def _card(led: Ledger, clip, posts, bucket: str, cfg: Config, personas: dict, now: datetime) -> ReviewCard:
    source_name, label, window, reason, language, excerpt = _lineage_for_clip(led, clip)
    surfaces = [_surface(p, persona=personas.get(p.account), now=now)
                for p in sorted(posts, key=lambda p: (p.account, p.platform.value))]
    return ReviewCard(
        clip_id=clip.id, preview_url=f"/clips/{clip.id}", source_name=source_name, label=label,
        moment_window=window, reason=reason, language=language, subtitles_burned=cfg.burn_subs,
        held=bool(clip.held), held_reason=clip.held_reason, transcript_excerpt=excerpt,
        surfaces=surfaces, bucket=bucket, clip_state=clip.state.value)

def review_buckets(led: Ledger, accounts: Accounts, cfg: Config, *, now: datetime) -> list[ReviewCard]:
    """Three buckets (spec §6): editable (queued posts grouped by clip), recent (published/analyzed
    within RECENT_WINDOW_HOURS), held (clips with held=True, no posts). A clip may appear in both
    editable and recent (different posts)."""
    personas = _personas(accounts)
    cards: list[ReviewCard] = []
    queued_by_clip: dict[str, list] = {}
    recent_by_clip: dict[str, list] = {}
    recent_cutoff = now - timedelta(hours=RECENT_WINDOW_HOURS)
    for p in led.posts.values():
        if p.state is PostState.queued:
            queued_by_clip.setdefault(p.parent_id, []).append(p)
        elif p.state in (PostState.published, PostState.analyzed):
            keep = True
            if p.scheduled_time:
                try:
                    dt = parse_iso(p.scheduled_time)
                    keep = dt.tzinfo is not None and dt >= recent_cutoff
                except (ValueError, TypeError):
                    keep = True   # unparseable but shipped -> still show it
            if keep:
                recent_by_clip.setdefault(p.parent_id, []).append(p)
    for clip_id, posts in queued_by_clip.items():
        clip = led.clips.get(clip_id)
        if clip is not None and not clip.held:        # a held clip belongs ONLY in the held bucket
            cards.append(_card(led, clip, posts, "editable", cfg, personas, now))
    for clip_id, posts in recent_by_clip.items():
        clip = led.clips.get(clip_id)
        if clip is not None and not clip.held:        # (same rule for the recent/shipped bucket)
            cards.append(_card(led, clip, posts, "recent", cfg, personas, now))
    clips_with_posts = {p.parent_id for p in led.posts.values()}
    for clip in led.clips.values():
        if clip.held:
            cards.append(_card(led, clip, [], "held", cfg, personas, now))
        elif clip.id not in clips_with_posts and clip.state in PREPARABLE_STATES:
            cards.append(_card(led, clip, [], "prepared", cfg, personas, now))
    return cards


def surface_for_post(led: Ledger, accounts: Accounts, post_id: str, *, now: datetime) -> Optional[SurfacePost]:
    """The single-surface read-model for ONE post — used by the Regenerate route to re-render just
    that surface's editable caption field after the model rewrites it. None if the post is gone."""
    p = led.posts.get(post_id)
    if p is None:
        return None
    return _surface(p, persona=_personas(accounts).get(p.account), now=now)


def schedule_rows(led: Ledger, cfg: Config, *, now: datetime) -> list[ScheduleRow]:
    """Queued posts (the editable timeline) plus recent published/analyzed posts (read-only past),
    sorted chronologically by scheduled_time. Rows with no/naive/unparseable time sort last."""
    recent_cutoff = now - timedelta(hours=RECENT_WINDOW_HOURS)
    rows: list[ScheduleRow] = []
    for p in led.posts.values():
        if p.state is PostState.queued:
            include = True
        elif p.state in (PostState.published, PostState.analyzed):
            include = True
            if p.scheduled_time:
                try:
                    dt = parse_iso(p.scheduled_time)
                    include = dt.tzinfo is not None and dt >= recent_cutoff
                except (ValueError, TypeError):
                    include = True
        else:
            include = False
        if not include:
            continue
        imm = _imminent(p.scheduled_time, now)
        state = p.state.value
        rows.append(ScheduleRow(
            post_id=p.id, scheduled_time=p.scheduled_time, account=p.account,
            platform=p.platform.value, clip_id=p.parent_id, state=state, imminent=imm,
            editable=(state == PostState.queued.value and not imm)))

    def _key(r: ScheduleRow):
        if not r.scheduled_time:
            return (1, "")
        try:
            dt = parse_iso(r.scheduled_time)
            if dt.tzinfo is None:
                return (1, r.scheduled_time)
            return (0, dt.isoformat())
        except (ValueError, TypeError):
            return (1, r.scheduled_time)
    rows.sort(key=_key)
    return rows


def _loop_state(led: Ledger, cfg: Config, accounts: Optional[Accounts], post,
                cache: Optional[dict] = None) -> str:
    """Per-surface learning-loop annotation, reusing the digest's fail-open gate computation.
    `cache` memoises per (account, platform) across one request — without it every variant post
    re-ran the full posts scan inside the scorer (stage-6 audit: digest had the cache, Lift lost it)."""
    try:
        from fanops.digest import gate_state
        return gate_state(led, cfg, post.account, post.platform, cache, accounts=accounts)
    except Exception:
        return "gathering data"

def lift_rows(led: Ledger, cfg: Config, accounts: Optional[Accounts] = None) -> LiftView:
    """Per-variant lift (spec §8): analyzed posts carrying a variant_key + lift_score, ranked desc.
    Honest, reason-bearing empty states per sub-view; amplify section mirrors digest's
    `if cfg.variant_amplify:` gate (absent, not blank, when off)."""
    variant_posts = [p for p in led.posts.values()
                     if p.variant_key and p.state is PostState.analyzed and LIFT_SCORE in p.metrics]
    variant_rows: list[LiftRow] = []
    variant_empty_reason: Optional[str] = None
    if not variant_posts:
        any_analyzed = any(p.state is PostState.analyzed for p in led.posts.values())
        if not any_analyzed:
            variant_empty_reason = ("No results yet — connect Postiz (Go Live) so posts come back "
                                    "with analytics. (Needs a POSTIZ_API_KEY, or a Blotato backend.)")
        else:
            variant_empty_reason = ("Creative variation (FANOPS_CREATIVE_VARIATION) was off when "
                                    "these posts were crossposted — no per-variant lift.")
    else:
        gate_cache: dict = {}                       # one scorer pass per surface per request
        for p in sorted(variant_posts, key=lambda p: p.metrics.get(LIFT_SCORE, 0.0), reverse=True):
            variant_rows.append(LiftRow(
                variant_hook=p.variant_hook or p.variant_key, account=p.account,
                platform=p.platform.value, lift_score=float(p.metrics.get(LIFT_SCORE, 0.0)),
                loop_state=_loop_state(led, cfg, accounts, p, gate_cache)))

    amplify_present = cfg.variant_amplify
    amplify_rows: list[LiftRow] = []
    amplify_empty_reason: Optional[str] = None
    if amplify_present:
        try:
            from fanops.variant_amplify import amplify_candidates
            cands = amplify_candidates(led, cfg)
            for c in cands:
                p = led.posts.get(c.get("post_id"))
                if p is None:
                    continue
                amplify_rows.append(LiftRow(
                    variant_hook=c.get("winning_hook"), account=p.account,
                    platform=p.platform.value, lift_score=float(p.metrics.get(LIFT_SCORE, 0.0)),
                    loop_state="amplify candidate", amplify_state=str(c.get("evidence", ""))))
            if not amplify_rows:
                amplify_empty_reason = "No sustained amplification streaks yet."
        except Exception:
            amplify_empty_reason = "Amplify state unavailable (fail-open)."
    return LiftView(variant_rows=variant_rows, variant_empty_reason=variant_empty_reason,
                    amplify_present=amplify_present, amplify_rows=amplify_rows,
                    amplify_empty_reason=amplify_empty_reason)


def review_candidates(cfg: Config) -> list[dict]:
    """Track C: discover candidates awaiting approval — the top-level thumbnails `fanops discover`
    wrote into 00_review/ (the approved/ subdir is excluded; glob('*.jpg') matches top-level only).
    Lets the operator approve in the browser instead of dragging files in Finder; approving moves the
    thumbnail to 00_review/approved/ (actions.approve_candidate), then `fanops intake` copies the
    original into the inbox."""
    d = cfg.review
    if not d.exists():
        return []
    return [{"eid": p.stem} for p in sorted(d.glob("*.jpg"))]


# States the manual Publish tab surfaces — the by-hand-postable subset of actions._POSTABLE
# (queued is the norm; failed/error/needs_reconcile are recoverable posts the operator posts by hand).
# submitting/submitted are in-flight on a live backend, not a manual worklist item.
_MANUAL_QUEUE = {PostState.queued, PostState.needs_reconcile, PostState.failed, PostState.error}

def publish_queue(cfg: Config, *, now: Optional[datetime] = None) -> list[dict]:
    """Track B (manual / zero-dependency publishing): the worklist of `queued` posts the operator
    posts BY HAND. Each row carries the surface, caption, and the post id (Studio serves the clip at
    /media/<post_id>, marks it posted at /publish/posted/<post_id>). `due` = scheduled_time has
    passed. Due-first, then by schedule. Lock-free read; mutation is actions.mark_published."""
    now = now or datetime.now(timezone.utc)
    led = Ledger.load(cfg)
    rows = []
    for p in led.posts.values():
        if p.state not in _MANUAL_QUEUE:                 # every state mark_published accepts by hand
            continue
        due = False
        if p.scheduled_time:
            try:
                due = parse_iso(p.scheduled_time) <= now
            except Exception:
                due = False
        rows.append({"post_id": p.id, "clip_id": p.parent_id, "account": p.account,
                     "platform": p.platform.value, "caption": p.caption, "state": p.state.value,
                     "scheduled_time": p.scheduled_time, "due": due})
    # due-first; within a bucket by schedule. "9999" sentinel (not "") so a None/unscheduled post
    # sorts LAST, not as if it were the most urgent (ecc:python-review).
    rows.sort(key=lambda r: (not r["due"], r["scheduled_time"] or "9999"))
    return rows


def pipeline_status(cfg: Config) -> dict:
    """Lock-free counts for the Run tab's status line: where the unit chain stands + how many gates
    are waiting + the active poster backend. Lets the operator see, in one glance, whether the next
    move is 'ingest', 'run a pass', or 'answer a gate'."""
    from fanops.agentstep import pending
    led = Ledger.load(cfg)
    return {
        "sources": sum(1 for s in led.sources.values() if s.origin_kind == "native"),  # M1: chain count = native only
        "third_party": sum(1 for s in led.sources.values() if s.origin_kind == "third_party"),
        "clips": len(led.clips), "posts": len(led.posts),
        "published": len(led.posts_in_state(PostState.published)),
        "holds": sum(1 for c in led.clips.values() if c.held),
        "pending_moments": len(pending(cfg, kind="moments")),
        "pending_captions": len(pending(cfg, kind="captions")),
        "backend": cfg.poster_backend,
    }


def asset_catalog(cfg: Config) -> dict:
    """Lock-free read-model for the Library tab (M1): every remembered Source split by origin_kind, with
    just-enough metadata to recognize it. Fail-open — a torn/absent ledger yields empty lists, never a
    500 (the Studio invariant)."""
    try:
        led = Ledger.load(cfg)
    except Exception:                                # invariant: the Library tab must never 500
        return {"native": [], "third_party": []}
    rows = [{"id": s.id, "origin_kind": s.origin_kind, "state": s.state.value,
             "duration": s.duration, "width": s.width, "height": s.height} for s in led.sources.values()]
    return {"native": [r for r in rows if r["origin_kind"] == "native"],
            "third_party": [r for r in rows if r["origin_kind"] == "third_party"]}


def golive_status(cfg: Config) -> GoLiveStatus:
    """Lock-free read-model for the Go-Live tab: the publish mode (dryrun/live), whether Postiz is
    configured (postiz_url is shown — it is NON-secret; key_set is a BOOL only, the key itself is never
    exposed), the ACTIVE accounts to map, and the doctor readiness checks/notes.

    Accounts are listed PER-CHANNEL: each active handle carries one GoLiveChannel per platform, because a
    handle's Instagram and TikTok are DIFFERENT Postiz integrations (M1). Each channel's integration_id is
    the effective current id — the per-platform integrations[platform], else the shared account_id
    fallback, else "" (unmapped). Tolerates a malformed accounts.json (falls back to an empty list) so the
    tab never 500s."""
    from fanops.doctor import doctor_report
    try:
        accts = [GoLiveAccount(
            handle=a.handle, persona=a.persona,
            channels=[GoLiveChannel(platform=p.value,
                                    integration_id=a.integrations.get(p.value) or a.account_id or "")
                      for p in a.platforms])
            for a in Accounts.load(cfg).active()]
    except Exception:
        accts = []                                   # malformed accounts.json — doctor's readiness check below names it
    try:
        report = doctor_report(cfg)
    except Exception:                                # invariant: the Go-Live tab must never 500 (ecc:python-review)
        report = {"checks": [], "notes": ["readiness check unavailable"]}
    from fanops.validation_gate import learning_validated
    return GoLiveStatus(
        mode=cfg.poster_backend,
        is_live=cfg.poster_backend != "dryrun",
        postiz_url=cfg.postiz_url,                    # non-secret; shown so the operator can confirm config
        key_set=cfg.postiz_api_key is not None,       # BOOL only — the API key value is NEVER exposed
        accounts=accts,
        checks=report["checks"],
        notes=report["notes"],
        learning_validated=learning_validated(cfg))   # M3: shows whether the loop is unfrozen (cutover done)


def gate_rows(cfg: Config) -> list[dict]:
    """Lock-free read-model for the Gates tab (Phase 3a): every PENDING moment/caption agent gate
    with the request context the operator needs to answer it (transcript/signals for moments, the
    surface list for captions). Same enumeration `fanops respond` uses, surfaced for the browser.
    A torn/unreadable request file is skipped (fail-open) rather than 500-ing the tab."""
    from fanops.agentstep import pending, request_path
    rows: list[dict] = []
    for kind in ("moments", "captions"):
        for key in pending(cfg, kind=kind):
            try:
                payload = json.loads(request_path(cfg, kind, key).read_text())
            except Exception:
                continue                               # torn/unreadable request file: SKIP it (match the
                                                       # docstring) rather than render an empty, unanswerable
                                                       # gate form whose blank submit could write a bad answer
                                                       # (ecc audit). The corruption is already logged by
                                                       # latest_request_id during pending().
            rows.append({"kind": kind, "key": key, **payload})
    return rows
