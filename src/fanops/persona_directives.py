# src/fanops/persona_directives.py
"""The persona DIRECTIVE / COMPOSE / PREVIEW engine (extracted from personas.py, audit #6 — behavior
byte-identical). The structured per-characteristic levers compile into the SUBSTANTIVE per-dimension
instruction (casting/hook/caption) the real pipeline prompts read; the same constants drive the
operator-facing lever catalog, the live compose breakdown, and the persona_facts transparency read.
Everything here is PURE + duck-typed (serves a Persona OR a hydrated Account); all functions are
re-exported from fanops.personas so every existing `from fanops.personas import ...` keeps working."""
from __future__ import annotations
from fanops.config import Config
from fanops import persona_levers as _levers

# THE DIRECTIVE ENGINE (M3). Each structured lever value compiles into a SUBSTANTIVE instruction CLAUSE the
# pipeline's prompt actually acts on — real selection/hook language, NOT a glued adjective ("favors moments:
# punchlines"). content_focus + energy -> the CASTING directive; hook_angle -> the HOOK directive.
# These clauses are the curated DEFAULT; a persona may OVERRIDE the compiled text per dimension (the operator
# owns the words). clip_profile/framing/tag_lean/corpus stay deterministic (cut + hashtags), NOT in this text.
# M1: the clause maps are PROJECTIONS of the single lever registry (fanops.persona_levers) — the SAME
# declaration personas' vocabularies + lever_catalog() derive from, so the three can no longer drift.
_FOCUS_CLAUSE = _levers.clause_map("content_focus")
_ENERGY_CLAUSE = _levers.clause_map("energy")
_ANGLE_CLAUSE = _levers.clause_map("hook_angle")


# P2: DERIVE a per-account CUT default (length tier + framing) from the persona's already-set content_focus +
# energy, so DEFINING a distinct persona IS defining a distinct CLIP — no hand-set clip_profile needed. The
# wire (hydrate -> resolve_clip_profile/top_bias -> account_render_spec.wants_cut -> render_account_cut) is
# already whole; this only supplies its inputs. content_focus -> length (a punchline is a quick rewatchable
# unit; a story needs room), energy -> framing (high=center action, low=top head-safe). M1: both maps are
# registry projections; _FOCUS_PROFILE is ordered LONGER-bias-first (the registry sorts by tier), so a
# multi-focus persona derives deterministically via next() (story+punchlines -> long).
_FOCUS_PROFILE = dict(_levers.focus_profile_map())
_ENERGY_FRAMING = _levers.energy_framing_map()        # medium -> absent -> None (no opinion -> global crop)


def derive_cut_spec(p):
    """The CUT default a persona implies from its content_focus + energy — (clip_profile|None, framing|None).
    content_focus picks the LENGTH (first match in _FOCUS_PROFILE's longer-bias-first order, so a multi-select
    is deterministic); energy picks the FRAMING. Unmapped/empty on a dimension -> None (the account/global
    default stands -> firewall-safe, byte-identical). Pure, duck-typed (Persona OR hydrated Account)."""
    foc = list(getattr(p, "content_focus", None) or [])
    profile = next((v for k, v in _FOCUS_PROFILE.items() if k in foc), None)   # longer-bias-first, order-independent
    framing = _ENERGY_FRAMING.get((getattr(p, "energy", None) or "").strip().lower())
    return profile, framing


def resolved_cut_spec(p):
    """The persona's EFFECTIVE cut spec = explicit pin OVER derived default OVER None (global). The ONE
    function both hydration (accounts._hydrate_from_personas) and the operator UI (compose_breakdown.cut)
    read, so the floor can't drift. A non-blank Persona.clip_profile/framing pin always wins. Pure."""
    d_prof, d_fr = derive_cut_spec(p)
    prof = (getattr(p, "clip_profile", None) or "").strip() or d_prof
    fr = (getattr(p, "framing", None) or "").strip().lower() or d_fr
    return (prof or None, fr or None)


def _base_voice(p) -> str:
    """The persona's freeform base instruction — its voice. Duck-typed (reads .voice OR the hydrated account's
    .persona). The voice is the single freeform field; the old separate `brief` folded into it."""
    return (getattr(p, "voice", None) or getattr(p, "persona", None) or "").strip()


def _join(voice: str, body: str) -> str:
    """voice + compiled directive body. Either empty -> the other (the firewall: no body -> bare voice)."""
    if voice and body: return f"{voice} {body}"
    return voice or body


