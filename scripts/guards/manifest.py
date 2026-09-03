"""Load .agents/lanes.json — single source for lane guard and collision guard."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _repo_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except Exception:
        pass
    return Path(__file__).resolve().parents[2]


def load_manifest(path=None) -> dict:
    p = Path(path) if path else (_repo_root() / ".agents" / "lanes.json")
    return json.loads(Path(p).read_text())
