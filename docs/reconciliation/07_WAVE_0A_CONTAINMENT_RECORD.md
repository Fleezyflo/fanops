# 07 — Wave 0A Live Containment Record

> **Status: COMPLETE. Both approved containments applied and verified.**
> Operator approval `APPROVE A AND B` was received at the Section 7 gate. Containment A (66 Zernio-routed
> posts parked) and Containment B (`FANOPS_CORPUS_AUTO=0`) are applied, verified, and reversible.
> **No root fix was performed. `ACT-01` and F-A/F-B/F-C remain UNFIXED (§17).**

---

## 1. Document Control

| Field | Value |
|---|---|
| **Title** | 07 — Wave 0A Live Containment Record |
| **Path** | `docs/reconciliation/07_WAVE_0A_CONTAINMENT_RECORD.md` |
| **Purpose** | The reversible record of Wave 0A live containment: evidence gate, mechanism proofs, pre-images, mutations, verification, rollback. |
| **Governing baseline** | `docs/reconciliation/05_FINAL_INTEGRATION_AND_CLOSEOUT.md` (integration baseline; its live counts remeasured, not trusted) |
| **Frame open** | **2026-07-16T21:25:41Z** |
| **Frame at gate** | **2026-07-16T21:40Z** |
| **Mutation A applied** | **2026-07-16T21:52:54Z** |
| **Mutation B applied** | **2026-07-16T21:53:24Z** |
| **Frame close** | **2026-07-16T21:58:57Z** |
| **Host TZ** | `Asia/Dubai` (UTC+04) — `/var/db/timezone/zoneinfo/Asia/Dubai`; `TZ` unset |
| **Operator TZ** | `FANOPS_OPERATOR_TZ = America/New_York` (**≠ host TZ** — publish_hour/publish_dow stamp in operator TZ) |
| **Repository root** | `/Users/molhamhomsi/Moh Flow Fanops` |
| **Scope** | Containment A (park Zernio-routed queue) + Containment B (`FANOPS_CORPUS_AUTO=0`). Nothing else. |
| **Mutations performed** | **Exactly two, both approved:** (A) 66 posts `queued`→`awaiting_approval`; (B) one line appended to `/Users/molhamhomsi/FanOps/.env`. Nothing else. |

### 1.1 Deviation from the baseline report

Report 05 §1.2 recorded the live ledger as `queued 67 · failed 3` at 20:31Z and predicted the next post
would fire at **2026-07-16T21:27Z** and become the **fourth failure**.

**That prediction was observed live during this frame.** See §3.3. The report was correct.

---

## 2. Authorization Received

| Item | Status |
|---|---|
| Read-only inspection | **Authorized** by prompt §1 (immediate) |
| **Containment mutation A** (park Zernio queue) | **APPROVED** — operator replied **`APPROVE A AND B`** at the §7 gate |
| **Containment mutation B** (`FANOPS_CORPUS_AUTO=0`) | **APPROVED** — same reply |
| Daemon restart | **NOT REQUESTED, NOT PERFORMED** — proven unnecessary (§10.2, §13) |
| Record creation (this file) | **Authorized** by prompt §1 |

The approval was one of the four exact tokens required by prompt §7. No mutation preceded it.

---

## 3. Pre-Mutation Operational Frame

*(All values MEASURED this frame. None carried over from report 05.)*

### 3.1 Repository

| Property | Value | Method |
|---|---|---|
| Branch | `main` | `git rev-parse --abbrev-ref HEAD` |
| HEAD | `6d21749ffc49c77383f537d93b028cca0d69a447` | `git rev-parse HEAD` |
| `origin/main` (remote) | `6d21749ffc49c77383f537d93b028cca0d69a447` | `git ls-remote origin refs/heads/main` — **read-only; no fetch, no ref mutated** |
| Ahead / behind | **0 / 0** | `git rev-list --left-right --count origin/main...HEAD` |
| Working tree | Clean except untracked `docs/constitution/`, `docs/reconciliation/` | `git status --porcelain=v1` |

### 3.2 Runtime

| Property | Value |
|---|---|
| Daemon | `com.fanops.run` **PID 9121**, `fanops run --loop --interval 600`, started **2026-07-16T16:49:48Z** |
| Daemon heartbeat SHA | `6d21749ffc49c77383f537d93b028cca0d69a447` — **== HEAD, zero code drift** |
| Keeper | `com.fanops.keeper` **loaded, not resident** (`StartInterval 120`, no `KeepAlive`) — not-running is healthy |
| Studio | `com.fanops.studio` **PID 9123**, `127.0.0.1:8787` |
| Daemon liveness at gate | `STAT S`, `%CPU 0.0` — **sleeping between passes, not hung** |
| Tick cadence (heartbeat) | 20:55:46.6 → 21:05:48.0 → 21:15:49.6 → 21:25:51.6 → **21:38:32.0** (≈601s + pass duration) |
| Active data root | `/Users/molhamhomsi/FanOps/MohFlow-FanOps` |
| Root resolution | Daemon plist sets **no `FANOPS_ROOT`**; `WorkingDirectory=/Users/molhamhomsi/FanOps` → `Config.root_source = "cwd"` → `base = root/"MohFlow-FanOps"` (`config.py:144-155`) |
| Active ledger | `00_control/ledger.sqlite` (`SqliteLedgerStore`) |
| Ledger schema version | **11** (`ledger_meta.schema_version`) |
| Ledger rows | **1,063** — posts 347 · clips 347 · moments 347 · tag_log 10 · sources 7 · batches 5 |

### 3.3 Ledger post states — and the observed fourth burn

| Time (UTC) | `awaiting_approval` | `queued` | `failed` | Note |
|---|---|---|---|---|
| 20:31Z (report 05) | 277 | 67 | 3 | historical |
| **21:27:08Z** (this frame, snapshot 1) | **277** | **67** | **3** | matches report 05 |
| **21:39:00Z** (this frame, snapshot 2) | **277** | **66** | **4** | **the 4th burn landed** |

**The fourth failure was observed live, not inferred.**

- `post_0a12cff53619`, scheduled `2026-07-16T21:27:00.272298Z`, `tiktok/backlikeineverleft` → `zernio`.
- Transitioned `queued` → `failed` during the daemon pass **21:35:51Z → 21:38:32Z**.
- `error_reason`: `publish failed: Zernio upload failed (405) — body withheld`
- `submission_id`: `fanops_41e15dc9f2ca` · `media_id`: `None` · `public_url`: `None` · `published_at`: `None`
- Its clip `clip_564a91798b1a` was re-encoded for upload mid-pass —
  `shrink_post … was=4237875 now=2908332 crf=28` at 21:38:27Z. **Media was prepared and sent; the platform rejected it.**
- **No 405 appears in `daemon.err`** (7.5 MB) — corroborating report 05 `CAN-027`: the terminal branch sets
  `error_reason` and breaks **without logging**. The only record of the burn is the ledger row.

**Incidental finding (not acted on, not in scope):** the same pass logged
`[postiz_lifecycle] ensure_up skipped (TimeoutExpired): … postiz-ondemand.sh ensure timed out after 150 seconds`.
The publish stage spends **150 s per pass with a due post** attempting to start a Postiz container that never
comes up — even though the due post routes to **zernio**, not postiz. This is why the pass took 159 s vs the
usual ~0.8 s. Logged here as an observation only; **out of Wave 0A scope**.

### 3.4 Publish funnel

| Property | Value |
|---|---|
| Ever published — current ledger | **0 of 347** (`published_at` present: 0; `public_url` present: 0) |
| Published archive (`06_published/`) | **73 records** — **37** with `published_at`, **55** with a real `public_url` |
| Archive day-buckets | `2026-06-29`, `2026-06-30`, **`2026-07-04`**, **`2026-07-05`** |
| **Latest successful archived publish** | **`2026-07-05T02:44:50.642621Z`** |
| Earliest archived publish | `2026-06-29T09:42:47.954369Z` |

> **Correction to report 05 §3.2.** That row lists the archive as `06_published/{2026-06-29,2026-06-30}`.
> The archive actually holds **four** day-buckets spanning **2026-06-29 → 2026-07-05**. This does not change
> `CAN-025`'s conclusion (it already states the upload worked 06-29→07-05); it corrects the §3.2 inventory row.

### 3.5 Queued / failed by platform, handle, backend

