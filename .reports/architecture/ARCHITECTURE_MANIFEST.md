# FanOps — Canonical Architectural Manifest

**Cycle 5 · 2026-07-14 · git HEAD `fcffa73`**
**This is the canonical architectural reference. Later cycles extend it; they do not restate it.**

Machine-readable knowledge base: [`kb/`](kb/) · Reader's guide: [`KNOWLEDGE_BASE.md`](KNOWLEDGE_BASE.md) ·
Dependency model: [`DEPENDENCY_MODEL.md`](DEPENDENCY_MODEL.md) · Corrections: [`CYCLE5_CORRECTIONS.md`](CYCLE5_CORRECTIONS.md)

> **Standard of proof.** Every claim below is backed by an AST census, an executed experiment, or a
> `file:line`. Documentation was used as a *lead*, never as proof. Where docs and code disagreed, **code
> won** and the disagreement is filed. **Unknowns are recorded as unknown** — see [`kb/unknowns.json`](kb/unknowns.json).

---

## 1. The repository in one page

**FanOps is a closed-loop clip-and-crosspost engine.** A source video is ingested, transcribed, and
analysed; an LLM (running as a *subprocess*, not an API) picks moments and writes hooks; clips are rendered
per-account; captions and hashtags are composed per-persona; posts are minted **awaiting operator approval**;
approved posts publish to Instagram/YouTube (via Postiz) and TikTok (via Zernio); reach is measured (via the
Meta Graph); and the measurements **bias the next round of selection**.

That last clause is the whole architecture — **the system is a closed loop.** But two claims must be kept
apart here, because Cycle 5's first draft ran them together and got one of them wrong:

| | Claim | Verdict |
|---|---|---|
| **A** | **The DOMAIN has a feedback loop** — select → render → caption → publish → measure → learn → select. | ✅ **Intentional. It IS the product. CERTAIN.** |
| **B** | **Therefore the module import graph must contain a cycle.** | 🔴 **DOES NOT FOLLOW — and it is false.** |

**At module level the domain core is *acyclic*.** 124 of 127 modules are singleton SCCs; the only compile-time
import cycle in the entire tree (`personas` ↔ `persona_store` ↔ `persona_research`) is in the *registry* and has
**nothing to do with the learning loop.** The loop is realized **without** a module-level import cycle — which is
arguably good design.

A feedback loop in *data/control flow* **does not require** a cycle in the *static import graph*: it could be
closed by the orchestrator (`pipeline` calling both `adjust` and `moments`) or via a shared abstraction.
**The mutual-import wiring is a design choice, not a consequence of the loop existing** — and **whether it is
intentional is UNKNOWN** (`UNK-C5-4`). Cycle 5's earlier verdict that it "is not a defect" **is retracted**
(`C5-COR-09`).

| | |
|---|---|
| **Language / layout** | Pure-Python `src/` layout, 127 modules, Python 3.12–3.13 |
| **Persistence** | **One** SQLite ledger (WAL, `synchronous=FULL`), 8 entity maps, schema v11, full-document replace |
| **The only queue** | **A filesystem queue.** `04_agent_io/requests/*.request.json` ↔ `.response.json`. No broker. |
| **Scheduling** | **launchd only.** No cron, no APScheduler, no in-process timer. |
| **Concurrency** | 5 lock domains. **L1 (ledger) and L2 (control files) do not exclude each other.** |
| **The engine** | `pipeline.advance()` — a five-phase pass. Every entry point funnels into it. |
| **Entry points** | `fanops` CLI (59 verbs) · the launchd daemon · the Flask Studio (149 routes, **0 authenticated**) |
| **External deps** | Postiz, Zernio, Meta Graph, Cloudflare R2, `claude -p`, ffmpeg, whisper, cv2, Docker |

**The cardinal rule the codebase actually honours:** *no network call and no heavy subprocess ever runs
inside the ledger lock.* Verified at every site.

**The safety property that matters most:** a post is **born `awaiting_approval`**; `publish_due` iterates
`queued` **only**; only `approve_post` — guarded on `awaiting_approval` — promotes. **Nothing publishes
without an explicit operator approval, even on a live backend with the daemon running.** (`INV-08`, upheld
across all five cycles.)

---

## 2. Architectural topology

### 2.1 The pipeline (logical)

