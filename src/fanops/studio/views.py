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
from fanops.models import LIFT_SCORE, PostState
from fanops.timeutil import parse_iso

IMMINENT_THRESHOLD_MINUTES = 5     # spec §4: a post within this of now (or past) is edit-disabled
RECENT_WINDOW_HOURS = 24           # spec §6: "what just shipped" read-only context window


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
    moment_window: str
    reason: str
    language: Optional[str]
    subtitles_burned: bool
    held: bool
    held_reason: Optional[str]
    transcript_excerpt: Optional[str]
    surfaces: list[SurfacePost]
    bucket: str


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

def _lineage_for_clip(led: Ledger, clip):
    """Return (source_name, moment_window, reason, language, transcript_excerpt) for a clip,
    walking clip -> moment -> source. Missing links degrade to safe '—'/None."""
    mom = led.moments.get(clip.parent_id)
    src = led.sources.get(mom.parent_id) if mom is not None else None
    source_name = Path(src.source_path).name if (src and src.source_path) else "—"
    moment_window = f"{int(mom.start)}–{int(mom.end)}" if mom is not None else "—"   # en dash
    reason = mom.reason if (mom and mom.reason) else "—"
    language = src.language if src else None
    excerpt = mom.transcript_excerpt if mom else None
    return source_name, moment_window, reason, language, excerpt

def _surface(post, *, persona, now: datetime) -> SurfacePost:
    imm = _imminent(post.scheduled_time, now)
    state = post.state.value
    return SurfacePost(
        post_id=post.id, account=post.account, platform=post.platform.value, persona=persona,
        caption=post.caption, hashtags=list(post.hashtags or []),
        scheduled_time=post.scheduled_time, media_url=f"/media/{post.id}",
        state=state, imminent=imm, editable=(state == PostState.queued.value and not imm))

def _card(led: Ledger, clip, posts, bucket: str, cfg: Config, personas: dict, now: datetime) -> ReviewCard:
    source_name, window, reason, language, excerpt = _lineage_for_clip(led, clip)
    surfaces = [_surface(p, persona=personas.get(p.account), now=now)
                for p in sorted(posts, key=lambda p: (p.account, p.platform.value))]
    return ReviewCard(
        clip_id=clip.id, preview_url=f"/clips/{clip.id}", source_name=source_name,
        moment_window=window, reason=reason, language=language, subtitles_burned=cfg.burn_subs,
        held=bool(clip.held), held_reason=clip.held_reason, transcript_excerpt=excerpt,
        surfaces=surfaces, bucket=bucket)

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
        if clip is not None:
            cards.append(_card(led, clip, posts, "editable", cfg, personas, now))
    for clip_id, posts in recent_by_clip.items():
        clip = led.clips.get(clip_id)
        if clip is not None:
            cards.append(_card(led, clip, posts, "recent", cfg, personas, now))
    for clip in led.clips.values():
        if clip.held:
            cards.append(_card(led, clip, [], "held", cfg, personas, now))
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
            variant_empty_reason = ("No analyzed posts yet — a live metrics backend "
                                    "(FANOPS_POSTER ≠ dryrun and BLOTATO_API_KEY) or fed "
                                    "metrics is required.")
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
        "sources": len(led.sources), "clips": len(led.clips), "posts": len(led.posts),
        "published": len(led.posts_in_state(PostState.published)),
        "holds": sum(1 for c in led.clips.values() if c.held),
        "pending_moments": len(pending(cfg, kind="moments")),
        "pending_captions": len(pending(cfg, kind="captions")),
        "backend": cfg.poster_backend,
    }


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
                payload = {}
            rows.append({"kind": kind, "key": key, **payload})
    return rows
