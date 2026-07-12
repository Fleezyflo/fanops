# tests/test_hashtag_page.py — U11 the Hashtags observatory page + the global ban mechanism.
# Covers: GET render-inertness (zero network), ban filters vet_hashtags selection + S12 auto-accept
# (ban beats pin), rotation consecutive-duplicate detection, fail-open store/budget rendering, and the
# read-only corpora rows. Mirrors test_studio_app.py's client fixture; respects the _LEAKY_ENV gotcha
# (no new env is introduced — the ban list is a control FILE — so nothing new to strip).
import json
from datetime import datetime, timezone, timedelta

import pytest
pytest.importorskip("flask")  # the Studio web UI is an optional extra ([studio]); skip cleanly when Flask is absent

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops import personas as core
from fanops import hashtags
from fanops.hashtags import vet_hashtags, load_bans, add_ban, remove_ban
from fanops.persona_research import refresh_persona_corpus
from fanops.studio import views_hashtags


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg)
    app.config.update(TESTING=True)
    return app.test_client()


def _z(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


# ── acceptance #1: GET renders five sections with ZERO network calls ────────────────────────────

def test_page_get_is_network_inert(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    core.add_persona(cfg, name="Blogger")               # so section 1 has a row
    # Any Graph call would go through requests.get — make it a hard failure if the GET touches the network.
    import requests
    def _boom(*a, **k):
        raise AssertionError("GET /hashtags must be budget-inert — no network call allowed")
    monkeypatch.setattr(requests, "get", _boom)
    r = _client(cfg).get("/hashtags")
    assert r.status_code == 200
    html = r.data.decode()
    for needle in ("Corpora at a glance", "Reach store", "Meta lookup budget", "Rotation health", "Ban lane"):
        assert needle in html                            # all five sections present


# ── acceptance #2: a banned tag never appears in vet_hashtags nor S12 auto-accept; ban beats pin ─

def test_ban_filters_vet_hashtags(tmp_path):
    cfg = Config(root=tmp_path)
    # store carries the banned tag (reach-vetted) AND a good one; corpus also curates the banned tag.
    store = ["#banned", "#rap", "#hiphop"]
    # Without cfg -> byte-identical, banned tag flows through the store/corpus.
    out_no_cfg = vet_hashtags(["#banned"], Platform.instagram, store=store, corpus=["#banned"])
    assert "#banned" in out_no_cfg
    # With the ban in place + cfg passed, the banned tag is stripped from EVERY path (model pick, store, corpus).
    add_ban(cfg, "#banned")
    out = vet_hashtags(["#banned"], Platform.instagram, store=store, corpus=["#banned"], cfg=cfg)
    assert "#banned" not in out
    assert out                                           # never empty — good tags backfill the freed slot


def test_ban_filters_s12_auto_accept(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    # no Meta creds -> the offline research_corpus fill path; seed the store so it has tags to propose.
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#good", "#more"], "reach": {"#good": 9, "#more": 5}}))
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#banned")            # a PINNED tag (add_corpus_tag stamps source=pinned)
    core.add_corpus_tag(cfg, pid, "#keep")
    add_ban(cfg, "#banned")                              # ban a PINNED tag -> ban must beat pin
    res = refresh_persona_corpus(cfg, pid)
    assert res.get("changed") is True
    assert "#banned" in (res.get("removed") or [])      # the refit reports the banned pin as removed
    corpus_after = set(core.Personas.load(cfg).get(pid).hashtag_corpus)
    assert "#banned" not in corpus_after                # removed (ban beats pin), and auto-fill never re-added it
    assert "#keep" in corpus_after                      # the non-banned pin survives


def test_ban_survives_store_refresh_shape(tmp_path):
    # A banned tag present in the store still never selects — the store file is NOT rewritten by the ban,
    # so re-reading it and re-vetting with cfg keeps excluding the tag (survives a store refresh).
    cfg = Config(root=tmp_path)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#banned", "#rap"], "reach": {"#banned": 99, "#rap": 3}}))
    add_ban(cfg, "#banned")
    store = hashtags.load_store(cfg)
    assert "#banned" in store                            # the store file is untouched by the ban (view-only)
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


# ── acceptance #4: corrupt budget/store fail-open copy ──────────────────────────────────────────

def test_corrupt_budget_fail_closed_copy(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.hashtag_budget_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtag_budget_path.write_text("CORRUPT")        # budget_remaining -> None (fail-closed)
    meter = views_hashtags.budget_meter(cfg)
    assert meter.fail_closed is True
    assert meter.copy == "budget file unreadable — querying nothing until it heals"
    assert meter.used == 0 and meter.remaining == 0      # no invented numbers


def test_budget_meter_reports_used_and_reset(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.hashtag_budget_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    cfg.hashtag_budget_path.write_text(json.dumps({"queries": [{"tag": "#x", "ts": now.isoformat()}]}))
    meter = views_hashtags.budget_meter(cfg, now=now)
    assert meter.fail_closed is False
    assert meter.used == 1 and meter.remaining == meter.limit - 1
    assert meter.window_reset == (now + timedelta(days=7)).isoformat()


def test_corrupt_store_unreadable(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text("NOT JSON {")
    status = views_hashtags._store_status(cfg)
    assert status.state == "unreadable"
    # and the whole page still renders 200 with a corrupt store
    r = _client(cfg).get("/hashtags")
    assert r.status_code == 200


def test_store_frozen_floor_when_missing(tmp_path):
    cfg = Config(root=tmp_path)
    status = views_hashtags._store_status(cfg)
    assert status.state == "frozen floor"


# ── acceptance #5: corpus rows byte-truth, no edit controls ─────────────────────────────────────

def test_corpus_rows_read_only(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="Music Blogger")
    core.add_corpus_tag(cfg, pid, "#rap")
    core.add_corpus_tag(cfg, pid, "#hiphop")
    rows = views_hashtags._corpora_rows(cfg)
    row = next(r for r in rows if r.pid == pid)
    assert row.size == 2                                 # byte-truth from personas.json
    assert row.pinned == 2 and row.auto == 0            # add_corpus_tag stamps source=pinned
    assert row.last_refreshed is not None               # a stamp from hashtag_corpus_meta
    # The section-1 HTML must carry NO add/remove/research controls (editing lives on Personas).
    html = _client(cfg).get("/hashtags").data.decode()
    section1 = html.split("Reach store")[0]              # everything before section 2 is section 1 + header
    assert "do_personas_corpus_add" not in section1
    assert "do_personas_research" not in section1
    assert "do_hashtags_ban" not in section1             # the ban forms live in section 5, not here
    assert "edit →" in section1                          # but the read-only link to Personas is present
