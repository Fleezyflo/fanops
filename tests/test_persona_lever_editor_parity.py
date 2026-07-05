# tests/test_persona_lever_editor_parity.py — M2: NO LYING REGISTRY. The coherence guard trusts the registry's
# `editable` declaration; this proves that declaration is BEHAVIORALLY true — every field the registry marks
# editable actually persists through the persona save route (create/update), and every non-exempt, non-
# quarantine model field is in the editable set. This is the bridge the PRD's "editor save is test-reconciled
# (not registry-derived in M1)" rests on: if a future field is declared editable but the save route drops it,
# this reds; if a field is added to the model with no editor wire and no quarantine, the coverage assertion reds.
from fanops.config import Config
from fanops.personas import Persona, Personas, add_persona, update_persona, add_corpus_tag
import fanops.persona_levers as pl

# the guard's quarantine is now EMPTY (M3 resolved all six incoherent fields end-to-end). Every model field is
# editable or exempt — nothing grandfathered.
_QUARANTINE = set()


def test_every_editable_field_persists_through_the_save_route(tmp_path):
    # behavioral proof, field by field: set it via the real writer, reload from disk, assert it stuck.
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="champions craft", content_focus=["punchlines", "hype"],
                selection_scope="controversy_seeking", hook_angle="curiosity")
    add_corpus_tag(cfg, "p", "#myscene")
    p = Personas.load(cfg).get("p")
    persisted = {
        "voice": p.voice == "champions craft",
        "content_focus": p.content_focus == ["punchlines", "hype"],
        "selection_scope": p.selection_scope == "controversy_seeking",
        "hook_angle": p.hook_angle == "curiosity",
        "hashtag_corpus": "#myscene" in p.hashtag_corpus,
    }
    for field in pl.editable_fields():
        assert persisted.get(field), f"registry marks {field!r} editable but it did NOT persist through the save route"


def test_update_route_also_persists_each_editable_field(tmp_path):
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="v")
    update_persona(cfg, "p", voice="changed", content_focus=["storytelling"], selection_scope="subject_locked", hook_angle="fomo")
    p = Personas.load(cfg).get("p")
    assert p.voice == "changed" and p.content_focus == ["storytelling"]
    assert p.selection_scope == "subject_locked" and p.hook_angle == "fomo"


def test_no_model_field_escapes_the_editable_exempt_or_quarantine_partition():
    # the partition is total: every model field is editable, exempt, or quarantined — nothing falls through.
    for f in Persona.model_fields:
        assert (f in pl.editable_fields() or f in pl.PERSONA_FIELD_EXEMPT or f in _QUARANTINE), (
            f"{f!r} is in none of editable/exempt/quarantine — the coherence partition has a hole")


def test_quarantined_fields_are_not_in_the_editable_set():
    # the quarantined fields must NOT claim editability (that would mask the incoherence the guard exists to catch).
    assert pl.editable_fields().isdisjoint(_QUARANTINE)


def test_editable_set_is_exactly_the_five_clean_levers():
    # pin the editable set so an accidental widening (e.g. re-admitting tag_lean as "editable") reds here.
    assert set(pl.editable_fields()) == {"voice", "content_focus", "selection_scope", "hook_angle", "hashtag_corpus"}
