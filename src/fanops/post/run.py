"""Publish stage. publish_due(now) submits ONLY posts whose scheduled_time <= now (FIX F12 —
v1 dumped the whole queue at once). Crash-safe: mark a post 'submitting' and SAVE before the
network call, so a crash mid-submit cannot lose the fact and cause a duplicate live post on
resume (FIX F11). Media is ensured ONCE PER CLIP (FIX F44). Failed submit -> PostState.failed
(retryable), never analyzed (FIX F22). Held/retired clips never reach here (crosspost skips)."""
from __future__ import annotations
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.post import get_poster
from fanops.post.media import ensure_clip_media

def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

def _now(now: str | None) -> datetime:
    return _parse(now) if now else datetime.now(timezone.utc)

def publish_due(led: Ledger, cfg: Config, *, now: str | None = None) -> Ledger:
    poster = get_poster(cfg)
    cutoff = _now(now)
    for post in led.posts_in_state(PostState.queued):
        if post.scheduled_time and _parse(post.scheduled_time) > cutoff:
            continue                                       # not due yet (FIX F12)
        # ensure media once per clip
        if not post.media_urls:
            post.media_urls = [ensure_clip_media(led, cfg, post.parent_id)]
        # crash-safe: record intent + persist BEFORE the irreversible network call (FIX F11)
        post.state = PostState.submitting
        led.save()
        led = poster.publish(led, post.id)
        if post.state is PostState.submitted:
            post.state = PostState.published
        elif post.state is PostState.failed:
            led.save()                                     # keep the failure durable
        led.save()
    return led
