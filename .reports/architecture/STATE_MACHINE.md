# FanOps — State Machine Proof

**Cycle 2 · 2026-07-14 · git HEAD `fcffa73`**

> ## ⚠ §1 IS PARTLY WRONG — read [`CYCLE2_EXTENSION.md`](CYCLE2_EXTENSION.md) §1 first
>
> The §1 "zero writers" census below was built from a **literal** grep (`\.state = PostState\.`). An AST
> census over all 127 modules found **five generic/dynamic writers it could not see** — `PostState(<str>)`,
> `p.state = <var>`, `setattr(p, <var>, v)`, `model_copy(update=<var>)`, and **enum-valued keyword
> defaults**. Authoritative twin: [`transitions.json`](transitions.json).
>
> **Correction: `PostState.retired` is NOT reserved.** It has a writer — [cli.py:395](src/fanops/cli.py:395),
> reachable via `fanops resolve <id> retired` (argparse `choices`, [cli.py:702](src/fanops/cli.py:702)).
> `PostState.analyzed` likewise has **two** writers (`track.py:193` **and** `cli.py:395`).
>
> **Survives the re-census:** `PostState.error` · `ClipState.{published,analyzed}` (so `_LIVE_CLIP_STATES`
> is still a **dead guard**) · `BatchState.{closed,error}` · `RenderState.*` (**Cycle-1 `FIND-001` stands**).

Companion to [`INVENTORY.md`](INVENTORY.md) (Cycle 1, canonical). This document does not restate the
inventory. It proves, per entity: every state, every writer, every guard, legal vs illegal
transitions, who enforces legality, where persistence happens, and whether the transition is atomic /
idempotent / recoverable.

**Evidence standard.** Every claim cites `file:line`. Documentation was navigation only. Where docs
and code disagree, code wins and the disagreement is recorded in [`INVARIANT_AUDIT.md`](INVARIANT_AUDIT.md).

---

## 0. The persistence substrate (applies to every transition below)

Every entity state lives in one SQLite ledger. There is no per-entity store.

| Property | Fact | Evidence |
|---|---|---|
| Store | `00_control/ledger.sqlite`, WAL + `synchronous=FULL` | [ledger_sqlite.py:27-28](src/fanops/ledger_sqlite.py:27) |
| Tables | `ledger_meta` (schema_version) + `ledger_rows` (map_name, row_id, payload) | [ledger_sqlite.py:30-32](src/fanops/ledger_sqlite.py:30) |
| Write shape | `DELETE FROM ledger_meta; DELETE FROM ledger_rows;` then re-INSERT **every row of every map** | [ledger_sqlite.py:64-71](src/fanops/ledger_sqlite.py:64) |
| Lock | `BEGIN IMMEDIATE` on a dedicated connection; `sqlite3.OperationalError` → typed `LockBusyError` | [ledger_sqlite.py:92-95](src/fanops/ledger_sqlite.py:92) |
| Nesting | refused: `RuntimeError("SqliteLedgerStore.lock() nested on same instance")` | [ledger_sqlite.py:86-87](src/fanops/ledger_sqlite.py:86) |
| Txn scope | `Ledger.transaction` holds the lock across **load → mutate → save** | [ledger.py:483-488](src/fanops/ledger.py:483) |
| Rollback | save runs **only on clean exit**; an uncaught raise leaves the last committed snapshot | [ledger.py:479-482](src/fanops/ledger.py:479), [ledger_sqlite.py:103-105](src/fanops/ledger_sqlite.py:103) |

**Consequences that hold for every entity, and are not repeated per-entity below:**

- **Atomicity is per-transaction, never per-field.** A save is a full-document replace. Two fields
  mutated in one `Ledger.transaction` commit together or not at all.
- **A transition is durable only at transaction exit.** In-memory `post.state = X` is not persisted
  until `_save_unlocked` ([ledger.py:505-507](src/fanops/ledger.py:505)).
- **Guards are in-lock re-checks.** Every state-promoting method re-reads the entity under the lock
  and no-ops on the wrong source state. This is what makes contended/duplicate calls safe — *not* a
  single-writer property (see [`INVARIANT_AUDIT.md`](INVARIANT_AUDIT.md) INV-02).
- **Schema version 11.** A newer on-disk version is **refused**, never downgraded
  ([ledger.py:436-440](src/fanops/ledger.py:436)).

---

## 1. Reserved states — the class Cycle 1 found one instance of

