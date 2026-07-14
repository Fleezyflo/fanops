# FanOps — Crash Boundaries and Recovery

**Cycle 3 · 2026-07-14 · git HEAD `fcffa73`** · Twin: [`retries.json`](retries.json)

For every workflow containing **both** durable writes and external effects, the exact ordering, what survives a
crash at each boundary, what the next tick sees, and whether recovery is autonomous, operator-driven, or
**nonexistent**.

---

## 1. Consistency mechanism, per operation

| Operation | Classification | Primitive |
|---|---|---|
| Ledger write | **transactional** | SQLite `BEGIN IMMEDIATE` + full-document replace + WAL + `synchronous=FULL` ([ledger_sqlite.py:27-28,64-71,92](src/fanops/ledger_sqlite.py:27)) |
| Control JSON (`accounts`, `personas`, `hashtag_*`) | **atomic FS replacement** | `controlio.write_json_atomic` — `mkstemp` same-dir + `os.replace` ([controlio.py:31](src/fanops/controlio.py:31)) |
| Gate request / response | **atomic FS replacement** | tmp + `os.replace` ([agentstep.py:63-66,82-87](src/fanops/agentstep.py:63)) |
| Clip / render `.mp4` | **atomic FS replacement** | ffmpeg → `<dst>.part.mp4` → `os.replace`. **The `.mp4` suffix is required** (it picks the muxer, MOL-78) |
| Studio upload | **atomic FS replacement** | `.uploadpart` → `os.replace` |
| **Publish** | **claim-before-side-effect** | `queued → submitting` **committed before any network I/O** ([run.py:264-272](src/fanops/post/run.py:264)) |
| Cascade `.mp4` unlink | **deferred until after commit** | `_deferred_unlinks` drained **after** `_save_unlocked` ([ledger.py:487-488](src/fanops/ledger.py:487)) |
| `06_published/<day>/<pid>.json` | **best effort** | `O_CREAT|O_WRONLY|O_TRUNC, 0o600` — created 0600 atomically; **outside** the finalize txn, fail-open |
| `studio_audit.log` | **best effort** | append; `except Exception: pass` ([audit.py:46](src/fanops/audit.py:46)) |
| **`agentstep.bump_attempts`** | ❌ **no consistency mechanism** | bare `write_text` ([agentstep.py:147](src/fanops/agentstep.py:147)) |
| **`pipeline_run.note_stage`** | ❌ **no consistency mechanism** | `ftruncate(0)` + `write` ([pipeline_run.py:64-69](src/fanops/pipeline_run.py:64)) |
| **`restore_snapshot`** | ❌ **lock provides no exclusion** | `flock` on `ledger.lock` + `os.replace` of the **db file** — mutually invisible to `BEGIN IMMEDIATE` |

**There is no two-phase commit anywhere, and none is needed** — except across the publish boundary, where
`claim-before-network` + `needs_reconcile` is the (correct) substitute.

---

## 2. Crash points on the publish path, in order

This is the boundary that matters. Every row was derived from
[run.py:242-373](src/fanops/post/run.py:242).

| # | Crash at | Durable state after | Next tick sees | Autonomous recovery? |
|---|---|---|---|---|
| **P-a** | **before the claim** | `queued` | `publish_due` re-selects it | ✅ **full** — the post publishes normally |
| **P-b** | **after claim-commit, before network** | **`submitting`** | `publish_due` **never re-drives `submitting`** ([run.py:442](src/fanops/post/run.py:442)) | ⚠ **reconcile only** — and see §3 |
| **P-c** | **during the network POST** | `submitting` | same as P-b. **Nothing was double-posted** — the body may or may not have landed | ⚠ **reconcile only** |
| **P-d** | **after external success, before finalize** | `submitting` (the `submitted` flip lives only in the throwaway in-memory ledger) | reconcile polls the backend, finds it **published**, and promotes | ✅ **this is the design working** |
| **P-e** | **during finalize** | the txn **rolls back** → still `submitting` | identical to P-d | ✅ |
| **P-f** | **after finalize, before `_archive_published`** | `published` (correct) | the `06_published/` record is **missing** | ⚠ the archive is a convenience artifact; reconcile re-archives a *reconcile-recovered* publish ([reconcile.py:728-732](src/fanops/reconcile.py:728)) but **nothing re-archives a `_publish_one` publish**. Cosmetic. |
| **P-g** | **after the media shrink replaced the file, before finalize persists `Render.path`** | the **on-disk render is the shrunk temp file**, but the ledger still points at the **original** | the original still exists (shrink writes to a **new** temp path, it does not overwrite) → **no dangle**. But the temp dir **leaks** (`C3-F4`). | ✅ safe (by luck of the temp-file design, not by an invalidation rule) |