Backend routing is per-channel from `accounts.json` `backends` (D12: `FANOPS_POSTER` is **absent**, so
`accounts.json` is the sole publish truth).

| State | Platform | Handle | Backend | Count |
|---|---|---|---|---|
| `queued` | tiktok | `backlikeineverleft` | **zernio** | **66** |
| `failed` | tiktok | `backlikeineverleft` | **zernio** | **4** |
| `awaiting_approval` | instagram | `markmakmouly` | postiz | 76 |
| `awaiting_approval` | instagram | `cisumwolfhom` | postiz | 67 |
| `awaiting_approval` | instagram | `perca.late` | postiz | 67 |
| `awaiting_approval` | tiktok | `hrmny-blog` | **zernio** | 66 |
| `awaiting_approval` | tiktok | `backlikeineverleft` | **zernio** | 1 |

**Every queued post is Zernio-routed.** There is no queued post on any other backend. The target set for
containment A is therefore the entire queue.

`awaiting_approval` holds 67 further Zernio-routed posts — **not publish-eligible, not targeted, not touched**
(prompt §9.6).

### 3.6 Next five queued due times (post-burn, at 21:39:00Z)

| # | Scheduled (UTC) | Post ID |
|---|---|---|
| 1 | **2026-07-16T23:48:00.272298Z** | `post_0a570125b88b` |
| 2 | 2026-07-17T02:35:00.272298Z | `post_17ae6436c3bf` |
| 3 | 2026-07-17T13:00:00Z | `post_1d175ca0039d` |
| 4 | 2026-07-17T13:00:00Z | `post_1fb3ec891c24` |
| 5 | 2026-07-17T13:00:00Z | `post_214da415f911` |

**Past-due at the gate: 0.** Queue span: `2026-07-16T23:48:00Z` → `2026-07-23T17:16:00Z`.

### 3.7 Latest failed records (all four)

| Post ID | Scheduled (UTC) | Error |
|---|---|---|
| `post_04b29c9f7f2d` | 2026-07-16T13:31:00.272298Z | `publish failed: Zernio upload failed (405) — body withheld` |
| `post_07e45c69ac0d` | 2026-07-16T16:03:00.272298Z | `publish failed: Zernio upload failed (405) — body withheld` |
| `post_0943840705ce` | 2026-07-16T18:57:00.272298Z | `publish failed: Zernio upload failed (405) — body withheld` |
| **`post_0a12cff53619`** | **2026-07-16T21:27:00.272298Z** | **`publish failed: Zernio upload failed (405) — body withheld`** ← **observed live this frame** |

### 3.8 Hashtag state

| Property | Value | Method |
|---|---|---|
| **Meta budget remaining** | **0 of 30** | `meta_graph.budget_remaining(cfg)` — the code's own pure-read function |
| Budget window | 7 days rolling (`_BUDGET_LIMIT=30`, `_BUDGET_WINDOW_DAYS=7`) | `meta_graph.py:126-127` |
| Unique tags in window | **30 of 30** — all burned 2026-07-12T17:25:18Z → 17:27:19Z (a **2-minute** window) | `hashtag_budget.json` |
| **First slot frees** | **2026-07-19T17:25:18.530741Z** | oldest in-window ts + 7 d |
| **All 30 free by** | **2026-07-19T17:27:19.901298Z** | newest in-window ts + 7 d |
| Personas | **8** | `personas.json` |
| **Curated corpus tags (total)** | **22** | `personas.json` |
| **Evidence store** | **18 tags**, `reach: {}` — **channel unfed** | `hashtags.json` |
| Corpora refresh throttle | marker `.corpora_refresh.json` mtime `2026-07-16T10:46:39.874648Z`, age 10.8 h, `max_age_s` 43200 (12 h) | `persona_research.py:216` |
| **Next corpora refresh DUE** | **2026-07-16T22:46:39.874648Z** | marker mtime + 12 h |

Per-persona corpus (`id  corpus/meta`):

| Persona | corpus | meta entries | meta sources |
|---|---|---|---|
| `craft-curator` | 3 | 3 | `pinned:3` |
| `underground-zine` | 3 | 3 | `pinned:3` |
| `burner-bold` | 3 | 3 | `pinned:3` |
| `credibility-first` | 2 | 2 | `pinned:2` |
| `controversy` | 2 | 2 | `pinned:2` |
| `edutainment` | 3 | 3 | `pinned:3` |
| `cliffhanger` | 2 | 2 | `pinned:2` |
| **`hype-vibe`** | **4** | **0** | **`{}` — 4 tags with NO meta** |
| **Total** | **22** | 18 | |

`hype-vibe`'s 4 meta-less tags are the **live fingerprint of F-A** (§17): absent meta is read as *pinned*.

### 3.9 Control-file hashes at the gate (integrity baseline)

| File | SHA-256 | Size | mtime (UTC) |
|---|---|---|---|
| `personas.json` | `302f0d27defff4e5e5419f2552bd42ce897be2b7afefe4bef540ed0a9e34420b` | 7266 | 2026-07-16T13:04:24.073867Z |
| `hashtags.json` | `5173804fff7739d8a7c6360945e5772be622882e8b2d9698797eb15bda1d9b29` | 335 | 2026-07-16T13:17:09.011465Z |
| `hashtag_budget.json` | `d47493c81f18284f8dc500f1f823f7748c860b73485d5e95833a8a2b3a7fbf09` | 2668 | 2026-07-12T17:27:35.241956Z |
| `.env` | `7b2631307077a6caf7ea3362502d60033de4dfcff722b28456204f360d6b216a` | 943 | 2026-07-13T16:59:02Z |
| R4 rollback snapshot `personas.json.r4-bak-20260716T130424Z` | *(present)* | **5369** | 2026-07-12T17:27Z |

---

## 4. Approved Scope

Operator approval: **`APPROVE A AND B`**.

| Approved | Action | Applied |
|---|---|---|
| **A** | Park the 66 Zernio-routed `queued` posts → `awaiting_approval` via `Ledger.unapprove_post` in one transaction | ✅ 2026-07-16T21:52:54Z |
| **B** | Append `FANOPS_CORPUS_AUTO=0` to `/Users/molhamhomsi/FanOps/.env` via `autopilot.set_env_var` | ✅ 2026-07-16T21:53:24Z |

Nothing outside this table was mutated (§20).

---

## 5. Secret-Handling Attestation

The `.env` evidence gap recorded in report 05 §1.2 (`Q-02`, `Q-03`) — *"permission-denied … reproduced 3×"* —
is **RESOLVED this frame**.

**Why prior agents were denied:** they looked for `.env` at the **repository root**
(`/Users/molhamhomsi/Moh Flow Fanops/.env`), where **no such file exists**. The live `.env` is at
**`/Users/molhamhomsi/FanOps/.env`** — the daemon's `WorkingDirectory`, which is also `Config.root`
(cwd fallback), and `cli.py` loads exactly `cfg.root / ".env"`. It was readable on first attempt.

**Attestation:**

- The eight Section-3-approved keys, and only those, were read for value.
- Every other key in the file was enumerated **by name only**; **no value was read into the transcript**,
  by construction — the reader script filters on an allow-list before printing.
- `ZERNIO_URL` is **absent from the file** — nothing to redact.
- **No API key, bearer token, access token, secret key, password, cookie, credential, or private webhook URL
  was printed, logged, copied, or written to any file.**
- `.env` contents are **not** included in this record. Only the file's SHA-256, size, permissions, and
  ownership are recorded.
- Key names withheld-by-value and present in the file: `POSTIZ_URL`, `POSTIZ_API_KEY`, `META_GRAPH_TOKEN`,
  `META_IG_USER_ID`, `FANOPS_HASHTAG_TRENDS`, `FANOPS_CREATIVE_VARIATION`, `ZERNIO_API_KEY`,
  `FANOPS_REALISTIC_CADENCE`, `FANOPS_RESPONDER`, `FANOPS_ZERNIO_MAX_UPLOAD_MB`, `FANOPS_MEDIA_PUBLIC_BASE`,
  `FANOPS_LLM_TRANSPORT`.

### 5.1 The eight approved values

