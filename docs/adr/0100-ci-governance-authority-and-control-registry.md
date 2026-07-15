---
status: accepted
date: 2026-07-15
accepted_in_principle: 2026-07-15
supersedes: []            # advances catalogue slug 0099 GOV-CI-CONTROL-PLANE-GAP from PROP -> mechanism defined
references: [0090, 0098, 0099]
deciders: [operator]
---

# ADR-0100 — CI Governance Authority and Control Registry

> **Accepted in principle 2026-07-15**, with the operator amendments folded into this revision.
> Declares authority and structure only; modifies no workflow and mutates no branch protection.
> Implementation is gated: the `tools/ci` validator and the repository-remediation PRs must be green
> before any live branch-protection mutation (ADR-0101 / Phase E).

## Status

**Accepted** (in principle, 2026-07-15). Defines the mechanism for catalogue candidate **0099
`GOV-CI-CONTROL-PLANE-GAP`** ("merge policy not machine-verifiable — the review thesis").

## Context

The CI system is strong control-by-control but has **no reconciled control plane**. Three planes
disagree (proven in `docs/CI_ARCHITECTURE_REVIEW.md`; re-confirmed in the Phase-A freeze,
`docs/ci/freeze/2026-07-15/PHASE-A-SNAPSHOT.md`, and re-probed 2026-07-15): workflow YAML defines ~11
jobs; governance prose tags ~18 rules "BLOCKING"; live branch protection requires **2** contexts.
Required-ness exists only in GitHub's UI. That is the root defect; every other CI finding is a symptom.

Program §7 is binding: CI governance must **integrate** with the wider repository governance system but
must **not** become a parallel authority — **and (operator amendment) it must not share ownership with
architecture governance**. The two governance subsystems (`tools/arch` for architecture, `tools/ci` for
CI) may reference each other but remain distinct, separately-owned subsystems.

## Decision

**Three reconciled planes + a validator that fails on divergence**, with ADRs owning rationale and a
generated doc as a derived view:

| Plane | Owner | Meaning |
|---|---|---|
| **Intent** | **CI Control Registry** — `.github/ci-control-registry.yml` | the **canonical declaration of CI intent**: what controls SHOULD exist, their classification, owner, invariant, deletion test |
| **Implementation** | **Workflow files** — `.github/workflows/` | the **executable implementation** of those controls |
| **Deployed state** | **Live GitHub branch protection** | the **deployed enforcement**: which contexts actually block a merge, right now |
| *(divergence detector)* | **`tools/ci` validator** (dedicated; NOT `tools/arch`) | **fails when the three planes diverge** |

Surrounding roles: **ADRs** (`docs/adr/`) own *why* (policy/rationale/supersession); the **generated
ownership table** (`docs/ci/CI_CONTROL_INVENTORY.md`) is a **derived view only**, never hand-maintained.

**The registry is NOT a fourth source of truth.** It is the declaration of *intent* that the validator
compares against implementation (workflow job names) and deployed state (live branch protection). Where
they disagree, reality wins per the precedence order — and the disagreement is a validator failure to be
resolved, never papered over.

**Precedence order** (program §2, adopted as CI law): (1) executable source & tests → (2) live GitHub
config → (3) accepted ADRs & the registry → (4) generated docs → (5) historical prose.

**Validator host (operator amendment):** a **dedicated `tools/ci` module** — *not* hosted in
`tools/arch`. CI governance and architecture governance integrate (they may share the DERIVED/DECLARED
+ negative-control *method*) but do **not** share ownership or collapse into one subsystem.

**Registry-integrity contract.** The `tools/ci` validator (built in Phase C; each blocking check backed
by a negative control) **fails** on any of these six divergences:

1. **Unknown workflow job** — a job in `.github/workflows/` with no registry row.
2. **Phantom control** — a registry control with no executable implementation (no matching workflow
   job, local hook, or scheduled process).
3. **Required-but-undeployed** — a registry control marked `required` whose context is absent from live
   branch protection.
4. **Deployed-but-unregistered** — a live required context absent from the registry.
5. **Renamed required context** — a required context string that no longer ⊆ the set of workflow job
   `name:`s (the anti-silent-detach guard; a rename deadlocks the queue if unmirrored).
6. **Unjustified duplicate ownership** — two controls sharing an invariant without an explicit
   `duplicate_group` justification stating each member's distinct boundary.

