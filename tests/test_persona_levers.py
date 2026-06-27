# tests/test_persona_levers.py — M1 the LEVER ENGINE. The persona stops being one fuzzy `voice` adjective:
# each characteristic becomes a validated lever (content_focus/energy → casting, hook_angle/hook_tone → hook,
# clip_profile/framing → cut, tag_lean/corpus → caption) and compose_persona_instruction renders the SET
# levers into the single instruction string the casting/hook/caption prompts read. THE FIREWALL: a persona
# with only `voice` set composes to that voice VERBATIM, so every existing persona's payload is byte-identical.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, MomentState
from fanops.accounts import Accounts, Account
from fanops.personas import (Persona, compose_persona_instruction, add_persona, update_persona, Personas,
                             CONTENT_FOCUS, ENERGY_LEVELS, HOOK_ANGLES, HOOK_TONES)
from fanops.agentstep import request_path
from fanops.casting import request_moment_casting
import pytest


# ---- compose: THE FIREWALL — only-voice is the verbatim identity ----
def test_compose_only_voice_is_verbatim_identity():
    assert compose_persona_instruction(Persona(id="p", voice="bold fan hyping the artist")) == "bold fan hyping the artist"

def test_compose_reads_account_persona_field_too():
    # duck-typed: an Account carries the hydrated voice in `.persona`, not `.voice`
    assert compose_persona_instruction(Account(handle="@a", persona="raw underground scene")) == "raw underground scene"

def test_compose_empty_is_empty():
    assert compose_persona_instruction(Persona(id="p")) == ""

def test_compose_levers_only_renders_substantive_body():
    # M3: the casting directive (compose alias) compiles content_focus+energy into REAL selection language,
    # NOT a glued adjective. hook_angle/hook_tone belong to hook_directive, not this casting text.
    out = compose_persona_instruction(Persona(id="p", content_focus=["punchlines", "emotional"], energy="high"))
    assert "punchline" in out and "emotion" in out                  # substantive clauses, not "favors moments: punchlines"
    assert "peak-intensity" in out or "skip calm" in out            # energy=high -> a real instruction
    assert "favors moments" not in out                              # the trivial phrasing is gone

def test_compose_both_body_then_voice():
    out = compose_persona_instruction(Persona(id="p", voice="a devoted fan", content_focus=["hype"], energy="high"))
    assert out.startswith("a devoted fan") and "hype moments" in out   # voice leads, then the substantive clip-for clause

def test_compose_ignores_cut_levers_in_text():
    # clip_profile/framing drive the deterministic CUT, NOT the prompt text
    out = compose_persona_instruction(Persona(id="p", voice="v", clip_profile="short", framing="top"))
    assert out == "v"


# ---- write boundary: levers validate + round-trip ----
def test_add_persona_persists_levers(tmp_path):
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="Curator", voice="tasteful crate-digger", content_focus=["storytelling", "visual"],
                energy="low", hook_angle="emotional", hook_tone="restrained", clip_profile="long", framing="center")
    p = Personas.load(cfg).get("curator")
    assert p.content_focus == ["storytelling", "visual"] and p.energy == "low"
    assert p.hook_angle == "emotional" and p.hook_tone == "restrained"
    assert p.clip_profile == "long" and p.framing == "center"

def test_add_persona_rejects_unknown_lever(tmp_path):
    cfg = Config(root=tmp_path)
    with pytest.raises(ValueError):
        add_persona(cfg, name="Bad", energy="ludicrous")
    with pytest.raises(ValueError):
        add_persona(cfg, name="Bad2", content_focus=["punchlines", "not-a-thing"])

def test_update_persona_changes_levers_only_when_passed(tmp_path):
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v", energy="low")
    update_persona(cfg, "p", hook_angle="challenge")          # voice/energy untouched
    p = Personas.load(cfg).get("p")
    assert p.voice == "v" and p.energy == "low" and p.hook_angle == "challenge"

def test_lever_vocabularies_are_frozensets():
    for v in (CONTENT_FOCUS, ENERGY_LEVELS, HOOK_ANGLES, HOOK_TONES):
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
           [{"id": "curator", "voice": "tasteful", "content_focus": ["storytelling"], "energy": "low",
             "hook_angle": "emotional", "hook_tone": "restrained", "clip_profile": "long", "framing": "center"}])
    a = next(x for x in Accounts.load(cfg).accounts if x.handle == "@a")
    assert a.persona == "tasteful" and a.content_focus == ["storytelling"] and a.energy == "low"
    assert a.hook_angle == "emotional" and a.clip_profile == "long" and a.framing == "center"

def test_unlinked_account_levers_stay_empty(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "persona": "x"}]}))
    a = next(x for x in Accounts.load(cfg).accounts if x.handle == "@a")
    assert a.content_focus == [] and a.energy is None and compose_persona_instruction(a) == "x"