| Key | Value | Source | Consequence |
|---|---|---|---|
| **`FANOPS_LIVE`** | **`1`** | `.env` | **Live publishing is ON.** Confirms `CAN-021` **directly** — no longer an inference from the dryrun-impossibility argument. |
| **`FANOPS_POSTER`** | **ABSENT** | — | Per D12, per-channel `accounts.json` routing is the sole publish truth. Consistent with `golive` never writing it. |
| **`FANOPS_SMART_FRAMING`** | **ABSENT → defaults ON** | `reframe.py` @property | **Resolves `Q-02`.** Report 05 `OPD-03` warned: *"if `0`, S1–S5 are inert and every 'active' claim collapses."* **It is not `0`.** S1–S5 are **active in code**. The reframe finding stands as report 05 states it (code-active, never applied). |
| **`FANOPS_CORPUS_AUTO`** | **ABSENT → defaults ON (`True`)** | `config.py:435-438` | **Resolves `Q-03`. The 07-19 time bomb IS ARMED.** |
| **`FANOPS_CORPUS_TARGET`** | **ABSENT → defaults `12`** | `config.py:441-448` | **Resolves `Q-03`. F-C's precondition holds** (12 × 3 posting personas = 36 seeds > 30 budget). |
| **`FANOPS_ROOT`** | **ABSENT from `.env`** | — | Daemon resolves root by **cwd fallback** → `/Users/molhamhomsi/FanOps`. Confirms `FI-OPS-003`. |
| **`FANOPS_OPERATOR_TZ`** | **`America/New_York`** | `.env` | **≠ host TZ (`Asia/Dubai`).** `publish_hour`/`publish_dow` stamp in operator TZ. |
| **`ZERNIO_URL`** | **ABSENT from `.env`** | — | **Materially narrows `Q-01`.** See §5.2. |

### 5.2 `ZERNIO_URL` is absent — this narrows the 405 root cause

Report 05 `Q-01` left the 405 undetermined between **"endpoint moved server-side"** and
**"stale `ZERNIO_URL`"**, and `OPD-03` listed `ZERNIO_URL` as *"an equally viable 405 cause"*.

**`ZERNIO_URL` is not set at all.** (`ZERNIO_API_KEY` **is** present, 67 chars — so the integration is
configured, just not URL-overridden.) The client therefore uses its **in-code default**, which is at HEAD and
identical to the URL that worked 2026-06-29 → 2026-07-05.

**Therefore: "a stale operator-set `ZERNIO_URL`" is eliminated as a cause.** The remaining hypothesis is a
**server-side contract drift** against the reverse-engineered contract the client's own docstring marks
*"DISCOVERED LIVE 2026-06-29"* (`CAN-026`).

> This is **evidence only**. Per prompt §2, `post/zernio.py` was **not read for repair, not modified, and not
> probed**. The permanent fix is out of scope.

---

## 6. Queue-Parking Mechanism

### 6.1 Publish eligibility

| Question | Answer | Evidence |
|---|---|---|
| Which states are publish-eligible? | **`queued` only** | `post/run.py:475` — `due = [post for post in led.posts_in_state(PostState.queued) if _due_or_fail(cfg, post, cutoff)]` |
| Which state prevents publishing while preserving the record? | **`awaiting_approval`** | `models.py:105-110` — *"a crossposted post is BORN here … It is NOT publishable — publish_due/publish_now iterate only `queued`, so an unapproved post is structurally never submitted (even on a live backend)."* |
| Is `rejected` a better park? | **No.** It is an operator **discard** — *"Terminal, never fires, kept as a record"* (`models.py:113-116`). Parking is not discarding. | `models.py:113-116` |

### 6.2 The supported mechanism

**`Ledger.unapprove_post(uid)`** — `ledger.py:607-610`:

```python
def unapprove_post(self, uid: str) -> None:
    p = self.posts.get(uid)
    if p is not None and p.state is PostState.queued:   # send an approved-but-unsent post back to review
        self.posts[uid] = p.model_copy(update={"state": PostState.awaiting_approval})
```

This is a **first-class, named, tested domain method** whose entire purpose is this transition. It is the
mechanism behind the Studio's **"Send back to Review"** control on both the Review and Schedule tabs
(`app_routes_review.py:140`, `app_routes_schedule.py:58`).

### 6.3 Mechanism audit (prompt §5 questions 4–9)

| # | Question | Answer |
|---|---|---|
| 4 | Preserves post ID? | **Yes** — keyed by `uid`; `model_copy` retains `id`. |
| 4 | Preserves scheduled time? | **Yes** — `update={"state": …}` touches **only** `state`. Every other field is carried by `model_copy`. |
| 4 | Preserves backend? | **Yes** — backend is not a post field; it is resolved from `accounts.json` at publish time. Untouched. |
| 4 | Preserves media linkage? | **Yes** — `parent_id`, `media_urls`, `media_id`, `render_id` untouched. |
| 4 | Preserves caption? | **Yes** — `caption`, `hashtags` untouched. |
| 4 | Preserves account? | **Yes** — `account`, `account_id` untouched. |
| 4 | Preserves prior failure history? | **Yes** — `error_reason`, `submission_id` untouched. (All 66 targets carry `error_reason: None` — no history to lose.) |
| 4 | Requeue capability? | **Yes** — `Ledger.approve_post` is the exact inverse. See §16. |
| 5 | Tests? | **Yes, five:** `test_post_approval.py:99` `test_unapprove_post_returns_queued_to_awaiting`; `test_post_approval.py:107` `test_unapprove_post_only_from_queued` (**pins the wrong-state no-op**); `test_studio_approval.py:43`, `:165`, `:178`; `test_studio_schedule_cockpit.py:87`. `test_state_liveness.py:134` pins `("fanops.ledger", "Ledger.unapprove_post", "queued -> awaiting_approval")` as a required liveness transition. |
| 6 | Triggers any external call? | **No.** Pure in-memory state change + local SQLite commit. No network, no Zernio, no Postiz, no Meta Graph. |
| 7 | Can it affect non-target posts? | **No.** Two independent guards: (a) it addresses exactly one `uid`; (b) `if p.state is PostState.queued` — a `failed` / `awaiting_approval` / `rejected` post is a **structural no-op**. |
| 8 | Atomic / transactionally bounded? | **Yes.** `Ledger.transaction` (`ledger.py:470-488`) acquires the store lock **before** `load`, holds it through a single `_save_unlocked()`, and the lock is a real SQLite `BEGIN IMMEDIATE` … `commit()` with `rollback()` on any raise (`ledger_sqlite.py:105-125`). All 66 in **one** transaction. |
| 9 | Rollback / requeue? | `Ledger.approve_post` — see §16. |

### 6.4 Concurrency with the live daemon — the decisive safety property

`Ledger.transaction` docstring (`ledger.py:471-476`):

> *"Hold the ledger lock across the WHOLE load-mutate-save cycle (AUDIT B4). Acquiring the lock here — BEFORE
> load — closes the lost-update window that the save()-only lock left open … **A second live process is
> excluded for the duration and gets a typed `LockBusyError` (bounded by timeout), never a silent overwrite.**"*

`SqliteLedgerStore.lock` (`ledger_sqlite.py:105-125`) implements this as `BEGIN IMMEDIATE`; on contention it
raises `LockBusyError` rather than proceeding.

**Consequence:** if the daemon is mid-pass, the containment aborts **cleanly and loudly**. It cannot half-apply,
and it cannot be silently reverted by a daemon save. The daemon holds the lock ~2 s per 600 s pass (~0.3 % duty
cycle), and at the gate it is `STAT S` / 0 % CPU — sleeping.

### 6.5 Preferred-order justification (prompt §5)

| Rank | Path | Verdict |
|---|---|---|
| 1 | **Supported domain/service method** | **SELECTED** — `Ledger.unapprove_post` inside one `Ledger.transaction`. |
| 2 | Supported CLI dry-run + mutation | **Does not exist.** The CLI (`cli.py:711-778`) has no unapprove/park verb. Enumerated: `status, ingest, digest, respond, reconcile, reframe, recover, advance, pull, track, map-media, verify-live, adjust, gc, amplify-variants, p4-bias, resolve, unhold, retry-source, retire-source, promote-source, retry-metrics, discover, intake, compose, doctor, config, init, health, publish-queue, audit`. |
| 3 | Supported Studio action | **Available but inferior** — `actions_approve.unapprove_post(cfg, pid)` (`actions_approve.py:80`) opens **one transaction per post** → 66 lock cycles, 66 full-ledger saves, and **not atomic across the set** (a mid-run failure leaves a partial park). Rank 1 wraps the identical domain call in **one** transaction. |
| 4 | Direct ledger service call | Rank 1 **is** the repository's own service call. |
| 5 | **Raw SQL / direct DB operation** | **REFUSED.** A supported state transition exists; prompt §5 forbids raw SQL in that case. |

