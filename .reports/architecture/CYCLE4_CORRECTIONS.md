# FanOps — Cycle 4 corrections to Cycles 1–3

**Cycle 4 · 2026-07-14 · git HEAD `fcffa73` (unchanged)**

Authority order applied: **executable code > executed experiment > prior JSON > prior prose > comments.** Each
correction names the superseded claim by its stable ID and says why it was wrong.

Cycle 3 closed with the method note:

> *"A guard's reachability is not established by reading the guard. It is established by reading the branch it
> sits on."*

**Cycle 4 adds the layer beneath it:**

> **A defect's reachability is not established by reading the code that would do it. It is established by
> proving something reaches that code.** Cycle 3's `C3-F5` is a mechanism that no production path can invoke —
> the experiment that "proved" it hand-built the entity whose absence is the whole point.
>
> **And a test that passes is not evidence the property holds.** It may be passing *because of* the defect.

---

## C4-COR-01 — 🔴 `test_restore_snapshot_serializes_with_transaction` **encodes the defect**

**Not previously examined by any cycle.** This is the most consequential correction in the audit, and it is a
correction to the *verification layer*, not to a finding.

[`tests/test_ledger_sqlite_store.py:161-186`](tests/test_ledger_sqlite_store.py:161) stages **exactly** the
`F-B` race:

```python
# :183
time.sleep(0.1)          # restorer blocked on flock held by writer      ← FALSE
# :186
assert store.read_raw() == doc      # restore wins; no orphan srcX
```

**Both lines are wrong, in opposite directions.**

- **The comment states a mechanism that does not exist.** `Ledger.transaction` takes `store.lock()` (SQLite
  `BEGIN IMMEDIATE`, [ledger.py:484](src/fanops/ledger.py:484)). `restore_snapshot` takes
  `_file_lock(cfg.lock_path)` ([ledger.py:551](src/fanops/ledger.py:551)). **The restorer is never blocked.**
  **Executed (EXP-C4-2): it completed in 0.001 s.**
- **The assertion passes *because of* the data loss.** The writer's `commit()` **succeeded** (no exception) and
  its row was **destroyed** by the `os.replace`. The test asserts `read_raw() == doc` — *"no orphan `srcX`"* — and
  gets it, **because `srcX` was annihilated.**

> **The test is a regression lock on the bug.** Any correct fix to `RC-4` turns it **red** — and a maintainer
> seeing a green test go red will read **the fix** as the regression.

**Consequence for remediation:** the test rewrite is **not** optional cleanup. It **must ship in the same PR** as
the source fix (see [`change_interference.json`](change_interference.json) → `must_ship_ATOMICALLY`), and the
independent verifier **must be briefed on this** or they will reject the fix.

**Generalization — carry this to Cycle 5:** Cycle 2 recorded the codebase's recurring shape as *"the doc names a
mechanism that does not exist, while the property survives via a different one."* `C4-COR-01` is that shape **in
the test layer**: the test **names** a mechanism that does not exist, while its **assertion** passes via the
defect. **Every "characterization test" this audit proposes must be checked for the same inversion before it is
trusted.**

---

## C4-COR-02 — `C3-F5` is **NOT reachable**. Reclassified.

**Superseded claim** ([`failures.json`](failures.json) `C3-F5`; [`CYCLE3_CORRECTIONS.md`](CYCLE3_CORRECTIONS.md)
`C3-COR-04`; [`SIDE_EFFECT_GRAPH.md`](SIDE_EFFECT_GRAPH.md) §5):

> *"`Render.path` is durably rewritten INTO a `mkdtemp` directory."* — filed **CERTAIN**, severity **MEDIUM**,
> *"evidence: EXP-10 (executed)"*.

**The mechanism is real. Nothing invokes it.**