def casting_directive(p) -> str:
    """WHICH MOMENTS this account clips for — the substantive instruction injected into the casting prompt's
    per-account slot. Override (Persona.casting_directive) wins VERBATIM; else compiled from content_focus +
    energy into real selection language; else the bare voice. THE FIREWALL: no levers + no override -> the
    bare voice, byte-identical to today. Duck-typed (Persona or hydrated Account)."""
    override = (getattr(p, "casting_directive", None) or "").strip()
    if override: return override
    parts: list[str] = []
    foc = [_FOCUS_CLAUSE[c] for c in (getattr(p, "content_focus", None) or []) if c in _FOCUS_CLAUSE]
    if foc: parts.append("Clip for this account: " + "; ".join(foc) + ".")
    e = _ENERGY_CLAUSE.get((getattr(p, "energy", None) or "").strip().lower(), "")
    if e: parts.append(e)
    return _join(_base_voice(p), " ".join(parts).strip())


def hook_directive(p) -> str:
    """The ON-SCREEN HOOK brief for this account — injected into the hook prompt's per-account slot. Override
    (Persona.hook_directive) wins VERBATIM; else compiled from hook_angle (the strategy); else the bare voice
    (firewall). The hook's REGISTER comes from the voice (which leads this directive), so there is no separate
    tone lever. Duck-typed."""
    override = (getattr(p, "hook_directive", None) or "").strip()
    if override: return override
    parts: list[str] = []
    a = _ANGLE_CLAUSE.get((getattr(p, "hook_angle", None) or "").strip().lower(), "")
    if a: parts.append("For the on-screen hook, " + a + ".")
    return _join(_base_voice(p), " ".join(parts).strip())


def caption_directive(p) -> str:
    """The CAPTION angle for this account — injected into the caption prompt's per-surface slot. Override
    (Persona.caption_directive) wins VERBATIM; else the bare voice (tag_lean/corpus drive the hashtags
    deterministically elsewhere, so the caption directive is purely the voice/angle). Duck-typed; firewall-safe."""
    override = (getattr(p, "caption_directive", None) or "").strip()
    return override or _base_voice(p)


def compose_persona_instruction(p) -> str:
    """Back-compat alias + the human-facing 'what the AI reads' summary: the CASTING directive (the primary
    'which moments' instruction). The hook/caption surfaces read their own directive (hook_directive /
    caption_directive); this stays the headline for the card + the strategy check. Firewall floor: bare voice."""
    return casting_directive(p)


def lever_catalog() -> list[dict]:
    """EXPOSE THE LEVERS — the operator-facing catalog of every persona lever and what each option DOES. M1:
    DERIVED from the single lever registry (fanops.persona_levers.build_catalog), the SAME declaration the
    clause maps + the persona vocabularies project from, so the effect the operator reads is EXACTLY what the
    pipeline acts on (zero drift — structural now, not a parity promise). Pure, ordered (the editor + the
    reference render it). Each lever: {key, label, kind, stage, does, options:[{value, effect}]}; corpus has no
    enumerated options. Per-PERSONA the cut LENGTH is DERIVED from content_focus (no per-persona knob);
    `clip_profile` remains here only as the GLOBAL clip-length lever (the Go-Live default) — its band labels
    feed that control (effect computed lazily from bands.band_for inside build_catalog). Hashtags are owned by
    the curated corpus; there is no tag_lean/hook_tone/clip_count persona lever."""
    return _levers.build_catalog()


def _casting_fragments(p) -> list[dict]:
    """The casting directive's pieces, each tagged with the lever that produced it — reconstructed from the
    SAME clause maps casting_directive() uses (the authoritative TEXT still comes from the compiler)."""
    frags: list[dict] = []
    voice = _base_voice(p)
    if voice: frags.append({"source": "voice", "text": voice})
    foc = [c for c in (getattr(p, "content_focus", None) or []) if c in _FOCUS_CLAUSE]
    if foc: frags.append({"source": "content_focus", "text": "Clip for this account: " + "; ".join(_FOCUS_CLAUSE[c] for c in foc) + "."})
    e = _ENERGY_CLAUSE.get((getattr(p, "energy", None) or "").strip().lower(), "")
    if e: frags.append({"source": "energy", "text": e})
    return frags


def _hook_fragments(p) -> list[dict]:
    frags: list[dict] = []
    voice = _base_voice(p)
    if voice: frags.append({"source": "voice", "text": voice})
    a = _ANGLE_CLAUSE.get((getattr(p, "hook_angle", None) or "").strip().lower(), "")
    if a: frags.append({"source": "hook_angle", "text": "For the on-screen hook, " + a + "."})
    return frags


