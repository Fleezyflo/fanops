# tests/test_hashtag_page.py — U11 the Hashtags observatory page (read-only after MOL-515).
# Covers: GET render-inertness (zero network), rotation consecutive-duplicate detection, the three
# cache states (empty / unreadable / ok), and the read-only corpora rows. Ban list deleted (MOL-515).
# Mirrors test_studio_app.py's client fixture; respects the _LEAKY_ENV gotcha.
import json
from datetime import datetime, timezone, timedelta

import pytest
pytest.importorskip("flask")  # the Studio web UI is an optional extra ([studio]); skip cleanly when Flask is absent

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops import personas as core
from fanops import hashtags as hashtags_mod
from fanops.hashtags import METRIC_FIELD
from fanops.studio import views_hashtags


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg)
    app.config.update(TESTING=True)
    return app.test_client()


def _z(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _cache(cfg, values, *, at=None):
    """Write the measurement cache in its real shape: {tag: {graph_id, like_count, measured_at}}."""
    at = at or datetime.now(timezone.utc).isoformat()
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        t: {"graph_id": "id-" + t.lstrip("#"), METRIC_FIELD: float(v), "measured_at": at}
        for t, v in values.items()}))


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
    for needle in ("Corpora at a glance", "Measurement cache", "Rotation health"):
        assert needle in html                            # all three sections present
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


def test_ok_cache_ranks_chips(tmp_path):
    cfg = Config(root=tmp_path)
    at = "2026-07-20T00:00:00+00:00"
    _cache(cfg, {"#low": 10, "#high": 900, "#mid": 500}, at=at)
    status = views_hashtags._store_status(cfg)
    assert status.state == "ok" and status.metric_field == METRIC_FIELD
    assert [r["tag"] for r in status.tags] == ["#high", "#mid", "#low"]   # platform metric desc
    assert all("banned" not in r for r in status.tags)
    assert status.oldest == at and status.age                                # freshness, not an allowance


# ── acceptance #5: corpus rows byte-truth, no edit controls ─────────────────────────────────────

def test_corpus_rows_read_only(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Music Blogger", niche=["hiphop"])
    _cache(cfg, {"#rap": 100, "#hiphop": 900})
    core.apply_auto_corpus(cfg, pid, tags=["#rap", "#hiphop"], meta={
        "#rap": {METRIC_FIELD: 100.0, "measured_at": "2026-07-20T00:00:00+00:00", "from": "#rap"},
        "#hiphop": {METRIC_FIELD: 900.0, "measured_at": "2026-07-21T00:00:00+00:00", "from": "#rap"}})
    rows = views_hashtags._corpora_rows(cfg)
    row = next(r for r in rows if r.pid == pid)
    assert row.size == 2                                 # byte-truth from personas.json
    assert row.top3 == ["#hiphop", "#rap"]               # ranked by the platform metric
    assert row.last_refreshed == "2026-07-21T00:00:00+00:00"   # the newest measurement stamp
    # The section-1 HTML must carry NO add/remove/research/ban controls (the corpus is DERIVED).
    html = _client(cfg).get("/hashtags").data.decode()
    section1 = html.split("<h3>Measurement cache</h3>")[0]   # everything before section 2 is section 1 + header
    assert "/personas/corpus/add" not in section1        # the add/remove corpus routes are GONE entirely
    assert "/personas/research" not in section1          # ...as is the research proposal lane
    assert "/hashtags/ban" not in html                   # ban forms gone from the whole page
    assert "edit →" in section1                          # but the read-only link to Personas is present


def test_corpus_row_size_is_byte_truth_from_personas_json(tmp_path):
    # A-11 deleted the deprecated-corpus record and its "Retired" column; `size` is the whole row story.
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", niche=["hiphop"])
    raw = json.loads(cfg.personas_path.read_text())
    for d in raw["personas"]:
        if d["id"] == pid: d["hashtag_corpus"] = ["#legacyone", "#legacytwo"]
    cfg.personas_path.write_text(json.dumps(raw))
    row = next(r for r in views_hashtags._corpora_rows(cfg) if r.pid == pid)
    assert row.size == 2
    assert not hasattr(row, "deprecated")



# --- MOL-512 (C-2): Studio transparency lead_tags are persona-scoped --------------------------

def test_persona_facts_store_is_aligned_pool_not_global_ranked(tmp_path):
    """Personas-page transparency (persona_facts) vets over `_aligned_pool`, not ranked_tags(load_measurements).
    A global-cache winner unaligned to this persona must not appear in lead_tags."""
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Hip", voice="va", niche=["hiphop"], id="pa")
    now = datetime.now(timezone.utc).isoformat()
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#hiphop": {"graph_id": "1", METRIC_FIELD: 100.0, "measured_at": now},
        "#detroitrap": {"graph_id": "2", METRIC_FIELD: 900.0, "measured_at": now,
                        "from": {"#hiphop": 1}},
        "#globalwinner": {"graph_id": "3", METRIC_FIELD: 9999.0, "measured_at": now},
    }))
    per = core.Personas.load(cfg).get(pid)
    lead = core.persona_facts(cfg, per)["lead_tags"]
    assert "#globalwinner" not in lead
    assert "#detroitrap" in lead or "#hiphop" in lead