| Check | Result |
|---|---|
| AST census: `Ledger.add_render` callers | **0** |
| AST census: `Render(...)` constructor sites | **2** — *both* deserializers ([ledger.py:458](src/fanops/ledger.py:458), [ledger_bridge.py:44](src/fanops/ledger_bridge.py:44)) |
| AST census: `led.renders[...] = ` stores | **4** — *all* `model_copy` **updates** of an existing row |
| [crosspost.py:225](src/fanops/crosspost.py:225) | **`render_id = None`** — hardcoded on every minted Post |
| **Live ledger** (read-only, `sqlite mode=ro`) | **0 renders · 0 of 347 posts carry a `render_id`** |

Therefore `led.renders` is **always empty**, every `led.renders.get(...)` returns `None`, and **all three**
`Render.path` writes ([compress.py:112](src/fanops/post/compress.py:112),
[compress.py:131](src/fanops/post/compress.py:131), [run.py:367](src/fanops/post/run.py:367)) sit behind
`if r is not None:` / `if post.render_id:` guards that are **always false**.

**Where Cycle 3 went wrong:** EXP-10 **hand-constructed a `Render` row** in its fixture. That proves *"the code
does X when given a Render"* — **not** *"the system produces a Render."* The two are different claims, and the
audit's own method note (*"grep absence ≠ code absence"*) has an unstated twin: **code presence ≠ path
reachability.**

**Reclassified:** `C3-F5` → **latent mechanism hazard (unreachable)**. Replaced by **`C4-F5`** (below).

---

## C4-COR-03 — the `C3-F4` ↔ `C3-F5` coupling is **FALSE**. Refuted by execution.

**Superseded claim** ([`failures.json`](failures.json) `C3-F5` → `worst_credible_consequence`):

> *"Compounds C3-F4: **the fix for the leak would BREAK the pointer**."*

That claim would have forced the temp-dir cleanup and a path-relocation + migration to ship as **one large,
risky slice**. **It is wrong on both halves.**

1. **The pointer Cycle 3 named does not exist** (`C4-COR-02`).
2. **The pointer that *does* exist self-heals.** EXP-C4-4, executed:
   - `Post.media_urls` **is** persisted into the `mkdtemp` dir — by the **Studio oversize-retry** path
     ([actions.py:1024/1029](src/fanops/studio/actions.py:1024) inside a `Ledger.transaction`;
     [:1034](src/fanops/studio/actions.py:1034) filters only `http` URLs, **keeping** the `file://` one). **This
     is the real defect, and Cycle 3 named the wrong field.**
   - **But deleting the temp dir recovers.** `media_path_for_post`
     ([compress.py:61-65](src/fanops/post/compress.py:61)) falls through its `media_urls` branch to the **clip**
     branch and returns `clip.path`; the next `apply_shrink_to_post` then re-points `media_urls` at a **fresh,
     existing** file. Measured: `re-pointed to a FRESH, EXISTING file? True`.

**Residual harm of the stale pointer:** a broken Studio media preview + one re-shrink per publish (disk churn).
**Real, but LOW — not a correctness break.**

> **The temp-dir cleanup is therefore a safe, independent, ~20-line slice.** No migration. No relocation. No
> atomicity requirement. **This cycle removed a slice rather than adding one.**

---

## C4-COR-04 — `C3-F2`'s **root cause** was mis-attributed

**Superseded claim** ([`failures.json`](failures.json) `C3-F2` → `root_cause`;
[`CYCLE3_CORRECTIONS.md`](CYCLE3_CORRECTIONS.md) `C3-COR-02`):

> *"A **SIBLING-PARITY DIVERGENCE** between the two status clients, exactly the class `src/fanops/CLAUDE.md`
> warns about."*

**The two clients are symmetric.** Verified at source this cycle:

| | Postiz | Zernio |
|---|---|---|
| `401` | `PostizAuthError` | `ZernioAuthError` |
| **`>= 300`** | **`raise RuntimeError`** ([metrics.py:164-165](src/fanops/post/metrics.py:164)) | **`raise RuntimeError`** ([metrics.py:516-517](src/fanops/post/metrics.py:516)) |

