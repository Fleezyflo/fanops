# src/fanops/personas.py
"""A1 — Personas as a FIRST-CLASS entity. Until now a "persona" was only a free-text Account.persona
string + tag_lean, seeded by hand from a brief doc — not editable, not reusable, not a thing you could
add. This makes a Persona a named record in 00_control/personas.json: a `voice` (the
string the pipeline reads), a `hashtag_corpus` (the per-persona reach-vetted pool, B1),
and a declared `niche` (subject terms for hashtag discovery). Accounts LINK
to a persona via Account.persona_id; the linked persona's voice HYDRATES the account in memory
at load (accounts._hydrate_from_personas), so every existing consumer (caption/moments/casting/
variant_transfer) stays byte-identical while an operator edit takes effect on the next load.

This module is the FOUNDATION — the Persona record + the Personas read-store + the lever vocabularies +
the id slug. The directive/compose engine, the writers, and the corpus research live in cohesive sibling
modules (persona_directives / persona_store / persona_research, audit #6); every name they own is
RE-EXPORTED below, so every existing `from fanops.personas import X` keeps resolving unchanged — and
`fanops.personas.discover_corpus` stays patchable at that exact attribute (tests monkeypatch it there)."""
from __future__ import annotations
import json
import logging
import re
from typing import Optional
from pydantic import BaseModel, Field
from fanops.config import Config
from fanops.errors import ControlFileError, reason as _reason
from fanops.persona_levers import vocab as _lever_vocab

_log = logging.getLogger("fanops.personas")

# The lever-engine vocabularies (the validated control surface — one lever per persona characteristic). Each
# is the WRITE boundary for its lever: add/update_persona refuses an unknown value (never write a typo that
# reloads as a silent no-op), and compose_persona_instruction renders the SET levers into the single
# instruction the casting/hook/caption prompts read. M1: these are now PROJECTIONS of the single lever
# registry (fanops.persona_levers) — the same declaration the clause maps + lever_catalog derive from, so the
# three can no longer drift. clip_profile/framing reuse the Account validators (bands.PROFILE_NAMES /
# config.FRAMING_NAMES) so a persona pins the SAME deterministic CUT an account can.
CONTENT_FOCUS = _lever_vocab("content_focus")
CUT_POLICY = _lever_vocab("cut_policy")          # MOL-523: the ONE surviving token vocabulary (it derives the cut)
SELECTION_SCOPE_LEVELS = _lever_vocab("selection_scope")
HOOK_ANGLES = _lever_vocab("hook_angle")
INTENSITY = _lever_vocab("intensity")


class Persona(BaseModel):
    id: str                                       # stable slug (the link key on Account.persona_id)
    name: str = ""                                # operator-facing display name
    voice: str = ""                               # the persona string the pipeline reads (caption/hook/casting voice)
    hashtag_corpus: list[str] = Field(default_factory=list)   # DERIVED by persona_research.derive_corpus from platform measurements — never hand-curated, recomputed every tick
    niche: list[str] = Field(default_factory=list)   # the persona's DECLARED subject terms; required at writers (add/update_persona refuse empty); per-entry validated at the write boundary, like `name`
    # Lever engine: explicit per-characteristic DIRECTION that compose_persona_instruction renders into the
    # one instruction the casting/hook/caption prompts read. ADDITIVE — all empty on a legacy persona, so
    # compose returns the bare `voice` (byte-identical). Validated at the write boundary (add/update_persona).
    content_focus: Optional[str] = None         # editorial focus (free text; formerly CONTENT_FOCUS multi-select)
    cut_policy: list[str] = Field(default_factory=list) # deterministic cut policy (tokens; MOL-523)
    selection_scope: Optional[str] = None         # selection constraint (free text; formerly SELECTION_SCOPE_LEVELS)
    hook_angle: Optional[str] = None              # on-screen hook strategy (free text; formerly HOOK_ANGLES)
    intensity: Optional[str] = None  # peak-filter tier: high|medium|low (INTENSITY); unset → None → no filter
    # M3 (2026-06-27): the per-persona clip_profile/framing PINS were RETIRED — invisible (no editor) + duplicate
    # of the content_focus-DERIVED cut (derive_cut_spec). A persona's cut LENGTH + FRAMING now derive from
    # content_focus; the Account.clip_profile/framing carriers + the global FANOPS_CLIP_PROFILE lever stay.
    # FANOPS_CLIP_PROFILE lever stay. resolved_cut_spec is duck-typed, so an absent Persona pin -> derived.
    # M3e (2026-06-27): the 3 freeform per-dimension OVERRIDES (casting/hook/caption_directive) were RETIRED —
    # invisible (no editor) + shadow-duplicates of the structured levers, an unaudited verbatim-injection
    # surface. The structured levers (content_focus/selection_scope/hook_angle) now ALWAYS compile the directives; the
    # voice carries any freeform register. The compile FUNCTIONS persona_directives.casting_directive/
    # hook_directive/caption_directive stay (they are the compile, not the override).


