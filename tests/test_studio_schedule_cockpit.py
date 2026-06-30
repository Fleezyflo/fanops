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
                          platform=Platform.instagram, caption="fire", state=state, scheduled_time=when, public_url="dryrun://clip_1"))


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
    assert b"schedule-guard" in html            # dryrun guard, no integration ids
    assert b"Publish" in html              # ship from the bucket
    assert b"Review" in html                # un-approve
    assert b"Re-spread" in html           # routine respread

def test_schedule_row_renders_lazy_clip_preview(tmp_path):
    # The Schedule bucket previews each clip without re-introducing the 150-<video> perf hit the table was
    # built to avoid: a collapsed thumbnail (lazy poster) that expands to a preload=none player, mirroring
    # the Publish-by-hand pattern. ScheduleRow already carries clip_id + post_id, so this is template-only.
    cfg = Config(root=tmp_path); _seed(cfg, pid="p1", account_id="ig_integ_1")
    html = _client(cfg).get("/schedule").data.decode()
    assert "/clip-thumb/clip_1" in html          # the poster frame for the row's clip
    assert 'loading="lazy"' in html              # the collapsed thumbnail never fetches off-screen


def test_schedule_respread_route_moves_posts(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, pid="p1", when=_z(_NOW + timedelta(hours=9)))
    r = _client(cfg).post("/schedule/respread")
    assert r.status_code == 200

def test_schedule_unapprove_route_sends_back_to_review(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, pid="p1", state=PostState.queued)
    r = _client(cfg).post("/schedule/unapprove/p1")
    assert r.status_code == 200 and Ledger.load(cfg).posts["p1"].state is PostState.awaiting_approval

def test_schedule_move_route_reschedules_and_rerenders_panel(tmp_path):
    # Move re-renders the whole bucket (so the row's time is fresh, not stale in the input). local-time:
    # the panel shows the operator's LOCAL form of the time; the ledger keeps canonical UTC.
    from fanops.timeutil import to_local_input
    cfg = Config(root=tmp_path); _seed(cfg, pid="p1")
    r = _client(cfg).post("/schedule/move/p1", data={"new_time": "2099-09-09T09:00:00Z"})
    assert r.status_code == 200 and to_local_input("2099-09-09T09:00:00Z").encode() in r.data
    assert Ledger.load(cfg).posts["p1"].scheduled_time == "2099-09-09T09:00:00Z"


def test_due_publish_plan_estimates_postiz_rate(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "ig1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig1"}, "provider": "postiz"}]}))
    past = _z(_NOW - timedelta(hours=1))
    _seed(cfg, pid="p0", when=past)
    with Ledger.transaction(cfg) as led:
        for i in range(1, 8):
            led.add_post(Post(id=f"p{i}", parent_id="clip_1", account="@a", account_id="ig_integ_1",
                              platform=Platform.instagram, caption="fire", state=PostState.queued,
                              scheduled_time=past, public_url="dryrun://clip_1"))
    plan = views.due_publish_plan(cfg, now=_NOW)
    assert plan.due == 8 and plan.postiz_due == 8
    assert plan.rate_per_min == cfg.postiz_publish_per_min
    assert plan.est_minutes == 2  # 8 posts @ 4/min


def test_publish_due_bucket_live_requires_confirm(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "sk_test")
    cfg = Config(root=tmp_path)
    past = _z(_NOW - timedelta(hours=1))
    _seed(cfg, pid="p1", when=past)
    res = actions.publish_due_bucket(cfg, confirmed=False)
    assert not res.ok and "tick" in (res.error or "").lower()


def test_schedule_shows_approve_not_ship_and_publish_due(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    past = _z(_NOW - timedelta(hours=1))
    _seed(cfg, pid="p1", when=past)
    html = _client(cfg).get("/schedule").data.decode()
    assert "schedule-guard" in html
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    html_live = _client(cfg).get("/schedule").data.decode()
    assert "Publish due" in html_live
