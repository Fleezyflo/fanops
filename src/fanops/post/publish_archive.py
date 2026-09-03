"""Day-bucketed published-post archive (fail-open)."""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone
from fanops.config import Config
from fanops.controlio import write_text_atomic
from fanops.ledger import Ledger
from fanops.models import Post
from fanops.log import get_logger


def _moment_hook(led, post: Post) -> str:
    clip = led.clips.get(post.parent_id)
    if clip is None:
        return ""
    m = led.moments.get(clip.parent_id)
    return (m.hook or "").strip() if m is not None else ""


def _archive_published(cfg: Config, post: Post) -> None:
    """Day-bucketed, human-browsable record of a just-published post -> 06_published/<YYYY-MM-DD>/<post_id>.json
    (the dir existed but nothing wrote it). FAIL-OPEN: any write/mkdir error is logged and swallowed — the
    archive is a convenience artifact, NEVER a publish blocker (a full disk must not strand a live post). Day =
    operator_local_day(published_at), else created_at, else scheduled_time, else now (MOL-735)."""
    from fanops.timeutil import operator_local_day
    try:
        day = None
        for ts in (post.published_at, post.created_at, post.scheduled_time):
            if ts:
                day = operator_local_day(ts, cfg)
                if day:
                    break
        if day is None:
            day = operator_local_day(datetime.now(timezone.utc), cfg)
        d = cfg.published / day
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(d, 0o700)             # L2 (audit): tighten a pre-existing world-listable day dir too
        except OSError:
            pass
        hook = ""
        try:
            hook = _moment_hook(Ledger.load(cfg), post)
        except Exception as exc:                          # hook lookup is best-effort enrichment — archive without it
            get_logger(cfg)("publish", post.id, "archive_hook_lookup_failed", err=str(exc)[:120])
        rec = {"post_id": post.id, "clip_id": post.parent_id, "account": post.account,
               "platform": post.platform.value, "caption": post.caption, "hashtags": list(post.hashtags or []),
               "public_url": post.public_url, "scheduled_time": post.scheduled_time,
               "created_at": post.created_at, "published_at": post.published_at,
               "render_id": post.render_id, "hook": hook,
               "media": (post.media_urls[0] if post.media_urls else None)}
        ap = d / f"{post.id}.json"
        # L2 (audit) + MOL-728: REPLACEMENT-atomic at 0600. mkstemp is born 0600, so the L2 property holds (no
        # write-then-chmod world-readable window), and os.replace means an interrupted re-archive — reconcile
        # re-fires this on an already-archived post — leaves the PRIOR record whole instead of the O_TRUNC husk
        # the final-path open produced. The swapped-in inode carries 0600 outright, so no tighten-after chmod.
        write_text_atomic(ap, json.dumps(rec, indent=2, ensure_ascii=False), mode=0o600)
    except Exception as exc:
        try:
            get_logger(cfg)("publish", post.id, "archive_error", err=str(exc)[:160])
        except Exception as log_exc:                      # even the breadcrumb write failed — fall back to stdlib, never re-raise
            logging.getLogger("fanops.post.run").warning(
                "_archive_published: breadcrumb write failed for %s (%s)", post.id, log_exc)
