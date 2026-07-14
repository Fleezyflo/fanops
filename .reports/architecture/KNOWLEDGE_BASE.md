# FanOps — Architectural Knowledge Base

**Cycle 5 · 2026-07-14 · git HEAD `fcffa73`**

The **subsystem inventory** and the **reader's guide** to [`kb/`](kb/).
Start at [`ARCHITECTURE_MANIFEST.md`](ARCHITECTURE_MANIFEST.md) for the one-page architecture.

---

## Part 1 — Subsystem Inventory

**19 subsystems (S01–S19; S16 is Studio). 127 modules. Partition is total: 0 unassigned, 0 ghosts.**
Machine-readable: [`kb/subsystems.json`](kb/subsystems.json).

> **Definition.** A **subsystem** is a partition class over the 127 modules of `src/fanops`, assigned by
> architectural responsibility. The partition is **total and disjoint** — every module is in **exactly one**.
>
> 🔴 **A subsystem is an ANALYTIC OVERLAY imposed by Cycle 5.** It is **not declared anywhere in the
> repository**: no package boundary, no `__init__`, no config, and no test corresponds to it, and **nothing
> enforces it.** The **modules** and the **import edges** are facts (AST-derived); the **grouping** is a
> **model.** A careless grouping *manufactures* cycles that do not exist — Cycle 5 caught itself doing exactly
> that (`C5-SC-2`). **Any claim made at subsystem level must be traceable to module-level edges before it is
> trusted.**

Each entry: **purpose · what it owns · public entry points · what it depends on · its architectural risk.**

---

### `S01` — Foundation (15 modules)
`ids` `errors` `timeutil` `text` `log` `models` `gate_keys` `controlio` `control` `stage_lock` `bands` `frames` `audio_energy` `_fwrun` `__init__`

**Purpose.** Contracts and primitives with no domain knowledge.
**Owns.** Entity identity (`ids._hash` — SHA-1[:12], content-addressed; **builtin `hash()` is banned**, PEP 456
salts it per interpreter and would break cross-process idempotency). The 18 pydantic models and 10 state enums.
Atomic control-file writes (`controlio.write_json_atomic`). The stage lock (L4). Logging. Typed errors.
**Entry points.** `make_id`, `surface_key`, `get_logger`, `write_json_atomic`, every model class.
**Depends on.** `config` only.
**Risk.** `COUP-04` — `ledger._SID_RE` hardcodes the *format* `ids` mints. **Changing the id length silently
breaks orphan detection**, with no error. `COUP-08` — three ledger maps key on hand-built `"a|b"` strings with
no shared constructor; the account-handle charset (defined in a *different* module) is load-bearing to the
ledger key space.

---

### `S02` — Configuration (4 modules)
`config` `settings` `config_introspect` `secret_provider`

**Purpose.** The env boundary.
**Owns.** 73 environment variables. All secrets (keyring-first).
**The runtime owner is `Config`, exclusively** — every property is an `os.getenv` evaluated **per access**,
uncached. **`Settings` never feeds it**: `Settings.runtime_load` has **zero callers** (`INV-05`). `Settings` is
a *doctor-only strict validator* + a docs surface. Runtime is lenient; doctor is strict. **The split is
intentional, not drift.**
**Secrets contract.** **Reads fail OPEN; writes fail CLOSED.** `set_secret` writes, then **reads it back**, and
raises if the value does not round-trip — load-bearing, because the caller then scrubs the plaintext `.env`
fallback on success.
🔴 **Risk (`AR-04`).** `config` is **depth 0 with fan-in 82** — the most-depended-on module in the system — and
it reaches **up** to `accounts` and `meta_graph` via lazy imports. `_VALID_BACKENDS` is **defined twice**
(`config.py:72`, `settings.py:18`), guarding the *write* and *read* boundaries separately. **If they drift, the
two boundaries disagree about what a legal backend is — which is the gap `RC-3` exploits.**

---

### `S03` — Persistence (3 modules)
`ledger` `ledger_sqlite` `ledger_bridge`

**Purpose.** The single source of truth.
**Owns.** `00_control/ledger.sqlite` — WAL, `synchronous=FULL`, `chmod 0600`, **schema v11**, 8 entity maps,
**full-document replace** on every save. The L1 lock (`BEGIN IMMEDIATE`, 30 s → typed `LockBusyError`).
**Entry points.** `Ledger.load`, `Ledger.transaction` (**74 call sites**), the `add_*` family (`setdefault` ⇒
**replay is a no-op**), `approve_post` / `reject_post` / `unapprove_post`.
**Migration.** 11 steps, forward-only. A **newer** on-disk schema is **refused, never downgraded** (`INV-13`).
Every model is `extra="ignore"` so an *older* binary can parse a *newer* ledger — **it holds by pydantic's
default, not by declaration** (`SHIM-005`).
🔴 **Risk (`AR-02`, CRITICAL).** `restore_snapshot` (`ledger.py:551`) takes a flock in **no lock domain**, then
`os.replace`s the DB. **The sibling six lines above it (`Ledger.snapshot`, `:540`) takes the correct lock.** A
concurrent writer's `commit()` **succeeds**, its data is discarded, **and its deferred media unlinks proceed** —
deleting real `.mp4`s whose rows the restore brings back.

