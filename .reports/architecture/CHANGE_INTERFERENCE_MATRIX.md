# FanOps — Change Interference Matrix

**Cycle 4 · 2026-07-14 · git HEAD `fcffa73`** · Twin: [`change_interference.json`](change_interference.json)

Pairwise classification of the ten proposed slices — **by file overlap *and* state ownership**. Per the brief,
**no pair is declared independent on the strength of differing filenames alone.**

---

## 1. The slices

| | Slice | Root | Owns (state / gate) |
|---|---|---|---|
| **S01** | `restore_snapshot` in-place under the ledger lock | RC-4 + RC-5 | the ledger **write path**; `ledger.lock` |
| **S02** | canonical backend normalization at the read boundary | RC-3 | `Account.backends` (the **value**); feeds `live_ready_channels()` |
| **S03** | the publish **CLAIM** refuses a post it will not POST | RC-1 | the `queued → submitting` transition |
| **S04** | reconcile terminal ladder = `f(state, age)` | RC-2 + RC-8 | `submitting → needs_reconcile`; the `GAVE UP:` label; the `:767` suppression key |
| **S05** | Zernio `404 → {"status":"unknown"}` | RC-2 (sibling) | the `get_status` return shape |
| **S06** | `bulk_send_to_review` clears `error_reason` | RC-8 | `Post.error_reason` on the revert path |
| **S07** | reconcile/publish gate parity | RC-3b | **`cfg.is_live_backend`** (the `_reconcile_safe` gate) |
| **S08** | daemon health: `alive` ≠ `succeeding` | RC-6 | the status verdict; `last_success_age_s` |
| **S09** | the shrink temp dir gets an owner | RC-10 + C4-F5 | `Post.media_urls` on the shrink path; the temp-dir lifetime |
| **S10** | irreversible retirement policy | RC-7 | `MomentState.retired` — 🔴 **BLOCKED ON PD-3** |

---

## 2. The matrix

`·` independent  ·  `→` ordering dependency  ·  `≈` same-file conflict only  ·  `⊃` supersedes
`⚠` semantic conflict  ·  `⛓` must coordinate  ·  `✕` unsafe concurrent

|  | S01 | S02 | S03 | S04 | S05 | S06 | S07 | S08 | S09 | S10 |
|---|---|---|---|---|---|---|---|---|---|---|
| **S01** | — | · | · | · | · | · | · | · | · | · |
| **S02** | · | — | · | · | · | · | **→** | · | · | **⚠** |
| **S03** | · | · | — | **→ ✕** | · | **→** | · | ≈ | · | · |
| **S04** | · | · | **→ ✕** | — | **⊃** | **⛓** | **→** | · | · | · |
| **S05** | · | · | · | **⊃** | — | · | · | · | · | · |
| **S06** | · | · | **→** | **⛓** | · | — | · | · | **≈** | · |
| **S07** | · | **→** | · | **→** | · | · | — | · | · | **⚠** |
| **S08** | · | · | ≈ | · | · | · | · | — | ≈ | ≈ |
| **S09** | · | · | · | · | · | **≈** | · | ≈ | — | · |
| **S10** | · | **⚠** | · | · | · | · | **⚠** | ≈ | · | — |

---

## 3. The pairs that matter

### 🔴 `S03 ✕ S04` — **unsafe concurrent, and it is not a code conflict**

Files are disjoint (`run.py` vs `reconcile.py`). **The conflict is a *contract* conflict:** both slices define
what `submitting` **means**. Two authors working them in parallel will produce two lifecycles.

**Complementary, not competing.** S03 stops the strand being **created** (via the publish door). S04 drains posts
**already** stranded — by **any** door, including the *legitimate* crash-during-network door, which must remain
(F11). **Neither alone achieves `INT-2`.**

**Order: S03 first.** If S04 lands first while publish keeps re-creating strands, the new ladder escalates a churn
of posts publish immediately re-strands. Noisy, not dangerous — but avoidable. *Stop the bleeding, then drain.*

