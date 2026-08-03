import json
from pathlib import Path

# Old clause maps from persona_levers.py
_SCOPE_CLAUSE = {
    "open": "",
    "subject_locked": "Only moments featuring the account's named subject qualify — subject presence is the filter.",
    "source_briefed": "Select only moments matching the campaign brief — the brief defines footage and angle.",
    "credibility_first": "Favor clear and accurate over sensational; pass on cuts that misrepresent the source.",
    "controversy_seeking": "Prefer the most inflammatory or rivalry-coded statement in the source.",
}
_ANGLE_CLAUSE = {
    "curiosity": "open a curiosity gap the viewer has to close",
    "challenge": "dare or challenge the viewer to react",
    "emotional": "name the high-arousal feeling the clip gives the viewer",
    "result-first": "open on the payoff, then reveal how it got there",
    "fomo": "carry genuine scarcity — a one-time, leaked, or unreleased drop",
}

def migrate_record(d):
    # selection_scope
    scope = d.get("selection_scope")
    if scope in _SCOPE_CLAUSE:
        d["selection_scope"] = _SCOPE_CLAUSE[scope]
    
    # hook_angle
    angle = d.get("hook_angle")
    if angle in _ANGLE_CLAUSE:
        d["hook_angle"] = _ANGLE_CLAUSE[angle]
    
    return d

def migrate_file(p):
    if not p.exists():
        return
    print(f"Migrating {p}...")
    raw = json.loads(p.read_text())
    if "personas" in raw:
        raw["personas"] = [migrate_record(per) for per in raw["personas"]]
    p.write_text(json.dumps(raw, indent=2) + "\n")

if __name__ == "__main__":
    # 1. Baked personas (in-repo)
    baked = Path(__file__).resolve().parent.parent / "src/fanops/data/baked_personas.json"
    migrate_file(baked)
    
    # 2. Live personas (if FANOPS_ROOT set or default)
    import os
    root = Path(os.environ.get("FANOPS_ROOT", "~/FanOps")).expanduser()
    live = root / "MohFlow-FanOps/00_control/personas.json"
    migrate_file(live)
