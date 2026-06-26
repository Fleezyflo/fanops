# tests/test_persona_lever_exposure.py — EXPOSE THE LEVERS. The persona editor must show what each lever
# IS and what it DOES, from the code: lever_catalog() pairs every option with its engine-true effect, and
# compose_breakdown() shows the LIVE composed translation (the exact casting/hook/caption directives + cut +
# lead tags the pipeline will run), decomposed to the lever that produced each fragment, with the engine's
# real precedence (override wins, energy=medium is a no-op) surfaced. preview_compose() runs it on TRANSIENT
# unsaved form values — never persists. Parity is the contract: what the operator SEES == what the engine RUNS.
from fanops.config import Config
from fanops.personas import (Persona, lever_catalog, compose_breakdown, produces_summary, casting_directive,
                             hook_directive, caption_directive, add_persona, Personas,
                             CONTENT_FOCUS, ENERGY_LEVELS, HOOK_ANGLES, HOOK_TONES,
                             _FOCUS_CLAUSE, _ENERGY_CLAUSE, _ANGLE_CLAUSE, _TONE_CLAUSE)
from fanops.hashtags import TAG_LEANS, _LEANS
from fanops.bands import band_for, PROFILE_NAMES
from fanops.config import FRAMING_NAMES


# ---- lever_catalog: every option carries its ENGINE-TRUE effect (no hand-written prose) ----
def _by_key(cat):
    return {lever["key"]: lever for lever in cat}


def test_catalog_focus_effect_is_the_engine_clause():
    lev = _by_key(lever_catalog())["content_focus"]
    eff = {o["value"]: o["effect"] for o in lev["options"]}
    for k, clause in _FOCUS_CLAUSE.items():
        assert eff[k] == clause                                    # the catalog shows EXACTLY what the compiler injects

def test_catalog_energy_angle_tone_effects_are_engine_clauses():
    cat = _by_key(lever_catalog())
    energy = {o["value"]: o["effect"] for o in cat["energy"]["options"]}
    assert energy["high"] == _ENERGY_CLAUSE["high"] and energy["low"] == _ENERGY_CLAUSE["low"]
    assert "medium" not in energy                                  # the no-op value is removed, not just relabelled
    angle = {o["value"]: o["effect"] for o in cat["hook_angle"]["options"]}
    assert angle["curiosity"] == _ANGLE_CLAUSE["curiosity"]
    tone = {o["value"]: o["effect"] for o in cat["hook_tone"]["options"]}
    assert tone["aggressive"] == _TONE_CLAUSE["aggressive"]

def test_catalog_clip_profile_effect_is_the_real_band():
    lev = _by_key(lever_catalog())["clip_profile"]
    eff = {o["value"]: o["effect"] for o in lev["options"]}
    b = band_for("short")
    assert f"{b.lo:g}-{b.hi:g}s" in eff["short"]                   # 8-15s — the SAME band the cut uses

def test_catalog_tag_lean_effect_lists_the_real_pool():
    lev = _by_key(lever_catalog())["tag_lean"]
    eff = {o["value"]: o["effect"] for o in lev["options"]}
    for tag in _LEANS["tasteful"]:
        assert tag in eff["tasteful"]                              # the effect names the real pool tags it floats

def test_catalog_covers_every_validated_vocab_no_orphan_options():
    cat = _by_key(lever_catalog())
    assert {o["value"] for o in cat["content_focus"]["options"]} == set(CONTENT_FOCUS)
    assert {o["value"] for o in cat["energy"]["options"]} == set(ENERGY_LEVELS)
    assert {o["value"] for o in cat["hook_angle"]["options"]} == set(HOOK_ANGLES)
    assert {o["value"] for o in cat["hook_tone"]["options"]} == set(HOOK_TONES)
    assert {o["value"] for o in cat["tag_lean"]["options"]} == set(TAG_LEANS)
    assert {o["value"] for o in cat["framing"]["options"]} == set(FRAMING_NAMES)
    assert {o["value"] for o in cat["clip_profile"]["options"]} == set(PROFILE_NAMES)


