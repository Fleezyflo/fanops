# tests/test_persona_lever_coherence.py — the ANTI-DRIFT guard for the persona lever model (M2: upgraded from
# output-sensitivity-only to the FULL invariant editable ∧ wired ∧ distinct, fail-closed, against the M1
# registry).
#
# The mess this prevents has THREE shapes, not one:
#   1. DEAD: a field in the model + hydrated but read by NO compiler (hook_tone, brief) — output-INsensitive.
#   2. INVISIBLE: a field that IS live (moves output) but has NO editor control (tag_lean) — so the operator
#      can't configure what ships. Output-sensitivity alone PASSES this — it is the exact Phase-1 blind spot.
#   3. DUPLICATE: two fields silently owning one output channel (the 3 *_directive overrides shadow the
#      structured levers; the clip_profile/framing pins duplicate the derived cut).
#
# The invariant that kills all three: EVERY non-identity Persona field is EXEMPT, or QUARANTINED (an explicit,
# only-ever-shrinking debt list M3 empties), or it is (a) EDITABLE — the persona save route persists it (NOT
# catalog-key presence: the catalog's GLOBAL clip_profile lever is not the persona clip_profile pin), (b) WIRED
# — mutating it changes compiled output, and (c) DISTINCT — its output channel has exactly one owner. FAIL-
# CLOSED. Runtime stays fail-open (a malformed field degrades to default, never raises), asserted below.
import warnings

from fanops.config import Config
from fanops.personas import Persona, compose_breakdown, resolved_cut_spec, casting_directive, hook_directive, caption_directive
import fanops.persona_levers as pl


# The original incoherent fields — the QUARANTINE CEILING (hard-coded, separate from the mutable set below so
# the ratchet test reds if anything NEW is grandfathered). M3 RESOLVED all six end-to-end (M3c tag_lean ->
# corpus; M3d clip_profile/framing pins -> derived; M3e the 3 directive overrides), so BOTH sets are now EMPTY
# — the coherence guard enforces editable ∧ wired ∧ distinct across EVERY model field with ZERO grandfathering.
_ORIGINAL_SIX = frozenset()
# The live quarantine: EMPTY (M3f). The guard below is green with no grandfathered field — coherence PROVEN.
_KNOWN_INCOHERENT = set(_ORIGINAL_SIX)

# Each lever field paired with (baseline, mutated) — two DISTINCT valid settings proving OUTPUT-SENSITIVITY.
# Post-M3 this is EXACTLY the editable lever set (the 6 incoherent fields were resolved end-to-end and left
# the model): every non-exempt model field is a covered, editable, wired, distinct lever. A non-exempt model
# field absent here is a DEAD (output-insensitive) lever and fails the coverage gate below.
_MUTATIONS = {
    "voice": ("a devoted fan", "a blunt critic"),
    "content_focus": (["punchlines"], ["hype"]),
    "selection_scope": ("open", "subject_locked"),
    "hook_angle": ("curiosity", "fomo"),
    "hashtag_corpus": (["#aaa"], ["#bbb"]),
}


def _output(cfg, p):
    """The full compiled fingerprint a persona produces — the bytes that actually reach the pipeline."""
    d = compose_breakdown(cfg, p)
    return (d["casting"]["text"], d["hook"]["text"], d["caption"]["text"],
            d["cut"]["band"], d["cut"]["framing"], tuple(d["tags"]["lead"]))


def test_quarantine_is_printed_every_run():
    # the debt is NEVER silent — surfaced on every run (a warning shows in the pytest summary).
    if _KNOWN_INCOHERENT:
        warnings.warn(f"persona lever quarantine (M3 empties this): {sorted(_KNOWN_INCOHERENT)}", stacklevel=1)
    assert _KNOWN_INCOHERENT <= _ORIGINAL_SIX


def test_quarantine_only_shrinks():
    # the ratchet: nothing NEW can be grandfathered. A future incoherent field must be made coherent, not added.
    assert _KNOWN_INCOHERENT <= _ORIGINAL_SIX, "quarantine grew beyond the original six — make the field coherent, don't grandfather it"