---

### `S04` — Account & Persona Registry (6 modules)
`accounts` `personas` `persona_store` `persona_directives` `persona_levers` `persona_research`

**Purpose.** Who we post as, and in what voice.
**Owns.** `accounts.json` (10 mutators, `accounts.lock`) and `personas.json`. Per-account backend routing,
Postiz integration ids, clip profile, framing, persona link. Per-persona voice + `hashtag_corpus`.
🔴 **Risk (`AR-09`).** **The OPERATOR is a second, unmodelled writer** — `accounts.py:112-113` documents
hand-editing as the normal channel. The *write* boundary (`set_backend`) strips, lowercases, and rejects
unknowns. **The *load* boundary has no validation at all**: `Account.backends` is `dict[str,str]`, and
`Accounts.validate()` checks the integration/backend **pairing**, never the **value**. A hand-edited
`"postiz "` → **`DryRunPoster` on a live system**, silently.
🔴 **Risk (`UNK-C5-1`).** Contains the **only compile-time import cycle in the tree**
(`personas ↔ persona_store ↔ persona_research`). Load-order sensitive and undefended.

---

### `S05` — Ingestion (4 modules) · `S06` — Media Analysis (5 modules)
`ingest` `discover` `artifacts` `batches` — `transcribe` `signals` `keyframes` `framing` `vocals`

**Purpose.** Bytes → a catalogued `Source`, then → transcript, signals, faces, keyframes.
**Idempotency.** Content-addressed (`src_<sha1[:12]>`); `add_source` is `setdefault`. **Re-ingesting the same
bytes is a no-op.**
**Birth state.** `pending` when `queue_gate` is ON (**the default**) — invisible to every reducer. **This is the
U4 queue gate, by design, not a stall.**
**The load-bearing contract.** `transcribe_source` / `detect_signals` take `in_lock=True` and **adopt-or-defer**:
they **never shell whisper or ffmpeg under the ledger lock**. A cold cache costs *one tick* of latency, never a
held lock.
🔴 **Risk.** `framing._detect_faces` returns the **partially accumulated** face list on an exception — a crash
after 3 of 5 faces is indistinguishable from *"there were 3 faces"*, and can classify a 2-shot as a 1-shot.

---

### `S07` — Agent Gate (5 modules)
`agentstep` `responder` `llm` `prompts` `autopilot`

**Purpose.** The only place an LLM is consulted.
🔴 **The LLM is a SUBPROCESS (`claude -p`), not an HTTP API.** It rides the operator's existing `claude login`.
**`ANTHROPIC_API_KEY` is not required**, and `claude --bare -p` provably **fails** — it never reads the keychain.
**Owns.** `QUE-001` — **the only queue in the system, and it is a filesystem queue**:
`04_agent_io/requests/{kind}__{key}.request.json` ↔ `.response.json`, correlated by a stamped `request_id`.
**No broker. No in-process queue.**
**Escalation.** 3 attempts, then: `moments` → `SourceState.error` (**fail-closed**); `moment_hooks`/`captions` →
a **synthesised clean fail-open response** so ingest proceeds.
🔴 **Risk (`AR-14`).** The gate-kind registry is **triplicated and unlinked** (the write site, `responder._SCHEMA`,
`gate_keys.gate_source_id`). **Nothing ties the three together — and `intro_match` is registered in one of three,
so it is permanently unanswerable.** It is the standing demonstration of this risk, shipped.
🔴 **Risk.** `bump_attempts` uses a **bare `write_text`** while its **two siblings in the same file** are atomic.
A torn `attempts.json` **silently resets the 3-attempt ceiling**, making the bounded escalation unbounded.

---

### `S08` — Selection (6) · `S09` — Render (6) · `S10` — Caption & Hashtag (5)
`moments` `casting` `hookscore` `hookcheck` `router` `intro_match` — `clip` `overlay` `compose` `produce` `stitch_render` `impact_cut` — `caption` `hashtags` `fanops_hashtags` `tagging` `meta_graph`