# ---- compose_breakdown: the LIVE composed translation, parity with the real compilers ----
def test_breakdown_text_is_exactly_the_compiler_output(tmp_path):
    cfg = Config(root=tmp_path)
    for p in (Persona(id="p", voice="a devoted fan", content_focus=["punchlines"], energy="high",
                      hook_angle="curiosity", hook_tone="playful"),
              Persona(id="q", voice="v"),
              Persona(id="r", casting_directive="hand-written override", hook_angle="fomo")):
        d = compose_breakdown(cfg, p)
        assert d["casting"]["text"] == casting_directive(p)        # the panel can't lie — text IS the compiler
        assert d["hook"]["text"] == hook_directive(p)
        assert d["caption"]["text"] == caption_directive(p)

def test_breakdown_fragments_trace_each_lever(tmp_path):
    cfg = Config(root=tmp_path)
    d = compose_breakdown(cfg, Persona(id="p", voice="a devoted fan", content_focus=["punchlines"], energy="high"))
    sources = {f["source"] for f in d["casting"]["fragments"]}
    assert sources == {"voice", "content_focus", "energy"}        # every fragment is attributed to its lever

def test_breakdown_fragment_text_is_substring_of_the_directive(tmp_path):
    # the provenance tooltips (fragment.text) must not drift from the compiler output — every fragment's text
    # must appear verbatim in the dimension's authoritative text, else a clause-map edit would desync the badges
    cfg = Config(root=tmp_path)
    p = Persona(id="p", voice="a devoted fan", content_focus=["punchlines", "emotional"], energy="high",
                hook_angle="curiosity", hook_tone="playful")
    d = compose_breakdown(cfg, p)
    for dim in ("casting", "hook"):
        for frag in d[dim]["fragments"]:
            assert frag["text"] in d[dim]["text"], f"{dim} fragment {frag['source']!r} not in the directive"

def test_breakdown_override_shadows_structured_levers(tmp_path):
    cfg = Config(root=tmp_path)
    d = compose_breakdown(cfg, Persona(id="p", hook_directive="my exact hook brief",
                                       hook_angle="curiosity", hook_tone="playful"))
    assert d["hook"]["override"] is True
    assert set(d["hook"]["shadowed"]) == {"hook_angle", "hook_tone"}   # the angle/tone are DEAD — surfaced, not hidden
    assert d["hook"]["text"] == "my exact hook brief"

def test_breakdown_cut_and_tags_from_real_resolvers(tmp_path):
    cfg = Config(root=tmp_path)
    d = compose_breakdown(cfg, Persona(id="p", clip_profile="short", framing="top",
                                       tag_lean="tasteful", hashtag_corpus=["#myscene"]))
    assert "8-15s" in d["cut"]["band"] and d["cut"]["framing"] == "top" and d["cut"]["source"] == "persona"
    assert "#myscene" in d["tags"]["lead"]                        # corpus floats to the lead, like the pipeline
    d2 = compose_breakdown(cfg, Persona(id="q", voice="v"))
    assert d2["cut"]["source"] == "global"                        # unset profile → global, not persona


# ---- produces_summary: the operator-facing "what this persona DROPS" lead (S7) ----
def test_produces_summary_lists_configured_dimensions(tmp_path):
    cfg = Config(root=tmp_path)
    p = Persona(id="p", voice="v", clip_profile="short", framing="top", hook_angle="curiosity",
                tag_lean="tasteful", hashtag_corpus=["#myscene"])
    d = compose_breakdown(cfg, p)
    clauses = produces_summary(d)
    joined = " · ".join(clauses)
    assert "8-15s" in joined and "clips" in joined                 # the LENGTH band, from the same cut resolver
    assert "top-framed" in clauses                                  # the FRAMING
    assert "curiosity hooks" in clauses                             # the hook ANGLE
    assert any(c.startswith("≤") and "hashtag" in c for c in clauses)  # the hashtag count (lean/corpus is set)

def test_produces_summary_unset_persona_is_empty(tmp_path):
    # a bare persona configures NOTHING distinctive -> every dimension is silent (global cut, no framing/angle,
    # no deliberate hashtag posture). The floor tags are not a persona-specific "produce".
    cfg = Config(root=tmp_path)
    assert produces_summary(compose_breakdown(cfg, Persona(id="q", voice="v"))) == []

