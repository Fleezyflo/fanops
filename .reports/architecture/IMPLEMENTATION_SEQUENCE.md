# FanOps — Implementation Sequence

**Cycle 4 · 2026-07-14 · git HEAD `fcffa73`** · Twin: [`implementation_sequence.json`](implementation_sequence.json)

**Proposed. Nothing executed.** Approval-ready prompts: [`prompts/`](prompts/).

---

## 1. The brief proposed an order. The evidence changed it.

| The brief's order | What the evidence says |
|---|---|
| 1. observability + characterization tests | 🔴 **Not a standalone phase.** `RC-5`: the characterization test for the highest-severity defect **already exists and encodes the bug**. You cannot write a passing characterization test for `restore` first — the honest one *fails*, and the existing one *passes for the wrong reason*. **Characterization tests fold into each slice.** |
| 2. provider validation | ✅ kept (→ **S02**) |
| 3. submission lifecycle | ✅ kept (→ **S03/S04/S05**) |
| 4. manual recovery normalization | ⚠️ **Split.** Its *safety* half is **S03** (the claim refusal, which makes preserving `submission_id` safe). Its *hygiene* half is **S06** (clearing the stale reason). Different roots, files, severities. |
| 5. validation-safe mutation boundary | 🔴 **Moved to LAST.** `RC-9` has **zero current reachability** (AST-verified). Fixing it at #5 spends the audit's credibility on a bug nobody can hit **while `RC-4` silently deletes media**. |
| 6. snapshot/restore | 🔴 **MOVED TO #1.** It is the **only** defect causing **unrecoverable loss of media** (`C4-F2a`, proven), it is triggered by the **documented recovery procedure**, and it is fully independent. Nothing justifies sequencing five slices ahead of it. |
| 7. durable media + cleanup | ✅ kept (→ **S09**), and **shrunk** — the coupling that would have forced a migration is **refuted** (EXP-C4-4). |
| 8. daemon health | ✅ kept (→ **S08**) |
| 9. irreversible learning | ✅ kept (→ **S10**) — 🔴 **blocked on PD-3** |

---

## 2. The sequence

```
 PHASE 1 ── STOP THE MEDIA LOSS ─────────────────────────────────────────────
   S01  restore_snapshot in-place under the ledger lock        CRITICAL
        └─ + REWRITE the test that asserts the data loss  ← ATOMIC, non-negotiable

 PHASE 2 ── CLOSE THE LIVE-MODE CONTRACT ───────────────────────────────────
   S02  canonical backend normalization at the READ boundary   HIGH
   S07  reconcile/publish gate parity                          HIGH
        (S02 before S07 — S02 shrinks what S07 must handle)

 PHASE 3 ── GIVE THE SUBMISSION LIFECYCLE AN OWNER ── ✕ SEQUENTIAL ─────────
   S03  the CLAIM refuses a post it will not POST              HIGH
   S04  terminal ladder = f(state, age)                        HIGH   ← report-only dry run FIRST
   S05  Zernio 404 → "unknown"                                 (with/after S04 — never alone)
   S06  revert clears the stale error_reason                   MEDIUM

 PHASE 4 ── MAKE FAILURE VISIBLE ──── (buildable any time) ─────────────────
   S08  daemon health: alive ≠ succeeding                      MEDIUM
   S09  the shrink temp dir gets an owner                      LOW

 PHASE 5 ── BLOCKED / DEFERRED ─────────────────────────────────────────────
   S10  irreversible retirement policy          🔴 PD-3 UNANSWERED — DO NOT EXECUTE
   RC-9 the mutation/validator boundary         ⚪ latent, zero reachability
```

**Parallel:** `S01 · S02 · S08 · S09` may be built concurrently.
**Sequential:** `S03 → S04 → S05`.
**Design together, implement apart:** `S02 + S07`.

---

## 3. The slices

