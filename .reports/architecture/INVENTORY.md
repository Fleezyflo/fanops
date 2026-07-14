# FanOps — Canonical Architecture Inventory

**Cycle 1 (reconciliation) · closed 2026-07-14 · git HEAD `fcffa73`**

This is **the** inventory. It supersedes and absorbs `.reports/ground-truth-inventory-2026-07-14.md`
(now a pointer stub). Machine-readable twin: [`inventory.json`](inventory.json) — 174 items, every one
carrying a `file:line` citation, zero duplicate IDs.

**Evidence standard.** Every fact cites source. Documentation was used as a *lead*, never as proof —
and where docs and code disagreed, code won and the disagreement is recorded as a finding. Anything
unprovable is an explicit `UNK-*` entry, not a silent omission.

**Read this before trusting any line below:** §7 (Completeness). 31 of 127 modules remain uninspected.
Absence from this document is not absence from the code.

---

## 1. Inventory IDs and taxonomy

Every item has a permanent ID for use in Cycles 2–7. **IDs never get renumbered.** New items append;
retired items keep their ID with `status: retired`.

The brief's 12 canonical types are used verbatim. Four extensions were necessary, and I'm flagging
them rather than silently forcing items into an ill-fitting bucket:

| Type | Prefix | Count | Canonical? |
|---|---|---|---|
| entity | `ENT` | 20 | ✅ |
| external service | `EXT` | 21 | ✅ |
| configuration | `CFG` | 19 (+82 env entries) | ✅ |
| artifact | `ART` | 17 | ✅ |
| state | `STA` | 14 | ✅ |
| sidecar | `SID` | 14 | ✅ |
| cache | `CAC` | 9 | ✅ |
| scheduler | `SCH` | 7 | ✅ |
| datastore | `DST` | 5 | ✅ |
| worker | `WRK` | 5 | ✅ |
| command | `CMD` | 3 (CLI + 130 routes) | ✅ |
| queue | `QUE` | 1 | ✅ |
| **location** | `LOC` | 17 | ⚠️ extension |
| **lock** | `LCK` | 8 | ⚠️ extension |
| **identity** | `IDN` | 7 | ⚠️ extension |
| **fingerprint** | `FPR` | 7 | ⚠️ extension |

**Why the four extensions.** Cycle 1's brief explicitly demanded fingerprints and filesystem
locations, and neither fits the 12. A *fingerprint* is a derivation **rule** — folding it into `cache`
would discard the hashed-input list, which is the load-bearing fact (see FPR-001). A *location* is a
pipeline stage directory: it holds artifacts but is not one, and is not a datastore. *Locks* are
load-bearing concurrency primitives with no home in the 12, and are not `worker`s. *Identity* minters
determine every entity key and are distinct from fingerprints (which address content, not identity).

---

## 2. The verification (brief item 6): RenderState

Cycle 1 asserted "RenderState has no driver" on the strength of a **code comment**. That is not
evidence. I re-derived it independently across all five surfaces named in the brief.

**Verdict: CONFIRMED — with two refinements Cycle 1 missed.** (`FIND-001`)

| Surface | Result | Evidence |
|---|---|---|
| Deserialization | state **is** parsed from the stored row | [ledger.py:458](src/fanops/ledger.py:458), [ledger_bridge.py:44](src/fanops/ledger_bridge.py:44) |
| Serialization | state **is** persisted | [ledger.py:501](src/fanops/ledger.py:501) |
| Migrations | **no** migration touches it — v6 is a pure additive injection | [ledger.py:224](src/fanops/ledger.py:224) |
| Ledger API | **no** state setter; `add_render` is `setdefault` | [ledger.py:560](src/fanops/ledger.py:560) |
| Studio actions | **read-only** at every site | [app.py:68](src/fanops/studio/app.py:68), [views_review.py:220](src/fanops/studio/views_review.py:220), [views_results.py:121](src/fanops/studio/views_results.py:121), [preview_media.py:13](src/fanops/studio/preview_media.py:13) |
| Reconciliation | **zero** render references in the module | `reconcile.py` |
| Publishing | the renders **map is mutated** — but state is not | [compress.py:112](src/fanops/post/compress.py:112), [:130](src/fanops/post/compress.py:130) |

**Refinement 1 — Cycle 1 was incomplete.** The renders map *is* written on the publish path:
`led.renders[post.render_id] = r.model_copy(update={"path": str(shrunk)})` — a path rewrite after
compression. So "Renders are never mutated" would be **false**. Only `Render.state` is never mutated;
`model_copy` carries every other field through unchanged.

**Refinement 2 — scope the invariant correctly.** The property is *"no code writes RenderState"*, not
*"the field cannot hold another value"*. Deserialization faithfully restores whatever is on disk, so a
hand-edited or externally-written ledger row could carry a non-`rendered` state and it would load and
re-persist.

**Consequence.** Only two `RenderState` references exist outside the model default: the birth default
([models.py:420](src/fanops/models.py:420)) and the `_SHIPPABLE_RENDER` read
([views_results.py:112](src/fanops/studio/views_results.py:112)). Since a Render is always `rendered`
in practice, that guard (`rendered|queued|published|analyzed`) is **always satisfied and is a no-op**.
Only `retired` would gate it, and nothing sets `retired`.

**Status: settled.** Safe to treat as ground truth in Cycles 3–7.

---

## 3. The ownership resolution (brief item 7): `studio_audit.log`

**Resolved. Owner = [`audit.py`](src/fanops/audit.py).** (`FIND-003`, `ART-002`)

- **Writer:** `write_audit` ([audit.py:19](src/fanops/audit.py:19)) — appends one JSON line per
  state-changing action; path built at [:39](src/fanops/audit.py:39); `os.chmod(path, 0o600)` at
  [:43](src/fanops/audit.py:43).
