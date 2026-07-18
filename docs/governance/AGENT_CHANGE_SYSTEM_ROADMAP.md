# Agent Change System — Program Roadmap

> **This file owns SEQUENCE and STATUS only.** It records where the program is, what was decided, what
> blocks it, and what the next gate is. It states no law, standard, ADR policy, CI policy, schema or
> executable rule — those live in their own authorities and are linked, never restated.

## Objective

A reusable, mechanically-verified change contract for agent-authored changes to this repository:
truthful authority surfaces first, then the contract architecture, its compiler and verifier,
cold-start acceptance, governance deployment, the orchestration-enforcement decision, production
acceptance, and freeze.

## Phases

| # | Phase | Status | Entry criteria | Exit criteria |
|---|---|---|---|---|
| 1 | Authority Repair and Program Boundaries | **ACCEPTED** | A verified contradiction register on a named base SHA | The six verified contradictions closed; this roadmap persisted; `tools/arch` + `tools/ci` clean; CI green on the exact PR head |
| 2 | Reusable Change-Contract Architecture | **APPROVED** | Phase 1 `ACCEPTED` | An accepted ADR defining the change-contract model |
| 3 | Change-Contract Compiler and Verifier | NOT STARTED | Phase 2 `ACCEPTED` | Compiler + verifier merged, each rule carrying a firing negative control |
| 4 | Cold-Start Acceptance | NOT STARTED | Phase 3 `ACCEPTED` | A fresh agent, unaided, drives three cases through the contract — see below |
| 5 | Operational Governance Deployment | NOT STARTED | Phase 4 `ACCEPTED` + explicit operator gate | M1–M6 applied one at a time; live required set == `intended_required_contexts` |
| 6 | Orchestration Enforcement Decision | NOT STARTED | Phase 5 `ACCEPTED` | A recorded decision to re-enable, replace or retire the dormant orchestration gate |
| 7 | Production Acceptance | NOT STARTED | Phase 6 `ACCEPTED` | The system accepted against its own contract on real work — see below |
| 8 | Closeout and Freeze | NOT STARTED | Phase 7 `ACCEPTED` | An immutable closeout record + amendment process; the program is frozen |

**Status values:** `NOT STARTED` → `IN DESIGN` → `APPROVED` → `IN IMPLEMENTATION` → `ACCEPTED`.

**A later phase may begin only after the preceding phase is `ACCEPTED`. A merge alone does not
authorize progression** — merging is an *event*, not a phase status. A phase remains
`IN IMPLEMENTATION` after its code lands, and reaches `ACCEPTED` only when its exit criteria are
demonstrated and explicitly signed off.

### Phase 1 — outcome

**ACCEPTED** 2026-07-18. Landed as PR #701, squash
`937777d930761048d04362637cab779020bf46a2` (`main`: `b2bb5cb` → `937777d`). 21 files; none touching
application runtime or CI workflow definitions. All six contradictions closed, with a regression guard
landed alongside them. Residual **R8** is Phase 6 work under **D4**, and is recorded on the PR, not
here.

### Phase 4 — acceptance intent

Cold-start acceptance is only met when a fresh agent, working from the entry point and the contract
alone, proves all three:

1. a **contained** change (single subsystem, inside one boundary);
2. a **cross-system** change (spanning subsystem boundaries, requiring impact analysis);
3. an **unsafe or under-specified** request that it **correctly stops on** rather than attempting.

Case 3 is not optional. A system that only proves the happy paths has not been tested for refusal.

### Phase 7 — acceptance intent

Production acceptance is only met when both are proven on real work:

1. one **successful real change** carried end-to-end through the contract;
2. one **unsafe or boundary-exceeding** request that is **correctly refused**.

## Accepted sequencing decisions

- **D1** — Authority truth precedes contract design. A contract built on false authority surfaces
  inherits their falsehoods.
- **D2** — The architecture gate becomes a required context (OGD M1) **before** any architecture
  de-duplication. Owner: `.github/ci-control-registry.yml` (`duplicate_groups.arch-drift-policy`) and
  `docs/adr/0101-required-checks-and-merge-gate-policy.md` §2. Removing the overlap first would leave
  architecture drift/policy/registries with no required enforcement.
- **D3** — The Cycle-6 implementation contract remains a historical program record. Future work is
  **not** added to it.
- **D4** — The dormant orchestration gate is neither re-enabled nor deleted before Phase 6.

## Unresolved blockers

- **B1** *(Phase 5)* — DC-3 (live-vs-declared reconciliation) is wired into no workflow and needs an
  operator-provisioned admin token. Until it exists, reconciliation after each mutation is the **manual
  read-only re-probe** prescribed by `docs/ci/CI_BRANCH_PROTECTION_MUTATIONS.md`, not an automated check.
- **B2** *(Phase 5)* — the de-duplication inventory is understated; see P5-1.
- **B3** *(external to every phase)* — the untracked reconstruction documents
  `docs/reconciliation/01_…` … `05_…` are **not safely committable in their current state**: one carries
  a stale `_CLI_PRINT_COUNT` assignment that `IMPL-007` reads as a live claim, which would turn the
  architecture gate red. They are deliberately untouched and remain outside every phase. Recorded so
  this is not rediscovered as a surprise.

## Phase-5 prerequisites (recorded, not executed)

- **P5-1** — the four-invariant description of the unit-lane de-duplication target may **omit two
  distinct invariants** (the path-selection tests). The complete inventory must be reverified from the
  test file itself before any de-duplication is designed.
- **P5-2** — generated-document drift does **not** receive complete required enforcement until the
  architecture gate is a required context. This strengthens D2.

## Evidence links

- `docs/REPOSITORY_CONSTITUTION.md` · `docs/ARCHITECTURAL_LAWS.md` · `docs/ENGINEERING_STANDARDS.md`
- `docs/adr/` (accepted ADRs) · `docs/adr/README.md` (historical decision evidence)
- `.github/ci-control-registry.yml` · `docs/ci/CI_GOVERNANCE_INDEX.md`
- `docs/ci/CI_BRANCH_PROTECTION_MUTATIONS.md` (Phase 5 runbook — nothing executed)
- `.orchestration/SPEC.md` (orchestration-gate status owner)
- `.reports/architecture/IMPLEMENTATION_CONTRACT.md` (Cycle-6 historical program record)
- `docs/adr/0105-reusable-change-contract-architecture.md` (Phase 2 — the change-contract model)

## Current next gate

**APPROVE REUSABLE CHANGE CONTRACT MERGE**

## Program Execution Method

- Each phase uses the governing artifact appropriate to its work — an ADR, contract, runbook, PR or
  acceptance record. Documents are not created to satisfy a template.
- This roadmap records status, accepted decisions, blockers, evidence links and the next gate. Nothing
  else.
- Verified evidence is reused while its relevant source remains unchanged.
- Reinvestigation occurs only for drift, conflicting evidence, incomplete proof, or a live/destructive
  action.
- Design, implementation, merge, deployment and acceptance are separately authorized, only where those
  actions exist.
- Implementation uses the smallest authorized scope, verification sufficient for its risk, and an
  explicit rollback.
- Adjacent findings stay out of scope unless they are required dependencies.
- Future sessions resume from this roadmap and the current phase's governing artifact, rather than
  reconstructing the program or repeating completed investigations.
