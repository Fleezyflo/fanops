# FanOps — Implementation Governance

**Cycle 6 · 2026-07-14 · git HEAD `fcffa73`**

Every implementation must pass these gates before merge. **This governance model COMPOSES with the repository's
existing rules — it does not invent a parallel one.** Where a rule already exists in `CLAUDE.md`, in CI, or in
project memory, it is **cited, not restated**, and it **wins**.

---

## 1. The gates, in order

```
  ┌─ G0  SCOPE ──────────────────────────────────────────────────────────┐
  │  The slice has a prompt in contract/prompts/. It is APPROVED         │
  │  (not PROPOSED). Its prerequisites are MERGED. Its product           │
  │  decisions are ANSWERED or explicitly NON-BLOCKING.                  │
  └──────────────────────────────────────┬───────────────────────────────┘
  ┌─ G1  RE-VERIFY ──────────────────────▼───────────────────────────────┐
  │  Every file:line the prompt cites is RE-RESOLVED against current     │
  │  source. If one does not resolve, STOP and RE-DERIVE it.             │
  │  (Cycle-2 method note: every prior cycle's citations rotted at       │
  │   least once. Cycle 4's did NOT — verified — but yours may.)         │
  └──────────────────────────────────────┬───────────────────────────────┘
  ┌─ G2  LIVE-STATE MERGE GATE ──────────▼───────────────────────────────┐
  │  S02 · S04 · S10 ONLY. Re-run the read-only probe. STATE the result. │
  │  A risk collapsed on 2026-07-14 and not re-checked at merge is a     │
  │  risk that was merely NOT LOOKED AT.                                 │
  └──────────────────────────────────────┬───────────────────────────────┘
  ┌─ G3  BOUNDARY ───────────────────────▼───────────────────────────────┐
  │  The diff touches NO file outside file_ownership.json, and NO        │
  │  function outside its permitted region in a partitioned file.        │
  │  GB-1..GB-7 unviolated.        ← MECHANICALLY CHECKABLE (Cycle 7)    │
  └──────────────────────────────────────┬───────────────────────────────┘
  ┌─ G4  CI ─────────────────────────────▼───────────────────────────────┐
  │  `unit` GREEN. BOTH AST RATCHETS REPLAYED. No baseline raised.       │
  │  NEVER RUN THE SUITE LOCALLY.                                        │
  └──────────────────────────────────────┬───────────────────────────────┘
  ┌─ G5  VERIFICATION ───────────────────▼───────────────────────────────┐
  │  Every INVARIANT test FAILED BEFORE and PASSES AFTER.                │
  │  Every PRESERVATION test passes. Every CONTRACT test exists.         │
  └──────────────────────────────────────┬───────────────────────────────┘
  ┌─ G6  APPROVAL ───────────────────────▼───────────────────────────────┐
  │  Recorded IN WRITING. For S01: the verifier CONFIRMS they were       │
  │  briefed on RC-5. For S07: the verifier CONFIRMS the _learn_pass     │
  │  gate is untouched.                                                  │
  └──────────────────────────────────────┬───────────────────────────────┘
  ┌─ G7  MERGE ──────────────────────────▼───────────────────────────────┐
  │  `git fetch` + `gh pr view` the target FIRST. ONE landing session    │
  │  at a time. PR title carries `(Unit: <slug>)`.                       │
  └──────────────────────────────────────┬───────────────────────────────┘
  ┌─ G8  REGENERATE ─────────────────────▼───────────────────────────────┐
  │  Update the KB files named in traceability.json -> regeneration.     │
  │  An invariant that moved FALSE -> VERIFIED must be RE-CLASSIFIED.    │
  │  AN UNREGENERATED KB IS THE NEXT INV-20.                             │
  └──────────────────────────────────────────────────────────────────────┘
```

---

## 2. Approval authority

