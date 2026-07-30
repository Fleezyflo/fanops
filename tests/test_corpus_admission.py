# tests/test_corpus_admission.py — MOL-665 relatedness→candidate, MOL-685 category bar,
# MOL-692 size→rank, MOL-714 niche-only seat + unique-vocab relatedness + MIN_MEDIA_FLOOR
# (the magnet soft lane and MAGNET_METRIC_FLOOR are DELETED).
#
# Callers: pytest. Existing tests/test_corpus_admission.py (not a new module). Synthetic personas/
# accounts/vocab JSON only. User: "Implement the plan as specified... Don't stop until you have
# completed all the to-dos."
from __future__ import annotations
import json
from datetime import datetime, timezone
from fanops.config import Config
from fanops.personas import Persona, Personas, add_persona
from fanops.persona_research import (
    _aligned_pool, _is_candidate, inbound_hits, n_roots, is_category,
    CATEGORY_MEDIA_FLOOR, MIN_MEDIA_FLOOR, TOP_GRID_N,
    niche_terms, persona_terms, relatedness_terms,
)
from fanops import hashtag_vocab as hv

NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


def _rec(*, play=None, like=None, frm=None, size=None, trend=None):
    d = {"measured_at": NOW.isoformat(), "from": dict(frm or {})}
    if play is not None: d["play_count"] = float(play)
    if like is not None: d["like_count"] = float(like)
    if size is not None: d["media_count"] = float(size)
    if trend is not None: d["current_top_reel_play_max_7d"] = float(trend)
    return d


def _link(cfg, by_handle):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "platforms": ["instagram"], "status": "active", "persona_id": pid}
        for h, pid in by_handle.items()]}))


def test_one_hit_collision_not_candidate_despite_huge_plays():
    anchors = {"#bars", "#lyricism"}
    rec = _rec(play=122467, frm={"#bars": 1}, size=50_000)
    assert inbound_hits(rec, anchors) == 1 and n_roots(rec, anchors) == 1
    assert not _is_candidate("#celia", rec, anchors)


def test_one_hit_collision_not_candidate_despite_huge_SIZE():
    anchors = {"#bars", "#lyricism"}
    rec = _rec(play=10, frm={"#bars": 1}, size=90_000_000)
    assert is_category(rec)
    assert not _is_candidate("#viral", rec, anchors)


def test_multi_hit_or_multi_root_is_candidate():
    anchors = {"#beatmaking", "#lyricism"}
    assert _is_candidate("#mpcbeats", _rec(play=9609, frm={"#beatmaking": 4}, size=5_000), anchors)
    assert _is_candidate("#rap", _rec(like=694, frm={"#lyricism": 1, "#beatmaking": 1}, size=5_000), anchors)


def test_anchor_always_candidate_when_evidence():
    anchors = {"#hiphop"}
    assert _is_candidate("#hiphop", _rec(play=100, frm={}), anchors)


def test_niche_unconditional_seat_even_without_media_count():
    assert _is_candidate("#bars", _rec(play=10, frm={}), {"#other"}, niche_anchors={"#bars"})
    assert not _is_candidate("#other", _rec(play=10, frm={"#bars": 4}), {"#bars"},
                             niche_anchors={"#bars"})


def test_category_scale_needs_multi_root_but_is_never_banned():
    anchors = {"#syrianrap", "#arabicdrill"}
    whale = CATEGORY_MEDIA_FLOOR * 40
    assert not _is_candidate("#love", _rec(like=6009, frm={"#syrianrap": 4}, size=whale), anchors)
    assert _is_candidate("#love", _rec(like=6009, frm={"#syrianrap": 2, "#arabicdrill": 2},
                                       size=whale), anchors)


def test_magnet_soft_lane_is_gone():
    import fanops.persona_research as pr
    for dead in ("is_magnet", "_MAGNET_BODIES", "MAGNET_METRIC_FLOOR"):
        assert not hasattr(pr, dead), f"{dead} is soft-lane machinery and must be deleted"
    assert not _is_candidate("#fyp", _rec(play=999999, frm={"#rapbeef": 1}, size=50_000), {"#rapbeef"})


