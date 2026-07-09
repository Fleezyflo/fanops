"""MOL-357: Prometheus-style GET /metrics on the Studio app."""
import json
import pytest

pytest.importorskip("flask")

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, ClipState, Fmt, Moment, MomentState, Platform, Post, PostState, Source


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg)
    app.config.update(TESTING=True)
    return app.test_client()


def _seed_posts(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "persona": "hype"}]}))
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/x.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
    led.add_post(Post(id="p_review", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="R", state=PostState.awaiting_approval))
    led.add_post(Post(id="p_queued", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="Q", state=PostState.queued))
    led.add_post(Post(id="p_shipped", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="P", state=PostState.published,
                      public_url="https://example.com/p"))
    led.save()


def test_metrics_returns_prometheus_text_with_named_gauges(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    _seed_posts(cfg)
    mocker.patch("fanops.health_model.build_health_report", return_value=type("R", (), {
        "deps": [type("D", (), {"name": "docker", "ok": True, "detail": "up"})()],
        "checks": [], "notes": [], "field_shape": None,
    })())
    mocker.patch("fanops.health_model.heartbeat_stale", return_value=(12.5, False, 600))
    r = _client(cfg).get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in (r.content_type or "")
    body = r.data.decode()
    assert 'fanops_posts{state="awaiting_approval"} 1' in body
    assert 'fanops_posts{state="queued"} 1' in body
    assert 'fanops_posts{state="published"} 1' in body
    assert "fanops_awaiting_moments 1" in body
    assert "fanops_daemon_heartbeat_age_seconds 12.5" in body
    assert 'fanops_dep_up{dep="docker"} 1' in body
    assert "fanops_metrics_degraded 0" in body


def test_metrics_ledger_read_error_still_200_degraded(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    mocker.patch("fanops.ledger.Ledger.load", side_effect=OSError("torn ledger"))
    mocker.patch("fanops.health_model.build_health_report", return_value=type("R", (), {
        "deps": [type("D", (), {"name": "postiz", "ok": False, "detail": "down"})()],
        "checks": [], "notes": [], "field_shape": None,
    })())
    mocker.patch("fanops.health_model.heartbeat_stale", return_value=(None, True, 600))
    r = _client(cfg).get("/metrics")
    assert r.status_code == 200
    body = r.data.decode()
    assert "fanops_metrics_degraded 1" in body
    assert 'fanops_dep_up{dep="postiz"} 0' in body