- **Reader:** `read_audit_tail` ([audit.py:50](src/fanops/audit.py:50)); path built at
  [:53](src/fanops/audit.py:53).
- **CLI:** `fanops audit tail` ([cli.py:732](src/fanops/cli.py:732)).

**The structural fact behind the Cycle-1 confusion:** every *other* control file is declared as a
`Config` attribute ([config.py:157-177](src/fanops/config.py:157)). This one is constructed **inline at
both the write and the read site**, duplicating the literal — which is why a `Config`-based search
missed it. Recorded as a fact; no recommendation, per the fact-only brief.

**And it is not alone.** Chasing this surfaced a *second* undeclared control file (`FIND-004`,
`LCK-005`): the run lease **`00_control/.run.lock`** ([pipeline_run.py:15](src/fanops/pipeline_run.py:15)).
`fcntl.flock LOCK_NB` is the authority; the lockfile body (pid + started ISO) is advisory for status
only; the kernel releases it on process death so `kill -9` self-heals. It guards the
respond→write_request→advance converge loop.

---

## 4. Findings (7)

| ID | Finding | Class |
|---|---|---|
| `FIND-001` | RenderState driverless — **confirmed**, +2 refinements | verified |
| `FIND-002` | "Single writer of `queued`" invariant is **false as written**; safety property holds | doc drift |
| `FIND-003` | `studio_audit.log` owner = `audit.py`; path built inline, not in `Config` | resolved |
| `FIND-004` | Second undeclared control file: `.run.lock` run lease | new |
| `FIND-005` | **Cycle 1 was wrong:** framing tunables live in `clip.py`, not `framing.py` | self-correction |
| `FIND-006` | **Cycle 1 was wrong:** `FANOPS_SKIP_PREPUSH` is a phantom, not a test-only var | self-correction |
| `FIND-007` | Search blind-spot: keyword-default constants are invisible to constant greps | method |

### FIND-002 — the one that matters

`src/fanops/CLAUDE.md` states: *"do NOT set a post's state to `queued` anywhere except
`Ledger.approve_post`."* **That claim is false.** There are **seven** writers of `queued`:

| Site | Role | Guard |
|---|---|---|
| [ledger.py:575](src/fanops/ledger.py:575) | `approve_post` — the promoter | returns unless state **is** `awaiting_approval` ([:579](src/fanops/ledger.py:579)) |
| [actions.py:1000](src/fanops/studio/actions.py:1000) | requeue | `state in (failed, error)` |
| [actions.py:1031](src/fanops/studio/actions.py:1031) | requeue + shrink | `state in (failed, error)` |
| [actions.py:1062](src/fanops/studio/actions.py:1062) | requeue transient | `state in (failed, error)` + transient |
| [actions.py:1103](src/fanops/studio/actions.py:1103) | requeue oversize | `classify_failure == "oversize"` |
| [run.py:238](src/fanops/post/run.py:238) | `_unclaim_no_integration` | `state is submitting` |
| [run.py:422](src/fanops/post/run.py:422) | daemon transient requeue | bounded by `_DAEMON_TRANSIENT_MAX` |

**The safety property nevertheless holds.** Every non-`approve_post` writer is guarded on a *source*
state of `failed`, `error`, or `submitting`. **None can move a post out of `awaiting_approval`.** So
"an unapproved post is structurally unpublishable" is intact — but it is enforced by `approve_post`'s
guard at [:579](src/fanops/ledger.py:579), **not** by a single-writer property. The doc's phrasing
describes a mechanism that does not exist.

Secondary: CLAUDE.md's line refs are stale — it cites `ledger.py:503/:519`; the real `approve_post` is
at `:575`. Lines 503–519 are now `_save_unlocked`/`save`.

Also located: `reject_post` ([:592](src/fanops/ledger.py:592), guards `awaiting_approval` only) and
`unapprove_post` ([:596](src/fanops/ledger.py:596), guards `queued` only).

---

## 5. Configuration classification (brief item 5)

These are **five different things** and the first write blurred them. Only §5.1 is *dead*; the other
**13 of 18 items are alive**.

> **Taxonomy corrections applied** (`taxonomy_changelog` in the JSON). The first write filed items by
> ID prefix rather than by description. All three are fixed; **no ID was renumbered.**
>
> - **`TXC-001`** — **`SHIM-007` was in the shim bucket while its own verdict field read *"not a
>   shim"*.** New permanent category `reserved_surfaces`; the ID is retained and now carries
>   `classification: reserved_surface`.
> - **`TXC-002`** — the parent key was `dead_configuration`, which asserted deadness over **13 items
>   that are alive**. Renamed to `configuration_classification`. This was the same error as TXC-001,
>   one level up.
> - **`TXC-003`** — `historical_compatibility_shims` → `compatibility_shims`. `SHIM-005` is
>   **forward**-compat (an *older* binary parsing a *newer* ledger), so "historical" was false for it.
>   It is still a shim; the adjective was the wrong part. A `direction` field now carries the fact the
>   name was overloading — exactly 1 of 6 is forward, which is why the misnomer survived.

### 5.1 Confirmed dead — a removed feature's flag (5)

Proven absent from `src/fanops` (0 files), referenced only in `tests/`:

| ID | Name | Why it's dead |
|---|---|---|
| `DEAD-001` | `FANOPS_CASTING_BIAS` | the P11 LLM-casting stage was removed; migration v11 `_migrate_v10_drop_selections` ([ledger.py:229](src/fanops/ledger.py:229)) is the teardown's on-disk half |
| `DEAD-002` | `FANOPS_CREATIVE_VARIATION` | the per-account render **fork** was deleted; one owner-moment hook burns at crosspost |
| `DEAD-003` | `FANOPS_HOOK_EDITOR` | hook editor/critic/taxonomy deleted; `is_weak_hook` is the sole gate |
| `DEAD-004` | `FANOPS_HOOK_JUDGE` | same teardown |
| `DEAD-005` | `00_control/ledger.lock` | declared **and labelled vestigial in code** ([config.py:159](src/fanops/config.py:159)) |