**Purpose.** One owner-moment → one hook → a per-account cut → a per-persona caption.
**Single-owner picking.** Each pick is attributed to exactly one persona (`Moment.affinities`, len == 1). One
hook per owner-moment. Owner × platform captions. **`ingest_moment_hooks` is atomic-per-source**
(`if dec is None: return led`) — *not* independent per-persona gates.
**Render cache.** `clip._render_fingerprint`. Its conditional-inclusion rule is the whole game: `content_type` +
`_REFRAME_GEOM_V` are hashed **only when a zoom applies** — so centred clips keep their historic fingerprint and
**never needlessly re-render**.
🔴 **ffmpeg picks its muxer by FILE EXTENSION.** A temp file must be `<dst>.part.mp4`, not `<dst>.part`
(`COUP-07` / MOL-78).
🔴 **cv2 is the ONE fail-closed dependency** — `smart_framing` is **ON by default** and the render **refuses**
without it (`INV-17`).

---

### `S11` — Crosspost (1) · `S12` — Publish (10)
`crosspost` — `post/*` `postiz_lifecycle`

**Purpose.** Mint one `Post` per owner surface; then, on approval, put it on the internet.
🔴 **The approval boundary.** A post is **born `awaiting_approval`** (the *model default*, so even a bare
`Post(...)` is unpublishable). `publish_due` iterates `queued` **only**. **`INV-08` — nothing publishes without
an explicit operator approval.**
**The publish contract.** `_publish_one` is the **sole** network-POST caller (`INV-09`). **CLAIM
(`queued→submitting`) is committed *before any network I/O*** — exactly right for a backend with **no
idempotency key**. Every ambiguous send **parks in `needs_reconcile` and is never re-POSTed.**
🔴 **The Postiz permalink trap.** `_postiz_permalink` **always returns `None` by design**. So the steady-state
happy path is `submitting → submitted → needs_reconcile → published` — **never `→ published` directly.**
🔴 **`INV-15` is the strongest invariant in the codebase**: `_JITTER_MAX < _STEP_MIN` is enforced by a
**module-level `assert`** — the only import-time assert in the tree. The schedule *cannot even be imported* in a
violating state.

---

### `S13` — Reconcile & Metrics (4) · `S14` — Learning (10)
`reconcile` `track` `metrics_schedule` `fanops_account_stats` — `adjust` `variant_*` `p4_dim_bias` `timing_bias` `validation_gate` `learn_doctor` `digest` `moment_hook_learning`

**Purpose.** Close the loop: find out what happened, and let it bias what happens next.
**Metric truth.** `track.pull_metrics` is the **sole** writer of `published → analyzed`. **IG reach is read from
the Meta Graph** — the sole IG metric reader.
**Actuator gates.** Every **reversible** bias actuator is DEFAULT-OFF + `learning_validated` + `p4_unlocked`
(≥8 posts × ≥2 values) + fail-safe + amplify-only.
🔴 **Risk (`AR-07`), and it is the asymmetry that matters.** **The one IRREVERSIBLE actuator has the WEAKEST
gate.** `adjust.retire` writes `MomentState.retired` — which `reconcile_moments` **refuses to un-retire** — gated
**only** on `is_live_backend`, firing at **n = 3** analyzed posts. **Whether that is aggressive-by-design or an
oversight is not recoverable from the code.** The guards that *do* exist are real and considered, which is
evidence *for* intent. **Filed (`PD-3`), not fixed.**
🔴 **Risk (`AR-01`).** `reconcile` is the **sole reader** of `submitting` — and **three independent exclusions**
each strand a post forever.

---

### `S15` — Orchestration (3) · `S17` — CLI & Daemon (3) · `S18` — Health (6) · `S19` — Maintenance (3)
`pipeline` `pipeline_run` `pipeline_status` — `cli` `daemon` `init_flow` — `doctor` `health` `health_model` `cutover` `cutover_postiz` `audit` — `ledger_wipe` `paths_rebase` `lever_docs`

**`pipeline.advance()` is the whole engine.** Five phases: lock-free stage → short txn → **lock-free producer**
(warms artifacts, saves nothing) → the main reduce txn → out-of-lock reconcile+publish+digest.
**An uncaught raise in the reduce rolls back the ENTIRE pass, by design** — safe *because* the heavy artifacts
were warmed lock-free, so the next pass fingerprint-**skips** onto them and **recovers** the work.
🔴 **The gate asymmetry (`AR-06`).** `_reconcile_safe` is gated on `is_live_backend`; **`_publish_safe` is not.**
**The sole reader of `submitting` can be switched off while the writer keeps producing it.**
**All scheduling is launchd.** No cron, no timer. The plist **bakes a full `PATH`** at install — launchd supplies
a bare one, and **a stale baked PATH once killed every gate for three days.**
🔴 **`alive ≠ succeeding` (`AR-10`).** `daemon status` returns `alive` whenever the newest `run.log` line of **any**
kind is fresh. A pass that halts on a rotated key still logs stage lines. **The heartbeat — which fires only on a
successful pass — is already the success signal. The verdict discards it.**