```
  01_inbox/                          ┌─────────────── THE ONLY QUEUE IS A FILESYSTEM QUEUE ───────────┐
      │                              │  04_agent_io/requests/{kind}__{key}.request.json               │
      ▼                              │                       ↕  (correlated by a stamped request_id)  │
  ┌────────┐                         │                      .response.json                            │
  │ INGEST │  sha256 → src_<sha[:12]>│         answered by `claude -p` — A SUBPROCESS, NOT AN API     │
  └───┬────┘  content-addressed      └───────────────────────────────────────────────────────────────┘
      │       (replay is a no-op)                          ▲              ▲              ▲
      ▼                                                    │ moments      │ hooks        │ captions
  ┌────────────────┐   ┌──────────┐   ┌──────────┐   ┌─────┴────┐   ┌─────┴────┐   ┌─────┴────┐
  │ TRANSCRIBE     │──▶│ SIGNALS  │──▶│ SELECT   │──▶│  RENDER  │──▶│ CAPTION  │──▶│CROSSPOST │
  │ whisper (L4)   │   │ ffmpeg   │   │ moments  │   │ clip.py  │   │ +hashtag │   │ mint Post│
  └────────────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘   └────┬─────┘
                                                                                        │
                                        ╔═══════════════════════════════════════════════▼═════════╗
                                        ║  Post is BORN `awaiting_approval`  — INV-08              ║
                                        ║  NOTHING publishes without an explicit operator approval ║
                                        ╚═══════════════════════════════════════════════╤═════════╝
                                                                                        │ approve_post
                                                                                        ▼
   ┌────────────────────────────────────────────────────────────────────────────────────────────┐
   │  PUBLISH  (_publish_one — the SOLE network-POST caller)                                     │
   │    CLAIM  queued→submitting, COMMITTED  ── BEFORE ANY NETWORK I/O ──  (the durability point)│
   │    NETWORK  lock-free:  ffmpeg shrink → R2/Postiz upload → POST /posts   ⚠ NO IDEMPOTENCY KEY│
   │    FINALIZE  merge only _NET_POST_FIELDS into a FRESHLY loaded ledger                        │
   └────────────────────────────────────┬───────────────────────────────────────────────────────┘
                                        ▼
                            ┌───────────────────────┐        ┌──────────────┐
                            │ RECONCILE             │───────▶│ TRACK        │
                            │ owns submitting /     │        │ metrics →    │
                            │ submitted /           │        │ analyzed     │
                            │ needs_reconcile       │        └──────┬───────┘
                            └───────────────────────┘               │
                                                                    ▼
                                                          ┌───────────────────┐
                                                          │ LEARN (adjust)    │
                                                          │ amplify / retire  │
                                                          └─────────┬─────────┘
                                                                    │
        ◀───────────────────────────────────────────────────────────┘
        THE LOOP CLOSES: `adjust → moments` biases the next selection.
        The DOMAIN loop is intentional and IS the product (CERTAIN).
        At MODULE level it is realized WITHOUT an import cycle — the domain
        core is ACYCLIC (124 of 127 modules are singleton SCCs). Whether the
        mutual-import WIRING is intentional is UNKNOWN (UNK-C5-4).
```

### 2.2 The subsystem topology (**19** subsystems, 127 modules, total partition)

> **What a subsystem is.** A partition class over the 127 modules, assigned by architectural responsibility.
> Total and disjoint: every module is in **exactly one**. **Count: 19 (S01–S19); S16 is Studio.**
>
> 🔴 **What a subsystem is NOT.** **Subsystems are an ANALYTIC OVERLAY imposed by Cycle 5.** They are not
> declared anywhere in the repository, no package boundary corresponds to them, and **nothing enforces them.**
> The modules and the import edges are **facts**; the grouping is a **model** — and a careless grouping
> *manufactures* cycles that do not exist (Cycle 5 caught itself doing exactly that; see
> [`CYCLE5_CORRECTIONS.md`](CYCLE5_CORRECTIONS.md) `C5-SC-2`).

