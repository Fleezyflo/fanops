# Brief — Untangle control plane vs data plane

**Root issue in remediation order:** #5 after config truth → escalation policy → derived-signal primitives → **health contract** → **this brief** → honesty ratchets.

**Prerequisite (hard):** Do **not** start this brief until:

1. [`.orchestration/briefs/config-truth-plane.md`](config-truth-plane.md) has landed (single env registry + `Config` as only runtime door), **and**
2. A **health-contract** brief has landed (one typed health owner; live probe vs snapshot; observe vs mutate; `report_is_healthy` semantics locked).

Without those, any ownership matrix is fiction: the same key means two things, and “healthy” means three.

**Principle:** Control plane **declares intent and reads truth**. Data plane **executes and writes product state**. Observation **never mutates** the systems it diagnoses. Bring-up / process lifecycle has **one owner**, not a side effect of publish or doctor.

**How to execute:** implement the WPs lean against this brief + `AGENTS.md`. No CHANGE BID / bidder / APPROVE-theatre process.

**Grounded on:** workspace evidence 2026-08-13. Symbols primary (STD-DOC-01). Existing stack pointer in `config-truth-plane.md` §Out of order.

---

## Three lenses

| Lens | Answer |
|---|---|
| Best practice | Explicit planes with a machine-checkable affect graph: one writer per mutable surface; CP = intent + observe; DP = execute + product state; health = pure observation over shared typed model |
| Root cause | Planes grew as convenience seams (Studio dual-write, publish-time `ensure_up`, doctor scrape freeze, dual health bring-ups) without a single ownership contract — so “who owns X” is folklore |
| Leanest | Freeze the matrix + ratchet tests first; remove doctor mutations; collapse dual `ensure_up`; document CP/DP APIs; only then move cross-plane writers. Do not redesign the pipeline |

---

## Plane definitions (target)

| Plane | Role | Examples |
|---|---|---|
| **Control plane (CP)** | Operator intent, config durability, process install/stop, readiness display | Studio Go-Live, `daemon.install` / `stop`, `fanops doctor` (observe), Home health strips |
| **Data plane (DP)** | Unattended / scheduled product work | Daemon pump `fanops run --loop`, ingest→moments→render, `publish_due`, reconcile, Layer A scrape tick |
| **Observation** | Read-only diagnosis over CP+DP state | `health_model.build_health_report`, snapshot *readers*, Prometheus `/metrics` |
| **Infra lifecycle** (sub-plane) | Start/stop Docker/Postiz/launchd agents | Exactly **one** bring-up owner; not publish, not doctor |

---

## A — Affect matrix (as-built inventory)

### Shared mutable surfaces (α)

| Surface | Path / symbol | Today’s writers | Today’s readers |
|---|---|---|---|
| Env durability | `.env` via `autopilot.set_env_var` / `golive._dual_write` | Studio Go-Live; historically `daemon.install` responder path | All processes via `load_dotenv` / `Config` getenv |
| Process env | `os.environ` | Studio `_dual_write` (this process only) | Studio live; daemon **reloads .env each tick** (`cli` loop `load_dotenv(override=True)`) |
| Secrets | OS keychain via `secret_provider` | Studio `_dual_write` for secret keys | Config / Postiz / Zernio / Meta resolves |
| Accounts | `cfg.accounts_path` (`accounts.json`) | Studio golive → `accounts.*` writers | Doctor, publish routing, go_live gates |
| Personas | `cfg.personas_path` | Studio personas tab | Pipeline / captions |
| Ledger | `cfg.ledger_path` (`ledger.sqlite`) | Studio `actions*` (approve/schedule/…); DP pipeline mint; `post.run` publish transitions | Everyone |
| Hashtag cooldown | Layer A cooldown store (`_persist_cooldown`) | Layer A tick; **also doctor scrape probe** | Layer A / doctor |
| Learn doctor sidecar | `cfg.learn_doctor_path` | `learn_doctor.cmd_learn_doctor` | M4 / health field-shape |
| Dep / daemon / strip snapshots | `deps_health.json`, `daemon_strip.json`, `strip_metrics.json` | `health.refresh_*` | Studio Home / Go-Live strips (snapshot-only) |
| Launchd plists | `daemon.plist_path` + keeper + studio labels | `daemon.install` / `ensure` / `install_studio` / Studio golive install routes | `daemon.status`, keeper, doctor liveness |
| Heartbeat / run log | `cfg.log_path` | DP pump | `daemon.status`, `health_model.heartbeat_stale`, doctor |
| Studio generation | `FANOPS_STUDIO_GENERATION` (plist env) | `daemon.install_studio` | Studio `/_fingerprint`; daemon redeploy compare |
| Cutover scratch | `cfg.cutover_path` | Cutover harness (CLI + Studio `validate_learning`) | Doctor / validation gate |
| External APIs | Postiz / Zernio / Meta / IG private | Publish + metrics + cutover probe; scrape Layer A | Doctor probes, health deps |

