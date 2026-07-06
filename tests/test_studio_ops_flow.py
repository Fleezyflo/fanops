# Ops flow UX: focus-default, approve→schedule, reconcile strip, publish guards, schedule cockpit.
import json
import pytest
pytest.importorskip("flask")
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops.studio import actions, views

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
_FUTURE = "2099-06-06T12:00:00Z"
_PAST = "2020-06-06T12:00:00Z"

def _accounts(cfg, handle="a"):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": handle, "account_id": "ig1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig1"}}]}))

def _seed(cfg, *, pid="p1", state=PostState.awaiting_approval, when=_FUTURE, handle="a"):
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "clip_1.mp4").write_bytes(b"V")
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="m1", path=str(cdir / "clip_1.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id=pid, parent_id="clip_1", account=handle, account_id="ig1", platform=Platform.instagram,
                      caption="c", state=state, scheduled_time=when, public_url="dryrun://p"))
    led.save()

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def test_review_defaults_to_focus_when_account_scoped(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg)
    html = _client(cfg).get("/review?account=@a").data.decode()
    assert "review-focus" in html and "<video" in html

def test_review_grid_escape_with_focus_off(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg)
    html = _client(cfg).get("/review?account=@a&focus=0&grid=1").data.decode()
    assert "account-pivot" in html and "review-focus" not in html

def test_approve_shows_schedule_outcome(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg)
    html = _client(cfg).post("/posts/approve?account=@a&view=account&focus=1", data={"ids": "p1"}).data.decode()
    assert "next clip" in html.lower() or "approved" in html.lower()
    assert "/schedule" not in html or "next clip" in html.lower()
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued

def test_publish_now_blocked_when_not_live(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); _seed(cfg, state=PostState.queued)
    res = actions.publish_now(cfg, "p1")
    assert not res.ok and "not live" in res.error.lower()

def test_publish_due_blocked_when_not_live(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    cfg = Config(root=tmp_path); _seed(cfg, state=PostState.queued, when=_PAST)
    res = actions.publish_due_bucket(cfg, confirmed=True)
    assert not res.ok and "not live" in res.error.lower()

def test_schedule_cockpit_shows_next_slot(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, state=PostState.queued, when=_FUTURE)
    html = _client(cfg).get("/schedule?account=@a").data.decode()
    assert "schedule-cockpit" in html and "Next slot" in html

def test_inflight_watch_strip_on_schedule(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, state=PostState.needs_reconcile, when=_PAST)
    html = _client(cfg).get("/schedule").data.decode()
    assert "reconcile-strip" in html and "Waiting for link" in html

def test_accept_suggested_account(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, state=PostState.queued, when=_PAST)
    before = Ledger.load(cfg).posts["p1"].scheduled_time
    res = actions.accept_suggested_account(cfg, "a", now=_NOW)
    assert res.ok and res.detail.get("rescheduled", 0) >= 1
    assert Ledger.load(cfg).posts["p1"].scheduled_time != before

def test_account_work_counts_includes_inflight(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, state=PostState.needs_reconcile, when=_PAST)
    wc = views.account_work_counts(cfg)
    assert wc["a"]["inflight"] == 1 and wc["a"]["awaiting"] == 0

def test_reconcile_strip_partial_route(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, state=PostState.needs_reconcile, when=_PAST)
    html = _client(cfg).get("/reconcile-strip?account=@a").data.decode()
    assert "reconcile-strip" in html and "Waiting for link" in html

def test_session_bar_links_inflight(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, state=PostState.needs_reconcile, when=_PAST)
    html = _client(cfg).get("/review?account=@a").data.decode()
    assert "waiting for link" in html.lower() and "delivery=inflight" in html

def test_schedule_dryrun_guard_banner(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, state=PostState.queued, when=_FUTURE)
    html = _client(cfg).get("/schedule?account=@a").data.decode()
    assert "schedule-guard" in html and "dryrun" in html.lower()

def test_publish_page_dryrun_guard_banner(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, state=PostState.queued, when=_FUTURE)
    html = _client(cfg).get("/publish").data.decode()
    assert "publish-guard" in html and "publishing is off" in html.lower()


def test_operator_error_strips_backend_codes():
    from fanops.studio.views import operator_error
    assert "postiz" not in operator_error("postiz 429 too many requests").lower()
    assert operator_error("", kind="rate_limit") == "Rate limited"

def test_accept_suggested_spreads_multiple_posts(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    for i in range(2):
        cid = f"c{i}"; (cdir / f"{cid}.mp4").write_bytes(b"V")
        led.add_clip(Clip(id=cid, parent_id="m1", path=str(cdir / f"{cid}.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id=f"p{i}", parent_id=cid, account="a", account_id="ig1", platform=Platform.instagram,
                          caption="c", state=PostState.queued, scheduled_time=_PAST, public_url="dryrun://p"))
    led.save()
    res = actions.accept_suggested_account(cfg, "a", now=_NOW)
    assert res.ok and res.detail["rescheduled"] == 2
    times = {Ledger.load(cfg).posts[f"p{i}"].scheduled_time for i in range(2)}
    assert len(times) == 2 and times.pop() != times.pop()

def test_cockpit_off_suggestion_zero_after_accept(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed(cfg, state=PostState.queued, when=_PAST)
    actions.accept_suggested_account(cfg, "a", now=_NOW)
    led = Ledger.load(cfg)
    cockpit = views.schedule_cockpit(led, cfg, "a", now=_NOW + timedelta(hours=1))
    assert cockpit.off_suggestion == 0

def test_reconcile_inflight_blocked_when_not_live(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    cfg = Config(root=tmp_path)
    res = actions.reconcile_inflight(cfg)
    assert not res.ok and "go live" in res.error.lower()

def test_account_work_counts_skips_timeless_queued(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    (cdir / "c0.mp4").write_bytes(b"V")
    led.add_clip(Clip(id="c0", parent_id="m1", path=str(cdir / "c0.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="p0", parent_id="c0", account="a", account_id="ig1", platform=Platform.instagram,
                      caption="c", state=PostState.queued, scheduled_time=None, public_url="dryrun://p"))
    led.save()
    assert views.account_work_counts(cfg).get("a", {}).get("scheduled", 0) == 0

def test_posted_failure_chip_uses_label(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    (cdir / "c0.mp4").write_bytes(b"V")
    led.add_clip(Clip(id="c0", parent_id="m1", path=str(cdir / "c0.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="p0", parent_id="c0", account="a", account_id="ig1", platform=Platform.instagram,
                      caption="c", state=PostState.failed, error_reason="postiz 429", public_url="dryrun://p"))
    led.save()
    html = _client(cfg).get("/posted?delivery=failed").data.decode()
    assert "Rate limited" in html and "rate_limit" not in html.split("posted-head")[1][:200]
