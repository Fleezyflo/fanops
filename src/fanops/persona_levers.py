# src/fanops/persona_levers.py
"""THE single lever registry (M1) — one ordered declaration per persona lever, the UPSTREAM of the three
projections that used to be separate literals synced by a manual parity promise: the validation vocabularies
(personas.CONTENT_FOCUS / SELECTION_SCOPE_LEVELS / HOOK_ANGLES), the compile + derived-cut clause maps
(persona_directives._FOCUS_CLAUSE / _SCOPE_CLAUSE / _ANGLE_CLAUSE / _FOCUS_PROFILE / _FRAMING_MAP), and the
operator catalog (lever_catalog). Adding/removing a lever or option is ONE edit here; the projections derive,
so the three can no longer drift. PURE LEAF — stdlib only at module load; `bands` is imported LAZILY inside
build_catalog exactly as lever_catalog() did. NEVER imports personas/accounts/config (one-way: this <- those)."""
from __future__ import annotations
from collections import OrderedDict

# Profile tiers, LONGEST-bias first — the derived-cut selection picks the HIGHEST tier present (a multi-focus
# persona derives deterministically: story+punchlines -> long). Rank 0 = longest. The selection reads this
# order, NOT the option order, so the longer-bias-first cut behavior is order-decoupled (the M1 GOTCHA).
PROFILE_TIERS = ["long", "medium", "short"]
INTENSITY_TIERS = ["high", "medium", "low"]

# Each content_focus option: value + casting CLAUSE + cut LENGTH tier + FRAMING + INTENSITY (MOL-170).
_CONTENT_FOCUS_OPTIONS = [
    {"value": "punchlines", "profile": "short", "framing": "center", "intensity": "high",
     "clause": "moments that land a verbal punchline — a bar with a clear setup and payoff, a quotable, rewatchable line"},
    {"value": "emotional", "profile": "medium", "framing": "top", "intensity": "low",
     "clause": "moments carrying real emotion — vulnerability, longing, devotion, a confession the viewer feels"},
    {"value": "hype", "profile": "short", "framing": "center", "intensity": "high",
     "clause": "the highest-energy hype moments — the hardest delivery, the beat drop, the room going up"},
    {"value": "storytelling", "profile": "long", "framing": "top", "intensity": "low",
     "clause": "moments that tell a story or reveal something — an origin, a turn, a payoff"},
    {"value": "visual", "profile": "medium", "framing": "center", "intensity": "medium",
     "clause": "visually arresting moments — a strong scene, motion, or setting, not audio alone"},
    {"value": "bold-statement", "profile": "short", "framing": "center", "intensity": "high",
     "clause": "a bold or contrarian statement that stops the scroll"},
]
_SELECTION_SCOPE_OPTIONS = [
    {"value": "open", "clause": ""},
    {"value": "subject_locked", "clause": "Only moments featuring the account's named subject qualify — subject presence is the filter."},
    {"value": "source_briefed", "clause": "Select only moments matching the campaign brief — the brief defines footage and angle."},
    {"value": "credibility_first", "clause": "Favor clear and accurate over sensational; pass on cuts that misrepresent the source."},
    {"value": "controversy_seeking", "clause": "Prefer the most inflammatory or rivalry-coded statement in the source."},
]
_HOOK_ANGLE_OPTIONS = [
    {"value": "curiosity", "clause": "open a curiosity gap the viewer has to close"},
    {"value": "challenge", "clause": "dare or challenge the viewer to react"},
    {"value": "emotional", "clause": "name the high-arousal feeling the clip gives the viewer"},
    {"value": "result-first", "clause": "open on the payoff, then reveal how it got there"},
    {"value": "fomo", "clause": "carry genuine scarcity — a one-time, leaked, or unreleased drop"},
]
# clip_profile: the GLOBAL deterministic cut-length lever (Go-Live default) — catalog-only (no per-persona
# vocab/clause; per persona the length is DERIVED from content_focus). Options are band names; the catalog
# effect is computed from bands.band_for (lazy). hashtag_corpus: catalog-only, no enumerated options.
_CLIP_PROFILE_BANDS = ["short", "medium", "long", "talk", "song"]

