# src/fanops/personas.py
"""A1 — Personas as a FIRST-CLASS entity. Until now a "persona" was only a free-text Account.persona
string + tag_lean, seeded by hand from a brief doc — not editable, not reusable, not a thing you could
add an intake for. This makes a Persona a named record in 00_control/personas.json: a `voice` (the
string the pipeline reads), a `tag_lean`, a `hashtag_corpus` (the per-persona reach-vetted pool, B1),
and free-form `intake` metadata (genre/language/reference accounts, seeds B3's research). Accounts LINK
to a persona via Account.persona_id; the linked persona's voice/tag_lean HYDRATE the account in memory
at load (accounts._hydrate_from_personas), so every existing consumer (caption/moments/casting/
variant_transfer) stays byte-identical while an operator edit takes effect on the next load.

The writers mirror accounts.py exactly: a per-file flock serializes the read-modify-write (no lost
update from two concurrent Studio writers), and an atomic temp+os.replace never leaves a torn file. The
control-file boundary validates (a known tag_lean, a non-blank id) so a write never lands a record that
won't reload. Like accounts.json the file is hand-editable (indented); Personas.load raising on a
corrupt file is guarded by the fail-open hydration helper so account loading never crashes on it."""
from __future__ import annotations
import json
import os
import re
import tempfile
from contextlib import contextmanager
from typing import Optional
from pydantic import BaseModel, Field
from fanops.config import Config, FRAMING_NAMES
from fanops.errors import ControlFileError, reason as _reason
from fanops.hashtags import TAG_LEANS, _norm
from fanops.bands import PROFILE_NAMES

_CORPUS_CAP = 40                # max curated tags per persona — keeps captions/budget bounded (cap, not a target)

# The lever-engine vocabularies (the validated control surface — one lever per persona characteristic). Each
# is the WRITE boundary for its lever: add/update_persona refuses an unknown value (never write a typo that
# reloads as a silent no-op), and compose_persona_instruction renders the SET levers into the single
# instruction the casting/hook/caption prompts read. clip_profile/framing reuse the Account validators
# (bands.PROFILE_NAMES / config.FRAMING_NAMES) so a persona pins the SAME deterministic CUT an account can.
CONTENT_FOCUS = frozenset({"punchlines", "emotional", "hype", "storytelling", "visual", "bold-statement"})
ENERGY_LEVELS = frozenset({"low", "medium", "high"})
HOOK_ANGLES = frozenset({"curiosity", "challenge", "emotional", "result-first", "fomo"})
HOOK_TONES = frozenset({"aggressive", "restrained", "playful"})


