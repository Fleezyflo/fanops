# tests/test_persona_lever_exposure.py — EXPOSE THE LEVERS. The persona editor must show what each lever
# IS and what it DOES, from the code: lever_catalog() pairs every option with its engine-true effect, and
# compose_breakdown() shows the LIVE composed translation (the exact casting/hook/caption directives + cut +
# lead tags the pipeline will run), decomposed to the lever that produced each fragment, with the engine's
# real precedence (override wins, selection_scope=open is a no-op) surfaced. preview_compose() runs it on TRANSIENT
# unsaved form values — never persists. Parity is the contract: what the operator SEES == what the engine RUNS.
from fanops.config import Config
from fanops.personas import (Persona, lever_catalog, compose_breakdown, produces_summary, casting_directive,
                             hook_directive, caption_directive, add_persona, Personas,
                             _FOCUS_CLAUSE)   # MOL-523: _SCOPE_CLAUSE/_ANGLE_CLAUSE dropped with the vocabularies


# ---- lever_catalog: every option carries its ENGINE-TRUE effect (no hand-written prose) ----
def _by_key(cat):
    return {lever["key"]: lever for lever in cat}


def test_catalog_focus_effect_is_the_engine_clause():
    # MOL-523: the moment-kind tokens (and their compiler clauses) moved from content_focus to cut_policy.
    lev = _by_key(lever_catalog())["cut_policy"]
    eff = {o["value"]: o["effect"] for o in lev["options"]}
    for k, clause in _FOCUS_CLAUSE.items():
        assert eff[k] == clause                                    # the catalog shows EXACTLY what the compiler injects

def test_free_text_levers_expose_no_option_vocabulary():
    # MOL-523: content_focus / selection_scope / hook_angle are FREE TEXT. The catalog must offer NO options
    # for them — an option list here is exactly the token->prose map that rots, and re-adding one reds this.
    cat = _by_key(lever_catalog())
    for key in ("content_focus", "selection_scope", "hook_angle"):
        assert not cat[key]["options"], f"{key} must stay free text — no option vocabulary"

# (test_catalog_scope_angle_effects_are_engine_clauses was DELETED by MOL-523, not repaired: it asserted the
#  selection_scope/hook_angle OPTION vocabularies, and those levers are now free text with no options at all.
#  test_free_text_levers_expose_no_option_vocabulary above is the replacement guard — it pins the absence.)

def test_catalog_omits_the_removed_persona_levers():
    # tag_lean (corpus owns hashtags), hook_tone (voice carries register), clip_count, and the framing knob are
    # gone as persona levers. clip_profile stays ONLY as the GLOBAL clip-length lever (Go-Live), never a per-
    # persona knob — per persona the length is derived from content_focus.
    keys = {lever["key"] for lever in lever_catalog()}
    assert keys.isdisjoint({"tag_lean", "framing", "hook_tone", "clip_count"})
    assert "clip_profile" in keys                                  # retained as the global cut-length lever

# (MOL-803: test_catalog_covers_every_validated_vocab_no_orphan_options was DELETED — after the free-text
#  migration all three of its assertions read `set() == set()`, so it passed no matter what the catalog did.
#  test_free_text_levers_expose_no_option_vocabulary above pins the same absence with a real assertion.)


# ---- compose_breakdown: the LIVE composed translation, parity with the real compilers ----
def test_breakdown_text_is_exactly_the_compiler_output(tmp_path):
    cfg = Config(root=tmp_path)
    for p in (Persona(id="p", voice="a devoted fan", cut_policy=["punchlines", "hype"],
                      hook_angle="curiosity"),
              Persona(id="q", voice="v"),
              Persona(id="r", casting_directive="hand-written override", hook_angle="fomo")):
        d = compose_breakdown(cfg, p)
        assert d["casting"]["text"] == str(casting_directive(p))        # the panel can't lie — text IS the compiler
        assert d["hook"]["text"] == str(hook_directive(p))
        assert d["caption"]["text"] == caption_directive(p)

