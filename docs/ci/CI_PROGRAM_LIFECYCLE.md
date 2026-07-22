# FanOps — CI Governance Program Lifecycle

> **This document replaces the binary "engineering complete" framing** with an explicit six-phase
> lifecycle and the current status of each phase. It is the single source of truth for *where the
> program is*. It is part of the governance system, not commentary — the phase statuses below are the
> program's authoritative state. (Operator directive, 2026-07-16.)

The CI-governance program has **six phases**. The first four ("engineering") produce the policy and the
machinery that enforces it; the fifth **deploys** that policy to live repository security settings; the
sixth **closes and freezes** the program. Each phase has one deliverable class and one completion test.

| # | Phase | What it produces | Completion test | **Status** |
|---|-------|------------------|-----------------|------------|
| 1 | **Investigation** | The CI Architecture Review — a mechanical, cited audit of every check (9 phases), surfacing the intent-vs-configuration gap. | The review exists and is evidence-backed (`docs/CI_ARCHITECTURE_REVIEW.md`). | ✅ **COMPLETE** |
| 2 | **Architecture** | The decision records: three reconciled planes (registry = intent, workflows = implementation, live protection = deployed), required-checks policy, merge-strategy policy. | ADRs 0100 / 0101 / 0102 accepted (in principle). | ✅ **COMPLETE** |
| 3 | **Governance** | The machine-readable control registry + schema, the control classification/lifecycle model, the duplicate-group model, the dedicated-validator mandate. | `ci-control-registry.yml` + `.schema.json` exist and are shape-valid; every control has one owner/invariant/classification. | ✅ **COMPLETE** |
| 4 | **Implementation** | The `tools/ci` validator (DC-1..DC-6, three modes, negative controls), every repository-remediation slice, and the validator wired into the required unit lane. | All implementation PRs merged; `tools.ci static` rc=0, `selftest` rc=0 on `main`. | ✅ **COMPLETE** |
| 5 | **Operational Governance Deployment (OGD)** | *Would have deployed* repository security policy to live branch protection via mutations M1–M6. | — | ⛔ **CANCELLED** 2026-07-22 (operator decision, CI simplification). Not deferred: the required set is final at two contexts, `intended == current == live`, and `tools.ci deployed` reports no findings. See `CI_BRANCH_PROTECTION_MUTATIONS.md`. |
| 6 | **Program Closeout** | The two permanent, immutable records — `CI_PROGRAM_CLOSEOUT.md` (historical closeout of the whole program) and `CI_GOVERNANCE_DNA.md` (the principles, mechanisms, non-negotiable rules, and amendment process) — then the **freeze**. | Both documents exist; the program is declared frozen. | ⏳ **NOT STARTED**. It no longer waits on OGD — that gate is gone — but nothing in this change produces these records, so their absence is a genuine residual, not a step this PR completed. |

## What "engineering complete" now means precisely

Phases 1–4 are the engineering. They are **complete and merged** on `main` and independently
re-provable at any time:

```
python -m tools.ci reconcile     # static rc=0 (no findings) · deployed rc=0 (no findings, since 2026-07-22)
python -m tools.ci selftest      # rc=0 — all 8 negative controls fire (the DCs discriminate)
```

Phase 5 (OGD) is **cancelled**, so `deployed` no longer reports a planned transition: the intended and
current required sets are identical and both match live GitHub. Phase 6 remains outstanding; it
produces the immutable records and freezes the program. Neither was ever "remaining work on the code" —
the code is done.

## Freeze semantics (end of Phase 6)

When `CI_PROGRAM_CLOSEOUT.md` and `CI_GOVERNANCE_DNA.md` both exist, **this CI-governance program is
frozen**. The closeout and the DNA document are immutable. **Any future CI-governance change begins as a
NEW governance program** — a new ADR and a new registry revision under the amendment process defined in
`CI_GOVERNANCE_DNA.md` — never as an extension of this one. Extending a frozen program is itself a
governance violation.

## Related governed artifacts

- `docs/ci/CI_VALIDATOR_SPEC.md` — the permanent specification of the `tools/ci` validator (a governed subsystem).
- `docs/ci/CI_BRANCH_PROTECTION_MUTATIONS.md` — the CANCELLED OGD runbook, kept as history, + the closeout spec.
- `docs/adr/0100–0102` — the Architecture-phase decision records.
- `.github/ci-control-registry.yml` (+ `.schema.json`) — the Governance-phase control registry.