Cycle 1 proved `RenderState` has no driver (`FIND-001`). **That is not a one-off.** Cycle 2 swept
every state enum for writers. Seven more enum members have **zero writers anywhere in `src/`**:

| Enum member | Writers | Readers (the guards it silently satisfies) |
|---|---|---|
| `ClipState.published` | **none** | [crosspost.py:76](src/fanops/crosspost.py:76) `_REUSABLE_CLIP_STATES`, [ledger.py:649](src/fanops/ledger.py:649) `_LIVE_CLIP_STATES`, [pipeline_status.py:17](src/fanops/pipeline_status.py:17) `_TERMINAL_CLIP` |
| `ClipState.analyzed` | **none** | same three |
| `PostState.error` | **none** | [digest.py:101](src/fanops/digest.py:101), [pipeline.py:438](src/fanops/pipeline.py:438), [views_results.py:620,645,679,687](src/fanops/studio/views_results.py:620) |
| `PostState.retired` | **none** | [ledger.py:660](src/fanops/ledger.py:660) `_PROTECTED_POST_STATES`, [pipeline.py:443](src/fanops/pipeline.py:443), [views_results.py:651](src/fanops/studio/views_results.py:651) |
| `BatchState.closed` | **none** | *no reader either* |
| `BatchState.error` | **none** | *no reader either* |
| `RenderState.{queued,published,analyzed,retired}` | **none** (Cycle 1 `FIND-001`) | [views_results.py:112](src/fanops/studio/views_results.py:112) `_SHIPPABLE_RENDER` |

### 1.1 The load-bearing consequence: `_LIVE_CLIP_STATES` is a dead guard

`_delete_moment_cascade` computes, for each clip:

```python
clip_live = c.state in self._LIVE_CLIP_STATES   # (published, analyzed)
```
— [ledger.py:665](src/fanops/ledger.py:665)

**Nothing ever sets a Clip to `published` or `analyzed`**, so `clip_live` is **always `False`** and
every branch it gates ([:667](src/fanops/ledger.py:667), [:671](src/fanops/ledger.py:671),
[:701-705](src/fanops/ledger.py:701)) is unreachable.

**The cascade-protection property nevertheless holds**, because it rests entirely on the *post* check
`p.state in self._PROTECTED_POST_STATES` ([ledger.py:667](src/fanops/ledger.py:667),
[:675](src/fanops/ledger.py:675)). A clip is deleted **iff no post hanging off it is protected**.
Protected = `{published, analyzed, submitted, submitting, needs_reconcile, awaiting_approval, queued,
retired}` ([ledger.py:652-660](src/fanops/ledger.py:652)). Unprotected = `{failed, error, rejected}`.

This is safe: when a clip's posts are all unprotected, the cascade deletes **the posts too**
([ledger.py:670](src/fanops/ledger.py:670)), so no post is left orphaned pointing at a deleted clip
or an unlinked `.mp4`. The clip check is **dead defensive code**, not a hole. Classified `Refined`.

### 1.2 `PostState.retired` is a deliberately abandoned feature, not an oversight

`models.py:123-127` describes `retired` as "a queued base post superseded by an operator-approved
stitch (M4)". No code writes it. `stitch_render.py:336` and `:350` set `StitchState.in_use` with the
comment *"additive: the bare base post is left to ship too"* — i.e. **the supersede was never built**.
That is coherent with the project's hard rule (fan accounts repost freely; never build
supersede/no-double-post), so `retired` is a reserved surface whose feature was intentionally dropped,
and its membership in `_PROTECTED_POST_STATES` is inert.

### 1.3 Dead method

`Ledger.set_post_state` ([ledger.py:572](src/fanops/ledger.py:572)) has **zero callers**. Every Post
transition uses direct `setattr` or `model_copy`. Its three siblings (`set_source_state`,
`set_moment_state`, `set_clip_state`) are all live.

---

## 2. `Post` — the publish state machine (Priority 1)

11 states ([models.py:104-127](src/fanops/models.py:104)). This is the only entity that reaches an
external network.

### 2.1 Complete transition table

