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
    out = str(compose_persona_instruction(Persona(id="p", content_focus=["punchlines", "emotional"])))
    assert "punchline" in out and "emotion" in out                  # substantive clauses, not "favors moments: punchlines"
    
    assert "favors moments" not in out                              # the trivial phrasing is gone

def test_compose_both_body_then_voice():
    out = str(compose_persona_instruction(Persona(id="p", voice="a devoted fan", content_focus=["punchlines", "hype"])))
    assert out.startswith("a devoted fan") and "hype moments" in out   # voice leads, then the substantive clip-for clause

def test_compose_ignores_cut_levers_in_text():
    # clip_profile/framing drive the deterministic CUT, NOT the prompt text
    out = str(compose_persona_instruction(Persona(id="p", voice="v", clip_profile="short", framing="top")))
    assert out == "v"


# ---- write boundary: levers validate + round-trip ----
def test_add_persona_persists_levers(tmp_path):
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="Curator", voice="tasteful crate-digger", content_focus=["storytelling", "emotional"], hook_angle="emotional")
    p = Personas.load(cfg).get("curator")
    assert p.content_focus == ["storytelling", "emotional"] and p.selection_scope is None
    assert p.hook_angle == "emotional"
    assert resolved_cut_spec(p) == ("long", "top")

def test_add_persona_rejects_unknown_lever(tmp_path):
    cfg = Config(root=tmp_path)
    with pytest.raises(ValueError):
        add_persona(cfg, name="Bad", selection_scope="ludicrous")
    with pytest.raises(ValueError):
        add_persona(cfg, name="Bad2", content_focus=["punchlines", "not-a-thing"])

def test_update_persona_changes_levers_only_when_passed(tmp_path):
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v", content_focus=["storytelling"])
    update_persona(cfg, "p", hook_angle="challenge")          # voice/energy untouched
    p = Personas.load(cfg).get("p")
    assert p.voice == "v" and p.selection_scope is None and p.hook_angle == "challenge"

def test_lever_vocabularies_are_frozensets():
    for v in (CONTENT_FOCUS, SELECTION_SCOPE_LEVELS, HOOK_ANGLES):
        assert isinstance(v, frozenset) and v


# ---- hydration: a linked persona's levers land on the account; unlinked is byte-identical ----
def _write(cfg, accts, personas):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accts}))
    cfg.personas_path.write_text(json.dumps({"personas": personas}))

def test_hydrate_levers_onto_linked_account(tmp_path):
    cfg = Config(root=tmp_path)
    _write(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
                  "persona_id": "curator"}],
           [{"id": "curator", "voice": "tasteful", "content_focus": ["storytelling"],
             "hook_angle": "emotional", "selection_scope": "open"}])
    a = next(x for x in Accounts.load(cfg).accounts if x.handle == "a")
    assert a.persona == "tasteful" and a.content_focus == ["storytelling"] and a.selection_scope == "open"
    # M3d: clip_profile/framing DERIVE from content_focus/energy onto the account (no per-persona pin)
    assert a.hook_angle == "emotional" and a.clip_profile == "long" and a.framing == "top"

def test_unlinked_account_levers_stay_empty(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "persona": "x"}]}))
    a = next(x for x in Accounts.load(cfg).accounts if x.handle == "a")
    assert a.content_focus == [] and a.selection_scope is None and compose_persona_instruction(a) == "x"


# ---- directive firewall: the composed casting instruction; only-voice == byte-identical ----
def test_casting_directive_only_voice_is_byte_identical(tmp_path):
    from fanops.personas import casting_directive
    # firewall: no levers -> the casting directive == raw voice (the string the picker brief reads).
    assert str(casting_directive(Account(handle="a", persona="bold fan"))) == "bold fan"

def test_casting_directive_carries_lever_direction(tmp_path):
    from fanops.personas import casting_directive
    persona_str = str(casting_directive(Persona(id="p", voice="bold fan", content_focus=["punchlines", "hype"])))
    assert "punchline" in persona_str and "hype moments" in persona_str  # substantive, not adjectives