class Persona(BaseModel):
    id: str                                       # stable slug (the link key on Account.persona_id)
    name: str = ""                                # operator-facing display name
    voice: str = ""                               # the persona string the pipeline reads (caption/hook/casting voice)
    tag_lean: Optional[str] = None                # persona TAG knob: tasteful|underground|bold (None -> no lean)
    hashtag_corpus: list[str] = Field(default_factory=list)   # B1: the per-persona reach-vetted pool
    intake: dict = Field(default_factory=dict)    # free-form intake (genre/language/reference accounts) — seeds B3 research
    # Lever engine: explicit per-characteristic DIRECTION that compose_persona_instruction renders into the
    # one instruction the casting/hook/caption prompts read. ADDITIVE — all empty on a legacy persona, so
    # compose returns the bare `voice` (byte-identical). Validated at the write boundary (add/update_persona).
    content_focus: list[str] = Field(default_factory=list)   # which moment KINDS to favor (casting): CONTENT_FOCUS
    energy: Optional[str] = None                  # clip energy: low|medium|high (ENERGY_LEVELS)
    hook_angle: Optional[str] = None              # on-screen hook strategy: curiosity|challenge|... (HOOK_ANGLES)
    hook_tone: Optional[str] = None               # on-screen hook tone: aggressive|restrained|playful (HOOK_TONES)
    clip_profile: Optional[str] = None            # per-account LENGTH tier (bands.PROFILE_NAMES) — hydrates onto the account
    framing: Optional[str] = None                 # per-account vertical CROP bias (config.FRAMING_NAMES) — hydrates onto the account
    brief: str = ""                               # M2 LOCK: an operator-APPROVED strategy frozen as the persona's downstream
                                                  # anchor. compose appends it after `voice` so the real casting/hook/caption
                                                  # prompts run against the agreed DEFINITION. Free text; ONLY a deliberate Save
                                                  # writes it (a strategy check NEVER auto-locks). Empty -> byte-identical.
    # M3 DIRECTIVE ENGINE: the structured levers above compile into a SUBSTANTIVE per-dimension instruction
    # (casting/hook/caption) injected into THAT dimension's real prompt — not a glued adjective. The operator
    # can OVERRIDE the compiled text per persona (these fields); a non-empty override is used VERBATIM, else
    # the lever-compiled clauses are used, else the bare voice (the firewall). Empty -> byte-identical.
    casting_directive: str = ""                   # override for "which moments to clip" (else compiled from content_focus+energy)
    hook_directive: str = ""                      # override for the on-screen hook brief (else compiled from hook_angle+hook_tone)
    caption_directive: str = ""                   # override for the caption angle (else the voice; tags stay deterministic)
    clip_count: Optional[int] = None              # per-persona CLIP BUDGET (deterministic): how many of its best-fit moments this
                                                  # account gets. None -> the global cfg.cast_pick_budget (byte-identical when unset)


class Personas:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.personas: list[Persona] = []

    @classmethod
    def load(cls, cfg: Config) -> "Personas":
        r = cls(cfg)
        p = cfg.personas_path
        if p.exists():
            try:
                raw = json.loads(p.read_text())
                r.personas = [Persona(**x) for x in raw.get("personas", []) if isinstance(x, dict)]
            except Exception as e:                 # a hand-edit typo: clear one-liner, not a raw traceback
                raise ControlFileError(f"{p.name} invalid: {_reason(e)}") from e
        return r

    def get(self, pid: Optional[str]) -> Optional[Persona]:
        return next((p for p in self.personas if p.id == pid), None) if pid else None

    def all(self) -> list[Persona]:
        return list(self.personas)


def _slug(s: str) -> str:
    """A stable id from a name/handle: lowercase, drop a leading '@', non-alphanumerics -> single '-'."""
    s = (s or "").strip().lower().lstrip("@")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


# THE DIRECTIVE ENGINE (M3). Each structured lever value compiles into a SUBSTANTIVE instruction CLAUSE the
# pipeline's prompt actually acts on — real selection/hook language, NOT a glued adjective ("favors moments:
# punchlines"). content_focus + energy -> the CASTING directive; hook_angle + hook_tone -> the HOOK directive.
# These clauses are the curated DEFAULT; a persona may OVERRIDE the compiled text per dimension (the operator
# owns the words). clip_profile/framing/tag_lean/corpus stay deterministic (cut + hashtags), NOT in this text.
_FOCUS_CLAUSE = {
    "punchlines": "moments that land a verbal punchline — a bar with a clear setup and payoff, a quotable, rewatchable line",
    "emotional": "moments carrying real emotion — vulnerability, longing, devotion, a confession the viewer feels",
    "hype": "the highest-energy hype moments — the hardest delivery, the beat drop, the room going up",
    "storytelling": "moments that tell a story or reveal something — an origin, a turn, a payoff",
    "visual": "visually arresting moments — a strong scene, motion, or setting, not audio alone",
    "bold-statement": "a bold or contrarian statement that stops the scroll",
}
_ENERGY_CLAUSE = {
    "low": "Favor calmer, more introspective moments over loud ones.",
    "medium": "",
    "high": "Strongly prefer peak-intensity moments; skip calm, low-energy passages.",
}
_ANGLE_CLAUSE = {
    "curiosity": "open a curiosity gap the viewer has to close",
    "challenge": "dare or challenge the viewer to react",
    "emotional": "name the high-arousal feeling the clip gives the viewer",
    "result-first": "open on the payoff, then reveal how it got there",
    "fomo": "carry genuine scarcity — a one-time, leaked, or unreleased drop",
}
_TONE_CLAUSE = {
    "aggressive": "Write it hard and confrontational.",
    "restrained": "Write it understated and quietly confident.",
    "playful": "Write it playful, a little cheeky.",
}