```
                    ┌──────────────────────────────────────────────┐
   ENTRY            │  S17 cli/daemon   S16 studio (149 routes,    │
                    │                       0 authenticated)       │
                    └───────────────────┬──────────────────────────┘
                                        ▼
   ORCHESTRATION    ┌──────────────────────────────────────────────┐
                    │  S15 pipeline — advance() is the WHOLE engine │
                    └───────────────────┬──────────────────────────┘
                                        ▼
   ╔════════ THE DOMAIN CORE — A 7-SUBSYSTEM CYCLE (SUBSYSTEM level, NOT an import cycle) ════╗
   ║                                                                                         ║
   ║    S04 registry ──▶ S10 caption ──▶ S14 learning ──▶ S08 selection ──▶ S09 render      ║
   ║        ▲                 ▲                │                 │              │            ║
   ║        │                 │                ▼                 ▼              │            ║
   ║        └───── S13 metrics ◀────────── S07 agent-gate ◀──────┴──────────────┘            ║
   ║                                                                                         ║
   ║    adjust → moments  CLOSES the loop that  moments → moment_hook_learning  OPENS.       ║
   ║                                                                                         ║
   ║    IS:     REAL COUPLING. You cannot extract S08 without S14. Every closing edge was    ║
   ║            traced to a real module edge and verified.                                   ║
   ║    IS NOT: AN IMPORT CYCLE. It is produced by GROUPING distinct modules into subsystem  ║
   ║            nodes. At module level these are all SINGLETON SCCs — the domain core is     ║
   ║            ACYCLIC, and this carries NONE of the load-order fragility of a real cycle.  ║
   ╚═════════════════════════════════════════════════════════════════════════════════════════╝
                                        │
   SERVICES         ┌──────────────────────────────────────────────┐
                    │ S05 ingestion  S06 media  S11 crosspost      │
                    │ S12 publish    S18 health  S19 maintenance   │
                    └───────────────────┬──────────────────────────┘
                                        ▼
   PERSISTENCE      ┌──────────────────────────────────────────────┐
                    │  S03 ledger (SQLite, 8 maps, v11)            │
                    └───────────────────┬──────────────────────────┘
                                        ▼
   FOUNDATION       ┌──────────────────────────────────────────────┐
                    │  S01 ids/errors/models/log   S02 config      │
                    │  ⚠ config: depth 0, fan-in 82 — and it       │
                    │    reaches UP to accounts + meta_graph       │
                    │    via LAZY imports.                         │
                    └──────────────────────────────────────────────┘
```

### 2.3 Lock domains

| | Primitive | Guards | Held across I/O? |
|---|---|---|---|
| **L1** ledger | SQLite `BEGIN IMMEDIATE` (30 s → typed `LockBusyError`) | all 8 entity maps | **no** — by design |
| **L2** control files | `fcntl.flock` per `<file>.lock` | `accounts.json`, `personas.json`, `hashtag_*` | no |
| **L3** run lease | `fcntl.flock LOCK_NB` on `.run.lock` | the converge loop | **yes** — the whole pass |
| **L4** stage lock | `fcntl.flock` per `(stage, source)` | one producer per stage | **yes** — across whisper/ffmpeg |
| **L5** `ledger.lock` | `fcntl.flock` | **nothing** | — |

> 🔴 **L1 and L2 do not exclude each other** (`COUP-01`). And **L5 excludes nothing at all**: it is taken
> *only* by `restore_snapshot`, which then `os.replace`s the database file. **The sibling six lines above it
> (`Ledger.snapshot`) takes the correct lock.**

---

## 3. Ownership — the one-line answers

| Asset | Authoritative owner |
|---|---|
| entity identity | `ids.py` — SHA-1[:12], content-addressed. **Builtin `hash()` is banned** (PEP 456 salts it). |
| the ledger | `ledger.py` (facade) + `ledger_sqlite.py` (store) |
| `accounts.json` | `accounts.py` — **and the OPERATOR, by hand.** That second writer is by design, and it is the door `RC-3` comes through. |
| the network POST | `post/run.py:296` `_publish_one` — **the sole caller** (`INV-09`) |
| `published → analyzed` | `track.py:193` |
| `MomentState.retired` | `adjust.retire` — **IRREVERSIBLE** (`reconcile_moments` refuses to un-retire) |
| `FANOPS_LIVE=1` | `golive._dual_write` — **the only setter** (`INV-18`, AST-confirmed: exactly 2 `os.environ` writes in `src/`) |
| **the lifecycle of a remote submission** | 🔴 **NOBODY.** See §5. |

---

## 4. What the architecture gets right

Recorded first, and deliberately, because an audit that lists only faults misrepresents the system.

1. **Claim-before-network.** The `queued → submitting` flip is **committed before any network I/O**. A crash
   mid-POST leaves `submitting`, which `publish_due` never re-drives. That is *exactly* right for a backend
   with **no idempotency key**.
