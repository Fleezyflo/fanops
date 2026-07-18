# FanOps — The Canonical Implementation Contract

**Cycle 6 · 2026-07-14 · git HEAD `fcffa73`**
**This is the implementation contract. No code modification may exceed it without an explicit, recorded
architectural approval.**

Machine-readable (the Cycle-7 enforcement surface): [`contract/`](contract/) ·
Corrections: [`CYCLE6_CORRECTIONS.md`](CYCLE6_CORRECTIONS.md) ·
Governance: [`IMPLEMENTATION_GOVERNANCE.md`](IMPLEMENTATION_GOVERNANCE.md) ·
Readiness: [`IMPLEMENTATION_READINESS.md`](IMPLEMENTATION_READINESS.md)

> **Cycle 6 implemented nothing, designed no architecture, and discovered no defects.** It froze the surface. It
> measured the live tree — which Cycle 4 asked for five times and never did. And it closed two holes in the
> program that Cycle 4 could not have seen.

---

## 1. What Cycle 6 changes, in four sentences

**Cycle 4 designed ten slices without a dependency model.** Cycle 5 then built one and found that **module
initialization order is load-bearing and nothing enforces it** — 56 strictly-upward lazy imports, any one of
which, hoisted, breaks the process at start. **Not one of Cycle 4's ten prompts forbids that**, and a reviewer
would wave the change through as a cleanup. That is `GB-1`, and it now binds every slice.

**Cycle 4 also left a root cause with no slice.** Ten roots, ten slices, and it looks like a bijection — but
`RC-4`+`RC-5` collapse into `S01`, and **`RC-9` maps to nothing.** Deferring it was right; **losing track of it
was not.** `S11` discharges it — not by fixing an unreachable bug, but by making its unreachability **mechanical**.

**And Cycle 4 asked five read-only questions about the live tree and answered none of them.** Cycle 6 answered
all five. **Three collapse a risk Cycle 4 rated as blocking. One sharpens the only CRITICAL slice from
"reachable in principle" to "on the procedure this operator runs every few weeks."**

---

## 2. The five measurements that reshape the program

Read-only, live tree, 2026-07-14. Full detail: [`CYCLE6_CORRECTIONS.md`](CYCLE6_CORRECTIONS.md).

| # | Question Cycle 4 asked | Answer | What it does |
|---|---|---|---|
| **LS-1** | *How many posts are stranded in `submitting`?* | 🔴 **0** — all 347 posts are `awaiting_approval` | **`S04`'s escalation burst — Cycle 4's *"most important note in this audit"* — has a blast radius of ZERO.** The report-only mode is still required, **as a permanent gate**, not a one-time warning. |
| **LS-2** | *How many moments are already `retired`?* | 🔴 **0** | **`PD-3` has cost nothing.** The irreversible actuator has **never fired**. The question is no less *required*; it is no longer *urgent*. |
| **LS-3** | *Does the live `accounts.json` contain a malformed backend?* | 🔴 **No — 5 channels, all canonical** | **The `S02`↔`S10` conflict — Cycle 4's *"single most dangerous scope expansion"* — is NOT REACHABLE.** `S02` cannot unfreeze irreversible retirement, because **there is no typo to normalize.** |
| **LS-4** | *Are any `Render` rows minted?* | **0 renders, 0/347 posts with a `render_id`** | **`IF-1` confirmed against the live tree**, not merely by AST. `S09`'s forbidden "repair" of `Render.path` stays forbidden. |
| **LS-5** | *How big is the shrink leak?* | **41 dirs · `04_agent_io` = 924 MB** | `S09`'s one-time clean is **real, bounded, and cheap.** |
| **LS-7** | *(not asked)* **Does the operator actually run `restore_snapshot`?** | 🔴 **11 snapshot/backup artifacts, Jun 20 → Jul 10** — including a genuine `ledger.snapshot.*.json` and a `prewipe-` backup | 🔴 **`S01` is not merely first by severity. It is on the machinery this operator reaches for every few weeks, with a daemon installed.** |

> **`LS-6`:** the `06_published/` archive holds **73 records across four days**. The publish lifecycle **has been
> exercised 73 times** — the current ledger reads `published: 0` only because it was **wiped**. I nearly wrote
> the opposite; the archive caught it. (`C6-SC-1`.)

---

## 3. The global boundaries — what **no** slice may do

