# src/fanops/persona_levers.py
"""THE single lever registry (M1) — one ordered declaration per persona lever, the UPSTREAM of the three
projections that used to be separate literals synced by a manual parity promise: the validation vocabularies
(personas.CONTENT_FOCUS / ENERGY_LEVELS / HOOK_ANGLES), the compile + derived-cut clause maps
(persona_directives._FOCUS_CLAUSE / _ENERGY_CLAUSE / _ANGLE_CLAUSE / _FOCUS_PROFILE / _ENERGY_FRAMING), and the
operator catalog (lever_catalog). Adding/removing a lever or option is ONE edit here; the projections derive,
so the three can no longer drift. PURE LEAF — stdlib only at module load; `bands` is imported LAZILY inside
build_catalog exactly as lever_catalog() did. NEVER imports personas/accounts/config (one-way: this <- those)."""
from __future__ import annotations
from collections import OrderedDict

# Profile tiers, LONGEST-bias first — the derived-cut selection picks the HIGHEST tier present (a multi-focus
# persona derives deterministically: story+punchlines -> long). Rank 0 = longest. The selection reads this
# order, NOT the option order, so the longer-bias-first cut behavior is order-decoupled (the M1 GOTCHA).
PROFILE_TIERS = ["long", "medium", "short"]

# Each content_focus option: value + the casting CLAUSE (== the catalog effect, what the compiler injects) +
# the cut LENGTH tier it derives. Declaration order IS the casting join order + the vocab + the catalog order.
_CONTENT_FOCUS_OPTIONS = [
    {"value": "punchlines", "profile": "short", "clause": "moments that land a verbal punchline — a bar with a clear setup and payoff, a quotable, rewatchable line"},
    {"value": "emotional", "profile": "medium", "clause": "moments carrying real emotion — vulnerability, longing, devotion, a confession the viewer feels"},
    {"value": "hype", "profile": "short", "clause": "the highest-energy hype moments — the hardest delivery, the beat drop, the room going up"},
    {"value": "storytelling", "profile": "long", "clause": "moments that tell a story or reveal something — an origin, a turn, a payoff"},
    {"value": "visual", "profile": "medium", "clause": "visually arresting moments — a strong scene, motion, or setting, not audio alone"},
    {"value": "bold-statement", "profile": "short", "clause": "a bold or contrarian statement that stops the scroll"},
]
# energy: the casting CLAUSE (medium = "" no-op) + the FRAMING it derives (medium -> None -> global crop).
_ENERGY_OPTIONS = [
    {"value": "low", "framing": "top", "clause": "Favor calmer, more introspective moments over loud ones."},
    {"value": "medium", "framing": None, "clause": ""},
    {"value": "high", "framing": "center", "clause": "Strongly prefer peak-intensity moments; skip calm, low-energy passages."},
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

LEVER_REGISTRY = [
    {"key": "content_focus", "label": "Clips · favors moments", "kind": "multi", "stage": "casting",
     "does": "which KINDS of moments this account clips for (casting prompt) — and DERIVES the cut LENGTH",
     "options": _CONTENT_FOCUS_OPTIONS},
    {"key": "energy", "label": "Energy", "kind": "select", "stage": "casting",
     "does": "biases moment selection toward calm or peak-intensity",
     "options": _ENERGY_OPTIONS},
    {"key": "hook_angle", "label": "Hook angle", "kind": "select", "stage": "hook",
     "does": "the strategy of the burned on-screen hook (the register comes from the voice)",
     "options": _HOOK_ANGLE_OPTIONS},
    {"key": "clip_profile", "label": "Clip length", "kind": "select", "stage": "cut",
     "does": "the GLOBAL deterministic cut-length band (Go-Live default; per-persona it is derived from content_focus)",
     "options": [{"value": n} for n in _CLIP_PROFILE_BANDS]},
    {"key": "hashtag_corpus", "label": "Corpus", "kind": "tags", "stage": "caption",
     "does": "your curated tags LEAD the caption hashtags", "options": []},
]


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
    """{value: clause} for a lever whose options carry a compile clause (content_focus/energy/hook_angle), in
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


def energy_framing_map() -> dict:
    """The derived-cut FRAMING map {energy: framing} — only options with a non-None framing (medium absent ->
    None -> the global crop)."""
    return {o["value"]: o["framing"] for o in lever("energy")["options"] if o.get("framing")}


def build_catalog() -> list[dict]:
    """lever_catalog()'s body, DERIVED from the registry — byte-identical to the legacy literal. content_focus/
    hook_angle render value+effect from the clause; energy renders medium's empty clause as the explicit no-op
    note; clip_profile renders the band ranges from bands.band_for (lazy, as the legacy catalog did);
    hashtag_corpus has no options. Each lever: {key, label, kind, stage, does, options:[{value, effect}]}."""
    from fanops.bands import band_for
    out: list[dict] = []
    for lv in LEVER_REGISTRY:
        if lv["key"] == "energy":
            opts = [{"value": o["value"], "effect": (o["clause"] or "no change — any energy")} for o in lv["options"]]
        elif lv["key"] == "clip_profile":
            opts = [{"value": o["value"], "effect": f"{band_for(o['value']).lo:g}-{band_for(o['value']).hi:g}s cuts"} for o in lv["options"]]
        elif lv["key"] == "hashtag_corpus":
            opts = []
        else:
            opts = [{"value": o["value"], "effect": o["clause"]} for o in lv["options"]]
        out.append({"key": lv["key"], "label": lv["label"], "kind": lv["kind"], "stage": lv["stage"],
                    "does": lv["does"], "options": opts})
    return out