| Approval | Who | When |
|---|---|---|
| **Slice scope** | *(the contract)* — already granted for `S01`–`S11` | at contract freeze |
| 🔴 **`PD-3`** — is irreversible retirement at n=3 intentional? | **THE OPERATOR. No recommendation is offered.** | **Blocks `S10` entirely.** |
| 🔴 **`PD-5`** — is `S12` (the `AR-04` layering guard) in scope? | **THE OPERATOR.** *(`AR-04` is a **risk**, not an approved root cause. Building it unbidden would be a hidden scope expansion.)* | **Blocks `S12` entirely.** |
| `PD-1`, `PD-2`, `PD-4` | the operator — **but none blocks its slice.** Recommendations are on the record. | before or after |
| **Independent verifier** | a second engineer/agent, **not** the implementer | `S01`, `S02`, `S03`, `S04`, `S07` |
| **Rollback** | the **landing engineer** — except **`S02`** and **`S04`**, where the trigger is an operator-visible event (an unexpected publish; an unexpected escalation), so **the operator holds it.** | any time |

### 🔴 The two approvals that are about a **human misreading**, not a technical risk

1. **`S01`'s verifier MUST be briefed on `RC-5` before reviewing.** The existing test **asserts the data loss**.
   **The correct fix turns it red.** An unbriefed verifier will reject the fix as a regression. *This is the only
   approval in the program whose failure mode is a correct fix being rejected.*
2. **`S07`'s verifier MUST specifically confirm `_learn_pass`'s `is_live_backend` gate (cli.py:965) is
   untouched.** *"Unifying the gating"* is the most natural-looking cleanup in that diff and it would arm
   **irreversible moment retirement**. **Do not leave this to be noticed.**

---

## 3. Merge requirements (checklist — every slice)

- [ ] Prompt is **APPROVED**, prerequisites **MERGED**
- [ ] All cited `file:line` **re-resolved** (G1)
- [ ] Live-state probe re-run and stated — **`S02` / `S04` / `S10` only** (G2)
- [ ] Diff stays inside `file_ownership.json` (G3)
- [ ] 🔴 **`GB-1`**: no lazy import hoisted; no module-level import inverting the layering
- [ ] 🔴 **`GB-2`**: nothing deleted on "zero callers"
- [ ] 🔴 **`GB-3`**: no `extra="forbid"` on a ledger model
- [ ] 🔴 **`GB-4`**: no new unguarded door to `published`/`analyzed`
- [ ] **`GB-5`**: no `Moment` `setattr` → `model_copy` conversion
- [ ] 🔴 **`GB-6`**: **`cli.py`'s print count is still 147** · zero `print()` added to `ledger.py`/`pipeline.py` · **no swallow baseline raised**
- [ ] CI `unit` green; **both ratchets replayed**
- [ ] Every **INVARIANT** test **failed before**, passes after
- [ ] Every **PRESERVATION** and **CONTRACT** test passes
- [ ] Rollback **class** stated; residue measured if DATA/WORLD-irreversible
- [ ] Approvals **recorded in writing**
- [ ] PR title carries **`(Unit: <slug>)`**
- [ ] KB **regenerated** (G8)
- [ ] **Remaining unknowns stated honestly**

---

## 4. What Cycle 7 must mechanize

Cycle 7's job is to turn §3 from a checklist into a **gate**. Everything below is already a
machine-checkable predicate in [`contract/`](contract/):

| Check | Source | Mechanism |
|---|---|---|
| **File-ownership violation** | `contract/file_ownership.json` | diff the changed-file set against the slice's allowance |
| **Function-region violation** | same (three partitioned files) | AST: which top-level functions did the diff touch? |
| 🔴 **`GB-1` layering** | `kb/dependencies.json → layering.levels` | AST: for every **added module-level** `fanops` import, assert `level(target) < level(source)` |
| 🔴 **`GB-3`** | — | AST: no `extra="forbid"` on a ledger model |
| 🔴 **`GB-4`** | — | **this is `S11`, landed as a test.** *The guard IS the enforcement.* |
| 🔴 **`GB-6` ratchets** | the two test files | **already enforced in CI.** Cycle 7 adds the **per-slice attribution** and the **one-PR rule** on `_CLI_PRINT_COUNT` |
| **`PD-3` fence** | — | reject any PR touching `adjust.py:82-96` or `cli.py:151-155` until `PD-3` is recorded |
| **Citation rot** | the prompts | re-resolve every cited `file:line` in CI; fail on a miss |

