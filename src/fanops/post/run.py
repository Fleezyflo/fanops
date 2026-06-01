"""Publish stage. publish_due(now) submits ONLY posts whose scheduled_time <= now (FIX F12 —
v1 dumped the whole queue at once). Crash-safe: mark a post 'submitting' and SAVE before the
network call, so a crash mid-submit cannot lose the fact and cause a duplicate live post on
resume (FIX F11). Media is ensured ONCE PER CLIP (FIX F44). Failed submit -> PostState.failed
(retryable), never analyzed (FIX F22). Held/retired clips never reach here (crosspost skips)."""
from __future__ import annotations
from datetime import datetime, timezone
from fanops.config import Config
from fanops.errors import BlotatoAuthError
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.post import get_poster
from fanops.post.media import ensure_clip_media

def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

def _now(now: str | None) -> datetime:
    return _parse(now) if now else datetime.now(timezone.utc)

def _is_fatal_auth_error(exc: Exception) -> bool:
    """Auth/config errors mean EVERY post will fail — halt the run instead of marking one post
    failed and grinding through the rest. Matched by TYPE (BlotatoAuthError), NOT by a substring
    in the message (AUDIT H8): the old `"401" in msg or "BLOTATO_API_KEY" in msg` both UNDER-fired
    (a reworded auth error slipped past and burned the whole queue — the F52 regression) and
    OVER-fired (a 5xx whose body contained "401" wrongly halted the queue). The posters/media
    uploader raise BlotatoAuthError on a real auth failure; everything else is a per-post failure."""
    return isinstance(exc, BlotatoAuthError)

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
        try:
            # Schedule gate is INSIDE the per-post try (AUDIT M2 / review): a malformed or
            # timezone-naive scheduled_time on disk (hand-edit, corruption, older schema) makes
            # _parse raise TypeError/ValueError. Outside the try that escaped publish_due entirely
            # — a NON-auth raise that, inside advance()'s transaction, skipped the exit-save and
            # rolled back the whole pass's progress. Now it is a per-post FAILURE (mark this post
            # failed, keep going — FIX F54), never an uncaught escape. The "not due yet" skip
            # (FIX F12) stays normal flow: continue WITHOUT marking failed or saving (unchanged post).
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
            # ensure media once per clip (FIX F44 — cached on the Clip)
            if not post.media_urls:
                post.media_urls = [ensure_clip_media(led, cfg, post.parent_id)]
            # crash-safe: record intent + persist BEFORE the irreversible network call (FIX F11)
            post.state = PostState.submitting
            _save()                                        # crash-safe persist, txn-aware (AUDIT B4)
            led = poster.publish(led, post.id)
            if post.state is PostState.submitted:
                post.state = PostState.published
        except Exception as exc:
            if _is_fatal_auth_error(exc):
                raise                                      # bad key/401: halt, don't burn the queue
            # per-post failure (e.g. media upload 5xx): mark THIS post failed, keep going (FIX F54)
            post.state = PostState.failed
            post.error_reason = f"publish failed: {str(exc)[:200]}"
        _save()                                            # persist the post's terminal/failed state
    return led