# ---- Studio surface (Task 5): set levers in the browser; the card shows "what the AI reads" ----
def test_studio_create_persona_persists_levers(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    r = sp.create_persona(cfg, name="Curator", voice="champions craft", content_focus=["punchlines", "hype"], hook_angle="curiosity")
    assert r.ok
    p = Personas.load(cfg).get(r.detail["created"])
    assert p.content_focus == ["punchlines", "hype"] and p.selection_scope is None or p.selection_scope == "open"
    assert p.hook_angle == "curiosity"                   # cut (length/framing) is DERIVED, not a settable knob

def test_studio_create_persona_bad_lever_is_clean_error(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    r = sp.create_persona(cfg, name="X", selection_scope="ludicrous")
    assert r.ok is False and r.error                     # no raise -> the panel renders the ✗

def test_personas_page_exposes_composed_instruction(tmp_path):
    from fanops.studio import views
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="a devoted fan", content_focus=["hype"])
    card = next(c for c in views.personas_page(cfg).personas if c.id == "p")
    assert card.instruction.startswith("a devoted fan") and "hype moments" in card.instruction   # voice + substantive clip-for
    assert card.content_focus == ["hype"] and card.selection_scope is None

def test_personas_panel_renders_lever_controls(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v", content_focus=["punchlines"], hook_angle="curiosity")
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().get("/personas").get_data(as_text=True)
    assert 'name="content_focus"' in html and 'name="hook_angle"' in html   # the lever controls render
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
    f = persona_facts(cfg, Persona(id="p", content_focus=["punchlines", "emotional"],
                                   hashtag_corpus=["#myscene"]))
    assert f["length_band"] == "16-26s"          # emotional wins medium tier over punchlines short
    assert f["framing"] == "center"
    assert "#myscene" in f["lead_tags"]         # vet_hashtags floats the curated corpus to the lead

def test_persona_facts_default_length_when_unset(tmp_path):
    from fanops.personas import persona_facts
    cfg = Config(root=tmp_path)
    f = persona_facts(cfg, Persona(id="p", voice="v"))
    assert f["length_band"] == "12-22s" and f["framing"] is None     # band_for(None) -> TALK default (pipeline-faithful)

def test_personas_page_exposes_facts(tmp_path):
    from fanops.studio import views
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v", content_focus=["storytelling"])   # M3d: derives long (28-45s)
    card = next(c for c in views.personas_page(cfg).personas if c.id == "p")
    assert card.length_band == "28-45s"
    assert isinstance(card.lead_tags, list)

def test_personas_panel_renders_transparency_facts(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v", content_focus=["punchlines"])   # M3d: derives short (8-15s)
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().get("/personas").get_data(as_text=True)
    assert "8-15s" in html                       # the resolved length band is shown (transparency)


# ======================================================================================
# M3 — THE DIRECTIVE ENGINE. Each structured lever compiles into a SUBSTANTIVE per-dimension
# instruction injected into THAT dimension's real prompt (not a glued adjective). The operator
# can OVERRIDE the compiled text per dimension; clip_count is a per-persona clip ceiling. THE
# FIREWALL holds: no levers + no override -> the bare voice, byte-identical to today.
# ======================================================================================
from fanops.personas import casting_directive, hook_directive, caption_directive

def test_casting_directive_is_substantive_not_adjective():
    out = str(casting_directive(Persona(id="p", content_focus=["punchlines", "hype"])))
    assert "punchline" in out and ("punchline" in out)
    assert "favors moments" not in out and "energy high" not in out      # the trivial phrasing is GONE

def test_hook_directive_compiles_angle():
    out = str(hook_directive(Persona(id="p", voice="bold fan", hook_angle="curiosity")))
    assert "curiosity gap" in out                                         # the angle compiles into real hook language
    assert "hook angle" not in out                                        # substantive, not "hook angle curiosity"
    assert out.startswith("bold fan")                                     # the voice leads (it carries the register)

def test_hook_directive_is_separate_from_casting():
    # the on-screen hook levers shape the HOOK prompt, NOT the casting prompt (per-dimension split)
    p = Persona(id="p", content_focus=["hype"], hook_angle="curiosity")
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
    add_persona(cfg, name="P", voice="v", content_focus=["punchlines"], hook_angle="curiosity")
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().get("/personas").get_data(as_text=True)
    assert "hook &#8594;" in html or "hook →" in html or "hook →" in html   # per-dimension directive shown (clips/hook/caption)
    assert 'name="content_focus"' in html and 'name="hook_angle"' in html   # the clean lever controls

def test_studio_edit_persona_persists_levers(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v")
    r = sp.edit_persona(cfg, "p", name="P", voice="v", content_focus=["punchlines"], hook_angle="curiosity")
    assert r.ok
    p = Personas.load(cfg).get("p")
    assert p.content_focus == ["punchlines"] and p.hook_angle == "curiosity"


# ======================================================================================
# MOL-170 (A1) — consolidate energy→content_focus (framing+intensity); repurpose the energy
# lever slot as selection_scope. Still 5 levers; resolve_top_bias stays on account.framing.
# ======================================================================================
import json as _json
from pathlib import Path as _Path
import fanops.persona_levers as _pl
from fanops.config import Config as _Cfg


def test_selection_scope_replaces_energy_in_registry():
    keys = [lv["key"] for lv in _pl.LEVER_REGISTRY]
    assert "energy" not in keys and "selection_scope" in keys
    assert keys == ["content_focus", "selection_scope", "hook_angle", "clip_profile", "hashtag_corpus"]
    assert set(_pl.vocab("selection_scope")) == {"open", "subject_locked", "source_briefed", "credibility_first", "controversy_seeking"}
    assert "energy" not in _pl.editable_fields()
    assert "selection_scope" in _pl.editable_fields()


def test_content_focus_derives_framing():
    assert _pl.framing_map()["storytelling"] == "top"
    assert _pl.framing_map()["punchlines"] == "center"
    assert resolved_cut_spec(Persona(id="p", content_focus=["storytelling"])) == ("long", "top")
    assert resolved_cut_spec(Persona(id="p", content_focus=["punchlines"])) == ("short", "center")
    assert resolved_cut_spec(Persona(id="p", content_focus=["punchlines", "storytelling"])) == ("long", "center")


def test_resolve_top_bias_still_reads_account_framing(tmp_path):
    cfg = _Cfg(root=tmp_path)
    top = Account(handle="top", framing="top")
    center = Account(handle="ctr", framing="center")
    assert cfg.resolve_top_bias(top) is True
    assert cfg.resolve_top_bias(center) is False
    assert cfg.resolve_top_bias(Account(handle="bare")) == cfg.aware_reframe


def test_energy_to_scope_migration_parity(tmp_path):
    cfg = _Cfg(root=tmp_path)
    legacy = {"personas": [{"id": "curator", "name": "Curator", "voice": "tasteful",
                            "content_focus": ["storytelling"], "energy": "low", "hook_angle": "emotional"}]}
    cfg.personas_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.personas_path.write_text(_json.dumps(legacy))
    p = Personas.load(cfg).get("curator")
    assert p.selection_scope == "open"
    assert not hasattr(p, "energy") or getattr(p, "energy", None) is None
    assert resolved_cut_spec(p) == ("long", "top")
    from fanops.personas import casting_directive, compose_breakdown
    assert "introspective" not in str(casting_directive(p))          # old energy=low clause gone; framing from focus
    d = compose_breakdown(cfg, p)
    assert d["cut"]["framing"] == "top" and "28-45s" in d["cut"]["band"]


def test_all_ten_archetypes_map():
    archetypes = _json.loads((_Path(__file__).resolve().parents[1] / "clipping_account_archetypes.json").read_text())
    ids = {t["id"] for t in archetypes["types"]}
    assert len(ids) == 10
    mapped = _pl.archetype_selection_scope_map()
    assert set(mapped) == ids
    assert mapped["single_source_briefed"] == "source_briefed"
    assert mapped["single_subject_fan"] == "subject_locked"
    assert mapped["manufactured_controversy"] == "controversy_seeking"
    assert mapped["credibility_first"] == "credibility_first"
    assert mapped["opportunistic_broad_curator"] == "open"


def test_no_new_lever_family():
    assert len(_pl.LEVER_REGISTRY) == 5
    persona_levers = {lv["key"] for lv in _pl.LEVER_REGISTRY if lv["key"] not in ("clip_profile", "hashtag_corpus")}
    assert persona_levers == set(_pl.editable_fields()) - {"voice", "hashtag_corpus"}


def test_no_surviving_account_energy_selection_reader():
    import inspect
    import fanops.accounts as accts_mod
    src = inspect.getsource(accts_mod)
    assert "acc.energy" not in src and "per.energy" not in src
    assert "selection_scope" in src
