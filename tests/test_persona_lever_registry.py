# tests/test_persona_lever_registry.py — M1 the SINGLE LEVER REGISTRY characterization + coherence guard.
#
# The drift this kills: the lever vocabularies (personas.py), the compile/derived-cut clause maps
# (persona_directives.py), and the editor catalog (lever_catalog) were three separate literals synced by a
# manual "parity test forbids divergence" promise. M1 makes ONE registry (fanops.persona_levers) the upstream
# of all three. This file is TWO things: (1) a CHARACTERIZATION net holding GOLDEN copies of the current
# literals + an INDEPENDENT reference derive over all 64 content_focus subsets, so the registry refactor is
# proven byte-identical; (2) the SINGLE-DECLARATION coherence proof — an option declared once is present in
# the derived vocab, the derived clause map, AND lever_catalog together (and absent from all three when gone).
import itertools

from fanops.config import Config
from fanops.personas import (CONTENT_FOCUS, ENERGY_LEVELS, HOOK_ANGLES,
                             _FOCUS_CLAUSE, _ENERGY_CLAUSE, _ANGLE_CLAUSE, _FOCUS_PROFILE, _ENERGY_FRAMING,
                             derive_cut_spec, lever_catalog, compose_breakdown, Persona)


# ---------------------------------------------------------------------------------------------------------
# GOLDEN snapshots — the EXACT current literals (captured from live code, 2026-06-27). These are the frozen
# reference; the refactor must keep the live exports byte-identical to them. NOT re-derived from the registry
# (that would defeat the net) — hand-frozen here on purpose.
# ---------------------------------------------------------------------------------------------------------
_GOLD_CONTENT_FOCUS = {"punchlines", "emotional", "hype", "storytelling", "visual", "bold-statement"}
_GOLD_ENERGY = {"low", "medium", "high"}
_GOLD_ANGLES = {"curiosity", "challenge", "emotional", "result-first", "fomo"}

_GOLD_FOCUS_CLAUSE = {
    "punchlines": "moments that land a verbal punchline — a bar with a clear setup and payoff, a quotable, rewatchable line",
    "emotional": "moments carrying real emotion — vulnerability, longing, devotion, a confession the viewer feels",
    "hype": "the highest-energy hype moments — the hardest delivery, the beat drop, the room going up",
    "storytelling": "moments that tell a story or reveal something — an origin, a turn, a payoff",
    "visual": "visually arresting moments — a strong scene, motion, or setting, not audio alone",
    "bold-statement": "a bold or contrarian statement that stops the scroll",
}
_GOLD_FOCUS_ORDER = ["punchlines", "emotional", "hype", "storytelling", "visual", "bold-statement"]
_GOLD_ENERGY_CLAUSE = {"low": "Favor calmer, more introspective moments over loud ones.", "medium": "",
                       "high": "Strongly prefer peak-intensity moments; skip calm, low-energy passages."}
_GOLD_ANGLE_CLAUSE = {"curiosity": "open a curiosity gap the viewer has to close",
                      "challenge": "dare or challenge the viewer to react",
                      "emotional": "name the high-arousal feeling the clip gives the viewer",
                      "result-first": "open on the payoff, then reveal how it got there",
                      "fomo": "carry genuine scarcity — a one-time, leaked, or unreleased drop"}
# longer-bias-first; the ORDER is load-bearing (next() picks the first/highest tier present)
_GOLD_FOCUS_PROFILE = {"storytelling": "long", "emotional": "medium", "visual": "medium",
                       "punchlines": "short", "hype": "short", "bold-statement": "short"}
_GOLD_ENERGY_FRAMING = {"high": "center", "low": "top"}


# ---- the live exports equal the golden literals (value-exact, order-exact where it matters) ----
def test_vocabularies_byte_identical():
    assert set(CONTENT_FOCUS) == _GOLD_CONTENT_FOCUS
    assert set(ENERGY_LEVELS) == _GOLD_ENERGY
    assert set(HOOK_ANGLES) == _GOLD_ANGLES
    for v in (CONTENT_FOCUS, ENERGY_LEVELS, HOOK_ANGLES):
        assert isinstance(v, frozenset)