| # | From → To | Writer (`file:line`) | Guard | Persist | Atomic | Idempotent | Recoverable |
|---|---|---|---|---|---|---|---|
| P0 | *(birth)* → `awaiting_approval` | [crosspost.py:234-252](src/fanops/crosspost.py:234) `_mint_surface_post`; [actions.py:491](src/fanops/studio/actions.py:491) `repost_post`; [actions.py:570](src/fanops/studio/actions.py:570) `crosspost_to_account` | `add_post` = `setdefault` (first-write-wins, content-addressed `pid`) [ledger.py:559](src/fanops/ledger.py:559) | txn exit | ✅ | ✅ (same `pid` ⇒ no-op) | n/a |
| P1 | `awaiting_approval` → `queued` | [ledger.py:591](src/fanops/ledger.py:591) `approve_post` | **`state is awaiting_approval`** [ledger.py:579](src/fanops/ledger.py:579) | txn exit | ✅ | ✅ (2nd call no-ops) | via `unapprove_post` |
| P2 | `awaiting_approval` → `rejected` | [ledger.py:595](src/fanops/ledger.py:595) `reject_post` | `state is awaiting_approval` [ledger.py:594](src/fanops/ledger.py:594) | txn exit | ✅ | ✅ | ❌ **terminal** |
| P3 | `queued` → `awaiting_approval` | [ledger.py:599](src/fanops/ledger.py:599) `unapprove_post`; [actions.py:944](src/fanops/studio/actions.py:944) | `state is queued` [ledger.py:598](src/fanops/ledger.py:598) | txn exit | ✅ | ✅ | ✅ |
| P4 | `queued` → `submitting` | [run.py:272](src/fanops/post/run.py:272) `_publish_one` **CLAIM** | `state is queued` [run.py:266](src/fanops/post/run.py:266) **+** due re-check under lock [run.py:268](src/fanops/post/run.py:268) | **txn exit BEFORE any network** (F11) | ✅ | ✅ (lost race ⇒ clean no-op) | reconcile |
| P5 | `submitting` → `queued` | [run.py:238](src/fanops/post/run.py:238) `_unclaim_no_integration` | `state is submitting` [run.py:237](src/fanops/post/run.py:237) **+** live backend w/ empty integration id [run.py:229](src/fanops/post/run.py:229) | txn exit | ✅ | ✅ | ✅ |
| P6 | `submitting` → `submitted` | [postiz.py:412](src/fanops/post/postiz.py:412); [zernio.py:256](src/fanops/post/zernio.py:256) | HTTP 200/201 **and** a recognizable post id [postiz.py:403-410](src/fanops/post/postiz.py:403) | FINALIZE txn [run.py:351-357](src/fanops/post/run.py:351) | ✅ | ⚠️ *see 2.3* | reconcile |
| P7 | `submitted` → `published` | [run.py:306](src/fanops/post/run.py:306) | **`public_url` non-empty** [run.py:305](src/fanops/post/run.py:305) | FINALIZE txn | ✅ | ✅ | ❌ terminal-positive |
| P8 | `submitted` → `needs_reconcile` | [run.py:313](src/fanops/post/run.py:313) | `public_url` **empty** [run.py:312](src/fanops/post/run.py:312) | FINALIZE txn | ✅ | ✅ | ✅ reconcile |
| P9 | `submitting` → `needs_reconcile` | [postiz.py:398](src/fanops/post/postiz.py:398) (network exc), [:409](src/fanops/post/postiz.py:409) (2xx no id), [:424](src/fanops/post/postiz.py:424) (5xx); [zernio.py:242,253,264](src/fanops/post/zernio.py:242); [run.py:329](src/fanops/post/run.py:329) (transient + real sid) | body **may** have landed; no idempotency key ⇒ never re-POST | FINALIZE txn | ✅ | ✅ | ✅ reconcile |
| P10 | `submitting` → `failed` | [run.py:334](src/fanops/post/run.py:334), [:339](src/fanops/post/run.py:339); [postiz.py:434](src/fanops/post/postiz.py:434); [zernio.py:272](src/fanops/post/zernio.py:272) | **`state is not needs_reconcile`** (never downgrade an ambiguous park) [run.py:325](src/fanops/post/run.py:325), [postiz.py:432](src/fanops/post/postiz.py:432) | FINALIZE txn | ✅ | ✅ | ✅ re-queueable |
| P11 | `queued` → `failed` | [run.py:389](src/fanops/post/run.py:389) `_due_or_fail` | `scheduled_time` unparseable **+** `state is queued` [run.py:388](src/fanops/post/run.py:388) | own short txn | ✅ | ✅ | ✅ |
| P12 | `failed` → `queued` | [run.py:422](src/fanops/post/run.py:422) daemon retry; [actions.py:1000,1031,1062,1103](src/fanops/studio/actions.py:1000) operator requeue | daemon: no real sid **+** transient reason **+** `n < _DAEMON_TRANSIENT_MAX` (=3) [run.py:413-421](src/fanops/post/run.py:413) · operator: `state in (failed, error)` | txn exit | ✅ | ✅ (bounded counter in `error_reason`) | ✅ |
| P13 | `published` → `analyzed` | [track.py:193](src/fanops/track.py:193) | **`prior is PostState.published`** [track.py:192](src/fanops/track.py:192) | txn exit | ✅ | ✅ | ❌ terminal |
| P14 | `submitting`/`submitted`/`needs_reconcile` → `published` | [reconcile.py:712-714](src/fanops/reconcile.py:712) | poller reports published **+** a valid `public_url` (else → `needs_reconcile` [:645](src/fanops/reconcile.py:645), [:689](src/fanops/reconcile.py:689)) | txn exit | ✅ | ✅ | ❌ terminal |
| P15 | `submitting`/`submitted` → `failed` | [reconcile.py:737](src/fanops/reconcile.py:737) | poller reports failed | txn exit | ✅ | ✅ | ✅ |
| P16 | `submitting` → `needs_reconcile` *(escalation)* | [reconcile.py:746-750](src/fanops/reconcile.py:746) | fake `fanops_` token **+** age > 72 h [reconcile.py:54](src/fanops/reconcile.py:54) | txn exit | ✅ | ✅ | ✅ |
| P17 | `needs_reconcile` → **`GAVE UP:`** *(labeled terminal)* | [reconcile.py:757-762](src/fanops/reconcile.py:757) | fake token **+** age > 72 h | txn exit | ✅ | ✅ (loop-head `_is_giveup` skip [:605](src/fanops/reconcile.py:605)) | 2-step: `fanops resolve <id> failed` → Studio recover |
| P18 | *(any non-terminal)* → `published`/`failed` *(operator force)* | [cli.py:394-398](src/fanops/cli.py:394) `cmd_resolve`; [actions.py:280](src/fanops/studio/actions.py:280) `mark_published` | `--url` **required** for terminal states [cli.py:384-388](src/fanops/cli.py:384); `mark_published` rejects empty url [actions.py:268-270](src/fanops/studio/actions.py:268) **and** an already-terminal post [actions.py:274](src/fanops/studio/actions.py:274) | txn exit | ✅ | `mark_published` ✅ / `cmd_resolve` ❌ (force-anything) | — |
| P19 | `awaiting_approval`/`queued`/live → *(cascade delete)* | — | **BLOCKED** by `_PROTECTED_POST_STATES` [ledger.py:667,675](src/fanops/ledger.py:667) | — | — | — | — |

