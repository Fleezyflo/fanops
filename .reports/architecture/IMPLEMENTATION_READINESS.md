# FanOps — Implementation Readiness Report

**Cycle 6 · 2026-07-14 · git HEAD `fcffa73`**

**Verdict: READY, with two named blockers and one named residual.**

---

## 1. Adversarial validation — I tried to invalidate this program

The brief requires it: *"Attempt to invalidate the implementation contract … Repair the implementation program.
Do not repair the repository."* Below is what I looked for and what I found.

| Attack | Result |
|---|---|
| **Hidden coupling** | 🔴 **FOUND ONE.** `cli.py`'s `_CLI_PRINT_COUNT = 165` is an **exact-equality** assertion, and **three slices touch `cli.py`.** Two landing with a bumped constant break each other. **Cycle 4 did not name it.** → **Repaired:** *no slice may change the print count* (`GB-6`/`IR-4`). |
| **Overlapping slices** | 🔴 **FOUND TWO.** `studio/actions.py` (S06 + S09) and `cli.py` (S08 + S09 + S10). Cycle 4 called both *"same-file conflict only; a rebase resolves it."* **That is true of the TEXT and false of the CONTRACT** — two slices editing one file with no declared partition is how one silently widens into the other. → **Repaired:** both **partitioned by function** in `file_ownership.json`. |
| **Duplicated ownership** | ✅ **NONE.** Every file has exactly one owning slice, or an explicit function-level partition. |
| **Rollback conflicts** | 🔴 **FOUND ONE.** If `S03` and `S04` are both merged, **revert `S04` first** — reverting `S03` alone re-opens the strand-creation path while the ladder is still draining. Safe either way; the pair has a preferred order. → **Recorded.** |
| **Verification gaps** | 🔴 **FOUND ONE, AND IT IS THE BIG ONE.** `S07` removes a gate on `is_live_backend`; the **same predicate** gates `_learn_pass` → `adjust.retire` → **irreversible `MomentState.retired`**. Cycle 4 wrote it into the prompt as *forbidden*, but **proposed no test**. → **Repaired:** `test_learn_pass_is_still_gated_on_is_live_backend`, a **CONTRACT** test. **One test prevents the worst outcome this program can produce.** |
| **Missing preservation boundaries** | 🔴 **FOUND FOUR, all from Cycle 5 — which did not exist when the slices were designed.** `GB-1` (lazy-import hoist), `GB-2` (false dead code), `GB-3` (`extra="forbid"`), `GB-4` (the fifth poison-pill door). **Not one appears in any Cycle-4 prompt.** → **Repaired as GLOBAL boundaries.** |
| **Undocumented dependencies** | 🔴 **FOUND ONE.** `S04`'s ladder is **unreachable** unless `S07` lands. Cycle 4 noted it in the matrix but the sequence treats them as independent phases. → **Repaired:** modelled as a **co-requirement**, and `INT-2` is marked **incomplete until both**. |
| **Dependency cycles** | ✅ **NONE.** 4 ordering edges, 0 back edges. **And the two co-requirements are deliberately *not* modelled as ordering edges — doing so would manufacture a cycle that does not exist** (the `C5-SC-2` error, applied to the implementation graph). |
| **Migration ambiguity** | 🔴 **FOUND — AND RESOLVED BY MEASUREMENT.** Cycle 4's `S04` migration was *"every stranded post escalates at once … 347 posts"*. **The actual reconcilable set is ZERO.** Cycle 4 used the wrong denominator. |
| **Conflicting prompts** | 🔴 **FOUND ONE.** `C4-SLICE-02` mandates a loud `is_live_backend` transition log *"if PD-3 is unanswered"*; `C4-SLICE-10` calls the same interaction *"the single most dangerous scope expansion."* **Both rest on a hazard that does not exist on this tree** (0 malformed backends). → **Repaired:** disarmed, and **re-armed as a merge gate**. |
| **Architectural assumption leakage** | 🔴 **FOUND ONE, IN MY OWN DRAFT.** I nearly wrote *"nothing has ever published on this tree"* from `published: 0`. **The `06_published/` archive holds 73 records.** The ledger was **wiped**. That error would have **downgraded `RC-1`/`RC-2` from "defects on a road the system has driven" to "latent hazards."** → **Recorded as `C6-SC-1`.** |
| **Traceability gaps** | 🔴 **FOUND ONE, AND IT IS STRUCTURAL.** **`RC-9` maps to no slice.** Ten roots, ten slices — but `RC-4`+`RC-5` collapse into `S01`. **`RC-9` was deferred and then simply stopped being tracked.** → **Repaired:** `S11`, a **GUARD**. |
| **Scope smuggling** *(my own)* | 🔴 **CAUGHT MYSELF.** `S12` (the `AR-04` layering guard) traces to a **Cycle-5 risk**, not an approved **root cause**. **Including it silently would be exactly the hidden scope expansion this contract exists to prevent.** → **Surfaced as `PD-5`. Not built.** |

---

## 2. Readiness by slice

