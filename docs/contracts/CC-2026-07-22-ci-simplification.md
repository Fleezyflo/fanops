---
id: CC-2026-07-22-ci-simplification
traits: [governance]
authorized_actions: [design, implement]
incidental_allowlist: []
blast_radius: [CI-UNIT, CI-E2E, CI-BASEINSTALL, ARCH-GATE, ARCH-IMPACT, LANE-GUARD, CI-E2E-NEGCONTROLS, CI-UNIT-ARCHGOV, ARCH-CONTROLS, CI-TIMING]
invariants:
  - "the `real-tooling E2E (must run, not skip)` context is never removed and never skipped at the workflow level — it reports on every pull request"
  - "live branch protection is not mutated by this change"
  - "a runtime-relevant change still runs the full real-tooling suite"
  - "`python -m tools.ci static` and `python -m tools.arch ci` stay clean"
stop_conditions:
  - "any change to live GitHub branch protection"
  - "a workflow-level `paths:` filter on a workflow backing a required context"
  - "deleting an advisory job rather than declassifying it"
supersedes: []
---

# CC-2026-07-22-ci-simplification

### objective

Cancel the five-required-context OGD rollout and reduce routine merge-blocking to what earns it. `unit
(fast, no toolchain)` becomes the sole routine PR blocker for logic. `real-tooling E2E (must run, not
skip)` stays required but does its work only on runtime-relevant changes. The architecture gate,
impact report, base-install smoke and the lane + cross-open-PR collision guard are reclassified
advisory: still run, still read, no longer blocking.

The problem being solved is concrete: a documentation-only pull request — the shape of every
governance-lifecycle publication in `docs/contracts/` — installs ffmpeg, espeak and Whisper and runs a
~7-minute integration suite before it can merge, and three further contexts were queued to start
blocking merges on top of that.

### success_condition

Observable, and each is checked below:

1. `python -m tools.ci deployed` reports **no findings**, where before it reported three contexts
   "pending Operational Governance Deployment".
2. A documentation-only pull request finishes the `real-tooling E2E (must run, not skip)` context in
   seconds, with a log line stating E2E was not relevant and did not run — and the context still
   REPORTS, so branch protection resolves.
3. A pull request touching `src/`, `tests/`, `scripts/`, `tools/`, `.github/`, `pyproject.toml` or
   `requirements/` still runs the full real-tooling suite. This PR is itself such a change.
4. `python -m tools.ci static` and `python -m tools.arch ci` stay clean, and the unit lane stays green.
5. Live branch protection still requires both contexts, unchanged, and is not touched by this PR.

### rollback

Revert the merge commit. Every change is repository-side — registry classifications, workflow step
conditions, one new script, one new test, and prose. No live GitHub setting is mutated, so there is no
out-of-band state to restore and the rollback cost is one revert.

### authority

| id | source_file | blob_sha |
|---|---|---|
| ADR-0105 | docs/adr/0105-reusable-change-contract-architecture.md | bce8525d462e9df8e070191972cc7a757c6da377 |

### owners

| subsystem_id | why_touched |
|---|---|
| S01_foundation | no `src/fanops/` module changes; ownership is DECLARED for the governance tooling and documentation paths, never inferred (ADR-0105 §7) |

Every path is non-source (`.github/`, `docs/`, `scripts/`, `tests/`), so `T1` spans zero subsystems by
class exclusion. The blast radius is expressed as CI controls, which is the taxonomy this change acts
in — see `blast_radius` in the front matter.

### allowed_scope

