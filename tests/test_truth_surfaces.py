# tests/test_truth_surfaces.py — S10: Studio truth-surface reconciliation (Postiz parked, Run idle label, Review picker progress).
import fcntl
import json
import os
import pytest
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, SourceState, Moment, MomentState, Clip, ClipState,
                            Post, PostState, Platform, Fmt)
from fanops.studio import views
from fanops.studio import views_common

_ENV_KEYS = ("FANOPS_LIVE", "FANOPS_POSTER", "POSTIZ_URL", "POSTIZ_API_KEY", "ZERNIO_API_KEY",
             "FANOPS_POSTIZ_AUTOSTART")
_ENV_BASELINE = {k: os.environ.get(k) for k in _ENV_KEYS}
NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)


def _z(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture(autouse=True)
def _restore_env():
    views_common._postiz_health_cache.clear()
    yield
    views_common._postiz_health_cache.clear()
    for k, v in _ENV_BASELINE.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _clean(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    return Config(root=tmp_path)


def _seed_accounts(cfg, handles=("@ig",)):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h.lstrip("@"), "account_id": "1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig_1"}, "backends": {"instagram": "postiz"}}
        for h in handles]}))


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg)
    app.config.update(TESTING=True)
    return app.test_client()


def _seed_due_postiz_post(cfg, *, when="2020-01-01T12:00:00Z"):
    with Ledger.transaction(cfg) as led:
        if not led.sources:
            led.add_source(Source(id="src_1", source_path="/v/s.mp4", language="en"))
            led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                                  reason="r", state=MomentState.clipped))
            led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c/clip_1.mp4", aspect=Fmt.r9x16,
                              state=ClipState.queued))
        led.add_post(Post(id="due_p1", parent_id="clip_1", account="ig", account_id="1",
                          platform=Platform.instagram, caption="fire", state=PostState.queued,
                          scheduled_time=when, public_url="dryrun://clip_1"))


def _mock_postiz_down_health(monkeypatch):
    import fanops.health as health
    monkeypatch.setattr(health, "system_health", lambda c: [
        health.DepHealth("docker", True, "daemon up"),
        health.DepHealth("postiz", False, "unreachable"),
        health.DepHealth("zernio", True, "skipped (not configured)")])


class _R:
    def __init__(s, code, body=None, text=""):
        s.status_code = code
        s._b = body if body is not None else {}
        s.text = text
    def json(s):
        return s._b


def test_golive_postiz_parked_matches_strip_no_blocker(tmp_path, monkeypatch, mocker):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("POSTIZ_URL", "http://127.0.0.1:5000")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed_accounts(cfg)
    _mock_postiz_down_health(monkeypatch)
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(502, text="Bad Gateway"))
    body = _client(cfg).get("/golive/health").data.decode()
    assert "dep-alert" not in body or "cannot ship" not in body.lower()
    assert "starts on publish" in body.lower()
    assert "parked" in body.lower() or "idle" in body.lower()


def test_golive_postiz_stall_still_blocks(tmp_path, monkeypatch, mocker):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("POSTIZ_URL", "http://127.0.0.1:5000")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed_accounts(cfg)
    _seed_due_postiz_post(cfg)
    _mock_postiz_down_health(monkeypatch)
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(502, text="Bad Gateway"))
    body = _client(cfg).get("/golive/health").data.decode()
    assert "dep-alert" in body
    assert "cannot ship" in body.lower()
    strip = views.build_system_strip(cfg)
    pd = strip.get("postiz_down") or {}
    assert pd.get("danger") is True
    assert "stalled" in (pd.get("hint") or "").lower()


def test_postiz_header_honest_when_autostart_off(tmp_path, monkeypatch, mocker):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed_accounts(cfg)
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(502, text="Bad Gateway"))
    strip = views.build_system_strip(cfg)
    pd = strip.get("postiz_down") or {}
    hint = (pd.get("hint") or "").lower()
    assert pd.get("show") is True
    assert "starts on publish" not in hint


def test_run_idle_shows_queued_not_in_progress(tmp_path):
    import re
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/v/show.mp4", state=SourceState.catalogued))
    html = _client(cfg).get("/run").data.decode()
    assert re.search(r'<strong>1</strong> queued', html, re.I)
    assert not re.search(r'<strong>\d+</strong> in progress', html, re.I)


def test_run_active_shows_in_progress(tmp_path):
    import os
    from fanops.pipeline_run import note_stage, _lock_path
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src-1", source_path="/v/show.mp4", state=SourceState.catalogued))
    lp = _lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        note_stage(cfg, "produce", "src-1")
        html = _client(cfg).get("/run").data.decode()
        assert "in progress" in html.lower()
        assert "produce:src-1" in html or "run_chip" in html or "produce" in html
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _seed_two_account_picker(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        {"handle": "b", "account_id": "2", "platforms": ["instagram"], "status": "active"}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "c.mp4"
    base.write_bytes(b"\x00\x00\x00\x18ftypmp42CLIP")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/v/show.mp4", language="en"))
        for acct in ("a", "b"):
            tag = acct
            led.add_moment(Moment(id=f"mom_{tag}", parent_id="src_1", content_token="0-7", start=0, end=7,
                                  reason="r", state=MomentState.clipped))
            led.add_clip(Clip(id=f"clip_{tag}", parent_id=f"mom_{tag}", path=str(base), aspect=Fmt.r9x16,
                              state=ClipState.queued))
            led.add_post(Post(id=f"aw_{tag}", parent_id=f"clip_{tag}", account=acct, account_id="1",
                              platform=Platform.instagram, caption=f"await {acct}",
                              state=PostState.awaiting_approval, scheduled_time=_z(NOW + timedelta(hours=3))))


def test_review_picker_progress_shows_real_totals(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_two_account_picker(cfg)
    html = _client(cfg).get("/review").data.decode()
    assert "review-account-picker" in html or "Which account" in html
    assert "0 awaiting" not in html
    assert "2 awaiting" in html or "awaiting ·" in html
