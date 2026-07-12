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
# punchlines"). content_focus + selection_scope -> the CASTING directive; hook_angle -> the HOOK directive.
# These clauses are the curated DEFAULT; a persona may OVERRIDE the compiled text per dimension (the operator
# owns the words). clip_profile/framing/tag_lean/corpus stay deterministic (cut + hashtags), NOT in this text.
# M1: the clause maps are PROJECTIONS of the single lever registry (fanops.persona_levers) — the SAME
# declaration personas' vocabularies + lever_catalog() derive from, so the three can no longer drift.
_FOCUS_CLAUSE = _levers.clause_map("content_focus")
_SCOPE_CLAUSE = _levers.clause_map("selection_scope")
_ANGLE_CLAUSE = _levers.clause_map("hook_angle")



class Directive:
    """MOL-171: structured directive VIEW over existing levers with a string-preserving façade.
    Every pipeline consumer that treated the directive as a str keeps working via __str__."""
    __slots__ = ("select_rule", "scope_lens", "mechanism_lean", "register", "demos", "ban_additions", "_rendered")

    def __init__(self, *, select_rule: str = "", scope_lens: str = "", mechanism_lean: str = "",
                 register: str = "", demos: list | None = None, ban_additions: list | None = None,
                 _rendered: str = ""):
        self.select_rule = select_rule
        self.scope_lens = scope_lens
        self.mechanism_lean = mechanism_lean
        self.register = register
        self.demos = list(demos or [])
        self.ban_additions = list(ban_additions or [])
        self._rendered = _rendered

    def __str__(self) -> str: return self._rendered
    def __bool__(self) -> bool: return bool(self._rendered)
    def __repr__(self) -> str: return f"Directive({self._rendered!r})"


# P2: DERIVE a per-account CUT default (length tier + framing) from the persona's content_focus, so DEFINING a
# distinct persona IS defining a distinct CLIP — no hand-set clip_profile needed. content_focus -> length (a
# punchline is a quick rewatchable unit; a story needs room) + framing (intensity/framing keys on each focus).
# M1: both maps are registry projections; _FOCUS_PROFILE is ordered LONGER-bias-first; _FRAMING_MAP is ordered
# HIGHEST-intensity-first (MOL-170 — framing moved off the retired energy lever).
_FOCUS_PROFILE = dict(_levers.focus_profile_map())
_FRAMING_MAP = dict(_levers.framing_map())


def derive_cut_spec(p):
    """The CUT default a persona implies from its content_focus — (clip_profile|None, framing|None).
    content_focus picks the LENGTH (first match in _FOCUS_PROFILE's longer-bias-first order) and the FRAMING
    (first match in _FRAMING_MAP's highest-intensity-first order). Unmapped/empty -> None (global stands)."""
    foc = list(getattr(p, "content_focus", None) or [])
    profile = next((v for k, v in _FOCUS_PROFILE.items() if k in foc), None)
    framing = next((v for k, v in _FRAMING_MAP.items() if k in foc), None)
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


def casting_directive(p) -> Directive:
    """WHICH MOMENTS this account clips for — the substantive instruction injected into the casting prompt's
    per-account slot. Compiled from content_focus + selection_scope into real selection language; else the bare voice.
    THE FIREWALL: no levers -> the bare voice, byte-identical to today. Duck-typed (Persona or hydrated
    Account). Returns a Directive (str(d) == today's string)."""
    voice = _base_voice(p)
    foc_clauses = [_FOCUS_CLAUSE[c] for c in (getattr(p, "content_focus", None) or []) if c in _FOCUS_CLAUSE]
    select_rule = ("Clip for this account: " + "; ".join(foc_clauses) + ".") if foc_clauses else ""
    scope_lens = _SCOPE_CLAUSE.get((getattr(p, "selection_scope", None) or "").strip().lower(), "")
    body_parts = [x for x in (select_rule, scope_lens) if x]
    rendered = _join(voice, " ".join(body_parts).strip())
    return Directive(select_rule=select_rule, scope_lens=scope_lens, register=voice, _rendered=rendered)


def hook_directive(p) -> Directive:
    """The ON-SCREEN HOOK brief for this account — injected into the hook prompt's per-account slot. Compiled
    from hook_angle (the strategy); else the bare voice (firewall). Persona-supplied demos/ban_additions ride
    as optional duck-typed attrs (hook_demos, hook_ban_additions). Returns Directive (str(d) == today's string)."""
    voice = _base_voice(p)
    lean = _ANGLE_CLAUSE.get((getattr(p, "hook_angle", None) or "").strip().lower(), "")
    body = ("For the on-screen hook, " + lean + ".") if lean else ""
    rendered = _join(voice, body.strip())
    demos = list(getattr(p, "hook_demos", None) or [])
    bans = list(getattr(p, "hook_ban_additions", None) or [])
    return Directive(mechanism_lean=lean, register=voice, demos=demos, ban_additions=bans, _rendered=rendered)


