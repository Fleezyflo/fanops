# tests/test_studio_views.py — CREATE
from datetime import datetime, timezone, timedelta
from fanops.studio.views import (
    _imminent, IMMINENT_THRESHOLD_MINUTES,
    SurfacePost, ReviewCard, ScheduleRow, LiftRow, LiftView,
)

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)

def _z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def test_imminent_none_is_true():
    assert _imminent(None, NOW) is True

def test_imminent_unparseable_is_true():
    assert _imminent("garbage", NOW) is True

def test_imminent_naive_is_true():
    # naive time can't be safely compared / would fail publish_due -> treat as non-editable
    assert _imminent("2026-06-06T13:00:00", NOW) is True

def test_imminent_past_is_true():
    assert _imminent(_z(NOW - timedelta(minutes=1)), NOW) is True

def test_imminent_within_threshold_is_true():
    assert _imminent(_z(NOW + timedelta(minutes=IMMINENT_THRESHOLD_MINUTES - 1)), NOW) is True

def test_not_imminent_when_far_future():
    assert _imminent(_z(NOW + timedelta(hours=2)), NOW) is False

def test_dataclasses_construct():
    sp = SurfacePost(post_id="p1", account="@a", platform="instagram", persona="hype",
                     caption="x", hashtags=["#a"], scheduled_time=_z(NOW), media_url="/media/p1",
                     state="queued", imminent=False, editable=True)
    assert sp.editable is True and sp.media_url == "/media/p1"
    card = ReviewCard(clip_id="c1", preview_url="/clips/c1", source_name="s.mp4",
                      moment_window="0–7", reason="r", language="en", subtitles_burned=True,
                      held=False, held_reason=None, transcript_excerpt="hi", surfaces=[sp],
                      bucket="editable")
    assert card.bucket == "editable" and card.surfaces[0] is sp
    LiftView(variant_rows=[], variant_empty_reason="none", amplify_present=False,
             amplify_rows=[], amplify_empty_reason=None)
    ScheduleRow(post_id="p1", scheduled_time=_z(NOW), account="@a", platform="instagram",
                clip_id="c1", state="queued", imminent=False, editable=True)
    LiftRow(variant_hook="WATCH", account="@a", platform="instagram", lift_score=42.0,
            loop_state="learning ACTIVE")
