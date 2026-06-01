# src/fanops/errors.py
"""Typed errors the CLI can catch to print one clean line instead of a traceback."""
from __future__ import annotations


class ControlFileError(Exception):
    """A control file under 00_control/ (ledger.json, accounts.json) is unreadable —
    malformed JSON or schema-violating content. Message is operator-facing and one-line:
    e.g. 'ledger.json invalid: Expecting property name enclosed in double quotes'."""


class LockBusyError(Exception):
    """The ledger lock is held by another LIVE fanops process (overlapping cron) and did not
    free within the timeout. Operator-facing, one-line. Distinct from a *stale* lock, which the
    flock-based lock self-heals automatically (the kernel releases an flock on process death),
    so this only ever means genuine contention — never an orphan needing manual `rm`."""


class BlotatoAuthError(Exception):
    """A Blotato authentication/credential failure (bad or missing BLOTATO_API_KEY, HTTP 401).
    Raised by the posters/media uploader so the publish loop can decide to HALT THE WHOLE QUEUE
    by exception TYPE — every post will fail on a bad key, so grinding through them is pointless
    (FIX F52). Type-matched, NOT substring-matched (AUDIT H8): a string match on "401" both
    under-fired (a reworded auth error slipped through and burned the queue) and over-fired (a
    5xx body merely containing "401" wrongly halted). If you wire a custom MCP tool_caller,
    raise THIS on an auth failure so the halt logic recognizes it."""


def reason(exc: Exception) -> str:
    """Condense a parse/validation error into one operator-readable line.
    json.JSONDecodeError already stringifies tidily; pydantic's ValidationError is
    multi-line and noisy, so we summarize it as 'N validation error(s): <first loc> — <first msg>'."""
    from pydantic import ValidationError
    if isinstance(exc, ValidationError):
        errs = exc.errors()
        head = errs[0] if errs else {}
        loc = ".".join(str(x) for x in head.get("loc", ())) or "?"
        return f"{len(errs)} validation error(s): {loc} — {head.get('msg', exc)}"
    return str(exc)
