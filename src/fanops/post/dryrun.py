"""Dry-run poster: writes the exact payload it WOULD send (with media + target fields),
posts nothing. Active until Blotato is connected."""
from __future__ import annotations
import json
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
        (self.cfg.scheduled / f"{post_id}.json").write_text(json.dumps(payload, indent=2))
        # Stamp a synthetic submission_id so dryrun emulates the real posters (rest/mcp set this
        # from Blotato's postSubmissionId). Without it, track.py — which binds metrics rows by
        # submission_id — can never reach a dryrun post, so classify/amplify/retire never fire and
        # the learning loop is dead in the default backend (AUDIT C4). The `dryrun_` prefix mirrors
        # dryrun_media_url's honest stand-in and is collision-free vs real Blotato ids.
        post.submission_id = f"dryrun_{post_id}"
        post.state = PostState.submitted
        return led
