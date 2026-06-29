"""ISO-8601 time helpers, consolidated (audit (i)). The parse step (a 'Z' suffix that
datetime.fromisoformat won't accept pre-3.11 is rewritten to '+00:00') and the inverse
render (an aware UTC datetime serialized back with a trailing 'Z') were duplicated across
crosspost / tagging / post.run / pipeline. One home now; the callers import from here.

parse_iso is STRICT (raises on a malformed / None input) — that matches the three call
sites that fed it a known-present scheduled_time. pipeline keeps its own None/except-tolerant
wrapper around parse_iso for the heartbeat path that must never raise.

M1 — operator timezone: to_local_display / to_local_input / local_input_to_utc_z accept an
optional `cfg=` kwarg; when set, conversion uses cfg.operator_tz (IANA name from
FANOPS_OPERATOR_TZ, default 'UTC') instead of the server's silent astimezone() default. The
process system tz was the M1 root: a server in PST silently rendered every time in PST
without labelling it, so the operator's clock was wrong. Storage stays canonical UTC; this
module is the single web-boundary conversion layer."""
from __future__ import annotations
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo            # 3.9+, std-lib — IANA tz database lookup
except ImportError:                          # pragma: no cover — old Python
    ZoneInfo = None                          # type: ignore[assignment]


def _operator_zone(cfg) -> "timezone | object | None":
    """Resolve the operator timezone for the web-boundary helpers. Returns a tz suitable for
    .astimezone(): a ZoneInfo for an IANA name (e.g. 'America/New_York'), `timezone.utc` for
    'UTC', and None when cfg is unset (back-compat: caller falls through to today's silent
    system-tz behaviour). An unknown IANA name fails CLOSED to UTC — the M1 fix's point is
    never to silently misrender; a bad cfg value is operator-visible (the rendered string
    shows 'UTC' instead of their expected zone)."""
    if cfg is None:
        return None
    name = getattr(cfg, "operator_tz", "UTC") or "UTC"
    if name == "UTC" or ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc                  # unknown name -> UTC, never a silent re-localize


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


def to_local_display(ts, *, cfg=None) -> str:
    """A stored UTC ISO time -> a friendly LOCAL string 'YYYY-MM-DD HH:MM TZ' in the operator's
    configured tz (cfg.operator_tz, IANA name). cfg=None falls back to the system tz for back-compat
    with callers that haven't threaded cfg yet (the M1 hook callers — Jinja filter `localdt` — DO
    thread cfg; raw cfg=None is the deliberate test-only path). None/empty/unparseable -> '' (the
    caller falls back to a dash)."""
    dt = _aware_utc(ts)
    if dt is None: return ""
    zone = _operator_zone(cfg)
    loc = dt.astimezone(zone) if zone is not None else dt.astimezone()
    tz = loc.strftime("%Z")
    return loc.strftime("%Y-%m-%d %H:%M") + (f" {tz}" if tz else "")


def to_local_input(ts, *, cfg=None) -> str:
    """A stored UTC ISO time -> naive-LOCAL 'YYYY-MM-DDTHH:MM' for an <input type=datetime-local> value
    (minute precision, no tz suffix — the control is local by the HTML spec). Uses cfg.operator_tz
    when cfg is set; back-compat falls back to the system tz. Bad/absent -> ''."""
    dt = _aware_utc(ts)
    if dt is None: return ""
    zone = _operator_zone(cfg)
    loc = dt.astimezone(zone) if zone is not None else dt.astimezone()
    return loc.strftime("%Y-%m-%dT%H:%M")


def local_input_to_utc_z(s, *, cfg=None) -> str:
    """The inverse for the web boundary: a datetime-local form value (naive == LOCAL per the HTML spec) ->
    canonical UTC '...Z'. A value already carrying a tz (Z/offset) is NORMALIZED to UTC, never reinterpreted
    as local (so a pasted UTC time still round-trips). When cfg is set, naive input is interpreted in
    cfg.operator_tz; cfg=None falls back to the system tz. Empty/None -> ''. Unparseable -> the raw string
    UNCHANGED, so the action layer raises its own 'bad time' error (the error path stays at one home).
    KNOWN EDGE: a wall-clock time in the once-a-year DST fall-back hour is ambiguous; astimezone() resolves
    it to the standard-time side deterministically — a tolerable 1h skew for a scheduler whose localized
    result is echoed back for the operator to verify."""
    if not isinstance(s, str) or not s.strip(): return ""
    s = s.strip()
    try: dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError: return s
    if dt.tzinfo is None:
        zone = _operator_zone(cfg)
        # naive datetime-local IS local: attach operator tz when cfg is set, else system tz (back-compat).
        dt = dt.replace(tzinfo=zone) if zone is not None else dt.astimezone()
    return iso_z(dt)
