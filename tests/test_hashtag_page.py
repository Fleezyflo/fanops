# tests/test_hashtag_page.py — Hashtags page: source locks (ship menu), play-ranked cache, duplicate-line warn.
# GET is network-inert. Ban list deleted (MOL-515). Mirrors test_studio_app.py's client fixture.
import json
from datetime import datetime, timezone, timedelta

import pytest
pytest.importorskip("flask")  # the Studio web UI is an optional extra ([studio]); skip cleanly when Flask is absent

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops import personas as core
from fanops import hashtags as hashtags_mod
from fanops.hashtags import METRIC_FIELD, SIZE_FIELD, TREND_FIELD
from fanops.studio import views_hashtags


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg)
    app.config.update(TESTING=True)
    return app.test_client()


def _z(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ── ban list gone (MOL-515) ─────────────────────────────────────────────────────────────────────

def test_ban_api_and_config_paths_are_gone(tmp_path):
    for name in ("load_bans", "_strip_banned", "add_ban", "remove_ban"):
        assert not hasattr(hashtags_mod, name)
    cfg = Config(root=tmp_path)
    assert not hasattr(cfg, "hashtag_bans_path")
    assert not hasattr(cfg, "hashtag_bans_lock")


def test_ban_routes_and_studio_module_are_gone(tmp_path):
    cfg = Config(root=tmp_path)
    app = _client(cfg).application
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/hashtags/ban/add" not in rules
    assert "/hashtags/ban/remove" not in rules
    import importlib.util
    assert importlib.util.find_spec("fanops.studio.hashtags") is None


# ── acceptance #1: GET renders the three sections with ZERO network calls ───────────────────────

def test_page_get_is_network_inert(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    core.add_persona(cfg, name="Blogger", niche=["hiphop"])               # so section 1 has a row
    # Any Graph call would go through requests.get — make it a hard failure if the GET touches the network.
    import requests
    def _boom(*a, **k):
        raise AssertionError("GET /hashtags must be network-inert — no Graph call allowed")
    monkeypatch.setattr(requests, "get", _boom)
    r = _client(cfg).get("/hashtags")
    assert r.status_code == 200
    html = r.data.decode()
    for needle in ("Source locks", "Measurement cache", "Rotation health"):
        assert needle in html
    assert "Corpora at a glance" not in html
    assert "corpus leads" not in html
    assert "Lead:" not in html
    assert "Ban lane" not in html
    assert "/hashtags/ban" not in html


# ── acceptance #3: rotation flags consecutive identical tag lines; green otherwise ──────────────

def _seed_two_posts(cfg, tags_a, tags_b, *, account="a"):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/x.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
    now = datetime.now(timezone.utc)
    led.add_post(Post(id="p_old", parent_id="clip_1", account=account, account_id="1", platform=Platform.instagram,
                      caption="A", state=PostState.queued, hashtags=tags_a, created_at=_z(now - timedelta(hours=2))))
    led.add_post(Post(id="p_new", parent_id="clip_1", account=account, account_id="1", platform=Platform.instagram,
                      caption="B", state=PostState.queued, hashtags=tags_b, created_at=_z(now - timedelta(hours=1))))
    led.save()
    return led


def test_rotation_warns_on_consecutive_dupes(tmp_path):
    cfg = Config(root=tmp_path)
    # two consecutive posts on ONE account carrying the IDENTICAL full tag line -> the pre-S06 failure.
    led = _seed_two_posts(cfg, ["#rap", "#hiphop"], ["#rap", "#hiphop"])
    rows = views_hashtags.rotation_health(led)
    row = next(r for r in rows if r.account == "a")
    assert row.warn is True


def test_rotation_reordered_line_is_not_a_dupe(tmp_path):
    cfg = Config(root=tmp_path)
    # a REORDERED line is a different line (rotation IS doing its job) — normalization preserves order.
    led = _seed_two_posts(cfg, ["#rap", "#hiphop"], ["#hiphop", "#rap"])
    rows = views_hashtags.rotation_health(led)
    row = next(r for r in rows if r.account == "a")
    assert row.warn is False


def test_rotation_green_when_rotated(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed_two_posts(cfg, ["#rap", "#hiphop"], ["#trap", "#bars"])   # adjacent lines differ
    rows = views_hashtags.rotation_health(led)
    row = next(r for r in rows if r.account == "a")
    assert row.warn is False


# ── acceptance #4: the cache's three states, fail-open ──────────────────────────────────────────

def test_corrupt_cache_is_unreadable_and_the_page_still_renders(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text("NOT JSON {")
    status = views_hashtags._store_status(cfg)
    assert status.state == "unreadable"
    assert status.tags == [] and status.age is None      # no invented numbers on an unreadable file
    r = _client(cfg).get("/hashtags")
    assert r.status_code == 200


def test_absent_cache_is_empty_not_a_frozen_floor(tmp_path):
    cfg = Config(root=tmp_path)
    status = views_hashtags._store_status(cfg)
    assert status.state == "empty" and status.tags == []


def test_ok_cache_ranks_chips_by_play(tmp_path):
    cfg = Config(root=tmp_path)
    at = "2026-07-20T00:00:00+00:00"
    # media_count DESC would be #folder then #hot; play_rank_key must put #hot first.
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#folder": {"graph_id": "id-folder", SIZE_FIELD: 900.0, METRIC_FIELD: 10.0, "measured_at": at},
        "#hot": {"graph_id": "id-hot", SIZE_FIELD: 10.0, METRIC_FIELD: 900.0, "measured_at": at},
    }))
    status = views_hashtags._store_status(cfg)
    assert status.state == "ok"
    assert SIZE_FIELD in status.size_label and TREND_FIELD in status.trend_label
    assert not hasattr(status, "metric_field")
    assert [r["tag"] for r in status.tags] == ["#hot", "#folder"]
    assert all("banned" not in r for r in status.tags)
    assert status.oldest == at and status.age


def test_lock_rows_ready_missing_and_in_progress(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_ready", source_path=str(tmp_path / "a.mp4"), title="ready vid"))
    led.add_source(Source(id="src_empty", source_path=str(tmp_path / "b.mp4"), title="empty vid"))
    led.add_source(Source(id="src_wait", source_path=str(tmp_path / "c.mp4"), title="wait vid"))
    led.add_source(Source(id="src_gone", source_path=str(tmp_path / "d.mp4"), title="gone vid"))
    led.save()
    from fanops.source_tags import source_tag_locks_path
    p = source_tag_locks_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "src_ready": {"pile": ["#a"], "lock": ["#a"], "researched_at": "2026-08-19T00:00:00Z"},
        "src_empty": {"pile": ["#z"], "lock": [], "researched_at": "2026-08-19T00:00:00Z"},
        "src_wait": {"pile": ["#w"], "verified": ["#w"], "remaining": ["#w"], "lock": []},
    }))
    page = views_hashtags.hashtags_page(cfg, led=led)
    by = {r.sid: r for r in page.locks}
    assert page.lock_total == 4 and page.lock_ready == 2
    assert by["src_ready"].state == "ready" and by["src_ready"].tags == ["#a"]
    assert by["src_empty"].state == "empty" and by["src_empty"].n == 0
    assert by["src_wait"].state == "in_progress"
    assert by["src_gone"].state == "missing"
    html = _client(cfg).get("/hashtags").data.decode()
    assert "Source locks" in html and "2 of 4 sources" in html
    assert "Corpora at a glance" not in html
    assert not hasattr(page, "corpora")
    assert not hasattr(views_hashtags, "_corpora_rows")
    assert not hasattr(views_hashtags, "CorpusRow")


