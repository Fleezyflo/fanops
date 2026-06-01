"""Blotato MCP backend (primary). Maps a Post to FLAT blotato_create_post args.
tool_caller(name, args)->dict is injected. IN PRODUCTION the runtime wires this to the
connected Blotato MCP tool; see RUNTIME.md 'wiring the MCP poster'. No caller -> raises."""
from __future__ import annotations
from typing import Callable
from fanops.config import Config
from fanops.errors import BlotatoAuthError
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.post.blotato_rest import _extract_submission_id
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
        try:
            result = self._call("blotato_create_post", args) or {}
        except BlotatoAuthError:
            # AUDIT B3 (adversarial re-confirm): a caller that ALREADY raised the typed
            # BlotatoAuthError is the authoritative auth signal — re-raise it UNCHANGED so run.py
            # halts by TYPE (F52/H8), regardless of its message. The broad `except Exception` below
            # would otherwise SWALLOW this subclass and re-park it as needs_reconcile, silently
            # defeating the halt-the-queue guarantee for any auth message the substring net misses
            # (e.g. "credentials rejected", or "BLOTATO_API_KEY missing" — note 'api key' never
            # matches the underscore form). The production MCP wiring is documented to raise this.
            raise
        except Exception as exc:
            msg = str(exc).lower()
            # AUDIT B3: an UNTYPED auth failure (a raw transport error) is best-effort matched by
            # substring and mapped to the typed error so run.py halts by type — a bad key must never
            # silently burn posts. A caller SHOULD raise BlotatoAuthError directly (handled above);
            # this substring net is the fallback for transports that don't type their auth error.
            if ("401" in msg or "403" in msg or "unauthorized" in msg or "forbidden" in msg
                    or "invalid token" in msg or "api key" in msg):
                raise BlotatoAuthError(f"Blotato MCP auth failure: {str(exc)[:200]}") from exc
            # PRIME DIRECTIVE: a NON-auth error is AMBIGUOUS (the tool MAY have posted, like a REST
            # 5xx) -> PARK as needs_reconcile and RETURN. Never raise (aborts the run) and never
            # mark failed (re-queueable => double-post to a real fan account).
            post.state = PostState.needs_reconcile
            post.error_reason = f"MCP publish error (may be live): {str(exc)[:200]}"
            return led
        sid = _extract_submission_id(result)   # AUDIT B3: reuse D2's helper — no divergent copy
        if not sid:
            # AUDIT B3: an MCP 2xx with no recognizable id is MAY-BE-LIVE -> park, NEVER failed.
            post.state = PostState.needs_reconcile
            post.error_reason = f"MCP 2xx but no recognizable submission id: {str(result)[:200]}"
            return led
        post.state = PostState.submitted
        post.submission_id = sid
        return led