**Identical.** The divergence is **the remote endpoint shape**:

- **Postiz** has *no per-post status endpoint.* An unknown id is a **row absent from a 200-OK *list* page** →
  `{"status": "unknown"}` — **no raise.**
- **Zernio** *has* a true per-post lookup. An unknown id is a **404 on `GET /posts/{id}`** → **raise.**

**The finding stands** (the ladder is dead code on Zernio). **Its cause did not.** And the cause matters,
because the wrong cause implies the wrong fix: *"make the clients agree"* is a **shallow fix** that leaves the
real-token case and the 500-forever case unterminated.

> **The correct statement:** *the terminal ladder was placed on a branch whose reachability depends on a remote
> API's response shape.* The root fix is to make the terminal a pure function of `(state, age)` — **independent
> of the poll** — which is `RC-2` / `SLICE-04`. The client fix (`SLICE-05`) is then a *semantic* correction, not
> a *structural* one, and **must not ship alone.**

---

## C4-COR-05 — `C3-F1`'s door was wrong, and the **real one is far more reachable**

**Superseded claim** ([`failures.json`](failures.json) `C3-NF2` → `but`;
[`EXECUTION_PATHS.md`](EXECUTION_PATHS.md) W6):

> *"Door: `bulk_send_to_review` **deliberately** preserves `submission_id`."* — with the implied path being a
> revert of a `needs_reconcile` post.

**`needs_reconcile` cannot be reverted.**
`_REVIEW_REVERT_BLOCKED` ([actions.py:844-847](src/fanops/studio/actions.py:844)) =
`{published, analyzed, **needs_reconcile**, **submitting**, **submitted**}`.

**The reachable door is `failed`** — and the chain is an **ordinary operator workflow**, proven end-to-end by
**EXP-C4-1**:

```
1. post publishes            → submitted, REAL submission_id
2. reconcile polls           → THE BACKEND REPORTS FAILED        (an ordinary event: IG rejects the aspect
                               → PostState.failed, real sid PRESERVED   ratio; TikTok flags the audio)
                                 (reconcile.py:735 model_copy sets only state + error_reason)
3. operator: "send back to Review"  → awaiting_approval, sid PRESERVED   (actions.py:949)
4. operator: re-approve            → queued,             sid PRESERVED   (ledger.py:591 never clears it)
5. publish_due → _publish_one CLAIM → submitting  (COMMITTED)
6.               NETWORK: is_real_submission_id → SKIP the POST          (run.py:287)
7.               FINALIZE persists → submitting          FOREVER
8. reconcile: _is_fake_token = False → no escalation, no give-up
```

**Executed result: still `submitting` at +100 000 h.**

> **This is not an exotic path.** It is *"a platform rejected my post; I sent it back to fix the caption and
> re-approved it."* The post **never publishes again**, and **nothing says so**.

**And it is worse than Cycle 3 recorded.** Cycle 3 said the post gets *"one `stuck …` breadcrumb, then never
updated."* **On the reachable path it gets ZERO breadcrumbs** — see `C4-COR-06`.

---

## C4-COR-06 — the stranded post is **silent from pass one**, not after one breadcrumb

**Superseded claim** ([`failures.json`](failures.json) `C3-F1` → `operator_signal`;
[`OBSERVABILITY.md`](OBSERVABILITY.md) §2):

> *"one `stuck …` breadcrumb stamped ONCE at 6h, then NEVER updated. A post stuck 3 days and one stuck 3 years
> look IDENTICAL."*

**On the reachable path there is no breadcrumb at all.**

`bulk_send_to_review` ([actions.py:944-951](src/fanops/studio/actions.py:944)) clears `scheduled_time`,
`public_url`, `metrics`, and `published_at` — **but not `error_reason`.** So the reverted post carries a **stale
reason from its previous life**.

