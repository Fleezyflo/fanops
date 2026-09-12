# tests/test_persona_directives.py — MOL-171
from fanops.personas import Persona, casting_directive, hook_directive, baked_personas
from fanops.persona_directives import Directive, _FOCUS_CLAUSE, _base_voice, _join

def _snap_casting(p):
    # MOL-523: content_focus is editorial; cut_policy tokens compile to clauses.
    editorial = (p.content_focus or "").strip()
    foc = [_FOCUS_CLAUSE[c] for c in (p.cut_policy or []) if c in _FOCUS_CLAUSE]
    select_rule = ("; ".join(foc) + ".") if foc else ""
    parts = [x for x in (editorial, select_rule) if x]
    full_select = ("Clip for this account: " + " ".join(parts).strip()) if parts else ""
    scope = (p.selection_scope or "").strip()
    body_parts = [x for x in (full_select, scope) if x]
    return _join(_base_voice(p), " ".join(body_parts).strip())

def test_directive_str_is_byte_identical_to_today():
    for p in [Persona(id="bare", voice="bold fan"),
              Persona(id="foc", voice="a devoted fan", cut_policy=["punchlines", "hype"]),
              Persona(id="scope", voice="v", cut_policy=["storytelling"], selection_scope="Favor accuracy.")]:
        d = casting_directive(p)
        assert isinstance(d, Directive) and str(d) == _snap_casting(p)

def test_directive_exposes_structured_fields():
    p = Persona(id="p", voice="a devoted fan", cut_policy=["punchlines"],
                selection_scope="Favor accuracy.", hook_angle="curiosity gap")
    d = casting_directive(p)
    assert d.select_rule.startswith("Clip for this account:")
    assert d.scope_lens == "Favor accuracy."
    assert d.register == "a devoted fan"
    assert hook_directive(p).mechanism_lean == "curiosity gap"

def test_every_string_consumer_still_works():
    # compiled bodies must differ across archetypes (or carry a named clause) — not a truthy str()
    baked = baked_personas()
    casts = [str(casting_directive(p)) for p in baked]
    hooks = [str(hook_directive(p)) for p in baked]
    assert len(set(casts)) > 1
    assert len(set(hooks)) > 1
    punch = Persona(id="p", voice="bold", cut_policy=["punchlines"], hook_angle="curiosity gap")
    story = Persona(id="s", voice="bold", cut_policy=["storytelling"], hook_angle="name the payoff")
    assert "punchline" in str(casting_directive(punch))
    assert "story" in str(casting_directive(story))
    assert str(casting_directive(punch)) != str(casting_directive(story))
    assert "curiosity gap" in str(hook_directive(punch))
    assert "name the payoff" in str(hook_directive(story))
    assert str(hook_directive(punch)) != str(hook_directive(story))