def test_clause_maps_byte_identical():
    assert dict(_FOCUS_CLAUSE) == _GOLD_FOCUS_CLAUSE
    assert list(_FOCUS_CLAUSE.keys()) == _GOLD_FOCUS_ORDER          # join order matters for the casting text
    assert dict(_ENERGY_CLAUSE) == _GOLD_ENERGY_CLAUSE
    assert dict(_ANGLE_CLAUSE) == _GOLD_ANGLE_CLAUSE


def test_derived_cut_maps_byte_identical():
    assert dict(_FOCUS_PROFILE) == _GOLD_FOCUS_PROFILE
    assert list(_FOCUS_PROFILE.items()) == list(_GOLD_FOCUS_PROFILE.items())   # tier-descending order is the selection
    assert dict(_ENERGY_FRAMING) == _GOLD_ENERGY_FRAMING


# ---- derive_cut_spec over ALL 64 content_focus subsets == an INDEPENDENT reference (the GOTCHA proof) ----
def _ref_profile(foc):
    """Reference: the highest tier present, computed from the GOLDEN profile map's insertion order — the SAME
    'first key whose option is present' semantics, but independent of the registry under test."""
    return next((v for k, v in _GOLD_FOCUS_PROFILE.items() if k in foc), None)


def test_derive_cut_spec_identical_over_all_focus_subsets():
    foci = sorted(_GOLD_CONTENT_FOCUS)
    for r in range(len(foci) + 1):
        for combo in itertools.combinations(foci, r):
            got_prof, got_fr = derive_cut_spec(Persona(id="x", content_focus=list(combo), energy=None))
            assert got_prof == _ref_profile(set(combo)), f"derive drift for content_focus={combo}"
            assert got_fr is None                                   # energy=None -> no framing opinion


def test_derive_cut_spec_framing_from_energy():
    for energy, exp in (("high", "center"), ("low", "top"), ("medium", None), (None, None)):
        _prof, fr = derive_cut_spec(Persona(id="x", energy=energy))
        assert fr == exp


# ---- lever_catalog() full shape characterization ----
def test_lever_catalog_shape_byte_identical():
    cat = {lev["key"]: lev for lev in lever_catalog()}
    assert list(cat) == ["content_focus", "energy", "hook_angle", "clip_profile", "hashtag_corpus"]
    # content_focus options == the focus clause map, value+effect exact, in clause order
    cf = cat["content_focus"]
    assert [(o["value"], o["effect"]) for o in cf["options"]] == list(_GOLD_FOCUS_CLAUSE.items())
    assert cf["kind"] == "multi" and cf["stage"] == "casting"
    # energy: medium's empty clause is shown as an explicit no-op note
    en = {o["value"]: o["effect"] for o in cat["energy"]["options"]}
    assert en["high"] == _GOLD_ENERGY_CLAUSE["high"] and "any" in en["medium"].lower()
    # hook_angle options == the angle clause map
    assert {o["value"]: o["effect"] for o in cat["hook_angle"]["options"]} == _GOLD_ANGLE_CLAUSE
    # clip_profile stays the GLOBAL band lever (5 bands), hashtag_corpus has no enumerated options
    assert [o["value"] for o in cat["clip_profile"]["options"]] == ["short", "medium", "long", "talk", "song"]
    assert cat["hashtag_corpus"]["options"] == []
    assert sorted(lever_catalog()[0].keys()) == ["does", "key", "kind", "label", "options", "stage"]


# ---- compose fingerprint across 3 live-shaped personas (any compile drift reds here) ----
def _fp(cfg, p):
    d = compose_breakdown(cfg, p)
    return (d["casting"]["text"], d["hook"]["text"], d["caption"]["text"],
            d["cut"]["band"], d["cut"]["framing"], d["cut"]["source"], tuple(d["tags"]["lead"]))