These bind **every** slice, including any a future cycle adds. **Four of the seven derive from Cycle 5 and
therefore appear in no Cycle-4 prompt.** They forbid; they do not build. They cost nothing.

| | Boundary | Why |
|---|---|---|
| 🔴 **GB-1** | **No slice may hoist a lazy `fanops` import to module level**, or add a module-level import to an equal-or-higher layer level. | 56 **strictly-upward** lazy imports. The 11-level DAG exists **only because they are deferred**. `config` — L0, fan-in **82** — reaches **up** to `accounts` (L2). **`S02` touches `accounts`.** Hoisting one **looks like a cleanup** and **breaks the process at start.** Nothing enforces this: no test, no lint rule, no layer declaration. |
| 🔴 **GB-2** | **No slice may delete a symbol on the strength of "zero callers."** | `providers.py` dispatches six backend factories via lazy in-function import **lambdas** from a dict. **A name-based call graph flags all six as uncalled. All six are live** (`COUP-10`). *Zero callers is a lead, never a verdict.* |
| 🔴 **GB-3** | **No slice may set `extra="forbid"` on a ledger model.** | Forward-compat holds by pydantic's **default**, not by declaration. Setting `forbid` — a change that **looks like tightening** — turns a forward-rolled ledger into a hard `ControlFileError`. **`S02`'s rejected Option C was exactly this.** |
| 🔴 **GB-4** | **No slice may create a new write path to `published`/`analyzed` without an explicit non-empty `public_url` guard at the call site.** | The R1 invariant fires **at construction only**. `model_copy` and `setattr` both bypass it. Four manual guards hold the line. **A fifth door saves cleanly and bricks the next `Ledger.load`.** **`S03`, `S04` and `S06` all mutate `Post`.** |
| **GB-5** | **No slice may convert a `setattr` on a `Moment` to `model_copy`** — not even "for consistency." | `Moment` is the only model with `validate_assignment=True`. **`model_copy` bypasses it anyway.** `cast_add`/`cast_remove` are correct *only* because of that setattr (`COUP-07`). |
| 🔴 **GB-6** | **The AST ratchets are a per-slice budget — and one is a *shared, exact-equality* budget across three slices.** | See §4. |
| **GB-7** | Inherited repo policy: **never run the suite locally · never mass-reformat · `(Unit: <slug>)` in the PR title · one landing session at a time.** | Project `CLAUDE.md` + memory. |

---

## 4. 🔴 The ratchet budgets — mechanical, in CI **today**, and Cycle 4 never derived them

Cycle 4 told every slice to *"replay both AST ratchets."* Correct, and insufficient. **Read from the test files
at `fcffa73`:**

### The exact-equality trap

```python
# tests/test_internal_prints_routed.py — the SINGLE SOURCE OF TRUTH for this budget
_CLI_PRINT_COUNT = <N>        # ← N is declared THERE and nowhere else. This doc deliberately does NOT copy it:
                              #    it rotted THREE times as a copy (147 at contract freeze, then 158, then 165).
assert len(_print_call_nodes(_SRC / "cli.py")) == _CLI_PRINT_COUNT   # ← EQUALITY, not a ceiling
```

> **Where the number lives:** measured in `src/fanops/cli.py` → declared once in
> `tests/test_internal_prints_routed.py` → generated into [`derived/ratchets.json`](derived/ratchets.json) →
> mirrored in exactly ONE declared contract copy (`contract/implementation_contract.json` `GB-6`), which
> **`IMPL-007`** holds to the test. Every other governance document references it symbolically, and Cycle-6
> historical snapshots keep their original value as prose. Never copy the literal into a new file.

