# tests/test_persona_lever_registry.py — M1 the SINGLE LEVER REGISTRY characterization + coherence guard.
import itertools
from fanops.config import Config
from fanops.personas import (CUT_POLICY, _FOCUS_CLAUSE, _FOCUS_PROFILE, _FRAMING_MAP,
                             derive_cut_spec, lever_catalog, compose_breakdown, Persona)

# ---------------------------------------------------------------------------------------------------------
# GOLDEN snapshots — the EXACT current literals (captured from live code, 2026-06-27). These are the frozen
# reference; the refactor must keep the live exports byte-identical to them.
# ---------------------------------------------------------------------------------------------------------
_GOLD_CONTENT_FOCUS = {"punchlines", "emotional", "hype", "storytelling", "visual", "bold-statement"}
_GOLD_FOCUS_CLAUSE = {
    "punchlines": "moments that land a verbal punchline — a bar with a clear setup and payoff, a quotable, rewatchable line",
    "emotional": "moments carrying real emotion — vulnerability, longing, devotion, a confession the viewer feels",
    "hype": "the highest-energy hype moments — the hardest delivery, the beat drop, the room going up",
    "storytelling": "moments that tell a story or reveal something — an origin, a turn, a payoff",
    "visual": "visually arresting moments — a strong scene, motion, or setting, not audio alone",
    "bold-statement": "a bold or contrarian statement that stops the scroll",
}
_GOLD_FOCUS_ORDER = ["punchlines", "emotional", "hype", "storytelling", "visual", "bold-statement"]
_GOLD_FOCUS_PROFILE = {"storytelling": "long", "emotional": "medium", "visual": "medium",
                       "punchlines": "short", "hype": "short", "bold-statement": "short"}
_GOLD_FRAMING_MAP = {"punchlines": "center", "hype": "center", "bold-statement": "center", "visual": "center", "emotional": "top", "storytelling": "top"}

def test_vocabularies_byte_identical():
    # MOL-523: cut_policy now owns the tokens.
    assert set(CUT_POLICY) == _GOLD_CONTENT_FOCUS
    # MOL-521/523: free-text fields have no vocab.
    assert isinstance(CUT_POLICY, frozenset)

def test_clause_maps_byte_identical():
    assert dict(_FOCUS_CLAUSE) == _GOLD_FOCUS_CLAUSE
    assert list(_FOCUS_CLAUSE.keys()) == _GOLD_FOCUS_ORDER
    # MOL-521: selection_scope and hook_angle no longer have clause maps.

def test_derived_cut_maps_byte_identical():
    assert dict(_FOCUS_PROFILE) == _GOLD_FOCUS_PROFILE
    assert list(_FOCUS_PROFILE.items()) == list(_GOLD_FOCUS_PROFILE.items())
    assert dict(_FRAMING_MAP) == _GOLD_FRAMING_MAP

def _ref_profile(foc):
    return next((v for k, v in _GOLD_FOCUS_PROFILE.items() if k in foc), None)

def test_derive_cut_spec_identical_over_all_focus_subsets():
    foci = sorted(_GOLD_CONTENT_FOCUS)
    for r in range(len(foci) + 1):
        for combo in itertools.combinations(foci, r):
            # MOL-523: cut_policy derives the cut.
            got_prof, got_fr = derive_cut_spec(Persona(id="x", cut_policy=list(combo)))
            assert got_prof == _ref_profile(set(combo)), f"derive drift for cut_policy={combo}"
            if not combo:
                assert got_fr is None

def test_derive_cut_spec_framing_from_cut_policy():
    for policy, exp in ((["punchlines"], "center"), (["storytelling"], "top"), ([], None)):
        _prof, fr = derive_cut_spec(Persona(id="x", cut_policy=policy))
        assert fr == exp

def test_lever_catalog_shape_byte_identical():
    cat = {lev["key"]: lev for lev in lever_catalog()}
    # MOL-523: content_focus (editorial) and cut_policy (deterministic) are separate.
    assert list(cat) == ["content_focus", "cut_policy", "intensity", "selection_scope", "hook_angle", "clip_profile", "niche"]
    assert cat["content_focus"]["kind"] == "text"
    assert cat["cut_policy"]["kind"] == "multi"
    assert [(o["value"], o["effect"]) for o in cat["cut_policy"]["options"]] == list(_GOLD_FOCUS_CLAUSE.items())

def _fp(cfg, p):
    d = compose_breakdown(cfg, p)
    return (d["casting"]["text"], d["hook"]["text"], d["caption"]["text"],
            d["cut"]["band"], d["cut"]["framing"], d["cut"]["source"], tuple(d["tags"]["lead"]))

def test_compose_fingerprint_for_live_shaped_personas(tmp_path):
    cfg = Config(root=tmp_path)
    # MOL-523: content_focus is editorial; cut_policy is tokens.
    personas = [
        Persona(id="craft-curator", voice="", cut_policy=["punchlines", "emotional"]),
        Persona(id="underground-zine", voice="", cut_policy=["punchlines", "hype"]),
        Persona(id="burner-bold", voice="", cut_policy=["bold-statement", "hype"]),
    ]
    fps = {p.id: _fp(cfg, p) for p in personas}
    assert len({fp[0] for fp in fps.values()}) == 3
    assert {p.id: _fp(cfg, p) for p in personas} == fps

def test_registry_is_a_pure_leaf():
    import fanops.persona_levers as pl
    assert isinstance(pl.LEVER_REGISTRY, list) and pl.LEVER_REGISTRY

def test_projections_derive_from_the_registry():
    import fanops.persona_levers as pl
    assert set(CUT_POLICY) == set(pl.vocab("cut_policy"))
    assert set(pl.vocab("content_focus")) == set()
    assert dict(_FOCUS_CLAUSE) == pl.clause_map("cut_policy")
    assert dict(_FOCUS_PROFILE) == dict(pl.focus_profile_map())
    assert dict(_FRAMING_MAP) == pl.framing_map()
    assert lever_catalog() == pl.build_catalog()