def test_breakdown_fragments_trace_each_lever(tmp_path):
    cfg = Config(root=tmp_path)
    d = compose_breakdown(cfg, Persona(id="p", voice="a devoted fan", cut_policy=["punchlines", "hype"]))
    sources = {f["source"] for f in d["casting"]["fragments"]}
    assert sources == {"voice", "cut_policy"}        # every fragment is attributed to its lever

def test_breakdown_fragment_text_is_substring_of_the_directive(tmp_path):
    # the provenance tooltips (fragment.text) must not drift from the compiler output — every fragment's text
    # must appear verbatim in the dimension's authoritative text, else a clause-map edit would desync the badges
    cfg = Config(root=tmp_path)
    p = Persona(id="p", voice="a devoted fan", cut_policy=["punchlines", "hype"],
                hook_angle="curiosity")
    d = compose_breakdown(cfg, p)
    for dim in ("casting", "hook"):
        for frag in d[dim]["fragments"]:
            assert frag["text"] in d[dim]["text"], f"{dim} fragment {frag['source']!r} not in the directive"

# (M3e: test_breakdown_override_shadows_structured_levers removed — the freeform directive overrides were
# retired, so no dimension can shadow its structured levers; `override` is always False, `shadowed` always [].)

def test_breakdown_flags_selection_scope_open_noop(tmp_path):
    # MOL-523: "open" was a VOCABULARY value whose clause was empty. With free text there is no such token —
    # the only no-op is a BLANK scope, and the panel must still say so rather than imply a constraint exists.
    cfg = Config(root=tmp_path)
    d = compose_breakdown(cfg, Persona(id="p", voice="v", selection_scope="", cut_policy=["hype"]))
    assert not any(f["source"] == "selection_scope" for f in d["casting"]["fragments"])
    d2 = compose_breakdown(cfg, Persona(id="q", voice="v", selection_scope="only the named subject",
                                        cut_policy=["hype"]))
    assert any(f["source"] == "selection_scope" for f in d2["casting"]["fragments"])   # set -> it compiles in

def test_breakdown_cut_and_tags_from_real_resolvers(tmp_path):
    cfg = Config(root=tmp_path)
    d = compose_breakdown(cfg, Persona(id="p", cut_policy=["punchlines", "emotional"],
                                       hashtag_corpus=["#myscene"]))   # M3d: cut DERIVES (punchlines->short, low->top)
    assert "16-26s" in d["cut"]["band"] and d["cut"]["framing"] == "center" and d["cut"]["source"] == "derived"
    assert "#myscene" in d["tags"]["lead"]                        # corpus floats to the lead, like the pipeline
    d2 = compose_breakdown(cfg, Persona(id="q", voice="v"))
    assert d2["cut"]["source"] == "global"                        # unset profile → global, not persona


# ---- produces_summary: the operator-facing "what this persona DROPS" lead (S7) ----
def test_produces_summary_lists_configured_dimensions(tmp_path):
    cfg = Config(root=tmp_path)
    p = Persona(id="p", voice="v", cut_policy=["punchlines", "emotional"], hook_angle="curiosity",
                hashtag_corpus=["#myscene"])                        # M3d: cut DERIVES (punchlines->short, low->top)
    d = compose_breakdown(cfg, p)
    clauses = produces_summary(d)
    joined = " · ".join(clauses)
    assert "16-26s" in joined and "clips" in joined                 # the LENGTH band, from the same cut resolver
    assert "curiosity hooks" in clauses                             # the hook ANGLE
    assert any(c.startswith("≤") and "hashtag" in c for c in clauses)  # the hashtag count (lean/corpus is set)

def test_produces_summary_unset_persona_is_empty(tmp_path):
    # a bare persona configures NOTHING distinctive -> every dimension is silent (global cut, no framing/angle,
    # no deliberate hashtag posture). The floor tags are not a persona-specific "produce".
    cfg = Config(root=tmp_path)
    assert produces_summary(compose_breakdown(cfg, Persona(id="q", voice="v"))) == []

