# Agent Change System — Program Roadmap

> **This file owns SEQUENCE and STATUS only.** It records where the program is, what was decided, what
> blocks it, and what the next gate is. It states no law, standard, ADR policy, CI policy, schema or
> executable rule — those live in their own authorities and are linked, never restated.

> **⛔ THE PROGRAM IS CLOSED. Phases 4–8 are CANCELLED (operator decision, 2026-07-22).** Phases 1–3
> shipped and are `ACCEPTED`; what they built — the trigger model, preflight, the declaration, the
> digest approval and the scope check — is live and stays live. Everything staged after Phase 3 was
> further ceremony, and the ceremony is what was cancelled. **This file is now a historical record of
> a finished program. It has no next gate and no open work.**

## Objective

A reusable, mechanically-verified change contract for agent-authored changes to this repository:
truthful authority surfaces first, then the contract architecture, then its compiler and verifier.
Phases 4–8 would have added cold-start acceptance, governance deployment, an orchestration decision,
production acceptance and a freeze; none was started, and none will be.

## Phases

| # | Phase | Status | Entry criteria | Exit criteria |
|---|---|---|---|---|
| 1 | Authority Repair and Program Boundaries | **ACCEPTED** | A verified contradiction register on a named base SHA | The six verified contradictions closed; this roadmap persisted; `tools/arch` + `tools/ci` clean; CI green on the exact PR head |
| 2 | Reusable Change-Contract Architecture | **ACCEPTED** | Phase 1 `ACCEPTED` | An accepted ADR defining the change-contract model |
| 3 | Change-Contract Compiler and Verifier | **ACCEPTED** | Phase 2 `ACCEPTED` | Compiler + verifier merged, each rule carrying a firing negative control; acceptance verified against the platform rather than asserted by its own row (ADR-0105 §4.3a) |
| 4 | Cold-Start Acceptance | ⛔ **CANCELLED** | — | — |
| 5 | Operational Governance Deployment | ⛔ **CANCELLED** | — | — |
| 6 | Orchestration Enforcement Decision | ⛔ **CANCELLED** | — | — |
| 7 | Production Acceptance | ⛔ **CANCELLED** | — | — |
| 8 | Closeout and Freeze | ⛔ **CANCELLED** | — | — |

**Status values:** `NOT STARTED` → `IN DESIGN` → `APPROVED` → `IN IMPLEMENTATION` → `ACCEPTED`, or
`CANCELLED`. **`CANCELLED` is not `DEFERRED`.** A cancelled phase is not waiting for a gate, a
prerequisite or a quieter week; there is nothing to resume and no condition under which it resumes.
Re-opening any of this would be a NEW program with its own ADR, never a continuation of this one.

**A later phase may begin only after the preceding phase is `ACCEPTED`.** That rule stands for the
record; with 4–8 cancelled it has nothing left to sequence.

### Why 4–8 were cancelled

Each was ceremony layered on a system that already worked without it, and the cancellations landed
across two decisions:

- **Phase 5 (OGD)** — cancelled 2026-07-22 by the CI simplification (PR #714). The required set is
  final at two contexts; `intended == current == live` and `tools.ci deployed` reports no findings.
  The three further contexts it would have made blocking still run on every PR, advisory.
- **Phases 4, 7 (cold-start and production acceptance)** — an acceptance *ceremony* for a system whose
  every rule already carries a firing negative control and whose verdicts are re-derivable on demand.
  Phase 3's exit criteria proved the tool discriminates; a scored cold-start run would have proved
  that a particular agent, on a particular day, followed a document.
- **Phase 6 (orchestration enforcement decision)** — the gate has been dormant since 2026-07-15 and
  normal work no longer routes through it (`AGENTS.md`). A recorded decision to re-enable, replace or
  retire it was a decision nothing was waiting on.
- **Phase 8 (closeout and freeze)** — this section is the closeout. A separate immutable record and a
  formal amendment process are governance about governance; the ADRs already are the amendment
  process.

The contract model itself was narrowed at the same time: **ADR-0106** makes a new contract
declaration-only, retiring the lifecycle append chain, the post-merge publication PR and the
acceptance ceremony. ADR-0105 remains in force for everything else, and in full for the six contracts
written under it.

### Phase 1 — outcome

**ACCEPTED** 2026-07-18. Landed as PR #701, squash
`937777d930761048d04362637cab779020bf46a2` (`main`: `b2bb5cb` → `937777d`). 21 files; none touching
application runtime or CI workflow definitions. All six contradictions closed, with a regression guard
landed alongside them. Residual **R8** is Phase 6 work under **D4**, and is recorded on the PR, not
here.

### Phase 2 — outcome

**ACCEPTED** 2026-07-18. Landed as PR #702, squash
`ce132f61c8637f5adfaed2e3de999c6254031792` (`main`: `937777d` → `ce132f6`). The accepted ADR is
`docs/adr/0105-reusable-change-contract-architecture.md`; its `approved_digest` was verified against
the merged blob. Phase 2 shipped no executable, schema, check or workflow, as §12 of that ADR
requires.

### Phase 3 — design

The implementation design was approved 2026-07-18 under the gate
`APPROVE CHANGE CONTRACT COMPILER IMPLEMENTATION DESIGN`, and is the governing implementation
specification for Phase 3B. It is recorded here because ADR-0102 §1 squashes each PR to one commit,
which would otherwise collapse the `APPROVED` → `IN IMPLEMENTATION` transition into a single commit
and erase the design-approval moment. The same device records Phase 1 above.

Phase 3B amends ADR-0105 §1 `T3` to add `tools/contract/**`. That is the ADR's own rule — *"adding a
governance surface must add it here in the same change"* — and it changes the body, hence
`approved_digest`, hence requires renewed approval of the amended body before merge.

### Phase 3 — outcome

**ACCEPTED** 2026-07-20. Landed across PRs #703 (compiler + verifier), #705 (`LIFECYCLE-REWRITTEN`
reachable from the shipped CLI), #707 (single-operator merge authorization; `ST-4` deleted), #708
(acceptance rederived against the platform) and #709 (the post-merge acceptance append).

Both exit criteria are met, and each is mechanically re-derivable rather than asserted here:

- **Every rule carries a firing negative control.** `python -m tools.contract selftest` reports the
  count and exits non-zero if any control fails to detect its injected defect. The count is
  deliberately not restated in this file; a number in prose has no reader and rots.
- **Acceptance is verified against the platform, not asserted by its own row.** Demonstrated in
  both directions. `CC-2026-07-20-acceptance-rederivation` reaches `accepted` through merge
  identity, tree fidelity, base-pinned required CI and provenance — *and*
  `CC-2026-07-18-change-contract-compiler`, which carries an `accepted` row, derives only
  `acceptance_claimed` because the merge beneath it is not fully authorized. A criterion that only
  admits is not a criterion; this one also refuses.

**Residual R9 — the phase's own lifecycle records are incomplete. DISPOSED 2026-07-20: all three
gaps stand unrepaired, permanently.** Three of the four contracts written under ADR-0105 during
Phase 3 carry gaps, and each has a final recorded outcome:

| gap | contract | derived state | disposition |
|---|---|---|---|
| **G1** | `CC-2026-07-19-cli-lifecycle-integrity` | `merged_unauthorized` | an **unratified, disclosed unauthorized merge** |
| **G2** | `CC-2026-07-18-change-contract-compiler` | `acceptance_claimed` | an **unratified historical violation** |
| **G3** | `CC-2026-07-19-single-operator-authorization` | `merged` | a **disclosed post-merge omission** |

Full statement, with each state as derived by the tool and the reasoning behind the disposition:
`docs/governance/PHASE3_LIFECYCLE_DISCLOSURE.md`.

**R9 is disposed, which is not the same as closed, repaired or forgiven.** The question of what to
do about each gap has been answered, and the answer is *nothing*. They are not pending items and
not follow-up work. **Nothing here ratifies G1's unauthorized merge or G2's claimed acceptance**,
and Phase 3's `ACCEPTED` must not be read as doing so — the two are independent. Phase 3's exit
criteria concern the compiler and verifier, which are met and proven; the gaps are
program-execution debt from building the tool, surfaced by the tool itself, and the verifier
reports them correctly at every run. Appending a row to G1, G2 or G3, or editing their bodies,
contradicts this disposition and requires reversing it explicitly first.

### Phases 4 and 7 — what "acceptance intent" meant, kept for the record

Cold-start acceptance would have required a fresh agent, working from the entry point and the
contract alone, to drive a contained change, a cross-system change and a request it correctly stops
on. Production acceptance would have required one real change carried end to end plus one correctly
refused. **Neither was ever attempted, and neither will be.** The refusal path they were designed to
prove is covered where it can be proved repeatedly rather than once: `RF-1`–`RF-4`, `ES-1`–`ES-3` and
`ST-1`–`ST-10` each carry a firing negative control in `python -m tools.contract selftest`.