**P-b/P-c is THE crash boundary of the system.** It is *correctly* engineered — `submitting` is persisted
before the network precisely so a crash cannot cause a duplicate live post on resume (F11). The design is
right. **The bug is that the reader of `submitting` — reconcile — has a hole.**

---

## 3. What actually recovers a `submitting` post — and what does not

| Post shape | Recovery | Verdict |
|---|---|---|
| `submitting`, **no** `submission_id`, > 15 min | `heal_stranded_submitting` → `needs_reconcile` ([reconcile.py:512-531](src/fanops/reconcile.py:512)) | ✅ **autonomous** |
| `submitting`, **fake** `fanops_` token, **Postiz**, > 24 h | escalate → `needs_reconcile`; > 72 h → `GAVE UP:` | ✅ **autonomous** |
| `submitting`, **fake** token, **Zernio** | the poll **raises** on the 404 → `continue` → **never reaches the escalation** | ❌ **NONE** — `C3-F2` |
| `submitting`, **real** `submission_id`, poll never resolves | `_is_fake_token` excludes it from **both** terminals | ❌ **NONE** — `C3-F1` |
| `submitting`, but **`is_live_backend == False`** (every channel's backend malformed) | `_reconcile_safe` is **gated** ([pipeline.py:318](src/fanops/pipeline.py:318)) while `_publish_safe` is **not** ([:334](src/fanops/pipeline.py:334)) → **reconcile never runs at all** | ❌ **NONE** — Cycle-2 `F-A`, mechanism proven by EXP-11 |

**The three ❌ rows are the same hole seen from three angles, and there is no manual recovery either:**

- The Studio **requeue family accepts only `failed`/`error`** — a `submitting` post is not offered.
- The Studio **Review revert** (`bulk_send_to_review`) blocks on `_REVIEW_REVERT_BLOCKED`.
- The only escape is **`fanops resolve <post_id> failed`** → then Studio *Recover*. That is a two-step CLI
  break-glass **that requires the operator to already know the post is stranded — and nothing tells them.**

---

## 4. Crash points elsewhere

| Boundary | Durable state | Next tick / operator sees | Recovery |
|---|---|---|---|
| **during the main reduce txn** ([pipeline.py:504-541](src/fanops/pipeline.py:504)) | **the whole pass rolls back** — save runs only on clean exit | the last committed snapshot | ✅ **by design.** The heavy artifacts were warmed **lock-free** beforehand, so the next pass fingerprint-**skips** onto them and recovers the work rather than redoing it (pinned by `test_advance_rollback_recovers_warm_artifacts`) |
| **ingest: after the file copy, before the Source mint** | a staged file, **no ledger row** | nothing | ✅ `rebuild_catalog` adopts it as `discovered` (**inert** until an operator promotes) |
| **ingest: after the mint, before `_archive_staged`** | the row exists; the inbox copy is not archived | the next `stage_inbox_candidates` re-stages it | ✅ `add_source` is `setdefault` → **no-op** |
| **during a control-file write** (`accounts.json` etc.) | `os.replace` is atomic → **either the old file or the new one**, never a torn one | — | ✅ |
| **during `bump_attempts`** | **a torn `attempts.json`** | `except Exception: n = 0` → **the gate-retry ceiling silently resets to 0** | ❌ `C3-F6` — the bounded 3-attempt escalation becomes **unbounded** |
| **during `note_stage`** | **an empty `.run.lock` body** | `_read_body → {}`; `run_stage_snapshot → None` | ⚠ the **flock** (the authority) is kernel-released on death, so the **lease self-heals**; only the mid-pass stage breadcrumb is lost. `C3-F7` |
| **`kill -9` holding the run lease** | — | the kernel releases the flock | ✅ **self-heals, no manual `rm`** |
| **`kill -9` holding a ledger txn** | WAL rolls back | last committed snapshot | ✅ |
| **daemon + Studio act concurrently** | ledger: serialized by `BEGIN IMMEDIATE` (typed `LockBusyError`, 30 s bound). Control files: per-file flock. **A↔B: NO shared lock** (`COUP-01`) | — | ⚠ handled for the one case that matters — `publish_due` re-resolves the integration id at publish time and FINALIZE merges `account_id` with an explicit **"in-flight wins"** policy |
| **`restore_snapshot` while the daemon is running** | **the concurrent writer's `commit()` SUCCEEDS and its data is SILENTLY DISCARDED** | the snapshot's state | ❌ **`F-B`** — proven by execution in Cycle 2. `restore_snapshot` has **no production caller**, but [ledger_wipe.py:246](src/fanops/ledger_wipe.py:246) advertises it as *the* wipe rollback path |
| **during `execute_wipe`** | the wipe is **entirely inside one `Ledger.transaction`** ([ledger_wipe.py:252-275](src/fanops/ledger_wipe.py:252)) → **full rollback** | unchanged | ✅ and it is gated: `WipeNotConfirmed` unless confirmed; `SnapshotRequired` unless `snapshot_is_restorable` **actually opens the SQLite file and reads `schema_version`** ([:218-235](src/fanops/ledger_wipe.py:218)). **The wipe does not unlink media** — it writes a `.files.txt` manifest and leaves the files. |

---

## 5. The one silent-corruption class (Cycle-2 `INV-01`, upheld)

A terminal-state write with **no `public_url`** **saves cleanly** and then **bricks the next `Ledger.load`**
with a `ControlFileError` — taking down the daemon **and every Studio page at once**.

`model_copy(update=…)` and direct `setattr` both **bypass the validator**; only *construction* raises. The
property survives via **four independent manual call-site guards**
([run.py:305](src/fanops/post/run.py:305), [actions.py:268](src/fanops/studio/actions.py:268),
[cli.py:384](src/fanops/cli.py:384), `dryrun.py:39`). **A fifth door added without a manual guard would be a
load-time poison pill.**

This is a **latent** crash-recovery hazard, not a live one: no current code path produces the row. It is the
single highest-leverage invariant to enforce structurally.

---

## 6. Recovery inventory

| Command / action | What it does | Excludes concurrent writers? |
|---|---|---|
| `Ledger.snapshot` | copies the SQLite image | — (read) |
| **`Ledger.restore_snapshot`** | flock `ledger.lock` → `os.replace` the DB + unlink `-wal`/`-shm` | ❌ **NO** — the flock is invisible to `BEGIN IMMEDIATE` |
| `rebuild_catalog` | adopts orphan media as `discovered` | ✅ ledger txn |
| `resume_source` | `error`/`moments_empty` → `transcribed` (keeps a good transcript) or `catalogued` | ✅ caller's txn; **refuses a healthy source** |
| `_force_reset_to_catalogued` | T0 reset: purge caches, discard gates, `reconcile_moments(sid, {})` | ✅ caller's txn |
| `heal_stranded_submitting` | `submitting` + **no sid** + > 15 min → `needs_reconcile` | ✅ own txn |
| `fanops resolve <id> <state>` | **force-anything**; `--url` required for terminal states | ✅ own txn |
| Studio `recover_posts` / retry ×4 | `failed`/`error` → `queued`; **all clear `submission_id`** | ✅ in-lock state re-check |
| `paths_rebase` | rewrites `Render.path` after a root move | ✅ ledger txn |
| daemon `stop` / `ensure` | shells `launchctl`; boots the **keeper first** so it cannot re-bootstrap the pump | — |

**The missing recovery path** (the brief asks for the single most important one):

> **There is no recovery — autonomous or manual — for a post stranded in `submitting`/`needs_reconcile` with a
> real `submission_id`, and no signal that tells the operator it happened.** The Studio surfaces it in the
> in-flight lane with a `stuck …` reason, but that reason is stamped **once** and never updated, so a post
> stuck for 3 days and one stuck for 3 years look **identical**.
