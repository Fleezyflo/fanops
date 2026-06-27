# tests/test_persona_lever_coherence.py — the ANTI-DRIFT guard for the persona lever model.
#
# The mess this prevents: a field can sit in the Persona model + get hydrated onto accounts while NO compiler
# reads it (hook_tone, brief) — a dead lever that silently stops differentiating. Or it stays live but loses
# its editor control. The structural invariant that kills this class of bug: EVERY non-identity Persona field
# must be OUTPUT-SENSITIVE — mutating it must change the compiled output (casting/hook/caption directive, cut,
# or lead hashtags). FAIL-CLOSED: a model field with no declared output-sensitivity coverage is itself a
# failure, so a future field that does nothing can't be added quietly. This is the council's guard: field
# existence ⇒ it changes output, enforced, not assumed.
from fanops.config import Config
from fanops.personas import Persona, compose_breakdown


# identity / metadata — legitimately NOT a per-clip output lever. id/name are identity; intake.genre seeds the
# hashtag RESEARCH stage (persona_research), never the per-clip compile, so it is exempt from this invariant.
_EXEMPT = {"id", "name", "intake"}

# Each lever field paired with (baseline, mutated) — two DISTINCT valid settings. The invariant: swapping
# baseline→mutated must change the compiled output. A field in the model but absent here (and not exempt) is a
# dead/undeclared lever and fails the coverage gate below. hook_tone + brief are deliberately ABSENT.
_MUTATIONS = {
    "voice": ("a devoted fan", "a blunt critic"),
    "content_focus": (["punchlines"], ["hype"]),
    "energy": ("high", "low"),
    "hook_angle": ("curiosity", "fomo"),
    "hashtag_corpus": (["#aaa"], ["#bbb"]),
    "tag_lean": ("tasteful", "bold"),
    "clip_profile": ("short", "long"),
    "framing": ("top", "center"),
    "casting_directive": ("", "ONLY clip the freestyle bars"),
    "hook_directive": ("", "POV hooks only"),
    "caption_directive": ("", "hype-fan caption energy"),
}


def _output(cfg, p):
    """The full compiled fingerprint a persona produces — the bytes that actually reach the pipeline."""
    d = compose_breakdown(cfg, p)
    return (d["casting"]["text"], d["hook"]["text"], d["caption"]["text"],
            d["cut"]["band"], d["cut"]["framing"], tuple(d["tags"]["lead"]))


def test_every_persona_field_has_output_sensitivity_coverage():
    # FAIL-CLOSED: every model field is either identity (exempt) or a declared, covered lever. A field that is
    # neither — present in the model but doing nothing provable — is the exact rot (hook_tone, brief) we ban.
    uncovered = set(Persona.model_fields) - _EXEMPT - set(_MUTATIONS)
    assert not uncovered, (
        f"persona model carries field(s) with no output effect (dead levers): {sorted(uncovered)} — "
        "retire them from the model or wire + declare their output-sensitivity here")


def test_each_lever_mutation_changes_the_compiled_output(tmp_path):
    # Parity: a lever that claims to differentiate MUST move the output when changed. Catches a field that is
    # declared a lever but whose wire was cut (compiles identically regardless of its value).
    cfg = Config(root=tmp_path)
    base = Persona(id="p", **{f: v[0] for f, v in _MUTATIONS.items()})
    base_out = _output(cfg, base)
    for f, (_v0, v1) in _MUTATIONS.items():
        mutated = base.model_copy(update={f: v1})
        assert _output(cfg, mutated) != base_out, (
            f"mutating {f!r} did not change the compiled output — it is a dead lever (wired into nothing)")