### Component matrix

| | **Studio** | **Daemon** | **Doctor / health observe** | **Publish (`post/`)** |
|---|---|---|---|---|
| **Owns (target)** | Operator intent: Go-Live flags, accounts/persona edits, approval/schedule UI, wipe confirm | Process lifecycle: launchd agents, PATH/WD pin, generation stamp, keeper adopt | Nothing mutable — pure verdicts | Outbound post lifecycle for `queued` → terminal; provider payloads |
| **Owns (today, drift)** | Same + can kick DP (`kick_prepare`), run ingest, publish-now, cutover live post | Same + `ensure` may rewrite plist / kickstart; `FANOPS_AUTO_ADOPT` via raw getenv | **Mutates** scrape cooldown on probe fail; learn_doctor writes sidecar; docs still say read-only | Calls `postiz_lifecycle.ensure_up` (infra) before publish |
| **Reads** | Ledger, snapshots, doctor/golive readiness, daemon strip | Config (per-tick dotenv), ledger via run, heartbeat self | Accounts, config, ledger, launchd, network probes | `cfg.is_live`, backends, ledger queue |
| **Writes** | `.env`+environ+keychain; accounts; ledger (actions); audit; optional daemon install | Plists; reports; run.log; studio generation | Cooldown (bug); learn_doctor.json; (health writers are separate module) | Ledger publish states; external APIs; ensure_up side effect |
| **Must not write (target)** | Launchd heartbeats; scrape cooldown; provider “truth” outside Go-Live | Operator secrets; approval state; FANOPS_LIVE | Any product/control file | Config / launchd / doctor sidecars |

Evidence anchors (symbols):

- `studio.golive._dual_write`, `go_live`, `go_dryrun`
- `studio.actions_run.kick_prepare` → `daemon._fanops_bin` / `_daemon_path`
- `daemon.install`, `daemon.ensure`, `daemon.status`, `daemon.root_divergence`, `daemon.install_studio`
- `doctor` module docstring (“READ-ONLY”) vs `doctor._hashtag_scrape_check` → `_freeze_for` / `_persist_cooldown`
- `learn_doctor` → `write_json_atomic(cfg.learn_doctor_path, …)`
- `health_model.build_health_report`, `health.refresh_runtime_snapshots`, `health.ensure_up`
- `postiz_lifecycle.ensure_up` (called from `post.run.publish_due` / `publish_post`, `reconcile`, Studio actions)
- `cli` daemon loop: `load_dotenv(..., override=True)` each tick
- Config dual plane: see `config-truth-plane.md`

---

## B — Issue inventory (CPDP-*)