---

## 7. Queue Preimage

**Target set: 66 posts.** Measured from ledger snapshot `20260716T213900Z`.

- **Target-set SHA-256** (sorted IDs, comma-joined): `c36f9107b84cba5d7c0e109b1458f0e6ea6643d55c584c467bea8f8f6f3ad596`
- **Homogeneity (verified, not assumed):** `platforms = {tiktok}` · `handles = {backlikeineverleft}` ·
  `states = {queued}` · `media_id = {None}` · `error_reason = {None}` · distinct caption hashes: **6**
- Backend for every target: **zernio** (`accounts.json` → `backlikeineverleft.backends.tiktok = "zernio"`)
- Schedule span: `2026-07-16T23:48:00.272298Z` → `2026-07-23T17:16:00.272298Z`
- Intended parked state for every target: **`awaiting_approval`**
- Full structured preimage (per-post: id, state, platform, handle, backend, scheduled_time, media refs,
  caption SHA-256, error_reason, submission_id, parent_id, batch_id, intended parked state):
  session scratchpad `wave0a/queue_preimage.json`, plus ledger snapshots
  `ledger.20260716T212708Z.sqlite` and `ledger.20260716T213900Z.sqlite` (read-only copies taken via the
  SQLite backup API on a `mode=ro` connection — **no WAL lock taken on the live database**).

### 7.1 The 66 target post IDs (schedule order)

```
post_0a570125b88b  post_17ae6436c3bf  post_1d175ca0039d  post_1fb3ec891c24  post_214da415f911  post_23def0c6f3fc
post_24d2b99d20ca  post_267b0a9b78ad  post_276725c7c0ba  post_345e68677d5f  post_3ca920472ac5  post_3e81ff0240a0
post_3f7a84d09794  post_429c48465ee3  post_43573413c646  post_45540cb8a777  post_4767fe1bffa1  post_47f54594639e
post_51f2d508529f  post_52839bea3670  post_587ca5cbdb87  post_5ddd1f82ad5a  post_5e6de729ca68  post_680f50f37274
post_6b4ce55bac0f  post_6c8a221d38c1  post_6d2b2a08fd22  post_6df98c7b5ea8  post_6e891b9daa02  post_6ef7cf8bb9ef
post_6fba5561c4ae  post_7b988562f15e  post_7cb4d8073466  post_8030d5652818  post_897ab25f5fdb  post_8ab85be8cad2
post_8c0972be75a2  post_9410fd708146  post_95c9e32100ee  post_9bc575cf9ada  post_9e9e4c3a0600  post_a3b7d089b006
post_a7ecbb950969  post_b0e0e598f3d3  post_b14918aead09  post_b271f716c05e  post_b700a741a8c5  post_b946fab9ebe0
post_bad39fcf091b  post_c7eb5789bc43  post_c98fd66d40b2  post_cada61b99077  post_cae61bfb1719  post_cb05e5a9f873
post_d250c4c0cee0  post_de425fe1fa15  post_e142d6fb8022  post_e604addfe0d2  post_e9eb69a10a8c  post_ead5e530d787
post_ed46e9f73831  post_ed9f7ad7dcaa  post_f340f8d58dbd  post_f4a5a7b71e7f  post_fba93f1451cf  post_fc1c03f03137
```

### 7.2 Explicitly NOT targeted

| Set | Count | Why excluded |
|---|---|---|
| `failed` Zernio posts | 4 | Prompt §9.4 — *"Do not alter failed posts."* Not publish-eligible. |
| `awaiting_approval` Zernio posts (`hrmny-blog` 66 + `backlikeineverleft` 1) | 67 | Prompt §9.6 — already parked; not publish-eligible. |
| `awaiting_approval` Instagram/Postiz posts | 210 | Prompt §9.5 — not Zernio-routed. |

---

## 8. Queue Mutation Result

**Applied 2026-07-16T21:52:54Z. Result: SUCCESS — 66 of 66 parked, single atomic commit.**

### 8.1 Execution

One `Ledger.transaction(cfg)`. The prompt §9.1/§9.2 re-read and abort-check were performed **inside** the
transaction, eliminating any time-of-check-to-time-of-use gap: had any target drifted, the `Abort` would have
propagated out of the `with` block and `Ledger.transaction` would have **rolled back without saving**
(`ledger.py:478-482`).

Pre-flight guards, all passed before any mutation:

| Guard | Result |
|---|---|
| Target count == 66 | ✅ |
| Target-set SHA-256 == `c36f9107b84cba…` (approved set) | ✅ |
| `cfg.ledger_path` == the live DB (root-divergence assert) | ✅ |
| Every target still present in the ledger | ✅ 66/66 |
| Every target still `state is PostState.queued` | ✅ 66/66 — **no drift since the approval block** |
| Every target still `tiktok` / `backlikeineverleft` (Zernio-routed) | ✅ 66/66 |
| In-txn post-condition: all 66 now `awaiting_approval` | ✅ |
| In-txn post-condition: **zero** posts remain `queued` ledger-wide | ✅ |

Only then did the transaction commit. **No partial application occurred; prompt §9's partial-state branch was
not reached.**

### 8.2 Mechanism fidelity

`Ledger.unapprove_post` was called once per approved ID. It is guarded by `if p.state is PostState.queued`, so
even a hypothetical stray ID could not have touched a `failed` or `awaiting_approval` post. **No external call
was made** — no Zernio, Postiz, or Meta Graph request was issued at any point.

`_drain_deferred_unlinks()` (which `Ledger.transaction` runs post-save) was verified inert beforehand: the list
is populated **only** by `_delete_moment_cascade` (`ledger.py:713`) and initialized empty per load
(`ledger.py:421`). `unapprove_post` never populates it. **No file was unlinked.**

---

## 9. Queue Postimage

Verified by full-ledger diff between snapshots `ledger.PREIMAGE.20260716T215254Z.sqlite` and
`ledger.POSTIMAGE.20260716T215254Z.sqlite` — **all 1,063 rows compared, not just the targets**.

| Check (prompt §9.9) | Expected | Actual | Verdict |
|---|---|---|---|
| Zero approved target IDs remain publish-eligible | 0 | **0** | ✅ |
| All records still exist | 1,063 | **1,063** | ✅ |
| No row added or removed | set equality | **identical key set** | ✅ |
| Rows changed | exactly 66 | **exactly 66** | ✅ |
| **Unrelated rows changed** | **0** | **0** | ✅ |
| Approved targets not changed | 0 | **0** | ✅ |
| **Rows where any field OTHER than `state` changed** | **0** | **0** | ✅ |
| Every transition | `queued`→`awaiting_approval` | **66/66** | ✅ |
| Queue count decreased by exactly the number parked | 66 → 0 (−66) | **66 → 0** | ✅ |
| Daemon remains healthy | yes | **PID 9121, `STAT S`, heartbeat continuing, SHA unchanged** | ✅ |

### 9.1 Ledger state after containment A

| State | Before | After | Δ |
|---|---|---|---|
| `awaiting_approval` | 277 | **343** | **+66** |
| `queued` | 66 | **0** | **−66** |
| `failed` | 4 | **4** | **0 — none added** |
| `rejected` | 0 | **0** | 0 — nothing discarded |
| `published` | 0 | **0** | 0 — nothing published |
| **Total** | 347 | **347** | **0** |

`277 + 66 = 343` — the parked posts are accounted for exactly. Failed IDs are the same four:
`post_04b29c9f7f2d`, `post_07e45c69ac0d`, `post_0943840705ce`, `post_0a12cff53619`.
`published_at` present: **0**. **No targeted post was published.**

**Material fields preserved.** The field-level diff proves `scheduled_time`, `caption`, `hashtags`, `account`,
`account_id`, `platform`, `parent_id`, `batch_id`, `media_urls`, `media_id`, `render_id`, `error_reason`,
`submission_id`, `aspect`, `clip_profile`, `cut_seconds`, `first_frame_kind`, `top_bias` and every other field
are **byte-identical** across all 66. Only `state` moved. **Requeue capability is fully intact.**

---

## 10. Hashtag Configuration Mechanism

### 10.1 Current effective value

