"""P3 T1 — the pure, clock-injected cadence selector (metrics_schedule.py). The polling schedule is
the FIXED operator spec (4h,12h,24h,72h,1w then weekly-to-1mo then monthly-to-1yr = 20 offsets), not a
tunable knob. due_offset is latest-due-wins (NEVER backfills an earlier missed offset), never raises on a
None/naive/malformed published_at, and returns None once the latest-elapsed offset is already captured."""
from datetime import datetime, timedelta, timezone
from fanops.metrics_schedule import CADENCE_OFFSETS, offset_seconds, due_offset, is_final

_PUB = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_cadence_offsets_is_the_exact_operator_spec():
    assert CADENCE_OFFSETS == ("4h", "12h", "24h", "72h", "1w", "2w", "3w", "4w",
                               "8w", "12w", "16w", "20w", "24w", "28w", "32w", "36w",
                               "40w", "44w", "48w", "52w")
    assert len(CADENCE_OFFSETS) == 20

def test_offset_seconds_known_values():
    assert offset_seconds("4h") == 4 * 3600
    assert offset_seconds("24h") == 86400
    assert offset_seconds("72h") == 3 * 86400
    assert offset_seconds("1w") == 604800
    assert offset_seconds("52w") == 52 * 604800

def test_cadence_is_strictly_increasing_in_seconds():
    secs = [offset_seconds(o) for o in CADENCE_OFFSETS]
    assert secs == sorted(secs) and len(set(secs)) == len(secs)   # strictly increasing, no dupes

def test_due_offset_first_window():
    assert due_offset(_PUB.isoformat(), (), _PUB + timedelta(hours=5)) == "4h"

def test_due_offset_already_captured_latest_is_none():
    # at 5h the latest-elapsed offset is "4h"; if it's already captured -> None (nothing newly due).
    assert due_offset(_PUB.isoformat(), ("4h",), _PUB + timedelta(hours=5)) is None

def test_due_offset_latest_due_wins_never_backfills():
    # at 5 days the latest-elapsed offset is "72h" (1w not yet). With only "4h" captured, the result is
    # "72h" — NEVER "12h"/"24h" (no backfill of skipped middle offsets).
    assert due_offset(_PUB.isoformat(), ("4h",), _PUB + timedelta(days=5)) == "72h"

def test_due_offset_too_soon_is_none():
    assert due_offset(_PUB.isoformat(), (), _PUB + timedelta(hours=1)) is None   # <4h -> nothing due

def test_due_offset_legacy_tag_in_captured_does_not_block_a_real_offset():
    # The migration's 'legacy' tag rides in `captured` harmlessly — it is NOT a cadence offset, so it
    # can never be the `due` value and never blocks a real one. With {legacy, 4h} captured at 5 days,
    # 72h is still correctly due (review completeness: pins the spec's 'legacy is inert' claim).
    assert due_offset(_PUB.isoformat(), ("legacy", "4h"), _PUB + timedelta(days=5)) == "72h"

def test_due_offset_full_series_captured_is_none():
    far = _PUB + timedelta(weeks=60)
    assert due_offset(_PUB.isoformat(), CADENCE_OFFSETS, far) is None            # every offset captured

def test_due_offset_none_published_at_is_none():
    assert due_offset(None, (), _PUB + timedelta(days=5)) is None

def test_due_offset_naive_published_at_is_none():
    # a naive (no-tzinfo) on-disk time is never local-guessed -> None (mirrors the migration/ pipeline guard).
    assert due_offset("2026-01-01T00:00:00", (), _PUB + timedelta(days=5)) is None

def test_due_offset_malformed_published_at_is_none():
    for bad in ("", "not-a-date", "2026-13-99T99:99:99Z", 12345):
        assert due_offset(bad, (), _PUB + timedelta(days=5)) is None            # never raises

def test_is_final():
    assert is_final("52w") is True
    assert is_final("24h") is False
    assert is_final("legacy") is False
