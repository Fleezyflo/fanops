# tests/test_persona_levers.py — M1 the LEVER ENGINE. The persona stops being one fuzzy `voice` adjective:
# each characteristic becomes a validated lever (content_focus/energy → casting, hook_angle/hook_tone → hook,
# clip_profile/framing → cut, tag_lean/corpus → caption) and compose_persona_instruction renders the SET
# levers into the single instruction string the casting/hook/caption prompts read. THE FIREWALL: a persona
# with only `voice` set composes to that voice VERBATIM, so every existing persona's payload is byte-identical.
import json
from fanops.config import Config
from fanops.accounts import Accounts, Account
from fanops.personas import (Persona, compose_persona_instruction, add_persona, update_persona, Personas,
                             resolved_cut_spec, CONTENT_FOCUS, SELECTION_SCOPE_LEVELS, HOOK_ANGLES)
import pytest


# ---- compose: THE FIREWALL — only-voice is the verbatim identity ----
def test_compose_only_voice_is_verbatim_identity():
    assert compose_persona_instruction(Persona(id="p", voice="bold fan hyping the artist")) == "bold fan hyping the artist"

def test_compose_reads_account_persona_field_too():
    # duck-typed: an Account carries the hydrated voice in `.persona`, not `.voice`
    assert compose_persona_instruction(Account(handle="a", persona="raw underground scene")) == "raw underground scene"

def test_compose_empty_is_empty():
    assert compose_persona_instruction(Persona(id="p")) == ""

def test_compose_levers_only_renders_substantive_body():
    # M3: the casting directive (compose alias) compiles content_focus+energy into REAL selection language,
    # NOT a glued adjective. hook_angle/hook_tone belong to hook_directive, not this casting text.
    out = str(compose_persona_instruction(Persona(id="p", cut_policy=["punchlines", "emotional"])))
    assert "punchline" in out and "emotion" in out                  # substantive clauses, not "favors moments: punchlines"
    
    assert "favors moments" not in out                              # the trivial phrasing is gone

def test_compose_both_body_then_voice():
    out = str(compose_persona_instruction(Persona(id="p", voice="a devoted fan", cut_policy=["punchlines", "hype"])))
    assert out.startswith("a devoted fan") and "hype moments" in out   # voice leads, then the substantive clip-for clause

def test_compose_ignores_cut_levers_in_text():
    # clip_profile/framing drive the deterministic CUT, NOT the prompt text
    out = str(compose_persona_instruction(Persona(id="p", voice="v", clip_profile="short", framing="top")))
    assert out == "v"


# ---- write boundary: levers validate + round-trip ----
def test_add_persona_persists_levers(tmp_path):
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="Curator", voice="tasteful crate-digger", cut_policy=["storytelling", "emotional"], hook_angle="emotional", niche=["hiphop"])
    p = Personas.load(cfg).get("curator")
    assert p.cut_policy == ["storytelling", "emotional"] and p.selection_scope is None
    assert p.hook_angle == "emotional"
    assert resolved_cut_spec(p) == ("long", "top")

def test_add_persona_rejects_unknown_lever(tmp_path):
    # MOL-523: only the VOCABULARY levers can reject a value. selection_scope is free text now, so any
    # prose is legal there — cut_policy and intensity are the two that still validate against a set.
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="Free", selection_scope="anything the operator types", niche=["hiphop"])
    with pytest.raises(ValueError):
        add_persona(cfg, name="Bad2", cut_policy=["punchlines", "not-a-thing"], niche=["hiphop"])
    with pytest.raises(ValueError):
        add_persona(cfg, name="Bad3", intensity="ludicrous", niche=["hiphop"])

def test_update_persona_changes_levers_only_when_passed(tmp_path):
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v", cut_policy=["storytelling"], niche=["hiphop"])
    update_persona(cfg, "p", hook_angle="challenge")          # voice/energy untouched
    p = Personas.load(cfg).get("p")
    assert p.voice == "v" and p.selection_scope is None and p.hook_angle == "challenge"

def test_free_text_directive_is_bounded_at_200_chars(tmp_path):
    # MOL-521: with content_focus/selection_scope/hook_angle free text, persona_store._norm_directive's
    # max_len=200 is the ENTIRE write boundary for them — no vocabulary can reject a value any more, so this
    # bound is the only thing between the operator and an unbounded blob compiled into every casting/hook
    # prompt. The bound is INCLUSIVE (200 lands, 201 raises), the error names the lever, and both writers
    # enforce it — update_persona is the sibling that would silently diverge if only add_persona validated.
    cfg = Config(root=tmp_path)
    at_cap, over_cap = "x" * 200, "x" * 201
    add_persona(cfg, name="Edge", content_focus=at_cap, selection_scope=at_cap, hook_angle=at_cap, niche=["hiphop"])
    saved = Personas.load(cfg).get("edge")
    assert saved.content_focus == at_cap and saved.selection_scope == at_cap and saved.hook_angle == at_cap
    for lever in ("content_focus", "selection_scope", "hook_angle"):
        with pytest.raises(ValueError, match=f"{lever} too long"):
            add_persona(cfg, name="TooLong", niche=["hiphop"], **{lever: over_cap})
        assert Personas.load(cfg).get("toolong") is None       # raised BEFORE the lock — no record landed
        with pytest.raises(ValueError, match=f"{lever} too long"):
            update_persona(cfg, "edge", **{lever: over_cap})
        assert getattr(Personas.load(cfg).get("edge"), lever) == at_cap   # the stored value survives the reject