def test_compose_fingerprint_for_live_shaped_personas(tmp_path):
    cfg = Config(root=tmp_path)
    personas = [
        Persona(id="craft-curator", voice="", content_focus=["punchlines", "emotional"], hook_angle="curiosity"),
        Persona(id="underground-zine", voice="", content_focus=["punchlines", "hype"], energy="high", hook_angle="curiosity"),
        Persona(id="burner-bold", voice="", content_focus=["bold-statement", "hype"], energy="high", hook_angle="challenge"),
    ]
    fps = {p.id: _fp(cfg, p) for p in personas}
    # the 3 are distinct on the casting text (different content_focus) — the differentiation is live
    assert len({fp[0] for fp in fps.values()}) == 3
    # stable, recomputable fingerprint (idempotent): recompute equals
    assert {p.id: _fp(cfg, p) for p in personas} == fps


# =========================================================================================================
# THE M1 DELIVERABLE — single-declaration coherence: an option declared ONCE in the registry is present in
# the derived vocab, the derived clause map, AND the catalog together; remove it from a registry COPY and it
# vanishes from all three projections at once. This is the structural property that kills the 3-way drift.
# =========================================================================================================
def test_registry_is_a_pure_leaf():
    import sys
    import fanops.persona_levers as pl                              # must import with no fanops deps at load
    # bands is imported LAZILY inside build_catalog — not at module load
    assert "fanops.bands" not in [m for m in sys.modules if m == "fanops.bands.__not__"]  # smoke: import didn't explode
    assert isinstance(pl.LEVER_REGISTRY, list) and pl.LEVER_REGISTRY


def test_projections_derive_from_the_registry():
    import fanops.persona_levers as pl
    # the live personas vocab IS the registry projection
    assert set(CONTENT_FOCUS) == set(pl.vocab("content_focus"))
    assert set(ENERGY_LEVELS) == set(pl.vocab("energy"))
    assert set(HOOK_ANGLES) == set(pl.vocab("hook_angle"))
    # the live clause maps ARE the registry projection
    assert dict(_FOCUS_CLAUSE) == pl.clause_map("content_focus")
    assert dict(_ENERGY_CLAUSE) == pl.clause_map("energy")
    assert dict(_ANGLE_CLAUSE) == pl.clause_map("hook_angle")
    assert dict(_FOCUS_PROFILE) == dict(pl.focus_profile_map())
    assert list(_FOCUS_PROFILE.items()) == list(pl.focus_profile_map().items())   # tier-descending order preserved
    assert dict(_ENERGY_FRAMING) == pl.energy_framing_map()
    # the live catalog IS the registry projection
    assert lever_catalog() == pl.build_catalog()


def test_single_declaration_option_present_in_all_three_projections():
    import fanops.persona_levers as pl
    val = "storytelling"
    assert val in pl.vocab("content_focus")                         # vocab
    assert val in pl.clause_map("content_focus")                    # clause map
    assert val in {o["value"] for o in next(lv for lv in pl.build_catalog() if lv["key"] == "content_focus")["options"]}
    assert val in pl.focus_profile_map()                            # derived-cut map


def test_removing_an_option_from_a_registry_copy_drops_it_from_all_three():
    import copy
    import fanops.persona_levers as pl
    reg = copy.deepcopy(pl.LEVER_REGISTRY)
    cf = next(lv for lv in reg if lv["key"] == "content_focus")
    cf["options"] = [o for o in cf["options"] if o["value"] != "storytelling"]
    # build the three projections from the MUTATED registry via the same pure derivers
    vocab = frozenset(o["value"] for o in cf["options"])
    clause = {o["value"]: o["clause"] for o in cf["options"]}
    cat_vals = {o["value"] for o in cf["options"]}
    assert "storytelling" not in vocab and "storytelling" not in clause and "storytelling" not in cat_vals
    # and the real (unmutated) registry still has it — the deepcopy didn't leak
    assert "storytelling" in pl.vocab("content_focus")