| ID | Title | Evidence | Symptom | Why CP/DP entanglement | Sev | Blocked on |
|---|---|---|---|---|---|---|
| CPDP-01 | Config truth split makes ownership undefined | `Config` vs `Settings`; façade bypasses (`ig_hashtag_scrape`, `daemon.ensure` getenv) | Doctor and runtime disagree on “what is set” | Cannot assign “CP owns config” if two planes define keys | **blocker** | config-truth-plane |
| CPDP-02 | No single health contract (live vs snapshot vs bring-up) | `build_health_report` vs `refresh_*` snapshots vs `health.ensure_up` vs `postiz_lifecycle.ensure_up` | UI can show green from stale snapshot while doctor fails live; publish starts Docker | Observation mixed with actuation | **blocker** | health-contract brief (not yet written) |
| CPDP-03 | Doctor mutates scrape cooldown | `doctor._hashtag_scrape_check` → `_persist_cooldown` | Running doctor changes Layer A freeze state | Observation writes DP control state | high | health-contract (observe-only rule) |
| CPDP-04 | Doctor advertised read-only; learn_doctor writes | `doctor` docstring; `learn_doctor.write_json_atomic` | Operators/agents trust “doctor never writes” | Control sidecar authored by “observe” verb | med | health-contract (classify learn_doctor as gated assay, not doctor) |
| CPDP-05 | Dual Postiz bring-up owners | `health.ensure_up` vs `postiz_lifecycle.ensure_up` | Two scripts/policies can start the same stack | Infra lifecycle not a plane with one owner | high | health-contract + this brief WP |
| CPDP-06 | Publish path owns infra bring-up | `post.run.publish_due` / `publish_post` → `postiz_lifecycle.ensure_up` | Data-plane publish side-effects Docker | DP executes CP/infra duties | high | CPDP-05 |
| CPDP-07 | Studio crosses into DP execution | `kick_prepare`, Run-tab ingest, publish-now, `validate_learning` cutover post | Same UI both configures and runs the machine | Acceptable as **explicit CP→DP commands** only if catalogued; today mixed with ownership folklore | med | this brief (catalog + boundaries) |
| CPDP-08 | Root / ledger split-brain | `daemon.root_divergence`, `FANOPS_ROOT` shell-only, plist `WorkingDirectory` | CLI doctor one ledger; daemon another | CP diagnosis against wrong DP state | high | config-truth (register bootstrap) + docs/ratchet |
| CPDP-09 | Go-Live dual-write vs resident processes | `_dual_write` updates Studio environ; daemon relies on per-tick dotenv; one-shot CLI needs restart | “I flipped LIVE but daemon still dryrun” windows | Config propagation not part of ownership contract | high | config-truth + this brief (propagation rule) |
| CPDP-10 | Circular health ownership | `health_model.daemon_liveness_check` imports `doctor._daemon_liveness_check`; doctor_report thin over `build_health_report` | “Who owns health?” unanswerable | Graph fiction | med | health-contract |
| CPDP-11 | CODEMAPS drift on health/doctor | `C8_ops_cli_daemon.md` still describes old `postiz_health` HTTP-only / doctor pure | Agents implement against stale map | Remediation guided by fiction | med | docs after code |
| CPDP-12 | Settings folklore | `settings` docstring “Built fresh per Config()” | False handoff story | Same as CPDP-01 narrative | med | config-truth WP4 |
| CPDP-13 | `.env` write not flocked like controlio | `autopilot.set_env_var` fixed `.env.tmp` (STD-PERSIST-01 note) | Concurrent Studio + CLI config writers can tear | CP durability not single-writer-safe | med | this brief or config-truth follow-on |
| CPDP-14 | No machine-checkable affect graph | No test/arch gate for “doctor must not write cooldown” etc. | Regressions return | Untangle not permanent | high | this brief WP ratchet |

---

## C — Why config + health first (concrete)

