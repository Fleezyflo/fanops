# tests/test_persona_directives.py — MOL-171
from fanops.personas import Persona, casting_directive, hook_directive
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
    assert d.select_rule and d.scope_lens and d.register == "a devoted fan"
    assert hook_directive(p).mechanism_lean

def test_every_string_consumer_still_works():
    p = Persona(id="p", voice="bold", cut_policy=["punchlines"], hook_angle="curiosity gap")
    assert str(casting_directive(p)) and str(hook_directive(p))
