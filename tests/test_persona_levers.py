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

def test_compose_levers_only_renders_body():
    out = compose_persona_instruction(Persona(id="p", content_focus=["punchlines", "emotional"], energy="high",
                                              hook_angle="curiosity", hook_tone="aggressive"))
    assert "favors moments: punchlines, emotional" in out
    assert "energy high" in out and "hook angle curiosity" in out and "hook tone aggressive" in out

def test_compose_both_body_then_voice():
    out = compose_persona_instruction(Persona(id="p", voice="a devoted fan", content_focus=["hype"], energy="high"))
    assert out.endswith(". a devoted fan") and out.startswith("favors moments: hype")

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
    assert payload["personas"] == [{"handle": "@a", "persona": "bold fan"}]    # composed == raw voice

def test_casting_payload_carries_lever_direction(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
                       "persona": "bold fan", "persona_id": "p"}])
    cfg.personas_path.write_text(json.dumps({"personas": [
        {"id": "p", "voice": "bold fan", "content_focus": ["punchlines"], "energy": "high"}]}))
    request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    payload = json.loads(request_path(cfg, "moment_casting", "src_1").read_text())
    persona_str = payload["personas"][0]["persona"]
    assert "favors moments: punchlines" in persona_str and "energy high" in persona_str


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
    assert "favors moments: hype" in card.instruction and card.instruction.endswith(". a devoted fan")
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
    assert out.startswith("favors moments: hype") and out.endswith("v. B")   # body. voice. brief

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

def test_lock_brief_drives_casting_payload(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
                       "persona": "bold fan", "persona_id": "p"}])
    cfg.personas_path.write_text(json.dumps({"personas": [
        {"id": "p", "voice": "bold fan", "brief": "clip the hardest punchlines for hype kids"}]}))
    request_moment_casting(led, cfg, "src_1", Accounts.load(cfg))
    payload = json.loads(request_path(cfg, "moment_casting", "src_1").read_text())
    assert "clip the hardest punchlines" in payload["personas"][0]["persona"]


# ---- Task 6: live strategy check — injected model, fail-open, writes NOTHING ----
def _fixture_strategy(prompt, schema):
    return {"clipping_objective": "cut the wordplay-dense bars", "hook_objective": "curiosity on the punchline",
            "caption_objective": "lyrical-scene hashtags", "audience": "hip-hop heads who replay bars",
            "strategy": "lean into craft, not hype"}

def test_persona_strategy_prompt_briefs_the_agent():
    from fanops.prompts import persona_strategy_prompt
    out = persona_strategy_prompt({"project": "FanOps brief here", "persona": "favors moments: punchlines. a devoted fan",
                                   "facts": {"length_band": "8-15s", "framing": "top", "lead_tags": ["#bars"]}})
    low = out.lower()
    assert "fan account" in low and "same" in low and "different" in low      # the repost-to-different-audiences framing
    assert "hook" in low and "caption" in low and "moment" in low             # the three downstream jobs
    assert "favors moments: punchlines. a devoted fan" in out                 # THIS persona's composed instruction

def test_persona_strategy_renders_objectives(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="Curator", voice="tasteful crate-digger", clip_profile="short")
    r = sp.persona_strategy(cfg, "curator", model=_fixture_strategy)
    assert r.ok and r.detail["strategy"]["clipping_objective"] == "cut the wordplay-dense bars"
    assert r.detail["brief"]                                                   # a composed lock-ready brief string is offered

def test_persona_strategy_does_not_autowrite_brief(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="Curator", voice="v")
    sp.persona_strategy(cfg, "curator", model=_fixture_strategy)              # SEE, don't lock
    assert Personas.load(cfg).get("curator").brief == ""                      # the strategy is NEVER auto-written

def test_persona_strategy_fail_open_on_llm_error(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="Curator", voice="v")
    def _boom(prompt, schema): raise RuntimeError("claude exploded")
    r = sp.persona_strategy(cfg, "curator", model=_boom)
    assert r.ok is False and r.error                                          # fail-open notice, no 500
    assert Personas.load(cfg).get("curator").brief == ""                      # nothing written

def test_persona_strategy_unknown_persona_is_clean_error(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    called = []
    r = sp.persona_strategy(cfg, "nope", model=lambda p, s: called.append(1) or {})
    assert r.ok is False and r.error and not called                           # rejected before any model call

def test_lock_brief_persists(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="Curator", voice="v")
    r = sp.lock_brief(cfg, "curator", "the operator-approved strategy")
    assert r.ok and Personas.load(cfg).get("curator").brief == "the operator-approved strategy"

def test_lock_brief_unknown_persona_is_clean_error(tmp_path):
    from fanops.studio import personas as sp
    cfg = Config(root=tmp_path)
    r = sp.lock_brief(cfg, "nope", "x")
    assert r.ok is False and r.error


# ---- Task 8: transparency — facts derived from the REAL resolvers (length band + lead tags) ----
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

def test_personas_panel_renders_strategy_and_lock_controls(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v", clip_profile="short")
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().get("/personas").get_data(as_text=True)
    assert "/personas/strategy" in html          # the [Strategy check] control is on the card (rendered action URL)
    assert "8-15s" in html                       # the resolved length band is shown (transparency)

def test_strategy_route_renders_objectives_and_lock_form(tmp_path, monkeypatch):
    # Drive the FULL HTTP -> handler -> template path with claude stubbed, so the strategy-RESULT block
    # (objectives + lock form) is actually rendered (the GET test only hits the always-on card controls).
    from fanops.studio.app import create_app
    import fanops.llm as llm
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v")
    monkeypatch.setattr(llm, "claude_json", _fixture_strategy)   # function-local import resolves this at call time
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().post("/personas/strategy", data={"id": "p"}).get_data(as_text=True)
    assert "cut the wordplay-dense bars" in html and "lean into craft, not hype" in html   # rendered objectives
    assert "/personas/lock" in html and "Lock as brief" in html                            # the lock form
    assert Personas.load(cfg).get("p").brief == ""                                          # SEE only — nothing locked yet

def test_lock_route_persists_and_card_shows_locked_brief(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v")
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().post("/personas/lock", data={"id": "p", "brief": "the approved strategy"}).get_data(as_text=True)
    assert "locked brief" in html and "the approved strategy" in html       # the card now shows the locked brief
    assert Personas.load(cfg).get("p").brief == "the approved strategy"