# ---- payload firewall: the casting request carries the composed instruction; only-voice == byte-identical ----
def _seed(cfg, accts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accts}))
    led = Ledger.load(cfg); led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    for mid in ("m0", "m1"):
        led.add_moment(Moment(id=mid, parent_id="src_1", content_token=mid, start=0, end=7, reason="r",
                              signal_score=1.0, transcript_excerpt="", state=MomentState.decided))
    led.save(); return Ledger.load(cfg)

def test_casting_payload_only_voice_is_byte_identical(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
                       "persona": "bold fan"}])
    request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    payload = json.loads(request_path(cfg, "moment_casting", "src_1").read_text())
    p0 = payload["personas"][0]                                     # firewall: no levers -> the casting directive == raw voice
    assert p0["handle"] == "@a" and p0["persona"] == "bold fan" and "clip_count" not in p0

def test_casting_payload_carries_lever_direction(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
                       "persona": "bold fan", "persona_id": "p"}])
    cfg.personas_path.write_text(json.dumps({"personas": [
        {"id": "p", "voice": "bold fan", "content_focus": ["punchlines"], "energy": "high"}]}))
    request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    payload = json.loads(request_path(cfg, "moment_casting", "src_1").read_text())
    persona_str = payload["personas"][0]["persona"]
    assert "punchline" in persona_str and ("peak-intensity" in persona_str or "skip calm" in persona_str)  # substantive, not adjectives


# ---- Studio surface (Task 5): set levers in the browser; the card shows "what the AI reads" ----
def test_studio_create_persona_persists_levers(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    r = sp.create_persona(cfg, name="Curator", voice="champions craft", content_focus=["punchlines", "hype"],
                          energy="high", hook_angle="curiosity", clip_profile="short", framing="top")
    assert r.ok
    p = Personas.load(cfg).get(r.detail["created"])
    assert p.content_focus == ["punchlines", "hype"] and p.energy == "high"
    assert p.hook_angle == "curiosity" and p.clip_profile == "short" and p.framing == "top"

def test_studio_create_persona_bad_lever_is_clean_error(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    r = sp.create_persona(cfg, name="X", energy="ludicrous")
    assert r.ok is False and r.error                     # no raise -> the panel renders the ✗

def test_personas_page_exposes_composed_instruction(tmp_path):
    from fanops.studio import views
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="a devoted fan", content_focus=["hype"], energy="high")
    card = next(c for c in views.personas_page(cfg).personas if c.id == "p")
    assert card.instruction.startswith("a devoted fan") and "hype moments" in card.instruction   # voice + substantive clip-for
    assert card.content_focus == ["hype"] and card.energy == "high"

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

def test_compose_appends_locked_brief_after_voice():
    out = compose_persona_instruction(Persona(id="p", voice="a devoted fan", brief="Clip the lyrical moments; reach heads who care about wordplay."))
    assert "a devoted fan" in out and "Clip the lyrical moments" in out
    assert out.index("a devoted fan") < out.index("Clip the lyrical moments")   # voice first, locked brief after

def test_compose_brief_with_levers_and_voice():
    out = compose_persona_instruction(Persona(id="p", voice="v", brief="B", content_focus=["hype"], energy="high"))
    assert out.startswith("v. B") and "hype moments" in out   # base (voice. brief) leads, then the substantive clip-for clause

def test_compose_brief_only():
    assert compose_persona_instruction(Persona(id="p", brief="just the locked strategy")) == "just the locked strategy"

def test_update_persona_brief_roundtrips_and_clears(tmp_path):
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v")
    update_persona(cfg, "p", brief="locked strategy text")
    assert Personas.load(cfg).get("p").brief == "locked strategy text"
    update_persona(cfg, "p", brief="")                          # blank CLEARS (authoritative form)
    assert Personas.load(cfg).get("p").brief == ""

def test_hydrate_brief_onto_linked_account(tmp_path):
    cfg = Config(root=tmp_path)
    _write(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
                  "persona_id": "curator"}],
           [{"id": "curator", "voice": "tasteful", "brief": "clip artistry; reach crate-diggers"}])
    a = next(x for x in Accounts.load(cfg).accounts if x.handle == "@a")
    assert "clip artistry" in compose_persona_instruction(a)    # the locked brief rides downstream via the account

def test_brief_drives_casting_payload(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
                       "persona": "bold fan", "persona_id": "p"}])
    cfg.personas_path.write_text(json.dumps({"personas": [
        {"id": "p", "voice": "bold fan", "brief": "clip the hardest punchlines for hype kids"}]}))
    request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    payload = json.loads(request_path(cfg, "moment_casting", "src_1").read_text())
    assert "clip the hardest punchlines" in payload["personas"][0]["persona"]


# ---- transparency — facts derived from the REAL resolvers (length band + lead tags) ----
def test_persona_facts_resolve_from_real_resolvers(tmp_path):
    from fanops.personas import persona_facts
    cfg = Config(root=tmp_path)
    f = persona_facts(cfg, Persona(id="p", clip_profile="short", framing="top", tag_lean="tasteful",
                                   hashtag_corpus=["#myscene"]))
    assert f["length_band"] == "8-15s"          # bands.band_for(short) == SHORT(8,15) — the SAME resolver the pipeline uses
    assert f["framing"] == "top"
    assert "#myscene" in f["lead_tags"]         # vet_hashtags floats the curated corpus to the lead