# P2: DERIVE a per-account CUT default (length tier + framing) from the persona's already-set content_focus +
# energy, so DEFINING a distinct persona IS defining a distinct CLIP — no hand-set clip_profile needed. The
# wire (hydrate -> resolve_clip_profile/top_bias -> account_render_spec.wants_cut -> render_account_cut) is
# already whole; this only supplies its inputs. content_focus -> length (a punchline is a quick rewatchable
# unit; a story needs room), energy -> framing (high=center action, low=top head-safe). Priority ORDER below
# is longer-bias-first, so a multi-focus persona derives deterministically (story+punchlines -> long).
_FOCUS_PROFILE = {"storytelling": "long", "emotional": "medium", "visual": "medium",
                  "punchlines": "short", "hype": "short", "bold-statement": "short"}
_ENERGY_FRAMING = {"high": "center", "low": "top"}   # medium -> absent -> None (no opinion -> global crop)


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
    """The persona's freeform base: the voice, then the LOCKED brief (M2) appended after it. Duck-typed
    (reads .voice OR the hydrated account's .persona). Empty brief -> just the voice (the firewall floor)."""
    voice = (getattr(p, "voice", None) or getattr(p, "persona", None) or "").strip()
    brief = (getattr(p, "brief", None) or "").strip()
    return ". ".join(s for s in (voice, brief) if s)


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
    (Persona.hook_directive) wins VERBATIM; else compiled from hook_angle + hook_tone; else the bare voice
    (firewall). Duck-typed."""
    override = (getattr(p, "hook_directive", None) or "").strip()
    if override: return override
    parts: list[str] = []
    a = _ANGLE_CLAUSE.get((getattr(p, "hook_angle", None) or "").strip().lower(), "")
    if a: parts.append("For the on-screen hook, " + a + ".")
    t = _TONE_CLAUSE.get((getattr(p, "hook_tone", None) or "").strip().lower(), "")
    if t: parts.append(t)
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
    """EXPOSE THE LEVERS — the operator-facing catalog of every persona lever and what each option DOES,
    sourced from the SAME engine constants the compilers/resolvers use (the clause maps above, bands.band_for,
    hashtags._LEANS), so the effect the operator reads is EXACTLY what the pipeline acts on (zero drift; a
    parity test forbids divergence). Pure, ordered (the editor + the reference render it). Each lever:
    {key, label, kind, stage, does, options:[{value, effect}]}; clip_count/corpus have no enumerated options."""
    from fanops.bands import band_for
    from fanops.hashtags import _LEANS
    # An ORDERED display list (PROFILE_NAMES is a frozenset, no order). The coverage test asserts this set ==
    # PROFILE_NAMES, so adding a band to bands.py fails the test until it is added here — keep the order, don't
    # "fix" this to PROFILE_NAMES (that would lose the short->long->legacy reading order).
    _profiles = ["short", "medium", "long", "talk", "song"]
    return [
        {"key": "content_focus", "label": "Clips · favors moments", "kind": "multi", "stage": "casting",
         "does": "which KINDS of moments this account clips for (injected into the casting prompt)",
         "options": [{"value": k, "effect": v} for k, v in _FOCUS_CLAUSE.items()]},
        {"key": "energy", "label": "Energy", "kind": "select", "stage": "casting",
         "does": "biases moment selection toward calm or peak-intensity",
         "options": [{"value": k, "effect": (v or "no change — any energy")} for k, v in _ENERGY_CLAUSE.items()]},
        {"key": "hook_angle", "label": "Hook angle", "kind": "select", "stage": "hook",
         "does": "the strategy of the burned on-screen hook (injected into the hook prompt)",
         "options": [{"value": k, "effect": v} for k, v in _ANGLE_CLAUSE.items()]},
        {"key": "hook_tone", "label": "Hook tone", "kind": "select", "stage": "hook",
         "does": "the voice of the burned on-screen hook",
         "options": [{"value": k, "effect": v} for k, v in _TONE_CLAUSE.items()]},
        {"key": "clip_profile", "label": "Clip length", "kind": "select", "stage": "cut",
         "does": "the deterministic cut-length band (the cut, not the prompt); if unset, derived from content_focus",
         "options": [{"value": n, "effect": f"{band_for(n).lo:g}-{band_for(n).hi:g}s cuts"} for n in _profiles]},
        {"key": "framing", "label": "Framing", "kind": "select", "stage": "cut",
         "does": "the deterministic vertical crop; if unset, derived from energy",
         "options": [{"value": "top", "effect": "head-safe upper-third crop"},
                     {"value": "center", "effect": "centered crop"}]},
        {"key": "tag_lean", "label": "Tag lean", "kind": "select", "stage": "caption",
         "does": "floats a flavor pool to the front of the caption hashtags (deterministic, not in the prompt)",
         "options": [{"value": k, "effect": "leads with " + " ".join(v)} for k, v in _LEANS.items()]},
        {"key": "clip_count", "label": "Clips per drop", "kind": "int", "stage": "casting",
         "does": "how many best-fit moments this account gets per source (blank = the global budget)", "options": []},
        {"key": "hashtag_corpus", "label": "Corpus", "kind": "tags", "stage": "caption",
         "does": "your curated tags LEAD the caption hashtags, ahead of the lean pool", "options": []},
    ]


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
    t = _TONE_CLAUSE.get((getattr(p, "hook_tone", None) or "").strip().lower(), "")
    if t: frags.append({"source": "hook_tone", "text": t})
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
            "shadowed": (["hook_angle", "hook_tone"] if hook_override else []),
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
    tags = {"lead": facts["lead_tags"], "lean": getattr(p, "tag_lean", None),
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
    if cut.get("framing"):
        out.append(f"{cut['framing']}-framed")
    angle = (breakdown.get("hook") or {}).get("angle")
    if angle:
        out.append(f"{angle} hooks")
    tags = breakdown.get("tags") or {}
    lead = tags.get("lead") or []
    if lead and (tags.get("lean") or tags.get("corpus")):   # a deliberate hashtag posture, not the cold-start floor
        out.append(f"≤{len(lead)} hashtags")
    return out


def persona_facts(cfg: Config, p) -> dict:
    """The TRANSPARENCY read (M2 Task 8) — "what this persona produces", derived from the EXACT resolvers the
    pipeline calls (never a re-encoded copy that could drift): the clip LENGTH band (bands.band_for on the
    resolved profile — the same call moment_pick_prompt makes), the FRAMING, and the deterministic LEAD
    hashtags (hashtags.vet_hashtags with this persona's lean + curated corpus over the live reach store). The
    lean/corpus are a DETERMINISTIC post-step (not shown to the caption LLM), so this is the only place the
    operator sees their effect. PURE read; FAIL-OPEN to the frozen floor when no store/creds. Duck-typed
    (serves a Persona OR a hydrated Account)."""
    from fanops.bands import band_for
    from fanops.hashtags import vet_hashtags, load_store
    from fanops.models import Platform
    band = band_for(getattr(p, "clip_profile", None))
    try:
        store = load_store(cfg)
    except Exception:
        store = None
    lead = vet_hashtags([], Platform.instagram, lean=getattr(p, "tag_lean", None),
                        corpus=list(getattr(p, "hashtag_corpus", None) or []), store=store)
    return {"length_band": f"{band.lo:.0f}-{band.hi:.0f}s", "framing": getattr(p, "framing", None), "lead_tags": lead}


def _enum_or_none(v, names, label) -> Optional[str]:
    """Normalize an optional enum lever to lowercase-or-None; raise on an unknown non-empty value (the write
    boundary — never persist a lever that won't reload / would be a silent typo)."""
    v = (v or "").strip().lower()
    if v and v not in names:
        raise ValueError(f"unknown {label}: {v!r}")
    return v or None


def _clip_count_or_none(v) -> Optional[int]:
    """Normalize the per-persona clip budget: a positive int, or None when blank/None. A non-numeric or
    non-positive value raises (the write boundary — never persist a budget that silently disables casting)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"clip_count must be a whole number: {v!r}")
    if n <= 0:
        raise ValueError(f"clip_count must be positive: {n}")
    return n


def _norm_focus(content_focus) -> list[str]:
    """Normalize + validate content_focus (the multi-select moment-kind lever): lowercase, deduped, each in
    CONTENT_FOCUS. A None/non-list -> []. An unknown kind raises (mirrors the enum levers)."""
    seq = content_focus if isinstance(content_focus, (list, tuple)) else []
    out: list[str] = []; seen: set[str] = set()
    for c in seq:
        s = str(c).strip().lower()
        if not s or s in seen: continue
        if s not in CONTENT_FOCUS:
            raise ValueError(f"unknown content_focus: {s!r}")
        seen.add(s); out.append(s)
    return out


def _load_raw(p) -> tuple[dict, list]:
    """personas.json as the RAW dict (absent -> empty) + its list. Mutating the raw dict (not
    Persona.model_dump) preserves unknown/future fields and sibling records exactly, like accounts.py."""
    raw = json.loads(p.read_text()) if p.exists() else {"personas": []}
    plist = raw.get("personas") if isinstance(raw, dict) else None
    if not isinstance(plist, list):
        raise ControlFileError(f"{p.name} invalid: expected a top-level 'personas' list")
    return raw, plist


def _write_atomic(p, raw: dict) -> None:
    """Persist via temp + os.replace (a unique mkstemp, same dir so replace stays atomic), so a crash
    mid-write never leaves a torn personas.json. Indented for the operator who still hand-edits."""
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh: fh.write(json.dumps(raw, indent=2) + "\n")
        os.replace(tmp, p)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise


@contextmanager
def _personas_txn(cfg: Config):
    """Serialize a mutator's read-modify-write under cfg.personas_lock_path (reuses the proven ledger
    flock; lazy import avoids a module-load cycle). mkdir the control dir first so a first-ever write on
    a fresh root can open the lock file."""
    from fanops.ledger import _file_lock
    cfg.personas_lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(cfg.personas_lock_path):
        yield


_UNSET = object()


def add_persona(cfg: Config, name: str, voice: str = "", tag_lean: str = "",
                intake: Optional[dict] = None, id: str = "", *, content_focus=None,
                energy: str = "", hook_angle: str = "", hook_tone: str = "",
                clip_profile: str = "", framing: str = "", brief: str = "",
                casting_directive: str = "", hook_directive: str = "", caption_directive: str = "",
                clip_count=None) -> str:
    """Create a NEW persona atomically. The id is the given slug or one derived from `name`; rejects a
    duplicate id and a blank name (never write a record that won't reload). Validates tag_lean AND every
    lever-engine field against its vocabulary. Returns the id; raises ValueError on bad input."""
    nm = (name or "").strip()
    if not nm:
        raise ValueError("persona name is required")
    pid = _slug(id) or _slug(nm)
    if not pid:
        raise ValueError(f"could not derive a persona id from name {name!r}")
    lean = (tag_lean or "").strip().lower()
    if lean and lean not in TAG_LEANS:
        raise ValueError(f"unknown tag_lean: {tag_lean!r}")
    focus = _norm_focus(content_focus)
    energy_v = _enum_or_none(energy, ENERGY_LEVELS, "energy")
    angle_v = _enum_or_none(hook_angle, HOOK_ANGLES, "hook_angle")
    tone_v = _enum_or_none(hook_tone, HOOK_TONES, "hook_tone")
    prof_v = _enum_or_none(clip_profile, PROFILE_NAMES, "clip_profile")
    fr_v = _enum_or_none(framing, FRAMING_NAMES, "framing")
    count_v = _clip_count_or_none(clip_count)
    p = cfg.personas_path
    with _personas_txn(cfg):
        raw, plist = _load_raw(p)
        if any(isinstance(d, dict) and d.get("id") == pid for d in plist):
            raise ValueError(f"duplicate persona id {pid!r} (already exists)")
        plist.append({"id": pid, "name": nm, "voice": str(voice or ""), "tag_lean": lean or None,
                      "hashtag_corpus": [], "intake": dict(intake or {}), "content_focus": focus,
                      "energy": energy_v, "hook_angle": angle_v, "hook_tone": tone_v,
                      "clip_profile": prof_v, "framing": fr_v, "brief": str(brief or ""),
                      "casting_directive": str(casting_directive or ""), "hook_directive": str(hook_directive or ""),
                      "caption_directive": str(caption_directive or ""), "clip_count": count_v})
        _write_atomic(p, raw)
    return pid


def update_persona(cfg: Config, pid: str, *, name=_UNSET, voice=_UNSET, tag_lean=_UNSET, intake=_UNSET,
                   content_focus=_UNSET, energy=_UNSET, hook_angle=_UNSET, hook_tone=_UNSET,
                   clip_profile=_UNSET, framing=_UNSET, brief=_UNSET, casting_directive=_UNSET,
                   hook_directive=_UNSET, caption_directive=_UNSET, clip_count=_UNSET) -> str:
    """Edit a persona's fields atomically (the A2 edit form). Only the fields PASSED change; tag_lean=""
    CLEARS the lean (-> None) and likewise each lever clears on "". Validates tag_lean + every passed lever
    against its vocabulary BEFORE the lock (never write a typo). Unknown id -> KeyError."""
    if tag_lean is not _UNSET:
        _l = (tag_lean or "").strip().lower()
        if _l and _l not in TAG_LEANS:
            raise ValueError(f"unknown tag_lean: {tag_lean!r}")
    _focus = _norm_focus(content_focus) if content_focus is not _UNSET else _UNSET
    _energy = _enum_or_none(energy, ENERGY_LEVELS, "energy") if energy is not _UNSET else _UNSET
    _angle = _enum_or_none(hook_angle, HOOK_ANGLES, "hook_angle") if hook_angle is not _UNSET else _UNSET
    _tone = _enum_or_none(hook_tone, HOOK_TONES, "hook_tone") if hook_tone is not _UNSET else _UNSET
    _prof = _enum_or_none(clip_profile, PROFILE_NAMES, "clip_profile") if clip_profile is not _UNSET else _UNSET
    _fr = _enum_or_none(framing, FRAMING_NAMES, "framing") if framing is not _UNSET else _UNSET
    _count = _clip_count_or_none(clip_count) if clip_count is not _UNSET else _UNSET
    p = cfg.personas_path
    with _personas_txn(cfg):
        raw, plist = _load_raw(p)
        found = False
        for d in plist:
            if isinstance(d, dict) and d.get("id") == pid:
                if name is not _UNSET:
                    _nm = str(name).strip()
                    if not _nm: raise ValueError("persona name cannot be blank")
                    d["name"] = _nm
                if voice is not _UNSET: d["voice"] = str(voice or "")
                if tag_lean is not _UNSET: d["tag_lean"] = ((tag_lean or "").strip().lower() or None)
                if intake is not _UNSET: d["intake"] = dict(intake or {})
                if _focus is not _UNSET: d["content_focus"] = _focus
                if _energy is not _UNSET: d["energy"] = _energy
                if _angle is not _UNSET: d["hook_angle"] = _angle
                if _tone is not _UNSET: d["hook_tone"] = _tone
                if _prof is not _UNSET: d["clip_profile"] = _prof
                if _fr is not _UNSET: d["framing"] = _fr
                if brief is not _UNSET: d["brief"] = str(brief or "")
                if casting_directive is not _UNSET: d["casting_directive"] = str(casting_directive or "")
                if hook_directive is not _UNSET: d["hook_directive"] = str(hook_directive or "")
                if caption_directive is not _UNSET: d["caption_directive"] = str(caption_directive or "")
                if _count is not _UNSET: d["clip_count"] = _count
                found = True
        if not found:
            raise KeyError(pid)
        _write_atomic(p, raw)
    return pid


def add_corpus_tag(cfg: Config, pid: str, tag: str) -> str:
    """Add ONE hashtag to a persona's curated corpus atomically — normalized (#prefix, lowercase),
    deduped, capped at _CORPUS_CAP. Empty tag -> ValueError. Unknown id -> KeyError."""
    h = _norm(tag)
    if not h:
        raise ValueError("empty hashtag")
    p = cfg.personas_path
    with _personas_txn(cfg):
        raw, plist = _load_raw(p)
        found = False
        for d in plist:
            if isinstance(d, dict) and d.get("id") == pid:
                cur = d.get("hashtag_corpus") if isinstance(d.get("hashtag_corpus"), list) else []
                out: list[str] = []; seen: set[str] = set()
                for t in list(cur) + [h]:
                    n = _norm(t) if isinstance(t, str) else ""
                    if n and n not in seen: seen.add(n); out.append(n)
                # Refuse a NEW tag past the cap rather than SILENTLY dropping it (an existing tag just
                # reorders/dedupes -> never grows past the cap, so it stays a clean no-op).
                if len(out) > _CORPUS_CAP:
                    raise ValueError(f"corpus full ({_CORPUS_CAP} tags) — remove one before adding {h}")
                d["hashtag_corpus"] = out
                found = True
        if not found:
            raise KeyError(pid)
        _write_atomic(p, raw)
    return pid


def remove_corpus_tag(cfg: Config, pid: str, tag: str) -> str:
    """Remove ONE hashtag from a persona's corpus atomically (normalization-insensitive). Unknown id ->
    KeyError; a tag not present is a no-op."""
    h = _norm(tag)
    p = cfg.personas_path
    with _personas_txn(cfg):
        raw, plist = _load_raw(p)
        found = False
        for d in plist:
            if isinstance(d, dict) and d.get("id") == pid:
                cur = d.get("hashtag_corpus") if isinstance(d.get("hashtag_corpus"), list) else []
                d["hashtag_corpus"] = [t for t in cur if isinstance(t, str) and _norm(t) != h]
                found = True
        if not found:
            raise KeyError(pid)
        _write_atomic(p, raw)
    return pid


def delete_persona(cfg: Config, pid: str) -> str:
    """Remove a persona atomically. Drops only the matching record; preserves siblings + unknown fields.
    Unknown id -> KeyError. (Accounts still linked keep the dangling id; load hydration falls open to
    their inline persona — never crashes.)"""
    p = cfg.personas_path
    with _personas_txn(cfg):
        raw, plist = _load_raw(p)
        kept = [d for d in plist if not (isinstance(d, dict) and d.get("id") == pid)]
        if len(kept) == len(plist):
            raise KeyError(pid)
        raw["personas"] = kept
        _write_atomic(p, raw)
    return pid


def research_corpus(cfg: Config, pid: str, *, limit: int = 8) -> list[str]:
    """B3: propose the reach-best hashtags this persona doesn't yet carry — the bootstrap "research my
    corpus" step. Grounded in the reach-ranked store (own-reach + Graph trends, default-ON) PLUS the
    persona's lean flavor pool, minus its current corpus. INSTANT + budget-free: the store already encodes
    the Graph signal (refresh_store blends it), so no per-candidate Graph call is spent here. The persona's
    flavor leads, then the reach-ranked universe. Returns an ordered list of candidate tags (most-reach
    first) the operator accepts into the corpus. Unknown id -> KeyError."""
    from fanops.hashtags import vetted_menu, load_store, _LEANS   # _norm already imported at module scope
    per = Personas.load(cfg).get(pid)
    if per is None:
        raise KeyError(pid)
    have = {_norm(t) for t in per.hashtag_corpus if isinstance(t, str)}
    lean_pool = _LEANS.get((per.tag_lean or "").strip().lower(), [])     # flavor leads
    ranked = vetted_menu(load_store(cfg))                                # store (own-reach+trends) else frozen reach-order
    out: list[str] = []; seen: set[str] = set()
    for t in lean_pool + ranked:
        n = _norm(t)
        if n and n not in have and n not in seen:
            seen.add(n); out.append(n)
    return out[:limit]


def discover_corpus(cfg: Config, pid: str, *, limit: int = 8, measure_k: int = 0, get=None) -> list[dict]:
    """M3: LIVE per-persona discovery — the upgrade from research_corpus's re-rank-what-we-know to
    finding tags we have never named. Seeds the Graph co-occurrence harvest from the persona's category
    (its corpus + lean flavor pool + intake `genre`), DROPS what we already know (VETTED ∪ reach store ∪
    corpus), and returns evidence-carrying proposals [{"tag","count","host_engagement",...}] reach-relevant
    first. FAIL-OPEN: no creds / nothing fresh / any Graph error -> today's offline research_corpus re-rank,
    wrapped as evidence-less {"tag": ...} dicts so the caller has ONE shape. measure_k defaults 0 (the free
    co-occurrence COUNT is the operator's evidence; per-tag reach stays the explicit 'Check reach' action) —
    the global refresh passes measure_k>0 to gate the menu on measured reach. Unknown id -> KeyError."""
    from fanops.hashtags import load_store, _LEANS, VETTED
    from fanops.meta_graph import discover_candidates
    per = Personas.load(cfg).get(pid)
    if per is None:
        raise KeyError(pid)
    corpus = [_norm(t) for t in per.hashtag_corpus if isinstance(t, str)]
    genre_seeds = [_norm("#" + w) for w in (per.intake.get("genre") or "").split() if w.strip()]   # `or ""`: a hand-edited "genre": null must not seed "#none"
    seeds = list(dict.fromkeys(corpus + _LEANS.get((per.tag_lean or "").strip().lower(), []) + genre_seeds))
    store = load_store(cfg) or []
    known = set(VETTED) | set(store) | set(corpus)
    try:
        cands = discover_candidates(cfg, seeds, known=known, measure_k=measure_k, get=get)
    except Exception:                                    # any Graph/transport error -> offline fallback
        cands = []
    if cands:
        return cands[:limit]
    return [{"tag": t} for t in research_corpus(cfg, pid, limit=limit)]   # FAIL-OPEN to the offline re-rank


def migrate_from_accounts(cfg: Config) -> dict:
    """Lift each account's inline persona string into a first-class Persona and LINK it (set persona_id),
    so the brief-seeded personas become editable + connectable. IDEMPOTENT: an account already linked is
    skipped, and a persona id that already exists is not recreated; an account with no inline persona is
    skipped (nothing to lift). Two SEQUENTIAL transactions (create personas, then link accounts) — never
    a nested lock. Returns {created:[ids], linked:[handles]}."""
    from fanops.accounts import Accounts, link_persona
    accts = Accounts.load(cfg)
    existing = {p.id for p in Personas.load(cfg).all()}
    created: list[str] = []; linked: list[str] = []
    for a in accts.accounts:
        if getattr(a, "persona_id", None):
            continue
        voice = (a.persona or "").strip()
        if not voice:
            continue
        pid = _slug(a.handle)
        if not pid:
            continue                                 # a handle with no usable slug (e.g. "@@@") -> never a false empty link
        if pid not in existing:
            add_persona(cfg, name=a.handle, voice=voice, tag_lean=(a.tag_lean or ""), id=pid)
            existing.add(pid); created.append(pid)
        link_persona(cfg, a.handle, pid)
        linked.append(a.handle)
    return {"created": created, "linked": linked}
