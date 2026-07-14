# FanOps — Remediation Options

**Cycle 4 · 2026-07-14 · git HEAD `fcffa73`** · Twin: [`remediation_options.json`](remediation_options.json)

**Design only. Nothing was implemented. No PR opened.** For each confirmed root cause: the smallest local
correction (**A**), the root-level correction (**B**), a broader simplification where justified (**C**) — and,
explicitly, the shallow fixes that were **rejected and why**.

---

## 0. The intended invariants, stated mechanically

The brief forbids vague wording. Each of these is a statement a test can falsify.

| ID | Root | Invariant | Today |
|---|---|---|---|
| **INT-1** | RC-1 | A post is claimed to `submitting` **only when the very next action is a network POST for that post**. | ❌ |
| **INT-2** | RC-2 | Every post in `{submitting, submitted, needs_reconcile}` has an autonomous transition to a terminal or retryable state in bounded time — **regardless of poll outcome, token provenance, or `error_reason`**. | ❌ |
| **INT-3** | RC-3 | A live account with an unsupported provider value **must fail before a post is claimed**. | ❌ |
| **INT-4** | RC-3b | The producer of a state and its **sole consumer** are gated on the **same predicate**. | ❌ |
| **INT-5** | RC-4 | Replacing the ledger's contents is **mutually exclusive with every active writer**, and never orphans an inode a writer holds open. | ❌ |
| **INT-6** | RC-6 | A verdict named `alive` **must not imply successful work** unless a successful-pass timestamp is independently reported. | ❌ |
| **INT-7** | RC-8 | No recovery action may leave a post carrying a status string describing a state it is **no longer in**; and **no status string may suppress the system's own attention**. | ❌ |
| **INT-8** | RC-10 | **No persisted path may point into storage whose lifetime is shorter than the ledger record.** | ❌ (self-healing) |
| **INT-9** | RC-9 | A validator that raises at construction must raise on **every** mutation API. | ❌ (latent) |

**INT-2 is testable as a 32-cell matrix**: `backend {postiz, zernio}` × `poll {published, failed, unknown,
RAISES}` × `token {fake, real}` × `reason {empty, stale}`. Today **three of those four axes can veto the
terminal.** That matrix is the acceptance criterion for SLICE-04.

---

## 1. `RC-4` + `RC-5` — the snapshot race · **SLICE-01** · CRITICAL

### ❌ Option A — swap the lock (`_file_lock` → `store.lock()`) · **REJECTED, INSUFFICIENT**

One line. `store.restore()` already supports it ([ledger_sqlite.py:131](src/fanops/ledger_sqlite.py:131),
`relock = self._conn is not None`). **And it does not work.**

> SQLite's write lock is held on the **inode**, via an open fd. A concurrent writer that opened the db *before*
> the replace **still holds that inode**. **No lock acquired before an `os.replace` can protect it.**

Taking the right lock narrows the window; it does not close it. This is precisely the brief's *"adding another
lock that does not exclude SQLite writers."*

### ✅ Option B — **do not replace the file. Write the snapshot's contents in place.** · **RECOMMENDED**

```python
# ledger_sqlite.py — extract, ~8 lines, zero behaviour change
def read_raw_from(path: Path) -> dict | None: ...      # the body of read_raw, parametrized on path
def read_raw(self): return read_raw_from(self.db_path)

# ledger.py:546 — restore_snapshot
snap_doc = read_raw_from(src)
with store.lock():              # ← BEGIN IMMEDIATE: the SAME domain every writer uses
    store.write_raw(snap_doc)   # ← in-place DELETE+INSERT. SAME INODE.
# corrupt-db fallback: only when store.lock() cannot open the db, use the old os.replace route
```

**Nothing is invented.** `write_raw` ([ledger_sqlite.py:59](src/fanops/ledger_sqlite.py:59)) *already* performs
a full-document `DELETE`+`INSERT` and, when `self._conn` is set, **reuses the caller's locked connection without
committing** — exactly the semantics required. And the correct pattern is already in the same class:
**`Ledger.snapshot` ([ledger.py:540](src/fanops/ledger.py:540)) uses `with store.lock():`.** `restore_snapshot`,
**six lines later**, does not.

