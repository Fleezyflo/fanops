"""Shared paths, deterministic serialization, and the repo's canonical locations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "fanops"
TESTS = REPO / "tests"

ARCH = REPO / ".reports" / "architecture"
KB = ARCH / "kb"                      # DECLARED (Cycle 5) — canonical, validated, never overwritten
CONTRACT = ARCH / "contract"          # DECLARED (Cycle 6) — canonical, validated, never overwritten
DERIVED = ARCH / "derived"            # GENERATED — never hand-edited
GOVERNANCE = ARCH / "governance"      # policy, exceptions, unknowns, field authority

REGEN_CMD = "python -m tools.arch regen"

# The regeneration command is recorded INSIDE every generated artifact. A generated file that
# does not name the command that produced it is a file nobody can reproduce.


def dumps(obj: object) -> str:
    """The ONE serializer. Deterministic: sorted keys, fixed indent, trailing newline.

    A generated artifact is a PURE FUNCTION OF THE SOURCE TREE. Nothing else. Not the clock, not
    the machine, not the user, and — the one that actually bit us — NOT THE GIT COMMIT.

    Every artifact used to carry `repository_commit: git rev-parse HEAD`, justified as "provenance
    is the COMMIT, which is deterministic; the clock is not." That is wrong, and it is wrong in a
    way that makes the entire gate unsatisfiable. The byte-compare asks: *does regeneration from
    this source tree reproduce the committed bytes?* A commit stamp answers a different question,
    and it is SELF-INVALIDATING: the moment you commit the artifact, HEAD becomes a new SHA, so
    regenerating in CI produces a different byte and the gate goes RED — and regenerating to "fix"
    it produces yet another commit, hence yet another SHA. It never converges. It would have failed
    every PR, forever, starting with the one that introduced it.

    The tree's identity is the `fingerprint` (a content hash of the sources). That is real
    provenance: it is derived from exactly the inputs the artifacts are derived from.
    """
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write(path: Path, obj: object) -> bool:
    """Write deterministically. Returns True if the bytes changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new = dumps(obj)
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == new:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def provenance(schema: str, *, inputs: list[str], confidence: str = "CERTAIN") -> dict:
    """The metadata block every generated artifact carries."""
    return {
        "$schema": schema,
        "generated_by": "tools/arch",
        "generator_version": _GENERATOR_VERSION,
        "regeneration_command": REGEN_CMD,
        # NO `repository_commit` HERE. See dumps(). A commit stamp cannot survive being committed.
        "source_inputs": sorted(inputs),
        "confidence": confidence,
        "hand_edits": "FORBIDDEN — this file is regenerated. Edit the code, or the DECLARED "
                      "artifact it derives from, and re-run the regeneration command.",
    }


_GENERATOR_VERSION = "arch/1.0.0"