`PostState.error` and `PostState.retired`: **no transition exists** (§1).

### 2.2 Illegal transitions and who enforces them

| Illegal transition | Enforcer | Mechanism |
|---|---|---|
| `awaiting_approval` → `queued` by anything but approval | `approve_post` guard [ledger.py:579](src/fanops/ledger.py:579) | Every *other* `queued` writer is guarded on a source state of `failed`/`error`/`submitting` — **none can leave `awaiting_approval`** (see INV-02) |
| `awaiting_approval` → published | publish iterates `queued` only [run.py:442](src/fanops/post/run.py:442) | structural |
| `needs_reconcile` → `failed` | explicit non-downgrade guard [run.py:325](src/fanops/post/run.py:325), [postiz.py:432](src/fanops/post/postiz.py:432) | `failed` is re-queueable ⇒ downgrading an ambiguous park risks a double-publish |
| `submitting` re-driven by `publish_due` | `posts_in_state(queued)` only [run.py:442](src/fanops/post/run.py:442) | reconcile owns `submitting`; auto-resubmit could double-post |
| `published` with empty `public_url` | **manual guards at 4 call sites**, *not* the type validator | ⚠️ see `INV-01` — the validator fires only at **construction** |
| double-POST of a post with a real submission id | [run.py:287-288](src/fanops/post/run.py:287) skip-resubmit | `is_real_submission_id` [models.py:384-393](src/fanops/models.py:384) |

### 2.3 The one idempotency gap in P6