def test_lock_row_shows_all_twelve(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_full", source_path=str(tmp_path / "a.mp4"), title="full lock"))
    led.save()
    lock = [f"#t{i:02d}" for i in range(12)]
    from fanops.source_tags import source_tag_locks_path
    p = source_tag_locks_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "src_full": {"pile": lock, "lock": lock, "researched_at": "2026-08-19T00:00:00Z"},
    }))
    page = views_hashtags.hashtags_page(cfg, led=led)
    row = next(r for r in page.locks if r.sid == "src_full")
    assert row.tags == lock and row.n == 12
    html = _client(cfg).get("/hashtags").data.decode()
    for t in lock:
        assert t in html


def test_persona_facts_lead_tags_are_empty(tmp_path):
    """Caption hashtags are the source lock — persona_facts must not present corpus leads as the menu."""
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Hip", voice="va", niche=["hiphop"], id="pa")
    now = datetime.now(timezone.utc).isoformat()
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#hiphop": {"graph_id": "1", METRIC_FIELD: 100.0, "measured_at": now},
        "#detroitrap": {"graph_id": "2", METRIC_FIELD: 900.0, "measured_at": now,
                        "from": {"#hiphop": 2}},
        "#globalwinner": {"graph_id": "3", METRIC_FIELD: 9999.0, "measured_at": now},
    }))
    per = core.Personas.load(cfg).get(pid)
    facts = core.persona_facts(cfg, per)
    assert facts["lead_tags"] == []
    html = _client(cfg).get("/personas").data.decode()
    assert "Lead:" not in html
    assert "corpus leads" not in html
    assert "Hashtags (" not in html
