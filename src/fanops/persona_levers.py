# src/fanops/persona_levers.py
"""THE single lever registry (M1) — one ordered declaration per persona lever, the UPSTREAM of the three
projections that used to be separate literals synced by a manual parity promise: the validation vocabularies
(personas.CONTENT_FOCUS / SELECTION_SCOPE_LEVELS / HOOK_ANGLES), the compile + derived-cut clause maps
(persona_directives._FOCUS_CLAUSE / _SCOPE_CLAUSE / _ANGLE_CLAUSE / _FOCUS_PROFILE / _FRAMING_MAP), and the
operator catalog (lever_catalog). Adding/removing a lever or option is ONE edit here; the projections derive,
so the three can no longer drift. PURE LEAF — stdlib only at module load; `bands` is imported LAZILY inside
build_catalog exactly as lever_catalog() did. NEVER imports personas/accounts/config (one-way: this <- those)."""
from __future__ import annotations

# Profile tiers, LONGEST-bias first — the derived-cut selection picks the HIGHEST tier present (a multi-focus
# persona derives deterministically: story+punchlines -> long). Rank 0 = longest. The selection reads this
# order, NOT the option order, so the longer-bias-first cut behavior is order-decoupled (the M1 GOTCHA).
PROFILE_TIERS = ["long", "medium", "short"]
INTENSITY_TIERS = ["high", "medium", "low"]

# Each content_focus option: value + casting CLAUSE + cut LENGTH tier + FRAMING.
# Intensity is a FIRST-CLASS persona field (MOL-520 / E-1) — no longer fused onto these tokens.
# Closed ordered scale for filter_peaks_by_intensity (P4b) — not a taste taxonomy.
# MOL-170 highest-intensity-first framing order (was option.intensity); kept until E-3 removes framing_map.
_FRAMING_PRIORITY = ("punchlines", "hype", "bold-statement", "visual", "emotional", "storytelling")
# clip_profile: the GLOBAL deterministic cut-length lever (Go-Live default) — catalog-only (no per-persona
# vocab/clause; per persona the length is DERIVED from content_focus). Options are band names; the catalog
# effect is computed from bands.band_for (lazy). niche: free text (declared subject terms), no enumerated options.


# -------------------------------------------------------------------------------------------------------------
# M2 COHERENCE FACETS — the model-FIELD coherence declaration the fail-closed guard reads. This is a SEPARATE
# namespace from LEVER_REGISTRY on purpose: LEVER_REGISTRY's keys are the editor CATALOG levers (incl. the
# GLOBAL `clip_profile` band lever), whereas the guard reasons about PERSONA MODEL FIELDS. Conflating them is
# the exact over-claim trap (the catalog's global `clip_profile` is NOT the persona `clip_profile` pin). So
# EDITABILITY here is defined as "the persona save route persists this field" — kept honest by the behavioral
# editor-parity test — NOT by catalog-key presence.
# Identity + DERIVED state. `hashtag_corpus` is not a lever any more: it is recomputed every tick from
# platform measurements (persona_research.derive_corpus), so it has — and should have — no editor control.
PERSONA_FIELD_EXEMPT = frozenset({"id", "name", "hashtag_corpus"})

# The EDITABLE coherent levers: model field -> the output CHANNEL(s) it owns. Distinctness rule = "<=1 owner per
# channel". content_focus owns casting-selection + cut-length + cut-framing; selection_scope owns casting-
# selection-scope. `voice` owns the freeform register (the base of all three directives, modeled as its own channel).
PERSONA_EDITABLE_CHANNELS = {
    "voice": ("voice",),
    "intensity": ("peak-filter",),
    "selection_scope": ("casting-selection-scope",),
    "hook_angle": ("hook-angle",),
    # niche owns the hashtags editor channel — persona_terms returns niche ONLY (MOL-637).
    "niche": ("hashtags",),
}


def editable_fields() -> frozenset:
    """The persona model fields the save route persists (the coherent editable lever set)."""
    return frozenset(PERSONA_EDITABLE_CHANNELS)


def channels_of(field: str) -> tuple:
    """The output channel(s) an editable lever owns (() for a non-editable field)."""
    return PERSONA_EDITABLE_CHANNELS.get(field, ())


def all_channels() -> frozenset:
    """Every output channel owned by an editable lever — the distinctness namespace."""
    return frozenset(ch for chans in PERSONA_EDITABLE_CHANNELS.values() for ch in chans)


def owner_of(channel: str) -> str | None:
    """The single editable lever that owns an output channel (None if no lever owns it). Distinctness
    guarantees at most one owner, so this is unambiguous — the manifest maps fragment->channel->lever."""
    return next((f for f, chans in PERSONA_EDITABLE_CHANNELS.items() if channel in chans), None)






