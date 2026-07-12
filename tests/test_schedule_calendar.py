# tests/test_schedule_calendar.py — U7: month calendar read-model, bucket split, dialog/drag move, randomize.
import pytest
pytest.importorskip("flask")
import json
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Clip, ClipState, Source, Moment, MomentState, Fmt
from fanops.studio import actions, views

_NOW = datetime(2099, 6, 15, 12, 0, tzinfo=timezone.utc)


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def _z(dt):
    from fanops.timeutil import iso_z
    return iso_z(dt)

def _seed(cfg, *, pid="p1", state=PostState.queued, account="a", when=None, source_id="src_1"):
    when = when if when is not None else _z(_NOW + timedelta(days=3, hours=2))
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "ig_integ_1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig_integ_1"}, "daily_window": [9, 21]}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id=source_id, source_path="/v/s.mp4", language="en"))
        led.add_moment(Moment(id="mom_1", parent_id=source_id, content_token="0-7", start=0, end=7,
                              reason="r", state=MomentState.clipped))
        led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c/clip_1.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id="clip_1", account=account, account_id="ig_integ_1",
                          platform=Platform.instagram, caption="fire", state=state, scheduled_time=when,
                          public_url="dryrun://clip_1"))


# ---- 1: account_color_hue deterministic SHA1 → 0-359 ----
def test_account_color_hue_deterministic():
    from fanops.studio.views import account_color_hue
    assert account_color_hue("a") == account_color_hue("a")
    assert 0 <= account_color_hue("a") < 360
    assert account_color_hue("a") != account_color_hue("b")


# ---- 2: calendar month places color-coded chips; account filter narrows ----
def test_schedule_calendar_month_chips_and_account_filter(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, pid="p1", account="a", when=_z(datetime(2099, 6, 20, 14, 30, tzinfo=timezone.utc)))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_2", source_path="/v/b.mp4", language="en"))
        led.add_moment(Moment(id="mom_2", parent_id="src_2", content_token="0-7", start=0, end=7,
                              reason="r", state=MomentState.clipped))
        led.add_clip(Clip(id="clip_2", parent_id="mom_2", path="/c/clip_2.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p2", parent_id="clip_2", account="b", account_id="ig_integ_1",
                          platform=Platform.instagram, caption="other", state=PostState.queued,
                          scheduled_time=_z(datetime(2099, 6, 21, 10, 0, tzinfo=timezone.utc)),
                          public_url="dryrun://clip_2"))
    led = Ledger.load(cfg)
    rows = views.schedule_rows(led, cfg, now=_NOW)
    cal_all = views.schedule_calendar_month(rows, cfg, year=2099, month=6, now=_NOW)
    chips_all = [c for w in cal_all.weeks for d in w for c in d.chips if d.in_month]
    assert len(chips_all) == 2
    hues = {c.account: c.hue for c in chips_all}
    assert hues["a"] == views.account_color_hue("a")
    cal_a = views.schedule_calendar_month(rows, cfg, year=2099, month=6, account="a", now=_NOW)
    chips_a = [c for w in cal_a.weeks for d in w for c in d.chips if d.in_month]
    assert len(chips_a) == 1 and chips_a[0].account == "a"


# ---- 3: bucket split untimed/timed grouped by source ----
def test_schedule_bucket_split_by_source(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, pid="timed", when=_z(_NOW + timedelta(days=1)))
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="untimed", parent_id="clip_1", account="a", account_id="ig_integ_1",
                          platform=Platform.instagram, caption="no time", state=PostState.queued,
                          scheduled_time=None, public_url="dryrun://clip_1"))
        led.add_source(Source(id="src_2", source_path="/v/b.mp4", language="en"))
        led.add_moment(Moment(id="mom_2", parent_id="src_2", content_token="0-7", start=0, end=7,
                              reason="r", state=MomentState.clipped))
        led.add_clip(Clip(id="clip_2", parent_id="mom_2", path="/c/clip_2.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="other_src", parent_id="clip_2", account="a", account_id="ig_integ_1",
                          platform=Platform.instagram, caption="src2", state=PostState.queued,
                          scheduled_time=None, public_url="dryrun://clip_2"))
    led = Ledger.load(cfg)
    rows = views.schedule_rows(led, cfg, now=_NOW, account="a")
    bucket = views.schedule_bucket_split(led, rows)
    assert "timed" in bucket and "untimed" in bucket
    assert "src_1" in bucket["timed"] and len(bucket["timed"]["src_1"]) == 1
    assert bucket["timed"]["src_1"][0].post_id == "timed"
    assert len(bucket["untimed"]["src_1"]) == 1 and len(bucket["untimed"]["src_2"]) == 1