1. **Ownership of a key is undefined** until one registry says studio/bootstrap/secret/deprecated (`config-truth-plane`). Assigning “Studio owns FANOPS_LIVE” is incomplete while `Settings` and `Config` can disagree on validity and while daemon bypasses `cfg.auto_adopt`.
2. **“Healthy” is undefined** until live probe vs snapshot TTL vs bring-up are separated. Today Studio Go-Live can render `read_dep_snapshot` while doctor runs live probes; publish can mutate infra via `ensure_up`. An affect graph that says “doctor owns health” is false in three directions at once.
3. **Mutation under observation** (CPDP-03) cannot be fixed by renaming planes — it needs an observe-only health contract with a ratchet.
4. Sequencing already declared in `config-truth-plane.md`: escalation → derived-signal → **health contract** → **plane untangle**.

---

## D — Cross-plane writers (same surface, >1 component)

| Surface | Writers |
|---|---|
| `.env` / secrets | Studio Go-Live; daemon install (responder legacy); hand-edit; (CLI tools) |
| Scrape cooldown | Layer A (DP) + doctor probe (observe) |
| Postiz Docker stack | `health.ensure_up` + `postiz_lifecycle.ensure_up` (+ external reaper sibling) |
| Ledger | Studio actions + DP pipeline + publish |
| Launchd main label | `daemon.install` / `ensure` / Studio golive install + keeper kickstart |
| Health snapshots | `health.refresh_*` (writer) vs live `build_health_report` (no write) — two truths |

Ledger multi-writer is **intentional** under `Ledger.transaction` (single flock writer). The defect is uncatalogued CP vs DP *reasons* for write, not the flock itself.

---

## E — Gaps / Unclear

- **Resolved (2026-08-13):** `health.refresh_runtime_snapshots` callers are only:
  1. `health.ensure_up` (after bring-up),
  2. `studio.app_routes_golive` `/golive/health` (refresh then read snapshot),
  3. `cli` daemon `--loop` tick (post-pass strip refresh).
  So Studio Go-Live health is **write-on-read** (CP observe path mutates snapshot files), and the DP pump also writes the same strips — dual writers on `deps_health.json` / `daemon_strip.json` / `strip_metrics.json` (extends CPDP-02/D).
- Unclear: whether cutover/`validate_learning` should be classified as CP assay or DP publish (dangerous live post) — decide in health-contract or this brief WP1.
- Unclear: full list of raw `os.getenv` bypasses after config-truth lands (re-census).
- No Linear parent ticket yet for “plane untangle” (search 2026-08-13 found no dedicated MOL); mint after APPROVE if desired.
- Health-contract brief **does not exist yet** — must be authored as root #4 before executing this brief’s code WPs.
- Deep inventory subagent failed (API limit); parent pass completed the brief without it.

---

## F — File index covered (α)

`.orchestration/briefs/config-truth-plane.md` · `docs/CONFIG.md` · `docs/CODEMAPS/subsystem-traces/C8_ops_cli_daemon.md` · `docs/ENGINEERING_STANDARDS.md` (STD-PERSIST-01) · `docs/runbooks/RUNTIME.md` · `src/fanops/cli.py` · `src/fanops/config.py` · `src/fanops/daemon.py` · `src/fanops/doctor.py` · `src/fanops/health.py` · `src/fanops/health_model.py` · `src/fanops/learn_doctor.py` · `src/fanops/post/run.py` · `src/fanops/postiz_lifecycle.py` · `src/fanops/settings.py` · `src/fanops/studio/actions_run.py` · `src/fanops/studio/app.py` · `src/fanops/studio/app_routes_golive.py` · `src/fanops/studio/golive.py` · `src/fanops/studio/CLAUDE.md` · `src/fanops/CLAUDE.md`

---

## KEEP / non-goals

- Do **not** bundle config-truth or invent a second env registry here.
- Do **not** break: `go_live` sole writer of `FANOPS_LIVE=1`; no auto-publish; Review approval; runtime fail-open / doctor fail-loud dual policy.
- Do **not** merge ledger writers into one process — keep flock transactions.
- Do **not** add auth/CSRF to Studio localhost.
- Do **not** edit `.agents/lanes.json` to steal hot files; stop and report collisions.

