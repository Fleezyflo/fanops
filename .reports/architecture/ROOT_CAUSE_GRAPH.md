# FanOps — Root-Cause Graph

**Cycle 4 · 2026-07-14 · git HEAD `fcffa73`** · Twin: [`root_causes.json`](root_causes.json)

Every Cycle-1/2/3 finding, reverified against live source and collapsed onto the **ten root causes** that
generate them. Four experiments were executed this cycle; three prior classifications did not survive and are
superseded (see [`CYCLE4_CORRECTIONS.md`](CYCLE4_CORRECTIONS.md)).

**This cycle implemented nothing.** No production source or test was modified. The live ledger was read
**read-only** (`sqlite mode=ro`) to settle one reachability question.

---

## 0. The verdict in one paragraph

The system does not have twenty defects. It has **ten root causes**, and the two that matter most are not the
two the prior cycles ranked highest. **`restore_snapshot` silently deletes real media files** (`RC-4`/`C4-F2a`,
new, proven) — and **a green CI test asserts that data loss is the correct behaviour** (`RC-5`, new, proven).
Below those, one architectural hole dominates everything else: **no component owns the lifecycle of a remote
submission.** `publish` creates `submitting` posts it will never re-drive; `reconcile` refuses to terminate
them. Neither owns them, so they live forever.

---

## 1. The ten roots, ranked by what they actually cost

| # | Root | Class | Sev | Evidence | Collapses |
|---|---|---|---|---|---|
| **RC-4** | `os.replace` of a SQLite file is incompatible with SQLite's inode locking — and `restore_snapshot` takes a lock in **no domain** | reachable defect | **CRITICAL** | EXP-C4-2/3 | `F-B`, `INV-07`, `DEAD-005`, **`C4-F2a`** |
| **RC-5** | **The verification layer encodes the defect** — a green test asserts the data-loss outcome | reachable defect | **CRITICAL** | EXP-C4-2 | *(new)* |
| **RC-2** | reconcile's terminal ladder is conditioned on **poll success** and **token provenance** — neither of which is a property of "is this post stuck?" | reachable defect | **HIGH** | EXP-C4-1 | `C3-F1`, `C3-F2`, `C3-OBS-7` |
| **RC-1** | A decision that **forbids** an action is evaluated **after** the state change that **authorizes** it | reachable defect | **HIGH** | EXP-C4-1 | `C3-F1` (creation) |
| **RC-3** | A backend id is normalized at **one write boundary** and consumed **raw** by five resolvers with four unknown-value behaviours | reachable defect | **HIGH** | FACT | `F-A`, `INV-03`, `COUP-05`, `COUP-16` |
| **RC-3b** | Reconcile is gated on `is_live_backend`; publish is **not** | reachable defect | **HIGH** | FACT | `F-A` (unlabeled variant) |
| **RC-8** | One free-text field carries four machine semantics — the fourth is a **permanent suppression latch** | reachable defect | MEDIUM | EXP-C4-1 | `COUP-03`, **`C4-F1a`**, `C3-F1`'s silence |
| **RC-6** | **`alive` conflates "the process is running" with "the work is succeeding"** | observability gap | MEDIUM | FACT | `C3-F8`, `C3-F9`, `C3-OBS-1` |
| **RC-7** | The one **irreversible** learning actuator has the **weakest** gate | operational hazard | MEDIUM | FACT | `C3-F10` — **blocked on `PD-3`** |
| **RC-10** | A `mkdtemp` with no owner | operational hazard | LOW | EXP-C4-4 | `C3-F4`, **`C4-F5`** |
| **RC-9** | Validators run at **construction only**; every mutation API bypasses them | **latent** | LOW today | FACT | `INV-01`, `INV-01b`, `SC-3` |

---

## 2. The causal graph

