from __future__ import annotations
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from fanops.config import Config

class Directive:
    def __init__(self, rendered: str = ""): self._rendered = rendered
    def __str__(self) -> str: return self._rendered
    def __bool__(self) -> bool: return bool(self._rendered)
    def __repr__(self) -> str: return f"Directive({self._rendered!r})"

def derive_cut_spec(p):
    """The CUT default a persona implies from its content_focus keywords."""
    foc = [s.lower() for s in (getattr(p, "content_focus", None) or [])]
    profile = next((v for v in ["short", "medium", "long", "talk", "song"] if v in foc), None)
    framing = next((v for v in ["top", "center", "bottom"] if v in foc), None)
    return profile, framing

def resolved_cut_spec(p):
    """The persona's EFFECTIVE cut spec = explicit pin OVER derived default OVER None (global)."""
    d_prof, d_fr = derive_cut_spec(p)
    prof = (getattr(p, "clip_profile", None) or "").strip() or d_prof
    fr = (getattr(p, "framing", None) or "").strip().lower() or d_fr
    return (prof or None, fr or None)

def _base_voice(p) -> str:
    """The persona's freeform base instruction — its voice."""
    return (getattr(p, "voice", None) or getattr(p, "persona", None) or "").strip()

def _join(voice: str, body: str) -> str:
    if voice and body: return f"{voice} {body}"
    return voice or body

def _casting_fragments(p) -> list[dict]:
    frags: list[dict] = []
    voice = _base_voice(p)
    if voice: frags.append({"source": "voice", "text": voice})
    foc = getattr(p, "content_focus", None) or []
    if foc: frags.append({"source": "content_focus", "text": "Clip for this account: " + "; ".join(foc) + "."})
    sc = (getattr(p, "selection_scope", None) or "").strip()
    if sc: frags.append({"source": "selection_scope", "text": sc})
    return frags

def _hook_fragments(p) -> list[dict]:
    frags: list[dict] = []
    voice = _base_voice(p)
    if voice: frags.append({"source": "voice", "text": voice})
    a = (getattr(p, "hook_angle", None) or "").strip()
    if a: frags.append({"source": "hook_angle", "text": "For the on-screen hook, " + a + "."})
    return frags

def _caption_fragments(p) -> list[dict]:
    voice = _base_voice(p)
    return [{"source": "voice", "text": voice}] if voice else []

def _cut_fragments(p) -> list[dict]:
    foc = getattr(p, "content_focus", None) or []
    if not foc: return []
    prof, fr = derive_cut_spec(p)
    frags = []
    if prof: frags.append({"source": "content_focus", "text": f"Length: {prof}", "band": prof})
    if fr: frags.append({"source": "content_focus", "text": f"Framing: {fr}", "framing": fr})
    return frags

def casting_directive(p) -> Directive:
    return Directive(_casting_fragments(p))

def hook_directive(p) -> Directive:
    return Directive(_hook_fragments(p))

def caption_directive(p) -> Directive:
    return Directive(_caption_fragments(p))

def compose_persona_instruction(p) -> str:
    return str(casting_directive(p))

def lever_catalog() -> list[dict]:
    return []

def compose_breakdown_detail(cfg: "Config", p) -> dict:
    prof, fr = resolved_cut_spec(p)
    from fanops.bands import band_for
    band = band_for(prof)
    return {
        "casting": {"text": str(casting_directive(p)), "fragments": _casting_fragments(p)},
        "hook": {"text": str(hook_directive(p)), "fragments": _hook_fragments(p), "angle": getattr(p, "hook_angle", "")},
        "caption": {"text": str(caption_directive(p)), "fragments": _caption_fragments(p)},
        "cut": {"band": f"{band.lo:.0f}-{band.hi:.0f}s", "framing": fr, "fragments": _cut_fragments(p), "source": "persona"},
        "tags": {"terms": ", ".join(getattr(p, "niche", [])), "lead": []}
    }

def compose_breakdown(cfg: "Config", p) -> list[dict]:
    out: list[dict] = []
    labels = {"content_focus": "Editorial Focus", "selection_scope": "Selection Scope", "hook_angle": "Hook Angle", "intensity": "Intensity", "voice": "Voice", "niche": "Territory seeds"}
    
    
    def _produces(key):
        if key == "voice": return getattr(p, "voice", "")
        if key == "content_focus": return "; ".join(getattr(p, "content_focus", []))
        if key == "selection_scope": return getattr(p, "selection_scope", "")
        if key == "hook_angle": return getattr(p, "hook_angle", "")
        if key == "niche": return ", ".join(getattr(p, "niche", []))
        return ""

    for key in ["voice", "content_focus", "selection_scope", "hook_angle", "intensity", "niche"]:
        out.append({
            "key": key, 
            "label": labels.get(key, key.replace("_", " ").title()),
            "channels": [], 
            "value": getattr(p, key, None), 
            "produces": _produces(key), 
            "source": key, 
            "health": "ok"
        })
    return out

def produces_summary(breakdown: dict) -> list[str]:
    out: list[str] = []
    cut = breakdown.get("cut") or {}
    if cut.get("band"):
        out.append(f"~{cut['band']} clips")
    angle = (breakdown.get("hook") or {}).get("angle")
    if angle:
        out.append(f"{angle} hooks")
    return out

def persona_facts(cfg: "Config", p) -> dict:
    from fanops.bands import band_for
    from fanops.hashtags import vet_hashtags, load_measurements
    from fanops.models import Platform
    from fanops.persona_research import _aligned_pool, persona_terms
    
    prof, fr = resolved_cut_spec(p)
    band = band_for(prof)
    
    try:
        pool = _aligned_pool(p, load_measurements(cfg), cfg=cfg)
        store = [t for t, _v, _s in pool] or None
    except Exception:
        store = None
        
    lead = vet_hashtags([], Platform.instagram,
                        corpus=list(getattr(p, "hashtag_corpus", None) or []), 
                        store=store,
                        cfg=cfg)
    return {
        "length_band": f"{band.lo:.0f}-{band.hi:.0f}s", 
        "framing": fr, 
        "lead_tags": lead,
        "terms": persona_terms(p, cfg)
    }
