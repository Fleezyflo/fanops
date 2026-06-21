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


# --- local-time RENDERING (storage stays canonical UTC; these localize at the Studio's web boundary) ---
def _aware_utc(ts) -> "datetime | None":
    """Parse a stored timestamp into an aware UTC datetime, or None on absent/garbage. A stored NAIVE time
    is canonical-UTC by storage convention (mirrors actions._normalize_z), so it's stamped UTC, never guessed."""
    if not isinstance(ts, str) or not ts.strip(): return None
    try: dt = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except ValueError: return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def to_local_display(ts) -> str:
    """A stored UTC ISO time -> a friendly LOCAL string 'YYYY-MM-DD HH:MM TZ' in the operator's system tz
    (datetime.astimezone() with no arg). None/empty/unparseable -> '' (the caller falls back to a dash)."""
    dt = _aware_utc(ts)
    if dt is None: return ""
    loc = dt.astimezone(); tz = loc.strftime("%Z")
    return loc.strftime("%Y-%m-%d %H:%M") + (f" {tz}" if tz else "")


def to_local_input(ts) -> str:
    """A stored UTC ISO time -> naive-LOCAL 'YYYY-MM-DDTHH:MM' for an <input type=datetime-local> value
    (minute precision, no tz suffix — the control is local by the HTML spec). Bad/absent -> ''."""
    dt = _aware_utc(ts)
    return dt.astimezone().strftime("%Y-%m-%dT%H:%M") if dt is not None else ""


def local_input_to_utc_z(s) -> str:
    """The inverse for the web boundary: a datetime-local form value (naive == LOCAL per the HTML spec) ->
    canonical UTC '...Z'. A value already carrying a tz (Z/offset) is NORMALIZED to UTC, never reinterpreted
    as local (so a pasted UTC time still round-trips). Empty/None -> ''. Unparseable -> the raw string
    UNCHANGED, so the action layer raises its own 'bad time' error (the error path stays at one home)."""
    if not isinstance(s, str) or not s.strip(): return ""
    s = s.strip()
    try: dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError: return s
    if dt.tzinfo is None: dt = dt.astimezone()     # naive datetime-local IS local -> attach the system offset
    return iso_z(dt)