`_publish_one` re-checks `is_real_submission_id(post.submission_id)` **before** the POST
([run.py:287](src/fanops/post/run.py:287)) and skips resubmission. But a post whose id is still the
birth token `fanops_<hash>` ([crosspost.py:246](src/fanops/crosspost.py:246)) is **not** a real id
([models.py:393](src/fanops/models.py:393)), so it does not skip. The double-post defence for that
case is the **claim** (P4): only a `queued` post is claimable, and the claim flips it to `submitting`
**and persists before the network** ([run.py:272](src/fanops/post/run.py:272)). A crash between the
claim-commit and the network leaves `submitting` — which `publish_due` never re-drives. **The backend
has no idempotency key** ([models.py:118-121](src/fanops/models.py:118)); this is the reason
`needs_reconcile` exists as a distinct state.

Residual: [run.py:270-271](src/fanops/post/run.py:270) logs `republish_with_real_id` and **proceeds**
— a deliberate allowance ("repost-freely OK, log it").

### 2.4 Full publish trace: Studio approval → external API → persistence

```
POST /posts/approve                                   app_routes_review.py:132  do_approve_posts
  └─> actions.approve_posts                           (Ledger.transaction)
        └─> Ledger.approve_post                       ledger.py:575   GUARD: state is awaiting_approval
              └─> state=queued, scheduled_time=…      ledger.py:591   [P1]
                    └─> _save_unlocked → BEGIN IMMEDIATE → full replace   ledger_sqlite.py:64-71

                        ── queue is the ledger itself; there is no broker ──
                        (QUE-001, the filesystem queue, is the AGENT-GATE queue, not the publish queue)

daemon tick / POST /schedule/publish-due              app_routes_schedule.py:108
  └─> publish_due(cfg, now)                           run.py:433
        ├─ _requeue_transient_failed_for_daemon       run.py:440   [P12]
        ├─ due = posts_in_state(queued) ∧ _due_or_fail run.py:442  [P11 on bad time]
        ├─ provider = _post_provider(cfg,accounts,p)  run.py:158   GATE-1: not live ⇒ "dryrun"
        │     └─ provider is None  ⇒ SKIP, stay queued (no_provider)      run.py:454
        │     └─ provider == dryrun ⇒ write_preview, HALT queued          run.py:458-467
        └─> _publish_one(cfg, pid, provider)          run.py:242
              ├─ CLAIM   (txn) queued→submitting      run.py:264-272  [P4] persisted pre-network
              ├─ NETWORK (lock-free)
              │    ├─ _ensure_media → upload          run.py:196  media.ensure_clip_media / ensure_render_media
              │    ├─ _publish_throttle_wait          run.py:140  postiz+live only, in-process dict
              │    ├─ get_poster(cfg, backend)        post/__init__.py:13  GATE-2 ⚠ INCOMPLETE (INV-03)
              │    │     └─ get_provider → PROVIDERS  providers.py:45-49
              │    └─ PostizPoster.publish            postiz.py:374  → POST {base}/public/v1/posts
              │          ├─ 200/201 + id → submitted  postiz.py:412  [P6]
              │          ├─ 2xx no id   → needs_recon postiz.py:409  [P9]
              │          ├─ 401         → PostizAuthError  postiz.py:417  → HALTS the whole run
              │          ├─ 5xx         → needs_recon postiz.py:424  [P9]  (never re-POST)
              │          └─ 429         → backoff+retry postiz.py:427
              ├─ promotion gate: public_url?          run.py:305   [P7] yes → published (+published_at,
              │                                                     publish_hour, publish_dow)
              │                                       run.py:313   [P8] no  → needs_reconcile
              └─ FINALIZE (txn) merge _NET_POST_FIELDS into a FRESHLY LOADED ledger   run.py:351-367
                    _NET_POST_FIELDS = (state, submission_id, error_reason, public_url,
                                        media_urls, published_at, account_id)   run.py:120
                    ⇒ a concurrent writer's OTHER fields are never clobbered (B4 lost-update fix)
              └─ _archive_published  OUTSIDE the txn  run.py:371-372  fail-open, never blocks a publish

reconcile (daemon)                                    reconcile.py
  ├─ _RECONCILABLE = (submitting, submitted, needs_reconcile)   reconcile.py:433
  ├─ poll backend → published (+ public_url)          reconcile.py:712  [P14] + publish_buckets :722
  ├─ poll backend → failed                            reconcile.py:737  [P15]
  ├─ 72h + fake token: submitting → needs_reconcile   reconcile.py:746  [P16]
  ├─ 72h + fake token: needs_reconcile → GAVE UP:     reconcile.py:757  [P17]
  └─ resolve_media_ids: stamp media_id + product_type reconcile.py:295  (IG only, published/analyzed)

track.pull_metrics                                    track.py
  └─ published → analyzed (+metrics, metrics_series)  track.py:193  [P13]
```

