"""Publish error classification — transient vs fatal auth."""
from __future__ import annotations
import re
import requests
from fanops.errors import AuthError


def _is_transient_publish_error(exc: Exception) -> bool:
    """True for network/timeout/5xx blips where retrying (or parking needs_reconcile) beats terminal failed.
    Permanent 4xx/auth/validation -> False (retrying won't help). AuthError is never transient."""
    if isinstance(exc, AuthError):
        return False
    if isinstance(exc, requests.exceptions.RequestException):
        return isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.ConnectTimeout,
                                requests.exceptions.Timeout, requests.exceptions.ReadTimeout))
    if isinstance(exc, RuntimeError):
        msg = str(exc)
        lower = msg.lower()
        m = re.search(r'\((\d{3})\)', msg)
        if m:
            code = int(m.group(1))
            if code == 401:
                return False
            if 400 <= code < 500:
                return False
            if 500 <= code < 600:
                return True
        if "upstream request 401" in lower and "timed out" in lower:
            return False
        if any(x in lower for x in ("nameresolution", "name resolution", "failed to resolve",
                                    "read timed out", "max retries exceeded", "connection refused",
                                    "connection reset", "connection aborted")):
            return True
        if "timed out" in lower or "timeout" in lower:
            return True
    return False


def _is_fatal_auth_error(exc: Exception) -> bool:
    """Auth/config errors mean EVERY post will fail — halt the run instead of marking one post
    failed and grinding through the rest. Matched by the TYPE AuthError (base of PostizAuthError +
    ZernioAuthError), NOT by a substring in the message (AUDIT H8): the old `"401" in msg or
    "API_KEY" in msg` both UNDER-fired (a reworded auth error slipped past and burned the
    whole queue — the F52 regression) and OVER-fired (a 5xx whose body contained "401" wrongly
    halted). Each backend's poster/media uploader raises an AuthError subclass on a real auth
    failure; everything else is a per-post failure."""
    return isinstance(exc, AuthError)