def test_produces_summary_hashtag_clause_needs_a_deliberate_posture(tmp_path):
    # length/framing set but NO lean/corpus -> the hashtag clause stays silent (the floor isn't a choice);
    # the other clauses still list.
    cfg = Config(root=tmp_path)
    clauses = produces_summary(compose_breakdown(cfg, Persona(id="p", voice="v", clip_profile="long", framing="center")))
    assert "center-framed" in clauses and any("clips" in c for c in clauses)
    assert not any("hashtag" in c for c in clauses)

def test_produces_summary_is_embedded_in_breakdown_with_parity(tmp_path):
    # compose_breakdown carries the SAME clause list under "produces" — no second resolver, can't drift (S7 additive).
    cfg = Config(root=tmp_path)
    d = compose_breakdown(cfg, Persona(id="p", voice="v", clip_profile="short", hook_angle="fomo", tag_lean="bold"))
    assert d["produces"] == produces_summary(d)                     # parity: embedded == standalone
    assert "angle" in d["hook"]                                     # the additive hook['angle'] key

def test_produces_summary_skips_angle_when_hook_overridden(tmp_path):
    # a freeform hook override SHADOWS the structured angle -> no "curiosity hooks" clause (it isn't what runs).
    cfg = Config(root=tmp_path)
    d = compose_breakdown(cfg, Persona(id="p", voice="v", hook_directive="my brief", hook_angle="curiosity"))
    assert d["hook"]["angle"] is None
    assert not any("hooks" in c for c in produces_summary(d))

def test_produces_summary_is_pure_no_persistence(tmp_path):
    cfg = Config(root=tmp_path)
    d = compose_breakdown(cfg, Persona(id="p", voice="v", clip_profile="short"))
    produces_summary(d); produces_summary(d)                        # idempotent, takes only the dict
    assert not cfg.personas_path.exists()


# ---- preview_compose: TRANSIENT, never persists ----
def test_preview_compose_returns_breakdown_without_persisting(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    form = {"voice": "a devoted fan", "content_focus": ["punchlines"], "energy": "high"}
    r = sp.preview_compose(cfg, _Form(form))
    assert r.ok and r.detail["casting"]["text"].startswith("a devoted fan")
    assert not cfg.personas_path.exists()                          # NOTHING written — a preview never persists

def test_preview_compose_merges_saved_corpus_for_an_existing_id(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="Curator", voice="v", tag_lean="tasteful")
    Personas.load(cfg)  # sanity
    from fanops.personas import add_corpus_tag
    add_corpus_tag(cfg, "curator", "#myscene")
    r = sp.preview_compose(cfg, _Form({"id": "curator", "energy": "high"}))
    assert r.ok and "#myscene" in r.detail["tags"]["lead"]         # the saved corpus shows in the live preview

def test_preview_compose_bad_value_is_clean_error(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    r = sp.preview_compose(cfg, _Form({"energy": "loud"}))         # not a valid energy
    assert r.ok is False and r.error
    assert not cfg.personas_path.exists()


# ---- HTTP: the editor renders effects; the compose route renders the live panel ----
def test_personas_page_renders_lever_effects(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().get("/personas").get_data(as_text=True)
    assert "skip calm, low-energy passages" in html               # the energy=high effect is shown in the editor
    assert "8-15s" in html                                        # the clip_profile=short band is shown

def test_compose_route_renders_directives_and_override_warning(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().post("/personas/compose", data={
        "voice": "a devoted fan", "content_focus": "punchlines", "energy": "high",
        "hook_directive": "my exact hook brief", "hook_angle": "curiosity"}).get_data(as_text=True)
    assert "a devoted fan" in html and "peak-intensity" in html.lower() or "skip calm" in html.lower()
    assert "not used" in html                                      # the hook override-shadow warning is rendered


class _Form(dict):
    """A minimal stand-in for a Werkzeug MultiDict: .get + .getlist over a plain dict (list values stay lists)."""
    def getlist(self, key):
        v = self.get(key)
        if v is None: return []
        return v if isinstance(v, list) else [v]