### 5.2 Phantom — never existed (1)

| ID | Name | Verdict |
|---|---|---|
| `PHANTOM-001` | `FANOPS_SKIP_PREPUSH` | **Not a config var at all.** It exists *only* in prose asserting its non-existence ([.githooks/pre-push:8](.githooks/pre-push:8)) plus a test **proving its absence** (`tests/test_check_scripts.py:239`). Cycle 1 bucketed it as "test/CI-only", implying a real knob. It is the opposite of a knob. |

### 5.3 Test fixtures — not product configuration (5)

`FANOPS_REDUCE_TXN_BUDGET_S`, `FANOPS_REQUIRE_E2E`, `FANOPS_REQUIRE_STUDIO`,
`FANOPS_CHECK_ALLOW_NO_TESTS`, `FANOPS_LOCAL_TESTS`. These gate CI/test behavior and are never read by
`src/`. They are *live* — just not product config.

### 5.4 Compatibility shims — deliberately retained (6)

**Do not confuse these with dead code.** Each is load-bearing. `direction` says *whose* compatibility
it serves:

| ID | Shim | Direction | Why it stays |
|---|---|---|---|
| `SHIM-001` | `FANOPS_POSTER` | backward | the legacy global poster bridge. Per-channel `accounts.json` routing is the truth; this only narrows the fallback for a provider-less channel. `go_live` **never** writes it. |
| `SHIM-002` | `ANTHROPIC_API_KEY` | backward | vestigial but still read; the responder rides the `claude login` session ([config.py:217-225](src/fanops/config.py:217)) |
| `SHIM-003` | `00_control/ledger.json` | backward | break-glass import only, via `ledger_bridge` |
| `SHIM-004` | `HookSource.per_account` | backward | legacy provenance label kept so old Render rows deserialize |
| `SHIM-005` | **pydantic `extra="ignore"` on every ledger model** | **forward** | **load-bearing forward-compat** ([models.py:172](src/fanops/models.py:172)): an *older* binary must parse a *newer* ledger, dropping unknown keys, never crash. Switching any ledger model to `extra="forbid"` turns a forward-rolled ledger into a hard `ControlFileError`. **This is the entry that made "historical" a false label — see `TXC-003`.** |
| `SHIM-006` | legacy `StartInterval` plists | backward | still round-trip; current installs use KeepAlive + env-carried interval |

### 5.5 Reserved surfaces — declared but unwired (1) · *new permanent category, `TXC-001`*

A **reserved surface** is a lifecycle that exists in the type system with **no code path that advances
it**. It is none of the four categories above: not dead (no removed feature left it behind), not a shim
(it serves no older or newer peer), not a fixture (it serves no test).

| ID | Surface | Classification |
|---|---|---|
| `SHIM-007` | `RenderState.{queued, published, analyzed, retired}` ([models.py:92](src/fanops/models.py:92)) | `reserved_surface` |

- **Why it's not dead:** a driver could be wired without changing the type; nothing was removed that
  used to drive it.
- **Why it's not a shim:** it serves no legacy on-disk shape and no external contract.
- **Why the members can't just be dropped:** `views_results._SHIPPABLE_RENDER`
  ([views_results.py:112](src/fanops/studio/views_results.py:112)) reads the enum **by name**.
- **Live consequence:** because a Render is always `rendered` (§2 / `FIND-001`), that guard is *always*
  satisfied and is a **no-op**. Only `retired` would gate it, and nothing sets `retired`.

> **On the ID.** `SHIM-007` **keeps its permanent ID**. The `SHIM-` prefix is now a historical artifact
> of its original mis-filing, **not a claim about its type** — per `meta.id_stability`, IDs are never
> renumbered when a classification is corrected. Cite it as `SHIM-007` with
> `classification: reserved_surface`. This is precisely the trap that caused the bug: **never infer an
> item's category from its ID prefix.**

---

## 6. The system in one pass (deduplicated)

Full detail is in [`inventory.json`](inventory.json). This is the shape.

**Root & layout** (`LOC-001`…`LOC-017`). Root resolves **arg → `FANOPS_ROOT` → `cwd`**
([config.py:145](src/fanops/config.py:145)), recording its origin in `Config.root_source` so
`daemon.root_divergence` can tell a deliberate root from a silent cwd fallback. Base is
`{root}/MohFlow-FanOps`. Ten stage dirs, `00_control` … `07_reports`. `01_thirdparty_inbox` is a
**peer** of `01_inbox`, deliberately outside the native ingest rglob.

**Datastores** (`DST-001`…`DST-005`). One SQLite ledger, `00_control/ledger.sqlite`, **WAL +
`synchronous=FULL`**, two tables (`ledger_meta`, `ledger_rows`), **`SCHEMA_VERSION = 11`**
([ledger.py:190](src/fanops/ledger.py:190)). Writes are `BEGIN IMMEDIATE` + **full replace**. Eight
entity maps. An 11-step migration chain in which **v7 adds `selection_facts` and v11 drops
selections** — the casting teardown is legible in the chain itself. A newer on-disk version is
**refused**, never downgraded ([ledger.py:243](src/fanops/ledger.py:243)).

**Identity** (`IDN-001`…`IDN-007`). Content-addressed throughout: SHA-1, `usedforsecurity=False`,
`\x00`-joined, **truncated to 12 hex chars** ([ids.py:7](src/fanops/ids.py:7)). Python's builtin
`hash()` is *banned* — PEP 456 salts it per interpreter, which would break cross-process idempotency
([ids.py:2](src/fanops/ids.py:2)). `surface_key = "{account}|{platform}"` is both the post-id content
token and the schedule seed.