## Accepted sequencing decisions

- **D1** — Authority truth precedes contract design. A contract built on false authority surfaces
  inherits their falsehoods. *(Held; Phase 1 was executed on it.)*
- **D2** — ⛔ **VOID.** It sequenced architecture de-duplication behind OGD M1, and OGD is cancelled.
  The architecture gate is advisory and stays advisory; de-duplication is not scheduled.
- **D3** — The Cycle-6 implementation contract remains a historical program record. Future work is
  **not** added to it. *(Holds, and now applies to this roadmap too.)*
- **D4** — ⛔ **VOID.** It held the dormant orchestration gate un-re-enabled and undeleted until Phase
  6, which is cancelled. The disposition is now permanent rather than pending: the machinery is
  **retained on disk, dormant, and out of the normal agent workflow**. `.orchestration/SPEC.md`
  remains its status owner.

## Residual notes

None of these is open work; each is recorded so it is not rediscovered as a surprise.

- **B1, B2, P5-1, P5-2** — ⛔ **VOID with Phase 5.** DC-3 needs no admin token because there is no
  mutation to reconcile after; the de-duplication inventory needs no re-verification because no
  de-duplication is planned.
- **B3** — the untracked reconstruction documents `docs/reconciliation/01_…` … `05_…` are **not safely
  committable in their current state**: one carries a stale `_CLI_PRINT_COUNT` assignment that
  `IMPL-007` reads as a live claim, which would turn the architecture gate red. They remain untouched
  and untracked. This is a property of those files, not a program task.

## Evidence links

- `docs/REPOSITORY_CONSTITUTION.md` · `docs/ARCHITECTURAL_LAWS.md` · `docs/ENGINEERING_STANDARDS.md`
- `docs/adr/` (accepted ADRs) · `docs/adr/README.md` (historical decision evidence)
- `.github/ci-control-registry.yml` · `docs/ci/CI_GOVERNANCE_INDEX.md`
- `docs/ci/CI_BRANCH_PROTECTION_MUTATIONS.md` (the CANCELLED OGD runbook — nothing was ever executed)
- `.orchestration/SPEC.md` (orchestration-gate status owner; dormant, permanently)
- `.reports/architecture/IMPLEMENTATION_CONTRACT.md` (Cycle-6 historical program record)
- `docs/adr/0105-reusable-change-contract-architecture.md` (Phase 2 — the change-contract model)
- `docs/adr/0106-declaration-only-change-contracts.md` (the narrowing: declaration-only contracts)
- `docs/contracts/CC-2026-07-18-change-contract-compiler.md` (Phase 3 — the contract governing its
  own compiler; the first contract written under ADR-0105)
- `docs/governance/PHASE3_LIFECYCLE_DISCLOSURE.md` (Phase 3 residual **R9** — the incomplete
  lifecycle records, disclosed and not repaired)

## Current next gate

**None. The program is closed.**

Phase 3 is `ACCEPTED`; Phases 4–8 are `CANCELLED`; residual **R9** was disposed 2026-07-20 and stays
disposed. There is no phase awaiting approval and no artifact awaiting a signature.

**What is live, and where it is governed** — none of this is program work, it is how the repository
operates from here:

| still in force | authority |
|---|---|
| `T1`–`T6` triggers; a contract is required when one fires | ADR-0105 §1 |
| path-only preflight before the first edit | ADR-0105 §1a · `python -m tools.contract preflight` |
| the declaration; the closed field set | ADR-0105 §3.1 |
| declaration digest approval | ADR-0106 §2–§3 |
| expected surfaces versus the actual diff | ADR-0105 §5.3 · `python -m tools.contract scope` |
| a separate operator gate for a live action | ADR-0105 §1 `T4` · ADR-0106 §2 |
| unit CI; relevance-gated E2E | ADR-0101 as amended 2026-07-22 |

## Program Execution Method — as executed, for the record

- Each phase used the governing artifact appropriate to its work — an ADR, contract, runbook, PR or
  acceptance record. Documents were not created to satisfy a template.
- This roadmap recorded status, accepted decisions, blockers, evidence links and the next gate.
  Nothing else. It is now a closed record and takes no further entries.
- Verified evidence was reused while its relevant source remained unchanged.
- Reinvestigation occurred only for drift, conflicting evidence, incomplete proof, or a
  live/destructive action.
- Implementation used the smallest authorized scope, verification sufficient for its risk, and an
  explicit rollback.
- Adjacent findings stayed out of scope unless they were required dependencies.