**Verified by execution (EXP-C4-3):**

| | Result |
|---|---|
| Restorer blocked on `BEGIN IMMEDIATE` until the writer committed? | ✅ **0.469 s** |
| Writer's commit visible (not orphaned)? | ✅ |
| In-place path on a **corrupt** db? | ❌ `DatabaseError: file is not a database` |
| Today's `os.replace` path on a corrupt db? | ✅ succeeds |

→ **the corrupt-db fallback is genuinely required.** The fix is **two-path**, and the fallback is honest: it is
reachable only when the alternative is impossible.

**Side effect:** `ledger.lock` (`LCK-008` / `DEAD-005`) loses its sole consumer and becomes **genuinely dead for
the first time** — it may then be deleted honestly, three cycles after being wrongly filed as dead.

**What it does *not* fix:** `COUP-01`'s first consequence (publish reads `accounts.json` outside the ledger
lock — already handled by "in-flight wins"); the wipe's `MOL-71` gap; anything in the submission lifecycle.

### ❌ Option C — a unified ledger storage contract · **REJECTED, NOT JUSTIFIED**
Every other writer **already** goes through `store.lock()` + `write_raw`. `restore_snapshot` is the **only**
outlier. There is no contract to unify — there is one method to bring **into** the existing contract.

### 🔴 And the test must be rewritten **in the same PR**

`test_restore_snapshot_serializes_with_transaction` **asserts the data loss** (`RC-5`). Any correct fix breaks
it. **A maintainer seeing it go red will read the fix as the regression.** The rewrite is not optional cleanup —
it is part of the fix.