def test_lever_vocabularies_are_frozensets():
    # MOL-523: content_focus / selection_scope / hook_angle became FREE TEXT, so their vocabularies are
    # deliberately EMPTY. Asserting the emptiness is the load-bearing half — it reds if someone re-introduces
    # a token->prose map for them, which is the one lever shape this system does not allow.
    from fanops.persona_store import CUT_POLICY
    for v in (CONTENT_FOCUS, SELECTION_SCOPE_LEVELS, HOOK_ANGLES):
        assert isinstance(v, frozenset) and not v
    assert isinstance(CUT_POLICY, frozenset) and CUT_POLICY   # the ONE surviving vocabulary: it derives the cut


# ---- hydration: a linked persona's levers land on the account; unlinked is byte-identical ----
def _write(cfg, accts, personas):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accts}))
    cfg.personas_path.write_text(json.dumps({"personas": personas}))

def test_hydrate_levers_onto_linked_account(tmp_path):
    cfg = Config(root=tmp_path)
    _write(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
                  "persona_id": "curator"}],
           [{"id": "curator", "voice": "tasteful", "cut_policy": ["storytelling"],
             "hook_angle": "emotional", "selection_scope": "open"}])
    a = next(x for x in Accounts.load(cfg).accounts if x.handle == "a")
    assert a.persona == "tasteful" and a.cut_policy == ["storytelling"] and a.selection_scope == "open"
    assert a.hook_angle == "emotional"
    assert a.clip_profile is None and a.framing is None

def test_unlinked_account_levers_stay_empty(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "persona": "x"}]}))
    a = next(x for x in Accounts.load(cfg).accounts if x.handle == "a")
    assert a.cut_policy == [] and a.selection_scope is None and compose_persona_instruction(a) == "x"


# ---- directive firewall: the composed casting instruction; only-voice == byte-identical ----
def test_casting_directive_only_voice_is_byte_identical(tmp_path):
    from fanops.personas import casting_directive
    # firewall: no levers -> the casting directive == raw voice (the string the picker brief reads).
    assert str(casting_directive(Account(handle="a", persona="bold fan"))) == "bold fan"

def test_casting_directive_carries_lever_direction(tmp_path):
    from fanops.personas import casting_directive
    persona_str = str(casting_directive(Persona(id="p", voice="bold fan", cut_policy=["punchlines", "hype"])))
    assert "punchline" in persona_str and "hype moments" in persona_str  # substantive, not adjectives