**`FANOPS_CORPUS_AUTO` is ABSENT from `.env` and unset in the daemon's environment → `cfg.corpus_auto` is `True`. The loop is ARMED.**

`config.py:435-438`:
```python
@property
def corpus_auto(self) -> bool:
    v = (os.getenv("FANOPS_CORPUS_AUTO") or "").strip().lower()
    return v not in {"0", "false", "no", "off"}     # DEFAULT ON
```
Verified empirically: `cfg.corpus_auto` → `True`; `cfg.corpus_target` → `12`.

### 10.2 How configuration is read — **a material correction to the baseline**

`config.py:3` states: *".env is loaded once at process entry (`cli.main`), not in `Config.__init__`."`
**For the resident daemon loop, that docstring is misleading.**

`cli.py:1447-1458` — the `run --loop` body:

```python
while True:
    load_dotenv(cfg.root / ".env", override=True)   # operator disk truth each tick (B01 C1)
    cfg = Config(cfg.root)                          # side-effect-free; re-read after dotenv
    base_time = _fresh_run_base_time()
    ...
    time.sleep(interval)
```

| Question (prompt §6) | Answer |
|---|---|
| 2. Where is config read? | **Every daemon tick**, at the top of the loop. `load_dotenv(..., override=True)` re-reads `.env` from disk and **overrides** the process env; `Config` is then rebuilt. `corpus_auto` is a `@property` reading `os.getenv` at each access — **never cached**. |
| 3. **Does changing `.env` alone take effect?** | **YES.** `override=True` means the file **beats** the stale process env. Effective on the **next tick** (≤ ~10 min). |
| 4. **Is a daemon restart required?** | **NO.** |
| 5. Would the keeper restart or overwrite the state? | **No.** `com.fanops.keeper` runs `fanops daemon ensure` every 120 s and kickstarts **only on code-SHA drift** (`heartbeat.code` vs disk). It never writes `.env`. And a keeper kickstart would be harmless: the fresh process loads the same `.env` at `cli.py:845` **and** per-tick at `:1448` → still `0`. **The keeper cannot undo containment B.** |

> This is a genuine finding, not a restatement: report 05 §22 Wave 0 assumed `ACT-02` might need a restart.
> **It does not.** The restart gate (prompt §10) is therefore **not triggered**.

### 10.3 What `FANOPS_CORPUS_AUTO=0` actually stops (prompt §6 q6)

The **only** read site is `persona_research.py:212`, and it is the **first statement** in `refresh_corpora_if_due`:

```python
def refresh_corpora_if_due(cfg, *, max_age_s=43200, get=None, now=None) -> dict:
    if not cfg.corpus_auto:
        return {"refreshed": False, "reason": "disabled"}
    marker = cfg.control / ".corpora_refresh.json"
    ...