**The Postiz permalink trap (by design):** `_postiz_permalink` always returns `None`
([post/CLAUDE.md](src/fanops/post/CLAUDE.md); [postiz.py:411](src/fanops/post/postiz.py:411)), so a
fresh Postiz publish **cannot self-promote to `published`** — it lands `submitted` with no URL, the
P7 gate fails, and it parks in `needs_reconcile` [P8]. `reconcile` back-fills the URL on a later pass
[P14]. **The steady-state happy path for Postiz is `submitting → submitted → needs_reconcile →
published`, not `→ published` directly.**

### 2.5 Two independent dryrun/live gates — one is incomplete

The documented invariant is two gates. Both exist; **the second does not do what it claims**.

- **GATE-1 — `_post_provider`** ([run.py:165-166](src/fanops/post/run.py:165)): `if not cfg.is_live:
  return "dryrun"`. Unconditional, precedes any per-channel override. **Verified sound.**
- **GATE-2 — `get_poster`** ([post/__init__.py:19-23](src/fanops/post/__init__.py:19)): raises **only
  when the resolved backend string is literally `dryrun`** (case-insensitive). An **unrecognized**
  backend falls through `get_provider` → `None` → `return DryRunPoster(cfg)`
  ([post/__init__.py:28-29](src/fanops/post/__init__.py:28)) — **on a live system, without raising.**
  Full analysis + blast radius in [`INVARIANT_AUDIT.md`](INVARIANT_AUDIT.md) `INV-03`.

---

## 3. `Source` — 11 states

[models.py:61-75](src/fanops/models.py:61). Birth default `catalogued` ([models.py:179](src/fanops/models.py:179)).

| From → To | Writer | Guard | Notes |
|---|---|---|---|
| *(birth)* → `pending` | ingest, when `cfg.queue_gate` ON (**default ON**, [config.py:592-596](src/fanops/config.py:592)) | — | Invisible to every pipeline reducer (they key on `catalogued`+) |
| *(birth)* → `catalogued` | `_catalogue_file` (queue-gate OFF) | — | |
| *(birth)* → `discovered` | [ledger.py:757](src/fanops/ledger.py:757) `rebuild_catalog` | orphan file matching `_SID_RE` [ledger.py:234](src/fanops/ledger.py:234) | **Inert** until promoted |
| `pending` → `catalogued` | [actions_run.py:116,136](src/fanops/studio/actions_run.py:116) | operator release | The U4 queue gate |
| `catalogued` → `transcribed` | [transcribe.py:338,486](src/fanops/transcribe.py:338) | — | |
| `transcribed` → `signalled` | [signals.py:161,204](src/fanops/signals.py:161) | — | |
| → `moments_requested` | [moments.py:330,360](src/fanops/moments.py:330) | gate written | |
| → `picks_decided` | [moments.py:414](src/fanops/moments.py:414) | pass-1 picks reconciled | per-pick `moment_hooks` gates now in flight |
| `picks_decided` → `moments_decided` | [moments.py:649](src/fanops/moments.py:649) | **every** picked moment's hook landed | **atomic-per-source** — not per-persona |
| → `moments_empty` | [moments.py:335,475,507](src/fanops/moments.py:335) | model returned `[]` | Non-terminal, re-runnable; prior good moments preserved |
| *(any)* → `error` | [transcribe.py:453,464,470,481](src/fanops/transcribe.py:453); [signals.py:180](src/fanops/signals.py:180); [moments.py:470,503](src/fanops/moments.py:470); [responder.py:182-184](src/fanops/responder.py:182) (gate ceiling 3/3); [pipeline_status.py:209](src/fanops/pipeline_status.py:209) | per-unit quarantine | |
| *(any)* → `retired` | [ledger.py:736](src/fanops/ledger.py:736) `retire_source` | operator only | Cascades moments via `reconcile_moments(src, {})`; **file left on disk**; `rebuild_catalog` will not resurrect it [ledger.py:756](src/fanops/ledger.py:756) |

**Wipe-safety invariant (verified):** `rebuild_catalog` **adds orphans only** — it never retires a
source whose file is missing ([ledger.py:746-748](src/fanops/ledger.py:746)).

---

## 4. `Moment` — 5 states