| | Slice | Ready? | Blocker |
|---|---|---|---|
| **S01** | restore in-place under the ledger lock | ✅ **READY — SHIP FIRST** | — *(verifier must be briefed on `RC-5`)* |
| **S02** | backend normalization | ✅ **READY** | — *(re-run the live `accounts.json` probe at merge)* |
| **S03** | the CLAIM refuses | ✅ **READY** | — |
| **S04** | terminal ladder = f(state, age) | ✅ **READY** | — *(ship report-only mode first; re-run the reconcilable count)* |
| **S05** | Zernio 404 → unknown | ⚠️ **GATED** | **`S04` must merge first.** Alone it is a forbidden shallow fix. |
| **S06** | revert clears the stale reason | ✅ **READY** | — |
| **S07** | gate parity | ✅ **READY** | — *(pre-flight decides Option A vs B; verifier must confirm the learn gate)* |
| **S08** | `alive` ≠ `succeeding` | ✅ **READY** | — |
| **S09** | shrink dir gets an owner | ✅ **READY** | — |
| **S10** | irreversible retirement | 🔴 **BLOCKED** | **`PD-3` — a human decision. No recommendation is offered.** |
| **S11** | pin the mutation boundary | ✅ **READY — SHIP SECOND** | — |
| **S12** | pin the layering | 🔴 **BLOCKED** | **`PD-5` — `AR-04` is a risk, not an approved root cause.** |

**10 of 12 ready. Both blockers are human decisions, not engineering gaps.**

---

## 3. The two decisions that are actually blocking

### 🔴 `PD-3` — is irreversible retirement at n = 3 intentional policy, or an oversight?

**Blocks `S10` entirely.**

**No recommendation is offered, and that is deliberate.** The audit brief forbids inferring product intent, and
**the guards that exist are real and considered** — a loser must be in the bottom 20 % **and** below `lift_floor`
**and** not a winner **and** not `lift_degraded`. **Somebody thought about this.** That is evidence *for* intent.

**What Cycle 6 adds — the number Cycle 4 asked for and never got:**

> 🔴 **ZERO moments are retired on the live ledger. The irreversible actuator has NEVER FIRED.**
>
> **The policy question is exactly as open as it was. But it has cost nothing, there is no cleanup burden, and —
> because there are no malformed backends — `S02` cannot unfreeze it either.**
>
> **`PD-3` is no less REQUIRED. It is no longer URGENT.**

### 🔴 `PD-5` — is `S12` in scope?

**Blocks `S12` entirely.** *(New in Cycle 6.)*

`AR-04` — **56 strictly-upward lazy imports; hoisting any one breaks the process at start; nothing enforces it**
— is a **Cycle-5 architectural risk**, not an approved **root cause**.

- **`GB-1`** (the boundary that **forbids** hoisting) **needs no approval.** It forbids a change; it does not
  make one. **It binds every slice today.**
- **`S12`** (the guard that **enforces** `GB-1`) **is code.** Building it unbidden would be a hidden scope
  expansion.

**Recommendation: APPROVE.** It is test-only, has zero blast radius, and it protects all eleven other slices —
including `S02`, which touches `accounts` (L2), the module `config` (L0, fan-in **82**) reaches **up** to.
**But it is the operator's call, and it is surfaced rather than smuggled.**

---

## 4. What would falsify this contract

| Claim | Falsified by |
|---|---|
| **The `S02`↔`S10` hazard is not reachable** | A malformed backend value appearing in `accounts.json`. → **Re-armed as a merge gate.** |
| **`S04`'s migration is a no-op** | Any post entering `submitting`/`submitted`/`needs_reconcile`. → **Re-armed as a merge gate.** |
| **`PD-3` has cost nothing** | Any moment in state `retired`. → **Re-armed as a merge gate.** |
| **`Render.path` is unreachable** | A production caller of `Ledger.add_render`. → **`S09`'s regression test is written to catch exactly this** (`IF-1`). |
| **Cycle 4's citations are sound** | A cited `file:line` that no longer resolves. → **All 16 verified at `fcffa73`. Gate `G1` re-checks at implementation time.** |
| **The implementation DAG is acyclic** | A back edge. → **Re-run Tarjan over `contract/implementation_contract.json → implementation_dag`.** |
| **`RC-9` is unreachable** | A **fifth** door to `published`/`analyzed` without a `public_url` guard. → **That is precisely what `S11` makes CI-red.** |

---

## 5. The residual — stated plainly

> 🔴 **This contract has never been adversarially reviewed by an independent agent.**
>
> `OPS-001` has refused every subagent spawn for **six consecutive cycles**. Cycle 6 attempted no exception.
>
> **Mitigation:** every **collapsible** risk in this program was collapsed by a **direct measurement of the live
> tree**, not by an argument. *A measurement does not need a second opinion the way an inference does.* The
> boundaries that remain **inferred** (`GB-1`…`GB-5`) are inherited from Cycles 2–5, each independently derived
> and each already surviving one round of correction.
>
> **What has NOT been independently checked is the SYNTHESIS** — the claim that these twelve slices, in this
> order, with these boundaries, constitute a complete and internally consistent program.
>
> **Disengage is `orchestrate.py stop`. An operator action, not a code change.**

---

## 6. Cycle 7 can proceed

The completion standard asks whether *"Cycle 7 can mechanically enforce the implementation contract without
introducing new architectural assumptions."*

**Yes — and it does not need to invent a mechanism, because this repository already has one.**

> **The two AST ratchets (`test_swallow_ratchet.py`, `test_internal_prints_routed.py`) are proof that FanOps
> CAN enforce a policy mechanically, in CI, today.** They are the model.
>
> **`S11` and `S12` are not merely slices — they are Cycle-7 enforcement, landed early.** That is the entire
> point of the **GUARD** class: *the guard is the mechanism.*

Every boundary in this contract is a **machine-checkable predicate** — a file path, a function name, a numeric
baseline, an AST assertion. **Nothing in [`contract/`](contract/) requires a judgement call to enforce.**

**That is deliberate, and it is defensive.** This codebase's signature defect — found in **all five** prior cycles
— is *"the doc names a mechanism that does not exist."* **This contract is a doc.** It was written to be
executed, not believed.

> 🔴 **Until Cycle 7 lands, this contract is enforced by attention. That is better than nothing, and it is not a
> mechanism.**
