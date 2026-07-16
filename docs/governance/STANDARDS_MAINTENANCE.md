<!-- Standards Maintenance — how docs/ENGINEERING_STANDARDS.md stays synchronized with reality.
     Base: origin/main @ a79528d (#676), 2026-07-16.
     THIS DEFINES NO NEW ENGINE. The drift-detection engine is `constitution-lint` (CM-1..CM-8), designed
     in CONSTITUTION_MAINTENANCE.md and built by SLICE-CONSTLINT. This document registers the standards
     layer as an INPUT to that engine and adds three standards-specific checks hosted inside it. -->

# Standards Maintenance

**The problem.** [`ENGINEERING_STANDARDS.md`](../ENGINEERING_STANDARDS.md) is prose. By this repository's
own thesis, prose that is not mechanically re-derived from the current tree rots. This document defines
how it stays honest — **without** creating a third governance authority.

## Non-negotiable: no second engine

The drift-detection engine already exists in design: **`CM-1..CM-8`** in
[`CONSTITUTION_MAINTENANCE.md`](CONSTITUTION_MAINTENANCE.md), hosted by `constitution-lint` (a thin module
that *delegates* to `tools/arch`'s symbol/derived tables and `tools/ci`'s registry parse), built by
`SLICE-CONSTLINT`.

**This document does exactly two things:**
1. **Registers** `ENGINEERING_STANDARDS.md` + `STANDARDS_ENFORCEMENT_MATRIX.md` as **additional inputs** to the existing CM-* checks.
2. **Adds three standards-specific checks (`SM-1..SM-3`)** hosted *inside* `constitution-lint`, sharing its method (a predicate + a negative control + block-or-report).

It creates **no** new registry, **no** new validator binary, **no** new required check.

## Inherited coverage — CM-* applied to the standards layer

Registering this layer as a CM-* input yields, with **zero new code**:

| Check | What it gives the standards layer | Block/Report |
|---|---|---|
| **CM-1** (schema) | every `STD-*` carries its six fields; `enforcement` ∈ the allowed vocabulary | block |
| **CM-5** (supersession) | a superseded `STD-*` has a live pointer; no dangling supersession | block |
| **CM-6** (evidence links) | every citation resolves — a cited `LAW-*`, `ADR-NNNN`, control `id`, test name, or path that does not exist fails | block |
| **CM-7** (stale anchors) | the `file:line` hints in Evidence are reported when the symbol moved (**STD-DOC-01**'s planned enforcement — this layer is CM-7's *consumer*, it defines no anchor checker) | report |
| **CM-8** (cross-plane contradiction) | an `STD-*` claiming `enforced` whose cited control is advisory/absent is reported | report |
| **CM-4** (dormant governance) | a standard naming a mechanism nothing executes (the `.markdownlint.json` class) | report |

**CM-6 is the load-bearing one.** This document is dense with cross-references *by design* (it references
rather than duplicates). That makes a dangling reference its characteristic failure mode — and exactly
what CM-6 catches.

## Standards-specific checks (`SM-*`) — hosted in `constitution-lint`

| ID | Check | Fails when | Block/Report | Negative control |
|---|---|---|---|---|
| **SM-1** | **Non-duplication** (the anti-competing-system check) | an `[OWNED]` `STD-*` section restates a rule owned by a `LAW-*`, an ADR, or a `.github/ci-control-registry.yml` control row — i.e. it asserts an enforcement claim for a topic it does not own | **report** (ownership is a judgment) | add an `STD-*` duplicating a `LAW-*`'s rule text → appears in the report |
| **SM-2** | **Matrix ↔ standards parity** | `STANDARDS_ENFORCEMENT_MATRIX.md` has a row with no `STD-*` in the standards doc, or an `[OWNED]` `STD-*` with no matrix row, or the two disagree on `Current enforcement` | **block** (a mechanical fact) | delete a matrix row / flip one status → must fail |
| **SM-3** | **Slice closure** | a standard whose `Planned enforcement` names a `SLICE-STD-*` that does not exist in `STANDARDS_AUTOMATION_PLAN.md` (or vice versa) | **block** | rename a slice in one file only → must fail |

Each `SM-*` obeys the same three rules as every validator here: a machine-evaluated predicate, a negative
control proving it fires, and **report, never auto-fix**.

## Synchronization contract

How this layer stays synchronized with each plane. **Precedence is inherited, never redefined**
(Constitution §2 / ADR-0100): *executable source & tests → live GitHub config → accepted ADRs &
registries → generated docs → historical prose.* This layer is plane 4/5 — **it always loses.**

| Plane | Sync direction | Trigger | Mechanism |
|---|---|---|---|
| **Constitution / Philosophy** | Constitution wins | a `C*` rule changes | `[REFERENCE]` sections carry no independent claim, so most changes need no edit here. A changed rule that a `STD-*` cites → **CM-6**/**CM-8**. |
| **ADRs** | ADR wins | an ADR is accepted/superseded | **CM-6** (the ADR id resolves) + **CM-5**. An `STD-*` change that alters a *decision* records an ADR first (C18.1). |
| **CI control registry** | registry wins, always | a control is added/renamed/promoted | **CM-6** (the control `id` resolves) + **CM-8** (an `STD-*` claiming `enforced` via an advisory control). **This layer never edits the registry.** |
| **Architecture (`tools/arch`)** | arch wins | a `LAW-*`/rule severity changes | **CM-8**. `[REFERENCE]` §4/§7/§8/§16/§22 are pointers by construction. |
| **Source code** | **source wins** | a cited symbol moves or disappears | **CM-6** (symbol missing → block) + **CM-7** (line moved → report). Per **STD-DOC-01**, the symbol is the identifier; the line is a hint. |
| **Tests** | tests win | a cited test is renamed/deleted | **CM-6**. *A cited test that no longer exists is the strongest possible signal that a standard's enforcement claim is now false.* |

## Drift-detection requirements (what "maintained" means here)

1. **Every enforcement claim is falsifiable.** An `STD-*` claiming `enforced` must cite a mechanism a machine can resolve (control `id`, test name, ratchet). An unfalsifiable claim is a **CM-1** schema failure — *"a rule that cannot fail is decoration."*
2. **Provenance is mandatory.** Every file in this layer carries a base-SHA header (C16.4). A layer document without one is a documentation defect.
3. **Re-attestation on amendment.** Any `STD-*` touched by an amendment has its `Current enforcement` **re-verified against the tree at that time** (C18.4) — the same revalidation discipline that produced it. *This is not ceremony: the audit that seeded this layer had its #1 finding fixed (#662) and two more superseded (ADR-0102, #666) within a day.*
4. **Never auto-fix.** No maintenance job edits a standard, the matrix, the registry, or branch protection to make itself pass. It **reports**; a human lands the correction (**LAW-CI-08**).
5. **A stale row is not a passing row.** A scorecard/standards row whose evidence cannot be re-derived is **stale**, and stale reads as *unknown*, never as *fine* (**C7.4** — ambiguity is never resolved as success).

## Review cadence

| Trigger | Action | Owner |
|---|---|---|
| An `STD-*`-touching PR | re-attest that standard's `Current enforcement` (requirement 3) | author |
| A `LAW-*` / ADR / registry-control change | run the CM-*/SM-* report; correct any `[REFERENCE]` that now dangles | constitution maintainer |
| `SLICE-STD-*` lands | flip that standard's `Current enforcement`; update the matrix (**SM-2** blocks a partial update) | slice author |
| Phase-E branch-protection mutation (ADR-0101) | re-attest **STD-LAYOUT-01** / **STD-DEP-02** (`CI-BASEINSTALL` advisory → required) | operator |
| Periodic | re-score [`ENGINEERING_SCORECARD.md`](ENGINEERING_SCORECARD.md) **by re-deriving each row's evidence**, never by reading the previous score | reviewer |

## Explicit non-goals

- **No second registry; no competing validator.** (ADR-0100's two hard constraints, inherited verbatim.)
- **No new required check.** Promotion of any `SM-*` follows ADR-0101 §8, later, and is not proposed here.
- **No executable code in this phase** — `SM-1..SM-3` are specifications, sequenced **after** `SLICE-CONSTLINT` builds the host.
- **No re-planning of CM-\*.** If a check belongs in the shared engine, it is proposed as a **CM-\*** in `CONSTITUTION_MAINTENANCE.md`, not forked here.