[models.py:77-82](src/fanops/models.py:77). **The only entity with `validate_assignment=True`**
([models.py:211](src/fanops/models.py:211)) — so direct attribute writes on a Moment *are* validated,
unlike every other unit.

| From → To | Writer | Guard |
|---|---|---|
| *(birth)* → `picked` | `moments` pass-1 ingest | window chosen, hook not yet authored — **not renderable** (the render loop keys on `decided`) |
| *(birth)* → `decided` | model default [models.py:214](src/fanops/models.py:214) | hand-built moments |
| `picked` → `decided` | `ingest_moment_hooks` | the per-pick `moment_hooks` gate landed (hook, **or a valid clean `null`**) |
| `decided` → `clipped` | [clip.py:741,758,815,872](src/fanops/clip.py:741) | render succeeded |
| *(any)* → `retired` | [adjust.py:95](src/fanops/adjust.py:95) (learning retire); [ledger.py:687](src/fanops/ledger.py:687) (cascade survivor) | |
| *(any)* → `error` | [pipeline.py:228](src/fanops/pipeline.py:228) `_quarantine` | per-unit quarantine |

**Resurrection is blocked:** `reconcile_moments` **skips the upsert** for a moment whose prior state
is `retired` ([ledger.py:636-642](src/fanops/ledger.py:636)) — a later decision cannot un-retire a
lineage that `adjust.retire` suppressed.

**GC preservation:** a moment whose `hook_strategy` starts with `CLEAN_AWAITING` is exempt from
cascade delete ([ledger.py:630-632](src/fanops/ledger.py:630)) — its future strategy must still find it.

---

## 5. `Clip` — 10 states (2 unreachable)

[models.py:84-90](src/fanops/models.py:84). Birth default `rendered` ([models.py:277](src/fanops/models.py:277)).

| From → To | Writer | Guard |
|---|---|---|
| *(birth)* → `rendered` | `render_moment` | |
| *(birth)* → `error` | [clip.py:830,838,847,860](src/fanops/clip.py:830) | render failure — **born error**, so a failed-aspect clip is never laundered into a captioned post with a dangling mp4 ([pipeline.py:218](src/fanops/pipeline.py:218)) |
| *(birth)* → `stitch_draft` | stitch producer | **structurally unpostable** — absent from both crosspost's seed set and `_REUSABLE_CLIP_STATES` ([crosspost.py:75-76](src/fanops/crosspost.py:75)) |
| `rendered` → `captions_requested` | [caption.py:223](src/fanops/caption.py:223) | |
| → `captioned` | [caption.py:360](src/fanops/caption.py:360) | |
| → `held` | [caption.py:297,316](src/fanops/caption.py:297) | HOLD gate (off-brand / lift floor) |
| `captioned` → `queued` | [crosspost.py:286](src/fanops/crosspost.py:286) | set **unconditionally** after the surface loop — *even when zero posts were born* ([crosspost.py:284-285](src/fanops/crosspost.py:284) logs `no_post_born`) |
| *(any)* → `retired` | [ledger.py:720](src/fanops/ledger.py:720) `retire_clip` | |
| *(any)* → `error` | [pipeline.py:292](src/fanops/pipeline.py:292) `_quarantine` | |
| → `published` / `analyzed` | **NONE** | §1 — unreachable |

**Seed-set guard** (`_seed_clips`, [crosspost.py:151-158](src/fanops/crosspost.py:151)): `captioned`
∧ ¬held ∧ ¬retired(clip) ∧ ¬retired(moment) ∧ moment ∈ {`decided`,`clipped`} (or absent → fail-open).

---

## 6. `Render` — driverless (Cycle 1 `FIND-001`, re-confirmed)

Born `rendered` ([models.py:420](src/fanops/models.py:420)); **no writer advances it**. `add_render`
is `setdefault` — first-write-wins, content-addressed dedup ([ledger.py:560](src/fanops/ledger.py:560)).

**Cycle-1 Refinement 1 re-verified:** the renders *map* **is** mutated on the publish path — but only
`path` and `media_url`, never `state`:
- [run.py:363](src/fanops/post/run.py:363): `r.media_url = render_media` (once-per-render upload cache)
- [run.py:367](src/fanops/post/run.py:367): `led.renders[p.render_id] = r2.model_copy(update={"path": render_path})` (post-shrink path rewrite)

---

## 7. `StitchPlan` — 5 states, the only fully-driven auxiliary machine

[models.py:458-460](src/fanops/models.py:458).