**Rejected shallow fixes:** an flock inside `Ledger.transaction` (serializes every write behind a second lock
for one break-glass path, and *still* doesn't stop the inode orphaning) · documenting *"don't restore while the
daemon runs"* (this **is** the documented procedure — [ledger_wipe.py:246](src/fanops/ledger_wipe.py:246)) · a
PID check (not mutual exclusion; races; blind to a Studio writer).

---

## 2. `RC-3` — provider normalization · **SLICE-02** · HIGH

### ⚠️ Option A — harden `get_poster`'s guard to membership
Closes the publish door. **Leaves four siblings divergent** — `get_media_uploader` still silently returns the
dryrun `file://` uploader. This is the brief's *"tightening only one provider resolver while leaving sibling
resolvers divergent."* **Insufficient alone.**

### ✅ Option B — **normalize + validate at `Accounts.load`, so a malformed value cannot exist in memory** · **RECOMMENDED**

Extract `normalize_backend(s)` — **the exact rule `set_backend` already applies** (`.strip().lower()`; `None` if
not in `_VALID_BACKENDS`) — and apply it at the **read** boundary. An unknown name drops the channel's backend
and lands in `skipped_rows` — the **existing** `MOL-79` channel ([accounts.py:141-145](src/fanops/accounts.py:141)),
which `validate()` **already** promotes to a visible doctor/health problem.

**This is not new policy. It moves an existing rule to the door that lacks it.**

> All five divergent resolvers consume the **loaded `Accounts` object**. Fixing the load boundary fixes all five
> **without touching five files.** `get_poster`'s guard is then hardened anyway, as defence in depth — and
> becomes unreachable, which is the point.

**Behavioural change:** a hand-edited `"Postiz"` now **normalizes** and the channel publishes correctly (today it
silently does not). A hand-edited `"blotato"` now **skips the channel and raises a visible problem** (today it
silently dry-runs). **`_VALID_BACKENDS` collapses to one home** (`COUP-05` reducible); `COUP-16` becomes harmless.

**Risk:** MEDIUM. A deployment currently *relying* on a malformed value to stay in dryrun would start
publishing. The `go_live` past-due-backlog gate (the existing anti-machine-gun guard) is untouched.

### ❌ Option C — make `Account.backends` a typed enum · **REJECTED**
pydantic would then **refuse to load** a registry containing a legacy value — converting a soft skip into a hard
`ControlFileError`. That is the exact failure mode `SHIM-005` (forward-compat `extra="ignore"`) exists to
prevent, and it breaks the `MOL-79` per-row leniency posture.

---

## 3. `RC-1` — the claim that forbids itself · **SLICE-03** · HIGH

### ⚠️ Option A — un-claim after the skip
Mirrors `_unclaim_no_integration` ([run.py:232-239](src/fanops/post/run.py:232)), which **already does exactly
this** for a different precondition. Acceptable, but it writes `submitting` and then writes it back — a spurious
state flap visible to any concurrent reader, plus two extra transactions. **The precondition is knowable before
the claim.**

### ✅ Option B — **move the predicate into the CLAIM; refusing is then a clean no-op** · **RECOMMENDED**

```python
# run.py CLAIM (:264-272)
if is_real_submission_id(post.submission_id):
    log("skip_resubmit_existing_id"); return None      # txn exits with NO mutation — stays `queued`
post.state = PostState.submitting
```
…and **delete** the now-dead skip at `:287-288` **and** the contradictory `republish_with_real_id` log at
`:270-271`. They are **two guards on one predicate with opposite intents**, 17 lines apart.

**The file already contains the correct model.** `_missing_integration_id` is checked **before** the claim
([run.py:256-262](src/fanops/post/run.py:256)) and returns `None`. `is_real_submission_id` is checked **after**.
Same function, two preconditions, two different phases. This slice makes them consistent — it brings one
predicate into an established pattern rather than inventing one.

**Also:** `publish_due` must **count the refusal** into its tally. Today `_publish_one` returns the
success-shaped string `"submitting"` and the post **vanishes from the tally entirely**.

**Behavioural change:** a real-sid post in `queued` now **stays `queued`** — visible, re-driveable, counted —
instead of being stranded in `submitting`: invisible, un-re-driveable, silent.

**What it does *not* fix:** posts **already** stranded (SLICE-04) · the crash-during-network door into
`submitting`, which is **legitimate and must remain** (F11) · **PD-1**.

### 🔵 Option C — a declarative `Post` lifecycle state machine · **DEFER**
Correct long-term; would structurally prevent this whole class. But it touches all 21 `PostState` writers and
every Studio recovery route at once — a very large blast radius for something Option B closes in ~15 lines.
**Revisit after 03/04/06 settle the semantics**, so the state machine encodes a *known-correct* contract rather
than the current one.

**Rejected shallow fix:** changing `_publish_one`'s return value so the tally is honest → fixes the **report**,
not the **strand**.

---

## 4. `RC-2` — the terminal ladder · **SLICE-04** · HIGH · *(pending PD-2)*

### ⚠️ Option A — drop `_is_fake_token` from the two terminals
Closes `C3-F1` **on Postiz only**. A raising poll still `continue`s at `:635` and never reaches the ladder
(`C3-F2` survives), and the `error_reason` latch at `:767` survives. **Two of three exclusions remain.** This is
the brief's *"adding another special case for a real submission token without defining submission lifecycle
ownership."*

### ✅ Option B — **hoist the terminal decision out of the poll-outcome branch** · **RECOMMENDED**

```python
_apply_age_terminal(post, now) -> Post | None     # a PURE function of (state, age)
```
- Owns **XC-1** (`submitting` + age > 24 h → `needs_reconcile`) and **XC-2** (`needs_reconcile` + age > 72 h →
  `GAVE UP:`).
- Called on **every** reconcilable post, on **every** pass, **before** the `try`/poll — so a raising poll can no
  longer bypass it.
- The poll then only **advances** a post (`published`/`failed`). A poll error still stamps a reason and
  continues — but it can no longer **prevent the terminal**.
- **`:767`'s suppression key changes** from *"any non-empty `error_reason`"* to the **explicit** terminal marker
  `_is_giveup(post)`. A post with a *transient* reason must still be **visited** — that is what makes the strand
  silent (`RC-8`).

> The correct predicate for "give up on this post" is **`(state, age)`**. Everything else the current code
> conditions on — this pass's poll outcome, the token's provenance, whether any string was ever written to a
> free-text field — is **incidental**.

**Safety (and this is the load-bearing point):** `GAVE UP:` is a **label** written to `error_reason`. It changes
**no state**. A given-up post is **not re-queueable**, so **no double-post is possible.** That is why PD-2's
recommendation (same ladder for a real token) is safe.

**Subsumes** SLICE-05 for the strand. SLICE-05 remains independently correct and should still ship — but it is
no longer load-bearing.

### 🔴 Operational migration — **the single most important note in this audit**

> On first run after deploy, **every currently-stranded post becomes eligible for escalation at once.** On the
> live ledger that is bounded (347 posts) — but the operator **must** be warned. **Ship a report-only mode
> first** (`fanops reconcile --report-terminals`) so the blast radius is *seen* before it is *written*.

**Rollback caveat:** reverting does **not** un-escalate posts the new ladder already labeled. That is
acceptable (the labels are true) but must be stated.

**Rejected shallow fixes:** raising `_SUBMITTING_ESCALATE_AFTER` / `_RECONCILE_GIVEUP_AFTER` → the brief names
this exactly; *the escalation is not late, it is unreachable* · making the Studio requeue family accept
`submitting` → hands the operator a button to re-drive a post whose **remote fate is unknown**. **That is a
double-post vector.** Terminate (label) first, then recover.

---

## 5. `RC-2` sibling — status-client parity · **SLICE-05**

`ZernioStatusClient.get_status` should return `{"status": "unknown"}` on a **404**, before the generic `>= 300`
raise — matching `PostizStatusClient`'s semantics for the **same condition** (an id the backend does not know).

**⚠️ MUST NOT SHIP ALONE.** Alone, it fixes the fake-token Zernio case and leaves **both** the real-token case
and the 500-forever case unterminated — the exact shallow fix the brief forbids. **Ships with or after SLICE-04.**

**Corrects the record:** Cycle 3 blamed a FanOps-side sibling divergence. **Both clients raise on `>= 300`
identically** ([metrics.py:164-165](src/fanops/post/metrics.py:164) vs
[:516-517](src/fanops/post/metrics.py:516)). The divergence is the **remote endpoint shape**. Absorbing a remote
API difference at the FanOps boundary is exactly where this fix belongs.

---

## 6. `RC-8` / `C4-F1a` — the stale-reason latch · **SLICE-06**

**One line.** `bulk_send_to_review` ([actions.py:944-951](src/fanops/studio/actions.py:944)) already clears
`scheduled_time`, `public_url`, `metrics`, `published_at`. **`error_reason` is the one omission — and it is the
one that latches.**

EXP-C4-1 proved a reverted post reaches `submitting` still carrying *"reconciled: poster reports failed (IG
rejected…)"* — a string that **(a)** lies about its state and **(b)** trips the `:767` latch on the **first**
pass, so the post never even earns the `stuck` breadcrumb Cycle 3 assumed it would get.

