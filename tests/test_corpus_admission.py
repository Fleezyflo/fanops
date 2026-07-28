# tests/test_corpus_admission.py — MOL-665: relatedness→candidate, numbers→member, magnet soft lane
from __future__ import annotations
from datetime import datetime, timezone
from fanops.personas import Persona
from fanops.persona_research import (
    _aligned_pool, _is_candidate, is_magnet, inbound_hits, n_roots,
    MAGNET_METRIC_FLOOR, TOP_GRID_N,
)

NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


def _rec(*, play=None, like=None, frm=None):
    d = {"measured_at": NOW.isoformat(), "from": dict(frm or {})}
    if play is not None: d["play_count"] = float(play)
    if like is not None: d["like_count"] = float(like)
    return d


def test_one_hit_collision_not_candidate_despite_huge_plays():
    """#celia-class: single inbound from a root is not relatedness."""
    anchors = {"#bars", "#lyricism"}
    tag = "#celia"
    rec = _rec(play=122467, frm={"#bars": 1})
    assert inbound_hits(rec, anchors) == 1 and n_roots(rec, anchors) == 1
    assert not is_magnet(tag)
    assert not _is_candidate(tag, rec, anchors)


def test_multi_hit_or_multi_root_is_candidate():
    anchors = {"#beatmaking", "#lyricism"}
    assert _is_candidate("#mpcbeats", _rec(play=9609, frm={"#beatmaking": 4}), anchors)
    assert _is_candidate("#rap", _rec(like=694, frm={"#lyricism": 1, "#beatmaking": 1}), anchors)


def test_anchor_always_candidate_when_evidence():
    anchors = {"#hiphop"}
    assert _is_candidate("#hiphop", _rec(play=100, frm={}), anchors)


def test_magnet_soft_lane_high_metric_one_hit():
    """Magnets are not banned: weak relatedness + high metric can enter the pool."""
    anchors = {"#viralvideo"}
    tag = "#love"
    rec = _rec(like=6009, frm={"#viralvideo": 1})
    assert is_magnet(tag)
    assert _is_candidate(tag, rec, anchors)
    # below floor → not soft, not normal → out
    weak = _rec(like=max(1, MAGNET_METRIC_FLOOR - 1), frm={"#viralvideo": 1})
    assert not _is_candidate(tag, weak, anchors)


def test_magnet_zero_relatedness_never_candidate():
    anchors = {"#rapbeef"}
    assert is_magnet("#fyp")
    assert not _is_candidate("#fyp", _rec(play=999999, frm={}), anchors)


def test_aligned_pool_drops_one_hit_keeps_multi_hit_and_soft_magnet():
    per = Persona(id="x", name="X", niche=["bars", "lyricism"])
    cache = {
        "#bars": _rec(play=100),
        "#celia": _rec(play=122467, frm={"#bars": 1}),
        "#songs": _rec(play=2130, frm={"#lyricism": 4}),
        "#love": _rec(like=9000, frm={"#bars": 1}),
        "#fyp": _rec(like=100, frm={"#bars": 1}),  # magnet but below floor + one hit
    }
    pool = {t for t, _, _ in _aligned_pool(per, cache, now=NOW)}
    assert "#bars" in pool and "#songs" in pool
    assert "#celia" not in pool
    assert "#love" in pool                                 # soft magnet
    assert "#fyp" not in pool                              # soft fail + not normal bar
    assert TOP_GRID_N == 9