def test_sub_floor_non_niche_refused():
    anchors = {"#bars"}
    assert MIN_MEDIA_FLOOR == 1_000.0
    assert not _is_candidate("#songs", _rec(play=2130, frm={"#bars": 4}), anchors)
    assert not _is_candidate("#songs", _rec(play=2130, frm={"#bars": 4}, size=999), anchors)
    assert _is_candidate("#songs", _rec(play=2130, frm={"#bars": 4}, size=1_000), anchors)


def test_aligned_pool_is_size_ordered_and_still_drops_one_hit():
    per = Persona(id="x", name="X", niche=["bars", "lyricism"])
    cache = {
        "#bars": _rec(play=100, size=4_000_000),
        "#celia": _rec(play=122467, frm={"#bars": 1}, size=50_000),
        "#songs": _rec(play=2130, frm={"#lyricism": 4}, size=8_000),
        "#remix": _rec(play=5, frm={"#bars": 2, "#lyricism": 2}, size=200_000),
        "#ghost": _rec(play=9000, frm={"#bars": 4}),
    }
    pool = _aligned_pool(per, cache, now=NOW)
    order = [t for t, _v, _s in pool]
    assert "#celia" not in order and "#ghost" not in order
    assert order == ["#bars", "#remix", "#songs"], "biggest first, regardless of median plays"
    assert dict((t, v) for t, v, _s in pool)["#bars"] == 4_000_000.0


def test_llm_vocab_is_not_a_root_at_all(tmp_path):
    """MOL-719: durable vocab is neither a search root nor a relatedness anchor."""
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="A", voice="v", niche=["bars"], id="a")
    _link(cfg, {"ha": "a"})
    hv.write_vocab(cfg, {"a": {"terms": ["syrianrap"], "expanded_at": NOW.isoformat(), "source": "llm"}})
    per = Personas.load(cfg).get("a")
    assert "syrianrap" not in persona_terms(per, cfg)
    assert "syrianrap" not in relatedness_terms(per, cfg)
    cache = {"#syrianrap": _rec(play=200, size=50_000, frm={})}
    pool = {t for t, _, _ in _aligned_pool(per, cache, now=NOW, cfg=cfg)}
    assert "#syrianrap" not in pool


def test_shared_llm_root_gives_no_relatedness(tmp_path):
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="A", voice="v", niche=["detroitrap"], id="a")
    add_persona(cfg, name="B", voice="v", niche=["undergroundhiphop"], id="b")
    _link(cfg, {"ha": "a", "hb": "b"})
    hv.write_vocab(cfg, {
        "a": {"terms": ["freestyle"], "expanded_at": NOW.isoformat(), "source": "llm"},
        "b": {"terms": ["freestyle"], "expanded_at": NOW.isoformat(), "source": "llm"},
    })
    a, b = Personas.load(cfg).get("a"), Personas.load(cfg).get("b")
    assert "freestyle" not in persona_terms(a, cfg) and "freestyle" not in persona_terms(b, cfg)
    assert "freestyle" not in relatedness_terms(a, cfg)
    assert "freestyle" not in relatedness_terms(b, cfg)
    cache = {"#craft": _rec(play=100, size=50_000, frm={"#freestyle": 5})}
    assert "#craft" not in {t for t, _, _ in _aligned_pool(a, cache, now=NOW, cfg=cfg)}
    assert "#craft" not in {t for t, _, _ in _aligned_pool(b, cache, now=NOW, cfg=cfg)}


def test_unique_llm_root_no_longer_attributes(tmp_path):
    """MOL-719: even a vocab term unique to this persona cannot attribute a tag into its corpus. A stale
    `from` edge keyed on a retired vocab root is inert — only declared niche attributes."""
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="A", voice="v", niche=["bars"], id="a")
    _link(cfg, {"ha": "a"})
    hv.write_vocab(cfg, {"a": {"terms": ["syrianrap"], "expanded_at": NOW.isoformat(), "source": "llm"}})
    per = Personas.load(cfg).get("a")
    assert relatedness_terms(per, cfg) == ["bars"]
    cache = {"#barsoverbeats": _rec(play=80, size=8_000, frm={"#syrianrap": 4})}
    assert "#barsoverbeats" not in {t for t, _, _ in _aligned_pool(per, cache, now=NOW, cfg=cfg)}
    cache["#barsoverbeats"]["from"] = {"#bars": 4}                    # niche root -> admitted
    assert "#barsoverbeats" in {t for t, _, _ in _aligned_pool(per, cache, now=NOW, cfg=cfg)}


