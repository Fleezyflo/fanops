"""Blotato MCP backend (primary). Maps a Post to FLAT blotato_create_post args.
tool_caller(name, args)->dict is injected. IN PRODUCTION the runtime wires this to the
connected Blotato MCP tool; see RUNTIME.md 'wiring the MCP poster'. No caller -> raises."""
from __future__ import annotations
from typing import Callable
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.post.payload import build_blotato_mcp_args, default_target_fields

ToolCaller = Callable[[str, dict], dict]

class BlotatoMcpPoster:
    def __init__(self, cfg: Config, tool_caller: ToolCaller | None = None):
        self.cfg = cfg
        self._call = tool_caller

    def publish(self, led: Ledger, post_id: str) -> Ledger:
        post = led.posts[post_id]
        if self._call is None:
            raise RuntimeError("BlotatoMcpPoster needs a tool_caller wired to blotato_create_post.")
        args = build_blotato_mcp_args(
            account_id=post.account_id, platform=post.platform.value, text=post.caption,
            media_urls=post.media_urls, scheduled_time=post.scheduled_time,
            extra=default_target_fields(post.platform.value) or None)
        result = self._call("blotato_create_post", args) or {}
        post.state = PostState.submitted
        post.submission_id = result.get("postSubmissionId")
        return led
