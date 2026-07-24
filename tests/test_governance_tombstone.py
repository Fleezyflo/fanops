"""The governance-prose tombstone (cleanup route, 2026-07).

The theatre census (2026-07-24, archived in main history) proved the prose legal system's stated
gates were overwhelmingly backed by no mechanism; the cleanup route deleted it. This test pins the
deletion in the required unit lane: the namespaces stay dead and the index of real enforcers exists.
A rule ships as an enforcer or it does not ship — docs/ENFORCEMENT.md is the index.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEAD = [
    "tools/contract",
    "docs/adr",
    "docs/constitution",
    "docs/contracts",
    "docs/governance",
    "docs/ci",
    "docs/superpowers",
    "docs/reconciliation",
    "docs/REPOSITORY_CONSTITUTION.md",
    "docs/ARCHITECTURAL_LAWS.md",
    "docs/ARCHITECTURE_GOVERNANCE.md",
    "docs/ARCH_RUNBOOK.md",
    "docs/CI_ARCHITECTURE_REVIEW.md",
    "docs/CI_SLO.md",
    "docs/CONSTITUTION-EVIDENCE-DOSSIER.md",
    "docs/SCAFFOLDING-VERDICT.md",
    "docs/ENGINEERING_PHILOSOPHY.md",
    "docs/handoff.md",
    "scripts/ci_e2e_trigger.py",
]


def test_governance_prose_stays_deleted():
    resurrected = [p for p in DEAD if (ROOT / p).exists()]
    assert not resurrected, f"deleted governance namespaces resurrected: {resurrected}"


def test_enforcement_index_exists():
    assert (ROOT / "docs" / "ENFORCEMENT.md").is_file()