```

The gate precedes the throttle check, `Personas.load`, `refresh_persona_corpus`, and `apply_auto_corpus`.

| Sub-step | Disarmed by `=0`? |
|---|---|
| **Corpus harvest** (`refresh_persona_corpus` → discovery) | ✅ **Yes** — unreachable |
| **Corpus measurement** (Graph reach for corpus candidates) | ✅ **Yes** — unreachable |
| **`apply_auto_corpus`** (the writer; F-A's site) | ✅ **Yes** — unreachable |
| **All three, for the persona-corpus loop** | ✅ **Yes** |

**Precise scope limit — disclosed, not hidden.** `FANOPS_CORPUS_AUTO=0` disarms the **persona-corpus** writer.
It does **not** gate the separate **hashtag-store** refresh (`fanops_hashtags.refresh_store_if_due`, `cli.py:1066`),
which is gated by `FANOPS_HASHTAG_TRENDS` (present in `.env`; **value not read** — outside the Section-3
allow-list). That loop writes **`hashtags.json` (the store)**, never a persona corpus.

Measured against the prompt's stated containment goal —
> *"No automatic process may **add, pad, promote, repin, or prune Hashtag corpus entries**"*

— `FANOPS_CORPUS_AUTO=0` is **sufficient**: `apply_auto_corpus` is the **only** automatic writer of
`hashtag_corpus` / `hashtag_corpus_meta`, and it is unreachable behind the gate.

### 10.4 Preservation (prompt §6 q7)

The change **writes no corpus and no store**. It only prevents a future write. `personas.json`,
`hashtags.json`, and `hashtag_budget.json` are **not touched** by the disarm.

### 10.5 Verification that proves the loop is disarmed (prompt §6 q8)

1. `.env` parses with `FANOPS_CORPUS_AUTO=0`; every other key byte-identical; perms `0600` retained.
2. A fresh `Config` in a clean process reports `cfg.corpus_auto is False`.
3. **The daemon's own log line flips.** Every tick currently emits:
   `{"stage":"hashtags","outcome":"corpora_refresh_skipped","reason":"fresh"}`
   Post-change it must emit `"reason":"disabled"` — because `cli.py:1092` logs
   `corpora_refresh_skipped` with `reason=cr.get("reason")`, and the gate returns `{"reason": "disabled"}`.
   **This is a direct, in-band, self-attesting proof from the live daemon that the gate closed** — no inference.
4. `personas.json` / `hashtags.json` / `hashtag_budget.json` SHA-256 unchanged vs §3.9.

### 10.6 Timing — why this is urgent independent of 07-19

| Event | Time (UTC) | Consequence |
|---|---|---|
| **Next corpora refresh fires** | **2026-07-16T22:46:39Z** (~1 h from the gate) | Currently **harmless**: budget is 0, so harvest/measure yield nothing (the last 4 runs logged `changed:0, added:0, removed:0`). |
| **First budget slot frees** | **2026-07-19T17:25:18Z** | — |
| **All 30 slots free** | **2026-07-19T17:27:19Z** | — |
| **First refresh AFTER the budget refills** | **the first tick after 2026-07-19T17:25:18Z where the 12 h throttle has also expired** | **This is the dangerous fire.** F-A/F-B/F-C re-pad, re-pin, re-starve. |

---

## 11. Configuration Preimage

| Property | Value |
|---|---|
| **Path** | `/Users/molhamhomsi/FanOps/.env` |
| **Ownership** | `molhamhomsi:staff` |
| **Permissions** | `-rw-------` (`0600`) |
| **Size** | 943 bytes |
| **SHA-256 before change** | `7b2631307077a6caf7ea3362502d60033de4dfcff722b28456204f360d6b216a` |
| **mtime** | 2026-07-13T16:59:02Z |
| **Line endings** | LF only (no CRLF, no bare CR), file ends with LF |
| **Shape** | 17 lines · 1 blank · 2 comments · 0 `export`-prefixed · 0 duplicate keys |
| **Key `FANOPS_CORPUS_AUTO`** | **ABSENT** → `set_env_var` will **append**, mutating no existing line |
| **Effective configuration source** | `.env` at `cfg.root/.env`, re-loaded **per daemon tick** with `override=True` |
| **Restart required** | **NO** (§10.2) |
| **Backup** | ✅ **TAKEN before the edit** — `/Users/molhamhomsi/FanOps/.env.wave0a-bak-20260716T215324Z`, 943 B, `0600`, `molhamhomsi:staff`, SHA-256 identical to the pre-change file, **outside Git** |

---

## 12. Configuration Mutation Result

**Applied 2026-07-16T21:53:24Z. Result: SUCCESS.**

### 12.1 Execution

Prompt §10.1/§10.2 re-read and abort-check performed first:

| Guard | Result |
|---|---|
| `.env` SHA-256 still `7b2631307077a6ca…` (the approval-block hash) | ✅ **unchanged since approval** |
| `FANOPS_CORPUS_AUTO` still absent (append, not overwrite) | ✅ |
| Secure backup taken **before** the edit | ✅ |

**Backup:** `/Users/molhamhomsi/FanOps/.env.wave0a-bak-20260716T215324Z`
· 943 bytes · perms `-rw-------` (`0600`) · owner `molhamhomsi:staff`
· SHA-256 `7b2631307077a6caf7ea3362502d60033de4dfcff722b28456204f360d6b216a` — **identical to the pre-change file**
· **Outside Git** (`/Users/molhamhomsi/FanOps/` is not the repository; repo `git status` is unaffected).

**Edit:** `autopilot.set_env_var(Path("/Users/molhamhomsi/FanOps/.env"), "FANOPS_CORPUS_AUTO", "0")` — the
repository's own helper (atomic temp + `os.replace`, `chmod 0600`, preserves every other line).

### 12.2 Post-change integrity (prompt §10.5/§10.6)

| Check | Result |
|---|---|
| SHA-256 after | `fbe085ed6504d9e96e170767cf024add9e967e58a7f2d4bb84ba4fefc9dcf17c` |
| `FANOPS_CORPUS_AUTO` parses as | **`'0'`** ✅ |
| Keys **added** | `['FANOPS_CORPUS_AUTO']` — exactly one ✅ |
| Keys **removed** | `[]` ✅ |
| Keys **changed** | `[]` — **no existing value altered** ✅ |
| Key count | 14 → **15** ✅ |
| Every prior line byte-preserved | ✅ |
| Permissions | `-rw-------` (`0600`) — **retained** ✅ |
| Ownership | `molhamhomsi:staff` — **retained** ✅ |
| CRLF introduced | no ✅ |
| `.env.tmp` leftover | none ✅ |
| `FANOPS_CORPUS_TARGET` altered | **NO** — still absent → `12` ✅ (prompt §2) |
| `FANOPS_SMART_FRAMING` altered | **NO** — still absent → default ON ✅ (prompt §2) |
| Any secret read, printed, or written | **NO** ✅ |

`.env` contents are not reproduced in this record.

---

## 13. Restart Decision and Result

**No restart was required, requested, or performed. The restart gate (prompt §10) was never triggered.**

Proven at §10.2 and then **demonstrated live**: the resident daemon adopted the change on its very next tick.

| Property | Before change | After change | Verdict |
|---|---|---|---|
| Daemon PID | **9121** | **9121** | **unchanged — never restarted** |
| Start time | 2026-07-16T16:49:48Z | 2026-07-16T16:49:48Z | unchanged |
| Heartbeat `code` SHA | `6d21749…` | `6d21749…` | unchanged — no code drift |
| Heartbeat `origin` | `loop` | `loop` | same resident loop |
| Keeper | loaded, not resident | loaded, not resident | healthy; **no kickstart** |
| Studio | PID 9123 | PID 9123 | unchanged |
| Downtime | — | **zero** | — |

### 13.1 The daemon's own attestation that the gate closed

`cli.py:1092` logs `corpora_refresh_skipped` with `reason=cr.get("reason")`, and the disarmed gate returns
`{"refreshed": False, "reason": "disabled"}` (`persona_research.py:212-213`). The live log:

```
21:15:49.567  "outcome":"corpora_refresh_skipped","reason":"fresh"
21:25:51.633  "outcome":"corpora_refresh_skipped","reason":"fresh"
21:38:32.044  "outcome":"corpora_refresh_skipped","reason":"fresh"
21:48:34.382  "outcome":"corpora_refresh_skipped","reason":"fresh"      ← last tick BEFORE the change
── .env change applied 21:53:24Z ──
21:58:36.328  "outcome":"corpora_refresh_skipped","reason":"disabled"   ← first tick AFTER the change
```

**This is direct, in-band, self-attesting proof from the live daemon** — not an inference, not a re-derivation
in a separate process. PID 9121 re-read `.env` at the top of its loop (`cli.py:1448`, `override=True`),
rebuilt `Config`, and the gate returned `disabled`.

Independently reproduced in a clean process replicating the daemon's exact sequence (cwd fallback → `Config` →
`load_dotenv(root/".env", override=True)` → `Config`):

```
corpus_auto BEFORE dotenv (stale env): True
corpus_auto AFTER  dotenv (disk truth): False   <-- the daemon does this EVERY tick
```

---

## 14. Hashtag Postimage

| Check (prompt §11) | Expected | Actual | Verdict |
|---|---|---|---|
| **Effective `FANOPS_CORPUS_AUTO=0`** | disabled | **`cfg.corpus_auto is False`; daemon logs `reason=disabled`** | ✅ |
| **Corpus file hash unchanged** | `302f0d27…` | **`302f0d27defff4e5e5419f2552bd42ce897be2b7afefe4bef540ed0a9e34420b`** | ✅ **byte-identical** |
| **Store file hash unchanged** | `5173804f…` | **`5173804fff7739d8a7c6360945e5772be622882e8b2d9698797eb15bda1d9b29`** | ✅ **byte-identical** |
| **Budget file hash unchanged** | `d47493c8…` | **`d47493c81f18284f8dc500f1f823f7748c860b73485d5e95833a8a2b3a7fbf09`** | ✅ **byte-identical** |
| R4 rollback snapshot intact | 5,369 bytes | **present, 5,369 bytes** | ✅ |
| **No auto-corpus event after the change** | none | **none** — see below | ✅ |
| Corpora unchanged | 8 personas / 22 tags | 8 / 22 | ✅ |
| Store unchanged | 18 tags, `reach: {}` | 18, `reach: {}` | ✅ |
| Budget unchanged | 0 of 30 | 0 of 30 | ✅ |

**Stronger evidence than a hash comparison:** `.corpora_refresh.json` is **untouched** — mtime still
`2026-07-16T10:46:39Z`, content still `{"ts": "2026-07-16T10:46:39.874377+00:00", "personas": 8, "changed": 0,
"added": 0, "removed": 0}`. Because the `corpus_auto` gate returns **before** the marker is written
(`persona_research.py:212` precedes `:230`), an untouched marker proves the refresh body **never executed** —
no `Personas.load`, no harvest, no measurement, no `apply_auto_corpus`. The corpus machinery did not merely
write nothing; **it did not run.**

**No Meta Graph call was made.** The budget file is byte-identical (any `ig_hashtag_search` would have appended
a `(tag, ts)` entry via `record_query`).

### 14.1 Operational consequence of the disarm — disclosed

The 12 h throttle marker is now **frozen** at `2026-07-16T10:46:39Z` and will not advance while B holds
(the gate returns before the marker write). Its window expired at **2026-07-16T22:46:39Z**.

**Therefore: whenever B is reverted, the corpora refresh will fire on the very next tick** — the throttle will
long since have lapsed. It will **not** wait 12 h. If B is reverted after **2026-07-19T17:25:18Z** (budget
refilled), the F-A/F-B/F-C chain fires **immediately**. **Do not revert B until F-A and F-C are fixed.**

---

## 15. Unrelated-State Integrity Check

| Domain | Check | Result |
|---|---|---|
| **Ledger — non-target rows** | full 1,063-row diff, pre vs post | **0 unrelated rows changed** ✅ |
| **Ledger — failed posts** | 4 before / 4 after, same IDs | **untouched** ✅ (prompt §9.4) |
| **Ledger — Instagram/Postiz posts** | 210 `awaiting_approval` | **untouched** ✅ (prompt §9.5) |
| **Ledger — pre-existing awaiting posts** | 277 before, all still present | **untouched** ✅ (prompt §9.6) |
| **Ledger — scheduling/content fields** | field-level diff across all 66 targets | **only `state` changed** ✅ (prompt §9.7) |
| **Ledger — history** | no row added or removed; nothing deleted or rewritten | ✅ |
| **Ledger — approval decisions** | `rejected` 0 → 0; no approval reversed into a discard | ✅ |
| **Hashtag corpora / store / budget** | SHA-256 ×3 | **byte-identical** ✅ |
| **`.corpora_refresh.json`** | mtime + content | **untouched** ✅ |
| **`.env` unrelated keys** | 14 pre-existing keys | **none added-to, removed, or altered** ✅ |
| **`FANOPS_CORPUS_TARGET` / `FANOPS_SMART_FRAMING`** | prompt §2 forbids | **not altered** ✅ |
| **Runtime processes** | daemon 9121 / studio 9123 / keeper | **unchanged; nothing restarted** ✅ |
| **Repository** | `git status --porcelain` | **`?? docs/constitution/`, `?? docs/reconciliation/`** — identical to the frame-open state. No tracked file modified. Nothing committed, pushed, merged, PR'd. ✅ |
| **Report 05** | unmodified | ✅ |
| **Media / clips / renders** | no unlink path reached (`_deferred_unlinks` empty) | ✅ |
| **External services** | Zernio / Postiz / Meta Graph | **zero calls made** ✅ |
| **Backups / worktrees / branches / evidence** | none removed | ✅ |

---

## 16. Rollback Instructions

### 16.1 Containment A — requeue the parked posts

The inverse of `unapprove_post` is **`Ledger.approve_post`** (`ledger.py:586-602`), the same method the Studio
**Review → Approve** control calls.

```python
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.timeutil import iso_z
from datetime import datetime, timezone
cfg = Config()                     # FANOPS_ROOT=/Users/molhamhomsi/FanOps  (or cwd=/Users/molhamhomsi/FanOps)
now_iso = iso_z(datetime.now(timezone.utc))
with Ledger.transaction(cfg) as led:
    for pid in TARGET_IDS:         # scratchpad wave0a/target_ids.txt
        led.approve_post(pid, now_iso=now_iso)
