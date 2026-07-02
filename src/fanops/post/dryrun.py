"""Dry-run PREVIEW writer (dryrun-boundary M2): writes the exact payload a real poster WOULD send
(media + target fields), and touches NOTHING else — no state, no submission_id, no public_url. A dry
run does not distribute, so it fabricates no distribution artifacts. Active whenever the system is not
live (cfg.is_live False) — the default-safe state; the global live switch (FANOPS_LIVE / go_live), not
any single backend, governs when a real poster takes over.

The preview is written at the publish_due boundary (post/run.py), the sole place a dryrun post is now
processed — post-M1 a dryrun post halts `queued` and never reaches a real poster's publish() path."""
from __future__ import annotations
import json
import os
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.log import get_logger

def write_preview(cfg: Config, post) -> None:
    """Write the would-send sidecar `<scheduled>/<post_id>.json` (0o600). Backend-neutral — a flat
    record of what a real poster WOULD send; the only consumer is the sidecar EXISTENCE check
    (dryrun-origin marker), never the internal shape, so a neutral summary is honest + sufficient."""
    payload = {"account": post.account, "account_id": post.account_id,
               "platform": post.platform.value, "text": post.caption,
               "media_urls": post.media_urls, "scheduled_time": post.scheduled_time}
    cfg.scheduled.mkdir(parents=True, exist_ok=True)
    pp = cfg.scheduled / f"{post.id}.json"
    pp.write_text(json.dumps(payload, indent=2))
    try:
        os.chmod(pp, 0o600)                # owner-only at rest (audit): dryrun payloads carry caption/media/target
    except OSError as exc:                 # chmod best-effort (e.g. a filesystem that ignores mode); the preview is
        get_logger(cfg)("publish", post.id, "preview_chmod_failed", err=str(exc)[:120])   # written — trace, don't swallow


class DryRunPoster:
    """Kept as the `not-live` provider handle for get_poster; its publish() now ONLY writes the preview
    (no distribution artifacts). The publish_due boundary calls write_preview directly, so this path is
    exercised only by a caller that still routes a dryrun post through a Poster contract."""
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def publish(self, led: Ledger, post_id: str) -> Ledger:
        write_preview(self.cfg, led.posts[post_id])    # preview only — no state/id/url (M2 boundary contract)
        return led