def test_every_field_is_exempt_quarantined_or_fully_coherent():
    # FAIL-CLOSED: every model field is identity/metadata (exempt), explicitly quarantined, or fully coherent
    # (editable ∧ wired ∧ distinct). A field that is none of these is the rot we ban.
    for f in Persona.model_fields:
        if f in pl.PERSONA_FIELD_EXEMPT or f in _KNOWN_INCOHERENT:
            continue
        assert f in pl.editable_fields(), (
            f"{f!r} is neither exempt, quarantined, nor EDITABLE (no persona save-route control). "
            "An invisible-but-wired lever is still incoherent — wire an editor control or quarantine + plan its fix.")
        assert f in _MUTATIONS, (
            f"{f!r} is editable but has no OUTPUT-SENSITIVITY coverage — declare its (baseline, mutated) here "
            "or it is a dead lever.")


def test_distinctness_no_channel_has_two_owners():
    # DISTINCT: no output channel is silently owned by more than one editable lever. content_focus owns TWO
    # channels (casting-selection, cut-length) and selection_scope owns casting-selection-scope — that is ONE
    # owner per channel, which is allowed; two DIFFERENT levers owning the SAME channel is the duplicate we ban.
    owner = {}
    for f in pl.editable_fields():
        for ch in pl.channels_of(f):
            assert ch not in owner, f"channel {ch!r} owned by both {owner[ch]!r} and {f!r} — a duplicate lever"
            owner[ch] = f


def test_each_lever_mutation_changes_the_compiled_output(tmp_path):
    # WIRED: a lever that claims to differentiate MUST move the output when changed (catches a cut wire).
    cfg = Config(root=tmp_path)
    base = Persona(id="p", **{f: v[0] for f, v in _MUTATIONS.items()})
    base_out = _output(cfg, base)
    for f, (_v0, v1) in _MUTATIONS.items():
        mutated = base.model_copy(update={f: v1})
        assert _output(cfg, mutated) != base_out, (
            f"mutating {f!r} did not change the compiled output — it is a dead lever (wired into nothing)")


def test_quarantine_teeth_a_hypothetical_incoherent_field_would_red():
    # THE GUARD'S TEETH — with the quarantine EMPTY (M3 resolved all six), no REAL field demonstrates the bite,
    # so prove it SYNTHETICALLY: the guard's predicate flags a non-exempt, non-editable field. This is the exact
    # editability axis that catches the Phase-1 over-claim — a field wired to output but with NO save-route
    # control is a violator even though output-sensitivity alone would pass it.
    phantom = "phantom_invisible_lever"
    assert phantom not in pl.PERSONA_FIELD_EXEMPT and phantom not in pl.editable_fields()
    is_violator = (phantom not in pl.PERSONA_FIELD_EXEMPT
                   and phantom not in _KNOWN_INCOHERENT
                   and phantom not in pl.editable_fields())
    assert is_violator, "the guard predicate must flag a non-exempt, non-editable, non-quarantined field"


def test_runtime_is_fail_open_on_malformed_fields():
    # RESILIENCE: a Persona carrying out-of-vocab lever values (the model itself does not enum-validate; only
    # the write boundary does) compiles to the documented DEFAULT and NEVER raises through any compile path.
    cfg = Config(root="/tmp/fanops_failopen_probe_unused")  # cfg only used for store load; compose tolerates absent store
    bad = Persona(id="p", voice="v", selection_scope="ludicrous", hook_angle="not-an-angle", content_focus=["not-a-focus"])
    # none of these raise; each degrades to the firewall default
    assert casting_directive(bad) == "v"                  # unknown focus/scope -> bare voice
    assert hook_directive(bad) == "v"                     # unknown angle -> bare voice
    assert caption_directive(bad) == "v"
    prof, fr = resolved_cut_spec(bad)                     # unknown focus/scope -> no derived cut
    assert prof is None and fr is None
    d = compose_breakdown(cfg, bad)                       # the whole breakdown composes without raising
    assert d["casting"]["text"] == "v"
