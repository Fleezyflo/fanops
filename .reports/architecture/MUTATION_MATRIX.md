# FanOps — Mutation Matrix

**Cycle 2 · 2026-07-14 · git HEAD `fcffa73`** · Priorities 4 (mutation matrix) + 7 (concurrency)

> ## ⚠ CORRECTED — read [`CYCLE2_EXTENSION.md`](CYCLE2_EXTENSION.md); twin: [`mutation_writers.json`](mutation_writers.json)
>
> - **The `Post.state` writer count is wrong below (19).** The AST re-census puts it at **21**, including
>   generic/dynamic writers a literal grep cannot see. See [`transitions.json`](transitions.json).
> - **The `model_copy` bypass is systemic, not specific to the published-URL rule.** It skips **every
>   validator on every model** — including `Moment`'s, despite `validate_assignment=True`
>   (which protects `setattr` only). Executed proof in the extension, §1 SC-3.
> - **The A↔B "no cross-surface exclusion" row is now PROVEN, not inferred.** A live `BEGIN IMMEDIATE`
>   writer had `restore_snapshot` swap the DB file out from under it; the writer's `commit()`
>   **succeeded** and its data was **silently discarded**.

Every mutable surface: who writes, who reads, who validates, who persists, whether multiple writers
exist, whether ordering matters, whether replay changes behaviour, whether a stale value is possible,
whether silent corruption is possible.

---

## 0. The five mutable surfaces

FanOps has exactly five kinds of mutable state. Everything below is one of these.

| # | Surface | Persistence | Concurrency control |
|---|---|---|---|
| **A** | **Ledger entities** (8 maps) | `00_control/ledger.sqlite` | SQLite `BEGIN IMMEDIATE` |
| **B** | **Control files** (9 JSON) | `00_control/*.json` | `fcntl.flock` per-file |
| **C** | **Process env** (`os.environ` + `.env`) | `.env` + keyring | **none** |
| **D** | **Module globals** | in-process only | **none** (single-process by design) |
| **E** | **Filesystem artifacts** (mp4/json/log) | stage dirs | `os.replace` / stage_lock |

**Surfaces A and B do not share a lock.** This is the single most important concurrency fact in the
system and is the root of `COUP-01`.

---

## A. Ledger entities

### A.0 Universal properties (true for every ledger field; not repeated per-row)

| Property | Value | Evidence |
|---|---|---|
| **Persists** | `Ledger._save_unlocked` → `SqliteLedgerStore.write_raw` | [ledger.py:505-507](src/fanops/ledger.py:505) |
| **Write granularity** | **Full-document replace** — every row of every map, every save | [ledger_sqlite.py:64-71](src/fanops/ledger_sqlite.py:64) |
| **Validates** | pydantic, **at construction only** (`Ledger.load`) | [ledger.py:444-459](src/fanops/ledger.py:444) |
| **Does NOT validate** | `model_copy(update=…)`, direct `setattr` (no `validate_assignment` except `Moment`) | proven — [`INVARIANT_AUDIT.md`](INVARIANT_AUDIT.md) INV-01 |
| **Ordering matters** | Only *within* a transaction. Across transactions, last-committed wins. | |
| **Stale reads possible** | **Yes** — `Ledger.load()` outside a transaction is a lock-free snapshot | [run.py:441](src/fanops/post/run.py:441), [run.py:274](src/fanops/post/run.py:274) |
| **Silent corruption** | **Yes, one class** — a terminal-state write with no `public_url` saves fine and **bricks the next load** | INV-01 |

**The `validate_assignment` asymmetry** is a real, load-bearing inconsistency:

| Model | `validate_assignment` | Consequence |
|---|---|---|
| `Moment` | **True** ([models.py:211](src/fanops/models.py:211)) | `m.segments = [...]` re-runs `_validate_segments` + re-derives the start/end envelope |
| `Source`, `Clip`, `Post`, `Render`, `StitchPlan`, `Batch`, `ImportedMedia` | **False** (default) | every `setattr` bypasses every validator |