def test_produces_summary_hashtag_clause_needs_a_deliberate_posture(tmp_path):
    # length set but NO corpus -> the hashtag clause stays silent (the floor isn't a choice); clips still list.
    cfg = Config(root=tmp_path)
    clauses = produces_summary(compose_breakdown(cfg, Persona(id="p", voice="v", cut_policy=["storytelling"])))   # M3d: derives long
    assert any("clips" in c for c in clauses)
    assert not any("hashtag" in c for c in clauses)

def test_produces_summary_is_embedded_in_breakdown_with_parity(tmp_path):
    # compose_breakdown carries the SAME clause list under "produces" — no second resolver, can't drift (S7 additive).
    cfg = Config(root=tmp_path)
    d = compose_breakdown(cfg, Persona(id="p", voice="v", cut_policy=["punchlines"], hook_angle="fomo"))   # M3d: derives short
    assert d["produces"] == produces_summary(d)                     # parity: embedded == standalone
    assert "angle" in d["hook"]                                     # the additive hook['angle'] key

# (M3e: test_produces_summary_skips_angle_when_hook_overridden removed — with the hook override retired, the
# structured hook_angle always drives the hook, so there is no shadow case to skip.)

def test_produces_summary_is_pure_no_persistence(tmp_path):
    cfg = Config(root=tmp_path)
    d = compose_breakdown(cfg, Persona(id="p", voice="v", cut_policy=["punchlines"]))   # M3d: derives short
    produces_summary(d); produces_summary(d)                        # idempotent, takes only the dict
    assert not cfg.personas_path.exists()


# ---- preview_compose: TRANSIENT, never persists ----
def test_preview_compose_returns_breakdown_without_persisting(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    form = {"voice": "a devoted fan", "cut_policy": ["punchlines"]}
    r = sp.preview_compose(cfg, _Form(form))
    assert r.ok and r.detail["casting"]["text"].startswith("a devoted fan")
    assert not cfg.personas_path.exists()                          # NOTHING written — a preview never persists

def test_preview_compose_merges_saved_corpus_for_an_existing_id(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="Curator", voice="v", niche=["hiphop"])
    Personas.load(cfg)  # sanity
    from fanops.personas import apply_auto_corpus
    apply_auto_corpus(cfg, "curator", tags=["#myscene"], meta={})
    r = sp.preview_compose(cfg, _Form({"id": "curator", "cut_policy": ["punchlines"]}))
    assert r.ok and "#myscene" in r.detail["tags"]["lead"]         # the saved corpus shows in the live preview

def test_preview_compose_bad_value_is_clean_error(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    # MOL-523: selection_scope is free text and cannot be "bad" — cut_policy is the validated lever.
    r = sp.preview_compose(cfg, _Form({"cut_policy": ["loud"]}))            # not a valid cut_policy token
    assert r.ok is False and r.error
    assert not cfg.personas_path.exists()


# ---- HTTP: the editor renders effects; the compose route renders the live panel ----
def test_personas_page_renders_lever_effects(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().get("/personas").get_data(as_text=True)
    # MOL-523: the scope/angle vocabularies are gone; cut_policy is the lever whose option effects the
    # editor still renders, so THAT is the proof the page shows engine-true effects rather than bare names.
    assert "punchline" in html and "cut_policy" in html

def test_compose_route_renders_directives(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().post("/personas/compose", data={
        "voice": "a devoted fan", "content_focus": "punchlines",
        "hook_angle": "curiosity"}).get_data(as_text=True)
    assert "a devoted fan" in html                                 # the live compiled directive renders from the clean levers


class _Form(dict):
    """A minimal stand-in for a Werkzeug MultiDict: .get + .getlist over a plain dict (list values stay lists)."""
    def getlist(self, key):
        v = self.get(key)
        if v is None: return []
        return v if isinstance(v, list) else [v]