---

### `S16` — Studio (28 modules)
**Purpose.** The operator cockpit: Home · Review · Schedule · Posted · Personas · Hashtags · Go-Live · Library.
🔴 **149 routes · 108 mutating · 0 authenticated · 0 CSRF-protected.** No session, no token, no `before_request`.
**The boundary is the network interface** (`app.run(host=…)`, default `127.0.0.1:8787`).
**This is a RECORDED, ACCEPTED decision** (`studio/CLAUDE.md`: *"no auth by design … declined as out-of-scope
for localhost"*) — recorded here as **ground truth, not as a recommendation.** But note: **any change to
`--host` removes the only control.**
**Durability.** Ledger writes always go through `Ledger.transaction` **inside the action**, never in the handler.
The browser never sees success before persistence. **The audit line, however, is written *after* the commit** —
so a crash between them **loses the audit line while keeping the state change.** *Audit is a record, not a
journal.*
🔴 **A running Studio never re-reads `.env`** (`COUP-02b`) — it `load_dotenv`s **once** at entry. The **daemon**
re-reads every tick. So a `.env` change made by the CLI or daemon **never reaches a running Studio.**

---

## Part 2 — Reader's guide to `kb/`

| File | Answers |
|---|---|
| [`kb/subsystems.json`](kb/subsystems.json) | What exists, what's in it, what it depends on. **Total partition — 127/127.** |
| [`kb/dependencies.json`](kb/dependencies.json) | 🔴 **The graph no prior cycle built.** The three graphs (G1 / G1c / G2), hubs, the one compile-time cycle, the 107 equal-or-higher-level lazy edges (**56 strictly upward**), and the 45-module **potential-dependency** SCC. |
| [`kb/ownership.json`](kb/ownership.json) | Who may create / mutate / read / retire every asset. Plus the **6 ownership pathologies**. |
| [`kb/lifecycles.json`](kb/lifecycles.json) | Per-entity lifecycle. **Defers to [`transitions.json`](transitions.json) (Cycle 2) as canonical** — re-verified, not restated. |
| [`kb/persistence.json`](kb/persistence.json) | Every persistent artifact: owner, schema, migration, backup, restore, caches, fingerprints. |
| [`kb/side_effects.json`](kb/side_effects.json) | Every FS / DB / network / subprocess effect, its ordering vs the durable writes, its rollback capability. |
| [`kb/configuration.json`](kb/configuration.json) | **73 env vars**, every read site, the precedence model, the propagation asymmetry. |
| [`kb/integrations.json`](kb/integrations.json) | Postiz · Zernio · Meta Graph · R2 · `claude -p` · ffmpeg · whisper · cv2 · launchd · Docker · keyring. |
| [`kb/maintenance.json`](kb/maintenance.json) | 59 CLI verbs; every recovery op; **and the missing recovery path.** |
| [`kb/invariants.json`](kb/invariants.json) | What is **actually** guaranteed — **classified by enforcement mechanism.** 7 are **FALSE as written.** |
| [`kb/risks.json`](kb/risks.json) | 15 architectural risks. Shapes, not incidents. |
| [`kb/unknowns.json`](kb/unknowns.json) | What **cannot** be proven. **Never speculated.** |
| [`kb/evidence.json`](kb/evidence.json) | How every claim was derived, and **what would falsify it.** |

### Which prior-cycle artifacts remain canonical

| Artifact | Status |
|---|---|
| [`transitions.json`](transitions.json) | ✅ **CANONICAL.** The AST transition census. Cycle 5 re-derived and confirms it. |
| [`route_contract.json`](route_contract.json) | ✅ **CANONICAL.** All 149 routes attributed. |
| [`IMPLEMENTATION_SEQUENCE.md`](IMPLEMENTATION_SEQUENCE.md) · [`REMEDIATION_OPTIONS.md`](REMEDIATION_OPTIONS.md) · [`CHANGE_INTERFERENCE_MATRIX.md`](CHANGE_INTERFERENCE_MATRIX.md) | ✅ **CANONICAL** (Cycle 4). **Cycle 5 designed no fixes.** |
| [`side_effects.json`](side_effects.json) (Cycle 3) | ⚠️ **SUPERSEDED** by [`kb/side_effects.json`](kb/side_effects.json) — one network-site correction. |
| `COUP-09` in [`couplings.json`](couplings.json) | ⚠️ **SUPERSEDED** — see [`CYCLE5_CORRECTIONS.md`](CYCLE5_CORRECTIONS.md). |
| [`INVENTORY.md`](INVENTORY.md) §§1–9 | ✅ Stands. Cycle 5 extends, does not retract. |
