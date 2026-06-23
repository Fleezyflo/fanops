# tests/test_corpus_research.py
# B3 — bootstrap research + active surfacing. research_corpus proposes the reach-best hashtags a persona
# doesn't yet carry, grounded in the reach-ranked store (own-reach + Graph trends, default-ON) plus the
# persona's lean flavor — instant + budget-free (the store already encodes the Graph signal); the operator
# accepts the proposals. The Personas page then surfaces the corpus REACH-RANKED (store order, top-first)
# with the high-reach (store-present) tags flagged.
import json
import pytest
from fanops.config import Config
from fanops import personas as core
from fanops.studio import personas as sp
from fanops.studio import views


# --- core.research_corpus ----------------------------------------------------------------------

def test_research_proposes_reach_tags_not_in_corpus(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", tag_lean="tasteful")
    core.add_corpus_tag(cfg, pid, "#lyrics")           # already curated
    out = core.research_corpus(cfg, pid)
    assert "#lyrics" not in out                         # excludes what's already in the corpus
    assert "#bars" in out                               # proposes the rest of the lean flavor
    assert all(t.startswith("#") for t in out)


def test_research_lean_flavor_leads(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1", tag_lean="underground")
    out = core.research_corpus(cfg, pid)
    assert out[0] in {"#freestyle", "#undergroundhiphop", "#trap"}   # the persona's flavor leads


def test_research_uses_reach_store_order(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")              # no lean -> store order leads
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#owned", "#hiphop"]}))
    out = core.research_corpus(cfg, pid)
    assert out[0] == "#owned"                           # own-reach + trends store leads the proposal


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
    pid = core.add_persona(cfg, name="P1", tag_lean="bold")
    r = sp.research_corpus(cfg, pid)
    assert r.ok and r.detail["persona"] == pid and len(r.detail["proposals"]) >= 1


def test_studio_research_unknown_persona_clean_error(tmp_path):
    r = sp.research_corpus(Config(root=tmp_path), "ghost")
    assert r.ok is False and r.error


# --- read-model surfacing: corpus reach-ranked + high-reach flag --------------------------------

def test_personas_page_surfaces_corpus_reach_ranked(tmp_path):
    cfg = Config(root=tmp_path)
    pid = core.add_persona(cfg, name="P1")
    core.add_corpus_tag(cfg, pid, "#bars")             # added first, but lower reach
    core.add_corpus_tag(cfg, pid, "#owned")
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#owned", "#bars"]}))   # #owned ranks above #bars
    card = next(c for c in views.personas_page(cfg).personas if c.id == pid)
    assert card.corpus[0] == "#owned"                   # displayed reach-first (store order), not insertion order
    assert "#owned" in card.reach_tags and "#bars" in card.reach_tags   # both are in the reach store -> flagged


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