```
                    ┌──────────────────────────────────────────────────────────┐
                    │  RC-5  THE TEST ENCODES THE DEFECT                        │
                    │  test_restore_snapshot_serializes_with_transaction        │
                    │  asserts  read_raw() == doc  ("no orphan srcX")           │
                    │  …and it PASSES *because* srcX was destroyed.             │
                    └───────────────────────────┬──────────────────────────────┘
                                                │ PROTECTS (CI green ⇒ "correct")
                                                ▼
   ┌────────────────────────────────────────────────────────────────────────────────┐
   │  RC-4   restore_snapshot: wrong lock domain + os.replace of the db file          │
   │         ledger.py:551 flock(ledger.lock)   ⟂   ledger.py:484 BEGIN IMMEDIATE     │
   │         ── the SIBLING SIX LINES ABOVE (ledger.py:540 snapshot) gets it RIGHT ── │
   └───────┬──────────────────────────────────────────────────┬─────────────────────┘
           │                                                  │
           ▼                                                  ▼
   F-B: writer's commit() SUCCEEDS,                  ★ C4-F2a (NEW, CRITICAL):
   its rows silently discarded                       the txn COMMITS into the orphan ⇒
                                                     it is NOT rolled back ⇒
                                                     _drain_deferred_unlinks() PROCEEDS ⇒
                                                     **os.remove() on real .mp4 files**
                                                     ⇒ rows restored, MEDIA GONE.


   ┌─────────────────────────────── THE SUBMISSION LIFECYCLE HAS NO OWNER ───────────────────────────────┐
   │                                                                                                     │
   │   RC-1  CREATION                                    RC-2  PERMANENCE                                │
   │   run.py:264-272  CLAIM commits `submitting`        reconcile.py:739  the ladder lives on the       │
   │   run.py:287      NETWORK then SKIPS the POST         SUCCESSFUL-POLL branch                        │
   │   run.py:355      FINALIZE persists `submitting`    reconcile.py:746/757  both gate _is_fake_token  │
   │   → returns "submitting" (SUCCESS-SHAPED)           reconcile.py:767  any error_reason ⇒ continue   │
   │                                                                                                     │
   │   ┌── the contradiction ──────────────────────────────────────────────────────────────────────┐    │
   │   │  run.py:270  logs `republish_with_real_id` and **PROCEEDS**  ("repost-freely OK, log it")  │    │
   │   │  run.py:287  refuses to POST                                 ("never double-POST")         │    │
   │   │  Two guards, same predicate, adjacent phases, OPPOSITE intents.                            │    │
   │   └───────────────────────────────────────────────────────────────────────────────────────────┘    │
   │                              │                                        │                             │
   │                              └────────────┬───────────────────────────┘                             │
   │                                           ▼                                                         │
   │                        C3-F1: stranded in `submitting` FOREVER                                      │
   │                        (EXP-C4-1: still `submitting` at +100 000 h)                                 │
   └─────────────────────────────────────────────────┬───────────────────────────────────────────────────┘
                                                     │ made SILENT by
                                                     ▼
                          RC-8  error_reason is a suppression LATCH
                          ★ C4-F1a (NEW): bulk_send_to_review clears scheduled_time,
                            public_url, metrics, published_at — but NOT error_reason.
                            The STALE reason trips reconcile.py:767 on pass ONE ⇒
                            the post never even earns the `stuck …` breadcrumb.


   ┌───────────────────────────────────────────────────────────────────────────────┐
   │  RC-3   no canonical normalize-and-validate at the READ boundary               │
   │  set_backend (accounts.py:412) strips+lowers+rejects … on the WRITE path only  │
   │  Accounts.validate() checks the PAIRING (accounts.py:241), never the VALUE     │
   └───────┬───────────────────────────────────────────────────────────────────────┘
           │  a hand-edited "Postiz" / "postiz " / "blotato" reaches five resolvers
           ▼
   get_poster → DryRunPoster ON A LIVE SYSTEM        ─┐
   get_media_uploader → file:// uploader              │  four different
   _post_provider → passes the raw string through     ├─ unknown-value
   compress.publish_backend_for_post → passes through │  behaviours
   Config.poster_backend → warns, falls back to dryrun ┘
           │
           ▼   live_ready_channels() == []  ⇒  is_live_backend == False
   ┌───────────────────────────────────────────────────────────────────┐
   │  RC-3b   pipeline.py:318 gates RECONCILE on is_live_backend        │
   │          pipeline.py:334 does NOT gate PUBLISH                     │
   │  ⇒ the sole READER of `submitting` is switched off while the       │
   │    WRITER keeps producing it. Permanently stranded, UNLABELED.     │
   └───────────────────────────────────────────────────────────────────┘
```