**EXP-C4-1, observed:** the post reaches `submitting` still carrying
`"reconciled: poster reports failed (IG rejected: unsupported aspect ratio)"`.

That stale string trips [reconcile.py:767](src/fanops/reconcile.py:767) (`if post.error_reason: continue`) on
the **very first** reconcile pass — so the post is skipped **before** it can ever be breadcrumbed.

> **The operator sees a post in `submitting` whose reason says *"poster reports failed."*** Incoherent, never
> updated, and the system will never look at it again. **Filed as `C4-F1a`.**

---

## C4-COR-07 — `F-B`'s severity was understated: it **deletes real media**

**Superseded claim** ([`invariants.json`](invariants.json) `INV-07`; [`CYCLE2_EXTENSION.md`](CYCLE2_EXTENSION.md)
§3; [`retries.json`](retries.json) `R-16`):

> *"**SILENT DATA LOSS.** … a live writer holding `BEGIN IMMEDIATE` had the db file `os.replace`'d out from
> under it; its `commit()` **SUCCEEDED** with no exception and its **data** was silently discarded."*

**True, and incomplete.** The loss is not confined to ledger rows.

`Ledger.transaction` ([ledger.py:484-488](src/fanops/ledger.py:484)):

```python
with store.lock():
    led = cls.load(cfg, store=store)
    yield led
    led._save_unlocked()            # ← commits into the ORPHANED inode. Succeeds.
    led._drain_deferred_unlinks()   # ← os.remove() on real .mp4 files. PROCEEDS.
```

The deferred-unlink design is documented as *"correct: a rolled-back txn never deletes a file it did not
drop."* **That is true against a rollback.** In a restore race the transaction is **not** rolled back — it
**commits successfully into an orphan** — **so the unlinks proceed.**

**EXP-C4-3, executed:**

```
writer commit()            : SUCCEEDED
ledger rows after restore  : ['src_000000000000']   ← RESTORED from the snapshot
media file still on disk?  : False                  ← os.remove() ran anyway
```

> **The ledger comes back claiming the media exists. The `.mp4` is gone.** Silent, unrecoverable, and reached by
> following the **documented** wipe-rollback procedure ([ledger_wipe.py:246](src/fanops/ledger_wipe.py:246))
> while the daemon is running.

**Filed as `C4-F2a`. This is why `RC-4` is sequenced first**, ahead of everything the brief proposed before it.

---

## C4-COR-08 — the sibling that gets it right was never noticed

**Not previously recorded.** Three cycles examined `restore_snapshot` and none looked six lines up.

```python
# ledger.py:540 — Ledger.snapshot
with store.lock():                       # ✅ SQLite BEGIN IMMEDIATE — the SAME lock every writer takes
    store.snapshot(dest)

# ledger.py:551 — Ledger.restore_snapshot          (ELEVEN LINES LATER)
with _file_lock(cfg.lock_path):          # ❌ fcntl.flock on a DIFFERENT FILE — excludes nothing
    store.restore(src)
```

**The correct pattern is present in the same class.** This is not a missing concept — it is **one method that
did not use the concept its own sibling uses.**

And `store.restore()` **already supports** being called under a held lock
([ledger_sqlite.py:131](src/fanops/ledger_sqlite.py:131), `relock = self._conn is not None`) — machinery that is
**dead**, because the only caller never holds the lock.

*(Taking the right lock is nonetheless **insufficient** — see [`REMEDIATION_OPTIONS.md`](REMEDIATION_OPTIONS.md)
§1, Option A. `os.replace` orphans an inode a writer already holds, and **no** lock acquired beforehand can
prevent that. The root fix is to **not replace the file**.)*

---

## C4-COR-09 — `OPS-001` is **still engaged**. Four consecutive cycles, single-threaded.