`Post` is the model with the **only raising validator** (`_enforce_published_url_invariant`) and the
one **without** `validate_assignment`. That pairing is the defect surface.

### A.1 `Post` — the highest-hazard entity (7 network-mutated fields)

`_NET_POST_FIELDS` ([run.py:120](src/fanops/post/run.py:120)) is the **only** set merged back from the
lock-free network phase into a freshly-loaded ledger — this is the B4 lost-update fix, and it is why
a concurrent writer's *other* fields are never clobbered.

| Field | Writers | Validated by | Multi-writer | Ordering | Replay-safe | Stale possible | Silent corruption |
|---|---|---|---|---|---|---|---|
| `state` | **19 sites** (see [`STATE_MACHINE.md`](STATE_MACHINE.md) §2) | in-lock source-state guards only | **YES (19)** | **YES** — claim must precede network | ✅ claim is a no-op if not `queued` | ✅ (re-read under lock) | **YES** → INV-01 |
| `public_url` | [postiz.py:414](src/fanops/post/postiz.py:414), [reconcile.py:713](src/fanops/reconcile.py:713), [actions.py:279](src/fanops/studio/actions.py:279), [cli.py:396](src/fanops/cli.py:396) | `safe_public_url` (https-only) [postiz.py:414](src/fanops/post/postiz.py:414) | YES (4) | **YES** — must be set **before** the state flip | ✅ | ✅ | via `state` |
| `submission_id` | crosspost birth (`fanops_…`) [crosspost.py:246](src/fanops/crosspost.py:246); poster overwrite [postiz.py:413](src/fanops/post/postiz.py:413); daemon retry clears it [run.py:423](src/fanops/post/run.py:423) | `is_real_submission_id` [models.py:384](src/fanops/models.py:384) | YES (3) | — | ✅ | ✅ | no |
| `account_id` | crosspost (frozen at mint); **re-resolved at publish** [run.py:280](src/fanops/post/run.py:280) | — | YES (2) | **"in-flight wins"** — FINALIZE writes it **only when changed** [run.py:356](src/fanops/post/run.py:356) | ✅ | **YES** — a Go-Live remap after crosspost | no (explicitly handled) |
| `media_urls` | `_ensure_media` [run.py:206,224](src/fanops/post/run.py:206) | — | 1 | — | ✅ (cached on Clip/Render) | ✅ | no |
| `published_at` | [run.py:307](src/fanops/post/run.py:307), [reconcile.py:713](src/fanops/reconcile.py:713) | — | YES (2) | — | ✅ | ✅ | no |
| `publish_hour` / `publish_dow` | [run.py:311](src/fanops/post/run.py:311), [reconcile.py:722](src/fanops/reconcile.py:722) | `publish_buckets` — **fails CLOSED to UTC** | YES (2) | — | ✅ | ✅ | no |
| `error_reason` | ~14 sites across run/postiz/zernio/reconcile | **`redact(…, postiz_api_key, zernio_api_key)`** [run.py:327,340](src/fanops/post/run.py:327) | **YES (14)** | — | ⚠️ **carries a counter** (`transient_daemon_retry=n/3`) [run.py:336](src/fanops/post/run.py:336) | ✅ | no |
| `metrics` / `metrics_series` | `track.record_metrics` (sole writer) | `_missing_high_weight` → `lift_degraded` marker | 1 | series is **append-only**, never rewritten [track.py:191](src/fanops/track.py:191) | ✅ idempotent per `offset` | ✅ | no |
| `media_id` / `product_type` | `reconcile.resolve_media_ids` (sole writer) [reconcile.py:295](src/fanops/reconcile.py:295) | `_norm_permalink` match | 1 | — | ✅ (skips resolved rows) | ✅ | no |
| `scheduled_time` | crosspost mint; `approve_post` [ledger.py:591](src/fanops/ledger.py:591); reschedule/clear actions; daemon retry [run.py:426](src/fanops/post/run.py:426) | `schedule_utc` — unparseable ⇒ **post `failed`** [run.py:385-391](src/fanops/post/run.py:385) | **YES (5)** | **YES** — `clear_time` must un-approve **before** clearing, else a `queued`-and-timeless post exists | ✅ | ✅ | no (a timeless `queued` post now **parks**, [run.py:381](src/fanops/post/run.py:381)) |