`submission_id` / `batch_id` stay preserved ("keep the lineage"). That is **PD-1**'s territory — and SLICE-03
makes preserving them **safe**.

The reconcile-side half (`:767`) is **owned by SLICE-04** — same file, same branch. **Do not duplicate it here.**

---

## 7. `RC-3b` — gate parity · **SLICE-07**

### ✅ Option A — **remove the gate from `_reconcile_safe`** · **RECOMMENDED**
`reconcile_due` is **already per-post safe** — its own docstring
([pipeline.py:315-316](src/fanops/pipeline.py:315)) says it *"resolves each post's provider via
`effective_provider` and skips dryrun/provider-less posts."* The gate is redundant defence that became an active
hazard: **it switches off the sole reader of a state the writer keeps producing.**

**Pre-flight requirement:** confirm `reconcile_due` makes **zero network calls** on a dryrun-only deployment.
That is the acceptance criterion. If it does not, prefer Option B.

### 🔵 Option B — gate **both** on `is_live_backend`
Strictly safer, but it also stops **dryrun previews** from being written on a not-live system — a real feature
regression. Prefer A unless the pre-flight fails.

**Rejected shallow fix:** another doctor warning. The half-live banner **already exists** and is **suppressed
whenever one valid channel remains** (`C3-OBS-5`). A second warning on the same broken predicate adds noise, not
safety.

