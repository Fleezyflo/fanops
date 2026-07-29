# tests/test_corpus_admission.py — MOL-665 relatedness→candidate, MOL-685 category bar,
# MOL-692 size→rank (the magnet soft lane and MAGNET_METRIC_FLOOR are DELETED).
from __future__ import annotations
from datetime import datetime, timezone
from fanops.personas import Persona
from fanops.persona_research import (
    _aligned_pool, _is_candidate, inbound_hits, n_roots, is_category,
    CATEGORY_MEDIA_FLOOR, TOP_GRID_N,
)

NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


def _rec(*, play=None, like=None, frm=None, size=None, trend=None):
    d = {"measured_at": NOW.isoformat(), "from": dict(frm or {})}
    if play is not None: d["play_count"] = float(play)
    if like is not None: d["like_count"] = float(like)
    if size is not None: d["media_count"] = float(size)
    if trend is not None: d["current_top_reel_play_max_7d"] = float(trend)
    return d


def test_one_hit_collision_not_candidate_despite_huge_plays():
    """#celia-class: single inbound from a root is not relatedness."""
    anchors = {"#bars", "#lyricism"}
    rec = _rec(play=122467, frm={"#bars": 1})
    assert inbound_hits(rec, anchors) == 1 and n_roots(rec, anchors) == 1
    assert not _is_candidate("#celia", rec, anchors)


def test_one_hit_collision_not_candidate_despite_huge_SIZE():
    """The MOL-692 version of the same guard: volume must not buy a seat either."""
    anchors = {"#bars", "#lyricism"}
    rec = _rec(play=10, frm={"#bars": 1}, size=90_000_000)
    assert is_category(rec)
    assert not _is_candidate("#viral", rec, anchors)


def test_multi_hit_or_multi_root_is_candidate():
    anchors = {"#beatmaking", "#lyricism"}
    assert _is_candidate("#mpcbeats", _rec(play=9609, frm={"#beatmaking": 4}), anchors)
    assert _is_candidate("#rap", _rec(like=694, frm={"#lyricism": 1, "#beatmaking": 1}), anchors)


def test_anchor_always_candidate_when_evidence():
    anchors = {"#hiphop"}
    assert _is_candidate("#hiphop", _rec(play=100, frm={}), anchors)


def test_category_scale_needs_multi_root_but_is_never_banned():
    anchors = {"#syrianrap", "#arabicdrill"}
    whale = CATEGORY_MEDIA_FLOOR * 40
    assert not _is_candidate("#love", _rec(like=6009, frm={"#syrianrap": 4}, size=whale), anchors)
    assert _is_candidate("#love", _rec(like=6009, frm={"#syrianrap": 2, "#arabicdrill": 2},
                                       size=whale), anchors)


def test_magnet_soft_lane_is_gone():
    """It admitted #fyp / #love on ONE inbound hit plus a high Top-grid median — the number MOL-692
    stopped ranking on. There is no `is_magnet` / `MAGNET_METRIC_FLOOR` left to consult."""
    import fanops.persona_research as pr
    for dead in ("is_magnet", "_MAGNET_BODIES", "MAGNET_METRIC_FLOOR"):
        assert not hasattr(pr, dead), f"{dead} is soft-lane machinery and must be deleted"
    assert not _is_candidate("#fyp", _rec(play=999999, frm={"#rapbeef": 1}), {"#rapbeef"})


def test_aligned_pool_is_size_ordered_and_still_drops_one_hit():
    per = Persona(id="x", name="X", niche=["bars", "lyricism"])
    cache = {
        "#bars": _rec(play=100, size=4_000_000),                       # anchor
        "#celia": _rec(play=122467, frm={"#bars": 1}),                 # one hit -> out
        "#songs": _rec(play=2130, frm={"#lyricism": 4}, size=8_000),   # tiny but related
        "#remix": _rec(play=5, frm={"#bars": 2, "#lyricism": 2}, size=200_000),
    }
    pool = _aligned_pool(per, cache, now=NOW)
    order = [t for t, _v, _s in pool]
    assert "#celia" not in order
    assert order == ["#bars", "#remix", "#songs"], "biggest first, regardless of median plays"
    assert dict((t, v) for t, v, _s in pool)["#bars"] == 4_000_000.0


def test_top_grid_n_tracks_the_real_sample():
    assert TOP_GRID_N == 27