def test_sibling_declared_term_attributes_only_to_declarer(tmp_path):
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="A", voice="v", niche=["hiphop"], id="a")
    add_persona(cfg, name="B", voice="v", niche=["drill"], id="b")
    _link(cfg, {"ha": "a", "hb": "b"})
    hv.write_vocab(cfg, {
        "a": {"terms": [], "expanded_at": NOW.isoformat(), "source": "llm"},
        "b": {"terms": ["hiphop"], "expanded_at": NOW.isoformat(), "source": "llm"},
    })
    a, b = Personas.load(cfg).get("a"), Personas.load(cfg).get("b")
    assert "hiphop" in niche_terms(a) and "hiphop" not in persona_terms(b, cfg)
    assert "hiphop" not in relatedness_terms(b, cfg)
    cache = {
        "#hiphop": _rec(play=100, size=4_000_000),
        "#craft": _rec(play=50, size=50_000, frm={"#hiphop": 4}),
    }
    assert "#craft" in {t for t, _, _ in _aligned_pool(a, cache, now=NOW, cfg=cfg)}
    assert "#craft" not in {t for t, _, _ in _aligned_pool(b, cache, now=NOW, cfg=cfg)}
    assert "#hiphop" in {t for t, _, _ in _aligned_pool(a, cache, now=NOW, cfg=cfg)}


def test_persona_terms_is_declared_niche_only(tmp_path):
    """MOL-719: the search-root set collapses to declared niche — shared AND unique vocab both drop out,
    so `persona_terms` and `relatedness_terms` are the same list."""
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="A", voice="v", niche=["detroitrap"], id="a")
    add_persona(cfg, name="B", voice="v", niche=["undergroundhiphop"], id="b")
    _link(cfg, {"ha": "a", "hb": "b"})
    hv.write_vocab(cfg, {
        "a": {"terms": ["freestyle", "cypher"], "expanded_at": NOW.isoformat(), "source": "llm"},
        "b": {"terms": ["freestyle"], "expanded_at": NOW.isoformat(), "source": "llm"},
    })
    a = Personas.load(cfg).get("a")
    assert persona_terms(a, cfg) == ["detroitrap"] == niche_terms(a)
    assert relatedness_terms(a, cfg) == ["detroitrap"]


def test_inputs_fingerprint_covers_policy_version(tmp_path, monkeypatch):
    """MOL-719: the derive-freshness digest must move when the ADMISSION RULES move, not only when a
    control file does. MOL-714/716 changed the rules in code, neither control file changed, so the live
    marker kept matching and no corpus was ever re-derived."""
    from fanops import persona_research as pr
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="A", voice="v", niche=["bars"], id="a")
    before_bytes = cfg.personas_path.read_bytes()
    fp = pr._inputs_fingerprint(cfg)
    monkeypatch.setattr(pr, "_POLICY_V", pr._POLICY_V + 1)
    assert cfg.personas_path.read_bytes() == before_bytes             # inputs byte-identical
    assert pr._inputs_fingerprint(cfg) != fp


def test_policy_bump_makes_corpora_due(tmp_path, monkeypatch):
    """MOL-719: a marker stamped under an older policy is DUE, not `unchanged`."""
    from fanops import persona_research as pr
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="A", voice="v", niche=["bars"], id="a")
    _link(cfg, {"ha": "a"})
    marker = cfg.control / ".corpora_refresh.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"ts": NOW.isoformat(), "inputs_fp": pr._inputs_fingerprint(cfg)}))
    assert pr.refresh_corpora_if_due(cfg, now=NOW).get("reason") == "unchanged"
    monkeypatch.setattr(pr, "_POLICY_V", pr._POLICY_V + 1)
    assert pr.refresh_corpora_if_due(cfg, now=NOW).get("refreshed") is True


def test_top_grid_n_tracks_the_real_sample():
    assert TOP_GRID_N == 27