---

## 8. `RC-6` — `alive` ≠ `succeeding` · **SLICE-08**

The data **already exists**: `heartbeat_age_s` is in the returned dict, and the heartbeat
([cli.py:1306](src/fanops/cli.py:1306)) fires **only** when a pass succeeds — **it already *is* the success
signal.** It is simply not *read* as one. The verdict discards it.

- `health_model.py`: add `last_success_age_s` alongside `heartbeat_age_s`.
- `daemon.py status`: **keep `alive` keyed on `daemon_progress`** — do **not** touch the mid-pass liveness owner
  (project memory `liveness-verdict-single-owner`). **Add** a second, orthogonal verdict:
  `alive (no successful pass in {N}m)`.

**This slice adds a signal. It moves none.**

**Rejected shallow fix:** rewording the verdict. The brief names it: *"changing `daemon status` wording without
separating progress signals."* One signal is answering two questions.

---

## 9. `RC-10` / `C4-F5` — the temp dir gets an owner · **SLICE-09**

The code that **creates** the dir should own its lifetime. Clean up after the upload; add a doctor check and a
`fanops clean --shrink` verb for the already-leaked tree.

> **Proven safe (EXP-C4-4).** Deleting the temp dir causes `media_path_for_post`
> ([compress.py:61-65](src/fanops/post/compress.py:61)) to fall through to `clip.path`, and the next shrink
> re-points `media_urls` at a fresh file. **Cycle 3's claim that "the fix for the leak would BREAK the pointer"
> is refuted by execution.** This slice needs **no** migration, **no** relocation, **no** atomicity requirement.

**Rejected shallow fix:** a cron/tmpwatch janitor. It is now *safe* — but it is the wrong **home**.

---

## 10. `RC-7` — irreversible retirement · **SLICE-10** · 🔴 **BLOCKED ON PD-3 — DO NOT EXECUTE**

Three legitimate options; **the code cannot tell us which is right**, and the brief forbids inferring product
intent.

| | Option | Note |
|---|---|---|
| **A** | Gate `retire` behind `p4_unlocked`, like every reversible actuator | Removes the **aggression** |
| **B** | Make retirement **reversible** (an operator un-retire verb) | Removes the **asymmetry**, keeps the policy — *arguably the better framing* |
| **C** | Leave it; **document it** | If intentional, the defect is that `INV-14` creates a **false impression** by being true only *as scoped* |

**No recommendation is offered.** The guards that exist (bottom-20 % ∧ `lift < 20.0` ∧ not-a-winner ∧ not
`lift_degraded`) are real and considered — which is evidence **for** intent. **PD-3 must be answered first.**

---

## 11. Deferred, with reasons

| Finding | Why deferred |
|---|---|
| `C3-F6` (torn `attempts.json`) | LOW. 3-line fix — use `tmp`+`os.replace` like its **two siblings in the same file**. Batch or ship standalone. |
| `C3-F7` (torn `note_stage`) | LOW **and structurally mitigated** — the flock is the authority and is kernel-released on death. Only a breadcrumb is lost. |
| `C3-F3` (log-free swallow) | Blast radius **nil** today (return value discarded). One log line; batch into SLICE-03 (same file). |
| `C3-F11` (upload cache never invalidated) | Needs a product call on URL lifetime. |
| `C3-F12` (partial face list) | Deliberate, fail-open — **and it lives in the reframe stream's files. Do not touch it from a Cycle-4 slice.** |
| `C3-F13` (lost audit line) | **Contract-correct.** Not a defect. |
| **`RC-9`** (mutation/validator boundary) | **LATENT — zero current reachability.** Sequenced last *on purpose*: fixing it first would spend the audit's credibility on a bug nobody can hit while `RC-4` silently deletes media. |
| `F-C` (CSRF ×108) | A **recorded, accepted** decision. Re-raise as a product question, not a bug. |
