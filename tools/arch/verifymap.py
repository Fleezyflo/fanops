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

    # WAS `changed_state_machines`, which could NEVER ARM: `impact.py` initialized the dimension and
    # never wrote it (ADR-0105 §9 records it as one of two permanently dead requirements, gap G4).
    # The OBLIGATION was real, so it is re-homed rather than deleted — onto a predicate that is
    # actually derivable. `entities.json` already extracts every enum's member set, and `report()`
    # already holds both the base and head derived dicts in scope, so the delta costs no new I/O.
    Requirement("changed_enums",
                "transition tests",
                "Many writer sites move PostState, several of them GENERIC/DYNAMIC (model_copy, setattr, "
                "PostState(<str>)) that a literal grep cannot see. A new transition added without a "
                "guard is a new door. (Exact site counts: derived/side_effects.json + the IMPL-009 run.)",
                "a test that asserts the illegal source states are REFUSED, not just that the legal "
                "one works"),

    Requirement("changed_dependencies",
                "import-order tests",
                "The layered DAG holds ONLY because the upward imports are deferred to call time. "
                "Hoisting one LOOKS LIKE A CLEANUP and breaks the process at start. Nothing else catches it.",
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

    # `changed_rollback` WAS here, and could never arm either (`impact.py` initialized the key at
    # its declaration and never wrote it). It is retired WITHOUT a replacement dimension, because
    # none would be honest: rollback is a property of the CHANGE'S DECLARATION, not of its diff, and
    # no derivable code signal distinguishes "this change has a rehearsed rollback" from "this
    # change has none". Inventing a proxy would produce a requirement that fires on the wrong
    # things, which is how a checker earns a reputation for crying wolf.
    #
    # The obligation survives in two places that already exist and are both reachable:
    #   * ADR-0105 §3.1 field #17 `rollback` — MANDATORY on every contract, so its absence is a
    #     `clarification_required` (control `NC-C20b`); and
    #   * ADR-0105 §5.1's `live` row — rollback REHEARSAL, on every live change (`NC-C20c`).
    # That is strictly more coverage than this requirement ever provided, which was none.

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
         f"**{len(reqs)}** verification class(es).",
         "",
         # *** SAY WHAT IS TRUE, NOT WHAT SOUNDS RIGOROUS. ***
         # This line used to read "CI fails if a high-risk change ships without them." IT DOES NOT.
         # `verify` always exits 0; the only failing step in the impact job is `impact --strict`,
         # which fails on BREAKING_CHANGE / UNKNOWN_IMPACT and knows nothing about tests. Deciding
         # whether a given test DISCHARGES a verification class is a semantic judgement no static
         # checker can make, so no such gate exists — and claiming one did was AR-03 ("a check whose
         # name promises what its assertion does not deliver") committed by the system built to
         # prevent it, in the one sentence an operator was most likely to trust.
         "> ⚠️ **This is a requirement on the AUTHOR and the REVIEWER, not a CI gate.** CI cannot "
         "decide whether a particular test discharges a verification class — that is a semantic "
         "judgement. What CI *does* enforce is narrower and worth knowing exactly:",
         ">",
         "> * `impact --strict` fails the PR on `BREAKING_CHANGE` or `UNKNOWN_IMPACT`.",
         "> * `IMPL-006` fails the PR if a verification the matrix already names **disappears**.",
         ">",
         "> Nothing fails a PR merely for *not adding* the tests below. Ship them anyway, or say in "
         "review why the class does not apply.",
         ""]
    for r in reqs:
        L += [f"### `{r.verification}`  ← armed by `{r.trigger}`", "",
              f"{r.why}", "",
              f"**How to satisfy:** {r.evidence_hint}", ""]
    return "\n".join(L)
