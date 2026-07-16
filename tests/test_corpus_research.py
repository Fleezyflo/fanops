# tests/test_corpus_research.py
# B3 — bootstrap research + active surfacing. research_corpus proposes hashtags a persona doesn't yet carry,
# the operator accepts them, and the Personas page surfaces the corpus reach-ranked.
#
# R4 (2026-07-16) — THIS FILE'S ORIGINAL PREMISE WAS FALSE AND IS CORRECTED HERE. It read: "grounded in the
# reach-ranked store (LIVE Meta Graph reach) ... the store already encodes the Graph signal". The store did
# NOT encode a Graph signal. `fanops_hashtags._seed_tags` BUILDS the store out of every persona's corpus, so
# the store was our own curation echoed back — measured live: byte-identical to `seeds + frozen floor`,
# 53 tags, 0 discovered, `reach: {}`. Proposing from it closed the loop corpus -> store -> corpus, and
# `refresh_persona_corpus` wrote the echo back as auto corpus entries.
#
# So the tests below no longer assert that an UNMEASURED store tag is proposed — that behaviour WAS the
# defect. research_corpus now proposes only real, unexpired Graph measurement. See ADR-0104.
import json
import pytest
from datetime import datetime, timezone
from fanops.config import Config
from fanops import personas as core
from fanops.studio import personas as sp
from fanops.studio import views

NOW = datetime(2026, 7, 16, tzinfo=timezone.utc)


def _evidence(cfg, **reach):
    """Write a store whose tags carry REAL Graph evidence — the only thing that may curate now."""
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": list(reach), "reach": {
        t: {"reach": v, "measured_at": NOW.isoformat(), "source": "graph-reach", "confidence": 1.0}
        for t, v in reach.items()}}))


# --- core.research_corpus ----------------------------------------------------------------------

def test_research_proposes_measured_tags_not_in_corpus(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#lyrics")           # already curated
    _evidence(cfg, **{"#lyrics": 500, "#bars": 900})
    out = core.research_corpus(cfg, pid, now=NOW)
    assert "#lyrics" not in out                         # excludes what's already in the corpus
    assert "#bars" in out                               # measured + uncurated -> proposed
    assert all(t.startswith("#") for t in out)


def test_research_proposes_nothing_from_an_unmeasured_store(tmp_path):
    # THE loop, pinned: the store is built from the corpora, so an unmeasured store tag is an echo of our
    # own curation. With no measurement anywhere the honest answer is nothing — not a re-ranked mirror.
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#owned", "#hiphop"], "reach": {}}))
    assert core.research_corpus(cfg, pid, now=NOW) == []


def test_research_uses_measured_reach_order(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    _evidence(cfg, **{"#owned": 900, "#hiphop": 10})
    out = core.research_corpus(cfg, pid, now=NOW)
    assert out[0] == "#owned"                           # highest MEASURED Graph reach leads the proposal


def test_research_capped(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    assert len(core.research_corpus(cfg, pid, limit=3)) <= 3


def test_research_unknown_persona_raises(tmp_path):
    with pytest.raises(KeyError):
        core.research_corpus(Config(root=tmp_path), "ghost")


# --- Studio action -----------------------------------------------------------------------------

def test_studio_research_returns_proposals(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    _evidence(cfg, **{"#bars": 900})                    # R4: the button surfaces MEASURED evidence, not the menu
    r = sp.research_corpus(cfg, pid)
    assert r.ok and r.detail["persona"] == pid and len(r.detail["proposals"]) >= 1


def test_studio_research_with_no_evidence_proposes_nothing(tmp_path):
    # The Studio "Research corpus" button must not manufacture proposals out of the store echo.
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    r = sp.research_corpus(cfg, pid)
    assert r.ok and r.detail["proposals"] == []


def test_studio_research_unknown_persona_clean_error(tmp_path):
    r = sp.research_corpus(Config(root=tmp_path), "ghost")
    assert r.ok is False and r.error


# --- read-model surfacing: corpus reach-ranked + high-reach flag --------------------------------

def test_personas_page_surfaces_corpus_reach_ranked(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#bars")             # added first, but lower reach
    core.add_corpus_tag(cfg, pid, "#owned")
    # MOL-59: ★ is measured-reach-gated — both tags carry a real reach value here so both stay flagged.
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#owned", "#bars"], "reach": {"#owned": 900, "#bars": 300}}))   # #owned ranks above #bars
    card = next(c for c in views.personas_page(cfg).personas if c.id == pid)
    assert card.corpus[0] == "#owned"                   # displayed reach-first (store order), not insertion order
    assert "#owned" in card.reach_tags and "#bars" in card.reach_tags   # both have MEASURED reach -> flagged


def test_personas_page_reach_tags_empty_without_store(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#detroitrap")        # a curated tag not in any reach store
    card = next(c for c in views.personas_page(cfg).personas if c.id == pid)
    assert card.corpus == ["#detroitrap"] and card.reach_tags == []


# --- route smoke -------------------------------------------------------------------------------

def test_research_route_smoke(tmp_path):
    cfg = Config(root=tmp_path)
    core.add_persona(cfg, name="P1", id="p1")
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/personas/research", data={"id": "p1"})
    assert r.status_code == 200