2. **The network-ambiguity decision table** (`ConnectTimeout` → retry; `5xx` → park, never re-POST; `401` →
   halt the run). It is correct on both backends, and `ZernioPoster` is byte-for-byte symmetric with
   `PostizPoster`. **This is the system's best engineering.**
3. **Content-addressing everywhere** + `setdefault` ⇒ **a whole-pipeline replay is a no-op.**
4. **Heavy work is warmed lock-free, then the reduce only flips state.** A rolled-back pass therefore loses
   only cheap in-memory transitions; the next pass fingerprint-*skips* onto the warm artifacts.
5. **No auto-publish.** Enforced by a guard, and it holds.
6. **Fail-open with a logged breadcrumb**, ratcheted in CI (`test_swallow_ratchet.py` fails a *new* silent
   broad `except`).

---

## 5. What the architecture gets wrong — the four structural holes

Full register: [`kb/risks.json`](kb/risks.json). These are **shapes**, not incidents.

### 🔴 `AR-01` — the submission lifecycle has no owner
`publish` **creates** posts in a state it will never re-drive; `reconcile` is that state's **sole reader** and
cannot always terminate it. **Three independent exclusions** each strand a post forever (a raising poll, a
real token, any non-empty `error_reason`). **Proven: still `submitting` at +100 000 h.** Reachable by an
*ordinary operator workflow* — a platform-rejected post, sent back to fix the caption, re-approved.

### 🔴 `AR-02` + `AR-03` — the restore race, and the test that locks it in
`restore_snapshot` takes a lock in **no domain**, then `os.replace`s the DB. A live writer's `commit()`
**succeeds**, its data is discarded, **and its deferred media unlinks proceed** — deleting real `.mp4` files
whose rows the restore brings back. **The ledger comes back claiming the media exists. The file is gone.**

And **a green CI test asserts that outcome and calls it correct.** Its comment names a mechanism that does
not exist; its assertion passes *because of* the data loss. **Any correct fix turns it red.**

> **`os.replace` of a SQLite file is fundamentally incompatible with SQLite's inode locking.** No lock
> acquired beforehand can protect it. The root fix is to *not replace the file*.

### 🔴 `AR-04` — import-time acyclicity is *purchased*, not designed

**First, the three graphs, because conflating them is itself an architectural error:**

| | What it is | What a cycle in it *means* |
|---|---|---|
| **G1** compile-time import graph | module-level imports | **A hard load-order constraint.** A cycle here can become an `ImportError` at process start. **G1 is NOT a DAG** — it has one non-trivial SCC (`personas`×3). |
| **G1c** its **SCC-condensation** | G1 with each SCC collapsed to a node | **A DAG by construction.** This — *and only this* — is the sense in which FanOps has an **11-level** layered compile-time structure. |
| **G2** static **potential-dependency** graph | compile **∪** lazy | 🔴 **A static OVER-APPROXIMATION of runtime dependency.** A lazy import materializes *only if its function is called*. G2 is a **superset of the runtime call graph, which was never derived.** Its SCCs **bound** blast radius; they do not **establish** it. |

**107 of 323 lazy imports point to an equal-or-higher level — 56 of them *strictly upward* (true layering
inversions), 51 lateral.** G1c is an 11-level DAG **only because those imports are deferred to call time.** In
**G2**: a **45-module SCC**, and all 19 subsystems in one — *potentially*.

> **`config` — level 0, fan-in 82, the most-depended-on module in the system — reaches *up* to `accounts`
> (level 2) and `meta_graph` (level 3).** Module init order is load-bearing and **nothing enforces it**: no
> test, no lint rule, no layer declaration. Hoisting any one of the **56** strictly-upward imports — *a change
> that looks like a cleanup* — breaks the process at start.
>
> **How many of the 56 are deliberate is UNKNOWN** (`UNK-C5-2`). It is not guessed.

### 🔴 `AR-09` — the operator is an unmodelled second writer
`accounts.json`, `tuning.json` and `.env` are **documented as hand-editable**. The *write* boundaries are
sound; the *load* boundaries have no validation. So **every load-boundary gap is reachable by design.** A
hand-edited `"postiz "` (trailing space — visually identical in any UI, diff, or JSON dump) resolves to a
**`DryRunPoster` on a live system**, silently.

---

## 6. The recurring defect shape — and how to find the next one

Five cycles have now converged on **one** shape:

> **The doc names a mechanism that does not exist, while the property survives via a different one.**