def hook_author_slot(p) -> str:
    """The per-account hook-authoring brief for moment_hooks — ALWAYS non-empty for an active account so the
    frame-seeing author writes ONE owner hook (P6); not only accounts whose levers compile a (not only accounts whose levers compile a
    hook_directive). Falls back: hook_directive -> inline persona voice -> tag_lean hint -> handle floor."""
    instr = hook_directive(p)
    if instr: return str(instr)
    voice = _base_voice(p)
    if voice: return voice
    lean = (getattr(p, "tag_lean", None) or "").strip()
    if lean: return f"Independent fan account — {lean} lean. Write a short, scroll-stopping on-screen hook in that voice."
    handle = (getattr(p, "handle", None) or "").strip()
    return f"Independent fan account ({handle or 'unknown'}). Write a short, distinct on-screen hook."


def caption_directive(p) -> str:
    """The CAPTION angle for this account — injected into the caption prompt's per-surface slot. It is the bare
    voice (the curated corpus drives the hashtags deterministically elsewhere, so the caption directive is
    purely the voice). Duck-typed; firewall-safe. (M3e: the freeform caption_directive OVERRIDE was retired.)"""
    return _base_voice(p)


def compose_persona_instruction(p) -> str:
    """Back-compat alias + the human-facing 'what the AI reads' summary: the CASTING directive (the primary
    'which moments' instruction). The hook/caption surfaces read their own directive (hook_directive /
    caption_directive); this stays the headline for the card + the strategy check. Firewall floor: bare voice."""
    return str(casting_directive(p))


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
    sc = _SCOPE_CLAUSE.get((getattr(p, "selection_scope", None) or "").strip().lower(), "")
    if sc: frags.append({"source": "selection_scope", "text": sc})
    return frags


def _hook_fragments(p) -> list[dict]:
    frags: list[dict] = []
    voice = _base_voice(p)
    if voice: frags.append({"source": "voice", "text": voice})
    a = _ANGLE_CLAUSE.get((getattr(p, "hook_angle", None) or "").strip().lower(), "")
    if a: frags.append({"source": "hook_angle", "text": "For the on-screen hook, " + a + "."})
    return frags


def _caption_fragments(p) -> list[dict]:
    """The caption directive's pieces (M4 provenance): the caption text IS the voice (hashtags are
    deterministic, not in this text), so the one fragment traces to the voice lever. No voice -> []."""
    voice = _base_voice(p)
    return [{"source": "voice", "text": voice}] if voice else []


def _cut_fragments(p) -> list[dict]:
    """The CUT's pieces (M4 provenance), each tagged with the lever that DERIVES it: content_focus -> the
    length band + framing. Empty when neither derives (a global cut, or a carrier-pin-only cut with no levers)."""
    frags: list[dict] = []
    d_prof, d_fr = derive_cut_spec(p)
    if d_prof:
        from fanops.bands import band_for
        b = band_for(d_prof)
        frags.append({"source": "content_focus", "text": f"{b.lo:g}-{b.hi:g}s ({d_prof}, derived from content_focus)"})
    if d_fr:
        frags.append({"source": "content_focus", "text": f"{d_fr} crop (derived from content_focus)"})
    return frags


