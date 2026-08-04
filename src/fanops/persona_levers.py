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

# Each content_focus option: value + casting CLAUSE + cut LENGTH tier + FRAMING.
# Intensity is a FIRST-CLASS persona field (MOL-520 / E-1) — no longer fused onto these tokens.
_CONTENT_FOCUS_OPTIONS = [
    {"value": "punchlines", "profile": "short", "framing": "center",
     "clause": "moments that land a verbal punchline — a bar with a clear setup and payoff, a quotable, rewatchable line"},
    {"value": "emotional", "profile": "medium", "framing": "top",
     "clause": "moments carrying real emotion — vulnerability, longing, devotion, a confession the viewer feels"},
    {"value": "hype", "profile": "short", "framing": "center",
     "clause": "the highest-energy hype moments — the hardest delivery, the beat drop, the room going up"},
    {"value": "storytelling", "profile": "long", "framing": "top",
     "clause": "moments that tell a story or reveal something — an origin, a turn, a payoff"},
    {"value": "visual", "profile": "medium", "framing": "center",
     "clause": "visually arresting moments — a strong scene, motion, or setting, not audio alone"},
    {"value": "bold-statement", "profile": "short", "framing": "center",
     "clause": "a bold or contrarian statement that stops the scroll"},
]
# Closed ordered scale for filter_peaks_by_intensity (P4b) — not a taste taxonomy.
_INTENSITY_OPTIONS = [
    {"value": "high", "clause": "keep the loudest tercile of peak scores"},
    {"value": "medium", "clause": "no peak filter — the full set stands"},
    {"value": "low", "clause": "keep the calmest tercile of peak scores"},
]
# MOL-170 highest-intensity-first framing order (was option.intensity); kept until E-3 removes framing_map.
_FRAMING_PRIORITY = ("punchlines", "hype", "bold-statement", "visual", "emotional", "storytelling")
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
# effect is computed from bands.band_for (lazy). niche: free text (declared subject terms), no enumerated options.
_CLIP_PROFILE_BANDS = ["short", "medium", "long", "talk", "song"]

LEVER_REGISTRY = [
    {"key": "content_focus", "label": "Editorial focus", "kind": "text", "stage": "casting",
     "does": "free-text editorial focus for moment selection (formerly the multi-select tokens)",
     "options": []},
    {"key": "cut_policy", "label": "Cut policy", "kind": "multi", "stage": "cut",
     "does": "deterministic cut LENGTH + FRAMING derived from moment kinds (MOL-523)",
     "options": _CONTENT_FOCUS_OPTIONS},
    {"key": "intensity", "label": "Peak intensity", "kind": "select", "stage": "pick",
     "does": "which tercile of signal peaks survive the P4b filter (high/medium/low); unset = no filter",
     "options": _INTENSITY_OPTIONS},
    {"key": "selection_scope", "label": "Selection scope", "kind": "text", "stage": "casting",
     "does": "the selection CONSTRAINT posture (free text; formerly open vs subject-locked vs briefed vs credibility-first vs controversy-seeking)",
     "options": []},
    {"key": "hook_angle", "label": "Hook angle", "kind": "text", "stage": "hook",
     "does": "the strategy of the burned on-screen hook (free text; formerly curiosity vs challenge vs emotional vs result-first vs fomo)",
     "options": []},
    {"key": "clip_profile", "label": "Clip length", "kind": "select", "stage": "cut",
     "does": "the GLOBAL deterministic cut-length band (Go-Live default; per-persona it is derived from cut_policy)",
     "options": [{"value": n} for n in _CLIP_PROFILE_BANDS]},
    {"key": "niche", "label": "Territory seeds", "kind": "tags", "stage": "caption",
     "does": "declared subject terms — the ONLY Layer A hashtag search roots (MOL-637); voice/levers "
             "stay on captions+hooks, not discovery", "options": []},
]


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
# channel". content_focus owns casting-selection; cut_policy owns cut-length + cut-framing; selection_scope owns
# casting-selection-scope. `voice` owns the freeform register (the base of all three directives).
PERSONA_EDITABLE_CHANNELS = {
    "voice": ("voice",),
    "content_focus": ("casting-selection",),
    "cut_policy": ("cut-length", "cut-framing"),
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
    """The derived-cut LENGTH map {cut_policy: tier}, ordered LONGEST-tier-first so next() over it picks the
    highest tier present (the longer-bias-first selection). Built from each option's `profile` + PROFILE_TIERS,
    stable within a tier on declaration order — byte-identical to the legacy _FOCUS_PROFILE."""
    lv = lever("cut_policy")
    opts = lv["options"] if lv else []
    out: "OrderedDict[str, str]" = OrderedDict()
    for tier in PROFILE_TIERS:
        for o in opts:
            if o.get("profile") == tier:
                out[o["value"]] = tier
    return out


def framing_map() -> "OrderedDict[str, str]":
    """The derived-cut FRAMING map {cut_policy: framing}, ordered highest-intensity-first (MOL-170).
    Order is `_FRAMING_PRIORITY` (the former option.intensity tiers) so next() over it still picks the
    highest-intensity present focus after intensity left the option dict (MOL-520)."""
    lv = lever("cut_policy")
    opts_list = lv["options"] if lv else []
    opts = {o["value"]: o for o in opts_list}
    out: "OrderedDict[str, str]" = OrderedDict()
    for v in _FRAMING_PRIORITY:
        o = opts.get(v)
        if o and o.get("framing"):
            out[v] = o["framing"]
    for o in opts_list:
        if o["value"] not in out and o.get("framing"):
            out[o["value"]] = o["framing"]
    return out


def build_catalog() -> list[dict]:
    """lever_catalog()'s body, DERIVED from the registry. content_focus/hook_angle render value+effect from the
    clause; selection_scope renders open's empty clause as the explicit no-op note; clip_profile renders the band
    note; clip_profile renders the band ranges from bands.band_for (lazy, as the legacy catalog did);
    hashtag_corpus has no options. Each lever: {key, label, kind, stage, does, options:[{value, effect}]}."""
    from fanops.bands import band_for
    out: list[dict] = []
    for lv in LEVER_REGISTRY:
        if lv["kind"] == "text":
            opts = []          # free text, no enumerated options
        elif lv["key"] == "selection_scope":
            opts = [{"value": o["value"], "effect": (o["clause"] or "no change — open selection")} for o in lv["options"]]
        elif lv["key"] == "clip_profile":
            opts = [{"value": o["value"], "effect": f"{band_for(o['value']).lo:g}-{band_for(o['value']).hi:g}s cuts"} for o in lv["options"]]
        elif lv["key"] == "niche":
            opts = []          # free text, not an enum
        else:
            opts = [{"value": o["value"], "effect": o["clause"]} for o in lv["options"]]
        out.append({"key": lv["key"], "label": lv["label"], "kind": lv["kind"], "stage": lv["stage"],
                    "does": lv["does"], "options": opts})
    return out