---

## 3. The four "this can't happen" comments that **are** the defects

This is the single most repeatable pattern in the codebase, and it now has four instances. Each is a
load-bearing assumption written as a statement of fact, at the exact site of a live bug.

| Site | The comment | Reality |
|---|---|---|
| [reconcile.py:76](src/fanops/reconcile.py:76) | *"A post carrying a real id is left to its normal poll (**its status WILL resolve**), never escalated."* | It does not resolve when the platform deleted the post, the integration was removed, or the backend reports `QUEUE` forever. **`C3-F1`.** |
| [providers.py:53-55](src/fanops/post/providers.py:53) | *"**no live account routes to an unknown backend** (all route postiz/zernio), so this path is a defensive default."* | A hand-edit of `accounts.json` — the *documented* operator channel — routes exactly there. **`F-A`.** |
| [test_ledger_sqlite_store.py:183](tests/test_ledger_sqlite_store.py:183) | *"restorer **blocked on flock held by writer**"* | `Ledger.transaction` never takes that flock. Measured: the restorer completed in **0.001 s**, unblocked. **`RC-5`.** |
| [ledger.py:487-488](src/fanops/ledger.py:487) | deferred unlink is *"correct: a rolled-back txn never deletes a file it did not drop"* | True against a *rollback*. In a restore race the txn **commits into an orphan** — so it is never rolled back — so the unlinks **proceed**. **`C4-F2a`.** |

> **Method note for Cycle 5+:** grep the tree for comments containing *"never"*, *"cannot"*, *"WILL"*, or
> *"no … routes to"*. Each is a hypothesis the author did not test. Four for four so far.

---

## 4. Cluster verdicts — two of the brief's six hypotheses are **rejected**

The Cycle-4 brief proposed six clusters and asked whether each is one defect or several. Answering honestly
means rejecting two of them.

### ✅ Cluster A — provider normalization → **TWO roots, not one**
`RC-3` (no canonical normalize-and-validate) and `RC-3b` (the gate asymmetry) are **independent**. `RC-3b` is
reachable with *no* malformed provider at all — a credential-less or provider-less deployment yields
`live_ready_channels() == []` just as well. Fixing `RC-3` shrinks `RC-3b`'s reachability but does not close it.
**Separate slices.**

### ✅ Cluster B — submission lifecycle → **ONE root, two halves**
The authoritative owner is nameable:

> **`reconcile` must own every post in `{submitting, submitted, needs_reconcile}`, and its terminal decision
> must be a pure function of `(state, age)`. `publish` must never leave a post in a state it does not own.**

Today `publish` **creates** `submitting` posts it will never re-drive (`RC-1`) and `reconcile` **refuses** to
terminate them (`RC-2`). Neither owns them.

### ⚠️ Cluster C — mutation bypasses → **the framing is wrong**
The brief implies the seven writers of `queued` are the problem. **They are not.** Every non-`approve_post`
writer is guarded on a *source* state of `failed`/`error`/`submitting`, so **none can leave
`awaiting_approval`**. The safety property **holds**; only the doc's *claimed mechanism* (single-writer) is
false — already correctly filed as doc drift (`INV-02`). The genuine root is `RC-9` (`model_copy` skips every
validator), and it is **latent**: no current path produces the poison row.

### 🔴 Cluster D — filesystem/ledger durability → **THIS CLUSTER DOES NOT EXIST**
The brief asks "which need one unified storage contract?" **None of them.** These are four unrelated bugs that
are merely all *about files*:

| | Root | Ships |
|---|---|---|
| `RC-4` | snapshot/restore inode race | **alone** — CRITICAL |
| `RC-10` | `mkdtemp` leak | **alone** — LOW. *Proven independent by execution.* |
| `C3-F6` | torn `attempts.json` | alone — a sibling-parity gap (its two neighbours **in the same file** are atomic) |
| `C3-F7` | torn `note_stage` | alone — and structurally mitigated (the flock is the authority; only a breadcrumb is lost) |

**And the coupling Cycle 3 asserted is false.** It claimed *"the fix for the leak would BREAK the pointer"*,
implying cleanup and path-relocation must ship atomically. **Refuted by execution** (EXP-C4-4) — see §5.
Manufacturing a "unified storage contract" here would be over-engineering built on a refuted premise.

### ✅ Cluster E — liveness → **ONE root: `RC-6`**
Six distinct facts are collapsed into one word: *process alive · loop ticking · pass started · **pass
succeeded** · **state advanced** · **publishing healthy***. `daemon status` reports the first two and names the
result `alive`, which the operator reads as the fourth.

### ✅ Cluster F — destructive automation → **ONE root: `RC-7`. Blocked on `PD-3`.**
Whether retiring a moment lineage on **3** analyzed posts is intentional policy or oversight **is not
recoverable from the code**. The brief forbids inferring product intent. It is filed, not fixed.

---

## 5. The false coupling, killed

Cycle 3 filed `C3-F5` — *"`Render.path` is durably rewritten INTO a `mkdtemp` directory"* — as **CERTAIN**,
severity MEDIUM, and warned that *"the fix for the leak would BREAK the pointer"*. That would have forced the
cleanup and a path-relocation + migration to ship as one large, risky slice.

**Three findings dissolve it:**

1. **`Render.path` is unreachable.** AST census: `Ledger.add_render` has **zero callers**; `Render(...)` is
   constructed **only** in the two deserializers. [crosspost.py:225](src/fanops/crosspost.py:225) hardcodes
   **`render_id = None`** on every minted Post. **Live ledger (read-only): 0 renders, 0 of 347 posts carry a
   `render_id`.** Every `Render.path` write sits behind an `if r is not None:` guard that is always false.
   *Cycle 3's EXP-10 hand-built a Render row — that proves the mechanism, not the reachability.*

2. **The real pointer is `Post.media_urls`** (`C4-F5`), written by the Studio oversize-retry path
   ([actions.py:1024/1029/1034](src/fanops/studio/actions.py:1024)) **inside a committed transaction**.

3. **And it self-heals.** EXP-C4-4: delete the temp dir → `media_path_for_post`
   ([compress.py:61-65](src/fanops/post/compress.py:61)) falls through to `clip.path` → the next shrink
   re-points `media_urls` at a fresh file. Residual harm: a broken Studio preview and one re-shrink per publish.
   Real, but **LOW** — not a correctness break.

> **The `mkdtemp` cleanup is therefore a safe, independent, ~20-line slice.** No migration. No relocation.
> No atomicity requirement. This is what a root-cause cycle is *for*: it removed a slice rather than adding one.

---

## 6. What collapsed into what

| Prior finding | Root | Status |
|---|---|---|
| `C3-F1` (real sid ⇒ no terminal) | **RC-1** + **RC-2** | CONFIRMED — and **more reachable than recorded** (§7) |
| `C3-F2` (ladder dead on Zernio) | **RC-2** | CONFIRMED — **cause re-attributed** (§7) |
| `F-A` / `INV-03` (malformed ⇒ DryRunPoster) | **RC-3** | CONFIRMED |
| `F-A` (all-channels variant, unlabeled) | **RC-3b** | CONFIRMED |
| `F-B` / `INV-07` / `DEAD-005` | **RC-4** | CONFIRMED — **+ media loss (`C4-F2a`)** |
| `C3-F8`, `C3-F9`, `C3-OBS-1` | **RC-6** | CONFIRMED |
| `C3-F10` | **RC-7** | CONFIRMED — blocked on `PD-3` |
| `COUP-03`, `C3-OBS-2` | **RC-8** | CONFIRMED — **+ `C4-F1a`** |
| `C3-F4` | **RC-10** | CONFIRMED |
| `C3-F5` | — | **SUPERSEDED → unreachable.** Replaced by `C4-F5`. |
| `INV-01`, `INV-01b`, `SC-3` | **RC-9** | CONFIRMED — **latent**, sequenced last |
| `C3-F3`, `C3-F6`, `C3-F7`, `C3-F11`, `C3-F12`, `C3-F13` | *(no shared root)* | Local defects. Deferred; none blocks a slice. |
| `INV-02` ("single writer of `queued`") | — | **Not a defect.** Doc drift. The property holds via `approve_post`'s guard. |
| `C3-NF1`, `C3-NF2` | — | **Non-defects, upheld.** |