_ARCHETYPE_SELECTION_SCOPE = {
    "single_source_briefed": "source_briefed",
    "single_subject_fan": "subject_locked",
    "multi_source_vibe_compilation": "open",
    "structural_cliffhanger": "open",
    "edutainment_niche": "open",
    "personality_banter": "open",
    "manufactured_controversy": "controversy_seeking",
    "credibility_first": "credibility_first",
    "gossip_drama_aggregator": "open",
    "opportunistic_broad_curator": "open",
}

LEVER_REGISTRY = [
    {"key": "content_focus", "label": "Clips · favors moments", "kind": "multi", "stage": "casting",
     "does": "which KINDS of moments this account clips for (casting prompt) — and DERIVES cut LENGTH + FRAMING",
     "options": _CONTENT_FOCUS_OPTIONS},
    {"key": "selection_scope", "label": "Selection scope", "kind": "select", "stage": "casting",
     "does": "the selection CONSTRAINT posture (open vs subject-locked vs briefed vs credibility-first vs controversy-seeking)",
     "options": _SELECTION_SCOPE_OPTIONS},
    {"key": "hook_angle", "label": "Hook angle", "kind": "select", "stage": "hook",
     "does": "the strategy of the burned on-screen hook (the register comes from the voice)",
     "options": _HOOK_ANGLE_OPTIONS},
    {"key": "clip_profile", "label": "Clip length", "kind": "select", "stage": "cut",
     "does": "the GLOBAL deterministic cut-length band (Go-Live default; per-persona it is derived from content_focus)",
     "options": [{"value": n} for n in _CLIP_PROFILE_BANDS]},
    {"key": "hashtag_corpus", "label": "Corpus", "kind": "tags", "stage": "caption",
     "does": "your curated tags LEAD the caption hashtags", "options": []},
]


# -------------------------------------------------------------------------------------------------------------
# M2 COHERENCE FACETS — the model-FIELD coherence declaration the fail-closed guard reads. This is a SEPARATE
# namespace from LEVER_REGISTRY on purpose: LEVER_REGISTRY's keys are the editor CATALOG levers (incl. the
# GLOBAL `clip_profile` band lever), whereas the guard reasons about PERSONA MODEL FIELDS. Conflating them is
# the exact over-claim trap (the catalog's global `clip_profile` is NOT the persona `clip_profile` pin). So
# EDITABILITY here is defined as "the persona save route persists this field" — kept honest by the behavioral
# editor-parity test — NOT by catalog-key presence.
PERSONA_FIELD_EXEMPT = frozenset({"id", "name", "intake"})   # identity / research-seed metadata, not a per-clip output lever