| glob | why | basis |
|---|---|---|
| .github/workflows/ci.yml | the E2E relevance gate lives inside the job | declared |
| .github/ci-control-registry.yml | classifications, the intended set, the rollout phase, duplicate groups | declared |
| docs/adr/0101-required-checks-and-merge-gate-policy.md | the merge-gate policy of record | declared |
| docs/ci/**.md | the CI governance docs that describe the cancelled rollout | declared |
| AGENTS.md | the DC-4 prose surface, which described the lane guard as a pending promotion | declared |
| docs/ARCHITECTURAL_LAWS.md | LAW rows staged `CI-BASEINSTALL` promotion and `required_linear_history` as Phase-E remediation | declared |
| docs/ENGINEERING_STANDARDS.md | the STD row named `CI-BASEINSTALL` promotion as planned work | declared |
| scripts/ci_e2e_relevance.py | the relevance predicate | declared |
| tests/test_ci_e2e_relevance.py | its proof, in the unit lane | declared |
| docs/contracts/CC-2026-07-22-ci-simplification.md | this declaration | declared |

### prohibited_scope

| glob | why |
|---|---|
| live GitHub branch protection | a repository settings mutation needs separate, explicit operator approval; no PR performs one |
| src/** | no runtime behaviour changes in this unit |
| docs/adr/0105-*.md | the contract architecture is not reopened here |
| .github/workflows/architecture.yml | the arch jobs are reclassified in the registry, not rewritten |
| .github/workflows/lane-guard.yml | same — declassify, do not delete or edit the job |

### expected_surfaces

| path | kind | why |
|---|---|---|
| .github/workflows/ci.yml | MODIFIED | relevance gate inside the `e2e` job; `workflow_dispatch` added as the manual force trigger |
| .github/ci-control-registry.yml | MODIFIED | ARCH-GATE / CI-BASEINSTALL / LANE-GUARD → advisory; intended set → 2; rollout phase; duplicate-group prose; CI-E2E relevance record |
| docs/adr/0101-required-checks-and-merge-gate-policy.md | MODIFIED | the amendment cancelling OGD and fixing the required set at two |
| docs/ci/CI_BRANCH_PROTECTION_MUTATIONS.md | MODIFIED | CANCELLED banner; M1–M6 are history, not pending steps |
| docs/ci/CI_CONTROL_INVENTORY.md | MODIFIED | classifications, the two-context section, the hand-maintained disclosure |
| docs/ci/CI_GOVERNANCE_INDEX.md | MODIFIED | lifecycle status, ownership matrix, decision tree |
| docs/ci/CI_PROGRAM_LIFECYCLE.md | MODIFIED | Phase 5 CANCELLED; Phase 6 residual stated honestly |
| AGENTS.md | MODIFIED | lane guard described as advisory; the promotion toggle removed |
| docs/ARCHITECTURAL_LAWS.md | MODIFIED | AR-3 and AR-4 residuals restated as permanent rather than pending a cancelled rollout |
| docs/ENGINEERING_STANDARDS.md | MODIFIED | `CI-BASEINSTALL` promotion removed from planned work |
| scripts/ci_e2e_relevance.py | NEW | the pure relevance predicate + its CLI |
| tests/test_ci_e2e_relevance.py | NEW | both directions and the fail-safe polarity |
| docs/contracts/CC-2026-07-22-ci-simplification.md | NEW | this declaration |

### coupling

| what | must_move_with | why |
|---|---|---|
| a control's `classification` | the prose in `AGENTS.md` | DC-4 blocks when a doc names a context and contradicts its classification |
| `intended_required_contexts` | `current_required_contexts` and live GitHub | DC-3 requires live == current; a gap between current and intended is now a failure, not a planned transition |
| a job `name:` | its `branch_protection_context` | DC-1 fails closed on an unmirrored rename |
| the relevance predicate | its test | the predicate decides whether a required context does any work; an unproven one could buy a silent green |

### reusable_evidence

| claim | proven_by | proven_at | binding |
|---|---|---|---|
| — | — | — | no evidence is reused; every claim is re-run on the final head |

### verification

| obligation_id | control_or_requirement | distinct_boundary |
|---|---|---|
| OB-ARCH-CI | python -m tools.arch ci | regeneration byte-compare plus the policy rule set |
| OB-CI-STATIC | python -m tools.ci static | registry-versus-workflow reconciliation — DC-1/2/4/5/6 against the reclassified registry |
| OB-CI-DEPLOYED | python -m tools.ci deployed | the OGD gap is gone: registry versus LIVE branch protection, read-only |
| OB-NEG-CONTROL | python -m tools.ci selftest | the DCs still discriminate after the registry rewrite |
| OB-UNIT-CI | the required context `unit (fast, no toolchain)` concluding success on the exact head | the only evidence the pytest suite, including the new relevance test, passes |
| OB-E2E-CI | the required context `real-tooling E2E (must run, not skip)` concluding success on the exact head, having RUN in full | this PR changes `.github/` and `scripts/`, so the gate must choose the slow path — proving the predicate does not skip a runtime-relevant change |
| OB-C18 | this contract and the ADR-0101 amendment | Constitution C18.1 / the ADR process |
| OB-REVERIFY | every claim above re-run on the final head, not reused from an earlier run | no evidence reuse |

## Lifecycle

| timestamp | event | values |
|---|---|---|
| 2026-07-22T11:55:00Z | created | id=CC-2026-07-22-ci-simplification; base_sha=3b6d89f99d35e9dea2e58b6db60b3f25a90d3814; timestamp_source=GitHub API Date response header, observed during this operation |
