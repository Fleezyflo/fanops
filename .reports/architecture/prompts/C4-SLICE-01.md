# C4-SLICE-01 — `restore_snapshot` writes the snapshot IN PLACE, under the ledger's own lock

**Root cause:** `RC-4` + `RC-5` · **Severity: CRITICAL** · **Prerequisites: none** · **Ships first**
**PR title must carry:** `(Unit: restore-in-place-under-ledger-lock)`

---

## 0. Before you edit anything

**Reverify every line cited below against current source.** Cycle-2's method note is binding: *every prior
cycle's line references have rotted at least once.* If a citation does not resolve, **stop and re-derive it** —
do not guess.

**Then state, in your own words, the root cause.** If your statement is *"restore_snapshot takes the wrong
lock,"* **you have not understood the defect and you will ship an insufficient fix.** Read §2.

---

## 1. What is broken

`Ledger.transaction` ([ledger.py:484](src/fanops/ledger.py:484)) serializes every ledger writer on
`store.lock()` — SQLite **`BEGIN IMMEDIATE`**.

`Ledger.restore_snapshot` ([ledger.py:551](src/fanops/ledger.py:551)) serializes on
`_file_lock(cfg.lock_path)` — an **`fcntl.flock` on a different file**.

**The two primitives are mutually invisible.** `restore` then `os.replace()`s the database file
([ledger_sqlite.py:151](src/fanops/ledger_sqlite.py:151)), **orphaning the inode a live writer holds open.**

### Two consequences, both proven by execution

**(a) The writer's `commit()` succeeds and its data is silently discarded.**
Measured: the restorer was **never blocked** — it completed in **0.001 s**.

**(b) 🔴 It deletes real media files.** `Ledger.transaction` runs:

```python
led._save_unlocked()            # ← commits into the ORPHANED inode. Succeeds. No exception.
led._drain_deferred_unlinks()   # ← os.remove() on real .mp4 paths (ledger.py:518-525). PROCEEDS.
```

The deferred-unlink design is documented as *"correct: a rolled-back txn never deletes a file it did not
drop."* **True against a rollback.** In a restore race the txn is **not** rolled back — it commits into an
orphan — **so the unlinks run.**

Measured: **ledger rows restored from the snapshot; the `.mp4` gone from disk.** Silent, unrecoverable, and
reached by following the **documented** wipe-rollback procedure
([ledger_wipe.py:246](src/fanops/ledger_wipe.py:246)) while the daemon is running.

---

## 2. 🔴 The fix you will be tempted to ship, and why it is **wrong**

> **Tempting:** `ledger.py:551` — change `with _file_lock(cfg.lock_path):` to `with store.lock():`.
> One line. And `store.restore()` **already supports** it
> ([ledger_sqlite.py:131](src/fanops/ledger_sqlite.py:131): `relock = self._conn is not None`).

**It does not work.**

> **SQLite's write lock is held on the *inode*, via an open file descriptor. A concurrent writer that opened
> the db *before* the `os.replace` still holds that inode. NO lock acquired before an `os.replace` can protect
> it.**

Taking the right lock **narrows the window; it does not close it.** This is exactly the shallow fix the audit
brief names: *"adding another lock that does not exclude SQLite writers."*

**If you ship Option A, you will have made the race rarer and left it in the codebase.**

---

## 3. The root fix — **do not replace the file**

Write the snapshot's **contents** into the live database, **in place**, inside the **same `BEGIN IMMEDIATE`
transaction every other writer uses.** Same inode ⇒ no orphan ⇒ no lost commit ⇒ no stray unlink.

**Nothing is invented.** `SqliteLedgerStore.write_raw` ([ledger_sqlite.py:59](src/fanops/ledger_sqlite.py:59))
**already** performs a full-document `DELETE` + `INSERT`, and when `self._conn` is already set (`own = False`)
it **reuses the caller's locked connection without committing** — exactly the semantics required.

```python
# ledger_sqlite.py — extract. ~8 lines. ZERO behaviour change.
def read_raw_from(path: Path) -> dict | None:  ...   # the body of read_raw, parametrized on path
def read_raw(self):  return read_raw_from(self.db_path)

# ledger.py:546 — restore_snapshot
snap_doc = read_raw_from(src)
if snap_doc is None:
    raise ControlFileError(...)
with store.lock():                  # ← BEGIN IMMEDIATE: THE SAME DOMAIN EVERY WRITER USES
    store.write_raw(snap_doc)       # ← in-place DELETE+INSERT. SAME INODE.
_snapshot_restore_control_files(cfg, src)
```

### The corrupt-db fallback is **required** — do not omit it

`store.restore()`'s contract says it *"must work even when live db is corrupt"* — and that is **not decoration.**
**Measured:** the in-place path raises `DatabaseError: file is not a database` on a corrupt live db, while
today's `os.replace` path **succeeds**.

**So the fix is two-path:**