# The EDITABLE coherent levers: model field -> the output CHANNEL(s) it owns. Distinctness rule = "<=1 owner per
# channel". content_focus owns casting-selection + cut-length + cut-framing; selection_scope owns casting-
# selection-scope. `voice` owns the freeform register (the base of all three directives, modeled as its own channel).
PERSONA_EDITABLE_CHANNELS = {
    "voice": ("voice",),
    "content_focus": ("casting-selection", "cut-length", "cut-framing"),
    "selection_scope": ("casting-selection-scope",),
    "hook_angle": ("hook-angle",),
    "hashtag_corpus": ("hashtags",),
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


def lever(key: str) -> dict | None:
    """The registry descriptor for a lever key, or None."""
    return next((lv for lv in LEVER_REGISTRY if lv["key"] == key), None)


def option_values(key: str) -> list[str]:
    """The declared option values for a lever, in declaration order."""
    lv = lever(key)
    return [o["value"] for o in (lv["options"] if lv else [])]


def vocab(key: str) -> frozenset:
    """The validation frozenset for a lever (the write-boundary vocabulary)."""
    return frozenset(option_values(key))


def clause_map(key: str) -> dict:
    """{value: clause} for a lever whose options carry a compile clause (content_focus/selection_scope/hook_angle), in
    declaration order (the casting/hook join order). Options without a clause are skipped."""
    lv = lever(key)
    return {o["value"]: o["clause"] for o in (lv["options"] if lv else []) if "clause" in o}


def focus_profile_map() -> "OrderedDict[str, str]":
    """The derived-cut LENGTH map {content_focus: tier}, ordered LONGEST-tier-first so next() over it picks the
    highest tier present (the longer-bias-first selection). Built from each option's `profile` + PROFILE_TIERS,
    stable within a tier on declaration order — byte-identical to the legacy _FOCUS_PROFILE."""
    opts = lever("content_focus")["options"]
    out: "OrderedDict[str, str]" = OrderedDict()
    for tier in PROFILE_TIERS:
        for o in opts:
            if o.get("profile") == tier:
                out[o["value"]] = tier
    return out


def framing_map() -> "OrderedDict[str, str]":
    """The derived-cut FRAMING map {content_focus: framing}, ordered HIGHEST-intensity-first so next() over it
    picks the highest-intensity present focus (MOL-170 — replaces energy_framing_map)."""
    opts = lever("content_focus")["options"]
    out: "OrderedDict[str, str]" = OrderedDict()
    for tier in INTENSITY_TIERS:
        for o in opts:
            if o.get("intensity") == tier and o.get("framing"):
                out[o["value"]] = o["framing"]
    return out


def intensity_map() -> "OrderedDict[str, str]":
    """{content_focus: intensity tier}, ordered HIGHEST-intensity-first (P4b peak filter reads this)."""
    opts = lever("content_focus")["options"]
    out: "OrderedDict[str, str]" = OrderedDict()
    for tier in INTENSITY_TIERS:
        for o in opts:
            if o.get("intensity") == tier:
                out[o["value"]] = tier
    return out


def derive_intensity_from_focus(content_focus: list[str] | None) -> str | None:
    """The peak-filter intensity for a persona's content_focus — highest-intensity present wins; []/unknown -> None."""
    im = intensity_map()
    for tier in INTENSITY_TIERS:
        for f in (content_focus or []):
            if im.get(f) == tier:
                return tier
    return None


def archetype_selection_scope_map() -> dict:
    """The 10 clipping_account_archetypes.json type ids -> selection_scope lever value (MOL-170)."""
    return dict(_ARCHETYPE_SELECTION_SCOPE)


def build_catalog() -> list[dict]:
    """lever_catalog()'s body, DERIVED from the registry. content_focus/hook_angle render value+effect from the
    clause; selection_scope renders open's empty clause as the explicit no-op note; clip_profile renders the band
    note; clip_profile renders the band ranges from bands.band_for (lazy, as the legacy catalog did);
    hashtag_corpus has no options. Each lever: {key, label, kind, stage, does, options:[{value, effect}]}."""
    from fanops.bands import band_for
    out: list[dict] = []
    for lv in LEVER_REGISTRY:
        if lv["key"] == "selection_scope":
            opts = [{"value": o["value"], "effect": (o["clause"] or "no change — open selection")} for o in lv["options"]]
        elif lv["key"] == "clip_profile":
            opts = [{"value": o["value"], "effect": f"{band_for(o['value']).lo:g}-{band_for(o['value']).hi:g}s cuts"} for o in lv["options"]]
        elif lv["key"] == "hashtag_corpus":
            opts = []
        else:
            opts = [{"value": o["value"], "effect": o["clause"]} for o in lv["options"]]
        out.append({"key": lv["key"], "label": lv["label"], "kind": lv["kind"], "stage": lv["stage"],
                    "does": lv["does"], "options": opts})
    return out