**Entities** (`ENT-001`…`ENT-029`). Source → Moment → Clip → Post, plus Render (child of Clip),
StitchPlan, Batch, Account, Persona. `ImportedMedia` is the one entity with a **natural** key (the
platform's own Graph `media_id`) and **no lineage by construction** — `models.py:508-519` states
lineage readers can never be handed one. The `Post` terminal-state invariant is enforced *at the type
level*: a model validator **raises** if `state ∈ {published, analyzed}` and `public_url` is empty
([models.py:356](src/fanops/models.py:356)).

**The only queue** (`QUE-001`) is a **filesystem** one: `04_agent_io/requests/{kind}__{key}.request.json`
paired with `.response.json`, correlated by a stamped `request_id`. No broker, no in-process queue.
Three gate kinds are resolvable (`moments`, `moment_hooks`, `captions`); **`intro_match` has no branch
in `gate_keys.gate_source_id`** — see `UNK-002`.

**Schedulers** (`SCH-001`…`SCH-007`). All scheduling is **launchd**. Three agents: `com.fanops.run`,
`com.fanops.keeper` (120 s poll), `com.fanops.studio`. **There is no `StartInterval`** — the daemon
uses `RunAtLoad` + `KeepAlive`, and the interval rides `EnvironmentVariables.FANOPS_DAEMON_INTERVAL`
([daemon.py:151](src/fanops/daemon.py:151)). The plist **bakes a full PATH** at install time because
launchd supplies a bare one. Plus one GitHub Actions cron (`0 3 * * *`).

**External services** (`EXT-001`…`EXT-L05`). Postiz (IG/YouTube), Zernio (TikTok), Meta Graph (the
**sole** IG metric reader), Cloudflare R2 (media mirror). The LLM is **not an HTTP API** — it is
`claude -p` as a subprocess riding the operator's existing login; `ANTHROPIC_API_KEY` is not required,
and `claude --bare -p` provably *fails* because it never reads the keychain
([llm.py:12-23](src/fanops/llm.py:12)). Every optional extra fails **open** — except one:

> **`[framing]` is the only fail-CLOSED dependency.** With `smart_framing` ON (the default) and cv2
> absent, the render **refuses** (`ToolchainMissingError` → exit 2) rather than silently centre-crop.

**Fingerprints** (`FPR-001`…`FPR-007`). The load-bearing one is `clip._render_fingerprint`
([clip.py:619](src/fanops/clip.py:619)). Its conditional-inclusion rule is the whole game:
`geom = bool(track) or (focus is not None and len(focus) > 2)` ([:633](src/fanops/clip.py:633)), and
`content_type` + `_REFRAME_GEOM_V` (**= 4**, [:616](src/fanops/clip.py:616)) are hashed **only when
`geom` is true** — so centred clips keep their historic fingerprint and never needlessly re-render.

**Learning** is gated, frozen, and amplify-only. `learning_validated` reads `cutover.json
metrics_confirmed` and is **auto-stamped** by the first real non-degraded live metric.
`p4_unlocked = learning_validated AND ≥8 attributed posts across ≥2 values`
([validation_gate.py:18,52](src/fanops/validation_gate.py:18)). `track._W` — the lift weights
`{saves 4.0, shares 4.0, retention 3.0, reach 0.001, likes 0.05}` — treats any weight ≥ 1.0 as
**primary**, and a missing primary stamps `lift_degraded` rather than trusting a partial scalar
([track.py:30](src/fanops/track.py:30)). Retention is **structurally unavailable** off IG REELS.
Notably, `variant_ucb` is **not** validation-frozen: it is a scorer swap on the safe caption-bias read
path, gated by statistics alone.

**Secrets** (`CFG-SECRET-001`). Three keys are keyring-first (service `"fanops"`), plus a dynamic
`META_GRAPH_TOKEN__<SLUG>` family. **Reads fail open; writes fail closed.** `set_secret` writes then
**reads back** and raises `OSError` if the value doesn't round-trip
([secret_provider.py:72-90](src/fanops/secret_provider.py:72)) — load-bearing, because the caller
scrubs the plaintext `.env` fallback on success, so an accepted-but-dropped write would erase the
secret from **both** stores.

---

## 7. Completeness metrics (brief item 9)

| Metric | Value |
|---|---|
| Modules total | **127** |
| Fully read (entire file in context) | **5** (3.9%) |
| Structurally scanned (≥1 cited fact extracted) | **91** (71.7%) |
| **Uninspected (zero facts extracted)** | **31** (24.4%) |
| Any-coverage | **75.6%** |
| Inventory items catalogued | **174** (100% carry evidence) |
| Environment-variable entries | **82** |
| Open UNKNOWNs | **11** |
| UNKNOWNs closed this cycle | **6** |

**Fully read:** `models.py`, `config.py`, `ids.py`, `secret_provider.py`, `gate_keys.py`.

**The 31 uninspected modules** — nothing was extracted from these, and no claim in this document rests
on them:

`__init__.py`, `_fwrun.py`, `audio_energy.py`, `bands.py`, `frames.py`, `hookcheck.py`,
`hookscore.py`, `persona_directives.py`, `persona_levers.py`, `persona_research.py`, `text.py`,
`cutover_postiz.py`, `post/__init__.py`, `post/media.py`, `post/metrics.py`, `post/providers.py`,
`studio/__init__.py`, `studio/actions_approve.py`, `studio/actions_casting.py`,
`studio/actions_common.py`, `studio/actions_run.py`, `studio/actions_segments.py`,
`studio/actions_wipe.py`, `studio/hashtags.py`, `studio/personas.py`, `studio/thumb_media.py`,
`studio/views.py`, `studio/views_common.py`, `studio/views_hashtags.py`, `studio/views_library.py`,
`studio/views_live.py`

*Caveat:* `studio/app_routes_*.py` count as **structurally scanned for their route surface only** —
decorators extracted, handler bodies unread.

### Open UNKNOWNs (11), by priority

**HIGH — these gate publish-safety or correctness:**

- `UNK-006` — **`post/__init__.py:19` `get_poster` live-guard is UNVERIFIED.** This is one half of the
  two-gate dryrun/live invariant, and I am taking it on `CLAUDE.md`'s word. Given that CLAUDE.md was
  *already proven wrong once this cycle* (FIND-002), this must be verified in Cycle 2.
- `UNK-007` — **`Settings` vs `Config` precedence is unproven.** Both read the same env keys;
  `settings.py` carries its own validators. Two config readers that can disagree is a correctness
  hazard, and I cannot currently say which wins.
- `UNK-002` — **`intro_match` gate-key resolution has no branch** in `gate_keys.gate_source_id`. A
  mis-resolved gate key silently orphans a gate.
- `UNK-010` — **Studio per-route ownership.** 149 route decorators enumerated by path; **not one**
  traced to its handler, action, or ledger effect. This is the operator's primary mutation surface.
- `UNK-001` — Persona identity field + minting function (`personas.py` unread).

**MEDIUM:** `UNK-004` (`context.md` consumer), `UNK-008` (`Clip.meta_captions` key format),
`UNK-011` (`_DAEMON_TRANSIENT_MAX` value).

**LOW:** `UNK-003` (TikTok oEmbed caller), `UNK-005` (VETTED cardinality), `UNK-009`
(`_evidence_fingerprint` inputs).

---

## 8. Operational findings — *not* architecture (brief item 8)

These are facts about the **working tree and operator environment**. They are quarantined here
precisely so no future cycle mistakes them for codebase properties.

**`OPS-001` — a stale orchestration wave marker is engaged.** `.orchestration/state/ACTIVE` reads
`engaged`; the state dir was last touched 2026-07-13. Its gate hook denies every subagent spawn outside
`{fanops-worker, fanops-lander}` and refuses any shell command *naming* `.orchestration/state/`,
`.cursor/hooks*`, or `.githooks/` — including read-only `ls`/`cat`. **Consequence: Cycles 1 and 2 were
executed single-threaded**; no parallel subsystem tracing was possible. Disengage is
`orchestrate.py done` (measured) or `orchestrate.py stop` (**operator-only**, denied from inside a
run). This is an operator action, not a code change.

**`OPS-002` — full repo copies exist under `.claude/worktrees/`.** Two git worktrees
(`upbeat-bassi-46a6ae`, `relaxed-golick-df075e`) contain complete repo copies, and `build/` is present
but untracked. A naive repo-wide grep **double-counts every hit** — this was observed live while
resolving `PHANTOM-001`. **Any future cycle must scope greps to `src/`, `tests/`, `docs/` and exclude
`.claude/worktrees/`.**

---

## 9. Method notes carried forward to Cycles 2–7

1. **Keyword-default constants are invisible to constant-shaped greps** (`FIND-007`). The hashtag cap
   (`max_tags: int = 4`, [hashtags.py:232](src/fanops/hashtags.py:232)) was nearly recorded as
   unproven because `MAX_HASHTAGS` / `[:4]` / `_CAP` all returned nothing. Any cycle enumerating
   tunables must scan **default parameter values**, not just module-level assignments.
2. **A code comment is not evidence.** Both Cycle-1 claims that rested on comments alone
   (`RenderState`, the `queued` single-writer rule) needed independent derivation — one survived, one
   did not.
3. **Grep absence ≠ code absence.** Two of this cycle's self-corrections (`FIND-005`, `FIND-006`) were
   Cycle-1 search artifacts, not code facts.
4. **Scope greps away from `.claude/worktrees/`** (`OPS-002`).
5. **Never infer an item's category from its ID prefix — and never let a category assert a property
   over its children that their own descriptions deny.** (`TXC-001`/`TXC-002`/`TXC-003`.) This cycle
   shipped the same error at *three* nesting levels: an item whose verdict read *"not a shim"* filed
   under shims; a parent key named `dead_configuration` asserting deadness over 13 live items; and an
   adjective (*"historical"*) that was false for the one forward-compat member of its bucket. **The
   check is mechanical:** for every item, assert that its `classification` is consistent with its own
   `status`/`verdict` text, and that its parent category's name does not contradict either. An ID is a
   stable handle, never a type. Run this check on any taxonomy Cycles 2–7 produce.

---
---

# APPENDIX A — Cycle 2 corrections (append-only)

**Cycle 2 · closed 2026-07-14 · git HEAD `fcffa73` (unchanged)**

Cycle 2 proved the architecture at mutation level. It produced four new documents — none of which
duplicate the sections above:

| Document | Scope |
|---|---|
| [`STATE_MACHINE.md`](STATE_MACHINE.md) | every entity: states, writers, guards, legal/illegal transitions, atomicity, idempotency, recoverability |
| [`MUTATION_MATRIX.md`](MUTATION_MATRIX.md) | every mutable field + the full concurrency audit |
| [`INVARIANT_AUDIT.md`](INVARIANT_AUDIT.md) | 21 invariants classified Verified / Refined / False / Unknown |
| [`COUPLINGS.md`](COUPLINGS.md) | 15 proven hidden couplings + all 149 Studio routes attributed |

**Nothing above this line is retracted except where explicitly stated in A.2.** Sections 1–9 stand.

---

## A.1 The five HIGH unknowns — all resolved

| ID | Cycle-1 status | Cycle-2 verdict |
|---|---|---|
| `UNK-006` | `get_poster` live-guard **unverified** | **RESOLVED → the invariant is FALSE.** `FIND-008` |
| `UNK-007` | `Settings` vs `Config` precedence **unproven** | **RESOLVED → there is no precedence; they never meet.** `FIND-009` |
| `UNK-002` | `intro_match` gate-key resolution has no branch | **RESOLVED → the gate is structurally unanswerable.** `FIND-010` |
| `UNK-010` | Studio per-route ownership: 149 routes, **0 traced** | **RESOLVED → all 149 attributed.** `FIND-013` |
| `UNK-001` | Persona identity field + minting function | **RESOLVED → `Persona` has no state field and no minted id.** Identity is the record key in `00_control/personas.json`. There is no state machine. ([`STATE_MACHINE.md`](STATE_MACHINE.md) §9) |

**Open UNKNOWNs: 11 → 6.** Remaining (all MEDIUM/LOW, none gating publish-safety): `UNK-004`
(`context.md` consumer), `UNK-008` (`Clip.meta_captions` key format — *partially* closed: crosspost
reads `"{account}/{platform}"` with a legacy `"@{account}/…"` fallback,
[crosspost.py:196-198](src/fanops/crosspost.py:196)), `UNK-003`, `UNK-005`, `UNK-009`. `UNK-011` is
**closed**: `_DAEMON_TRANSIENT_MAX = 3` ([run.py:68](src/fanops/post/run.py:68)).

---

## A.2 Corrections to Cycle 1 — three items change classification

### `DEAD-005` — **RECLASSIFIED. It is not dead.**

Cycle 1 filed `00_control/ledger.lock` as *"Confirmed dead — declared **and labelled vestigial in
code**"* ([config.py:159](src/fanops/config.py:159)), resting on the comment rather than a call check.
**This repeated the exact error Cycle 1's own method note #2 warns against ("a code comment is not
evidence").**

**It has one live consumer:** `Ledger.restore_snapshot` → `with _file_lock(cfg.lock_path):`
([ledger.py:551](src/fanops/ledger.py:551)).

It is worse than dead: it is a **live lock that excludes nothing.** Every other ledger writer serializes
on the SQLite `BEGIN IMMEDIATE` transaction ([ledger_sqlite.py:92](src/fanops/ledger_sqlite.py:92)); this
one is an `fcntl.flock` on a *different file*, after which `restore` calls `os.replace` on the database
([ledger_sqlite.py:151](src/fanops/ledger_sqlite.py:151)). The two locks are mutually invisible.

> **New classification:** `live_lock_no_mutual_exclusion`. The ID is retained per `meta.id_stability`;
> the `DEAD-` prefix is now a historical artifact of the mis-filing — **exactly the trap `TXC-001`
> documented.** Exposure today is nil (`restore_snapshot` has no production caller — the only other
> reference is a docstring at [ledger_wipe.py:246](src/fanops/ledger_wipe.py:246)).

### `SHIM-007` / `FIND-001` — **GENERALIZED. `RenderState` is one instance of a class of seven.**

Cycle 1's *reserved surface* category was correct but **under-populated**. Cycle 2 swept every state
enum for writers. **Seven more enum members have zero writers anywhere in `src/`:**

`ClipState.published` · `ClipState.analyzed` · `PostState.error` · `PostState.retired` ·
`BatchState.closed` · `BatchState.error` — plus `RenderState.{queued,published,analyzed,retired}`
already known.

**The load-bearing consequence Cycle 1 could not have seen:** `ledger._LIVE_CLIP_STATES =
(ClipState.published, ClipState.analyzed)` ([ledger.py:649](src/fanops/ledger.py:649)) is read by
`_delete_moment_cascade` as `clip_live = c.state in self._LIVE_CLIP_STATES`
([ledger.py:665](src/fanops/ledger.py:665)). Since nothing writes either state, **`clip_live` is always
`False` and every branch it gates is unreachable.** The cascade-protection property still holds — via
`_PROTECTED_POST_STATES` alone — so the clip half of the guard is **dead defensive code**, structurally
identical to the `_SHIPPABLE_RENDER` no-op Cycle 1 found. See `FIND-011`.

Also: **`Ledger.set_post_state` ([ledger.py:572](src/fanops/ledger.py:572)) has zero callers.** Its three
siblings are all live.

### `FIND-001` Refinement 1 — **re-verified, with the exact fields named**

The renders map *is* written on the publish path. Cycle 2 pins both writes:
`r.media_url = render_media` ([run.py:363](src/fanops/post/run.py:363)) and
`led.renders[…] = r2.model_copy(update={"path": render_path})` ([run.py:367](src/fanops/post/run.py:367)).
`Render.state` remains unwritten. **Cycle 1's refinement stands, unchanged.**

---

## A.3 New findings (7)

| ID | Finding | Class |
|---|---|---|
| `FIND-008` | **`get_poster`'s live-guard is incomplete.** It raises only on the literal string `dryrun`; an *unrecognized* backend falls through to `DryRunPoster` **on a live system**. The guard is case-insensitive, the `PROVIDERS` lookup is case-sensitive — they disagree. Reachable via a hand-edited `accounts.json` `backends` value (`Account.backends` is `dict[str,str]`, unvalidated at load; `Accounts.validate()` checks the *pairing*, never the *value*). Blast radius traced: **not** a phantom-published row (DryRunPoster sets no state post-M2) but a post **stranded in `submitting`**, escalated to `needs_reconcile` at 72 h and then `GAVE UP:`. | **doc drift + latent defect** |
| `FIND-009` | **`Settings` is never constructed by `Config`.** `Config.__init__` sets paths only; all 74 of its `os.getenv` calls read the env directly, uncached, per access. `Settings.runtime_load` — whose docstring claims "constructed per `Config()`" — has **zero callers**. `Settings`' only three consumers are `doctor` (strict validation), `config_introspect` (docs), and `accounts` (a constant import). **No runtime precedence conflict exists, because there is no runtime handoff.** The hazard is maintenance: two hand-maintained parsers for one env surface, and `_VALID_BACKENDS` defined twice. | **doc drift** |
| `FIND-010` | **`intro_match` is an unfinished gate.** It is live-wired (`pipeline.py:242-244` calls it; `intro_match.py:108` writes a real gate) but absent from `responder._SCHEMA`/`_PROMPT` **and** from `gate_keys.gate_source_id`. `answer_pending` iterates `_SCHEMA` only ⇒ **the autonomous responder can never answer it**, so `Moment.intro_matches` is never written and no intro-tease plan can be produced. Inert by default (`intro_tease` DEFAULT OFF); with it ON + `RESPONDER=llm`, unanswerable request files accumulate forever, invisible to the by-source status view. | **unfinished** |
| `FIND-011` | **The R1 published-URL invariant is enforced at construction only.** Proven against pydantic 2.13.4: `model_copy(update=…)` and direct `setattr` both bypass the validator, the bad row **serializes cleanly**, and the *next* `Ledger.load` raises → `ControlFileError` → **the whole ledger becomes unloadable**. `models.py:360`'s claim that "no door … can produce the ghost row" is false for **4 of the 5 doors it names**; two code comments assert that "Pydantic re-validates on serialization", which is **false**. The property survives via four independent **manual** call-site guards. | **doc drift + latent poison-pill** |
| `FIND-012` | **Seven more never-written state-enum members** (see A.2) — `RenderState` was not a one-off. `_LIVE_CLIP_STATES` is a dead guard. | **reserved surface (class)** |
| `FIND-013` | **All 149 Studio routes attributed** (108 mutating / 41 read-only). **Zero have authentication, session, or CSRF** — including `POST /golive/live` (flip to live publishing), `POST /schedule/publish-due` (publish the due bucket), and `POST /live-library/wipe/confirm`. The security boundary is the **network interface** (`app.run(host=…)`, [cli.py:1285](src/fanops/cli.py:1285)), a recorded prior decision — not an oversight, but the *only* control. | **resolved + recorded risk** |
| `FIND-014` | **Every `CLAUDE.md` safety-claim line reference is stale.** Not one resolves (`approve_post` cited at `ledger.py:503`, actually `:575`; `_publish_one` at `run.py:213`, actually `:242`; `_post_provider` at `run.py:120`, actually `:166`; and 7 more). Function names and semantics are correct; only the citations rotted — which matters because they are how a future editor finds the guard they are told not to break. | **doc drift** |

---

## A.4 Completeness (revised)

| Metric | Cycle 1 | Cycle 2 |
|---|---|---|
| Modules fully read | 5 | **13** (+`ledger.py`, `ledger_sqlite.py`, `settings.py`, `accounts.py`, `crosspost.py`, `post/__init__.py`, `post/providers.py`, `post/run.py`, `post/dryrun.py`, `intro_match.py`, `gate_keys.py`, `responder.py`) |
| Open UNKNOWNs | 11 | **6** |
| HIGH UNKNOWNs | 5 | **0** |
| Studio routes attributed | 0 / 149 | **149 / 149** |
| Invariants classified | 0 | **21** (11 Verified · 4 Refined · 5 False · 1 Verified-absent) |
| Proven couplings | 0 | **15** |

**Still uninspected and load-bearing** (no Cycle-2 claim rests on them): `post/metrics.py`,
`post/media.py`, `post/compress.py`, `studio/actions.py` (read only in the publish/approve regions),
`studio/golive.py`, `daemon.py`, `pipeline.py` (read only at the intro_match + quarantine call sites),
`meta_graph.py`, `track.py` (read only at the `analyzed` transition).

---

## A.5 Method notes carried forward to Cycles 3–7

1. **`OPS-001` is still engaged.** The orchestration gate refused an `Explore` subagent spawn during
   this cycle (*"REFUSED (orchestration gate): spawn type 'Explore' is not allowed during a wave"*).
   **Cycle 2 was executed single-threaded, like Cycle 1.** Disengage remains an operator action.
2. **A code comment is not evidence — and Cycle 1 broke its own rule.** `DEAD-005` was classified dead
   on the strength of the word "vestigial" in a comment. It has a live caller. **When a comment asserts
   a property, grep for the callers before recording the property.**
3. **Grep for the *writers* of an enum member, not its references.** Six of the seven reserved states
   in `FIND-012` have many *readers*, so a naive grep shows them as busy. Only a
   writer-shaped grep (`state = X`, `set_*_state(… X)`, `update={… X}`) reveals that nothing sets them.
4. **Verify pydantic semantics by execution, not by reading.** `FIND-011` was settled by running the
   three doors against the installed pydantic. Two in-code comments confidently asserted the opposite of
   what the library does.
5. **A registry that must be edited in N places will eventually be edited in N-1.** The gate-kind
   registry (`COUP-06`) is triplicated and unlinked; `intro_match` is registered in 1 of 3. When
   auditing any extension point, enumerate its registries and check each is complete.
6. **The recurring defect shape in this codebase is: *the doc names a mechanism that does not exist,
   while the property survives via a different one.*** It appeared in `FIND-002` (Cycle 1) and again in
   `INV-01`, `INV-02`, `INV-03`, `INV-06` (Cycle 2). **Always ask "does the named mechanism exist?"
   separately from "does the property hold?" — they have different answers here more often than not.**

---
---

# APPENDIX B — Cycle 2 EXTENSION (append-only; supersedes where it says so)

**Closed 2026-07-14 · git HEAD `fcffa73`** · Full detail: [`CYCLE2_EXTENSION.md`](CYCLE2_EXTENSION.md)

The extension pass did deep verification by **execution**, not inspection. It **corrected Cycle 2's own
first pass in three places** — two of which repeated the exact methodological error Cycle 2 had just
written down as a method note. That is the finding under the findings.

**Machine-readable outputs (authoritative for CI/compilation):**
[`transitions.json`](transitions.json) · [`mutation_writers.json`](mutation_writers.json) ·
[`route_contract.json`](route_contract.json) · [`invariants.json`](invariants.json) ·
[`couplings.json`](couplings.json)

## B.1 The classification correction

Cycle 2's first pass said the malformed-provider path's "safety property survives." **Withdrawn — too
generous.** Binding statement:

> **The malformed-provider path does not currently create a false published row, but it violates the
> live-mode fail-closed contract and causes a delayed operational failure.**

## B.2 Self-corrections (3)

| ID | First-pass claim | Verdict |
|---|---|---|
| `SC-1` | "`PostState.retired` has zero writers" | **FALSE.** `cli.py:395` writes it via `fanops resolve <id> retired` (argparse `choices`, `cli.py:702`). `PostState.analyzed` has **two** writers, not one. The census had grepped for *literal* enum refs and missed five **generic/dynamic** writers (`PostState(<str>)`, `p.state = <var>`, `setattr(p, <var>, v)`, `model_copy(update=<var>)`, enum **keyword defaults**). |
| `SC-2` | `COUP-02` — "`os.environ` has no cross-process propagation" | **RETRACTED.** The daemon loop re-reads `.env` with `override=True` **and rebuilds `Config` every tick** (`cli.py:1303-1304`). The real coupling is the **opposite** direction: a running **Studio** `load_dotenv`s once at entry (`cli.py:795`) and never again. |
| `SC-3` | `INV-01` scoped to the published-URL rule | **GENERALIZED.** `model_copy` bypasses **every validator on every model** — including `Moment`'s, despite `validate_assignment=True` (which protects `setattr` only). Not reachable today (`set_segments` round-trips through `model_validate`), but **proven latent**: `model_copy(update={"segments": …})` leaves a **stale start/end envelope**. |

**Survives the AST re-census:** `PostState.error` · `ClipState.{published,analyzed}` (so `_LIVE_CLIP_STATES`
at `ledger.py:665` remains a **dead guard**) · `BatchState.{closed,error}` · `RenderState.*`
(**Cycle-1 `FIND-001` stands**) · `Ledger.set_post_state` still has **zero callers**.

## B.3 Two defects proven by execution

**`F-A` — malformed provider (`UNK-006` / `INV-03`).** The write boundary is **sound**: `set_backend`
strips + lowercases and rejects unknowns (`accounts.py:412`), so malformed values are **hand-edit only**.
But the hand-edit path is unguarded end-to-end: `Accounts.validate()` returns **clean, no flag** for
`Postiz`, `POSTIZ`, `"postiz "`, `" postiz"`, and `blotato`; each resolves to a **`DryRunPoster` on a live
system**. The guard fires **only** on `dryrun`/`DryRun` — the two least dangerous values. Terminal state
depends on deployment shape: with a valid sibling channel → 72 h to a `GAVE UP:` label **and no half-live
warning**; with **all** channels malformed → `is_live_backend=False` → the reconcile pass **never runs**
(`pipeline.py:318`) → the post is stranded in `submitting` **permanently, unlabeled**.

**`F-B` — `restore_snapshot` (`INV-07` / `DEAD-005`).** Not merely "locks are mutually invisible."
**Executed:** a live writer holding `BEGIN IMMEDIATE` had `restore_snapshot` `os.replace` the DB file out
from under it. The writer's **`commit()` succeeded — no exception — and its data was silently discarded**;
the final on-disk state was the snapshot's. It **can** overwrite, it **does** race, and the losing writer
is told it succeeded.

## B.4 Route contract — all 149 verified (`route_contract.json`)

**0 / 149 authenticated. All 108 mutating routes CSRF-exposed.** Per-route: 44 ledger-write · 13
fs-write · 11 network · 9 subprocess · 12 env-write · 16 audited · 18 confirm-gated · 97 validated.
Authorization is a **recorded decision** (`studio/CLAUDE.md`: *"no auth by design … declined as
out-of-scope for localhost"*); the boundary is the network interface (`cli.py:1285`, default
`127.0.0.1:8787`). Known server-side gap **MOL-71**: `do_wipe_confirm` has no server check that
`do_wipe_preview` ran.

## B.5 Modules inspected

`daemon.py` (a **launchd manager**, not a pipeline driver — zero ledger writes) · `post/metrics.py`
(pure HTTP transport — zero `PostState` writes) · `post/media.py` (→ `COUP-17`: a Postiz URL is
identified by containing a literal `"|"`) · `post/compress.py` (→ `COUP-16`: a **second provider resolver
that never checks `cfg.is_live`** — shrink-decision only, **not** a publish bypass) · `golive.py` ·
`actions_segments.py` · `actions_casting.py` (→ `COUP-07`: safe **only** because
`Moment.validate_assignment=True`).

**Still uninspected:** `meta_graph.py`, `variant_*.py`, `adjust.py`, `digest.py`, `persona_*.py`, most
`views_*.py`. No claim rests on them.

## B.6 Method note carried forward

**A census is only as good as its query.** Cycle 2's first-pass writer census used a literal-shaped grep
and produced two false "zero writers" claims — after Cycle 1 had already recorded *"keyword-default
constants are invisible to constant-shaped greps"* (`FIND-007`) and *"grep absence ≠ code absence"*.
**Any claim of the form "nothing does X" must be produced by an AST pass over the full executable tree,
covering dynamic dispatch, variable-valued assignment, generic mutation APIs, and keyword defaults — never
by a grep.** And any claim about library behaviour (pydantic re-validation, SQLite locking) must be
**executed**, not read.