It has appeared in the docs (`INV-01`, `INV-02`, `INV-03`, `INV-05`, `INV-07`), in the **comments**, and — most
dangerously — in the **test layer** (`AR-03`).

**The mechanical tell, four for four:** a load-bearing assumption written as a statement of fact sits at the
exact site of a live bug.

| Site | The comment | Reality |
|---|---|---|
| `reconcile.py:76` | *"its status **WILL** resolve, never escalated"* | It does not, when the platform deleted the post. |
| `providers.py:53` | *"**no** live account routes to an unknown backend"* | A hand-edit — the *documented* channel — routes exactly there. |
| `test_ledger_sqlite_store.py:183` | *"restorer **blocked on flock** held by writer"* | Never blocked. Measured: **0.001 s**. |
| `ledger.py:487` | deferred unlink is *"correct: a rolled-back txn **never** deletes a file it did not drop"* | The txn **commits into an orphan**, so it is never rolled back. |

> **Carry this forward:** grep the tree for comments containing **"never", "cannot", "WILL", "no … routes
> to"**. Each is a hypothesis the author did not test. And **for every invariant claimed to be protected by a
> test, read the test's *assertion*, not its *name*.**

---

## 7. Coverage, honestly

| | |
|---|---|
| Modules in tree | **127** |
| **Structural coverage** (every ledger txn, lock, network call, subprocess, `mkdtemp`, env read/write enumerated) | **127 / 127 = 100 %** |
| **Behavioural coverage** (business logic actually read) | **~100 / 127 ≈ 79 %** |
| Modules never read by any of the 5 cycles | **~27** |

**What the census *proves* about the 27 unread modules:** they contain **no ledger writer, no lock, no network
call, no status client, no subprocess.** That is a *structural guarantee*, not an assumption — and it is a
strictly stronger statement than Cycles 1–4 could make. **Their business logic remains unread** (`UNK-C5-3`).

---

## 8. Operational constraint — five cycles running

> **`OPS-001` is STILL ENGAGED.** The orchestration gate refused, *this cycle*, both a read-only shell command
> that merely *named* `.orchestration/state/` and a `general-purpose` subagent spawn.
>
> **Cycles 1, 2, 3, 4 and 5 have all been executed single-threaded.** The independent verifier Cycle 5
> attempted to spawn — to refute its own dependency claims — **was refused.** Cycle 5 fell back to
> cross-validation (the same AST pass reproduced four of Cycle 3's censuses exactly), which is **weaker than
> an independent agent, and is recorded as such.**
>
> Disengage is `orchestrate.py stop` — **an operator action, not a code change.** It remains the single
> largest constraint on this audit's throughput.

---

## 9. How to use this knowledge base

**Answer an architectural question without re-reading the tree:**

| Question | File |
|---|---|
| *What subsystems exist, and what is in them?* | [`kb/subsystems.json`](kb/subsystems.json) |
| *If I change X, who breaks?* | [`kb/dependencies.json`](kb/dependencies.json) → `hubs_fan_in_compile` |
| *Who is allowed to write this field?* | [`kb/ownership.json`](kb/ownership.json) |
| *What states can this entity be in, and who moves it?* | [`transitions.json`](transitions.json) *(Cycle 2, canonical — re-verified by Cycle 5)* |
| *What is persisted, and how is it recovered?* | [`kb/persistence.json`](kb/persistence.json) |
| *What talks to the outside world?* | [`kb/side_effects.json`](kb/side_effects.json), [`kb/integrations.json`](kb/integrations.json) |
| *What does this env var do?* | [`kb/configuration.json`](kb/configuration.json) *(73 vars, every read site)* |
| *What is actually guaranteed — and by what mechanism?* | [`kb/invariants.json`](kb/invariants.json) |
| *What is structurally fragile?* | [`kb/risks.json`](kb/risks.json) |
| *What don't we know?* | [`kb/unknowns.json`](kb/unknowns.json) |
| *How do I know any of this is true?* | [`kb/evidence.json`](kb/evidence.json) |

**Cycle 4's remediation plan is unchanged and still authoritative** —
[`IMPLEMENTATION_SEQUENCE.md`](IMPLEMENTATION_SEQUENCE.md), [`REMEDIATION_OPTIONS.md`](REMEDIATION_OPTIONS.md),
[`CHANGE_INTERFERENCE_MATRIX.md`](CHANGE_INTERFERENCE_MATRIX.md). **Cycle 5 designed no fixes and touched no
production code.**