**`error_reason` is a control channel, not just a message.** Three separate parsers read structure out
of it: `transient_daemon_retry_count` (the retry counter, [run.py:333](src/fanops/post/run.py:333)),
`_is_giveup` (the `GAVE UP:` prefix, [reconcile.py:85](src/fanops/reconcile.py:85)), and the REST-gate
quarantine sentinel ([reconcile.py:90](src/fanops/reconcile.py:90)). **A free-text overwrite of
`error_reason` silently resets the retry budget.** [run.py:714](src/fanops/reconcile.py:714) does
exactly that deliberately (`"error_reason": None` on a successful publish — "a transient poll-error
reason must not survive a successful publish").

### A.2 `Clip.media_url` / `Render.media_url` — the F44 upload cache

| Property | Value |
|---|---|
| Writers | FINALIZE only, **and only when currently unset**: `if c is not None and clip_media and not c.media_url` [run.py:359](src/fanops/post/run.py:359); same for Render [run.py:362](src/fanops/post/run.py:362) |
| Concurrent write | Safe by the `not …media_url` guard — first writer wins, no clobber |
| Replay | Idempotent — a second publish reuses the cached URL, uploading once |
| Stale | **Yes** — if the hosted URL expires, nothing invalidates the cache |

### A.3 `Render.path` — the one Render mutation

`led.renders[p.render_id] = r2.model_copy(update={"path": render_path})` — a **post-compression path
rewrite** ([run.py:367](src/fanops/post/run.py:367)), guarded on `r2.path != render_path`. Cycle 1's
`FIND-001` Refinement 1 re-confirmed: the renders **map** is written on the publish path; `Render.state`
is not.

### A.4 Idempotency of the `add_*` family

| Method | Semantics | Evidence |
|---|---|---|
| `add_source` / `add_moment` / `add_clip` / `add_post` / `add_render` / `add_stitch_plan` / `add_batch` | **`setdefault`** — first-write-wins | [ledger.py:556-560](src/fanops/ledger.py:556), [:762](src/fanops/ledger.py:762), [:774](src/fanops/ledger.py:774) |
| `add_imported_media` | **UPSERT** — last-write-wins | [ledger.py:562](src/fanops/ledger.py:562) — deliberate: a re-pull's fresher metrics must win |

Because every id is **content-addressed** ([ids.py:7](src/fanops/ids.py:7)), `setdefault` makes a
whole-pipeline **replay a no-op** for entity creation. This is the system's core idempotency property.

**One deliberate exception** — `_mint_surface_post` **deletes and re-mints** a post whose prior state
is `rejected` or `failed` ([crosspost.py:229-231](src/fanops/crosspost.py:229)), so a re-run *can*
resurrect a rejected post. Everything else is first-write-wins.

---

## B. Control files — the parallel mutation system

Nine JSON files under `00_control/`, **none** of them in the ledger, each with its own flock.

| File | Writers | Lock | Validated at write | Read by |
|---|---|---|---|---|
| `accounts.json` | `add_account`, `set_backend`, `write_integration`, `set_status`, `set_persona`, `set_clip_profile`, `set_ig_user_id`, `link_persona`, `ensure_channel`, `remove_account` — **10 mutators** ([accounts.py:372-620](src/fanops/accounts.py:372)) | `accounts.lock` [accounts.py:362-369](src/fanops/accounts.py:362) | **partially** — see below | publish routing, Studio, doctor |
| `personas.json` | `persona_store` mutators | `personas.lock` [persona_store.py:106-108](src/fanops/persona_store.py:106) | `_norm_focus` | account hydration |
| `hashtag_budget.json` | `record_query` | `hashtag_budget.lock` [meta_graph.py:519](src/fanops/meta_graph.py:519) | — | Graph budget gate |
| `hashtag_bans.json` | `add_ban`, `remove_ban` | `hashtag_bans.lock` [hashtags.py:125,137](src/fanops/hashtags.py:125) | — | `vet_hashtags` |
| `hashtags.json` | `refresh_store` | **none** | — | tag selection |
| `cutover.json` | `track._auto_validate_metrics_shape`, cutover probe | **none** | — | `learning_validated` |
| `timing_bias.json` | `apply_timing_bias` | **none** | — | `surface_time` hour hint |
| `tuning.json` | operator (hand-edit) | **none** | `_sanitize_tuning` — **warn+drop**, never raise [config.py:32-57](src/fanops/config.py:32) | HOLD gate, lift weights |
| `.env` | `golive._dual_write` [golive.py:47-66](src/fanops/studio/golive.py:47) | **none** | — | everything |

**Every `accounts.json` mutator follows the same correct pattern** — load **inside** the lock
(`_load_raw_accounts` is called *after* `with _accounts_txn(cfg):`, e.g.
[accounts.py:384-385](src/fanops/accounts.py:384)) and mutate the **raw dict**, never
`Account.model_dump()`. That is what preserves sibling accounts and unknown/future fields
([accounts.py:354-358](src/fanops/accounts.py:354)). **Verified across all 10 mutators.**

### B.1 The `accounts.json` validation gap (the INV-03 root)

| Field | Write-boundary validation | Load-time validation | Gap |
|---|---|---|---|
| `handle` | `validate_account_handle` — raises | canonicalized on read | none |
| `platforms` | membership in `Platform` — raises | pydantic enum | none |
| `status` | membership in `AccountStatus` — raises | pydantic enum | none |
| `clip_profile` | membership in `PROFILE_NAMES` — raises | **none** (`Optional[str]`) | tolerated (fail-open downstream) |
| `framing` | membership in `FRAMING_NAMES` — raises | **none** | tolerated |
| **`backends[platform]`** | membership in `_VALID_BACKENDS` — raises ([accounts.py:414](src/fanops/accounts.py:414)) | **NONE** — `dict[str, str]` | **⚠ INV-03** |

`Accounts.validate()` checks the integration/backend **pairing** ([accounts.py:241-250](src/fanops/accounts.py:241))
but **never the backend value**. A hand-edited `"backends": {"instagram": "Postiz"}` passes `validate()`,
passes `load()`, reaches `get_poster`, and constructs a `DryRunPoster` on a live system.

### B.2 Per-row leniency (`MOL-79`) and its sibling gap

`Accounts.load` builds each `Account` under its **own** guard, so one malformed row is skipped and
recorded in `skipped_rows` ([accounts.py:141-145](src/fanops/accounts.py:141)) rather than crashing the
registry; `validate()` promotes the skip to a visible problem ([accounts.py:230-231](src/fanops/accounts.py:230)).
A **wrong top-level shape** still fails loud ([accounts.py:134-135](src/fanops/accounts.py:134)). This
is correct and matches `Personas.load`'s posture.

---

## C. Process environment — the only truly unsynchronized surface

| Property | Value |
|---|---|
| Writers | `golive._dual_write` → `os.environ[key] = value` **and** `.env` ([golive.py:47-66](src/fanops/studio/golive.py:47)); `autopilot.py:80` → `FANOPS_RESPONDER` |
| Readers | **`Config`, on every property access** — 74 `os.getenv` calls, none cached |
| Lock | **NONE** |
| Ordering | dual-write: `.env` then `os.environ` |
| **Stale value possible** | **YES — the load-bearing one.** `os.environ` is **per-process**. A Studio `go_live` writes the Studio process's env + `.env`. **A separately-running daemon process does not see it** until it re-reads `.env`. |

This is the documented "live-flip never reaches the resident daemon" defect class (project memory
`code-audit-2026-07-11-verified-defects`). Recorded here as a **structural property**, not a bug report:
*surface C has no cross-process propagation mechanism at all.*

The **secret** sub-surface is stronger: `set_secret` writes to keyring, **reads it back**, and raises
`OSError` if the value does not round-trip ([secret_provider.py:72-90](src/fanops/secret_provider.py:72))
— load-bearing, because the caller scrubs the plaintext `.env` fallback on success. **Reads fail open;
writes fail closed.**

---

## D. Module globals

| Global | Writers | Scope | Hazard |
|---|---|---|---|
| `run._publish_throttle_last: dict[(str,str), float]` | [run.py:123,155](src/fanops/post/run.py:123) | **in-process only, by design** | If `fanops` ever runs as multiple concurrent publisher processes, the Postiz per-minute throttle is **silently per-process**, so N processes publish N× the rate limit. Self-declared in [post/CLAUDE.md](src/fanops/post/CLAUDE.md). |
| `run._sleep` | test hook [run.py:128](src/fanops/post/run.py:128) | in-process | test-only |
| `ledger._DEFAULT_LOCK_TIMEOUT = 30.0` | [ledger.py:26](src/fanops/ledger.py:26) | read at **call** time, not bound as a default arg [ledger.py:274-275](src/fanops/ledger.py:274) | tunable without re-import — deliberate |

This is the **entire** set of mutable module state. There are no singletons and no caches.

---

## E. Filesystem artifacts

| Artifact | Writer | Atomicity | Permissions |
|---|---|---|---|
| Control JSON | `controlio.write_json_atomic` — mkstemp same-dir + `os.replace` | **atomic** | — |
| Clip / render `.mp4` | ffmpeg → `<dst>.part.mp4` → `os.replace` | **atomic**; the `.mp4` suffix is **required** (it picks the muxer, MOL-78) | — |
| `06_published/<day>/<pid>.json` | `_archive_published` [run.py:59](src/fanops/post/run.py:59) | `O_CREAT\|O_WRONLY\|O_TRUNC, 0o600` — **created 0600 atomically**, no world-readable window | `0o600` |
| `05_scheduled/<pid>.json` (dryrun preview) | `dryrun.write_preview` [dryrun.py:25](src/fanops/post/dryrun.py:25) | plain `write_text` then `chmod` | `0o600` (best-effort) |
| `studio_audit.log` | `audit.write_audit` [audit.py:19](src/fanops/audit.py:19) | append | `0o600` |
| `ledger.sqlite` | `write_raw` | `chmod 0o600` after every write [ledger_sqlite.py:81](src/fanops/ledger_sqlite.py:81) | `0o600` |
| Agent gates | `agentstep.write_request` / `write_response` | `write_response` is **atomic** (no torn-read window, [responder.py:125](src/fanops/responder.py:125)) | — |
| Cascade `.mp4` unlink | **deferred until after commit** — `_deferred_unlinks` [ledger.py:421,518-525](src/fanops/ledger.py:421) | **correct**: a rolled-back txn never deletes a file it did not drop | — |

`_drain_deferred_unlinks` is a genuinely well-designed ordering guarantee: the file is unlinked
**after** `_save_unlocked` ([ledger.py:487-488](src/fanops/ledger.py:487)), so a transaction that raises
leaves both the row **and** the file intact.

---

## Concurrency audit (Priority 7)

| Mechanism | Present? | Evidence |
|---|---|---|
| **WAL** | ✅ | [ledger_sqlite.py:27](src/fanops/ledger_sqlite.py:27) |
| **`BEGIN IMMEDIATE`** | ✅ — the ledger's sole writer lock | [ledger_sqlite.py:92](src/fanops/ledger_sqlite.py:92) |
| **`synchronous=FULL`** | ✅ | [ledger_sqlite.py:28](src/fanops/ledger_sqlite.py:28) |
| **Typed busy error** | ✅ `LockBusyError`, bounded by a 30 s timeout | [ledger_sqlite.py:94](src/fanops/ledger_sqlite.py:94) |
| **Nested-lock refusal** | ✅ `RuntimeError` on same instance | [ledger_sqlite.py:86-87](src/fanops/ledger_sqlite.py:86) |
| **Atomicity** | ✅ full-document replace inside one txn | [ledger_sqlite.py:64-71](src/fanops/ledger_sqlite.py:64) |
| **Crash recovery** | ✅ flock is **kernel-released on process death** (`kill -9` self-heals) | [ledger.py:265-272](src/fanops/ledger.py:265) · WAL rollback for SQLite |
| **Retry loops** | ✅ publish: 3 attempts w/ jittered exp-backoff ([run.py:292-323](src/fanops/post/run.py:292)); Postiz 429/ConnectTimeout backoff ([postiz.py:427](src/fanops/post/postiz.py:427)); flock 0.1 s poll ([ledger.py:288](src/fanops/ledger.py:288)) |
| **Duplicate suppression** | ✅ content-addressed ids + `setdefault` | [ledger.py:556-560](src/fanops/ledger.py:556) |
| **Idempotency (publish)** | ✅ claim-before-network + `is_real_submission_id` skip | [run.py:264-272](src/fanops/post/run.py:264), [:287](src/fanops/post/run.py:287) |
| **Double-publish protection** | ✅ **three independent layers** — see below |
| **Stale-lock recovery** | ✅ flock: kernel-released. SQLite: WAL. `.run.lock`: `LOCK_NB`, self-heals | [pipeline_run.py](src/fanops/pipeline_run.py) |
| **Lost-update protection** | ✅ B4 fix — `Ledger.transaction` holds the lock **across load**, and FINALIZE merges only `_NET_POST_FIELDS` into a **freshly loaded** ledger | [ledger.py:471-476](src/fanops/ledger.py:471), [run.py:351-357](src/fanops/post/run.py:351) |
| **Worker races (responder pool)** | ✅ safe — every guard is **per-key local state** (`rid_before`/`rid_after`, a unique `response_path`); no shared mutable state | [responder.py:99-101](src/fanops/responder.py:99), [:227-230](src/fanops/responder.py:227) |
| **Cross-surface (A↔B) exclusion** | ❌ **NONE** | `COUP-01` |
| **Cross-process env propagation** | ❌ **NONE** | Surface C |
| **Throttle across processes** | ❌ **NONE** | Surface D |

### The three double-publish layers (all verified)

1. **Approval gate** — publish iterates `queued` only; only `approve_post` (guarded on
   `awaiting_approval`) promotes there. ([run.py:442](src/fanops/post/run.py:442),
   [ledger.py:579](src/fanops/ledger.py:579))
2. **Claim** — in-lock re-read; publish **only if still `queued`**; flip to `submitting` and **persist
   before any network I/O**. A lost race is a clean no-op; a crash mid-network leaves `submitting`,
   which `publish_due` **never re-drives**. ([run.py:264-272](src/fanops/post/run.py:264))
3. **Ambiguity park** — a 5xx/timeout **after the body was sent** parks in `needs_reconcile`, never
   `failed` (which is re-queueable). The backend has **no idempotency key**, so a blind re-POST could
   double-publish. ([postiz.py:424](src/fanops/post/postiz.py:424),
   [run.py:325](src/fanops/post/run.py:325))

### The one race the design accepts

`_publish_one`'s NETWORK phase runs **lock-free** on a throwaway `Ledger.load()`
([run.py:274](src/fanops/post/run.py:274)). A concurrent writer may change other fields meanwhile.
This is **deliberate and correctly handled**: FINALIZE re-loads and merges **only** the 7
`_NET_POST_FIELDS`, so the concurrent writer's other changes survive. The one field with a declared
conflict policy is `account_id` — **"in-flight wins"**, written only when changed, so a concurrent
Go-Live remap to a *different* channel is not churn-clobbered by an identical value
([run.py:116-119,356](src/fanops/post/run.py:116)).
