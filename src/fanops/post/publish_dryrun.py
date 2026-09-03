"""Dryrun boundary — preview sidecar without distribution."""
from __future__ import annotations
from fanops.config import Config
from fanops.models import Post
from fanops.log import get_logger


def _handle_dryrun_boundary(cfg: Config, post: Post, *, post_id: str | None = None) -> None:
    """dryrun-boundary (M2): NOT live -> no backend to distribute to. Write the would-send preview
    sidecar (fail-open on write errors) and log dryrun_not_distributed. Post stays `queued` — never
    claimed, never a phantom-published row."""
    from fanops.post.dryrun import write_preview
    pid = post_id or post.id
    try:
        write_preview(cfg, post)
    except Exception as exc:
        get_logger(cfg)("publish", pid, "preview_write_failed", err=str(exc)[:120])
    get_logger(cfg)("publish", pid, "dryrun_not_distributed",
                    account=post.account, platform=post.platform.value)