> 🔴 **CYCLE-7 CORRECTION — and it is the proof this whole governance layer was necessary.**
> This contract froze the constant at **147** as a `DERIVED_FACT` "read from the test files at `fcffa73`."
> **One commit later (#634) the test said 158 and the contract still said 147.** A slice obeying `GB-6`
> *as written* would have been told the budget was 147 while CI enforced 158. The number is now
> regenerated into [`derived/ratchets.json`](derived/ratchets.json) and enforced by rule **`IMPL-007`**,
> which fails CI whenever this copy disagrees with the test that actually enforces it.
> **Migration target: delete the copy, point at the derived twin.**

> 🔴 **`S08`, `S09` and `S10` all touch `cli.py`.** If two land carrying a bumped constant, **the second's rebase
> yields a wrong count and CI goes red for a reason unrelated to its change.**
>
> **THE RULE: no slice may change `cli.py`'s `print()` count.** Route new operator output through `get_logger`,
> or reuse an existing print. **This is free — none of the three needs a new print — and it dissolves the
> coupling rather than serializing around it.**

**Also:** `_INTERNAL_MODULES` includes **`ledger.py`** (S01's file) and **`pipeline.py`** (S07's file) — both must
contain **zero** `print()`.

### The zero-budget files

`tests/test_swallow_ratchet.py` keys a **per-file baseline dict** (49 files) and fails on a file **not in it**
gaining *any* silent broad `except`. These slice-owned files are **not in the baseline**:

> **`ledger.py` · `ledger_sqlite.py` · `settings.py` · `post/__init__.py` · `post/metrics.py` · `adjust.py`**
>
> **Any new silent broad `except` in them is an instant CI failure — not a budget overrun.**

Ceilings for the rest: `accounts.py` 3 · `run.py` 4 · `reconcile.py` 9 · `studio/actions.py` **23 (shared
S06+S09)** · `pipeline.py` 13 · `daemon.py` 5 · `health_model.py` 5 · `cli.py` **3 (shared S08+S09+S10)** ·
`compress.py` 1 · `doctor.py` 8.

---

## 5. The slice catalog

**12 slices. Two classes.**

- **REMEDIATION** — changes runtime behaviour to close an approved root cause.
- 🔴 **GUARD** — changes **no** runtime behaviour. Adds mechanical enforcement so a closed (or unreachable)
  hazard cannot silently re-open. **A GUARD is how an unreachable root cause is discharged without
  over-engineering a fix for a bug nobody can hit.**

| | Slice | Class | Root | Sev | Prereq | Verifier? |
|---|---|---|---|---|---|---|
| **S01** | `restore_snapshot` in-place under the ledger lock | REMEDIATION | RC-4 + RC-5 | 🔴 **CRIT** | — | ✅ **briefed on RC-5** |
| **S02** | backend normalization at the read boundary | REMEDIATION | RC-3 | HIGH | — | ✅ |
| **S03** | the CLAIM refuses a post it will not POST | REMEDIATION | RC-1 | HIGH | — | ✅ |
| **S04** | terminal ladder = `f(state, age)` | REMEDIATION | RC-2 + RC-8 | HIGH | S03 | ✅ |
| **S05** | Zernio `404 → unknown` | REMEDIATION | RC-2 | MED | **S04** | ❌ |
| **S06** | revert clears the stale reason | REMEDIATION | RC-8 | MED | S03 | ❌ |
| **S07** | reconcile/publish gate parity | REMEDIATION | RC-3b | HIGH | S02 | ✅ **must confirm the learn gate** |
| **S08** | `alive` ≠ `succeeding` | REMEDIATION | RC-6 | MED | — | ❌ |
| **S09** | the shrink temp dir gets an owner | REMEDIATION | RC-10 | LOW | — | ❌ |
| **S10** | irreversible retirement | REMEDIATION | RC-7 | MED | 🔴 **PD-3** | 🔴 **BLOCKED** |
| 🔴 **S11** | **pin the mutation boundary — `RC-9` cannot silently become reachable** | **GUARD** | **RC-9** | LOW-effort / **CRIT-if-reached** | — | ❌ |
| ⚠️ **S12** | pin the layering — a lazy import cannot be hoisted | **GUARD** | *(AR-04 — a **risk**, not a root cause)* | MED | 🔴 **PD-5** | ❌ |

### Why `S11` exists

**`RC-9` is an orphaned root cause.** Cycle 4 deferred it correctly (*"zero reachability … fixing it first would
spend the audit's credibility on a bug nobody can hit"*) and then **stopped tracking it**.

**`S11` does not fix `RC-9`.** `model_copy` still bypasses every validator. **It pins the unreachability**: an AST
policy test whose baseline is the **four known guarded doors**, and **a fifth makes CI red.**

> **That converts an inspection result with a shelf life into a mechanical invariant.** It is test-only, has zero
> blast radius, self-merges on green — and it is why the recommended order lands it **second**, before the three
> slices that all mutate `Post`.

### Why `S12` is **proposed, not approved**

`AR-04` is a **Cycle-5 architectural risk**, not an approved Cycle-4 **root cause**. The brief requires that every
code modification trace to an approved root cause.

> 🔴 **Slipping `S12` into the program would be exactly the hidden scope expansion this contract exists to
> prevent.** It is surfaced as **`PD-5`**, a decision for the operator.
>
> **`GB-1` — the boundary that *forbids* hoisting — needs no approval and binds everyone today.** Only the
> **guard that enforces it** is code.

---

## 6. The implementation DAG — **acyclic, and honestly so**

```
   S01 ─┐                                   S08 ─┐
   S02 ─┼─ (roots)                          S09 ─┼─ (roots)
   S03 ─┘                                   S11 ─┘

   S03 ──▶ S04 ──▶ S05          S02 ──▶ S07
    └───▶ S06

   S10  ✕ BLOCKED (PD-3)        S12  ✕ BLOCKED (PD-5)
```

**4 ordering edges. 0 back edges. 12 singleton SCCs.**

### 🔴 The two **co-requirements** — which are *not* ordering edges

| Pair | Invariant | Why it is **not** a cycle |
|---|---|---|
| **S04 ⟷ S07** | `INT-2` | If `_reconcile_safe` is gated off, **S04's ladder never runs.** S04 is correct but *unreachable* until S07 lands. **Neither must precede the other.** |
| **S04 ⟷ S06** | `INT-7` | Two halves of `RC-8`, in different files. **No file conflict.** `INT-7` holds only when **both** land. |

> **Modelling these as ordering edges would manufacture a DAG cycle that does not exist — the `C5-SC-2` error,
> applied to the implementation graph.** A co-requirement says *"both are needed for the invariant"*; an ordering
> edge says *"this one first."* **They are different claims and the contract keeps them apart.**

---

## 7. 🔴 The recommended landing order — and why it **inverts** Cycle 4's

| # | Slices | Why |
|---|---|---|
| **1** | **S01** | The only CRITICAL — and **`LS-7` proves it sits on the procedure the operator actually runs.** Nothing lands ahead of it. |
| **2** | 🔴 **S11** | **Free, and it protects everything that follows.** S03/S04/S06 all mutate `Post`; **S11 makes `GB-4` mechanical *before* they land, not after.** Test-only, self-merges on green. **The cheapest risk reduction in the program.** |
| **3** | **S03 → S04 → S05 → S06** | 🔴 **THE WINDOW IS NOW.** `LS-1`: the reconcilable set is **empty** — S04's migration is a **no-op today**. **The moment the operator approves one of the 347 pending posts and it publishes, that stops being true.** Close the lifecycle before it is next exercised; the system has already walked it **73 times**. |
| **4** | **S02 → S07** | `LS-3` decouples S02 from `PD-3` on the current tree. **Re-verify at merge.** |
| **5** | **S08 · S09** | Independent. Placed here by severity alone. |
| **6** | S10 · S12 | 🔴 **Blocked on human decisions.** |

**Parallel-safe:** `S01 · S02 · S08 · S09 · S11`. **Sequential:** `S03 → S04 → S05`. **Design together,
implement apart:** `S02 + S07`.

**Reframe stream:** ✅ **verified disjoint by state ownership** (Cycle 4) — and `LS-4` re-confirms it against the
live tree. **May proceed in parallel.**

---

## 8. The verification contract — four kinds of test, and Cycle 4 mixed them

| Class | What it proves | Fails today? |
|---|---|---|
| **INVARIANT** | An intended invariant (`INT-1`…`INT-9`) now **holds**. | ✅ **Must.** If it passes today, it is not testing the defect. |
| **PRESERVATION** | A capability that must **survive** the change. | ⚪ Passes today; must keep passing. |
| 🔴 **CONTRACT** | Guards a **forbidden scope expansion**. Protects the *boundary*, not the behaviour. | ⚪ Passes today; would fail **if the forbidden change were made**. |
| 🔴 **REGRESSION-LOCK REWRITE** | A test that **currently encodes the defect**. **There is exactly one** (`RC-5`). | 🔴 It **passes today — *because of* the data loss.** |

### The single most valuable test in the program

> **`test_learn_pass_is_still_gated_on_is_live_backend`** — a **CONTRACT** test in `S07`.
>
> `S07` removes the `is_live_backend` gate from `_reconcile_safe`. **The same predicate gates `_learn_pass`**,
> which calls `adjust.retire`, which writes `MomentState.retired` — **a state `reconcile_moments` refuses to
> undo.** *"Unifying the gating"* is **the most natural-looking cleanup in that diff**, and it would run
> **permanent, irreversible moment retirement** on a deployment where learning was frozen — **while `PD-3`, the
> question of whether that policy is even correct, is unanswered.**
>
> **One test prevents the worst outcome this program can produce. Write it.**

### And the most dangerous artifact

> 🔴 **`tests/test_ledger_sqlite_store.py:161-186` asserts the data-loss outcome and calls it correct.** It passes
> **because** the writer's committed row was destroyed. Its comment names a mechanism that does not exist
> (*"restorer blocked on flock"* — measured: **0.001 s, never blocked**).
>
> **Any correct fix to `RC-4` turns it red. A maintainer seeing a green test go red will read *the fix* as the
> regression.** The rewrite **must ship in the same PR**, and **the verifier must be briefed** — a named approval
> precondition, not a courtesy.

**Cycle 6 checked all 18 of Cycle 4's proposed tests for the same inversion. Only this one is inverted.**

---

## 9. Rollback — *"revert"* is not one thing

| Class | Meaning |
|---|---|
| **CODE-REVERSIBLE** | `git revert` fully restores prior architectural behaviour. |
| **DATA-IRREVERSIBLE** | Revert restores the **code**. **Ledger rows or disk state persist.** |
| 🔴 **WORLD-IRREVERSIBLE** | Revert restores the **code**. **Posts on the internet persist. No revert unpublishes.** |

Cycle 4 wrote *"revert"* for nine of ten slices. **For two that is insufficient — and the live measurement tells
us both residues are currently empty:**

- **`S02` is WORLD-IRREVERSIBLE *conditionally*** — if it normalizes a typo and a dark channel publishes. 🔴
  **`LS-3`: no typo exists. The condition does not fire. Effective class today: CODE-REVERSIBLE. Re-verify at
  merge.**
- **`S04` is DATA-IRREVERSIBLE** — revert does not un-label posts the ladder stamped. 🔴 **`LS-1`: zero rows to
  stamp. Effective class today: CODE-REVERSIBLE. Re-verify at merge.** *(And the residue is harmless anyway:
  `GAVE UP:` is a **label**, it changes no state, and the labels are **true**.)*
- **`S09` is DATA-IRREVERSIBLE** — the clean deletes 41 dirs. **Garbage by construction.** Recorded for honesty.

**The one rollback that can *harm*:** a **dirty revert of `S07`** that also takes `_learn_pass`'s gate with it.
**That would arm irreversible retirement.** S07's post-rollback validation exists entirely for this.

---

## 10. What this program deliberately does **not** do

- **It does not fix `RC-9`.** Zero reachability. **It pins it** (`S11`), and the contract says so rather than
  implying the invariant now holds.
- **It does not fix `AR-04`** unless `PD-5` approves `S12`. **`GB-1` forbids making it worse; only `S12` would
  make it enforceable.**
- **It does not fix `AR-13`** (CSRF on 108 mutating routes). A **recorded, accepted** decision. Re-raise as a
  **product** question, not a bug.
- **It does not fix `INV-04`** (`intro_match` structurally unanswerable). Dormant behind a **default-off** flag.
- **It does not build a `Post` lifecycle state machine.** Correct long-term; **deferred until S03/S04/S06 settle
  the semantics** — so it would encode a *known-correct* contract rather than the current one.
- **It does not touch the reframe stream.** Verified disjoint by **state ownership**, re-confirmed by `LS-4`.

---

## 11. 🔴 The risk this contract runs

**This repository's signature defect — found in all five prior cycles — is:**

> *"The doc names a mechanism that does not exist, while the property survives via a different one."*

It has appeared in the docs (`INV-01/02/03/05/07`), in the **code comments** (four for four), and — most
dangerously — **in the test layer** (`AR-03`/`RC-5`).

**This contract is a doc. It is the next candidate.**

That is why **every boundary here is a machine-checkable predicate** — a file path, a function name, a numeric
baseline, an AST assertion — and **not prose**. It is why [`contract/file_ownership.json`](contract/file_ownership.json)
exists and why this document is its **twin**, not its **source**.

> 🔴 **Until Cycle 7 lands, this contract is enforced by attention. That is better than nothing, and it is not a
> mechanism.** The two AST ratchets already in CI are the proof that this repository *can* enforce a policy
> mechanically. **They are the model. Cycle 7's whole job is to follow it.**
