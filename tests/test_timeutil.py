"""Lock in the consolidated ISO helpers (audit (i)). These guard the exact behaviors the
callers (crosspost / tagging / post.run / pipeline) relied on before the dedup."""
from datetime import datetime, timezone, timedelta

import pytest

from fanops.timeutil import parse_iso, iso_z


def test_parse_iso_accepts_trailing_z():
    dt = parse_iso("2026-06-04T12:30:00Z")
    assert dt == datetime(2026, 6, 4, 12, 30, tzinfo=timezone.utc)


def test_parse_iso_accepts_explicit_offset():
    dt = parse_iso("2026-06-04T12:30:00+00:00")
    assert dt == datetime(2026, 6, 4, 12, 30, tzinfo=timezone.utc)


def test_parse_iso_is_strict_on_garbage():
    # The three strict call sites fed it a known-present scheduled_time and expect a raise on junk
    # (only pipeline wraps it in a None/except guard). Keep that contract.
    with pytest.raises(ValueError):
        parse_iso("not-a-timestamp")


def test_iso_z_round_trips_and_uses_z_suffix():
    dt = datetime(2026, 6, 4, 12, 30, tzinfo=timezone.utc)
    s = iso_z(dt)
    assert s == "2026-06-04T12:30:00Z"
    assert s.endswith("Z") and "+00:00" not in s
    assert parse_iso(s) == dt


def test_iso_z_normalizes_a_non_utc_offset_to_utc():
    # iso_z must convert to UTC first (the crosspost surface_time path depended on .astimezone).
    dt = datetime(2026, 6, 4, 14, 30, tzinfo=timezone(timedelta(hours=2)))
    assert iso_z(dt) == "2026-06-04T12:30:00Z"
