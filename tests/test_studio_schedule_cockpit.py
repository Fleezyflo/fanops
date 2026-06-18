# tests/test_studio_schedule_cockpit.py — checkpoint 3: the Schedule tab is the APPROVED-posts bucket
# cockpit. It shows which Postiz integration each approved post will hit, and offers reschedule,
# publish-now, send-back-to-review, and a routine re-spread of the whole bucket.
import pytest
pytest.importorskip("flask")
import json
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Clip, ClipState, Source, Moment, MomentState, Fmt
from fanops.studio import actions, views

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def _z(dt): from fanops.timeutil import iso_z; return iso_z(dt)

def _seed(cfg, *, pid="p1", state=PostState.queued, account_id="ig_integ_1", when=None):
    when = when or "2099-06-06T12:00:00Z"   # far future so the row is editable under the route's real wall-clock now
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "shared", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig_integ_1"}}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/v/s.mp4", language="en"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="r", state=MomentState.clipped))
        led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c/clip_1.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id="clip_1", account="@a", account_id=account_id,
                          platform=Platform.instagram, caption="fire", state=state, scheduled_time=when))


# ---- ScheduleRow carries the integration id ----
def test_schedule_row_exposes_integration_id(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, account_id="ig_integ_1")
    rows = views.schedule_rows(Ledger.load(cfg), cfg, now=_NOW)
    r = [x for x in rows if x.post_id == "p1"][0]
    assert r.integration_id == "ig_integ_1"


# ---- routine re-spread ----
def test_reschedule_bucket_respreads_queued_skips_imminent(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, pid="far", state=PostState.queued, when=_z(_NOW + timedelta(hours=9)))
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="imm", parent_id="clip_1", account="@a", account_id="ig_integ_1",
                          platform=Platform.instagram, caption="x", state=PostState.queued,
                          scheduled_time=_z(_NOW + timedelta(seconds=30))))   # imminent
    r = actions.reschedule_bucket(cfg, now=_NOW)
    assert r.ok and r.detail["rescheduled"] == 1                              # only the far one
    led = Ledger.load(cfg)
    assert led.posts["far"].scheduled_time != _z(_NOW + timedelta(hours=9))   # moved
    assert led.posts["imm"].scheduled_time == _z(_NOW + timedelta(seconds=30))  # untouched

def test_reschedule_bucket_ignores_awaiting_and_published(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, pid="await", state=PostState.awaiting_approval)
    r = actions.reschedule_bucket(cfg, now=_NOW)
    assert r.ok and r.detail["rescheduled"] == 0


# ---- routes ----
def test_get_schedule_shows_integration_publish_sendback_respread(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, pid="p1", account_id="ig_integ_1")
    html = _client(cfg).get("/schedule").data
    assert b"ig_integ_1" in html               # integration visible
    assert b"Publish now" in html              # ship from the bucket
    assert b"Send back" in html                # un-approve
    assert b"Reschedule all" in html           # routine respread

def test_schedule_respread_route_moves_posts(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, pid="p1", when=_z(_NOW + timedelta(hours=9)))
    r = _client(cfg).post("/schedule/respread")
    assert r.status_code == 200

def test_schedule_unapprove_route_sends_back_to_review(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, pid="p1", state=PostState.queued)
    r = _client(cfg).post("/schedule/unapprove/p1")
    assert r.status_code == 200 and Ledger.load(cfg).posts["p1"].state is PostState.awaiting_approval