| | Slice | Root | Sev | Prereq | Self-merge on green? | Verifier? |
|---|---|---|---|---|---|---|
| **S01** | `restore_snapshot` in-place under the ledger lock | RC-4+RC-5 | **CRIT** | — | ❌ | ✅ **briefed on RC-5** |
| **S02** | backend normalization at the read boundary | RC-3 | HIGH | — | ❌ | ✅ |
| **S03** | the CLAIM refuses a post it will not POST | RC-1 | HIGH | — | ❌ | ✅ |
| **S04** | terminal ladder = `f(state, age)` | RC-2 | HIGH | S03 | ❌ | ✅ |
| **S05** | Zernio `404 → unknown` | RC-2 | MED | **S04** | ✅ *only if S04 merged* | ❌ |
| **S06** | revert clears the stale reason | RC-8 | MED | S03 | ✅ | ❌ |
| **S07** | reconcile/publish gate parity | RC-3b | HIGH | S02 | ❌ | ✅ |
| **S08** | `alive` ≠ `succeeding` | RC-6 | MED | — | ✅ | ❌ |
| **S09** | the shrink temp dir gets an owner | RC-10 | LOW | — | ✅ | ❌ |
| **S10** | irreversible retirement | RC-7 | MED | **PD-3** | 🔴 | 🔴 **BLOCKED** |

Per repo policy, a non-MOL PR title carries **`(Unit: <slug>)`**, and the lowercased slug must match the
verifier record filename. CI lane for every slice: **`unit`**. **Never run the suite locally.** Replay both AST
ratchets (`test_swallow_ratchet`, `test_internal_prints_routed`) before pushing.

---

## 4. Every proposed test must **fail before** and **pass after**

The brief requires this. Marked ✅ where the test **must fail on current source** — that is the proof it tests
the defect and not a tautology.

| Test | Fails today? |
|---|---|
| `test_restore_snapshot_serializes_with_transaction` **(rewrite)** | ✅ — **today it passes *because of* the data loss** |
| `test_restore_does_not_orphan_media_unlinks` | ✅ |
| `test_restore_two_process_contention` | ✅ |
| `test_restore_falls_back_on_corrupt_db` | ⚪ passes today — **pins the fallback** |
| `test_backend_normalization_matrix` | ✅ |
| `test_unknown_backend_is_surfaced_not_silent` | ✅ |
| `test_claim_refuses_real_submission_id` | ✅ — *this is EXP-C4-1, encoded* |
| `test_publish_due_tally_surfaces_the_refusal` | ✅ |
| **`test_terminal_ladder_matrix`** *(32 cells)* | ✅ — **today 3 of the 4 axes can veto the terminal** |
| `test_giveup_is_a_label_not_a_state_change` | ⚪ passes today — **pins the double-post safety** |
| `test_reconcile_visits_a_post_carrying_a_transient_reason` | ✅ |
| `test_zernio_404_is_unknown_not_an_error` | ✅ |
| `test_revert_clears_stale_reason` | ✅ |
| `test_reconcile_and_publish_gate_parity` | ✅ |
| `test_daemon_status_distinguishes_alive_from_succeeding` | ✅ |
| `test_shrink_tempdir_is_cleaned` | ✅ |
| `test_no_persisted_media_url_points_into_a_shrink_dir` | ✅ |
| `test_publish_still_works_after_the_shrink_dir_is_removed` | ⚪ passes today — **pins the self-heal** |

> **`RC-5` is why this table exists.** A test that passes today and is *supposed* to is fine. A test that passes
> today **because the defect is present** is a regression lock. Every ⚪ row above was checked for that
> inversion.

---

## 5. Operational gates — a green unit test is **not** a proven migration

