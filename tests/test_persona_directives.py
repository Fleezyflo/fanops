# tests/test_persona_directives.py — MOL-171
from fanops.personas import Persona, casting_directive, hook_directive
from fanops.persona_directives import Directive, _FOCUS_CLAUSE, _SCOPE_CLAUSE, _base_voice, _join

def _snap_casting(p):
    parts = []
    foc = [_FOCUS_CLAUSE[c] for c in (p.content_focus or []) if c in _FOCUS_CLAUSE]
    if foc: parts.append("Clip for this account: " + "; ".join(foc) + ".")
    sc = _SCOPE_CLAUSE.get((p.selection_scope or "").strip().lower(), "")
    if sc: parts.append(sc)
    return _join(_base_voice(p), " ".join(parts).strip())

def test_directive_str_is_byte_identical_to_today():
    for p in [Persona(id="bare", voice="bold fan"),
              Persona(id="foc", voice="a devoted fan", content_focus=["punchlines", "hype"]),
              Persona(id="scope", voice="v", content_focus=["storytelling"], selection_scope="credibility_first")]:
        d = casting_directive(p)
        assert isinstance(d, Directive) and str(d) == _snap_casting(p)

def test_directive_exposes_structured_fields():
    p = Persona(id="p", voice="a devoted fan", content_focus=["punchlines"],
                selection_scope="credibility_first", hook_angle="curiosity")
    d = casting_directive(p)
    assert d.select_rule and d.scope_lens and d.register == "a devoted fan"
    assert hook_directive(p).mechanism_lean

def test_scope_lens_from_selection_scope():
    d = casting_directive(Persona(id="p", voice="v", selection_scope="credibility_first"))
    assert "sensational" in d.scope_lens.lower() or "accurate" in d.scope_lens.lower()

def test_every_string_consumer_still_works():
    p = Persona(id="p", voice="bold", content_focus=["punchlines"], hook_angle="curiosity")
    assert str(casting_directive(p)) and str(hook_directive(p))
