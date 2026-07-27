# tests/test_hashtag_page.py — U11 the Hashtags observatory page + the global ban mechanism.
# Covers: GET render-inertness (zero network), ban filters vet_hashtags selection + the derived-corpus
# write (a ban outranks a derivation), rotation consecutive-duplicate detection, the three cache states
# (empty / unreadable / ok), and the read-only corpora rows. Mirrors test_studio_app.py's client fixture;
# respects the _LEAKY_ENV gotcha (no new env is introduced — the ban list is a control FILE).
import json
from datetime import datetime, timezone, timedelta

import pytest
pytest.importorskip("flask")  # the Studio web UI is an optional extra ([studio]); skip cleanly when Flask is absent

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops import personas as core
from fanops.hashtags import METRIC_FIELD, vet_hashtags, load_bans, add_ban, remove_ban, ranked_tags, load_measurements
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


# ── ban control-file mechanism ──────────────────────────────────────────────────────────────────

def test_load_bans_missing_and_corrupt_never_raise(tmp_path):
    cfg = Config(root=tmp_path)
    assert load_bans(cfg) == set()                       # absent -> empty
    cfg.hashtag_bans_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtag_bans_path.write_text("NOT JSON {")
    assert load_bans(cfg) == set()                       # corrupt -> empty, no raise
    cfg.hashtag_bans_path.write_text(json.dumps({"bans": [1, "#Ok", None]}))
    assert load_bans(cfg) == {"#ok"}                     # non-str dropped, normalized


def test_add_remove_ban_roundtrip_normalized(tmp_path):
    cfg = Config(root=tmp_path)
    add_ban(cfg, "OK")                                   # no '#', mixed case -> normalized to #ok
    add_ban(cfg, "#ok")                                  # dupe -> still one
    add_ban(cfg, "  #Two  ")
    assert load_bans(cfg) == {"#ok", "#two"}
    remove_ban(cfg, "ok")
    assert load_bans(cfg) == {"#two"}
    remove_ban(cfg, "#nope")                             # absent -> clean no-op
    assert load_bans(cfg) == {"#two"}


# ── acceptance #1: GET renders the four sections with ZERO network calls ────────────────────────

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
    for needle in ("Corpora at a glance", "Measurement cache", "Rotation health", "Ban lane"):
        assert needle in html                            # all four sections present


# ── acceptance #2: a banned tag never selects, and a ban outranks a derivation ──────────────────

def test_ban_filters_vet_hashtags(tmp_path):
    cfg = Config(root=tmp_path)
    # the measured menu carries the banned tag AND good ones; the corpus also holds the banned tag.
    store = ["#banned", "#rap", "#hiphop"]
    # Without cfg -> byte-identical, banned tag flows through the store/corpus.
    out_no_cfg = vet_hashtags(["#banned"], Platform.instagram, store=store, corpus=["#banned"])
    assert "#banned" in out_no_cfg
    # With the ban in place + cfg passed, the banned tag is stripped from EVERY path (model pick, cache, corpus).
    add_ban(cfg, "#banned")
    out = vet_hashtags(["#banned"], Platform.instagram, store=store, corpus=["#banned"], cfg=cfg)
    assert "#banned" not in out
    assert out                                           # never empty — good tags backfill the freed slot


def test_ban_beats_a_derived_corpus_write(tmp_path):
    # apply_auto_corpus is the ONLY corpus writer now (derivation replaces wholesale); a banned tag must
    # not land even when the derivation chose it — the operator veto outranks the platform measurement.
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", niche=["hiphop"])
    add_ban(cfg, "#banned")
    core.apply_auto_corpus(cfg, pid, tags=["#banned", "#keep"], meta={})
    corpus = core.Personas.load(cfg).get(pid).hashtag_corpus
    assert corpus == ["#keep"]                           # the ban filtered it at the write boundary


def test_ban_survives_a_cache_refresh(tmp_path):
    # A banned tag present in the cache still never selects — the cache FILE is not rewritten by the ban,
    # so re-reading it and re-vetting with cfg keeps excluding the tag (survives a measurement refresh).
    cfg = Config(root=tmp_path)
    _cache(cfg, {"#banned": 9900, "#rap": 300})
    add_ban(cfg, "#banned")
    store = ranked_tags(load_measurements(cfg))
    assert "#banned" in store                            # the cache file is untouched by the ban (view-only)
    out = vet_hashtags(None, Platform.instagram, store=store, corpus=["#rap"], cfg=cfg)
    assert "#banned" not in out                          # yet selection still excludes it


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


def test_ok_cache_ranks_chips_and_flags_bans(tmp_path):
    cfg = Config(root=tmp_path)
    at = "2026-07-20T00:00:00+00:00"
    _cache(cfg, {"#low": 10, "#high": 900, "#banned": 500}, at=at)
    add_ban(cfg, "#banned")
    status = views_hashtags._store_status(cfg)
    assert status.state == "ok" and status.metric_field == METRIC_FIELD
    assert [r["tag"] for r in status.tags] == ["#high", "#banned", "#low"]   # platform metric desc
    assert [r["banned"] for r in status.tags] == [False, True, False]        # flagged, not removed
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
    # The section-1 HTML must carry NO add/remove/research controls (the corpus is DERIVED, not curated).
    html = _client(cfg).get("/hashtags").data.decode()
    section1 = html.split("<h3>Measurement cache</h3>")[0]   # everything before section 2 is section 1 + header
    assert "/personas/corpus/add" not in section1        # the add/remove corpus routes are GONE entirely
    assert "/personas/research" not in section1          # ...as is the research proposal lane
    assert "/hashtags/ban" not in section1               # the ban forms live in section 4, not here
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
