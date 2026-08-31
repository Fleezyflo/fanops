# tests/test_lever_docs.py — MOL-162/MOL-163 generated lever + threshold docs (anti-drift)
from pathlib import Path

from fanops.config import Config
from fanops import persona_levers as pl
from fanops import moments
from fanops.personas import baked_personas

_ROOT = Path(__file__).resolve().parents[1]
_LEVERS_PATH = _ROOT / "docs" / "LEVERS.md"
_THRESH_PATH = _ROOT / "docs" / "LEVER-THRESHOLDS.md"


def _cfg():
    return Config(root=_ROOT)


def test_lever_docs_covers_every_option():
    from fanops.lever_docs import render_levers
    md = render_levers(_cfg())
    for lv in pl.LEVER_REGISTRY:
        if lv["key"] == "niche":          # the NICHE is free text — no enumerated options to cover
            continue
        for opt in lv["options"]:
            assert opt["value"] in md, f"missing option {lv['key']}:{opt['value']}"


def test_lever_docs_matches_committed():
    from fanops.lever_docs import render_levers
    assert render_levers(_cfg()) == _LEVERS_PATH.read_text()


def test_lever_docs_names_deterministic_op():
    from fanops.lever_docs import render_levers
    md = render_levers(_cfg())
    assert "filter_peaks_by_intensity" in md or "peak-filter" in md.lower() or "tercile" in md
    assert "top" in md and "center" in md
    assert "8-15s pick-time" not in md


def test_archetype_crosswalk_matches_personas():
    from fanops.lever_docs import render_levers, archetype_crosswalk_rows
    baked = baked_personas()
    assert baked, "baked personas seed required (MOL-175)"
    md = render_levers(_cfg())
    assert "ARCHETYPE CROSSWALK" in md
    for row in archetype_crosswalk_rows():
        assert row["id"] in md
        for foc in row["content_focus"]:
            assert foc in md
        assert row["hook_angle"] in md
        assert row["selection_scope"] in md or row["selection_scope"] == "open"
    ids = {r["id"] for r in archetype_crosswalk_rows()}
    assert ids == {p.id for p in baked}


def test_threshold_docs_matches_committed():
    from fanops.lever_docs import render_thresholds
    assert render_thresholds(_cfg()) == _THRESH_PATH.read_text()


def test_threshold_docs_lists_live_values():
    from fanops.lever_docs import render_thresholds
    md = render_thresholds(_cfg())
    assert str(moments._MAX_OVERLAP_FRAC) in md
    assert "_MAX_TARGET_PICKS" not in md
    assert "_target_pick_count" not in md
    assert "tercile" in md.lower() or "filter_peaks_by_intensity" in md
    assert str(moments._EOF_TOLERANCE_S) in md
    assert "_MIN_MOMENT_S" not in md