# ---- 4: drag move keeps operator-local HH:MM (server route) ----
def test_schedule_move_drag_keeps_local_time(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    cfg = Config(root=tmp_path)
    orig = _z(datetime(2099, 6, 20, 14, 30, tzinfo=timezone.utc))
    _seed(cfg, pid="p1", when=orig)
    new_local = "2099-06-25T14:30"   # same wall-clock time, new date
    r = _client(cfg).post("/schedule/move/p1", data={"new_time": new_local},
                          query_string={"account": "a", "month": "2099-06"})
    assert r.status_code == 200
    led = Ledger.load(cfg)
    from fanops.timeutil import parse_iso, to_local_input
    assert to_local_input(led.posts["p1"].scheduled_time).endswith("14:30")
    assert parse_iso(led.posts["p1"].scheduled_time).date().isoformat() == "2099-06-25"


# ---- 5: seeded randomize respects window, min-gap, source scope ----
def test_randomize_account_schedule_seeded_window_gap_source(tmp_path):
    from fanops.timeutil import parse_iso
    cfg = Config(root=tmp_path)
    _seed(cfg, pid="p1", when=_z(_NOW + timedelta(days=30)), source_id="src_1")
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p2", parent_id="clip_1", account="a", account_id="ig_integ_1",
                          platform=Platform.instagram, caption="two", state=PostState.queued,
                          scheduled_time=_z(_NOW + timedelta(days=30)), public_url="dryrun://clip_1"))
        led.add_source(Source(id="src_2", source_path="/v/b.mp4", language="en"))
        led.add_moment(Moment(id="mom_2", parent_id="src_2", content_token="0-7", start=0, end=7,
                              reason="r", state=MomentState.clipped))
        led.add_clip(Clip(id="clip_2", parent_id="mom_2", path="/c/clip_2.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p3", parent_id="clip_2", account="a", account_id="ig_integ_1",
                          platform=Platform.instagram, caption="src2", state=PostState.queued,
                          scheduled_time=_z(_NOW + timedelta(days=30)), public_url="dryrun://clip_2"))
    r1 = actions.randomize_account_schedule(cfg, "a", seed=42, now=_NOW)
    assert r1.ok and r1.detail["rescheduled"] == 3
    led1 = Ledger.load(cfg)
    times1 = sorted(parse_iso(led1.posts[pid].scheduled_time) for pid in ("p1", "p2", "p3"))
    assert actions.randomize_account_schedule(cfg, "a", seed=42, now=_NOW).ok
    led2 = Ledger.load(cfg)
    times2 = sorted(parse_iso(led2.posts[pid].scheduled_time) for pid in ("p1", "p2", "p3"))
    assert times1 == times2
    for t in times1:
        assert t > _NOW
        local_h = t.astimezone(timezone.utc).hour
        assert 9 <= local_h < 21
    for i in range(1, len(times1)):
        gap = (times1[i] - times1[i - 1]).total_seconds()
        assert gap >= 30 * 60
    r_src = actions.randomize_account_schedule(cfg, "a", seed=99, source_id="src_1", now=_NOW)
    assert r_src.ok and r_src.detail["rescheduled"] == 2


# ---- 6: past day / past time rejected ----
def test_reschedule_post_rejects_past_time(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, pid="p1")
    past = _z(_NOW - timedelta(hours=1))
    r = actions.reschedule_post(cfg, post_id="p1", new_time=past, now=_NOW)
    assert not r.ok and "future" in (r.error or "").lower()
    r2 = _client(cfg).post("/schedule/move/p1", data={"new_time": "2020-01-01T12:00"})
    assert r2.status_code == 200
    assert Ledger.load(cfg).posts["p1"].scheduled_time != "2020-01-01T12:00:00Z"


# ---- 7: dialog path — POST move sets future scheduled_time, stays queued ----
def test_schedule_dialog_move_route(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, pid="p1", when=None)
    future = "2099-08-08T16:45"
    r = _client(cfg).post("/schedule/move/p1", data={"new_time": future},
                          query_string={"account": "a", "month": "2099-06"})
    assert r.status_code == 200
    led = Ledger.load(cfg)
    assert led.posts["p1"].state is PostState.queued
    from fanops.timeutil import parse_iso
    assert parse_iso(led.posts["p1"].scheduled_time) > _NOW


# ---- 8: existing schedule-route smoke (calendar renders, respread still works) ----
def test_schedule_panel_renders_calendar_and_respread(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, pid="p1", when=_z(_NOW + timedelta(days=5)))
    c = _client(cfg)
    html = c.get("/schedule").data.decode()
    assert "schedule-cal" in html and "schedule-cal-chip" in html
    html_acct = c.get("/schedule", query_string={"account": "a"}).data.decode()
    assert "schedule-bucket" in html_acct
    r = c.post("/schedule/respread", query_string={"month": "2099-06"})
    assert r.status_code == 200 and b"schedule-cal" in r.data
