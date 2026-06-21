"""local-time rendering helpers (timeutil): UTC stays the canonical STORAGE form; these three render it
in the operator's system timezone for the Studio. Tests pin TZ (env-leak-guarded) for the absolute-format
assertions and use the UTC<->local-input round-trip (tz-INDEPENDENT) for the conversion invariant."""
from __future__ import annotations
import time, os
from datetime import datetime, timezone
import pytest
from zoneinfo import ZoneInfo
from fanops.timeutil import to_local_display, to_local_input, local_input_to_utc_z

_ZONE = "Etc/GMT-2"          # Olson Etc zones invert sign: Etc/GMT-2 == UTC+02:00, no DST -> deterministic


@pytest.fixture
def pinned_tz():
    """Pin the SYSTEM tz (the helpers call datetime.astimezone() with no arg) to _ZONE, then restore the
    prior TZ exactly — a delenv on an originally-absent key would leak _ZONE into later tests (env-leak guard)."""
    had = "TZ" in os.environ; prev = os.environ.get("TZ")
    os.environ["TZ"] = _ZONE; time.tzset()
    try:
        yield ZoneInfo(_ZONE)
    finally:
        if had: os.environ["TZ"] = prev
        else: os.environ.pop("TZ", None)
        time.tzset()


def test_display_renders_local_clock(pinned_tz):
    z = "2026-06-08T14:00:00Z"                                  # 14:00 UTC -> 16:00 in UTC+2
    expect = datetime(2026, 6, 8, 14, tzinfo=timezone.utc).astimezone(pinned_tz)
    got = to_local_display(z)
    assert got.startswith(expect.strftime("%Y-%m-%d %H:%M"))    # local date+time prefix (tz suffix appended)
    assert "16:00" in got and not got.endswith("Z")

def test_input_renders_naive_local_iso(pinned_tz):
    z = "2026-06-08T14:00:00Z"
    expect = datetime(2026, 6, 8, 14, tzinfo=timezone.utc).astimezone(pinned_tz).strftime("%Y-%m-%dT%H:%M")
    assert to_local_input(z) == expect                          # datetime-local form: minute, no tz suffix
    assert to_local_input(z) == "2026-06-08T16:00"

@pytest.mark.parametrize("bad", [None, "", "   ", "not-a-time", 123, "2026-13-99"])
def test_display_and_input_empty_on_bad(bad):
    assert to_local_display(bad) == ""                          # caller falls back to a dash
    assert to_local_input(bad) == ""

def test_stored_naive_treated_as_utc_for_display(pinned_tz):
    # a stored time WITHOUT a tz is canonical-UTC by storage convention -> display still localizes from UTC
    assert to_local_input("2026-06-08T14:00:00") == "2026-06-08T16:00"

def test_local_input_to_utc_passes_through_tz_aware():
    # a value that already carries a tz (Z or offset) is NORMALIZED to UTC, never reinterpreted as local
    assert local_input_to_utc_z("2026-06-08T14:00:00Z") == "2026-06-08T14:00:00Z"
    assert local_input_to_utc_z("2026-06-08T16:00:00+02:00") == "2026-06-08T14:00:00Z"

def test_local_input_to_utc_interprets_naive_as_local(pinned_tz):
    # the datetime-local control submits naive LOCAL -> 16:00 local in UTC+2 is 14:00Z
    assert local_input_to_utc_z("2026-06-08T16:00") == "2026-06-08T14:00:00Z"

@pytest.mark.parametrize("z", ["2026-06-08T14:00:00Z", "2099-01-01T00:00:00Z", "2026-12-31T23:59:00Z"])
def test_utc_local_input_roundtrip_is_tz_independent(z):
    # UTC -> local-input -> UTC returns the original, in WHATEVER system tz the test runs (no fixture)
    assert local_input_to_utc_z(to_local_input(z)) == z

def test_local_input_to_utc_empty_and_garbage():
    assert local_input_to_utc_z(None) == "" and local_input_to_utc_z("") == "" and local_input_to_utc_z("  ") == ""
    assert local_input_to_utc_z("not-a-time") == "not-a-time"   # raw passthrough -> action layer raises its 'bad time'