### 🔴 `S04 ⊃ S05` — **S05 must not ship alone**

Once S04 hoists the terminal out of the poll-outcome branch, a raising Zernio poll no longer prevents the
terminal — so **S05 is no longer load-bearing.** It remains *independently correct* (a 404 semantically **is**
`unknown`, not an error) and should still ship.

> **Shipping S05 alone is the brief's explicitly-forbidden shallow fix** — *"adding another special case for a
> real submission token without defining submission lifecycle ownership."* It would fix the fake-token Zernio
> case and leave **both** the real-token case **and** the 500-forever case unterminated.

### 🔴 `S02 ⚠ S10` — **the non-obvious one, and it is the strongest argument for answering PD-3 first**

Both read `cfg.is_live_backend`. S02 changes **what feeds it**; S10's `_learn_pass` is **gated on it**.

> **If S02 normalizes a typo'd backend, a previously-dark channel goes live → `is_live_backend` flips `True` →
> `_learn_pass` starts running — including the IRREVERSIBLE `retire()` — on a deployment where it previously did
> not.**

Fixing a typo could silently begin permanently retiring moment lineages. **Mitigation if PD-3 is unanswered:**
S02 **must** log the `is_live_backend` transition loudly, and the operator must be told that fixing a malformed
backend can **unfreeze the learning pass**.

### 🟡 `S02 → S07` — ordering, with a real test cost

Shared gate. S02 normalizes malformed backends → `live_ready_channels()` stops returning `[]` for a typo →
`is_live_backend` stops being *spuriously* `False` → **S07's premise becomes much harder to reach.**

S02 therefore **shrinks S07's blast radius**. Landing S07 first isn't *wrong*, but its fixtures would encode a
malformed-backend world that S02 then eliminates, forcing a rewrite.

**S07 is still required after S02:** a **credential-less or provider-less** deployment yields
`live_ready_channels() == []` with **no malformed value at all**.

### 🟡 `S04 → S07` — S07 is an **enabler** for S04's guarantee

If `_reconcile_safe` is gated **off**, **S04's ladder never runs.** `INT-2` holds only once **both** land.
Neither breaks the other; S04's ladder is correct but *unreachable* in the all-channels-malformed deployment
until S07 lands.

### 🟡 `S04 ⛓ S06` — two halves of RC-8, in different files

S04 owns `reconcile.py:767` (change the suppression key from *any* `error_reason` to the **explicit**
`_is_giveup` marker). S06 owns `actions.py:944-951` (clear the stale reason on revert). **No file conflict** —
but `INT-7` holds only when **both** land. **Do not duplicate the `:767` fix into S06.**

### 🟡 `S03 → S06` — S06 is safe *because* S03 exists

S06 keeps preserving `submission_id` ("keep the lineage"). **That is safe only because S03 makes the claim refuse
a real-sid post.** S06 without S03 is not *harmful* — merely incomplete: the post still reverts with a real sid,
gets approved, and strands. **S06 is hygiene; S03 is safety.**

### 🟡 `S07 ⚠ S10` — a forbidden scope expansion, written into the prompt

S10's `_learn_pass` is gated on `cfg.is_live_backend` — **the same gate S07 removes from `_reconcile_safe`.**
S07 does **not** touch the learn gate. But an engineer "unifying the gating" could remove **both**, which would
run the **irreversible** `retire()` on a not-live-backend deployment.

**This is written into S07's prompt as a forbidden scope expansion.**

### ⚪ Same-file conflicts only (rebase resolves)

`S06 ≈ S09` (both in `studio/actions.py`, ~80 lines apart, different functions) · `S08 ≈ S09 ≈ S10 ≈ S03` (all
could plausibly touch `cli.py`; **assign the `cli.py` success-signal to S08 exclusively**).

---

## 4. Must ship **atomically**

