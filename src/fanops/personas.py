# src/fanops/personas.py
"""A1 — Personas as a FIRST-CLASS entity. Until now a "persona" was only a free-text Account.persona
string + tag_lean, seeded by hand from a brief doc — not editable, not reusable, not a thing you could
add an intake for. This makes a Persona a named record in 00_control/personas.json: a `voice` (the
string the pipeline reads), a `tag_lean`, a `hashtag_corpus` (the per-persona reach-vetted pool, B1),
and `intake` metadata whose one live field is `genre` (seeds B3's research). Accounts LINK
to a persona via Account.persona_id; the linked persona's voice/tag_lean HYDRATE the account in memory
at load (accounts._hydrate_from_personas), so every existing consumer (caption/moments/casting/
variant_transfer) stays byte-identical while an operator edit takes effect on the next load.

This module is the FOUNDATION — the Persona record + the Personas read-store + the lever vocabularies +
the id slug. The directive/compose engine, the writers, and the corpus research live in cohesive sibling
modules (persona_directives / persona_store / persona_research, audit #6); every name they own is
RE-EXPORTED below, so every existing `from fanops.personas import X` keeps resolving unchanged — and
`fanops.personas.discover_corpus` stays patchable at that exact attribute (tests monkeypatch it there)."""
from __future__ import annotations
import json
import re
from typing import Optional
from pydantic import BaseModel, Field
from fanops.config import Config
from fanops.errors import ControlFileError, reason as _reason
from fanops.persona_levers import vocab as _lever_vocab

# The lever-engine vocabularies (the validated control surface — one lever per persona characteristic). Each
# is the WRITE boundary for its lever: add/update_persona refuses an unknown value (never write a typo that
# reloads as a silent no-op), and compose_persona_instruction renders the SET levers into the single
# instruction the casting/hook/caption prompts read. M1: these are now PROJECTIONS of the single lever
# registry (fanops.persona_levers) — the same declaration the clause maps + lever_catalog derive from, so the
# three can no longer drift. clip_profile/framing reuse the Account validators (bands.PROFILE_NAMES /
# config.FRAMING_NAMES) so a persona pins the SAME deterministic CUT an account can.
CONTENT_FOCUS = _lever_vocab("content_focus")
ENERGY_LEVELS = _lever_vocab("energy")
HOOK_ANGLES = _lever_vocab("hook_angle")


class Persona(BaseModel):
    id: str                                       # stable slug (the link key on Account.persona_id)
    name: str = ""                                # operator-facing display name
    voice: str = ""                               # the persona string the pipeline reads (caption/hook/casting voice)
    hashtag_corpus: list[str] = Field(default_factory=list)   # B1: the per-persona reach-vetted pool — the SOLE per-account hashtag differentiator (tag_lean folded in, M3)
    intake: dict = Field(default_factory=dict)    # intake metadata; one live field `genre` — seeds B3 research
    # Lever engine: explicit per-characteristic DIRECTION that compose_persona_instruction renders into the
    # one instruction the casting/hook/caption prompts read. ADDITIVE — all empty on a legacy persona, so
    # compose returns the bare `voice` (byte-identical). Validated at the write boundary (add/update_persona).
    content_focus: list[str] = Field(default_factory=list)   # which moment KINDS to favor (casting): CONTENT_FOCUS
    energy: Optional[str] = None                  # clip energy: low|medium|high (ENERGY_LEVELS)
    hook_angle: Optional[str] = None              # on-screen hook strategy: curiosity|challenge|... (HOOK_ANGLES)
    # M3 (2026-06-27): the per-persona clip_profile/framing PINS were RETIRED — invisible (no editor) + duplicate
    # of the content_focus/energy-DERIVED cut (derive_cut_spec). A persona's cut LENGTH now derives from
    # content_focus and FRAMING from energy; the Account.clip_profile/framing carriers + the global
    # FANOPS_CLIP_PROFILE lever stay. resolved_cut_spec is duck-typed, so an absent Persona pin -> derived.
    # M3e (2026-06-27): the 3 freeform per-dimension OVERRIDES (casting/hook/caption_directive) were RETIRED —
    # invisible (no editor) + shadow-duplicates of the structured levers, an unaudited verbatim-injection
    # surface. The structured levers (content_focus/energy/hook_angle) now ALWAYS compile the directives; the
    # voice carries any freeform register. The compile FUNCTIONS persona_directives.casting_directive/
    # hook_directive/caption_directive stay (they are the compile, not the override).


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


# Re-export the sibling modules' public surface so `from fanops.personas import X` keeps resolving for every
# existing consumer (the facade contract). These imports sit AFTER the foundation above — the siblings import
# the foundation back from this partially-initialized module, which already holds those names, so there is no
# cycle. discover_corpus is bound here as an attribute, which is what tests patch + fanops_hashtags reads.
from fanops.persona_directives import (   # noqa: E402,F401  (facade re-export; after foundation by design)
    derive_cut_spec, resolved_cut_spec, casting_directive, hook_directive, caption_directive,
    compose_persona_instruction, lever_catalog, compose_breakdown, produces_summary, persona_facts,
    _FOCUS_CLAUSE, _ENERGY_CLAUSE, _ANGLE_CLAUSE, _FOCUS_PROFILE, _ENERGY_FRAMING)
from fanops.persona_store import (   # noqa: E402,F401
    add_persona, update_persona, add_corpus_tag, remove_corpus_tag,
    delete_persona, migrate_from_accounts)
from fanops.persona_research import research_corpus, discover_corpus   # noqa: E402,F401
from fanops.persona_levers import LEVER_REGISTRY, build_catalog as _registry_build_catalog   # noqa: E402,F401  (facade re-export of the M1 registry)
