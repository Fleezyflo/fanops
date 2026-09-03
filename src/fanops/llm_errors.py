"""Typed errors and stderr/stdout classifiers for LLM CLI transports."""
from __future__ import annotations


class LlmTimeoutError(RuntimeError):
    """`claude -p` exceeded its time budget. Distinct from a generic RuntimeError so the responder
    can RETRY it (a timeout is usually transient) rather than treating it like a hard failure."""


class LlmRateLimitError(RuntimeError):
    """`claude -p` stayed rate-limited (api_error_status 429/503/529) across all backoff retries.
    Typed so the responder fails LOUDLY on a sustained rate limit instead of silently producing
    nothing (the asymmetry the publishers' backoff already fixed; the creative path lacked it)."""


class LlmContextLimitError(RuntimeError):
    """`claude -p` rejected the request as too large for the model context. Typed (AGENT-2) so the responder
    turns a payload-too-big failure into a VISIBLE degraded gate state instead of an infinite-pending wedge."""


class LlmSchemaError(RuntimeError):
    """`claude -p` returned output that could not be parsed/validated into the requested JSON
    schema (unparseable envelope, non-object envelope, non-JSON `result`, or no structured
    payload). Typed so the responder can stamp a VISIBLE degraded gate state on a schema failure
    instead of letting it fall through as a generic RuntimeError. Subclass of RuntimeError so
    every existing `raises(RuntimeError)` assertion (e.g. test_llm.py) stays green."""


class LlmFramesUnreadError(RuntimeError):
    """Attached frames were never opened (`num_turns<=1` after re-asks). A reason-only hook is
    not a completion — the responder leaves the gate pending so the next tick re-runs."""


class LlmToolchainError(RuntimeError):
    """`claude -p` exited nonzero with a CLI/toolchain usage error (unknown option, usage banner, etc.).
    Typed so the responder treats a broken/outdated claude install as deterministic — enrichment gates
    fail-open at the ceiling, the moments gate still terminates the source."""


_CONTEXT_LIMIT_MARKERS = ("prompt is too long", "context length", "exceeds the maximum", "too many tokens",
                          "maximum context")
_TOOLCHAIN_MARKERS = ("unknown option", "unrecognized option", "unknown argument", "unknown command", "usage:",
                      "unknown flag", "invalid option", "invalid flag")


def _is_context_limit(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in _CONTEXT_LIMIT_MARKERS)


def _is_toolchain_error(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in _TOOLCHAIN_MARKERS)