| Unit | Why |
|---|---|
| **S01 source fix + S01 test rewrite** | 🔴 **NON-NEGOTIABLE (`RC-5`).** `test_restore_snapshot_serializes_with_transaction` currently **asserts the data-loss outcome**. Any correct fix turns it **red**. A maintainer seeing a green test go red will read **the fix** as the regression. **The test rewrite *is* part of the fix.** |
| **S04's `reconcile.py:739-777` rewrite, *including* the `:767` suppression-key change** | The latch lives **inside** the branch S04 rewrites. Splitting it leaves the ladder reachable but its outcome still suppressed. |

---

## 5. The reframe stream — **zero overlap, verified by state ownership**

The brief forbids concluding "parallel-safe" from filenames. Here is the behavioural check.

**Current status:** the reframe stream's most recent commit **is HEAD** (`fcffa73`, *"fix(framing): cv2
required"*). **Nothing is in flight** — no unmerged reframe branch exists. There is no concurrent-edit hazard
today.

| Reframe file | Ledger state owned |
|---|---|
| `framing.py` | **ZERO** references to `Ledger` or `led.` (`grep -c` → 0). Pure detection. |
| `keyframes.py` | **ZERO** references to `Ledger` or `led.` (`grep -c` → 0). A content-addressed cache. |
| `clip.py` | Its **only three** occurrences of the token `Render` are **docstrings** (lines 1, 503, 904). It **never** constructs, reads, or writes a `Render` row, and **never** touches `led.renders`. |

**File intersection with the Cycle-4 footprint: EMPTY.**
**State intersection: EMPTY.**

> The reframe stream and every Cycle-4 slice are **disjoint in state ownership**, not merely in filenames.
> **Cycle-4 implementation may proceed in parallel with reframe work.**

### ⚠️ `IF-1` — the one forward-looking hazard

> **If a future reframe slice starts *minting* `Render` rows** — i.e. gives `Ledger.add_render` its first caller,
> or stops [crosspost.py:225](src/fanops/crosspost.py:225) hardcoding `render_id = None` — **it would reactivate
> the currently-unreachable `C3-F5`.** The `Render.path` writes at
> [compress.py:112](src/fanops/post/compress.py:112), [compress.py:131](src/fanops/post/compress.py:131) and
> [run.py:367](src/fanops/post/run.py:367) are guarded **only** by `if r is not None:` / `if post.render_id:`.
> **They are dormant because nothing mints a Render — not because they are safe.**

**This belongs on the reframe stream's risk list, not in a Cycle-4 slice.** A slice that "defensively" repaired
unreachable code would be over-engineering, and it would conflict with whatever the reframe stream decides
`Render` should mean.

**Cheapest possible guard, costing the reframe stream nothing:** S09's regression test
(`test_no_persisted_media_url_points_into_a_shrink_dir`) is written so it would **also** catch a `Render.path`
regression if Renders are ever minted.

---

## 6. Verdicts

### ✅ Safe to **build in parallel**
**S01 · S02 · S08 · S09** — disjoint files, disjoint state, no shared gate, no ordering dependency. S01 and S02
in particular touch entirely separate subsystems (the ledger write path vs the accounts control file).

### 🔴 Must be **sequential**
**S03 → S04 → S05.** All three concentrate on one architectural question — *submission lifecycle ownership* —
and splitting them across concurrent authors would fragment the contract they are jointly defining.

### 🟡 May be **designed** concurrently, **not implemented** concurrently
**S02 + S07.** Shared gate (`is_live_backend`). Designing both at once is desirable; implementing concurrently
risks two authors encoding contradictory assumptions about what `is_live_backend` means.

### 🔴 Blocked on product decisions
- **S10 → PD-3.** **Do not execute.**
- **S04's exact semantics → PD-2.** The *shape* is decided; the terminal for a real token needs the call.
- **S03 → PD-1: ship anyway.** The claim-refusal is safe with or without a republish action.
- **S02 → PD-4:** nearly not a decision — it moves `set_backend`'s existing rule to the read boundary.
