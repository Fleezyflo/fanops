"""ISO-8601 time helpers, consolidated (audit (i)). The parse step (a 'Z' suffix that
datetime.fromisoformat won't accept pre-3.11 is rewritten to '+00:00') and the inverse
render (an aware UTC datetime serialized back with a trailing 'Z') were duplicated across
crosspost / tagging / post.run / pipeline. One home now; the callers import from here.

parse_iso is STRICT (raises on a malformed / None input) — that matches the three call
sites that fed it a known-present scheduled_time. pipeline keeps its own None/except-tolerant
wrapper around parse_iso for the heartbeat path that must never raise."""
from __future__ import annotations
from datetime import datetime, timezone


def parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp (with an optional trailing 'Z') into a datetime."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def iso_z(dt: datetime) -> str:
    """Serialize an aware datetime as UTC ISO-8601 with a trailing 'Z' (the inverse of
    parse_iso). Normalizes to UTC first, then swaps the '+00:00' offset for 'Z'."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