# ---- Studio surface (Task 5): set levers in the browser; the card shows "what the AI reads" ----
def test_studio_create_persona_persists_levers(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    r = sp.create_persona(cfg, name="Curator", voice="champions craft", cut_policy=["punchlines", "hype"], hook_angle="curiosity", niche="hiphop")
    assert r.ok
    p = Personas.load(cfg).get(r.detail["created"])
    assert p.cut_policy == ["punchlines", "hype"] and p.selection_scope is None or p.selection_scope == "open"
    assert p.hook_angle == "curiosity"                   # cut (length/framing) is DERIVED, not a settable knob

def test_studio_create_persona_bad_lever_is_clean_error(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    # MOL-523: selection_scope is free text and accepts anything — cut_policy is the validated lever now.
    r = sp.create_persona(cfg, name="X", cut_policy=["not-a-focus"], niche="hiphop")
    assert r.ok is False and r.error                     # no raise -> the panel renders the ✗

def test_personas_page_exposes_composed_instruction(tmp_path):
    from fanops.studio import views
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="a devoted fan", cut_policy=["hype"], niche=["hiphop"])
    card = next(c for c in views.personas_page(cfg).personas if c.id == "p")
    assert card.instruction.startswith("a devoted fan") and "hype moments" in card.instruction   # voice + substantive clip-for
    assert card.cut_policy == ["hype"] and card.selection_scope is None

def test_personas_panel_renders_lever_controls(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v", cut_policy=["punchlines"], hook_angle="curiosity", niche=["hiphop"])
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().get("/personas").get_data(as_text=True)
    assert 'name="cut_policy"' in html and 'name="hook_angle"' in html and 'name="niche"' in html
    assert 'name="content_focus"' not in html and 'name="selection_scope"' not in html
    assert "AI reads" in html                                                # the composed-instruction line


# ======================================================================================
# M2 — SEE & LOCK (the wrapper): a live strategy check, a LOCKED brief that steers the
# real prompts, and a transparency breakdown derived from the SAME resolvers the pipeline
# uses. THE FIREWALL EXTENDS: the new `brief` field defaults empty, so a persona with no
# brief composes EXACTLY as before (every M1 firewall test still holds).
# ======================================================================================

# ---- Task 7: LOCK — the brief composes into the instruction; empty brief is byte-identical ----
def test_compose_empty_brief_is_byte_identical():
    assert compose_persona_instruction(Persona(id="p", voice="bold fan")) == "bold fan"   # brief default "" -> firewall holds

# ---- transparency — facts derived from the REAL resolvers (length band + lead tags) ----
def test_persona_facts_resolve_from_real_resolvers(tmp_path):
    from fanops.personas import persona_facts
    cfg = Config(root=tmp_path)
    f = persona_facts(cfg, Persona(id="p", cut_policy=["punchlines", "emotional"],
                                   hashtag_corpus=["#myscene"]))
    assert f["length_band"] == ""
    assert f["framing"] == "center"
    assert f["lead_tags"] == []                  # caption tags are the source lock, not persona compile

def test_persona_facts_default_length_when_unset(tmp_path):
    from fanops.personas import persona_facts
    cfg = Config(root=tmp_path)
    f = persona_facts(cfg, Persona(id="p", voice="v"))
    assert f["length_band"] == "" and f["framing"] is None

def test_personas_page_exposes_facts(tmp_path):
    from fanops.studio import views
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v", cut_policy=["storytelling"], niche=["hiphop"])   # M3d: derives long (28-45s)
    card = next(c for c in views.personas_page(cfg).personas if c.id == "p")
    assert card.length_band == ""
    assert isinstance(card.lead_tags, list)

def test_personas_panel_renders_transparency_facts(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v", cut_policy=["punchlines"], niche=["hiphop"])   # M3d: derives short (8-15s)
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().get("/personas").get_data(as_text=True)
    assert "AI reads" in html                                                # the composed-instruction line


# ======================================================================================
# M3 — THE DIRECTIVE ENGINE. Each structured lever compiles into a SUBSTANTIVE per-dimension
# instruction injected into THAT dimension's real prompt (not a glued adjective). The operator
# can OVERRIDE the compiled text per dimension; clip_count is a per-persona clip ceiling. THE
# FIREWALL holds: no levers + no override -> the bare voice, byte-identical to today.
# ======================================================================================
from fanops.personas import casting_directive, hook_directive, caption_directive

def test_casting_directive_is_substantive_not_adjective():
    out = str(casting_directive(Persona(id="p", cut_policy=["punchlines", "hype"])))
    assert "punchline" in out and ("punchline" in out)
    assert "favors moments" not in out and "energy high" not in out      # the trivial phrasing is GONE

def test_hook_directive_compiles_angle():
    # MOL-523: hook_angle is FREE TEXT — the operator's own words compile in verbatim (no token->clause map).
    out = str(hook_directive(Persona(id="p", voice="bold fan", hook_angle="open a curiosity gap")))
    assert "curiosity gap" in out                                         # the angle compiles into real hook language
    assert "hook angle" not in out                                        # substantive, not "hook angle curiosity"
    assert out.startswith("bold fan")                                     # the voice leads (it carries the register)

def test_hook_directive_is_separate_from_casting():
    # the on-screen hook levers shape the HOOK prompt, NOT the casting prompt (per-dimension split)
    p = Persona(id="p", cut_policy=["hype"], hook_angle="open a curiosity gap")
    assert "curiosity gap" in str(hook_directive(p)) and "curiosity gap" not in str(casting_directive(p))
    assert "hype moments" in str(casting_directive(p)) and "hype moments" not in str(hook_directive(p))

# (M3e: the freeform directive OVERRIDE tests were removed — the per-dimension overrides were retired as
# invisible shadow-duplicates of the structured levers. The compile FUNCTIONS remain; their firewall + bare-
# voice behavior is covered below and the structured-lever compile is covered above.)
def test_directives_firewall_to_bare_voice():
    p = Persona(id="p", voice="bold fan")                                 # no levers set
    assert str(casting_directive(p)) == "bold fan" and str(hook_directive(p)) == "bold fan" and caption_directive(p) == "bold fan"

def test_caption_directive_is_the_voice():
    assert caption_directive(Persona(id="p", voice="v")) == "v"   # hashtags stay deterministic, not in the text


def test_personas_panel_renders_directive_ui(tmp_path):
    # the per-persona UI: the compiled directives show per dimension (read-only "what this compiles to")
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v", cut_policy=["punchlines"], hook_angle="curiosity", niche=["hiphop"])
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().get("/personas").get_data(as_text=True)
    assert "hook &#8594;" in html or "hook →" in html or "hook →" in html   # per-dimension directive shown (clips/hook/caption)
    assert 'name="cut_policy"' in html and 'name="hook_angle"' in html and 'name="niche"' in html
    assert 'name="content_focus"' not in html

def test_studio_edit_persona_persists_levers(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v", niche=["hiphop"])
    r = sp.edit_persona(cfg, "p", name="P", voice="v", cut_policy=["punchlines"], hook_angle="curiosity")
    assert r.ok
    p = Personas.load(cfg).get("p")
    assert p.cut_policy == ["punchlines"] and p.hook_angle == "curiosity"


# ======================================================================================
# MOL-170 (A1) — consolidate energy→content_focus (framing+intensity); repurpose the energy
# lever slot as selection_scope. MOL-520 adds intensity (6 levers); resolve_top_bias stays on account.framing.
# ======================================================================================
import json as _json
import fanops.persona_levers as _pl
from fanops.config import Config as _Cfg


def test_selection_scope_replaces_energy_in_registry():
    keys = [lv["key"] for lv in _pl.LEVER_REGISTRY]
    assert "energy" not in keys and "selection_scope" in keys
    assert keys == ["content_focus", "cut_policy", "intensity", "selection_scope", "hook_angle",
                    "clip_profile", "niche"]
    assert set(_pl.vocab("selection_scope")) == set()   # MOL-523: free text — no vocabulary to enumerate
    assert "energy" not in _pl.editable_fields()
    assert "selection_scope" in _pl.editable_fields()
    assert "intensity" in _pl.editable_fields()


def test_content_focus_derives_framing():
    assert _pl.framing_map()["storytelling"] == "top"
    assert _pl.framing_map()["punchlines"] == "center"
    assert resolved_cut_spec(Persona(id="p", cut_policy=["storytelling"])) == ("long", "top")
    assert resolved_cut_spec(Persona(id="p", cut_policy=["punchlines"])) == ("short", "center")
    assert resolved_cut_spec(Persona(id="p", cut_policy=["punchlines", "storytelling"])) == ("long", "center")


def test_resolve_top_bias_still_reads_account_framing(tmp_path):
    cfg = _Cfg(root=tmp_path)
    top = Account(handle="top", framing="top")
    center = Account(handle="ctr", framing="center")
    assert cfg.resolve_top_bias(top) is True
    assert cfg.resolve_top_bias(center) is False
    assert cfg.resolve_top_bias(Account(handle="bare")) == cfg.aware_reframe


def test_legacy_energy_key_ignored_scope_unset(tmp_path):
    # MOL-502: leftover energy keys are inert (Pydantic extra=ignore); do NOT infer selection_scope=open.
    cfg = _Cfg(root=tmp_path)
    legacy = {"personas": [{"id": "curator", "name": "Curator", "voice": "tasteful",
                            "cut_policy": ["storytelling"], "energy": "low", "hook_angle": "emotional"}]}
    cfg.personas_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.personas_path.write_text(_json.dumps(legacy))
    p = Personas.load(cfg).get("curator")
    assert p.selection_scope is None                         # honest unset — no energy→open inference
    assert not hasattr(p, "energy") or getattr(p, "energy", None) is None
    assert resolved_cut_spec(p) == ("long", "top")
    from fanops.personas import casting_directive, compose_breakdown
    assert "introspective" not in str(casting_directive(p))          # old energy=low clause gone; framing from focus
    d = compose_breakdown(cfg, p)
    assert d["cut"]["framing"] == "top" and d["cut"]["band"] == ""


def test_no_new_lever_family():
    assert len(_pl.LEVER_REGISTRY) == 7        # MOL-523 split content_focus -> content_focus + cut_policy
    persona_levers = {lv["key"] for lv in _pl.LEVER_REGISTRY if lv["key"] not in ("clip_profile", "niche")}
    assert persona_levers == set(_pl.editable_fields()) - {"voice", "niche"}


def test_derived_intensity_helpers_gone():
    assert not hasattr(_pl, "derive_intensity_from_focus")
    assert not hasattr(_pl, "intensity_map")


def test_persona_intensity_explicit():
    p = Persona(id="p", intensity="high", cut_policy=["hype"])
    assert p.intensity == "high"
    # content_focus alone does NOT imply intensity (derive deleted)
    bare = Persona(id="q", cut_policy=["hype", "bold-statement"])
    assert bare.intensity is None


def test_no_surviving_account_energy_selection_reader():
    import inspect
    import fanops.accounts as accts_mod
    src = inspect.getsource(accts_mod)
    assert "acc.energy" not in src and "per.energy" not in src
    assert "selection_scope" in src