These map to the deterministic checks `DC-1…DC-6`: DC-1 (5 · anti-detach, static per-PR), DC-2 (1,2 ·
registry↔jobs, static per-PR), DC-3 (3,4 · intent↔live, **authenticated + scheduled**, not per-PR),
DC-4 (governance-prose "BLOCKING" ↔ registry classification, static per-PR), DC-5 (6 · duplicate-group
justification + generated-view byte-compare, static per-PR), DC-6 (workflow hygiene: every job has a
timeout, every `uses:` SHA-pinned, concurrency where declared, static per-PR).

## Alternatives considered

- **Markdown table as source of truth** — needs a bespoke parser; adds a fourth plane. Rejected.
- **Branch protection as source of truth** (status quo) — not version-controlled, unprovable from the
  tree. Rejected (the defect being fixed).
- **Enriching governance prose** — unparseable, rots. Rejected.
- **Hosting the validator in `tools/arch`** — rejected by operator amendment: it would fuse CI and
  architecture governance into one subsystem. A dedicated `tools/ci` keeps ownership separate.

## Rejected alternatives (non-obvious rejections)

- **Auto-committing reconciliation** (a bot that rewrites branch protection or the registry to match).
  Rejected — a bot silently editing the governance-of-record is the opposite of governance;
  reconciliation *reports* drift, a human lands the fix (mirrors `architecture.yml`'s reconcile job).
- **Deriving the registry from the workflows.** Rejected — it would make the registry a *view of the
  implementation*, unable to express intent that differs from current implementation (e.g. an
  advisory-today control that is intended-required).

## Consequences

- One declared plane replaces three implicit ones; "what blocks a merge, and why" is answerable from
  the tree.
- The `tools/ci` validator turns the six divergences into red checks with negative controls.
- The generated ownership table becomes byte-reproducible; hand-editing it is a DC-5 failure.
- CI and architecture governance stay separate subsystems that share method, not ownership.

## Risks

- **Registry rot** if `DC-1…DC-6` never land. *Mitigated:* DC-2 (every job has a row) + DC-5
  (byte-compare) once Phase C lands; until then the registry is `status: proposed` and load-bearing on
  nothing. *(estimate.)*
- **The registry becoming a hand-edited second rotting doc.** *Mitigated:* the human table is generated
  from it; the registry is small + schema-validated. *(estimate.)*
- **Two governance subsystems drifting apart** (tools/arch vs tools/ci). *Mitigated:* shared method +
  cross-references; the boundary is deliberate (operator amendment). *(estimate.)*

## Migration plan

1. **Phase B (this PR):** land `.github/ci-control-registry.yml` + schema + the generated inventory,
   with all amendments folded. No workflow/BP change.
2. **Phase C:** implement `DC-1…DC-6` in a dedicated **`tools/ci`** module, each blocking check with a
   negative control; wire the ownership-table generator; add the static checks to a workflow (ADR-0101).
3. **Phase D/E:** remediation + branch-protection reconciliation happen *through* the registry (each
   change edits a registry row in the same PR), gated on the validator being green.

## Rollback plan

The registry, schema, and generated view are **inert** until a validator references them. Rollback =
delete the three files (no workflow/BP/test affected). After Phase C, rollback of a validator is a
one-line workflow revert + the registry-row revert in the same PR.

## Enforcement mechanism

`DC-1…DC-6` in the dedicated `tools/ci` module, each backed by a negative control; the JSON schema
(`ci-control-registry.schema.json`) validated by DC-2.

## Verification contract

- The registry validates against its schema.
- DC-2: every job has a row; no phantom controls. DC-5: generated view == fresh regeneration;
  duplicate groups justified. Each DC has a negative control that fires on an injected divergence.
- This ADR asserts no live behavior change; the only tree artifacts are data + docs.

## Superseded decisions or documents

- The `docs/CI_ARCHITECTURE_REVIEW.md` sketch filename `.github/ci-ownership.yml` (never created) →
  superseded by `.github/ci-control-registry.yml`.
- Advances catalogue **0099 `GOV-CI-CONTROL-PLANE-GAP`** from `PROP` to *mechanism defined*.

## Affected workflows and controls

- **Workflows:** none modified by this ADR. Phase C adds a `tools/ci` validator invocation; Phase D
  remediates workflows.
- **Controls:** all — the registry inventories every control. Referenced by the arch-governance controls
  and the local ruff/secret-scan tiers.

## Operator decisions required — RESOLVED (2026-07-15)

1. **Accept ADR-0100?** → **Yes, in principle** (amendments folded).
2. **Registry location** → **keep `.github/ci-control-registry.yml`** and
   `.github/ci-control-registry.schema.json`.
3. **Validator host** → **dedicated `tools/ci`** (not `tools/arch`; integrate, don't merge ownership).
