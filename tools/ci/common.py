"""Shared paths and the divergence `Finding` type for the tools/ci validator."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO / ".github" / "ci-control-registry.yml"
SCHEMA = REPO / ".github" / "ci-control-registry.schema.json"
WORKFLOWS = REPO / ".github" / "workflows"
GEN_VIEW = REPO / "docs" / "ci" / "CI_CONTROL_INVENTORY.md"

# Hand-maintained docs that may make required/advisory claims about CI contexts (DC-4).
# Kept small and explicit — the generated inventory is covered by the byte-compare, not by DC-4.
PROSE_DOCS = [REPO / "AGENTS.md"]

DEFAULT_REPO = "Fleezyflo/fanops"
DEFAULT_BRANCH = "main"


@dataclass(frozen=True)
class Finding:
    """One plane-divergence, with the control id and the EXACT divergence for actionable output.

    blocking=True  -> a real failure.
    skipped=True   -> non-authoritative (e.g. no GitHub access); NEVER counted as a pass.
    """
    dc: str
    control_id: str
    divergence: str
    blocking: bool = True
    skipped: bool = False

    def render(self) -> str:
        tag = "SKIP" if self.skipped else ("FAIL" if self.blocking else "INFO")
        return f"[{tag}] {self.dc} · {self.control_id}: {self.divergence}"
