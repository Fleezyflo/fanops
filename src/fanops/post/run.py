"""Publish stage. publish_due(now) submits ONLY posts whose scheduled_time <= now (FIX F12 —
v1 dumped the whole queue at once). Crash-safe: mark a post 'submitting' and SAVE before the
network call, so a crash mid-submit cannot lose the fact and cause a duplicate live post on
resume (FIX F11). Media is ensured ONCE PER CLIP (FIX F44). Failed submit -> PostState.failed
(retryable), never analyzed (FIX F22). Held/retired clips never reach here (crosspost skips)."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from fanops.config import Config
from fanops.errors import AuthError
from fanops.ledger import Ledger
from fanops.models import Post, PostState
from fanops.post import get_poster, get_media_uploader, Poster
from fanops.post.media import ensure_clip_media
from fanops.timeutil import parse_iso as _parse, iso_z
from fanops.log import get_logger

def _now(now: str | None) -> datetime:
    return _parse(now) if now else datetime.now(timezone.utc)

def _archive_published(cfg: Config, post: Post) -> None:
    """Day-bucketed, human-browsable record of a just-published post -> 06_published/<YYYY-MM-DD>/<post_id>.json
    (the dir existed but nothing wrote it). FAIL-OPEN: any write/mkdir error is logged and swallowed — the
    archive is a convenience artifact, NEVER a publish blocker (a full disk must not strand a live post). Day =
    post.published_at, else created_at, else scheduled_time, else now (content-lifecycle Phase 3)."""
    try:
        day = None
        for ts in (post.published_at, post.created_at, post.scheduled_time):
            if ts:
                try:
                    dt = _parse(ts)
                    if dt.tzinfo is not None: day = dt.date().isoformat(); break
                except (ValueError, TypeError): pass
        if day is None: day = datetime.now(timezone.utc).date().isoformat()
        d = cfg.published / day; d.mkdir(parents=True, exist_ok=True)
        rec = {"post_id": post.id, "clip_id": post.parent_id, "account": post.account,
               "platform": post.platform.value, "caption": post.caption, "hashtags": list(post.hashtags or []),
               "public_url": post.public_url, "scheduled_time": post.scheduled_time,
               "created_at": post.created_at, "published_at": post.published_at}
        (d / f"{post.id}.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False))
    except Exception as exc:
        try: get_logger(cfg)("publish", post.id, "archive_error", err=str(exc)[:160])
        except Exception: pass

def _is_fatal_auth_error(exc: Exception) -> bool:
    """Auth/config errors mean EVERY post will fail — halt the run instead of marking one post
    failed and grinding through the rest. Matched by the TYPE AuthError (base of BlotatoAuthError +
    PostizAuthError), NOT by a substring in the message (AUDIT H8): the old `"401" in msg or
    "BLOTATO_API_KEY" in msg` both UNDER-fired (a reworded auth error slipped past and burned the
    whole queue — the F52 regression) and OVER-fired (a 5xx whose body contained "401" wrongly
    halted). Each backend's poster/media uploader raises an AuthError subclass on a real auth
    failure; everything else is a per-post failure."""
    return isinstance(exc, AuthError)

def _submit_one(led: Ledger, cfg: Config, poster: Poster, post: Post, _save: Callable[[], None]) -> Ledger:
    """Publish ONE queued post NOW (no schedule gate): ensure media, crash-safe 'submitting' persist
    BEFORE the irreversible network call (FIX F11), poster.publish -> published. A per-post failure
    (e.g. media upload 5xx) marks THIS post failed and is swallowed so the queue keeps moving
    (FIX F54); a FATAL AuthError (bad key/401) is RE-RAISED to halt instead of burning the queue
    (AUDIT H8). Shared by publish_due (after its due-gate) and publish_post (the Publish-now path)."""
    try:
        # ensure media once per clip (FIX F44 — cached on the Clip)
        if not post.media_urls:
            post.media_urls = [ensure_clip_media(led, cfg, post.parent_id)]
        elif cfg.poster_backend != "dryrun":
            # AUDIT (stage-6 HIGH): a variant post is BORN with media_urls=["file://<variant
            # render>"] (crosspost.py stamps the per-account hook-burned file). Pre-stamped media
            # used to skip the upload entirely and ship the LOCAL path to Blotato, which cannot
            # fetch it — every live variant post died. Upload the variant FILE itself, NOT
            # ensure_clip_media (the clip-level cache holds the parent's BASE render — using it
            # would silently drop the burned hook). The https result replaces file:// on the post
            # and is persisted by the submitting _save below, so a retried post never re-uploads.
            # dryrun keeps file:// (the offline pipeline must run with no network by design).
            _upload = get_media_uploader(cfg)
            post.media_urls = [_upload(cfg, Path(u.removeprefix("file://")))
                               if u.startswith("file://") else u for u in post.media_urls]
        # crash-safe: record intent + persist BEFORE the irreversible network call (FIX F11)
        post.state = PostState.submitting
        _save()                                        # crash-safe persist, txn-aware (AUDIT B4)
        led = poster.publish(led, post.id)
        if post.state is PostState.submitted:
            post.state = PostState.published
            post.published_at = iso_z(datetime.now(timezone.utc))   # content-lifecycle: TRUE publish time (Posted-archive day-anchor)
    except Exception as exc:
        if _is_fatal_auth_error(exc):
            raise                                      # bad key/401: halt, don't burn the queue
        # per-post failure (e.g. media upload 5xx): mark THIS post failed, keep going (FIX F54).
        # ECC fix #17 (defensive): if poster.publish already parked the post as needs_reconcile
        # (ambiguous-live), do NOT downgrade it to failed here — failed is re-queueable => double-post.
        if post.state is not PostState.needs_reconcile:
            post.state = PostState.failed
            post.error_reason = f"publish failed: {str(exc)[:200]}"
    # content-lifecycle Phase 3: fail-open day-bucketed record. OUTSIDE the try on purpose (ECC review) —
    # _archive_published is double-guarded and cannot leak, but placing it here makes the guarantee
    # structural: an archive write can NEVER be caught by the per-post except above and downgrade an
    # already-published post to failed (a re-queueable double-post). Fires only on a confirmed publish.
    if post.state is PostState.published:
        _archive_published(cfg, post)
    _save()                                            # persist the post's terminal/failed state
    return led


def publish_due(led: Ledger, cfg: Config, *, now: str | None = None,
                in_transaction: bool = False) -> Ledger:
    poster = get_poster(cfg)
    cutoff = _now(now)
    # AUDIT B4: when called inside Ledger.transaction() (the lock is already held), the crash-safe
    # mid-loop persists must use the UNLOCKED save — a plain led.save() would try to re-acquire the
    # held flock and self-deadlock (block to LockBusyError under the timeout loop). Standalone
    # callers keep the locking save().
    _save = led._save_unlocked if in_transaction else led.save
    # Only 'queued' is iterated: a post stranded in 'submitting' by a crash is deliberately
    # NOT re-driven here — recovering it is a separate reconcile/poll concern, because
    # auto-resubmitting could double-post a live post (FIX F11).
    for post in led.posts_in_state(PostState.queued):
        # Schedule gate (AUDIT M2 / review): a malformed or timezone-naive scheduled_time on disk
        # (hand-edit, corruption, older schema) makes _parse raise TypeError/ValueError — that is a
        # per-post FAILURE (mark this post failed, keep going — FIX F54), never an uncaught escape
        # that, inside advance()'s transaction, would skip the exit-save and roll back the whole
        # pass. The "not due yet" skip (FIX F12) stays normal flow: continue WITHOUT marking failed
        # or saving (unchanged post). The submit body lives in _submit_one (shared with publish_post).
        if post.scheduled_time:
            try:
                not_due = _parse(post.scheduled_time) > cutoff
            except (ValueError, TypeError) as exc:
                post.state = PostState.failed
                post.error_reason = f"bad schedule time {post.scheduled_time!r}: {str(exc)[:120]}"
                _save()
                continue
            if not_due:
                continue                               # not due yet (FIX F12) — leave queued
        led = _submit_one(led, cfg, poster, post, _save)
    return led


def publish_post(led: Ledger, cfg: Config, post_id: str, *, in_transaction: bool = False) -> Ledger:
    """Publish ONE queued post NOW, IGNORING its schedule — the operator clicked 'Publish now' in the
    Studio (milestone 5: publish in the UI). Same crash-safe per-post path as publish_due (_submit_one)
    but with NO due-gate and scoped to a single post: other queued/future posts are untouched. A
    missing or non-queued post is a no-op (the Studio action guards + reports first). A FATAL AuthError
    propagates (halt), matching publish_due."""
    post = led.posts.get(post_id)
    if post is None or post.state is not PostState.queued:
        return led
    poster = get_poster(cfg)
    _save = led._save_unlocked if in_transaction else led.save
    return _submit_one(led, cfg, poster, post, _save)
