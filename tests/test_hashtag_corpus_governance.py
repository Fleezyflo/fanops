# tests/test_hashtag_corpus_governance.py
# R4 — the curated corpus is BRAND data; the store is MEASURED EVIDENCE; the two must not feed each other.
#
# The live failure this pins (00_control, 2026-07-16): `_seed_tags` builds the store out of every persona's
# corpus, and `research_corpus` proposed from `vetted_menu(load_store(cfg))` — the store, re-ranked — whose
# output `refresh_persona_corpus` wrote back as auto corpus entries. corpus -> store -> corpus, closed, with
# no external evidence in it and nothing in the data to show it. The live store was BYTE-IDENTICAL to
# `seeds + frozen floor`: 53 tags, 0 discovered, `reach: {}` — while every proposal looked like research.
#
# Each test below fails on the pre-R4 code.
import json
from datetime import datetime, timedelta, timezone
from fanops.config import Config
from fanops import personas as P          # the FACADE must be imported before persona_research: personas.py
                                          # re-exports from it and persona_research imports Personas back
                                          # (pre-existing on main — see personas.py's module docstring).
from fanops.hashtags import load_store_evidence, load_store_reach

_FYP = "#fypppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp"   # 73 p's — was live + shipping
NOW = datetime(2026, 7, 16, tzinfo=timezone.utc)


def _persona(cfg, pid="p1", corpus=()):
    P.add_persona(cfg, name=pid, id=pid)
    if corpus:                                     # bypass the hygiene gate to model LEGACY polluted data
        raw = json.loads(cfg.personas_path.read_text())
        for d in raw["personas"]:
            if d["id"] == pid: d["hashtag_corpus"] = list(corpus)
        cfg.personas_path.write_text(json.dumps(raw))
    return cfg


# ---- the cut: a corpus echo in the store cannot masquerade as discovery -------------------------
def test_store_built_from_corpus_seeds_proposes_nothing(tmp_path):
    # THE circularity. The store is seeded from the corpora, so its tags are our own curation echoed back.
    # Pre-R4 research_corpus re-ranked that menu and handed it back as "research". With no measurement
    # anywhere, the honest answer is NOTHING.
    from fanops.persona_research import research_corpus
    cfg = _persona(Config(root=tmp_path), corpus=["#bars", "#lyrics"])
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({                     # exactly the live shape: seeds, no evidence
        "tags": ["#bars", "#lyrics", "#hiphop", "#rap", "#undergroundhiphop"], "reach": {}}))
    assert research_corpus(cfg, "p1") == [], "a seed echo was proposed back as research — the loop is open"


def test_only_measured_evidence_is_proposed(tmp_path):
    from fanops.persona_research import research_corpus
    cfg = _persona(Config(root=tmp_path), corpus=["#bars"])
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#bars", "#measured", "#echo", "#legacy"], "reach": {
        "#measured": {"reach": 900, "measured_at": NOW.isoformat(), "source": "graph-reach", "confidence": 1.0},
        "#legacy": 5000,                                          # a bare number: provenance genuinely unknown
    }}))
    out = research_corpus(cfg, "p1", now=NOW)
    assert out == ["#measured"], f"only real Graph evidence may curate, got {out}"
    assert "#legacy" not in out, "an unprovenanced number must not curate (and must not be back-dated)"
    assert "#echo" not in out, "an unmeasured store tag is not evidence"


def test_expired_evidence_does_not_curate(tmp_path):
    from fanops.persona_research import research_corpus, _EVIDENCE_MAX_AGE_DAYS
    cfg = _persona(Config(root=tmp_path), corpus=["#bars"])
    old = (NOW - timedelta(days=_EVIDENCE_MAX_AGE_DAYS + 1)).isoformat()
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#stale"], "reach": {
        "#stale": {"reach": 900, "measured_at": old, "source": "graph-reach", "confidence": 1.0}}}))
    assert research_corpus(cfg, "p1", now=NOW) == [], "a dead measurement must expire, not curate forever"


def test_discovered_junk_cannot_be_promoted(tmp_path):
    # evidence is necessary but NOT sufficient: junk with real reach still must not become curated data.
    from fanops.persona_research import research_corpus
    cfg = _persona(Config(root=tmp_path), corpus=["#bars"])
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": [_FYP, "#love"], "reach": {
        t: {"reach": 9_000_000, "measured_at": NOW.isoformat(), "source": "graph-reach", "confidence": 1.0}
        for t in (_FYP, "#love")}}))
    assert research_corpus(cfg, "p1", now=NOW) == [], "huge reach must not buy junk a curated slot"


def test_auto_refresh_cannot_rewrite_a_pinned_curated_corpus(tmp_path, monkeypatch):
    # curated == pinned == human-governed. A daemon tick must never reclaim those slots.
    from fanops.persona_research import refresh_persona_corpus
    from fanops.persona_store import add_corpus_tag
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False); monkeypatch.delenv("META_IG_USER_ID", raising=False)
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="Curator", id="curator")
    for t in ("#bars", "#lyrics", "#hiphopmusic"):
        add_corpus_tag(cfg, "curator", t)
    before = list(P.Personas.load(cfg).get("curator").hashtag_corpus)
    refresh_persona_corpus(cfg, "curator")
    assert list(P.Personas.load(cfg).get("curator").hashtag_corpus) == before


# ---- evidence store: provenance, honesty, no destructive overwrite ------------------------------
def test_legacy_bare_reach_is_marked_unknown_not_backdated(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#a"], "reach": {"#a": 1200}}))
    ev = load_store_evidence(cfg)
    assert ev["#a"]["reach"] == 1200.0                    # the number survives verbatim
    assert ev["#a"]["source"] == "unknown"                # we do not know where it came from — say so
    assert ev["#a"]["measured_at"] is None                # NOT fabricated
    assert ev["#a"]["confidence"] == 0.0
    assert load_store_reach(cfg) == {"#a": 1200.0}        # flat projection unchanged for legacy readers


def test_evidence_records_round_trip_and_project(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({"tags": ["#a"], "reach": {
        "#A": {"reach": 900, "measured_at": NOW.isoformat(), "source": "graph-reach", "confidence": 1.0}}}))
    assert load_store_reach(cfg) == {"#a": 900.0}         # normalized key, flat view
    assert load_store_evidence(cfg)["#a"]["source"] == "graph-reach"


def test_refresh_stamps_provenance_and_survives_zero_budget(tmp_path, monkeypatch):
    from tests.test_fanops_hashtags import _graph_router
    from fanops.fanops_hashtags import refresh_store
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path)
    P.add_persona(cfg, name="Curator", id="curator")
    P.add_corpus_tag(cfg, "curator", "#bars")
    refresh_store(cfg, get=_graph_router({"#beta": 900}, cooccur="#beta"), now=NOW)
    ev = load_store_evidence(cfg)
    assert ev["#beta"]["source"] == "graph-reach" and ev["#beta"]["measured_at"] == NOW.isoformat()
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False)     # zero-budget tick measures nothing
    refresh_store(cfg)
    assert load_store_evidence(cfg) == ev, "a zero-budget refresh erased accrued evidence"
