import json
from pathlib import Path

PERSONAS_FILE = Path("/Users/molhamhomsi/fanops-mol-521/src/fanops/data/baked_personas.json")

def migrate_personas():
    if not PERSONAS_FILE.exists():
        print(f"Error: {PERSONAS_FILE} not found.")
        return

    with open(PERSONAS_FILE, "r") as f:
        data = json.load(f)

    migrated_personas = []
    for persona in data.get("personas", []):
        # Convert content_focus from list to string
        if isinstance(persona.get("content_focus"), list):
            persona["content_focus"] = ", ".join(persona["content_focus"])
        elif persona.get("content_focus") is None:
            persona["content_focus"] = ""

        # Ensure selection_scope and hook_angle are strings
        if persona.get("selection_scope") is None:
            persona["selection_scope"] = ""
        if persona.get("hook_angle") is None:
            persona["hook_angle"] = ""

        # Add default values for new fields if not present
        if "clip_profile" not in persona:
            persona["clip_profile"] = None
        if "framing_bias" not in persona:
            persona["framing_bias"] = None

        migrated_personas.append(persona)

    data["personas"] = migrated_personas

    with open(PERSONAS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Successfully migrated {len(migrated_personas)} personas in {PERSONAS_FILE}")

if __name__ == "__main__":
    migrate_personas()