| From → To | Writer | Guard |
|---|---|---|
| *(birth)* → `suggested` | [ledger.py:762](src/fanops/ledger.py:762) `add_stitch_plan` (`setdefault`) | content-addressed dedup |
| `suggested` → `approved` | [ledger.py:766](src/fanops/ledger.py:766) | **`state is suggested`** [ledger.py:765](src/fanops/ledger.py:765) — a contended 2nd approval is a clean no-op |
| `approved` → `in_use` | [stitch_render.py:336,350](src/fanops/stitch_render.py:336) | render committed |
| `suggested`/`approved` → `dismissed` | [ledger.py:770](src/fanops/ledger.py:770) | **`state in (suggested, approved)`** [ledger.py:769](src/fanops/ledger.py:769) — an `in_use` plan is forward-only |
| → `error` | [stitch_render.py:359](src/fanops/stitch_render.py:359) | `render_attempts` cap |

---

## 8. `Batch` — 1 reachable state

[models.py:487-488](src/fanops/models.py:487). Born `open`; `closed` and `error` have **no writer and
no reader** (§1). The model comment concedes it: *"born open; this build only ever sets open"*.

---

## 9. `Persona` / `Account` / `ImportedMedia` — no state machine

- **`Account`** has `AccountStatus` (`planned|warming|active|retired`,
  [accounts.py:19-20](src/fanops/accounts.py:19)) but it is **not a ledger entity** — it lives in
  `00_control/accounts.json`, mutated by `set_status` ([accounts.py:513](src/fanops/accounts.py:513)),
  serialized by an **flock** on `accounts.lock`, not by the SQLite lock. Only `active` participates in
  `surfaces()` ([accounts.py:271](src/fanops/accounts.py:271)).
- **`Persona`** (`personas.json`) has **no state field**. `UNK-001` (Cycle 1) resolves to: identity is
  the record key in `00_control/personas.json`; there is no state machine.
- **`ImportedMedia`** has no state field ([models.py:508-531](src/fanops/models.py:508)). `add_imported_media`
  is a **true UPSERT** (`self.imported_media[im.media_id] = im`, [ledger.py:562](src/fanops/ledger.py:562))
  — deliberately *not* `setdefault`, so a re-pull's fresher metrics win.

---

## 10. The agent-gate machine (filesystem, not ledger)

`QUE-001` is a **filesystem** queue: `04_agent_io/requests/{kind}__{key}.request.json` +
`.response.json`, correlated by a stamped `request_id`.

**Four gate kinds are written. Only three can be answered.**

| Kind | Requester | In `responder._SCHEMA`? | In `gate_keys.gate_source_id`? | Answerable? |
|---|---|---|---|---|
| `moments` | [moments.py:329,359](src/fanops/moments.py:329) | ✅ [responder.py:50](src/fanops/responder.py:50) | ✅ [gate_keys.py:9](src/fanops/gate_keys.py:9) | ✅ |
| `moment_hooks` | [moments.py:577,582](src/fanops/moments.py:577) | ✅ | ✅ [gate_keys.py:9](src/fanops/gate_keys.py:9) | ✅ |
| `captions` | [caption.py:222](src/fanops/caption.py:222) | ✅ | ✅ (clip→moment→source, [gate_keys.py:11-13](src/fanops/gate_keys.py:11)) | ✅ |
| **`intro_match`** | [intro_match.py:108](src/fanops/intro_match.py:108) | ❌ **absent** | ❌ **no branch** | ❌ **never** |

`UNK-002` resolved — full analysis in [`INVARIANT_AUDIT.md`](INVARIANT_AUDIT.md) `INV-04`.

**Gate escalation (live):** `_on_deterministic_fail` ([responder.py:145-153](src/fanops/responder.py:145))
burns a per-gate attempt counter; at `_GATE_DETERMINISTIC_MAX = 3` ([responder.py:53](src/fanops/responder.py:53))
it calls `_terminate_gate_source`:
- `moment_hooks` / `captions` → **synthesize a clean fail-open response** ([responder.py:159-174](src/fanops/responder.py:159)) so ingest proceeds.
- `moments` → **promote the owning source to `SourceState.error`** ([responder.py:182-184](src/fanops/responder.py:182)) — fail-closed — then `discard_gate` ([responder.py:190](src/fanops/responder.py:190)).

**Stale-answer guard:** `_answer_one` re-reads `latest_request_id` after the model call and **drops
the answer** if the gate re-seeded mid-call ([responder.py:115-119](src/fanops/responder.py:115)).
