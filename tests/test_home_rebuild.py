# tests/test_home_rebuild.py — U3: Home accounts panel, sources gallery, week-ahead calendar.
import json
from datetime import datetime, timedelta, timezone
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Clip, Post, Platform, PostState, ClipState)
from fanops.studio import views
from fanops.studio import views_common


def _accounts(cfg, rows=None):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows or [
        {"handle": "a", "account_id": "1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig1"}, "ig_user_id": "12345"}]}))


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    return app.test_client()


def test_home_renders_three_panels_no_legacy_sections(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/footage/clip.mp4", origin_kind="native",
                              created_at="2026-06-01T10:00:00Z"))
    html = _client(cfg).get("/").data.decode()
    assert "Accounts" in html and "Sources" in html and "Week ahead" in html
    assert "ops-board" not in html and "home-start-here" not in html
    assert "home-batches" not in html and "<details" not in html


def test_home_accounts_panel_posted_total_and_badge(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="c1", parent_id="m", path="/c.mp4", state=ClipState.queued))
        led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.published, public_url="dryrun://p1"))
        led.add_post(Post(id="p2", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                          caption="y", state=PostState.analyzed, public_url="dryrun://p2"))
        led.add_post(Post(id="p3", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                          caption="z", state=PostState.awaiting_approval, public_url="dryrun://p3"))
        led.add_post(Post(id="p4", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                          caption="w", state=PostState.queued, public_url="dryrun://p4"))
    panel = views.home_accounts_panel(cfg)
    assert len(panel) == 1
    assert panel[0]["posted_total"] == 2
    assert panel[0]["awaiting"] == 1


def test_home_accounts_panel_followers_fail_open(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    cfg.account_stats_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.account_stats_path.write_text(json.dumps({"a": {"followers": 4200, "fetched_at": "2026-06-01T00:00:00Z"}}))
    assert views.home_accounts_panel(cfg)[0]["followers"] == 4200
    cfg.account_stats_path.unlink()
    assert views.home_accounts_panel(cfg)[0]["followers"] == "—"


def test_home_source_gallery_ordering_and_pagination(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        for i in range(15):
            led.add_source(Source(id=f"s{i}", source_path=f"/v{i}.mp4", origin_kind="native",
                                  created_at=f"2026-06-{i+1:02d}T00:00:00Z"))
    g1 = views.home_source_gallery(cfg, page=1, per_page=12)
    assert g1["total"] == 15 and g1["pages"] == 2 and len(g1["entries"]) == 12
    assert g1["entries"][0]["id"] == "s14"
    g2 = views.home_source_gallery(cfg, page=2, per_page=12)
    assert len(g2["entries"]) == 3
    assert g2["entries"][0]["thumb_url"] == "/thumb/source/s2"


def test_home_week_calendar_operator_tz_bucket(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_OPERATOR_TZ", "Asia/Dubai")
    cfg = Config(root=tmp_path)
    now = datetime.now(timezone.utc)
    # 23:30Z on day D -> next calendar day in Dubai (+04)
    ts = datetime(now.year, now.month, now.day, 23, 30, tzinfo=timezone.utc) + timedelta(days=1)
    st = ts.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="c1", parent_id="m", path="/c.mp4", state=ClipState.queued))
        led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.queued, scheduled_time=st, public_url="dryrun://p1"))
    cal = views.home_week_calendar(cfg)
    from fanops.timeutil import _operator_zone, parse_iso
    zone = _operator_zone(cfg)
    expected = parse_iso(st).astimezone(zone).date().isoformat()
    found = [d for d in cal["days"] if any(p["account"] == "a" for p in d["posts"])]
    assert len(found) == 1 and found[0]["date"] == expected


def test_refresh_account_stats_throttle(tmp_path, monkeypatch):
    from fanops.fanops_account_stats import refresh_account_stats_if_due
    cfg = Config(root=tmp_path); _accounts(cfg)
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok")
    monkeypatch.setenv("META_IG_USER_ID", "12345")
    calls = []
    def _fake_overview(c, handle, **kw):
        calls.append(handle)
        return {"followers": 100, "fetched_at": "2026-06-01T00:00:00Z"}
    monkeypatch.setattr("fanops.fanops_account_stats.account_overview", _fake_overview)
    assert refresh_account_stats_if_due(cfg)["refreshed"] is True
    assert len(calls) == 1
    assert refresh_account_stats_if_due(cfg, max_age_s=43200)["refreshed"] is False
    assert len(calls) == 1


def test_index_never_calls_account_overview(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); _accounts(cfg)
    calls = []
    monkeypatch.setattr("fanops.meta_graph.account_overview",
                        lambda *a, **k: calls.append(1) or {"followers": 1, "fetched_at": "Z"})
    _client(cfg).get("/")
    assert calls == []


def test_home_no_contradictory_postiz_wording(tmp_path, monkeypatch, mocker):
    views_common._postiz_health_cache.clear()
    cfg = Config(root=tmp_path); _accounts(cfg)
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("POSTIZ_URL", "http://127.0.0.1:5000")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    import fanops.health as health
    monkeypatch.setattr(health, "system_health", lambda c: [
        health.DepHealth("docker", True, "up"),
        health.DepHealth("postiz", False, "down"),
        health.DepHealth("zernio", True, "skipped")])
    class _R:
        status_code = 502
        text = "Bad Gateway"
        def json(s): return {}
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R())
    html = _client(cfg).get("/").data.decode().lower()
    strip = views.build_system_strip(cfg)
    hint = ((strip.get("postiz_down") or {}).get("hint") or "").lower()
    if "stalled" in hint:
        assert "cannot ship" not in html or "stalled" not in html
    if "parked" in hint or "idle" in hint:
        assert "cannot ship" not in html


def test_gallery_htmx_pagination(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        for i in range(14):
            led.add_source(Source(id=f"s{i}", source_path=f"/v{i}.mp4", origin_kind="native",
                                  created_at=f"2026-06-{i+1:02d}T00:00:00Z"))
    r = _client(cfg).get("/home/gallery?page=2")
    assert r.status_code == 200 and "Page 2 / 2" in r.data.decode()


def test_home_tile_badge_links_review(tmp_path):
    cfg = Config(root=tmp_path); _accounts(cfg)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="c1", parent_id="m", path="/c.mp4", state=ClipState.queued))
        led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.awaiting_approval, public_url="dryrun://p1"))
    html = _client(cfg).get("/").data.decode()
    assert 'class="home-acct-badge">1</span>' in html
    assert '/review?account=a' in html
