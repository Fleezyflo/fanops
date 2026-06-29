"""Dry-run poster: writes the exact payload it WOULD send (with media + target fields),
posts nothing. Active whenever the system is not live (cfg.is_live False) — the default-safe state; the global live switch (FANOPS_LIVE / go_live), not any single backend, governs when a real poster takes over."""
from __future__ import annotations
import json
import os
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.post.payload import build_blotato_payload, default_target_fields

class DryRunPoster:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def publish(self, led: Ledger, post_id: str) -> Ledger:
        post = led.posts[post_id]
        payload = build_blotato_payload(
            account_id=post.account_id, platform=post.platform.value, text=post.caption,
            media_urls=post.media_urls, scheduled_time=post.scheduled_time,
            extra_target=default_target_fields(post.platform.value, artist_name=self.cfg.artist_name))
        self.cfg.scheduled.mkdir(parents=True, exist_ok=True)
        pp = self.cfg.scheduled / f"{post_id}.json"
        pp.write_text(json.dumps(payload, indent=2))
        try: os.chmod(pp, 0o600)            # owner-only at rest (audit): dryrun payloads carry caption/media/target
        except OSError: pass
        # Stamp a synthetic submission_id so dryrun emulates the real posters (rest/mcp set this
        # from Blotato's postSubmissionId). Without it, track.py — which binds metrics rows by
        # submission_id — can never reach a dryrun post, so classify/amplify/retire never fire and
        # the learning loop is dead in the default backend (AUDIT C4). The `dryrun_` prefix mirrors
        # dryrun_media_url's honest stand-in and is collision-free vs real Blotato ids.
        # R1/D16: is_real_submission_id now excludes the dryrun_ prefix so track/reconcile don't
        # try to poll the backend for a synthetic id.
        post.submission_id = f"dryrun_{post_id}"
        # R1/D1: stamp a synthetic permalink in the dryrun:// scheme so the next promotion step
        # (run.py _publish_one: submitted -> published) satisfies the R1 invariant: state=published
        # MUST carry a non-empty public_url. M5's _classify_channel reads the scheme to label this
        # row as 'dryrun' in the Posted tub — the operator sees an HONEST row, not a ghost. Without
        # this line a dryrun publish produced Post(state=published, public_url='') — exactly the 5
        # ghost rows on 2026-06-29 (5 sidecar JSONs at 05_scheduled/post_*.json).
        post.public_url = f"dryrun://{post_id}"
        post.state = PostState.submitted
        return led