---

## 7. Three prior classifications did not survive

Full detail in [`CYCLE4_CORRECTIONS.md`](CYCLE4_CORRECTIONS.md). In brief:

- **`C3-F5` → unreachable.** The Render entity is never minted (§5).
- **`C3-F2`'s cause was mis-attributed.** Cycle 3 blamed *"a sibling-parity divergence between the two status
  clients."* **They are symmetric** — both raise on `>= 300`. The divergence is the **remote endpoint shape**
  (Postiz's unknown id is a row absent from a 200-OK *list*; Zernio's is a **404** on a *per-post* GET). The
  finding stands; the wrong cause implies the wrong (shallow) fix.
- **`C3-F1`'s door was wrong — and the right one is worse.** `_REVIEW_REVERT_BLOCKED`
  ([actions.py:844-847](src/fanops/studio/actions.py:844)) **blocks** `needs_reconcile`. The reachable door is
  **`failed`**: reconcile polls → *the backend reports failed* → `PostState.failed` **with the real
  `submission_id` preserved** → `bulk_send_to_review` → approve → the strand. That is an **ordinary operator
  workflow** — a platform-rejected post, sent back to fix the caption, re-approved. It never publishes again,
  and nothing says so.

---

## 8. Product decisions that block work

| ID | Question | Blocks | Recommendation |
|---|---|---|---|
| **PD-1** | After `RC-1` is fixed, a real-sid post sits visibly in `queued` and never publishes. Does the operator need an explicit **"republish (mint a new submission)"** action? | SLICE-03's *completeness* (not its safety) | **Ship SLICE-03 without it.** Strictly safer than today; decide separately. |
| **PD-2** | What is the correct terminal for a **real-token** post the backend never resolves? | SLICE-04's *semantics* (not its shape) | **The same age ladder as a fake token.** `GAVE UP:` is a *label* written to `error_reason` — it changes no state and therefore **cannot** cause a double-post. |
| **PD-3** | Is irreversible retirement at **n = 3** intentional policy or oversight? | **SLICE-10 entirely** | **None offered.** Not recoverable from code. The guards that exist are considered — which is evidence *for* intent. |
| **PD-4** | On a malformed backend at load: normalize-and-skip, or raise? | SLICE-02's *edge* | **Normalize case/whitespace; skip-and-flag an unknown name** via the existing `skipped_rows` channel. This is not new policy — it moves `set_backend`'s existing rule to the door that lacks it. |

---

## 9. What this cycle did **not** establish

Recorded so no later cycle mistakes silence for coverage.

- **`RC-4`'s fix was verified on a healthy DB and on a corrupt DB, but not under multi-process contention.**
  The experiments used threads in one process. SQLite locking is per-inode and cross-process, so the mechanism
  carries — but a **real two-process test is required before the slice merges** (it is written into the
  SLICE-01 prompt as an acceptance criterion).
- **No proposed fix was implemented, and no test was run.** "A green unit test is not a proven operational
  migration" — the sequence in [`IMPLEMENTATION_SEQUENCE.md`](IMPLEMENTATION_SEQUENCE.md) names the operational
  gate for each slice separately from its CI gate.
- **Uninspected modules:** `variant_*.py`, `digest.py`, `persona_*.py`, `hookscore.py`, `meta_graph.py`, most
  `views_*.py`. **No root cause and no slice depends on them** — they contain no ledger writer, no publish
  path, no lock, and no status client (verified by the AST censuses, which cover the full tree).