```

Or, per post, via the Studio **Review** tab → **Approve** (which additionally supplies a strictly-future
suggested time).

**Rollback fidelity — disclosed honestly:**

`approve_post` preserves `scheduled_time` **only if it is still strictly future** at requeue time:

```python
keep = sched > parse_iso(now_iso)
... update={"state": PostState.queued,
            "scheduled_time": p.scheduled_time if keep else _fallback_iso(suggested_iso, now_iso)}
```

- **Requeued while still future → time preserved verbatim.** (`keep = True`)
- **Requeued after its time has passed → time is REWRITTEN.** Via the raw ledger call with
  `suggested_iso=None`, `_fallback_iso` returns **`now_iso` exactly** (`ledger.py:297-302`) — i.e. the post
  becomes **immediately due**. Via the Studio path, `approve_posts` supplies a strictly-future suggestion
  instead.

**Operational consequence:** rollback is **lossless** for any target requeued before its scheduled time
elapses. The window closes progressively: the earliest target passes at **2026-07-16T23:48:00Z**, the last at
**2026-07-23T17:16:00Z**. **Requeuing a large past-due set via the raw ledger call would bump them all to
`now` and machine-gun the backlog at a still-broken Zernio.** Use the Studio approve path (suggested times) or
re-stagger via **Schedule → Reschedule all** if requeuing after times have lapsed.

**Requeue capability is never lost** — every field needed to requeue is preserved by the park.

### 16.2 Containment B — re-arm the auto-corpus

Either restore the backup:
```
cp -p /Users/molhamhomsi/FanOps/.env.wave0a-bak-<TS> /Users/molhamhomsi/FanOps/.env
```
or remove the key with the repository's own helper (`autopilot.unset_env_var`, `autopilot.py:53`), or set
`FANOPS_CORPUS_AUTO=1`. Effective on the next daemon tick (≤ ~10 min). No restart.

---

## 17. Deferred Root Fixes

**Nothing in this record fixes anything.** Both containments are reversible holds.

| ID | Defect | Status | Evidence re-verified this frame |
|---|---|---|---|
| **`ACT-01`** | **Zernio 405** — publish contract drift | **UNFIXED.** `post/zernio.py` not read for repair, not modified, not probed. | 4th failure observed live at 21:38Z (§3.3). `ZERNIO_URL` absent → **stale-URL cause eliminated** (§5.2); server-side drift is the surviving hypothesis. |
| **`F-A`** | `apply_auto_corpus` drops new auto tags' meta → next tick `_is_pinned` reads absent-meta as **pinned** → every auto tag permanently pinned → `auto_slots → 0` → corpus freezes | **UNFIXED** | **Mechanism confirmed in code.** `_is_pinned` (`persona_research.py:114-117`) returns `True` when `m is None`. In `apply_auto_corpus` the conflation fires **twice**: (a) the merge guard `if not _is_pinned(merged, nk): merged[nk] = v` is **`False` for every brand-new tag** (absent ⇒ "pinned") → **its meta is never written**; (b) `d["hashtag_corpus_meta"] = {t: merged[t] for t in out if t in merged}` then drops it entirely. Next tick reads it as pinned. **Live fingerprint: `hype-vibe` = 4 corpus tags / 0 meta entries.** |
| **`F-B`** | `harvest_cooccurring` runs first and spends one budget slot per seed unconditionally → measurement starved | **UNFIXED** | Budget shows **30/30 unique tags burned in a 2-minute window** (17:25:18Z→17:27:19Z) — the starvation signature. |
| **`F-C`** | `FANOPS_CORPUS_TARGET=12` × 3 posting personas = 36 seeds > 30 budget → `CURATED` is not a fixed point | **UNFIXED** | **`FANOPS_CORPUS_TARGET` absent → default `12` confirmed** (`config.py:441-448`). Precondition holds. |
| `ACT-03` | RCDR:85-86 falsified `[OBS]` claim not retracted | **UNTOUCHED** — prompt §2 forbids documentation corrections | **Not re-verified this frame.** A documentation-only defect; RCDR was not read, so this frame adds no evidence either way. Status is carried forward, not re-established. |
| `ACT-04`/`ACT-05` | Track A (S1–S5) never applied; nothing visually accepted | **UNTOUCHED** — prompt §2 forbids applying Smart Reframing | **Newly established: `FANOPS_SMART_FRAMING` is not `0`** → S1–S5 are code-active, so report 05's finding stands (report 05 §5.1). |

---

## 18. Required Follow-Up Times

Both containments are applied. These are the times at which someone must **check that they still hold**.

| Time (UTC) | Event | Required action |
|---|---|---|
| ~~2026-07-16T21:58:36Z~~ | ~~First tick after the B change~~ | ✅ **DONE — daemon logged `reason=disabled`.** B proven in-band. |
| **2026-07-16T23:48:00.272298Z** | **`post_0a570125b88b`** — the **formerly** next-due Zernio post. **Now parked; nothing should fire.** | **Verify `failed` is still 4, `queued` still 0.** A 5th failure here would mean the park did not hold. |
| **2026-07-17T02:35:00.272298Z** | **`post_17ae6436c3bf`** — second formerly-due item | Same check. Two clean passes = the park is durable. |
| **2026-07-19T17:25:18.530741Z** | **First Meta budget slot frees** | Confirm B still reads `disabled`. Earliest possible arming moment for F-A/F-B/F-C. |
| **2026-07-19T17:27:19.901298Z** | **All 30 budget slots free** | Full arming. **B must still hold**, or the corpora re-pad off `CURATED`. |
| **2026-07-23T17:16:00.272298Z** | Last parked post's scheduled time lapses | After this, **every** parked post is past-due → a raw-ledger requeue bumps all 66 to `now` and machine-guns the backlog (§16.1). Requeue via the Studio approve path or **Schedule → Reschedule all** instead. |

**The two 07-19 timestamps are the ones that matter.** Prompt §11 requires this stated plainly: the immediate
burn is stopped, but **no root fix was performed**, and the containment is a switch someone must not flip back.

### 18.1 Later verification procedure (no waiting required now)

```bash
# A — the parked queue held; no burn at 23:48Z
python - <<'PY'
import sqlite3, json, collections, datetime
c = sqlite3.connect("file:/Users/molhamhomsi/FanOps/MohFlow-FanOps/00_control/ledger.sqlite?mode=ro", uri=True)
p = [json.loads(x) for (x,) in c.execute("select payload from ledger_rows where map_name='posts'")]
print(collections.Counter(q["state"] for q in p))          # expect failed == 4, queued == 0
print([q["id"] for q in p if q["state"]=="failed"])          # expect the SAME four IDs
PY

# B — the daemon itself attests the gate is closed
grep '"outcome":"corpora_refresh' /Users/molhamhomsi/FanOps/MohFlow-FanOps/07_reports/daemon.err | tail -3
#   expect: "reason":"disabled"      (was: "reason":"fresh")