def compose_breakdown(cfg: Config, p) -> dict:
    """THE LIVE COMPOSED TRANSLATION — what this persona compiles to RIGHT NOW: the exact casting/hook/caption
    directives the pipeline will read, the deterministic cut band + framing, and the lead hashtags, each
    decomposed to the lever that produced it (selection_scope=open is a no-op, surfaced). The `text` of each dimension
    is the compiler's own output (parity — the panel can't drift); the fragments reconstruct the assembly for
    provenance. Pure read; the cut/tags reuse the same band_for / persona_facts resolvers the pipeline runs.
    Duck-typed (Persona/Account). (M3e: the freeform directive OVERRIDES were retired — no `override`/`shadowed`
    surface remains; every dimension is the structured-lever compile.) (M3d: a persona never pins the cut — the
    `persona` cut source is reachable only via an Account carrier pin.)"""
    from fanops.bands import band_for
    casting = {"text": str(casting_directive(p)), "override": False,
               "fragments": _casting_fragments(p), "shadowed": []}
    hook = {"text": str(hook_directive(p)), "override": False,
            "fragments": _hook_fragments(p), "shadowed": [],
            "angle": (getattr(p, "hook_angle", None) or None)}    # S7: the EFFECTIVE structured angle
    caption = {"text": caption_directive(p), "override": False, "fragments": _caption_fragments(p)}
    pin_prof = (getattr(p, "clip_profile", None) or "").strip()
    res_prof, res_fr = resolved_cut_spec(p)               # carrier pin > derived > None — the SAME floor hydration applies
    band = band_for(res_prof or "")
    cut = {"band": f"{band.lo:g}-{band.hi:g}s", "framing": res_fr,
           "source": ("persona" if pin_prof else ("derived" if res_prof else "global")),
           "fragments": _cut_fragments(p)}                # M4: the lever(s) that DERIVE the cut (content_focus)
    facts = persona_facts(cfg, p)                         # reuse the EXACT lead-tags + length resolver
    tags = {"lead": facts["lead_tags"],
            "corpus": list(getattr(p, "hashtag_corpus", None) or [])}
    noops: list[str] = []
    if (getattr(p, "selection_scope", None) or "").strip().lower() in ("", "open"):
        noops.append("selection_scope=open has no effect on selection")
    bd = {"casting": casting, "hook": hook, "caption": caption, "cut": cut, "tags": tags, "noops": noops}
    bd["produces"] = produces_summary(bd)                 # S7: the operator-facing OUTPUT lead, from this same detail
    return bd


def manifest(cfg: Config, p) -> list[dict]:
    """M4 — THE LEVER MANIFEST: one row per EDITABLE lever — its current value, the output CHANNEL(s) it owns,
    what it PRODUCES right now, and a HEALTH flag — DERIVED from the registry (persona_levers) + compose_breakdown
    (the SAME resolvers the pipeline runs), so the operator view, this manifest, and the live output cannot
    disagree (no-drift: mutate a lever and `produces` moves with it). Rows are in registry/declaration order.
    health == 'ok' for a coherent lever (editable ∧ each owned channel single-owner); post-M3 every lever is
    'ok'. Pure read; duck-typed (Persona/Account)."""
    from fanops import persona_levers as levers
    bd = compose_breakdown(cfg, p)
    labels = {lv["key"]: lv["label"] for lv in lever_catalog()}

    def _produces(key):
        if key == "voice":
            return next((f["text"] for f in bd["caption"]["fragments"] if f["source"] == "voice"), "")
        if key == "content_focus":
            return next((f["text"] for f in bd["cut"]["fragments"] if f["source"] == "content_focus"), bd["cut"]["band"])
        if key == "selection_scope":
            return next((f["text"] for f in bd["casting"]["fragments"] if f["source"] == "selection_scope"), "—")
        if key == "hook_angle":
            return bd["hook"]["text"]
        if key == "hashtag_corpus":
            return bd["tags"]["lead"]
        return ""

    def _health(key):
        if key not in levers.editable_fields():
            return "incoherent"
        for ch in levers.channels_of(key):              # distinctness: each owned channel has exactly THIS owner
            if levers.owner_of(ch) != key:
                return "incoherent"
        return "ok"

    out: list[dict] = []
    for key in levers.PERSONA_EDITABLE_CHANNELS:        # declaration order: voice, content_focus, selection_scope, hook_angle, hashtag_corpus
        out.append({"key": key, "label": labels.get(key, key.replace("_", " ").title()),
                    "channels": list(levers.channels_of(key)), "value": getattr(p, key, None),
                    "produces": _produces(key), "source": key, "health": _health(key)})
    return out


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
    prof, fr = resolved_cut_spec(p)          # the EFFECTIVE cut — pin OR derived from content_focus (the
    band = band_for(prof)                    # SAME spec hydration applies), so the card shows the REAL length, not
    try:                                     # the raw-unset value (which made every persona read as one global band)
        store = load_store(cfg)
    except Exception as exc:
        from fanops.log import get_logger     # a store read-fail falls to the frozen floor — record it, don't hide it
        get_logger(cfg)("personas", getattr(p, "handle", "-"), "store_load_error", err=str(exc)[:160])
        store = None
    lead = vet_hashtags([], Platform.instagram,
                        corpus=list(getattr(p, "hashtag_corpus", None) or []), store=store,
                        genre=((getattr(p, "intake", None) or {}).get("genre") or None), cfg=cfg)   # U11: honor the global ban list here too (a banned tag must not show as a persona's "lead tag")
    return {"length_band": f"{band.lo:.0f}-{band.hi:.0f}s", "framing": fr, "lead_tags": lead}