def test_persona_facts_default_length_when_unset(tmp_path):
    from fanops.personas import persona_facts
    cfg = Config(root=tmp_path)
    f = persona_facts(cfg, Persona(id="p", voice="v"))
    assert f["length_band"] == "12-22s" and f["framing"] is None     # band_for(None) -> TALK default (pipeline-faithful)

def test_personas_page_exposes_facts_and_brief(tmp_path):
    from fanops.studio import views
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v", clip_profile="long", brief="locked strategy")
    card = next(c for c in views.personas_page(cfg).personas if c.id == "p")
    assert card.length_band == "28-45s" and card.brief == "locked strategy"
    assert isinstance(card.lead_tags, list)

def test_personas_panel_renders_transparency_facts(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v", clip_profile="short")
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().get("/personas").get_data(as_text=True)
    assert "8-15s" in html                       # the resolved length band is shown (transparency)


# ======================================================================================
# M3 — THE DIRECTIVE ENGINE. Each structured lever compiles into a SUBSTANTIVE per-dimension
# instruction injected into THAT dimension's real prompt (not a glued adjective). The operator
# can OVERRIDE the compiled text per dimension. THE
# FIREWALL holds: no levers + no override -> the bare voice, byte-identical to today.
# ======================================================================================
from fanops.personas import casting_directive, hook_directive, caption_directive

def test_casting_directive_is_substantive_not_adjective():
    out = casting_directive(Persona(id="p", content_focus=["punchlines"], energy="high"))
    assert "punchline" in out and ("peak-intensity" in out or "skip calm" in out)
    assert "favors moments" not in out and "energy high" not in out      # the trivial phrasing is GONE

def test_hook_directive_compiles_angle_and_tone():
    out = hook_directive(Persona(id="p", hook_angle="curiosity", hook_tone="aggressive"))
    assert "curiosity gap" in out and ("hard" in out or "confrontational" in out)
    assert "hook angle" not in out                                        # substantive, not "hook angle curiosity"

def test_hook_directive_is_separate_from_casting():
    # the on-screen hook levers shape the HOOK prompt, NOT the casting prompt (per-dimension split)
    p = Persona(id="p", content_focus=["hype"], hook_angle="curiosity")
    assert "curiosity gap" in hook_directive(p) and "curiosity gap" not in casting_directive(p)
    assert "hype moments" in casting_directive(p) and "hype moments" not in hook_directive(p)

def test_directive_override_wins_verbatim():
    p = Persona(id="p", voice="ignored", content_focus=["hype"], casting_directive="ONLY clip the freestyle bars.")
    assert casting_directive(p) == "ONLY clip the freestyle bars."        # operator text wins, verbatim
    assert hook_directive(Persona(id="p", hook_directive="POV hooks only")) == "POV hooks only"
    assert caption_directive(Persona(id="p", caption_directive="hype-fan energy")) == "hype-fan energy"

def test_directives_firewall_to_bare_voice():
    p = Persona(id="p", voice="bold fan")                                 # no levers, no override
    assert casting_directive(p) == "bold fan" and hook_directive(p) == "bold fan" and caption_directive(p) == "bold fan"

def test_caption_directive_is_voice_or_override():
    assert caption_directive(Persona(id="p", voice="v", tag_lean="tasteful")) == "v"   # tags stay deterministic, not in the text

def test_directive_fields_roundtrip(tmp_path):
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v", casting_directive="cd", hook_directive="hd", caption_directive="capd")
    p = Personas.load(cfg).get("p")
    assert p.casting_directive == "cd" and p.hook_directive == "hd" and p.caption_directive == "capd"

def test_directive_override_hydrates_and_drives_hook_payload(tmp_path):
    # a linked persona's hook_directive override hydrates onto the account and drives the HOOK payload downstream
    cfg = Config(root=tmp_path)
    _write(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
                  "persona_id": "p"}],
           [{"id": "p", "voice": "v", "hook_directive": "always a POV hook"}])
    a = next(x for x in Accounts.load(cfg).accounts if x.handle == "@a")
    assert hook_directive(a) == "always a POV hook"                       # override hydrated + drives the hook prompt

def test_personas_panel_renders_directive_ui(tmp_path):
    # the per-persona UI: the three compiled directives show per dimension, the override editors render
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v", content_focus=["punchlines"], hook_angle="curiosity")
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().get("/personas").get_data(as_text=True)
    assert "hook &#8594;" in html or "hook →" in html or "hook →" in html   # per-dimension directive shown (clips/hook/caption)
    assert 'name="casting_directive"' in html and 'name="hook_directive"' in html  # the override editors

def test_studio_edit_persona_persists_directives(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v")
    r = sp.edit_persona(cfg, "p", name="P", voice="v", casting_directive="only freestyles",
                        hook_directive="POV only")
    assert r.ok
    p = Personas.load(cfg).get("p")
    assert p.casting_directive == "only freestyles" and p.hook_directive == "POV only"