# B — corpora/store untouched
shasum -a 256 /Users/molhamhomsi/FanOps/MohFlow-FanOps/00_control/{personas.json,hashtags.json,hashtag_budget.json}
#   expect: 302f0d27…  5173804f…  d47493c8…
```

---

## 19. Evidence Ledger

| ID | Evidence | Method | Location |
|---|---|---|---|
| `W0A-001` | Repo frame: branch/HEAD/remote/ahead-behind/status | `git rev-parse`, `git ls-remote` (**no fetch**), `git status` | §3.1 |
| `W0A-002` | Daemon/keeper/Studio PIDs, start times, plists | `launchctl list`, `ps -eo`, `plutil -p` | §3.2 |
| `W0A-003` | Heartbeat SHA == HEAD; tick cadence | `07_reports/daemon.err` | §3.2 |
| `W0A-004` | Ledger snapshot 1 (`queued 67 · failed 3`) | Python `sqlite3.backup()` on a `mode=ro` connection | scratchpad `ledger.20260716T212708Z.sqlite` |
| `W0A-005` | **Ledger snapshot 2 (`queued 66 · failed 4`) — the 4th burn** | same | scratchpad `ledger.20260716T213900Z.sqlite` |
| `W0A-006` | 4th burn observed: `post_0a12cff53619` → failed, 405, submission `fanops_41e15dc9f2ca` | ledger diff across snapshots + `daemon.err` `shrink_post clip_564a91798b1a` | §3.3 |
| `W0A-007` | `.env` eight approved values; all other keys name-only | allow-list reader script | §5.1 |
| `W0A-008` | `.env` metadata: perms `0600`, owner, size, SHA-256, LF-only | `os.stat`, `hashlib` | §11 |
| `W0A-009` | `corpus_auto=True`, `corpus_target=12` effective | live `Config` property read | §10.1 |
| `W0A-010` | Budget 0/30; releases 17:25:18Z / 17:27:19Z | `meta_graph.budget_remaining` (the code's own pure read) | §3.8 |
| `W0A-011` | Parking mechanism + guards + atomicity | `ledger.py:470-488,586-610`, `ledger_sqlite.py:105-125`, `post/run.py:475`, `models.py:104-116` | §6 |
| `W0A-012` | 5 tests + 1 liveness pin cover `unapprove_post` | `grep` over `tests/` (**not executed** — CI-only per project rule) | §6.3 |
| `W0A-013` | Per-tick `load_dotenv(override=True)` in the run loop | `cli.py:1447-1458` | §10.2 |
| `W0A-014` | `corpus_auto` gate is first statement of `refresh_corpora_if_due` | `persona_research.py:205-213` | §10.3 |
| `W0A-015` | F-A mechanism (double `_is_pinned` conflation) | `persona_research.py:114-117`, `persona_store.py:182-220` | §17 |
| `W0A-016` | Queue preimage (66 posts, full fields) | ledger snapshot 2 | scratchpad `queue_preimage.json`, `target_ids.txt` |
| `W0A-017` | Archive: 73 records, 37 `published_at`, 55 `public_url`, latest 2026-07-05T02:44:50Z | `06_published/*/*.json` | §3.4 |
| `W0A-018` | Backend routing per channel | `accounts.json` (**integration ids redacted**) | §3.5 |
| `W0A-019` | **Mutation A**: full-ledger preimage (1,063 rows) before the park | `sqlite3.backup()` on `mode=ro` | scratchpad `ledger.PREIMAGE.20260716T215254Z.sqlite`, `preimage_row_hashes.json` |
| `W0A-020` | **Mutation A**: full-ledger postimage + 1,063-row diff → exactly 66 changed, `state`-only, 0 unrelated | same | scratchpad `ledger.POSTIMAGE.20260716T215254Z.sqlite`, `phaseE_result.json` |
| `W0A-021` | **Mutation B**: `.env` backup, identical SHA-256, `0600`, outside Git | `shutil.copy2` + `os.stat` | `/Users/molhamhomsi/FanOps/.env.wave0a-bak-20260716T215324Z` |
| `W0A-022` | **Mutation B**: post-change key-set diff — 1 added, 0 removed, 0 changed | allow-list parser | §12.2 |
| `W0A-023` | **B proven in-band by the live daemon**: `reason:"fresh"` ×4 → `reason:"disabled"` at 21:58:36Z, PID 9121 unchanged | `07_reports/daemon.err` | §13.1 |
| `W0A-024` | **No corpus machinery ran**: `.corpora_refresh.json` mtime/content untouched (gate precedes the marker write) | `os.stat` + content | §14 |
| `W0A-025` | Post-containment control-file hashes byte-identical to §3.9 | `shasum -a 256` | §14 |

**Evidence-handling note.** Ledger snapshots were taken with the SQLite **backup API on a `mode=ro`
connection**, into the session scratchpad — **outside the repository and outside Git**. No WAL lock was taken
on the live database. Copying out is not a mutation.

---

## 20. Mutation Attestation

### 20.1 Every mutation performed — the complete list

| # | Mutation | Target | Mechanism | Time (UTC) | Reversible |
|---|---|---|---|---|---|
| **1** | 66 posts `queued` → `awaiting_approval` (**`state` field only**) | `00_control/ledger.sqlite`, the 66 approved IDs | `Ledger.unapprove_post` × 66 inside **one** `Ledger.transaction` (`BEGIN IMMEDIATE`…`commit`) | **2026-07-16T21:52:54Z** | Yes — §16.1 |
| **2** | One line **appended**: `FANOPS_CORPUS_AUTO=0` | `/Users/molhamhomsi/FanOps/.env` | `autopilot.set_env_var` (atomic temp + `os.replace`) | **2026-07-16T21:53:24Z** | Yes — §16.2 |
| **3** | Backup file created | `/Users/molhamhomsi/FanOps/.env.wave0a-bak-20260716T215324Z` | `shutil.copy2` | **2026-07-16T21:53:24Z** | additive |
| **4** | This record created | `docs/reconciliation/07_WAVE_0A_CONTAINMENT_RECORD.md` | authorized by prompt §1 | this frame | additive, untracked |

**Nothing else was mutated.** Read-only ledger snapshots were written to the session scratchpad (outside the
repository, outside Git) as evidence.

### 20.2 Prohibited actions — confirmed NOT performed

- ❌ No implementation code changed · ❌ `post/zernio.py` not fixed · ❌ Zernio not probed
- ❌ No throwaway asset uploaded · ❌ No real or test content published · ❌ Meta Graph not called
- ❌ Hashtag corpus not refreshed · ❌ Hashtag migration not run · ❌ Smart Reframing not applied
- ❌ No failed post requeued or retried · ❌ No non-Zernio post changed · ❌ No ledger history rewritten
- ❌ No ledger record deleted · ❌ No approval decision changed · ❌ No Instagram/Postiz record altered
- ❌ `FANOPS_CORPUS_TARGET` not altered · ❌ `FANOPS_SMART_FRAMING` not altered · ❌ No secret exposed
- ❌ No daemon restarted · ❌ No worktree/branch/report/backup/evidence removed
- ❌ No governance or documentation correction made · ❌ No Wave 1+ action performed
- ❌ Nothing committed, pushed, merged, PR'd, or issued · ❌ No GitHub setting altered
- ❌ Report 05 not modified · ❌ No test suite run locally (project rule: CI only)
- ❌ No `fanops` CLI verb invoked (read-only or otherwise)

Two prohibitions deserve an explicit note because they were *reachable* and deliberately not taken:

- **Raw SQL on the ledger was refused** even though it was trivially available — a supported state transition
  existed (prompt §5: *"Do not use raw SQL when a supported state transition exists"*; §2: *"Do not invent a
  direct SQLite mutation merely because it is possible"*).
- **The 4 failed posts were not requeued or retried**, though the same transaction could have done it. They
  remain `failed` with their `error_reason` and `submission_id` intact.

---

## 21. Final Classification

| Dimension | Classification |
|---|---|
| **Publish** | **`CONTAINED — QUEUE PARKED`** |
| **Hashtags** | **`TEMPORARILY CONTAINED — AUTO CORPUS OFF`** |
| **Overall** | **`YELLOW — LIVE RISKS CONTAINED, ROOT FIXES PENDING`** |

### 21.1 What these classifications do and do not claim

**Publish — `CONTAINED — QUEUE PARKED`.** Zero posts are publish-eligible; the burn is stopped. This
**explicitly does NOT claim** the Zernio contract is fixed. It is not `RESOLVED — CONTRACT WORKING`. The 405
regression (`ACT-01`) is untouched and would recur on the first requeue. The burn is stopped because nothing
can fire, not because anything can succeed.

**Hashtags — `TEMPORARILY CONTAINED — AUTO CORPUS OFF`.** `ACT-02` is **TEMPORARILY CONTAINED**, **not
`RESOLVED`**. F-A, F-B, and F-C are each **verified-present and unfixed** (§17). The 2026-07-19T17:25:18Z
budget rollover still arrives; the disarm means no automatic writer will be listening. Reverting B without
first fixing F-A/F-C re-arms the chain **immediately** (§14.1).

**Overall — `YELLOW`.** Both live risks are held by reversible switches, not repairs. Every root fix remains
outstanding.
