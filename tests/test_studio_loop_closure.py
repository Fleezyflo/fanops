# Structural loop closure: resolve inflight, pull metrics, bulk guards, unmapped publish.
import json
import pytest
pytest.importorskip("flask")
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops.studio import actions, views

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
_PAST = "2020-06-06T12:00:00Z"

def _accounts(cfg, handle="a", *, integrations=None):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    integ = integrations if integrations is not None else {"instagram": "ig1"}
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": handle, "account_id": "ig1", "platforms": ["instagram"], "status": "active",
         "integrations": integ}]}))

def _seed_inflight(cfg, pid="p1"):
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "c0.mp4").write_bytes(b"V")
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="c0", parent_id="m1", path=str(cdir / "c0.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id=pid, parent_id="c0", account="a", account_id="ig1", platform=Platform.instagram,
                      caption="c", state=PostState.needs_reconcile, scheduled_time=_PAST,
                      submission_id="sub-1", public_url=""))
    led.save()

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def test_resolve_post_mark_failed(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed_inflight(cfg)
    res = actions.resolve_post(cfg, "p1", "failed")
    assert res.ok and Ledger.load(cfg).posts["p1"].state is PostState.failed

def test_resolve_post_mark_published_requires_url(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed_inflight(cfg)
    assert not actions.resolve_post(cfg, "p1", "published").ok
    res = actions.resolve_post(cfg, "p1", "published", url="https://www.instagram.com/p/abc/")
    assert res.ok and Ledger.load(cfg).posts["p1"].state is PostState.published

def _seed_queued(cfg, pid="p1", state=PostState.queued):
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "c0.mp4").write_bytes(b"V")
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="c0", parent_id="m1", path=str(cdir / "c0.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id=pid, parent_id="c0", account="a", account_id="ig1", platform=Platform.instagram,
                      caption="c", state=state, scheduled_time="2099-01-01T00:00:00Z", public_url="dryrun://p1"))
    led.save()

def test_resolve_post_rejects_non_terminal_states(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    for st in (PostState.queued, PostState.awaiting_approval, PostState.submitting):
        _seed_queued(cfg, pid="p1", state=PostState.queued)
        res = actions.resolve_post(cfg, "p1", st.value)
        assert res.ok is False and "resolve only supports" in (res.error or "")
        assert Ledger.load(cfg).posts["p1"].state is PostState.queued

def test_posted_inflight_resolve_forms(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg); _seed_inflight(cfg)
    html = _client(cfg).get("/posted?delivery=inflight").data.decode()
    assert "Mark live" in html and "Mark failed" in html and "/posts/resolve/" in html

def test_pull_metrics_blocked_when_not_live(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    cfg = Config(root=tmp_path)
    res = actions.pull_metrics_studio(cfg)
    assert not res.ok and "go live" in res.error.lower()

def test_pull_metrics_empty_pollable(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    cfg = Config(root=tmp_path)
    res = actions.pull_metrics_studio(cfg)
    assert res.ok and res.detail["pollable"] == 0

def test_run_panel_metrics_not_on_run_page(tmp_path, monkeypatch):
    # U4: Pull metrics / Learn card removed from Run — metrics live on Results / daemon.
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    cfg = Config(root=tmp_path)
    html = _client(cfg).get("/run").data.decode()
    assert "Pull metrics" not in html and "04" not in html
    assert "Add footage" in html

def test_bulk_send_to_review_skips_needs_reconcile(tmp_path):
    cfg = Config(root=tmp_path)
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "c0.mp4").write_bytes(b"V")
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="c0", parent_id="m1", path=str(cdir / "c0.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="p1", parent_id="c0", account="a", account_id="ig1", platform=Platform.instagram,
                      caption="c", state=PostState.needs_reconcile, scheduled_time=_PAST, submission_id="x"))
    led.save()
    res = actions.bulk_send_to_review(cfg, ["p1"], reason="test")
    assert res.ok and res.detail["skipped"] == 1 and res.detail["moved"] == 0
    assert Ledger.load(cfg).posts["p1"].state is PostState.needs_reconcile

def test_studio_publish_guard_blocks_unmapped_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    _accounts(cfg, integrations={})
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "c0.mp4").write_bytes(b"V")
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="c0", parent_id="m1", path=str(cdir / "c0.mp4"), aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="p1", parent_id="c0", account="a", account_id="ig1", platform=Platform.instagram,
                      caption="c", state=PostState.queued, scheduled_time=_PAST))
    led.save()
    err = actions._studio_publish_guard(cfg, Ledger.load(cfg).posts["p1"])
    assert err and "not mapped" in err.lower()
    res = actions.publish_now(cfg, "p1")
    assert not res.ok and "not mapped" in res.error.lower()

def test_daemon_health_shows_ok_when_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(views, "daemon_health", lambda _cfg: type("D", (), {"verdict": "alive", "heartbeat_age_s": 12})())
    html = _client(Config(root=tmp_path)).get("/home/daemon-health").data.decode()
    assert "daemon-ok" in html and "running" in html.lower()