| Slice | Operational requirement |
|---|---|
| **S01** | 🔴 **A two-PROCESS contention test, not threads.** SQLite locking is per-inode and cross-process; the experiments used threads and are *indicative, not sufficient.* **Merge gate.** |
| **S02** | 🔴 **This changes which channels are live.** A deployment relying on a malformed value to stay in dryrun **would start publishing**. `go_live`'s past-due-backlog gate is the existing safety net and is untouched. |
| **S04** | 🔴 **THE MOST IMPORTANT NOTE IN THIS AUDIT.** On the first pass after deploy, **every currently-stranded post becomes eligible for escalation at once.** **Ship a report-only mode first** (`fanops reconcile --report-terminals`). *Do not let the first run of the new ladder be the first time anyone learns how many posts it touches.* |
| **S07** | ⚠️ **Pre-flight, and it decides the option:** confirm `reconcile_due` makes **zero network calls** on a dryrun-only deployment. If it does not → take Option B (gate **both**) instead of Option A (gate **neither**). |
| **S09** | ⚪ A one-time clean of the already-leaked tree. |

---

## 6. The three most dangerous scope expansions

Written into the prompts as **forbidden**.

1. 🔴 **S07 must not also remove the `is_live_backend` gate from `_learn_pass`** (`cli.py:965`). That gate
   protects the **irreversible** `retire()`. "Unifying the gating" would run **permanent moment retirement** on a
   not-live-backend deployment. *This is the single most dangerous expansion in the sequence.*

2. 🔴 **S09 must not "defensively" repair the `Render.path` writes.** They are **unreachable** (no `Render` is
   ever minted; `crosspost.py:225` hardcodes `render_id = None`; live ledger: **0 renders, 0/347 posts with a
   `render_id`**). Fixing unreachable code is over-engineering **and** it would conflict with whatever the
   reframe stream decides `Render` should mean.

3. 🔴 **S01 must not touch `Ledger.transaction` or any other writer.** They are **already correct**.
   `restore_snapshot` is the *only* outlier — and the correct pattern sits **six lines above it**
   (`Ledger.snapshot` at [ledger.py:540](src/fanops/ledger.py:540) already takes `store.lock()`).

---

## 7. Product decisions, and what each actually blocks

| ID | Blocks | Verdict |
|---|---|---|
| **PD-1** — does the operator need an explicit "republish" action? | **Nothing.** | ✅ **Ship S03 anyway.** The claim-refusal is safe with or without it, and strictly safer than today. |
| **PD-2** — the terminal for a real-token post that never resolves | S04's *semantics*, not its *shape* | ✅ **Recommendation: the same age ladder.** `GAVE UP:` is a **label** written to `error_reason` — it changes **no state**, so a given-up post is **not re-queueable** and **cannot double-post**. |
| **PD-3** — is irreversible retirement at n=3 intentional? | 🔴 **S10 entirely.** | ❌ **No recommendation.** Not recoverable from code. The guards that exist are considered — evidence *for* intent. **Answer before S02 ships**, or S02 must loudly log the `is_live_backend` transition. |
| **PD-4** — malformed backend at load: normalize-and-skip, or raise? | S02's *edge* | ✅ **Normalize case/whitespace; skip-and-flag an unknown name** via the existing `skipped_rows` channel. This is not new policy — it moves `set_backend`'s existing rule to the door that lacks it. |

---

## 8. What this sequence deliberately does **not** do

- **It does not fix `RC-9`** (the mutation/validator boundary). Zero reachability. Filed, sequenced last.
- **It does not fix `F-C`** (CSRF on 108 routes). A **recorded, accepted** decision. Re-raise as a *product*
  question, not a bug.
- **It does not touch the reframe stream.** Verified disjoint — by state ownership, not by filename.
- **It does not build a `Post` lifecycle state machine** (SLICE-03 Option C). Correct long-term; deferred until
  S03/S04/S06 have settled the semantics, so the state machine would encode a *known-correct* contract rather
  than the current one.
- **It does not manufacture a "unified storage contract"** for Cluster D. That cluster **does not exist** — it
  is four unrelated bugs that are merely all *about files*, and the coupling that would have justified the
  abstraction is **refuted by execution**.