| Live DB | Route |
|---|---|
| **openable** (the case that actually bites — the documented rollback with the daemon running) | **in-place under `store.lock()`** — fully serialized |
| **corrupt / unopenable** (`store.lock()` cannot open it) | **fall back to the existing `os.replace` route** |

The fallback is **honest**: it is reachable only when the alternative is *impossible*. **Name the residual in
your decision record:** a writer that opened the db *before* it became corrupt could still hold an fd. That
window is genuinely unavoidable without a system-wide lock, and it is far narrower than today's.

---

## 4. 🔴 **The test that must be rewritten — and why the verifier must be told**

[`tests/test_ledger_sqlite_store.py:161-186`](tests/test_ledger_sqlite_store.py:161)
**`test_restore_snapshot_serializes_with_transaction`**

```python
# :183
time.sleep(0.1)      # restorer blocked on flock held by writer      ← FALSE. It is never blocked.
# :186
assert store.read_raw() == doc      # "restore wins; no orphan srcX"
```

**This test asserts the data loss and calls it correct.** It passes **because** the writer's committed row was
destroyed. **It is a regression lock on the bug.**

> **Your fix WILL turn this test red. That is the proof it worked.**
> A reviewer who does not know this will read **your fix** as the regression. **Say so, loudly, in the PR
> description, and make sure the independent verifier has read `C4-COR-01`.**

**Rewrite it** to assert what the property actually requires: the restorer **blocks** on `BEGIN IMMEDIATE`
until the writer commits, and the writer's commit is **visible** (not orphaned) before the restore supersedes
it.

---

## 5. Acceptance criteria

1. A writer holding `BEGIN IMMEDIATE` **blocks** a concurrent restore until it commits.
   *(Measurable: with the writer holding for 0.4 s, the restore must take **> 0.3 s**. Today: **0.001 s**.)*
2. The writer's commit is **visible** — not orphaned — before the restore supersedes it.
3. 🔴 **A cascade transaction's deferred media unlinks NEVER delete a file whose ledger row the restore brings
   back.** *(This is the severity driver. Test it explicitly.)*
4. A restore against a **corrupt** live db **still succeeds** via the fallback.
5. The **snapshot file format is unchanged** — a snapshot taken post-fix restores on pre-fix code.
6. 🔴 **A two-PROCESS contention test passes** — *not* threads. SQLite locking is per-inode and **cross-process**;
   the audit's thread experiments are **indicative, not sufficient.** **This is a merge gate.**

---

## 6. Tests

| Test | Action | Must fail before? |
|---|---|---|
| `test_restore_snapshot_serializes_with_transaction` | 🔴 **REWRITE** | ✅ *it currently passes **because of** the loss* |
| `test_restore_does_not_orphan_media_unlinks` | NEW | ✅ |
| `test_restore_two_process_contention` | NEW | ✅ |
| `test_restore_falls_back_on_corrupt_db` | NEW | ⚪ passes today — **pins the fallback** |

Every test must be **adversarial**: it must **fail on current source** unless explicitly marked as pinning an
existing property.

---

## 7. Enumerate before you edit

State in the PR:
- **Every caller** of `Ledger.restore_snapshot` (expect: **zero in production**; `ledger_wipe.py:246` advertises
  it in a docstring; tests call it).
- **Every caller** of `SqliteLedgerStore.restore` and `.write_raw` and `.read_raw`.
- **Every writer** that takes `store.lock()` — confirm **none** of them is changed by this slice.
- Confirm **no schema change** and **no on-disk format change**.

---

## 8. Preserve — do not break

- `Ledger.transaction`, `_save_unlocked`, and every other writer: **already correct. Do not touch them.**
- `_snapshot_restore_control_files` (the `accounts.json` / `personas.json` sidecar restore).
- `snapshot()`'s existing behaviour (it already takes the right lock).
- The `ControlFileError` typing on a missing/unreadable snapshot.

## 9. 🔴 Forbidden scope expansion

- ❌ Do **not** touch `Ledger.transaction` or any other writer.
- ❌ Do **not** "unify" the A↔B lock domains (`COUP-01`).
- ❌ Do **not** fix the wipe's `MOL-71` preview gap.
- ❌ Do **not** delete `ledger.lock` in this PR — **even though this fix makes it genuinely dead for the first
  time.** That is a separate, trivial cleanup; bundling it obscures the diff.

---

## 10. Process

- **CI:** `unit`. **Never run the suite locally** (repo policy). Replay both AST ratchets
  (`test_swallow_ratchet`, `test_internal_prints_routed`) before pushing.
- **Self-merge on green: NO.**
- **Independent verifier: REQUIRED — and they must be briefed on `C4-COR-01`** (the encoding test), or they will
  reject the fix for turning a green test red.
- **Deliver:** the exact diff + a decision record covering (a) why Option A was rejected, (b) the corrupt-db
  fallback and its residual window, (c) the two-process test result.
- **Rollback:** revert the PR. The snapshot file format is unchanged.
- **State remaining unknowns honestly.** In particular: whether any operator has *already* run a restore against
  a live daemon (if so, media may already be missing, and the ledger will not say so).
