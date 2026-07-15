# FanOps — `tools/ci` Validator Specification (governed subsystem)

> **This is a permanent specification, not documentation of convenience.** The `tools/ci` validator is a
> **governed subsystem**: the mechanism that proves the three CI-governance planes agree. Changing it
> changes what CI can silently let through. This spec defines its ownership, invariants, guarantees,
> extension rules, failure semantics, and the ADR requirements for future changes. (Operator directive,
> 2026-07-16.) Authority: **ADR-0100** (dedicated validator, NOT `tools/arch`) and **ADR-0101**
> (required-checks policy). Registered as control **`CI-UNIT-CIVALIDATOR`**.

## 1 · Purpose and the three planes

CI governance has three planes that must not silently diverge:

- **Intent** — `.github/ci-control-registry.yml` (what the repository *declares* it enforces).
- **Implementation** — `.github/workflows/*.yml` (what CI *actually runs*).
- **Deployed** — live GitHub branch protection (what *actually gates a merge*).

The validator's single job: **prove all three agree, and fail loudly and specifically when they do
not.** It is the anti-drift mechanism for governance itself — the thing that stops the registry from
becoming a pretty fiction disconnected from the workflows and the live gate.

## 2 · Ownership

- **Owner:** `ci-lane` (the registry owner of `CI-UNIT-CIVALIDATOR`). Changes to the validator are
  CI-lane changes and follow the lane-ownership rules (ADR-0095).
- **Location:** `tools/ci/` — a **dedicated** module, deliberately **NOT** `tools/arch` (ADR-0100,
  operator amendment). The two never share code: `tools/arch` governs the *architecture* of `src/`;
  `tools/ci` governs the *CI system*. Conflating them was explicitly rejected.
- **Authoritative paths** (`tools/ci/common.py`, single source):
  `REGISTRY=.github/ci-control-registry.yml` · `SCHEMA=.github/ci-control-registry.schema.json` ·
  `WORKFLOWS=.github/workflows` · `GEN_VIEW=docs/ci/CI_CONTROL_INVENTORY.md` ·
  `PROSE_DOCS=[AGENTS.md]` · `DEFAULT_REPO=Fleezyflo/fanops` · `DEFAULT_BRANCH=main`.
- **Module map:** `checks.py` (the DCs) · `registry.py` (load + shape gate) · `workflows.py` (job
  discovery) · `live.py` (GitHub probe) · `selftest.py` (negative controls) · `cli.py` (verbs) ·
  `common.py` (paths + `Finding`).

## 3 · Invariants (what the validator guarantees hold, or reddens)

Six divergence checks (**DC-1..DC-6**, ADR-0100), each a **pure function** of `(registry, discovered
jobs)` [+ live contexts for DC-3], each emitting a `Finding` with the control id and the **exact**
divergence (actionable output is an ADR requirement):

| DC | Invariant it protects | Fires when… |
|----|-----------------------|-------------|
| **DC-1** | No required context can silently detach. | A required control's `branch_protection_context`, or a `current`/`intended_required_contexts` entry, matches **no** workflow job `name`. (Anti-silent-detach; a rename fails **closed**.) |
| **DC-2** | Registry ↔ jobs is a bijection. | A control names a `(workflow, job)` that **doesn't exist** (phantom control), **or** a workflow job has **no** control mapping to it (unknown job). |
| **DC-3** | Declared intent == live deployment. | Live required contexts != `current_required_contexts` (blocking), **or** (when `rollout.phase==enforced`) `current` != `intended`. The `current→intended` gap is a non-blocking **PLANNED TRANSITION** while transitioning. |
| **DC-4** | Prose can't contradict classification. | A `PROSE_DOCS` line names a **required** context but calls it "advisory" (or an advisory one "required"). Deterministic: exact context match + a contradicting status word. |
| **DC-5** | No hidden duplicate ownership. | Two controls share a byte-identical `invariant` **without** a common `duplicate_group`; or a `duplicate_group` has <2 members, names an unknown control, or a member lacks a `distinct_boundaries` entry. |
| **DC-6** | Every governed job is hygienic. | A job has no `timeout-minutes`, or an action `uses:` ref is **not** a 40-hex SHA pin. |

Plus a **shape gate** (`registry.py::shape_findings`, always runs — dependency-light): every control has
the eleven required fields (`id, name, invariant, owner, classification, trigger, justification,
deletion_consequence, adr, failure_evidence, status`); ids unique; `classification ∈
{required, advisory, scheduled, local}`; `status ∈ {active, transitional, deprecated, dormant}`;
`intended_required_contexts` present. When `jsonschema` is installed, full Draft-7 validation runs on
top; when it is not, the core shape checks still run — **a missing `jsonschema` can never produce a
false pass.**

## 4 · Guarantees (modes, exit codes, output)

Three verbs (`tools/ci/cli.py`), each a distinct plane:

| Verb | Planes | Network | Where enforced |
|------|--------|---------|----------------|
| `static` | intent ↔ implementation (DC-1/2/4/5/6 + shape) | none | **Blocking** on every PR — run as a unit test (`test_ci_registry_validator.py`), so it gates via the required `unit` context. Also runnable locally. |
| `deployed [--require-live]` | intent ↔ live GitHub (DC-3) | authenticated read | Scheduled / on-demand. Needs an admin-scoped token to read branch protection. |
| `reconcile` (alias `full`) | all three (static + deployed) | authenticated read | On-demand full reconciliation. |
| `selftest` | — | none | Runs the eight negative controls; proves the DCs discriminate. |

**Exit-code contract** (stable — external callers depend on it):

- **0** = clean, *or* an explicit non-authoritative **SKIP** (never a false pass).
- **1** = at least one **blocking** divergence.
- **2** = usage error (unknown verb).

**`Finding` semantics** (`common.py`) — `blocking` and `skipped` are orthogonal:

- `blocking=True, skipped=False` → a real failure (`[FAIL]`), counts toward exit 1.
- `blocking=False, skipped=False` → informational (`[INFO]`, e.g. a PLANNED TRANSITION); never fails.
- `skipped=True` → **non-authoritative** (`[SKIP]`, e.g. live protection unreadable); **NEVER counted
  as a pass.** The one rule that matters most: *absence of authority is not evidence of agreement.*

## 5 · Failure semantics (the non-negotiable behaviors)

1. **Fail closed, fail specific.** Every DC names the exact divergence and the control id. A vague or
   swallowed failure is itself a defect (ADR requirement: actionable output).
2. **A skip is never a pass.** If `deployed` cannot read live protection, it emits a `[SKIP]`
   non-authoritative finding and — with `--require-live` (the designated authenticated job) — exits **1**
   rather than reporting "in sync." Silence about the deployed plane must never look like agreement.
3. **The rollout is not self-deadlocking.** DC-3 is rollout-aware: it requires `live == current` and
   reports `current → intended` as a PLANNED TRANSITION while `phase != enforced`, so declaring the
   five-context target before OGD deploys it does **not** red CI. When `phase == enforced`, any
   `current != intended` becomes blocking.
4. **One implementation, two callers.** The negative controls live once in `selftest.py` and are invoked
   by **both** the CLI `selftest` verb and `tests/test_ci_registry_validator.py`. The pytest gate and
   the CLI can never disagree on a commit (the drift `tools/arch` was bitten by).
5. **The checks are proven, not assumed.** Every blocking DC has ≥1 negative control that injects
   exactly one defect and asserts the DC fires with evidence absent before. A check nobody has tried to
   fool is a check nobody should trust.

## 6 · Extension rules (how to change the validator — mechanically enforced)

**To add a divergence check (a new DC):**
1. Add a **pure function** `dcN_*(reg, jobs[, live])` to `checks.py` returning `list[Finding]`.
2. Wire it into `run_static` **or** `run_deployed`.
3. Add a `Control(id, expect_dc, defect)` to `selftest.CONTROLS` that injects **exactly one** defect and
   asserts the new DC fires. *This is not optional:* `test_ci_registry_validator.py::
   test_every_blocking_condition_has_a_negative_control` reddens if any DC lacks a control.
4. The new DC's output must name the exact divergence + control id.

**To add a registry control field:** update `_REQUIRED_CONTROL_FIELDS` in `registry.py` **and** the JSON
schema together (the shape gate and the schema must agree).

**To add / promote a required context:** add it to `intended_required_contexts`; DC-1 requires a mirrored
workflow job `name`; the live promotion is an **OGD** mutation (never a code change alone).

**To change a classification enum or status enum:** update the `_CLASSES` / `_STATUSES` sets in
`registry.py` **and** the schema enums together.

## 7 · ADR requirements for future changes

A change is **ADR-gated** (requires a new ADR referencing 0100/0101, operator-accepted) when it changes
**what the validator has authority over or how it decides**:

- Adding, removing, or re-scoping a **DC** (the set of divergences that block).
- Changing what is **blocking** vs advisory vs skipped (the exit-code meaning of a plane).
- Changing the **required-context policy** (which contexts are required; the rollout model).
- Changing the **classification/status enums** or the control-field contract.
- Moving the validator's ownership out of `tools/ci`, or sharing code with `tools/arch`.

A change is a **normal PR** (no ADR) when it does **not** change authority: adding a negative control,
fixing a message string, refactoring a DC's internals without changing what it flags, performance work.

**After the program freeze** (Phase 6): *any* change to this validator — even a normal-PR-class one —
begins as a **new governance program** (`CI_GOVERNANCE_DNA.md` amendment process). The freeze binds the
subsystem, not just the docs.

## 8 · Verification (run it)

```
python -m tools.ci static      # DC-1/2/4/5/6 + shape; exit 0 clean / 1 blocking
python -m tools.ci selftest     # 8 negative controls; exit 0 = all fire
python -m tools.ci deployed --require-live   # DC-3 vs live GitHub (needs admin token); skip => exit 1
python -m tools.ci reconcile    # all three planes
```

The static plane is additionally enforced as a **required** gate via `CI-UNIT-CIVALIDATOR` (the unit
lane), so no PR can merge a registry↔workflow divergence.