The orchestration gate refused even a **read** of the marker this cycle:

> `REFUSED (orchestration gate): this command would modify a PROTECTED path (.orchestration/state/).`

…in response to `cat .orchestration/state/ACTIVE`.

**Cycles 1, 2, 3 and now 4 have all been executed single-threaded** because a stale wave marker (last touched
**2026-07-13**) has never been disengaged. Disengage is `orchestrate.py stop` — an **operator action**, not a
code change. It remains the single largest constraint on this audit's throughput.

---

## Claims from Cycles 1–3 that Cycle 4 **re-verified and upholds**

Recorded so no later cycle re-litigates them.

| Claim | Cycle-4 status |
|---|---|
| `C3-F1` — a real `submission_id` has no terminal path | **UPHELD** — and **more reachable** than recorded (`C4-COR-05`) |
| `C3-F2` — the ladder is dead code on Zernio | **UPHELD** — **cause re-attributed** (`C4-COR-04`) |
| `F-A` / `INV-03` — malformed provider → `DryRunPoster` on a live system | **UPHELD** — re-derived from [post/\_\_init\_\_.py:19-29](src/fanops/post/__init__.py:19) + [providers.py:56](src/fanops/post/providers.py:56) |
| `F-B` — `restore_snapshot` silently discards a committed txn | **UPHELD** — re-executed, **and severity raised** (`C4-COR-07`) |
| `C3-F4` — unbounded `fanops-shrink-*` leak | **UPHELD** — AST census confirms it is the **only** `mkdtemp` in `src/fanops`, with **no** `rmtree` anywhere |
| `C3-F9` — `daemon status` reports `alive` while every pass halts | **UPHELD** — [daemon.py:467](src/fanops/daemon.py:467) |
| `C3-F10` — the irreversible actuator has the weakest gate | **UPHELD** — [adjust.py:82-96](src/fanops/adjust.py:82), [cli.py:155](src/fanops/cli.py:155) |
| `C3-NF1` — the publish retry loop cannot double-POST | **UPHELD** |
| `C3-NF2` — the five requeue paths all clear `submission_id` | **UPHELD** (the strand arrives by the **`failed`** door, not a requeue) |
| `INV-02` — "single writer of `queued`" is false, **but the property holds** | **UPHELD** — **not a defect.** Doc drift. |
| `INV-08` — no-auto-publish | **UPHELD** |
| `INV-09` — `_publish_one` is the sole network-POST caller | **UPHELD** |
| `INV-10` — `needs_reconcile` never downgraded to `failed` | **UPHELD** |
| Cycle-3's timer pins (`24 h` escalate / `72 h` give-up, both from `scheduled_time`) | **UPHELD** — [reconcile.py:48,54](src/fanops/reconcile.py:48) |

---

## Method notes carried forward to Cycles 5+

1. **Code presence ≠ path reachability.** Before filing a defect, prove something **reaches** it. `C3-F5`'s
   experiment hand-built the entity whose absence was the whole point. **Ask: what production call site
   constructs the input this defect requires?**
2. 🔴 **A passing test is not evidence the property holds.** It may pass **because of** the defect
   (`C4-COR-01`). **For every invariant an audit claims is protected by a test, read the test's *assertion*, not
   its *name*.**
3. **When two methods on the same class do the same kind of thing, diff them.** `snapshot` and
   `restore_snapshot` sit eleven lines apart; one takes the right lock. **Three cycles missed it because nobody
   compared the siblings.**
4. **A "sibling-parity divergence" claim must be checked against the actual siblings.** Cycle 3 asserted the two
   status clients diverge. **They are byte-identical in shape.** The divergence was in the *remote APIs*. A wrong
   cause implies a wrong (shallow) fix.
5. **Test the coupling before you design around it.** Cycle 3's *"the fix for the leak would break the pointer"*
   would have forced a migration. **One experiment refuted it and deleted a whole slice of work.**