class Personas:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.personas: list[Persona] = []
        # Per-row parse failures collected at load (index + reason). Mirrors Accounts.skipped_rows
        # (MOL-79): ONE stray null / missing-field row must degrade to "that row skipped" rather than
        # crash the whole registry. NOT silent: validate() promotes these to problems + load logs.
        self.skipped_rows: list[str] = []

    @classmethod
    def load(cls, cfg: Config) -> "Personas":
        r = cls(cfg)
        p = cfg.personas_path
        if p.exists():
            text = p.read_text()                       # an I/O error here is a real problem, not "invalid"
            try:
                raw = json.loads(text)                 # a corrupt file (bad JSON) still fails loud
            except Exception as e:
                raise ControlFileError(f"{p.name} invalid: {_reason(e)}") from e
            # Wrong top-level shape is NOT a per-row typo — fail loud like Accounts.load.
            if not isinstance(raw, dict):
                raise ControlFileError(
                    f"{p.name} invalid: top-level must be an object with a 'personas' list, got {type(raw).__name__}")
            # Per-ROW leniency (Accounts.load parity): one bad row is skipped + recorded + logged;
            # every other persona still loads. Skips surface via validate() -> doctor.
            for i, x in enumerate(raw.get("personas", [])):
                try:
                    if not isinstance(x, dict):
                        raise TypeError(f"expected object, got {type(x).__name__}")
                    d = dict(x)
                    d.pop("intake", None)                       # MOL-529: leftover intake keys ignored at load
                    r.personas.append(Persona(**d))
                except Exception as e:
                    reason = _reason(e)
                    r.skipped_rows.append(f"row {i}: {reason}")
                    _log.warning("personas.json %s — malformed, skipped: %s", f"row {i}", reason)
        return r

    def get(self, pid: Optional[str]) -> Optional[Persona]:
        return next((p for p in self.personas if p.id == pid), None) if pid else None

    def all(self) -> list[Persona]:
        return list(self.personas)

    def validate(self) -> list[str]:
        """Config-integrity problems to surface (doctor/health). A per-row parse skip at load is
        named HERE so a silently-dropped persona cannot vanish without a doctor hint — mirrors
        Accounts.validate's skipped_rows promotion."""
        return [f"personas.json {s} — malformed, skipped (fix the row in personas.json)"
                for s in self.skipped_rows]


def _slug(s: str) -> str:
    """A stable id from a name/handle: lowercase, drop a leading '@', non-alphanumerics -> single '-'."""
    s = (s or "").strip().lower().lstrip("@")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


# Re-export the sibling modules' public surface so `from fanops.personas import X` keeps resolving for every
# existing consumer (the facade contract). These imports sit AFTER the foundation above — the siblings import
# the foundation back from this partially-initialized module, which already holds those names, so there is no
# cycle. The corpus mutators (add_corpus_tag/remove_corpus_tag) and the proposal flows (research_corpus/
# discover_corpus) are GONE: the corpus is derived from platform measurements, so there is nothing to curate
# by hand and nothing to propose for approval.
from fanops.persona_directives import (   # noqa: E402,F401  (facade re-export; after foundation by design)
    derive_cut_spec, resolved_cut_spec, casting_directive, hook_directive, hook_author_slot, caption_directive,
    compose_persona_instruction, lever_catalog, compose_breakdown, produces_summary, persona_facts, manifest,
    _FOCUS_CLAUSE, _FOCUS_PROFILE, _FRAMING_MAP)
from fanops.persona_store import (   # noqa: E402,F401
    add_persona, update_persona, apply_auto_corpus,
    delete_persona, migrate_from_accounts, link_personas_by_voice,
    baked_personas, ensure_baked_personas)
from fanops.persona_research import persona_terms, derive_corpus, derived_report   # noqa: E402,F401
from fanops.persona_levers import LEVER_REGISTRY, build_catalog as _registry_build_catalog   # noqa: E402,F401  (facade re-export of the M1 registry)
