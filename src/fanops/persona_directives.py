from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from fanops.config import Config
    from .personas import Persona

def derive_cut_spec(p: Persona) -> dict:
    """The CUT default a persona implies from its content_focus keywords."""
    foc = p.content_focus.lower()
    profile = next((v for v in ["short", "medium", "long", "talk", "song"] if v in foc), "medium")
    framing = next((v for v in ["top", "center", "bottom"] if v in foc), "center")
    return {"profile": profile, "framing": framing}

def resolved_cut_spec(p: Persona, cfg: Config) -> dict:
    """The final cut spec, resolving persona defaults against operator config overrides."""
    base = derive_cut_spec(p)
    if cfg.cut_profile_override:
        base["profile"] = cfg.cut_profile_override
    return base

def casting_directive(p: Persona) -> str:
    """The free-text editorial directive for casting (selecting moments)."""
    return p.selection_scope

def hook_directive(p: Persona) -> str:
    """The free-text editorial directive for hook generation."""
    return p.hook_angle

def hook_author_slot(p: Persona) -> str:
    """Compatibility wrapper for hook generation."""
    return hook_directive(p)

def caption_directive(p: Persona) -> str:
    """The free-text editorial directive for caption generation."""
    return p.content_focus