---

## Target end-state

1. Published **affect graph** (this brief §A target columns) checked into docs + ratchet tests.
2. **Observation plane** never writes product/control state (doctor scrape freeze removed or moved behind an explicit `fanops hashtags …` repair verb).
3. **One infra bring-up owner** (`postiz_lifecycle` *or* `health.ensure_up`, not both); publish only *requests* readiness, does not own Docker policy.
4. **CP→DP commands** enumerated (kick, run, publish-now, cutover) as intentional bridges with audit + no hidden dual writers.
5. **Config propagation rule**: dual-write + daemon tick reload + documented one-shot restart; root_divergence remains loud.
6. Arch/docs (C8) match code; CODEMAPS regenerated or banner’d.

---

## Work packages (only after prerequisites)

### WP0 — Inventory freeze (read-only)

Refresh CPDP table against post-config-truth / post-health-contract tree. Census `refresh_*` callers and remaining getenv bypasses. No behavior change.

### WP1 — Classify every bridge

Tag each Studio/CLI verb: `cp.config` | `cp.process` | `cp.observe` | `dp.execute` | `infra.lifecycle` | `assay.dangerous`. Cutover must be explicitly `assay.dangerous`.

### WP2 — Observe-only doctor

Remove `_persist_cooldown` / `_freeze_for` side effects from `doctor._hashtag_scrape_check`. Probe may report; freeze stays Layer A’s job (or a named repair command). Update tests (MOL-879 family) accordingly.

### WP3 — Single infra bring-up

Collapse `health.ensure_up` vs `postiz_lifecycle.ensure_up` into one module API; other call sites become thin delegates. Publish retains “ensure before network” call but not a second policy.

### WP4 — Affect-graph ratchet

Tests (or arch INV): doctor path does not write cooldown file; only listed symbols write `.env`; only listed symbols call docker compose / ondemand script; snapshot readers never call live Postiz probe.

### WP5 — Docs + folklore purge

Update C8 / RUNTIME / studio CLAUDE ownership tables; supersede stale “doctor pure” claims with symbol-accurate text.

### WP6 — Gate

`./scripts/check.sh`; arch regen if needed; publish-lane branch for `config.py` if touched (prefer not); PR lists CPDP IDs closed.

---

## Acceptance

- [ ] Prerequisites (config-truth + health-contract) marked done with evidence
- [ ] Affect matrix target matches code (no dual undeclared writers on cooldown / Postiz start / FANOPS_LIVE)
- [ ] Doctor (and `/healthz` observe paths) perform zero product/control mutations
- [ ] One bring-up implementation; all callers delegate
- [ ] Ratchet tests fail when a new doctor write or second ensure_up appears
- [ ] Docs cite symbols; C8 no longer claims doctor is universally pure if any assay remains (must be renamed out of doctor)

---

## Lane / blast radius

| Area | Notes |
|---|---|
| `doctor.py` / `health*.py` | Primary; coordinate lanes |
| `post/run.py`, `postiz_lifecycle.py` | Publish lane adjacency |
| `studio/golive.py`, `actions*` | Studio lane |
| `daemon.py` | Process CP; avoid drive-by |
| `config.py` | Prefer **zero** edits here (owned by config-truth / publish hot file) |

---

## Risks

| Risk | Mitigation |
|---|---|
| Removing doctor freeze regresses multi-account scrape PASS | Retry/open_client without persist; or explicit repair verb |
| Collapsing ensure_up breaks autostart kill-switch | Preserve `_should_autostart` semantics in the surviving owner |
| Over-classifying Studio run/publish as forbidden | Keep bridges; require audit + catalog |
| Doing this before health-contract | Stop; graph stays fiction |

---

## One-sentence objective

**Make Studio/daemon/doctor/publish ownership a checked contract—observe never mutates, infra has one starter, config/health truth exists first—so control vs data plane cannot drift back into folklore.**