> 🔴 **`S11` and `S12` are not merely slices — they are Cycle-7 enforcement, landed early.** That is the point of
> the **GUARD** class: *the guard is the mechanism.*

---

## 5. Release gates

**No slice in this program may be released to a live-publishing deployment until:**

1. **`S01` is merged.** The restore race deletes **real media**, and `LS-7` shows the operator runs that procedure
   every few weeks with a daemon installed.
2. **`S03` + `S04` + `S06` are merged** *(and `S07`, for `S04`'s ladder to be reachable)*. **Otherwise the first
   real publish after the 347 pending posts are approved walks straight into `RC-1`/`RC-2` — a road the system
   has already driven 73 times.**
3. **`PD-3` is answered** *(or `S07`'s CONTRACT test is in place, which is the cheap substitute)*.

> 🔴 **`FANOPS_LIVE=1` is set only by `golive.go_live`, behind: accounts-valid → ≥1 live-ready channel →
> past-due-backlog gate → explicit confirm.** That gate is **not weakened by any slice in this program** — and
> `S02`'s changes to `live_ready_channels()` feed it. **Re-verify it after `S02`.**

---

## 6. Inherited rules — cited, not restated

| Rule | Source |
|---|---|
| **NEVER run the test suite locally.** CI-only, on a PR. | project `CLAUDE.md` |
| **NEVER mass-reformat.** No `black`, no `ruff format`. | project `CLAUDE.md` (rationale in `pyproject.toml`) |
| **The 60 s pytest timeout is a deadlock guardrail.** A hanging test **is the bug**. | project `CLAUDE.md` |
| **Don't run live CLI verbs speculatively** (publish, reconcile, pull, track, up, verify-live, cutover). | project `CLAUDE.md` |
| **`(Unit: <slug>)` in the PR title**; the lowercased slug must match the verifier record filename. | memory `land-gate-needs-a-unit-tag` |
| **One orchestrator landing session at a time.** `git fetch` + `gh pr view` before merge. | memory `parallel-orchestrators-collide` |
| **Replay both AST ratchets before pushing.** | memory `fanops-ast-ratchets-catch-new-except-and-prints` |
| **`daemon_progress` is THE mid-pass liveness owner.** Don't touch `_heartbeat_age_s`. | memory `liveness-verdict-single-owner` |
| **launchd env ≠ shell env.** The plist bakes a full `PATH`. | memory `daemon-only-failures-check-plist-path` |
| **`.claude/workflows/*.js` are tracked, load-bearing.** Never delete. | project `CLAUDE.md` |

---

## 7. 🔴 `OPS-001` — six consecutive cycles, single-threaded

The orchestration gate has refused **every** subagent spawn since Cycle 1. **Cycle 6 was executed
single-threaded, like its five predecessors.** No independent agent could be spawned to **refute** this
contract's boundaries.

**Mitigation applied:** Cycle 6 grounded every **collapsible** risk in a **direct measurement of the live tree**
rather than in an argument. *A measurement does not need a second opinion the way an inference does.* The
boundaries that remain **inferred** (`GB-1`…`GB-5`) are inherited from Cycles 2–5, each independently derived.

**Residual, stated plainly:** *the **synthesis** — this contract — has never been adversarially reviewed. Its
individual claims have.*

**Disengage:** `orchestrate.py stop` — **an operator action, not a code change.** It remains the single largest
constraint on this audit's throughput.
