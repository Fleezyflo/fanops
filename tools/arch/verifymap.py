"""Verification mapping: an architectural change class -> the verification it REQUIRES.

CI fails when a high-risk change ships without the verification class its change demands. This is
the mechanism that stops "the tests pass" from being mistaken for "the change is proven" — a
distinction this codebase has already paid for once: a green CI test asserted the data-loss
outcome of the restore race and called it correct (`RC-5` / `AR-03`).

Which is why every rule below names the verification by what it must PROVE, not by a filename.
"""
from __future__ import annotations

from dataclasses import dataclass



@dataclass(frozen=True)
class Requirement:
    trigger: str            # the change class that arms it
    verification: str       # the class of proof required
    why: str
    evidence_hint: str      # how a reviewer confirms it


REQUIREMENTS: list[Requirement] = [
    Requirement("changed_persistence",
                "migration tests",
                "A ledger schema is FORWARD-ONLY and a newer on-disk schema is REFUSED, never "
                "downgraded (INV-13). An unmigrated field is a load-time failure for every reader "
                "at once — the daemon and every Studio page.",
                "a test that loads a ledger written by the PREVIOUS schema version"),

    Requirement("changed_state_machines",
                "transition tests",
                "21 writer sites move PostState, five of them GENERIC/DYNAMIC (model_copy, setattr, "
                "PostState(<str>)) that a literal grep cannot see. A new transition added without a "
                "guard is a new door.",
                "a test that asserts the illegal source states are REFUSED, not just that the legal "
                "one works"),

    Requirement("changed_dependencies",
                "import-order tests",
                "The 11-level DAG holds ONLY because 107 imports are deferred to call time. Hoisting "
                "one LOOKS LIKE A CLEANUP and breaks the process at start. Nothing else catches it.",
                "`python -c 'import fanops.<module>'` for the module at the TOP of the new edge, "
                "in a clean interpreter"),

    Requirement("changed_ownership",
                "dependency tests",
                "A module that changes subsystem changes who is allowed to write it and who reviews "
                "it. Ownership is the input to every other rule here.",
                "kb/subsystems.json updated AND the module's new subsystem's dependency set still "
                "closes"),

    Requirement("changed_side_effects",
                "trust-boundary tests",
                "The cardinal rule the codebase actually honours is that NO network call and NO heavy "
                "subprocess ever runs inside the ledger lock. A new effect must be shown to respect it.",
                "a test asserting the new effect is OUTSIDE `Ledger.transaction`"),

    Requirement("changed_integrations",
                "contract tests + config declaration",
                "A new env var is an undocumented input to a live system, and the operator is a "
                "DOCUMENTED hand-editor of .env (AR-09). The load boundary is where the gap is.",
                "declared in kb/configuration.json and docs/CONFIG.md, with a load-boundary test"),

    Requirement("changed_boundaries",
                "slice verification",
                "A slice that silently widens is how one slice's scope becomes another's. The "
                "boundary is the contract.",
                "contract/file_ownership.json updated as a REVIEWED scope change"),

    Requirement("changed_rollback",
                "rollback validation",
                "'Revert' is not one thing. Two slices in this program are NOT simply revertible: "
                "S02 can be WORLD_IRREVERSIBLE (posts on the internet) and S04 DATA_IRREVERSIBLE.",
                "the rollback CLASS stated in the PR, and the residue MEASURED if not CODE_REVERSIBLE"),

    Requirement("changed_preserved_behaviors",
                "merge-gate validation",
                "`_CLI_PRINT_COUNT` is an EXACT-EQUALITY budget shared across three slices. Two PRs "
                "moving it collide, and the second goes red for a reason unrelated to its change.",
                "the ratchet test updated IN THIS PR, and no other open PR touching the constant"),
]

_CONCURRENCY = Requirement(
    "concurrency", "race tests",
    "SQLite locking is per-INODE and CROSS-PROCESS. Every experiment in this audit used THREADS in "
    "one process and is therefore INDICATIVE, NOT SUFFICIENT.",
    "a TWO-PROCESS contention test, not a two-thread one")


def required_for(impact: dict) -> list[Requirement]:
    """The verification classes this diff arms."""
    armed: list[Requirement] = []
    fired = {k for section in ("architecture", "implementation")
             for k, v in impact.get(section, {}).items() if v}
    for r in REQUIREMENTS:
        if r.trigger in fired:
            armed.append(r)

    touched = impact.get("touched_src", [])
    if any(f.endswith(("ledger.py", "ledger_sqlite.py", "pipeline_run.py", "stage_lock.py"))
           for f in touched):
        armed.append(_CONCURRENCY)
    return armed


def render(reqs: list[Requirement], impact: dict) -> str:
    if not reqs:
        return ("## Required verification\n\nNone — this diff arms no high-risk change class.\n")
    L = ["## Required verification", "",
         f"This diff is classified **{impact['classification']}** and arms "
         f"**{len(reqs)}** verification class(es). CI fails if a high-risk change ships without them.",
         ""]
    for r in reqs:
        L += [f"### `{r.verification}`  ← armed by `{r.trigger}`", "",
              f"{r.why}", "",
              f"**How to satisfy:** {r.evidence_hint}", ""]
    return "\n".join(L)