def compose_breakdown(cfg: Config, p) -> dict:
    """THE LIVE COMPOSED TRANSLATION — what this persona compiles to RIGHT NOW: the exact casting/hook/caption
    directives the pipeline will read, the deterministic cut band + framing, and the lead hashtags, each
    decomposed to the lever that produced it, with the engine's REAL precedence surfaced (an override SHADOWS
    its structured levers; energy=medium is a no-op). The `text` of each dimension is the compiler's own output
    (parity — the panel can't drift); the fragments reconstruct the assembly for provenance. Pure read; the
    cut/tags reuse the same band_for / persona_facts resolvers the pipeline runs. Duck-typed (Persona/Account)."""
    from fanops.bands import band_for
    cast_override = (getattr(p, "casting_directive", None) or "").strip()
    hook_override = (getattr(p, "hook_directive", None) or "").strip()
    cap_override = (getattr(p, "caption_directive", None) or "").strip()
    casting = {"text": casting_directive(p), "override": bool(cast_override),
               "fragments": ([{"source": "override", "text": cast_override}] if cast_override else _casting_fragments(p)),
               "shadowed": (["content_focus", "energy"] if cast_override else [])}
    hook = {"text": hook_directive(p), "override": bool(hook_override),
            "fragments": ([{"source": "override", "text": hook_override}] if hook_override else _hook_fragments(p)),
            "shadowed": (["hook_angle"] if hook_override else []),
            # S7: the EFFECTIVE structured angle — None when a freeform override shadows it (so produces_summary
            # never names an angle that doesn't actually drive the hook).
            "angle": (None if hook_override else (getattr(p, "hook_angle", None) or None))}
    caption = {"text": caption_directive(p), "override": bool(cap_override)}
    pin_prof = (getattr(p, "clip_profile", None) or "").strip()
    res_prof, res_fr = resolved_cut_spec(p)               # pin > derived > None — the SAME floor hydration applies
    band = band_for(res_prof or "")
    cut = {"band": f"{band.lo:g}-{band.hi:g}s", "framing": res_fr,
           "source": ("persona" if pin_prof else ("derived" if res_prof else "global"))}
    facts = persona_facts(cfg, p)                         # reuse the EXACT lead-tags + length resolver
    tags = {"lead": facts["lead_tags"],
            "corpus": list(getattr(p, "hashtag_corpus", None) or [])}
    noops: list[str] = []
    if (getattr(p, "energy", None) or "").strip().lower() == "medium" and not cast_override:
        noops.append("energy=medium has no effect on selection")
    bd = {"casting": casting, "hook": hook, "caption": caption, "cut": cut, "tags": tags, "noops": noops}
    bd["produces"] = produces_summary(bd)                 # S7: the operator-facing OUTPUT lead, from this same detail
    return bd


def produces_summary(breakdown: dict) -> list[str]:
    """S7 — the operator-facing "what this persona PRODUCES" lead: an ordered clause list distilled from the
    SAME compose_breakdown detail (parity-guaranteed — no second resolver, so it can't drift from what the
    pipeline runs), e.g. ['~8-15s clips', 'top-framed', 'curiosity hooks', '≤4 hashtags']. Each clause is shown
    ONLY for a deliberately-configured dimension: a global cut, an unset framing/angle, and a floor-only
    hashtag posture (no lean/corpus) are all SILENT — so an unconfigured persona yields []. Pure; reads only
    the passed dict, never the disk."""
    out: list[str] = []
    cut = breakdown.get("cut") or {}
    if cut.get("source") and cut.get("source") != "global" and cut.get("band"):
        out.append(f"~{cut['band']} clips")
    angle = (breakdown.get("hook") or {}).get("angle")
    if angle:
        out.append(f"{angle} hooks")
    tags = breakdown.get("tags") or {}
    lead = tags.get("lead") or []
    if lead and tags.get("corpus"):                         # a deliberate hashtag posture (curated corpus), not the cold-start floor
        out.append(f"≤{len(lead)} hashtags")
    return out


def persona_facts(cfg: Config, p) -> dict:
    """The TRANSPARENCY read (M2 Task 8) — "what this persona produces", derived from the EXACT resolvers the
    pipeline calls (never a re-encoded copy that could drift): the clip LENGTH band (bands.band_for on the
    resolved profile — the same call moment_pick_prompt makes), the FRAMING, and the deterministic LEAD
    hashtags (hashtags.vet_hashtags with this persona's curated corpus over the live reach store). The corpus
    is a DETERMINISTIC post-step (not shown to the caption LLM), so this is the only place the operator sees
    its effect. PURE read; FAIL-OPEN to the frozen floor when no store/creds. Duck-typed (serves a Persona OR
    a hydrated Account)."""
    from fanops.bands import band_for
    from fanops.hashtags import vet_hashtags, load_store
    from fanops.models import Platform
    prof, fr = resolved_cut_spec(p)          # the EFFECTIVE cut — pin OR derived from content_focus/energy (the
    band = band_for(prof)                    # SAME spec hydration applies), so the card shows the REAL length, not
    try:                                     # the raw-unset value (which made every persona read as one global band)
        store = load_store(cfg)
    except Exception:
        store = None
    lead = vet_hashtags([], Platform.instagram,
                        corpus=list(getattr(p, "hashtag_corpus", None) or []), store=store)
    return {"length_band": f"{band.lo:.0f}-{band.hi:.0f}s", "framing": fr, "lead_tags": lead}
