# 02 — Repository Reality and Integrity

## 1. Document Control

| Field | Value |
|---|---|
| **Title** | Repository Reality and Integrity Reconstruction |
| **Purpose** | Establish the FanOps repository's present technical reality — what exists, what runs, what is wired, what is enforced — from primary evidence, independently of architectural narrative. |
| **Status** | Complete. Evidence-backed present-state reconstruction. |
| **Observation timestamp** | 2026-07-16 **18:19:32Z** (frame A) → **18:47Z** (frame B). Host TZ = **UTC+04**. |
| **Repository root** | `/Users/molhamhomsi/Moh Flow Fanops` |
| **Checkout branch / SHA** | `main` @ **`6d21749ffc49c77383f537d93b028cca0d69a447`** |
| **`origin/main` SHA** | **`6d21749ffc49c77383f537d93b028cca0d69a447`** (identical) |
| **Merge base** | `6d21749…` — **0 ahead / 0 behind** |
| **Scope** | Tracked source, git history, branches, worktrees, PRs, workflows, live GitHub settings, runtime/daemon state, live ledger, generated artifacts, local-only state. |
| **Exclusions** | No implementation/test/CI/config/runtime changes. No artifact regeneration. No test-suite execution (project rule). No live `fanops` verbs. |
| **Independence** | **No external reconciliation report was supplied.** This reconstruction was completed independently from primary evidence. §21 is therefore a one-line statement. |
| **Authorization** | This document records present technical reality. **It authorizes no change.** |

**Evidence limitations.** (1) Reading the live `.env` was **denied by the permission layer**; its key set is reported via secondary evidence (`config.py` read sites, subagent probes) and is marked medium-confidence — see `R-Q-04`. (2) Test-suite execution is prohibited by project rule (`CLAUDE.md`), so all test claims are **collection/static**, never "observed green locally"; CI results are used as the execution evidence. (3) Artifact freshness is judged by embedded provenance and source-revision comparison, never by regeneration. (4) mtime is treated as weak evidence only.

**Mutation attestation.** Exactly one repository path was created: this document (and its parent `docs/reconciliation/`). A read-only **copy** of the live ledger was taken to the session scratchpad (outside the repo) to avoid a WAL lock on the live database; the source was not opened for write. Nothing else in the repository, runtime, or GitHub was modified.

---

## 2. Executive Repository Reality

*(Written last. Every conclusion cites claim IDs.)*

**FanOps is a live, single-operator content pipeline that currently runs its own current code and publishes nothing.**

It is a pure-Python `src/` layout (**132** modules under `src/fanops/`), one console script (`fanops`), a Flask cockpit (149 route rules), and a launchd-resident daemon — clipping video into per-persona social posts across 5 accounts. It is **operationally live** (`FANOPS_LIVE=1`) with a 40 GB data root and a 347-post ledger `R-CLM-030`.

**The repository is technically coherent; its runtime is not delivering.** Four things matter:

1. **Zero runtime drift — the standing "stale daemon" risk is closed.** The resident daemon self-attests its revision in every heartbeat: `"code":"6d21749…"` == HEAD == `origin/main` `R-CLM-020`. It adopted new code **11 times today** `R-CLM-021`. This was broken until ~13:50Z today and was fixed by #688/#689 — the fix is **operationally confirmed, five hours old, on a single day's evidence** `R-CLM-022`.

2. **The live publish funnel is broken, and it is the closeout blocker.** `FANOPS_LIVE=1`, yet **0 posts have ever published** (`last_published_age_hours: "None"`) `R-CLM-031`. All **68** queued posts are TikTok; both TikTok accounts route to the **Zernio** backend; Zernio's media upload returns **HTTP 405** and 2 posts have already failed on it `R-CLM-032`. The remaining 68 are exposed as they come due `R-CLM-033`. Failure handling itself is correct (per-post `failed`, re-queueable, no run-halt) — the defect is an **external contract that drifted** under a client whose own docstring says the contract was "DISCOVERED LIVE 2026-06-29" `R-CLM-034`.

3. **Governance is real and honestly declared — but three guards are decorative, and the detector for that class is unbuilt.** Three planes (frozen snapshot / control registry / live GitHub) agree **byte-for-byte** on the required set `R-CLM-010`, the arch gate **passes** `R-CLM-080`, and the layer **self-reports 18 of 66 rules as not-fully-enforced** `R-CLM-085` — it underclaims more than it overclaims. Only **2** checks are merge-blocking `R-CLM-011`, but that is largely a **misattribution, not a hole**: the arch invariants really do block, via unmarked tests in the **required unit lane** (`CI-UNIT-ARCHGOV`), not via the non-required `ARCH-GATE` the docs credit `R-CLM-081`.

   The genuine defects are three validators that **cannot fire while being asserted as working**: **`check-locks.sh:12`**, whose `rg -n` prefix makes its `^\+` anchor unmatchable — collapsing the `required` lock-drift gate to a substring test, while the registry asserts it `verified-this-session` `R-CLM-012`; **ARCH-006's doc byte-compare**, which runs only in the non-required gate `R-CLM-082`; and **`field_authority.json:87`**, which claims ARCH-008/009 *"fail CI"* when `policy.py:144` makes them `WARNING` — **and the censuses they govern are drifting right now** (declared 35 subprocess sites vs **37** in code; 3 rmtree vs **5**) with the gate green `R-CLM-083`. That is this repo's own named signature defect — *"the doc names a mechanism that does not exist"* — occurring **inside the file titled Declaration of Canonical Authority**. The check that would catch all three (**CM-8**) is specified at `CONSTITUTION_MAINTENANCE.md:42` and explicitly **unbuilt**, gated on a validator (DC-3) that itself never runs `R-CLM-084`.

4. **Documentation is largely honest; its two failures are both in always-loaded files.** `docs/CONFIG.md` survives a mechanical audit with **zero undocumented vars** `R-CLM-041`. But `CLAUDE.md` sends every agent "FIRST" to `.reports/issue-register-2026-07-03.md`, which is **gitignored and untracked** — local-only authority in the instruction file every agent loads `R-CLM-040`; and it advertises a "Full 108-module map" whose own header claims `109/109` coverage of a tree that now holds **132** `R-CLM-042`.

**Do implementation, enforcement, documentation, and operational state agree?** Materially yes, with bounded, mostly-disclosed exceptions. The live governance artifacts are current (132/132, content-hashed, determinism-contracted) `R-CLM-013`. The frozen codemaps are labeled frozen with a correct precedence rule ("when prose and code disagree, the code is right") — their defect is an unretracted completeness claim, not a lie about currency `R-CLM-042`.

**Is the repository coherent enough for final integration?** **Coherent on main, materially divergent in runtime** (§24). Main is clean, current, and self-consistent. The runtime is live and *failing to publish*. That gap — not the code — is what blocks closeout.

---

## 3. Observation Frame

### 3.1 `origin/main`

| Field | Value | Evidence |
|---|---|---|
| SHA | `6d21749ffc49c77383f537d93b028cca0d69a447` | `git rev-parse origin/main` |
| Subject | `docs(hashtags): rebuild the diversity brief on measured data… (#693)` | `git log -1` |
| Commit time | `2026-07-16T20:39:07+04:00` (= 16:39:07Z) | `git log -1 --format=%cI` |
| Remote `pushed_at` | `2026-07-16T16:39:10Z` | `gh api repos/Fleezyflo/fanops` |
| Visibility | **public**, not archived, default branch `main` | same |

### 3.2 Current checkout

**Identical to `origin/main`.** `git rev-list --left-right --count origin/main...HEAD` → `0  0`. Merge base == HEAD. No staged changes, no modified tracked files.

### 3.3 Local-only state

| Item | State | Materiality |
|---|---|---|
| `docs/constitution/` (11 `.md`) | **untracked** | Superseded draft — see §16.1 |
| `.reports/*` except `.reports/architecture/` | **gitignored** (`.gitignore:62`, re-include `:73`) | **Material** — `CLAUDE.md` cites one as authority (`R-CLM-040`) |
| `.reports/structural_index.json` (108 modules, Jul 3) | untracked | Dead codemap-pipeline residue |
| `/Users/molhamhomsi/FanOps/` (40 GB data root + `.env`) | **outside the repo** | The live system's entire state |
| `~/Library/LaunchAgents/com.fanops.{run,keeper,studio}.plist` | **outside the repo** | Sole binding of the 40 GB data root |
| Stashes | **none** | — |

### 3.4 Open-change state

| Item | Count | Note |
|---|---|---|
| **Open pull requests** | **0** | `gh pr list --state open` → `[]` |
| Local branches | 63 | many `[origin/…: gone]` (post-squash-merge) |
| Remote branches | 275 | §16.3 |
| Worktrees | 26 | §16.2 |

### 3.5 Operational state

| Component | Status | Revision identity |
|---|---|---|
| `com.fanops.run` | **RUNNING** PID 9121, since `16:49:48Z` | heartbeat `code = 6d21749…` = **HEAD** |
| `com.fanops.studio` | **RUNNING** PID 9123, port 8787 accepting | same tree (editable install) |
| `com.fanops.keeper` | **ARMED**, `StartInterval` 120 s | `fanops daemon ensure` |
| Live ledger | `00_control/ledger.sqlite`, `schema_version = 11` | matches `ledger.py:190` |

### 3.5b Frame re-validation (required before finalization)

Baseline commands were re-run at close. **No material state change.**

| Check | Frame A (18:19:32Z) | Frame B (19:12:11Z) | Δ |
|---|---|---|---|
| `origin/main` (after explicit `git fetch`) | `6d21749…` | `6d21749…` | **none — main did not move during the investigation** |
| HEAD / ahead / behind | `6d21749…` / 0 / 0 | identical | none |
| Tracked-file changes | 0 | 0 | none |
| Daemon PID 9121 | running since 16:49:48Z | **same PID, elapsed 02:22:23** | **never restarted ⇒ still executing `6d21749`** |

The daemon's continuity across both frames is what lets `R-CLM-020` stand at close rather than only at first observation: no restart occurred, so no re-import of changed code was possible. Every section was analyzed under Frame A; Frame B confirms it.

### 3.6 Evidence gaps

| Gap | Consequence |
|---|---|
| Live `.env` read **denied** by permission layer | Key set is medium-confidence (secondary evidence only). Values never sought. |
| Test suite must not run locally | No local execution evidence; CI is the sole execution witness. |
| Zernio API not probed (live-verb rule) | The 405's *server-side cause* is inferred from ledger + client code, not reproduced. |
| Lock resolution not re-run | Cannot distinguish "locks didn't move" from "locks weren't regenerated". |

---

## 4. Repository Inventory

**914 tracked files.** Language mix: 559 `.py`, 171 `.md`, 71 `.html`, 63 `.json`, 17 `.js`, 9 `.sh`, 6 `.yml`.

| Path | Type | Lang | Purpose | Executable relevance | Active | Evidence / anomaly |
|---|---|---|---|---|---|---|
| `src/fanops/` (132 `.py`) | source | py | The engine: 95 top-level + `post/` (9) + `studio/` (28) | **Primary** — the console script's package | **ACTIVE** | `git ls-files`; editable-installed into the live venv |
| `tests/` (384) | tests | py | 378 test modules + fixtures | CI-only | **ACTIVE (CI)** | §13 |
| `docs/` (104) | docs | md | Governance, codemaps, ADRs, runbooks | Authority-bearing | mixed | §15, §20 |
| `.reports/architecture/` (97 tracked) | generated + declared | md/json | Cycle-7 KB. Only `derived/` (10) is gate-verified; `kb/`+`contract/` are **declared** and stale; ~36 loose `.md` have **zero consumers** | mixed | **partly ACTIVE** | §15 |
| `.reports/codemap-diff.txt` (1 tracked) | residue | txt | narrative of the **decommissioned** codemap sync | none — **zero consumers**; its generator was deleted at `2b81f81` | **DEAD ORPHAN** | §15, G-05 |
| `tools/arch/` (16) | validator | py | Architecture governance, stdlib-only | Run by `architecture.yml` | **ACTIVE (advisory)** | `architecture.yml:55` |
| `tools/ci/` (9) | validator | py | CI Control Registry validator (ADR-0100) | **CLI unreferenced** | **library-only** | `R-CLM-014` |
| `scripts/` (21) | scripts | py/sh | CI + operator utilities | 8 CI-invoked | mixed | §14.2 |
| `.github/workflows/` (4) | workflow | yml | ci, architecture, lane-guard, nightly | 3 active, **nightly disabled** | mixed | `R-CLM-015` |
| `.github/ci-control-registry.yml` | registry | yml | Intended executable inventory | **No workflow reads it** | declared-inert | `R-CLM-011` |
| `.githooks/` (3) | local gate | sh | pre-commit / pre-push | `core.hooksPath` set | **ACTIVE (local only)** | `git config core.hooksPath` |
| `.claude/` (26) | agent config | md/js/py | 10 `*.js` workflows, 7 hooks, agents | **No in-repo reference** | external-runner | `R-CLM-016` |
| `.agents/`, `.cursor/`, `.orchestration/` | agent config | md/json/py | Lane rules, worker protocol | `lanes.json` read by `lane_guard.py` | partial | `lane-guard.yml:46` |
| `requirements/` (2) | locks | txt | `ci-unit.txt` (38 pins) / `ci-e2e.txt` (77 pins) | Hash-pinned CI installs | **ACTIVE** | `ci.yml:49,142` |
| `clipping_account_archetypes.json` | data | json | Persona archetype levers | Read by `persona_levers.py` | **ACTIVE** | grep-confirmed |
| `pyproject.toml` | build | toml | fanops 0.4.0, py>=3.12,<3.14 | Defines the entry point | **ACTIVE** | `:42` |
| `PROBE.sh` | script | sh | One-off T01 cursor-agent envelope probe | **Zero references** | **ORPHAN** | `R-CLM-050` |

**Uncategorized areas:** none. Every executable and operational path above belongs to a category.

---

## 5. Build, Dependency, and Environment Reality

### 5.1 Build and package systems

| Facet | Value | Evidence |
|---|---|---|
| Backend | `setuptools>=68` / `setuptools.build_meta`, src-layout | `pyproject.toml:44-49` |
| Name / version | **fanops 0.4.0** | `pyproject.toml:2-3` (live heartbeat agrees: `fanops_version:"0.4.0"`) |
| Python | `>=3.12,<3.14`; live = CPython 3.12 | `pyproject.toml:5` |
| Console script | `fanops = "fanops.cli:main"` | `pyproject.toml:42` |
| Base deps (5) | pydantic, pydantic-settings, requests, python-dotenv, yt-dlp | `pyproject.toml:6` |
| Extras (7) | dev, transcribe, studio, compose, asr, framing, keyring | `pyproject.toml:9-39` |
| Live install | **editable** → `/Users/molhamhomsi/Moh Flow Fanops/src` | `direct_url.json` `{"editable": true}`; no site-packages copy |

**Why editable matters:** it is the link that makes the runtime-drift claim provable. The daemon imports the working tree directly, so "daemon revision" == "tree revision at process start" (`R-CLM-020`).

### 5.2 Dependency matrix (material)

| Source | Consumer | Type | Optional | Pinned | Runtime-critical | Fallback |
|---|---|---|---|---|---|---|
| pydantic / -settings | `models.py`, `settings.py` | base | no | floor only | **yes** | none |
| requests | `post/*`, `meta_graph.py` | base | no | floor only | **yes** | none |
| python-dotenv | `cli.py:845`, `settings.py:375` | base | no | floor | **yes** | none |
| yt-dlp | `ingest.py:479` | base | no | floor | for `pull` | `ToolchainMissingError` → exit 2 |
| flask | `studio/` | `[studio]` | **yes** | lock | Studio only | lazy import |
| opencv-headless | `framing.py` | `[framing]` | **yes** | lock | **render** | **FAIL-CLOSED** → exit 2 |
| moviepy | `compose.py` | `[compose]` | yes | lock | no | fail-open (copies base clip) |
| demucs / faster-whisper | `vocals.py`, `_fwrun.py` | `[asr]` | yes | **no lock** | no | fail-open → raw audio |
| keyring | `secret_provider.py` | `[keyring]` | yes | **no lock, no CI job** | no | degrade to `.env` |

**Lock reality:** `ci-unit.txt` (dev+studio+framing, 38 pins) and `ci-e2e.txt` (dev+studio+transcribe+compose+framing, 77 pins), both `--generate-hashes`, installed via `pip install --require-hashes` (`ci.yml:49,142`). Locks are load-bearing and **do not currently drift** (all base floors satisfied). The numpy split (2.5.1 unit / 2.4.6 e2e) is a legitimate `numba==0.66.0` ceiling.

### 5.3 Environment configuration

**58 `FANOPS_*` vars in `config.py` + 3 daemon vars + 15 non-FANOPS.** Precedence (`settings.py:375`): **`.env` overrides the shell env** (`load_dotenv(..., override=True)`), then keychain wins for secrets (`config_introspect.py:43`: keychain → `os.environ` → `.env` → default).

**Default-ON** (verified in source): `FANOPS_HASHTAG_TRENDS` `:431` · `FANOPS_CORPUS_AUTO` `:437` · `FANOPS_VISUAL_START` `:588` · `FANOPS_QUEUE_GATE` `:595` · **`FANOPS_SMART_FRAMING` `:611`** · `FANOPS_ISOLATE_VOCALS` `:665` · `FANOPS_BURN_SUBS` `:675` · **`FANOPS_ACCOUNT_CASTING` `:704`** · `FANOPS_POSTIZ_AUTOSTART` `:1108` · **`FANOPS_AUTO_ADOPT`** (`daemon.py:368`)

**Default-OFF:** `FANOPS_P4_DIM_BIAS` `:897` · `FANOPS_TIMING_BIAS` `:907` · `FANOPS_VARIANT_*` (7 vars) · `FANOPS_HOOK_ROUTER` `:712` · `FANOPS_IMPACT_CUT` `:721` · `FANOPS_INTRO_TEASE` `:731` · `FANOPS_AWARE_REFRAME` `:685` · `FANOPS_SHOW_EXTRAS` `:601` · `FANOPS_REALISTIC_CADENCE` `:1001` · +5 more

**Routing:** `FANOPS_ROOT` `:145` → default **cwd**; `FANOPS_POSTER` `:238` → `dryrun`, unknown→dryrun+warn; `FANOPS_LIVE` `:301` → derived; `FANOPS_RESPONDER` `:506` → `manual`; `FANOPS_LLM_TRANSPORT` `:135` → `claude`.

**Secrets (values never read):** `POSTIZ_API_KEY` `:348`, `ZERNIO_API_KEY` `:396`, `META_GRAPH_TOKEN` `:407`, `META_GRAPH_TOKEN__<SLUG>` `:1124-1132` (dynamic per-handle), `R2_ACCESS_KEY_ID` `:368`, `R2_SECRET_ACCESS_KEY` `:373`. `_SECRET_KEYS` = `{POSTIZ_API_KEY, ZERNIO_API_KEY, META_GRAPH_TOKEN}` (`secret_provider.py:11`).

**Live env (secondary evidence — `.env` read denied):** `FANOPS_LIVE=1`, `FANOPS_RESPONDER=llm`, `FANOPS_LLM_TRANSPORT=claude`, `FANOPS_HASHTAG_TRENDS=1`, `FANOPS_REALISTIC_CADENCE=1`, `FANOPS_OPERATOR_TZ=America/New_York`, `FANOPS_CREATIVE_VARIATION=1` (**dead**). `FANOPS_POSTER` **absent** → `poster_backend` → `dryrun`, so per-channel `accounts.json` routing is the publish truth.

### 5.4 Environment integrity findings

| ID | Finding | Severity |
|---|---|---|
| **E-01** | **`scripts/check-locks.sh:12` cannot fire.** `rg -n` prepends `N:` to each line, so the `^\+` anchor never matches; the guard collapses to a substring test for `dependencies`, satisfied only by `pyproject.toml:6`. **Every extra** (`:9,11,14,20,27,35,39`) and **`requires-python` `:5`** can change while it prints OK. Also fail-open (`\|\| true`), PR-only (`ci.yml:44`) though the repo **does** push directly to main. Registry asserts it `verified-this-session` (`ci-control-registry.yml:150,163`) — **overstated**. | **High** |
| **E-02** | `[keyring]` extra is in **no lock and no CI job**. `[asr]` is excused in writing (`lock-deps.sh:15`); `[keyring]` is excused nowhere. | Medium |
| **E-03** | **`demucs` has no presence check anywhere** → silent quality degrade (returns raw audio, warns only) (`vocals.py:49`). | Medium |
| **E-04** | `FANOPS_DAEMON_INTERVAL` is baked into the live plist, read at `daemon.py:185`, and appears in **neither `docs/CONFIG.md` nor `Settings`**. | Low |
| **E-05** | **`Settings` ≠ `Config` drift:** 4 live vars have no `Settings` field — `FANOPS_ROOT`, `FANOPS_AUTO_ADOPT`, `FANOPS_POSTIZ_ONDEMAND`, `FANOPS_DAEMON_INTERVAL` — so `fanops config` **cannot show the operator the data root or the code-adoption switch**. No test binds the two files. | Medium |
| **E-06** | `.env.example` documents vestigial `ANTHROPIC_API_KEY` and omits `META_GRAPH_TOKEN` / `META_IG_USER_ID`, which the live system requires. | Low |
| **E-07** | `FANOPS_ROOT` is set in **neither `.env` nor any plist** → `root_source='cwd'`; an **untracked plist's `WorkingDirectory` is the only thing binding the 40 GB data root**. `daemon.root_divergence` (`daemon.py:53-64`) exists to catch the resulting split. | Medium |

**External binaries (undeclared as Python deps).** Only `ffmpeg`/`ffprobe`/`yt-dlp` are gated on the ingest path; **no automatic presence gate exists for ffmpeg/ffprobe/whisper/yt-dlp/demucs** — `fanops doctor` is operator-only (4 callers, never the daemon). The only automatic gate is `_check_preflight` (`cli.py:942`), covering **only** the LLM binary + poster creds. `espeak` is **not used in `src/`** (docstring prose only; real use is tests/CI).

---

## 6. Executable Entry-Point Register

**Scale:** 1 console script → **45 top-level CLI verbs + 17 subcommands**; 3 launchd agents; 1 internal subprocess entry; 2 tool packages.

| Entry ID | Name | Type | Defining location | Invocation source | Config/env | First downstream | Side effects | Active state | Evidence | Failure behavior |
|---|---|---|---|---|---|---|---|---|---|---|
| **E-01** | `fanops` | console script | `pyproject.toml:42` → `cli.py:708` | human + launchd | `Config()`, `load_dotenv` `cli.py:845` | `_dispatch` `cli.py:1238` | per-verb | **ACTIVE** | `.venv/bin/fanops` | typed ladder `cli.py:855-905` → exit 1/2, never a traceback |
| **E-02** | `fanops run --loop --interval 600` | **daemon** | `cli.py:1432` | **launchd** `com.fanops.run` | `FANOPS_DAEMON_INTERVAL=600` | `_check_accounts`→`_check_preflight`→`_cmd_run_pass` | ledger W, network, LLM subprocess, ffmpeg | **ACTIVE — PID 9121** | `launchctl list`; `ps`; heartbeat | `run halted:` → stderr; loop survives |
| **E-03** | `fanops studio` | **app** | `cli.py:1395` | **launchd** `com.fanops.studio` | `[studio]` extra | `ensure_up`→`create_app`→`app.run` | **starts Docker + Postiz**, HTTP :8787 | **ACTIVE — PID 9123** | TCP probe | port-busy guard `cli.py:1408` → exit 0 |
| **E-04** | `fanops daemon ensure` | **job** | `cli.py:628` → `daemon.py:338` | **launchd** `com.fanops.keeper`, 120 s | PATH, HOME | `_confirm_loaded`/`_load_plist`/kickstart | **rewrites plist, restarts pump** | **ACTIVE — armed** | `launchctl list` → `- 0` | `daemon: {e}` exit 2 |
| **E-05** | `python -m fanops._fwrun` | internal subprocess | `_fwrun.py:86,96` | **`transcribe.py:137`** | `FANOPS_ASR_MODEL`, `[asr]` | `_load_model` → faster-whisper | atomic `<stem>.json` | **ACTIVE (dynamically selected, CLI-invisible)** | `transcribe.py:137` | FAIL-LOUD → source parked retriable |
| **E-06** | `python -m tools.arch` | CI tool | `tools/arch/__main__.py` | `architecture.yml:55` | stdlib-only | 10 verbs | writes `.reports/architecture/` on `regen` | **ACTIVE (CI, advisory)** | `architecture.yml:55` | exit 1, job-fatal, **not merge-blocking** |
| **E-07** | `python -m tools.ci` | validator CLI | `tools/ci/__main__.py` | **NONE** | — | `checks.run_static` | — | **UNREFERENCED** `R-CLM-014` | `grep tools\.ci .github/workflows/` → 0 | n/a |
| **E-08** | `python -m fanops` | — | **absent** | — | — | — | — | **DOES NOT EXIST** (no `__main__.py`) | `git ls-files` | n/a |
| **E-09** | `.githooks/{pre-commit,pre-push}` | local gate | `.githooks/` | `core.hooksPath` | local git config | scan-secrets, ruff, check.sh | blocks commit/push | **ACTIVE (this machine only)** | `git config core.hooksPath` | exit 1 |
| **E-10** | `PROBE.sh` | dev probe | repo root | **none** | — | — | — | **ORPHAN** | full sweep → 0 refs | n/a |
| **E-11** | 3 orphan scripts | operator | `mol164_canon_test_handles.py`, `operator/mol-11{6,26}-*.sh` | **none** | — | — | `mol164…` **mutates `tests/**` in place** | **ORPHAN** | 0 refs repo-wide | n/a |

**Declared in code but not installable:** `SIBLING_POLL_AGENTS` (`daemon.py:414-416`) declares `com.fanops.postiz-reaper` and `com.fanops.media-sync`; `sibling_agents_status()` (`:472`) is consumed by `doctor.py:406` and `studio/views.py:1001`. **No installer, plist renderer, or ProgramArguments exists in the repo for either** — both report "not installed" forever. `media-sync` *is* implemented, as an **out-of-tree, unversioned** `~/postiz-selfhost/media-sync.sh` `R-CLM-051`.

**Externally-invoked (source alone cannot prove use):** the 10 `.claude/workflows/*.js` have **zero in-repo references**; the three docs corroborating `CLAUDE.md`'s "load-bearing build workflows" claim all cite `CLAUDE.md` itself — **circular**. They are agent skill definitions consumed by an external runner; `fanops-phase-e.js:17` hardcodes an absolute machine path `R-CLM-016`.

---

## 7. Runtime Wiring and Reachability

### 7.1 Active wiring graph — `fanops run --loop` (PID 9121)

```
main(cli.py:708) → argparse → Config() → load_dotenv(cfg.root/".env", override=True)  [:845]
  → daemon.root_divergence(cfg)  [:847]   — WARN if cwd-root ≠ installed plist root
  → _dispatch [:1238] → "run" [:1432]
     ├ _check_accounts   [:1433→908]  Accounts.load(cfg).validate() → exit 2
     ├ _check_preflight  [:1434→921]  responder=llm ⇒ require `claude` on PATH → exit 2
     └ while True:                                                        [:1447]
          load_dotenv(override=True)  [:1448]   ← operator disk truth EVERY tick
          cfg = Config(cfg.root)      [:1449]
          _cmd_run_pass(cfg, base_time) [:1452 → :981]
          _heartbeat(cfg, s, origin="loop") [:1453 → :1113]
          time.sleep(600)             [:1458]
```

`_cmd_run_pass` (`cli.py:981`) — **the pass**:
1. `run_lease(cfg)` — workspace lease; `RunBusyError` skips the tick (`:1454`)
2. ≤10× { `get_responder(cfg).answer_pending` (`:995`) → **LLM subprocess `claude -p`**; `advance(cfg, base_time)` (`:996`) } until no awaiting (`:1002`)
3. `_gates_blocked_note` (`:1007→29`) — LOUD stderr
4. **`if cfg.is_live_backend:`** → `_learn_pass` (`:1015→135`) — **LIVE NOW**
5. `variant_amplify` (`:1031`), `p4_dim_bias` (`:1043`), `timing_bias` (`:1054`) — each own flag + `is_live_backend` + own try/except
6. `refresh_store_if_due` (`:1067`) — **not** live-gated, 12 h throttle; `refresh_account_stats_if_due` (`:1078`); `refresh_corpora_if_due` (`:1086`)

**Shutdown:** none. No signal handler, no lease cleanup on SIGTERM; survival rests on `KeepAlive{SuccessfulExit:false}` + `ThrottleInterval 60`.

**Code adoption is EXTERNAL by design** (`cli.py:1441-1446`): the in-process `os.execv` adopter was deleted; `_running_code_sha` (`:1100`) snapshots the SHA **once per process** and caches it (`:1109`) — deliberately, so the keeper's drift check can ever fire. This is the mechanism that makes `R-CLM-020` provable.

### 7.2 Active wiring graph — `fanops studio` → `create_app` (PID 9123)

`create_app` (`studio/app.py:271`) registers **149 route rules, zero duplicates**: Flask app `:272`; config incl. `MAX_CONTENT_LENGTH` `:273-275`; **9 Jinja filters** `:280-306`; **4 globals**; **5 context processors**; direct routes `:384-618`; `@app.errorhandler(ControlFileError)` `:635`. `ensure_up(cfg)` (`:1418` → `health.py:78`) has a **side effect on startup: `open -a Docker` + `docker compose up -d`**.

**`register_*_routes` are NOT Flask Blueprints.** `grep -rn "Blueprint" --include=*.py src/` → **0**. They are plain closures `def register_x_routes(app, cfg)` calling `@app.get/@app.post` on the app object. `studio/CLAUDE.md:13` asserting "Blueprints" is the **only** occurrence of that word in the tree `R-CLM-043`. *(Verified directly by this investigation after two subagents disagreed.)*

### 7.3 Dynamic discovery

| Mechanism | Site | Visibility to static analysis |
|---|---|---|
| Lazy in-function provider lambdas | `post/providers.py:19-27` | **Invisible** — every one flagged "zero callers"; **all live** |
| Lazy extra imports | `studio`, `compose`, `framing`, `asr`, `keyring` | invisible |
| Aliased import | `responder.py:18` (`as _gate_source_id`) | invisible |
| Re-export | `actions.py:25`, `views.py:24` | invisible |
| argparse `type=` callback | `cli.py:700` (`_http_url`) | invisible |
| Jinja filters/globals | `app.py:280-306` | invisible |
| Subprocess module entry | `transcribe.py:137` → `_fwrun` | invisible |

**This is why "zero callers" is a lead, not a verdict** (`src/fanops/CLAUDE.md`). Every dead-code claim in §18 was swept against this list.

### 7.4 Wiring defects

| ID | Defect | Severity |
|---|---|---|
| **W-01** | **`ensure()`'s plist fixed-point is PATH-dependent → latent pump-restart oscillation.** `daemon.py:352` computes `expected` via `_plist_spec`→`render_plist`→`_daemon_path()` (`:66`), which calls `shutil.which()` **against the calling process's PATH**. On-disk proof: `run.plist` carries the keeper's PATH (no nvm); `keeper.plist`+`studio.plist` carry the shell's (with nvm). Any operator `fanops daemon ensure`/`fanops up` rewrites `run.plist` to the shell form and calls `_load_plist` (`:360`) = bootout+bootstrap = **kills the pump mid-pass**; the keeper reverts it ≤120 s later with another restart. **Not currently firing** (`action=none`); arms on the next operator bring-up. | **High** |
| **W-02** | **The resident's stdout heartbeat never reaches disk.** `_heartbeat` (`cli.py:1116-1118`) promises stdout **and** `cfg.log_path`. Under launchd fd 1 is a file → Python block-buffers; the resident never exits → never flushes. `grep -c 6d21749 daemon.out` = **0** vs `run.log` = 10. No `PYTHONUNBUFFERED` in any plist. Delayed by one ~8 KB block (hours at ~600 B/tick), not lost. `daemon.status` unaffected (reads `cfg.log_path`). | Medium |
| **W-03** | Duplicate handler: `fanops lever docs` and `fanops threshold docs` dispatch to the **same** `cmd_lever_docs` at the same line (`cli.py:1313-1316`) with identical help strings (`:821`,`:824`). `threshold` is an undeclared alias. | Low |
| **W-04** | Hidden/split initialization: route registration interleaves through `create_app` — four `register_*` at `:440-449`, ~170 inline routes, then three more at `:621-631`. | Low |
| **W-05** | Orphan readiness rows (`R-CLM-051`) — see §6. | Low |
| **W-06** | **`.githooks` self-detection is inverted.** Installed `core.hooksPath` is **absolute**; `scripts/check.sh:33` and `setup-hooks.sh:10` string-compare against the literal `".githooks"` → `check.sh:34` prints *"policy hooks not wired"* on **every run despite the hooks being armed**, and `setup-hooks.sh` silently rewrites config. `tests/test_check_scripts.py:388` bakes in the relative form. Fails safe. | Low |
| **W-07** | No log rotation anywhere: `daemon.err` 7.5 MB, `studio.err` 9.1 MB, `run.log` 6.9 MB, monotonic. launchd rotates nothing. | Low |

---

## 8. Subsystem Reality Register

Boundaries derived from module organization, state ownership, and execution paths — **not** inherited from codemaps.

| Subsystem | Owned paths | Entry points | State owned | Public interface | Test coverage | Operational evidence | Maturity | Integrity findings |
|---|---|---|---|---|---|---|---|---|
| **CLI / ops** | `cli.py`, `daemon.py`, `health*.py`, `doctor.py`, `init_flow.py`, `autopilot.py` | E-01..E-04 | plists, `.run.lock` | 45 verbs | high | 3 launchd agents live | **mature** | W-01, W-02 |
| **Ingest** | `ingest.py`, `discover.py`, `frames.py`, `keyframes.py` | `fanops ingest/pull/discover/intake` | `01_inbox`, `02_sources` | `stage_inbox_candidates`, `ingest_staged` | high | 7 sources live | mature | `_catalogue_file` **dead** (`R-CLM-060`); 22 GB `.ingested/` unretained |
| **Clip / framing** | `clip.py`, `framing.py`, `framing_outcomes.py`, `reframe*.py`, `overlay.py`, `compose.py` | `fanops reframe/compose` | `03_clips` (1,864 dirs) | `render_reframed` | high (+`slow`) | 347 clips live | **mature** | Render limb dead (`R-CLM-061`) |
| **Moments / personas** | `pick.py`, `casting.py`, `personas.py`, `persona_levers.py`, `hookscore.py` | daemon pass | `personas.json` | `request_moments` | high | 347 moments, 347/347 single-owner | mature | `Personas.load` non-defensive (`R-CLM-044`) |
| **Caption / hashtags** | `caption.py`, `hashtags.py`, `fanops_hashtags.py`, `hashtag_hygiene.py`, `hashtag_migrate.py`, `meta_graph.py` | `fanops hashtags {refresh,discover,migrate}` | `hashtags.json`, `hashtag_budget.json` | `vet_hashtags` | high | **reach loop has produced 0 data** | **immature in practice** | `R-CLM-062` |
| **Crosspost / publish** | `post/` (9), `crosspost.py` | `publish_due`, `publish_now` | posts in ledger | `_publish_one` (sole network POST) | high + failure proofs | **0 published / 2 failed / 68 exposed** | code mature, **runtime failing** | **`R-CLM-032`** |
| **Metrics / learning** | `track.py`, `adjust.py`, `validation_gate.py`, `timing_bias.py`, `variant_*.py` | daemon post-loop | `cutover.json`, `timing_bias.json` | `apply_p4_dim_bias` | high | 0 analyzed posts → all inert | **unproven** | `R-CLM-063`, `R-CLM-064` |
| **Ledger** | `ledger.py`, `ledger_sqlite.py`, `ledger_bridge.py`, `ledger_wipe.py` | all | **`ledger.sqlite` (schema 11)** | `Ledger.transaction` | high | 1,063 rows, `integrity_check: ok` | **mature** | JSON snapshot unrestorable (`R-CLM-065`) |
| **Studio** | `studio/` (28) | E-03 | via Ledger + controlio | 149 routes | 121/150 routes HTTP-tested | PID 9123 live | mature | `R-CLM-043`, `actions.py:403` |
| **Governance tooling** | `tools/arch` (16), `tools/ci` (9) | E-06, E-07 | `.reports/architecture/` | 10 + 3 verbs | 25 negative controls | gate green | **arch mature; ci CLI unreached** | `R-CLM-014` |

---

## 9. Import, Call, and Dependency Integrity

### 9.1 Internal import graph

The **tracked, gate-verified** `.reports/architecture/derived/dependencies.json` is the authority (regenerated by `tools.arch`, byte-compared in CI). It covers **132/132** modules `R-CLM-013`.

Primary direction: `cli` → {`pipeline`, `post`, `studio`, `track`} → {`ledger`, `config`, `models`} → `controlio`. In practice **`models.py` and `config.py` are leaves**; `ledger.py` is the sole ledger gateway.

**Import-time side effects:** `config.py:24-25` writes `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` via `setdefault` at module import — a real import-time environment mutation (macOS CA fix), scoped and documented.

**Optional/lazy imports** are the documented pattern for all 5 optional extras and are the dominant source of false "dead" positives (§7.3).

### 9.2 Call-chain register (major behaviors)

| Behavior | Initiator | Sequence | External | Persistence | Failure exit | Tests |
|---|---|---|---|---|---|---|
| **Ingest** | `fanops ingest` | `stage_inbox_candidates`→`_stage_candidate` (`ingest.py:283`, lock-free sha+copy2+probe)→`_mint_candidate` (`:305`, in-lock) | ffprobe | `02_sources` + `01_inbox/.ingested/` | ffprobe absent → `ToolchainMissingError` exit 2; probe timeout → `degraded="probe_failed"` | high |
| **Daemon pass** | launchd | `run_lease`→≤10×(`answer_pending`+`advance`)→`_learn_pass`→biases→`refresh_*` | `claude -p` | ledger | `RunBusyError` → skip tick | high |
| **Advance** | pass | `advance()` opens **exactly 2 transactions** (`pipeline.py:479`,`:504`); the heavy work is a **lock-free prewarm** (`produce.run_all` `:486`) whose ledger is **discarded** — only on-disk artifacts survive | ffmpeg | `03_clips` | per-clip `ClipState.error` | high |
| **Publish** | `publish_due`/`publish_now` | both funnel to **`_publish_one`** — the sole network-POST caller: CLAIM (tight txn, re-read under flock, `queued`→`submitting` **before** network) → NETWORK (lock-free) → FINALIZE (merge `_NET_POST_FIELDS` into a **freshly loaded** ledger) | Postiz / **Zernio** / R2 | posts | `AuthError` **halts whole queue by type**; else per-post `failed` | high + transient-retry proofs |
| **Reconcile** | daemon/CLI | back-fills `public_url` (`reconcile.py:757`); escalates stuck `submitting` at 24 h/72 h (`:110-119`) | Postiz | posts | fail-closed verifiers (`:187`,`:231`) | high |
| **Track** | daemon | `_default_list_posts`→`GraphInsightsClient` (**sole IG metric reader**) | Meta Graph | metrics | budget fails **closed + loud** | VCR contract |

### 9.3 Dependency anomalies

| Anomaly | Evidence |
|---|---|
| **Hidden global mutable state** | `post/run.py:83` `_publish_throttle_last` — module-level dict enforcing `postiz_publish_per_min`. **In-process only by design**; documented; would need rework only if `fanops` ran multi-process. `reset_publish_throttle` (`:88`) is test-only. |
| **Direct access bypassing canonical services** | `hashtag_migrate.py:176,192` writes personas/hashtags **outside the lock** (reads unlocked at `:137`) — bypasses `controlio` + the store locks. |
| **Sibling-parity divergence** | `daemon.py:620` raw `write_text` for the studio plist while `:332`,`:358`,`:445` all use `write_text_atomic`. |
| **Duplicate abstraction** | `Settings` (pydantic) mirrors `Config` (`os.getenv`) with **no test binding them**; 4 vars exist only in `Config` (E-05). |
| **Circular dependencies** | None material found in the gate-verified graph. |

---

## 10. Data Shape and Flow Reality

### 10.1 Actual data objects

`models.py` typed models: `Source`, `Moment`, `Clip`, `Render`, `Post`, `Account`, `Persona`, `Batch`, plus enums (`PostState`, `ClipState`, `SourceState`, `RenderState`). **`SCHEMA_VERSION = 11`** lives in **`ledger.py:190`** (not `models.py`) and is **confirmed against the live DB** (`ledger_meta.schema_version = 11`) `R-CLM-030`.

Untyped JSON crossing boundaries: `accounts.json`, `personas.json`, `hashtags.json`, `cutover.json`, `hashtag_budget.json`, `timing_bias.json`, `account_stats.json`, `04_agent_io/**`.

### 10.2 Producer-consumer matrix (material)

| Object | Producer | Validation | Persistence | Consumer | Observed mismatch |
|---|---|---|---|---|---|
| `Source` | `_mint_candidate` `ingest.py:305` | pydantic | ledger `sources` | `pipeline.py:161` | **Born `pending`, not `catalogued`** (`ingest.py:321`, `queue_gate` ON) while the render loop keys on `catalogued` → new footage waits for a human promotion. **By design; contradicts the "ingest → catalogued" narrative.** |
| `Moment` | `request_moments` | pydantic + `affinities` len==1 | ledger `moments` | `crosspost` | none — 347/347 single-owner |
| `Clip` | `produce.run_all` | pydantic | ledger `clips` + `03_clips` | `crosspost` | none |
| **`Render`** | **nothing** | — | **0 rows** | — | **`R-CLM-061` — limb unreachable** |
| `Post` | `_mint_surface_post` `crosspost.py:234` | pydantic | ledger `posts` | `_publish_one` | `public_url`/`media_id`/`published_at`/`metrics_series` = **0 across all 347** |
| `hashtags.json` | `fanops_hashtags:85` (**floor path**) | shape | control file | `vet_hashtags` | **`reach: {}` — the measured path (`:127`) has never run** `R-CLM-062` |
| `cutover.json` | `track.py:369` auto-stamp | none | control file | `learning_validated` | **stale-true** `R-CLM-063` |
| `timing_bias.json` | `timing_bias.py:103-107` | none | control file | **NOBODY** | **write-only** `R-CLM-064` |

### 10.3 End-to-end flow — actual, with divergences

```
01_inbox ──_stage_candidate──▶ 02_sources (copy2)   ──┐  original ──▶ 01_inbox/.ingested/ (move, 22 GB, no retention)
                                                      ▼
                              Source(state=pending)  ◀── queue_gate ON ⇒ HUMAN PROMOTION REQUIRED (not "catalogued")
                                                      ▼
   daemon pass ─▶ answer_pending (claude -p) ─▶ advance ─▶ Moment(affinities=1) ─▶ hook ─▶ Clip(render)
                                                      ▼
                            crosspost._mint_surface_post ─▶ Post(state=awaiting_approval)   [347]
                                                      ▼
                              Studio Review ── approve_post ──▶ queued   [68, all TikTok]
                                                      ▼
                     publish_due ─▶ _publish_one ─▶ CLAIM(queued→submitting) ─▶ NETWORK
                                                      ▼
                    ┌── Postiz (IG) ──▶ permalink ALWAYS None ─▶ needs_reconcile ─▶ reconcile back-fills URL
                    └── Zernio (TikTok) ──▶ ✗ HTTP 405 on /media/upload ──▶ failed   ◀── THE BLOCKER
                                                      ▼
                              track ─▶ Meta Graph (IG only) ─▶ metrics ─▶ bias   [0 rows — never reached]
```

**Divergences from the documented pipeline:**
1. Sources are born **`pending`** and wait for a human promotion — not `catalogued`.
2. `_catalogue_file` (`ingest.py:427`) — the function the codemap names as the catalogue step — is **dead**; the live path is `_stage_candidate`/`_mint_candidate` `R-CLM-060`.
3. Media is stored **twice by design** (copy to `02_sources`, move original to `.ingested/`).
4. **A Postiz publish cannot self-promote to `published`** — `_postiz_permalink` unconditionally returns `None` (`postiz.py:90`), so `run.py:338` parks `needs_reconcile` (`:346`). Intentional; do not "fix".
5. **IG metrics come from Meta Graph regardless of publish backend** (`track.py:278,285-287`).
6. **The learning half of the loop has never executed** — 0 analyzed posts.

**Real partial-state window:** `run.py:329`→`:384`. `submission_id` is **not** persisted pre-network (the `fanops_` token is stamped at birth, `crosspost.py:246`); a crash between the network POST and finalize loses the real backend id — the post reads `submitting` with a token that 404s, escalated by reconcile at 24 h/72 h. Dedupe rests on `queued`-only iteration (`run.py:475`) + the claim flip.

### 10.4 Data integrity findings

| ID | Finding | Severity |
|---|---|---|
| **D-01** | `timing_bias.json` **write-only**; `timing_bias_hour_for` (`timing_bias.py:65-77`) **re-derives from the ledger** and gates only on `p4_unlocked` (`:36`), **never reading `cfg.timing_bias`** → **`FANOPS_TIMING_BIAS=off` gates a no-op file write while the schedule bias applies anyway.** Inert today (0 analyzed posts); **dated to activate** at ≥8 analyzed posts spanning ≥2 hours, with `p4_min_reach_gap` defaulting to 0.0. `digest.py:251` would then print "winner found (bias OFF)" while biasing. `config.py:175` + `timing_bias.py:82` both assert a reader that does not exist. | **High (latent)** |
| **D-02** | `cutover.json metrics_confirmed: true` is **stale-true** — auto-stamped from a real analyzed post that **no longer exists** (0 published/analyzed in the ledger). No wipe path resets it; `_snapshot_copy_control_files` (`ledger.py:331-333`) preserves `accounts.json`/`personas.json` but **not** `cutover.json`. A correctness gate whose proof became unfalsifiable. `variant_amplify` gates on `learning_validated` **alone** (`variant_amplify.py:166`). | **High (latent)** |
| **D-03** | **Error-sentinel mismatch** on `hashtag_budget.json`: `budget_remaining` (`meta_graph.py:520`) treats `_read_queries()→None` as fail-closed; `record_query` (`:493`) does `or []` — **silently rewriting a torn file as clean and destroying the 7-day history**. | Medium |
| **D-04** | Atomicity gaps — raw writes into stores the `controlio` rule governs: `meta_graph.py:533` (`hashtag_budget.json`, **locked but raw `write_text`**), `timing_bias.py:107`, `meta_graph.py:462`, `daemon.py:620`, `autopilot.py:46,68` (`.env`, fixed `.tmp` name — the exact hazard `controlio.py:24-26` documents, with a comment at `:50` falsely claiming atomicity), `agentstep.py:147`. | Medium |
| **D-05** | Latent: `_read_doc` (`ledger_sqlite.py:69-70`) keys existence solely on `ledger_meta` — **a meta-less DB with rows reads as empty and the next save deletes them.** | Medium |
| **D-06** | 30 of 347 posts carry no `batch_id`; 2 batch dirs + 1 src dir on disk have **no ledger rows at all**. | Low |

---

## 11. Persistence, Stores, and State Ownership

**The entire live state is outside the repository and untracked.** `.gitignore:10` ignores `MohFlow-FanOps/` wholesale. `ledger.py:2` calls the ledger **"git-versioned" — this is false** `R-CLM-066`.

| Store | Path | Owner | Writers | Readers | Schema | Txn / concurrency | Tracked | Integrity risk |
|---|---|---|---|---|---|---|---|---|
| **Ledger** | `00_control/ledger.sqlite` (3.0 MB) | `Ledger` | `Ledger.transaction` only | all | **v11**, `_MAP_NAMES` (10) ↔ `_to_doc` (10) — **no drift** | `BEGIN IMMEDIATE` + `busy_timeout` 30 s; full-replace; lock held load→mutate→save (`ledger.py:470`) | **no** | D-05 |
| `accounts.json` | `00_control/` | `Accounts` | 10 mutators, all in `_accounts_txn` (`:397-405`) | many | typed | locked | no | per-row leniency ✅ |
| `personas.json` | `00_control/` | `Personas` | 7 of 8 locked | many | typed | **1 unlocked writer** (`hashtag_migrate.py`) | no | **`R-CLM-044`** |
| `hashtags.json` | `00_control/` | `fanops_hashtags` | multi, **unlocked** | `vet_hashtags` | `{tags, reach}` | — | no | `reach: {}` |
| `cutover.json` | `00_control/` | `cutover` | multi, **unlocked** | `learning_validated` | flat | — | no | **D-02** |
| `hashtag_budget.json` | `00_control/` | `meta_graph` | locked but **raw write** | budget | list | flock | no | **D-03/D-04** |
| `timing_bias.json` | `00_control/` | `timing_bias` | 1 | **none** | — | none | no | **D-01** |
| `hashtag_bans.json` | `00_control/` | hygiene | atomic + locked | vet | — | ✅ | no | **best-behaved store** |
| `.run.lock` | `00_control/` | `pipeline_run` | `run_lease` | keeper | pid+stage | flock; `note_stage` (`:63-69`) does in-place `ftruncate`+write **without the flock** (documented, `fail_open`-wrapped) | no | low |
| Media | `01_inbox` 21 G, `02_sources` 15 G, `03_clips` 2.1 G | pipeline | ingest/produce | render/publish | — | — | no | 22 GB `.ingested/` + ~50 leaked `fanops-shrink-*` dirs + 2,083 unswept `04_agent_io/requests/` |
| Keyring | OS keychain | `secret_provider` | `set_secret` (verifies read-back `:82-90`) | 4 keys | — | — | n/a | R2 keys **excluded** |

**State-ownership matrix.** *Single-owner:* ledger, `hashtag_bans.json`, keyring. *Multi-writer-with-lock:* `accounts.json`, `personas.json`. **Multi-writer-without-lock:** `hashtags.json`, `account_stats.json`, `cutover.json`, `.env`, `04_agent_io/manifests/`. **Bypass:** `hashtag_migrate.py:176,192`. **Generated state mistaken for source truth:** `.reports/structural_index.json` (§15).

**Do tests mutate real state?** **No.** A grep for writes outside `tmp_path` returns empty — a genuine positive.

---

## 12. Side Effects and External Integration Reality

**Headline: 0 of 21 HTTP calls lack a `timeout=`.** 34 of 37 subprocess sites are bounded; the 3 exceptions are defensible (`open -a Docker` — instant launcher + bounded poll; `git` version stamp — local/fast; `Popen(start_new_session=True)` — **detached by design, a timeout would be wrong**).

| Integration | Caller | Auth | Timeout | Retry | Idempotency | Fallback | Tests |
|---|---|---|---|---|---|---|---|
| **Postiz publish** | `post/postiz.py:392` | `POSTIZ_API_KEY` keyring→env | **30 s** | 4×, jittered — **ConnectTimeout + 429 only** | **No key — compensated**: 5xx/ReadTimeout → `needs_reconcile`, **never re-POST** | 401 → `PostizAuthError` **halts queue by type** | mocked + transient-retry proofs |
| **Postiz media** | `postiz.py:242,182` | same | 120 s | none | — | ≥300 → `RuntimeError`, **body withheld** | mocked |
| **Zernio publish** | `post/zernio.py:235` | `ZERNIO_API_KEY` keyring→env | 30 s | 4× ConnectTimeout only; **ConnectionError parks immediately** | same discipline | 401 → `ZernioAuthError` halts | mocked |
| **Zernio media** | **`zernio.py:141,155`** | same | 30/120 s | none | — | **≥300 → RuntimeError — THE 405** | mocked |
| **Meta Graph** | `meta_graph.py:158,412,102` | `META_GRAPH_TOKEN` (+ per-handle) keyring→env | **20 s** | none | read-only | fail-**soft** → `None`; **budget fails CLOSED**; scope refusal loud | **VCR cassettes, `record_mode=none`** → a missing cassette errors, never a silent live call |
| **Cloudflare R2** | `postiz.py:152` (hand-rolled SigV4, no boto3) | **`R2_*` — bare `os.getenv`, NO keyring** | 120 s | none | **content-addressed key (sha256[:32]) → naturally idempotent** | ≥300 → body withheld | mocked |
| **LLM `claude -p`** | `llm.py:225` | **operator's `claude login` OAuth — deliberately NOT `ANTHROPIC_API_KEY`** (`llm.py:12-24`) | **300 s** | 4× on `{429,503,529}` | generator — safe | typed ladder | `test_llm.py` |
| **TikTok oEmbed** | `post/metrics.py:446` | public | 20 s | none | read-only | **fail-CLOSED** — unverifiable never accepted | injected |
| **whisper / demucs / ffmpeg** | 20 sites | none | bounded (whisper length-scaled, 2700 s floor) | model downgrade | `.part.mp4` + `os.replace` | fail-open | integration lane |

### Integrity findings

| ID | Finding | Severity |
|---|---|---|
| **X-01** | **Secrets split is real and mis-drawn.** `resolve_secret` (keyring→env) covers exactly 4 keys. **`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY` use bare `os.getenv`** (`config.py:367-374`) and are absent from `_SECRET_KEYS` (`secret_provider.py:11`) — so `golive._dual_write:52` would write an **AWS-SigV4 HMAC signing key to plaintext `.env`** while its sibling API keys get the keychain. **Knowingly documented** (`docs/CONFIG.md:34-35,145-152`) → an accepted design split, not a hidden bug — but it is the **wrong split**: R2 signs the media uploads the entire IG publish path depends on. Mitigated: SigV4 sends a derived signature, and bodies are withheld on error. | **Medium-High** |
| **X-02** | `.env.example` names **2** live keys against ~70 that `config.py` reads; **R2_\* and `FANOPS_MEDIA_PUBLIC_BASE` are absent entirely** — the copy-to-`.env` template omits the credentials the primary IG publish path requires. | Medium |
| **X-03** | **No socket-level network guard in `tests/conftest.py`.** Isolation rests on `_LEAKY_ENV` credential-stripping + injected `get`; VCR's `record_mode=none` covers only `@pytest.mark.vcr`. Nothing **structurally** prevents a new test from hitting live Postiz/Meta. | Medium |
| **X-04** | Circuit breaking absent — **replaced by something better**: `AuthError` halts the whole queue **by type** rather than burning it post-by-post (`run.py:353-354`). Deliberate and tested. | *not a defect* |
| **X-05** | Provider abstraction is **consistent**; `_publish_one` is the sole network-POST caller; IG metrics go only through `GraphInsightsClient`. **No direct-provider bypass found.** | *not a defect* |

---

## 13. Test Reality

### 13.1 Test inventory

**384 tracked files / 374 `test_*.py` / 5,379 tests collected, 0 collection errors** (`pytest --collect-only -q` — collection only; the suite is never executed locally, per project rule).

| Type | Files |
|---|---|
| unit (hermetic) | 340 |
| integration (real toolchain, `tests/integration/`) | 10 |
| integration (marked, in `tests/` root) | 7 |
| architecture / static AST ratchet | 9 |
| slow cross-face E2E (hermetic) | 5 |
| migration | 2 |
| contract (VCR, recorded real API) | 1 |
| **snapshot / operational** | **0 — none exist** |

`tests/conftest.py` is the **only** conftest in the tree. Mechanisms: `FANOPS_REQUIRE_STUDIO` collection abort (`:83-87`, `pytest.exit` at `pytest_configure`); `FANOPS_REQUIRE_E2E` skip→fail hookwrapper (`:90-103`, rewrites outcome **only** for `integration`-keyword items); `_LEAKY_ENV` 34-var strip (`:46-74`); `_hermetic_publish_env` autouse (`:116`, force-sets `FANOPS_ISOLATE_VOCALS=0`/`FANOPS_BURN_SUBS=0`); `_no_real_publish_sleep` autouse (`:106-113`); `vcr_config` scrubbing tokens → `DUMMY`, `record_mode` default **`none`**.

### 13.2 Test-execution matrix — **authoritative, from `--collect-only -m`**

| Marker expression | Selected | CI job | Required by branch protection? |
|---|---|---|---|
| `not integration and not slow` | **5,322** | `unit` (`ci.yml:61`) | ✅ **REQUIRED** |
| `integration and not ci_hook_regression and not asr` | **24** | `e2e` (`ci.yml:165`) | ✅ **REQUIRED** |
| `slow` | **30** | `e2e` (`ci.yml:179`) | ✅ **REQUIRED** |
| `ci_hook_regression` | **1** | `unit` (`ci.yml:86`, by path, asserts `$? -eq 1`) | ✅ REQUIRED |
| `asr` | **2** | `nightly.yml:79` — **WORKFLOW DISABLED** | ❌ **NEVER RUNS** |

`5322 + 27 + 30 = 5379` exactly → **`integration ∩ slow = ∅`**. **PR coverage = 5,377 / 5,379 (99.96%)**.

**Never executed in any lane:** `tests/integration/test_asr_real.py::test_asr_libraries_import` and `::test_demucs_cli_on_path_and_command_shape` — last run **2026-07-14** `R-CLM-015`.

Conditional skips: 63 `importorskip` (all `flask`, neutralized by `FANOPS_REQUIRE_STUDIO=1` in both jobs); 7 `skipif` (3 win32 — never fire on ubuntu; 3 ffmpeg — integration-marked so skip→fail catches them); **0 `xfail`**; 1 unconditional skip (`test_state_liveness.py:200-204` — runs 3 real assertions **then** skips; cosmetic).

### 13.3 Coverage by subsystem

**Uncovered modules: 1 of 132**, not 11. Ten first-pass candidates were **false positives killed by the sweep rule**: the 7 `studio/app_routes_*.py` (registered in `create_app`; 121/150 routes exercised over HTTP), `gate_keys.py` (aliased at `responder.py:18`), `studio/actions_segments.py` (re-exported `actions.py:25`), `studio/views_live.py` (re-exported `views.py:24`).

**The genuine gap: `cutover_postiz.py` (78 sloc) — zero test mentions**, reachable only via lazy dispatch (`cutover.py:58,68,79`), and it **touches a live Postiz backend**. `gate_keys.py` is live with 3 src refs and **zero test files**.

**Route-wiring gap:** **29 of 150 Studio route URLs are never exercised over HTTP** — incl. `/schedule/publish-due`, `/run/pull-metrics`, `/golive/daemon-install`, `/posts/recover`. **Bounded:** each is a thin wrapper over an `actions.*`/`golive.*` function that **is** unit-tested; the untested surface is form-parsing + panel-rendering only.

### 13.4 Test integrity findings

| ID | Finding | Severity |
|---|---|---|
| **T-01** | **Tests writing persistent state: ZERO.** A grep for writes outside `tmp_path` returns empty. Genuine positive. | *positive* |
| **T-02** | **The swallow ratchet miscounts in both directions.** `tests/test_swallow_ratchet.py` baseline = **203** handlers / 49 files; actual at HEAD = **201** → **2 handlers of silent regrowth pre-authorized**. Its allowlist (`:37`) recognizes only literal `fail_open`/`getLogger`/`get_logger`/`warning` — but the house idiom is `log = get_logger(cfg)` then `log(...)`, **invisible to it** → **44 false positives** (`pipeline.py` all 12, `produce.py` all 8, `stitch_render.py` all 4, `compose.py:104,157` — `_failopen`, *one underscore* from recognition). True silent ≈ **157**, not 201. Its nested-funcdef guard (`:25-26`) is **decorative**: it `continue`s on a `FunctionDef` intending to prune the subtree, but `ast.walk` queues children *before* yielding, so it prunes nothing. It also **under**-counts (`run.py:190` logs only `if cfg is not None` yet scores non-silent). | **Medium** |
| **T-03** | **No socket-level network guard** (X-03) — nothing structurally prevents a new test from hitting live Postiz/Meta. | Medium |
| **T-04** | Shell validators have **zero tests**: `scan-secrets.sh`, `check-locks.sh`, `ci_env_probe.py`, `base_install_smoke.py`. This is the direct cause of E-01. | **High** |
| **T-05** | Mocks bypassing real contracts — **mitigated by design**: publish/Graph seams are stubbed, but `test_meta_graph_contract.py` + 3 VCR cassettes pin the **recorded real** shape, and `integration/` runs real ffmpeg/whisper. | *not a defect* |
| **T-06** | Failure paths **are** tested — `test_publish_transient_retry.py`, `test_publish_transient_network_mol125.py`, `test_fail_open_primitive.py`, `test_transcribe_timeout.py`, `test_daemon_exec_fail.py`, `test_meta_graph_contract.py`. 130 of 372 files touch failure constructs. | *positive* |

Sibling ratchet `tests/test_internal_prints_routed.py`: 9 internal modules must have 0 `print()`; `cli.py` pinned at `_CLI_PRINT_COUNT = 168` (was recorded 165 at census time; the test is authoritative and the copy is corrected for archive).

---

## 14. CI, Validator, and Repository-Control Reality

### 14.1 Workflow register — 4 files, 11 jobs

| Workflow | Trigger | Job (`name:` = **the status-check context**) | Command | Required? |
|---|---|---|---|---|
| **CI** `ci.yml` (`contents:read, actions:read`) | push[main], PR[main] | `unit (fast, no toolchain)` `:28` | secret-scan→lock-drift→env-probe→ruff→pytest→SLO→hook-verify | ✅ **REQUIRED** |
| | | `base install (no extras) refuses smart-framing` `:97` | `base_install_smoke.py` in a fresh venv | ❌ |
| | | `real-tooling E2E (must run, not skip)` `:117` | apt ffmpeg/espeak → integration → slow | ✅ **REQUIRED** |
| | | `ci-timing artifact (main only)` `:195` | merge timing partials | ❌ |
| **architecture** (`contents:read`) | push, PR, cron `17 5 * * 1` | `gate (drift + policy + registries)` `:41` | `python -m tools.arch ci` | ❌ |
| | | `impact report` `:68` | `tools.arch impact --strict` | ❌ |
| | | `negative controls (validator effectiveness)` `:102` | `tools.arch selftest` (path-selected) | ❌ |
| | | `scheduled reconciliation` `:151` | `regen`+`docs`+untracked-aware drift | ❌ |
| **lane-guard** (`contents:read, pull-requests:read`) | PR[main] | `lane file-ownership + cross-PR collision` `:29` | `lane_guard.py` + `pr_collision_guard.py` | ❌ |
| **nightly** (`contents:read`) | cron `0 3 * * *`, dispatch | `dependency audit (pip-audit)` `:22` | `pip-audit` (`continue-on-error`) | ❌ **DISABLED** |
| | | `[asr] toolchain smoke (nightly)` `:51` | `-m "integration and asr"` | ❌ **DISABLED** |

**No workflow has write permissions. All actions are SHA-pinned.** Recent runs: **20/20 success** (`gh run list --limit 20`).

### 14.2 Validator register

| Path | Rule | Invoked | Failure | Bypass / fail-open |
|---|---|---|---|---|
| `scan-secrets.sh` | 6 secret patterns in added diff lines | `ci.yml:37` | exit 1 | ⚠️ **PR-only** → **a direct push to main is unscanned** |
| **`check-locks.sh`** | dep change ⇒ lock regen | `ci.yml:45` | exit 1 | 🔴 **BROKEN — E-01**; `\|\| true` swallows all failure; PR-only |
| `ci_env_probe.py` | measure ffmpeg/cv2 | `ci.yml:52` | 🔴 **never fails** — no `sys.exit` | registry `:183` claims *"exits non-zero on mismatch"* — **false** |
| `ci_slo_gate.py` | pytest wall ≤135 s PR / 140 s push | `ci.yml:73` | exit 1/2 | fail-**closed** ✅ |
| `base_install_smoke.py` | base imports; cv2 absent; render **refuses** | `ci.yml:114` | exit 1 | none ✅ (but job **not required**) |
| `lane_guard.py` | no cross-lane hot-file edit | `lane-guard.yml:46` | exit 1 | ⚠️ **4 fail-opens**; no `LINEAR_API_KEY` → SKIP on the very branches needing it |
| `pr_collision_guard.py` | no hot file in 2 open PRs | `lane-guard.yml:50` | exit 1 | ⚠️ 2 fail-opens — any `gh` error → exit 0 |
| `tools/arch` | **21 policy rules** (18 blocking, 2 warn, 1 info) + drift + registries | `architecture.yml:55`; **and via `unit`** (`test_arch_governance.py`) | exit 1 | gate job **not required**, but the invariants **are** carried by the required unit lane |
| `tools/arch selftest` | **25 negative controls (NC-01…NC-25)** covering all 21 rules | `@slow` → **required `e2e`** | exit 1 | ✅ **REQUIRED** |
| `tools/ci` | **DC-1..DC-6** | **only** via `unit`/`test_ci_registry_validator.py` | — | **CLI never invoked; DC-3 (live probe) never runs automatically** |

**Never run in CI (13 scripts):** `check.sh` (hook-only, lint-only unless `FANOPS_LOCAL_TESTS=1`), **`check-full.sh` (nothing calls it at all)**, `check_scope.py`, `repo_sweep.py`, `orchestrate.py`, `setup-hooks.sh`, `lock-deps.sh`, `gen_framing_vectors.py` (deliberate), `codemap_extract/{ast_extract,build_graphs}.py` (**orphaned — the decommissioned pipeline**), `mol164_canon_test_handles.py` (**orphan; mutates `tests/**` in place**), `operator/mol-11{6,26}-*.sh` (orphans).

### 14.3 Repository settings (live, measured 2026-07-16)

| Setting | Value |
|---|---|
| Required status checks | **exactly 2**: `unit (fast, no toolchain)`, `real-tooling E2E (must run, not skip)` |
| `strict` (up-to-date) | true |
| **`enforce_admins`** | **false** |
| **`required_approving_review_count`** | **0** |
| `required_linear_history` / `conversation_resolution` / `signatures` | false / false / false |
| `allow_force_pushes` / `allow_deletions` | false / false |
| **Rulesets** | **none** (`gh api …/rulesets` → `[]`) — classic protection is the only gate |
| Merge methods | squash + merge + rebase all enabled; `delete_branch_on_merge: false` |

⚠️ **`enforce_admins: false` + 0 required reviews** means the two required contexts **do not bind the repo admin, who is the sole operator**. CI is the only gate, and it is advisory for the only person who uses it `R-CLM-011`.

### 14.4 Enforcement gaps — ranked

| # | Gap | Evidence |
|---|---|---|
| **🔴 1** | **`check-locks.sh` — a `required`-classified control that misses its primary case.** Proven by execution against a realistic hunk adding `httpx` to an existing `dependencies = [` block: guard prints *"locks not required to move. OK."* and exits 0; the same input **fires** with `-n` removed. **Independently reproduced by two investigators.** | `check-locks.sh:11-13` |
| **🔴 2** | **The negative-control discipline structurally cannot reach the shell validators.** `test_ci_registry_validator.py:49-53` asserts coverage against a **hardcoded DC set**, never enumerating the registry's `controls:` list. So `CI-UNIT-SECRETSCAN` and `CI-UNIT-LOCKDRIFT` — both `classification: required` — have **zero** negative controls while the registry asserts `evidence_status: verified-this-session` (24 occurrences) and `failure_evidence: "check-locks.sh exits non-zero when pyproject deps change without lock regen"` (`:163`) — **disproved**. This is precisely the failure mode `architecture.yml:143-145` names: *"A MISSED control means the rule it names is DECORATIVE… it manufactures confidence."* | as cited |
| **🟠 3** | **`nightly` disabled; registry says `status: active`.** Registry `:575-617` declares `NIGHTLY-PIPAUDIT`/`NIGHTLY-ASR` active. GitHub: `disabled_manually`. **`NIGHTLY-ASR`'s own `deletion_consequence` — *"the [asr] real toolchain is never proven; an asr regression ships silently"* (`:612`) — is already the live state.** No registry field models workflow state; DC-1..DC-6 are all blind to it. pip-audit also dark. | `gh api …/actions/workflows` |
| **🟠 4** | **`jsonschema` is installed nowhere → the 8,366-byte registry schema is dead.** `tools/ci/registry.py:35-36` guards on `find_spec("jsonschema") is None → return []`; absent from `pyproject.toml` and both locks, which install `--require-hashes`. `_jsonschema_findings()` returns `[]` on **every** CI run. Fail-**soft** (hand-rolled shape checks still run) → decorative, not a false pass. | as cited |
| **🟠 5** | **The arch deep-gate's documented FAIL-OPEN is unreachable from its only caller.** `select.py:10-12` promises the deep gate RUNS when the changed-file list is undeterminable. But `architecture.yml:120` `git diff … > /tmp/changed.txt \|\| true` — **the `>` redirect creates the file before git runs**, and `\|\| true` swallows failure → `changed = []` → `deep_required([])` → `(False, 'no files changed')` → **negative controls SKIP**. The `except → changed = None` branch needs an IO error, impossible after a successful redirect. Contained by `fetch-depth: 0` + the cron `reconcile` job running selftest unconditionally. | proven by execution |
| **🟡 6** | 3 unique controls run **without power to block**: `base install`, `impact report`, `lane-guard` — the registry itself records *"no other control covers this"* (`:324`) and *"UNIQUE, no blocking backup"* (`:497`). By contrast `gate` and `negative controls` **do** have blocking backups in the required lanes (registry `duplicate_group: arch-drift-policy`). | registry |
| **🔴 1b** | **The generated-doc byte-compare has NO required owner.** `policy.py:122` scopes ARCH-006 to `derived/** + docs/ARCHITECTURE_GOVERNANCE.md`, BLOCKING. But `stale_docs()` (`drift.py:74`) has exactly two callers: `drift.py:204` (`all_stale` → `cli.py:79` → the **non-required** `gate` job) and `selftest.py:324` (NC-04 — which injects into a **temp** root, proving the *detector* fires, never inspecting the real repo). **The required unit test calls `drift.stale_artifacts()` only** (`test_arch_governance.py:38`) — derived JSON, **not the doc**. And ARCH-006 emits **no policy Finding** (`test_arch_governance.py:115`), so `test_no_blocking_policy_findings` doesn't cover it either. **Net: a hand-edited `docs/ARCHITECTURE_GOVERNANCE.md` goes red in `architecture/gate` and merges anyway.** `drift.py:78` even documents the hole it was written to close. **Verified directly.** | `drift.py:74,204`; `test_arch_governance.py:38` |
| **🟠 7** | **Unknowns ceiling is at 8/8 — zero headroom.** The next UNKNOWN anywhere reddens the **required** unit lane (`test_unknowns_do_not_grow_without_approval`). A live tripwire by design, but with no slack. | gate output |
| **🟡 8** | `registries.py:39-50` docstring says `today` is injected by CI; **no caller passes it** → exception-expiry verdicts depend on the runner's wall clock. `tools/ci/workflows.py` globs `*.yml` only (silent miss on `*.yaml`, reusable `uses:`, matrix expansion — all latent today). | as cited |

**The arch gate's real blocking topology (the load-bearing correction).** The `architecture/gate` job is **not** a required context — but `tests/test_arch_governance.py` is **unmarked**, so it is collected by `-m "not integration and not slow"` and runs in the **required `unit` job**. These therefore **do block**, via `CI-UNIT-ARCHGOV` rather than via `ARCH-GATE`: `test_derived_artifacts_are_not_stale`, `test_regeneration_is_deterministic`, `test_generated_artifacts_are_a_pure_function_of_the_source_tree`, `test_no_blocking_policy_findings`, `test_every_rule_is_reachable`, `test_registries_are_valid`, `test_unknowns_do_not_grow_without_approval`, `test_field_authority_declares_all_six_attributes`; plus `test_negative_control_is_detected` (`@slow`) via the required `e2e` lane. The registry states this exactly at `ci-control-registry.yml:70`: *"the sole REQUIRED arch enforcement until ARCH-GATE becomes a required context."* **So most docs crediting `ARCH-GATE` are misattributing the mechanism, not overclaiming the outcome — with two exceptions where the property has no required owner at all: gap 1b above, and CI-BASEINSTALL (§20).**

**Gate status: PASS**, verified by read-only invocation (`drift` regenerates into a `tempfile.mkdtemp()`, `drift.py:47-49`, and writes nothing to the repo — `git status` byte-identical before/after): `0 stale artifact(s), 0 BLOCKING policy finding(s); unknowns 8 open / 8 approved ceiling; verdict: PASS`.

**Credit where due.** `ci-control-registry.yml:38-53` is **honest** about the required-context gap: `rollout.phase: transitioning`, `current_required_contexts` (2, matching live **exactly**) vs `intended_required_contexts` (5), with DC-3 blocking on `live == current` and reporting the gap as informational. `classification: required` there means "required per ADR-0101 intent", **not** "live". That is good engineering, and it is why §24 classifies the enforcement gap as a **disclosed, bounded residual** rather than a contradiction.

---

## 15. Generated Artifacts, Reports, Indexes, and Caches

**The decisive split: two `.reports` families with opposite health.**

| Artifact | Generator | Command | Consumers | Tracked | Freshness | Evidence |
|---|---|---|---|---|---|---|
| **`.reports/architecture/derived/*.json`** (10) | `tools/arch` | `python -m tools.arch regen` | `tools.arch ci`, `test_arch_governance.py` | ✅ **tracked** | ✅ **VERIFIED CURRENT — 132/132 modules** | `derived/modules.json` = 132; `MANIFEST.json` |
| `.reports/architecture/*.md` (26) + `*.json` | Cycle 1-6 agents | — | humans | ✅ tracked | historical (Jul 15) | — |
| `.reports/architecture/kb/`, `contract/`, `governance/`, `prompts/` | agents | — | humans | ✅ tracked | historical | — |
| **`.reports/structural_index.json`** (108 modules) | `scripts/codemap_extract/ast_extract.py` | **decommissioned** (#543) | `full-trace-index.md` (prose only) | 🔴 **UNTRACKED** (`.gitignore:62`) | 🔴 **STALE — Jul 3; 108 vs 132** | measured |
| `.reports/{call_graph,import_graph,ruff_report,unreferenced_candidates}.json` | same | decommissioned | — | 🔴 untracked | 🔴 stale (Jul 3) | measured |
| **`.reports/issue-register-2026-07-03.md`** | agents | — | **`CLAUDE.md` "read FIRST"** | 🔴 **UNTRACKED** | local-only | **`R-CLM-040`** |
| `.reports/codemap-diff.txt` | decommissioned sync | — | none | ✅ tracked | orphan residue | the lone tracked non-architecture report |
| **`docs/CODEMAPS/full-trace-index.md`** | agents + AST | frozen | `CLAUDE.md`, `src/fanops/CLAUDE.md` | ✅ tracked | ⚠️ **FROZEN 2026-07-11, labeled** — but claims `109/109` | **`R-CLM-042`** |
| `docs/CODEMAPS/subsystem-traces/C1..C10` | 10 agents | frozen | humans | ✅ tracked | frozen; ≥2 stale caller claims (`C8:201`, `C1:146`) | `R-CLM-042` |
| `docs/ci/CI_CONTROL_INVENTORY.md` | `tools/ci` | `generated_view` | humans | ✅ tracked | generated-view; credits hand-edit protection to DC-5, which is `dc5_duplicate_ownership` | — |
| `ci-timing.json` | `ci_timing_report.py` | `ci.yml:211` | none | artifact only | per-run | — |
| `<src>.detect.json`, `.render.json` sidecars | `framing`, `clip` | runtime | render fingerprint | untracked (live root) | runtime cache | — |

### Freshness classification

- **Verified current:** `.reports/architecture/derived/*` (10 artifacts). The `MANIFEST.json` carries per-artifact **content digests**, `"generated_by": "tools/arch"`, `"source_inputs": ["src/fanops/**/*.py"]`, `hand_edits: "FORBIDDEN"`, and an explicit **determinism contract**: *"Regenerating with no source change MUST reproduce these digests byte-for-byte. Nothing here stamps a wall-clock time: a generated artifact that changes on every run trains reviewers to ignore its diff."* **This is the correct implementation of "a generated artifact is a pure function of source"** — no self-invalidating git stamp `R-CLM-013`.
- **Stale, untracked, local-only:** `.reports/structural_index.json` + 4 siblings (Jul 3, 108 modules).
- **Frozen and labeled:** `docs/CODEMAPS/*`. Line 1 of `full-trace-index.md`: *"Frozen 2026-07-11 — invariants map, not auto-synced. **When prose and code disagree, the code is right.**"* — an explicit, correct precedence rule.
- **Cannot verify:** `ci-timing.json` (artifact-only, not retained in-tree).

### The arch gate mechanism

`python -m tools.arch ci` = `drift` + `registries` (`cli.py:204`). `drift` **regenerates every DERIVED artifact and byte-compares** against the committed copy. Because line numbers are part of the extracted evidence, **any source line-number shift fails the gate until `regen` is re-run and committed** — the artifacts are pure functions of source, so this is intended behavior, not brittleness. The scheduled `reconcile` job additionally treats **untracked generator output as drift** (`architecture.yml:174`) — closing the "an 11th artifact appeared and `git diff` was blind to it" hole. **It never auto-commits** (`architecture.yml:163-165`): it fails with a reviewable diff. The gate is **currently green** on main.

### Generated-artifact findings

| ID | Finding | Severity |
|---|---|---|
| **G-01** | **`docs/CODEMAPS/full-trace-index.md` asserts coverage it no longer has.** Header `:3` — *"Files scanned: **109/109** src/fanops/*.py"*; `:34` — *"Every one of the **109** modules"*; `:51` — *"**109/109** modules covered"*; `:179` — *"**108/108** modules parsed"* (**self-contradictory within one file**). The tree holds **132**. **≥23 modules sit outside the "zero-omission" trace**, including `ledger_sqlite.py`, `ledger_bridge.py`, `secret_provider.py`, `settings.py`, `reframe.py`, `reframe_apply.py`, `pipeline_run.py`, `hashtag_hygiene.py`, and 6 `studio/*` — i.e. **both applied programs' newer surfaces and the ledger backend**. The freeze banner discloses *staleness* but never retracts the *completeness claim*, and states no magnitude. | **High** |
| **G-02** | **`CLAUDE.md` advertises the frozen artifact as current authority:** *"Full 108-module map… → `docs/CODEMAPS/full-trace-index.md`"* — a third number (108) for a 132-module tree, with **no freeze caveat**, in the file every agent loads. Mirrored at `src/fanops/CLAUDE.md`. | **High** |
| **G-03** | **The codemap's "deterministic layer" is unreproducible from a clone.** `full-trace-index.md:18-19` cites `.reports/structural_index.json` + `import_graph.json` as its evidence base; both are **gitignored** (`.gitignore:62`). A fresh clone has neither. **A tracked document's evidence base is untracked local-only state.** | **Medium** |
| **G-04** | 25 modules are absent from `structural_index.json` and 1 indexed path no longer exists — the measured drift of the decommissioned pipeline. Harmless *because* it is untracked and unconsumed by code; material only through G-01/G-03. | Low |
| **G-05** | Orphan bytecode: `scripts/__pycache__/codemap_drift.cpython-{313,314}.pyc` with **no** `scripts/codemap_drift.py`. | Low |

---

## 16. Local, Branch, Worktree, Stash, and Pull-Request State

**Headline: in 63 local branches, 275 remote branches, and 26 worktrees there are exactly TWO units of unmerged code.** Everything else is squash-merge residue. Main is not missing work — it is buried in dead scaffolding.

### 16.1 The untracked `docs/constitution/` — **DEAD RESIDUE, correctly quarantined**

| Check | Result |
|---|---|
| Ever landed in **any** ref? | **No** — `git log --oneline --all -- docs/constitution` → empty; zero hits across all 338 refs |
| Tracked live layer exists? | **Yes** — `docs/REPOSITORY_CONSTITUTION.md`, `docs/ARCHITECTURAL_LAWS.md`, `docs/ENGINEERING_PHILOSOPHY.md`, `docs/governance/` (7 files), landed as squash `e2cf862` |
| Is the draft dangerous? | **Yes — the §4.2 / GB-5 inversion is real** |
| Already adjudicated? | **Yes** — `docs/governance/EVIDENCE_RECONCILIATION.md:105-128` (tracked, on main) |

**The inversion, quoted verbatim:**
- Untracked draft `docs/constitution/LAWS.md:83`: *"**4.2** A transition MUST replace, not mutate (`model_copy(update=…)`), so it is safe even on a frozen model"* — **citing `STATE-PER-UNIT-ENUMS`**.
- Tracked law `docs/ARCHITECTURAL_LAWS.md:121`: *"**LAW-STATE-03** — A `Moment` is mutated by setattr, never `model_copy` (GB-5)"*.
- Tracked contract `.reports/architecture/IMPLEMENTATION_CONTRACT.md:65`: *"**GB-5** — No slice may convert a `setattr` on a `Moment` to `model_copy` — not even 'for consistency.' `Moment` is the only model with `validate_assignment=True`. **`model_copy` bypasses it anyway.** `cast_add`/`cast_remove` are correct *only* because of that setattr."*

The draft mandates the exact operation the live layer forbids, **and cites GB-5's own evidence key as support for the opposite rule**. Following §4.2 would silently break the per-persona ownership gate. The draft self-marks (`README.md:9`: *"⛔ SUPERSEDED — NOT AUTHORITY. NEVER LANDED. DO NOT CITE, DO NOT REVIVE."*), and `EVIDENCE_RECONCILIATION.md` records it as *"SUPERSEDED; NOT LANDED; NOT ABSORBED… wholly superseded, zero genuinely-missing knowledge."* **Contains zero code. No operator decision required — the disposition is already recorded.** `R-CLM-070`

### 16.2 Worktree register (26)

All 26 paths exist (**0 prunable**); **all are clean** except the primary's one untracked dir. **Not one worktree holds uncommitted work.**

| Classification | Count | Notes |
|---|---|---|
| primary checkout | 1 | `?? docs/constitution/` only |
| **completed-but-unmerged** | **1** | **row 2** — `fix/darwin-test-gate` @ `9107c07`, 66 behind / 1 ahead, living in **another session's `/private/tmp` scratchpad** ⚠ |
| **superseded — must not land** | **1** | row 8 — `cursor/mol-476-hook-author-always`; **main deliberately reverted it** |
| fully merged (ancestor of main) | 4 | rows 5, 10, 13, 23 |
| squash-merge residue | 19 | incl. row 25 (**LOCKED**, pid 51129 from Jul 15 — a dead session holding a lock on already-landed work) |

**24 of 26 are disposable.**

### 16.3 Genuinely unmerged branches — 2 units of 63

52 of 63 local branches are not ancestors of main; slug-grep against main resolved 29 as squash-landed; the remainder were adjudicated **at blob level, not by commit message**.

| Branch | Diffstat vs main (own files) | Verdict |
|---|---|---|
| **`fix/darwin-test-gate`** | `.claude/hooks/darwin_test_gate.py` **+58 (new)**, `.claude/settings.json`, `CLAUDE.md` +23, `tests/CLAUDE.md`, `tests/test_darwin_test_gate.py` **+67 (new)** — 5 files, **+201/-23** | **UNMERGED, complete, tested.** Proof: `git cat-file -e origin/main:.claude/hooks/darwin_test_gate.py` → **ABSENT** |
| **`fix/cursor-all-route`** | `src/fanops/llm.py` +31/-14, `tests/test_llm.py` +14/-4 | **UNMERGED, complete.** Proof: main has `llm.py:113 _CURSOR_SUPPORTS_VISION = False`; the branch sets `True`. No `--add-dir` on main. |
| `fix/cursor-agent-trust-flag` | — | **EXACT DUPLICATE** — `git diff fix/cursor-all-route..fix/cursor-agent-trust-flag` → **empty**; identical blob hashes on all 3 files. Keep `all-route`, drop this. |

**Methodological warning (recorded for later agents):** `git diff --stat origin/main <branch>` reports e.g. *"617 files changed, +4,984 / -95,983"* for `cursor/mol-476` — **that is 245 commits of main the branch lacks, not new work**. `git cherry` also over-reports because **squash-merge destroys patch-id**. Only blob comparison settles it. `R-CLM-071`

### 16.4 Remote aggregate

`total 274 refs · merged into origin/main 106 · NOT merged 168`. By prefix: `cursor/` 75, `fix/` 39, `bycreamco/` 24, `feat/` 8, `docs/` 8, `ci/` 7, other 7. With **0 open PRs** and ~96% of the local not-ancestor sample proven to be squash residue, the 168 are overwhelmingly the same artifact. Not sampled individually; the 63-branch local sample is the evidence base.

### 16.5 The exposed ledger — **measured, and narrower than it first appears**

`origin/cursor/cloud-agent-1783626326349-f4sx9` commit `fd13524` tracks, **forced past `.gitignore:10 MohFlow-FanOps/`**, on a repo whose visibility is **public**:

| Path | Size | Verified |
|---|---|---|
| `MohFlow-FanOps/00_control/ledger.sqlite` | **57,344 B** | magic = `SQLite format 3` — a **real database** |
| `MohFlow-FanOps/00_control/RUNTIME.md` | 60,997 B | — |
| `MohFlow-FanOps/00_control/{RISK.md, context.md, .gitkeep}` | 3.4 KB / 5.8 KB / 0 | — |

**Main tracks 0 files under `MohFlow-FanOps/`** — main is clean.

**Severity, measured rather than assumed:** the exposed DB holds **18 rows total** (1 batch, 5 clips, 5 moments, 5 posts, 1 source, 1 tag_log) — an **early/small ledger, not the 347-post production database**. A count-only scan for `POSTIZ_API_KEY`, `ZERNIO_API_KEY`, `META_GRAPH_TOKEN`, `R2_SECRET_ACCESS_KEY`, `Bearer`, `access_token` returns **0 for every key**. So this is **not a credential leak**. It is a **repository-hygiene violation with low data sensitivity**: production-shaped operational metadata (5 posts' captions/handles/ids) on a public branch, in a path the project's own `.gitignore` declares must never be tracked. `R-CLM-072`

*(An initial assessment called this "severe / operator production data". That over-states it; the measurement above is the record.)*

### 16.6 Integration-risk list

| Item | Operator decision? | Risk of integrating | Risk of dropping |
|---|---|---|---|
| **`fix/darwin-test-gate`** | **Yes — land or lose it** | Low; highest-value item. Self-contained hook + 67-line test, fails open; scopes the local-test ban to Darwin so Linux CI/cloud sessions can run the suite — directly relieves the `CLAUDE.md` "NEVER run tests locally" constraint. 66 behind → rebase first. | **Real** — its only copy is in another session's `/private/tmp` scratchpad, one temp-reap from gone. |
| **`fix/cursor-all-route`** | **Yes** | **Medium — behavioral.** Flips `_CURSOR_SUPPORTS_VISION` False→True, deleting the vision→claude fallback so every gate routes to cursor-agent. Comment claims "proven live"; **unverified here**. Wrong ⇒ vision gates fail with no fallback. | Low — main's `False` is the safe default. |
| **Exposed ledger branch** | **Yes** | n/a — its real work (MOL-477) already landed at `config.py:669-671`. | **None.** |
| **`cursor/mol-476-hook-author-always`** | No — **drop** | **High** — reintroduces a ladder main deliberately removed (#513, `681e6ec`); reddens `tests/test_hook_authorship.py:99`. Also uses a stale gate key. | None. |
| **`docs/constitution/`** | **No** | **High if revived** (§16.1). | None. |
| 24 residue worktrees + ~49 local + ~168 remote branches | Batch cleanup | None | None — verified landed at blob level. |

---

## 17. Runtime and Deployment Reality

### 17.1 Runtime-component register

| Component | Env | Version / SHA | Startup | Config source | Persistent state | Status | Source correspondence | Evidence @ |
|---|---|---|---|---|---|---|---|---|
| `com.fanops.run` | macOS 15 (Darwin 25.5), UTC+04 | **`6d21749…` / v0.4.0** | launchd `RunAtLoad` + `KeepAlive{SuccessfulExit:false}`, `ThrottleInterval 60`, `LSMultipleInstancesProhibited` | plist env (PATH, HOME, `FANOPS_DAEMON_INTERVAL=600`) + `.env` re-read each tick | ledger, control files | **RUNNING** PID 9121 since 16:49:48Z | **EXACT — == HEAD == origin/main** | 18:30:13Z |
| `com.fanops.studio` | same | same tree | launchd | `[studio]` | via Ledger | **RUNNING** PID 9123, :8787 accepting | exact | 18:26Z |
| `com.fanops.keeper` | same | same tree | launchd `StartInterval 120` | plist env | — | **ARMED** (exit 0 — correct for a poll timer) | exact | 18:30Z |
| Interpreter | CPython 3.12 (`/Library/Frameworks/…`) | — | via `.venv/bin/fanops` | — | — | — | **editable → repo `src/`** | — |
| Data root | `/Users/molhamhomsi/FanOps` — **40 GB** | — | plist `WorkingDirectory` → `Path.cwd()` | `root_source='cwd'` | 01_inbox 21 G · 02_sources 15 G · 03_clips 2.1 G · 07_reports 1.0 G | — | **untracked, outside the repo** | — |
| Ledger | `00_control/ledger.sqlite` 3.0 MB | schema **11** | — | — | 1,063 rows, `integrity_check: ok` | actively written (mtime 22:30 +04) | matches `ledger.py:190` | 18:32Z |

### 17.2 Runtime drift register — **the standing risk is closed, and it closed today**

| Check | Verdict |
|---|---|
| Deployed revision vs main | **NO DRIFT.** Heartbeat at `18:30:13Z`: `{"code":"6d21749ffc49c77383f537d93b028cca0d69a447","fanops_version":"0.4.0","published_in_run":"0","last_published_age_hours":"None","origin":"loop"}`. Pump started `16:49:48Z` — **10 m 41 s AFTER** HEAD was committed (`16:39:07Z`). Editable install ⇒ it imported this tree. Tree clean of Python changes ⇒ **running code == main** `R-CLM-020` |
| Code adoption working? | **YES — 11 adoptions today**: `ba17c5d → cb3df5f → 6186431 → 073a37e → caa3427 → fb8a057 → 52659f3 → 946428c → 2b86694 → 6d21749`, with **9 `action=kickstart_stale_code`** entries in `daemon-keeper.out`. The storm guard was observed counting `241s→362s→482s→603s` against a `720s` settle (= interval + 120), then releasing and kickstarting `R-CLM-021` |
| Why it works now | Fixed **today** by `6186431` (#688 — *"keeper could never adopt new code — `etimes` is not a BSD ps keyword"*) and `073a37e` (#689 — *"storm guard must outlast a pass"*), merged 13:31Z / 13:50Z. Before that, `ps -o etimes` (a **GNU** keyword absent from BSD `ps`) made `age` always `None` → the storm guard skipped permanently → *"the pump sat on a day-old SHA through 18 merges"* (`daemon.py:250-266`). **The standing memory "live daemon runs stale code" was true until ~13:50Z today and is now obsolete** `R-CLM-022` |
| **Residual** | The SHA is snapshotted at the **first heartbeat**, not at import. A `git pull` landing inside that window would stamp the new disk SHA onto a process running old code, hiding drift for that process's life. **Observed gap: 2 s** — not currently biting. Also: the fix is **5 hours old** and its only proof is one day's evidence. |
| Stale plist PATH | **Real but currently inert.** `run.plist`'s PATH **omits** the nvm bin dir that `keeper.plist`/`studio.plist` carry (all three call the same `_daemon_path()`, resolved via `which()` at write time — W-01). `claude` still resolves for the pump **only because `~/.local/bin/claude` symlinks into `~/.nvm/versions/node/v24.11.1/bin/claude`** (both PATHs → claude 2.1.147, tested with `env -i`). Delete that symlink or `nvm install` a new node and the daemon's `claude` vanishes while the shell keeps finding it → `_check_preflight` exits 2 at pump start, visible only in `daemon.err`. |
| Config absent from source | The **40 GB data root is bound solely by launchd `WorkingDirectory`** → `Path.cwd()`. `FANOPS_ROOT` is set in **neither `.env` nor any plist** (E-07). |
| Operational fix not in the repo | The `~/.local/bin/claude` shim the daemon depends on; and `~/postiz-selfhost/media-sync.sh` (`R-CLM-051`). |
| Scheduled job calling a removed command | **None** — all three plists invoke live verbs. |
| Generated state older than code | `00_control/` holds 14 legacy `ledger.json*` backups (~14 MB) + `ledger.sqlite.pre-pull-*` with orphaned `-shm`/`-wal`. |
| Active flag, no documented owner | `FANOPS_DAEMON_INTERVAL` (E-04); `FANOPS_CREATIVE_VARIATION=1` in the live `.env` with **0 readers** (`R-CLM-045`). |

### 17.3 The live operational verdict

**`FANOPS_LIVE=1`. The system is publishing-enabled and has never published.**

| Ledger fact | Value |
|---|---|
| posts | **347** — `awaiting_approval` **277**, `queued` **68**, `failed` **2**, **`published` 0** |
| by platform | instagram: 210 awaiting, **0 queued** · tiktok: 67 awaiting, **68 queued**, 2 failed |
| `public_url` / `media_id` / `published_at` / `metrics_series` | **0 across all 347** |
| heartbeat | `"published_in_run":"0"`, **`"last_published_age_hours":"None"`** |
| renders | **0** rows — yet **108 `render_*.mp4` on disk** |

**The blocker, traced end-to-end** `R-CLM-032`:
- Both TikTok accounts route to **Zernio**: `accounts.json` → `backlikeineverleft {tiktok: zernio}`, `hrmny-blog {tiktok: zernio}`. (The three IG accounts route to `postiz`.)
- **All 68 queued posts are TikTok** ⇒ all route to Zernio.
- The 2 that came due (`scheduled_time 2026-07-16T13:31:00Z`) **failed**: `"publish failed: Zernio upload failed (405) — body withheld"`, `media_urls: []`.
- Raised at `post/zernio.py:161` — **step 2** of the upload. Step 1 (`POST /media/upload-token`) **succeeded** (its own error at `:146` did not fire), so the token mints and the **`POST /media/upload?token=…` returns HTTP 405 Method Not Allowed**.
- `zernio_upload_media`'s docstring (`:124-131`) records the contract as *"Two-step contract **DISCOVERED LIVE 2026-06-29**"* — reverse-engineered, not specified. **A 405 on a previously-working POST is the signature of a server-side contract change.**
- **The remaining 68 are exposed** as they come due (earliest `2026-07-16T18:57Z` — within the hour of observation).

**Failure handling itself is correct** — per-post `failed` (re-queueable), no run-halt, `AuthError` reserved for queue-halt (`post/CLAUDE.md`). The defect is external contract drift, not the publish state machine. **This is measured from the ledger and the client source; the Zernio API was NOT probed** (live-verb rule), so the server-side cause is inferred, not reproduced. `R-Q-01`

---

## 18. Dead Code, Orphan, Duplicate, and Shadow Register

**Method:** every candidate was swept for bare strings across all file types, Jinja registration (`app.py:280-305` registers each filter/global by explicit Python reference — no name-based template dispatch exists), `getattr` (all attribute-guarding, never module dispatch), CLI `add_parser` tables, the `_MIGRATIONS` dispatch dict, and subprocess module strings.

**No module is unreachable.** Every low-inbound module resolves via a lazy `cli.py` import. The sweep rule earned its keep twice:

| Item | Static inbound | Sweep result | Classification | Conf. | Removal risk |
|---|---|---|---|---|---|
| `_fwrun.py` | **0 imports** | invoked as a **subprocess module string** — `[sys.executable, "-m", "fanops._fwrun", …]` `transcribe.py:137` | **operationally-invoked — LIVE** | High | **Critical** |
| `cutover_postiz.py` | 0 eager | 3 **lazy** dispatch sites `cutover.py:58,68,79` | **live, untested** | High | Med |
| `post/providers.py` lambdas | 0 | dict-of-lambdas service locator `PROVIDERS:45` → `get_provider:52` | **all live** — the documented false-dead source | High | Critical |
| `ledger_bridge` | 1 lazy | `ledger.py:382` — **auto-fires** when no DB + legacy `ledger.json` exists | migration-only, auto-reachable | High | High |

**Genuinely unreachable — 12 of 1,366 module-level functions.** The four recommended for adjudication (each backed by an *affirmative* sweep, not by absence of reference):

| Function | Sweep | Classification |
|---|---|---|
| **`retry_transient_failures`** `studio/actions.py:1048` | **its own def line is the ONLY hit in the entire repo** — no route, test, template, or doc. Fully implemented. | **definitely-unreachable — an unwired feature** |
| **`Settings.runtime_load`** `settings.py:374` | 0 src / 0 tests / 0 tools / 0 CI. **Independently re-verified by this investigation** | **definitely-unreachable** |
| **`health.zernio_health`** `health.py:43` | 0 callers; its twin `postiz_health` is live. Codemap `C8:201` still claims *"Called by `system_health`"* — stale. | **apparently-unreachable** — MOL-298 consolidation orphan |
| **`health_model.daemon_liveness_check`** `health_model.py:200` | 0 callers; the live+tested impl is `doctor._daemon_liveness_check` | **apparently-unreachable** — unused wrapper |

Plus 8 **test-only** functions (`load_validated` `controlio.py:82` — *the canonical fail-loud control-file loader that no control file uses*; `normalize_account_handle` `models.py:440`; `reschedule_account`; `refresh_zernio_accounts`; `zernio_permalink_from_analytics`; `all_channels`; `archetype_selection_scope_map`).

**Hedge recorded:** `zernio_permalink_from_analytics` — Zernio is a sparsely-wired backend; a future wire cannot be ruled out. **Nothing is recommended for deletion on static non-reference alone** — `_fwrun` is the standing proof of why.

### Documented-as-removed — verification

| Claim | Verdict |
|---|---|
| `moment_casting` "gone" | **TRUE** — 4 src hits, all comments |
| durable `AccountSelection` "gone" | **TRUE as a class**, but `ledger._migrate_v8_account_selections` (`:152`) still **builds** the map and `_migrate_v10_drop_selections` (`:182`) **drops** it two hops later — reachable only via `_MIGRATIONS` (`:227`). **Deliberate + documented** (`ledger.py:211-213`): the hop-chain must have no gap. Not dead; its output is provably discarded by design. |
| `casting_bias` "removed" | **TRUE** — 1 hit = the `CLAUDE.md` rule text |
| `hooks_by_persona`, `scoped_caption_surfaces`, `FANOPS_CREATIVE_VARIATION` | **TRUE — 0 src hits each**, guarded by negative tests |
| `variant_hook` "deleted" | **HALF TRUE.** The `Post.variant_hook` **field is gone** (AST-verified; `Render.hook_text` is the single home, `models.py:417`). But the **name survives as a live view-model field** across `views_results.py` (9 sites), `views_review.py` (4) + 8 templates, fed by `variant_learning._hook_for_post`. It is now an alias for the moment/render hook. |
| `moments_wait_cycles` "stays dead" | **TRUE and enforced by negative controls** — `tests/test_moments.py:360-362,476` assert absence |
| `PostizMetricsClient`-for-IG "dead" | **TRUE** — `track.py:286-287` routes IG to `GraphInsightsClient`. **Caveat:** the `posts is None` back-compat branch (`:265-266`) would still route IG ids to Postiz — but **no production caller passes `submission_ids`** (all four pass `posts=`). Unreachable branch. |
| `FANOPS_POSTER` "legacy bridge only" | **TRUE** — `accounts.effective_provider` (`:208`) is the per-channel truth; `go_live` never writes it (`golive.py:671`) |

### Duplicate / shadow register

**Most suspected pairs are deliberate, correctly-factored splits.** Reported plainly rather than inflated: `ledger`/`ledger_sqlite`/`ledger_bridge` = 3 distinct roles; the 4 hashtag modules = 4 lifecycle roles (the `fanops.fanops_hashtags` name merely stutters); `health`/`health_model` = typed owner + facade (MOL-298); `frames`/`keyframes` = scoring vs extraction; `framing`/`framing_outcomes` = engine vs contract; `cutover`/`cutover_postiz` = harness vs backend body.

**The real ones:**

| # | Finding | Severity |
|---|---|---|
| **DUP-1** | **`config.py` vs `settings.py` — a shadow config surface.** `settings.py:1` calls itself the *"typed env boundary (constructed per `Config()`)"*. **It is not.** `config.py` **never imports `fanops.settings`** and makes **74 raw `os.getenv` calls**; `Settings` is **never instantiated in `src/`** (verified: `grep "Settings("` → only the class def `:141`); `runtime_load` has **zero callers**. Its only consumers are `config_introspect.py:82,87` (the `fanops config` verb) and `doctor.py:27`. **Consequence: `fanops config` introspects a parser the runtime does not use.** Duplicated logic includes a **byte-identical** unknown-backend handler (`config.py:242-244` ≡ `settings.py:349-351`) and `_VALID_BACKENDS` defined twice (`config.py:72`, `settings.py:18`) — with `accounts.py:13` importing **settings'** copy while `config` resolves against **config's**. **The split is deliberate** (strict doctor-only vs lenient runtime) — `contract/prompts/C6-S02.md:139` says **"DO NOT 'unify' them."** Independently corroborated by `INVENTORY.md:506` (FIND-009) and `INVARIANT_AUDIT.md:253` (INV-05). **The defect is the false docstring + the dead `runtime_load`, not the split** — a maintainer "fixing" this reintroduces a real boundary bug. | **High (maintenance trap)** |
| **DUP-2** | `_is_pinned` — **byte-identical copy-paste**: `persona_research.py:115` ≡ `persona_store.py:175`. Both sit in the one eager cycle, so neither can cleanly import the other. A *decision rule* duplicated → silent divergence risk. | Med |
| **DUP-3** | `_enabled_strategies` — `pipeline.py:39` ≡ `produce.py:44`, byte-identical, and `produce.py:44`'s docstring **admits it**. Accepted trade, but **no test pins the pair** → a third structural-hook format added to one site diverges silently. | Med |
| **DUP-4** | `_run_ffmpeg` ×2 (`ingest.py:196`, `signals.py:131`) — same semantics; no canonical ffmpeg-runner helper exists. | Low |
| **DUP-5** | `_now` ×3 (`meta_graph.py:131`, `studio/actions_common.py:45`, `post/run.py:25`) — different signatures, but three ad-hoc clock adapters alongside a canonical 13-function `timeutil.py`. | Low |
| **DUP-6** | `fix/cursor-all-route` ≡ `fix/cursor-agent-trust-flag` — identical trees (§16.3). | Low |

**Orphans:** `PROBE.sh`; `.reports/codemap-diff.txt`; `scripts/mol164_canon_test_handles.py` (**mutates `tests/**` in place**); `scripts/operator/mol-11{6,26}-*.sh`; `scripts/codemap_extract/*` (decommissioned); `scripts/check-full.sh` (**nothing calls it at all**); `.run-kick` (live file, **0 src references** — superseded per `docs/adr/README.md:1172`, yet `C9_studio_backend.md:462` still documents it as current); `04_agent_io/requests/` **2,083 never-swept gate files**; ~50 leaked `fanops-shrink-*` dirs.

---

## 19. Failure, Fallback, and Silent-Anomaly Register

**The mechanical numbers look alarming and are wrong in both directions.** Measured posture: **0 HTTP calls without a timeout · 0 bare `except:` in Python · 0 `TODO/FIXME/XXX/HACK` in `src/` · 0 unbounded retry loops · 0 swallowed ledger writes.**

| Metric | Value |
|---|---|
| `except Exception` in `src/` | **335** across 71 of 132 files |
| Bare `except:` in Python | **0** (the only textual hit is prose in `post/CLAUDE.md:63`) |
| `except BaseException` | **3 — all correct** (`controlio.py:35,50,65`: cleanup-then-`raise`, guarding only a temp unlink) |
| `while True` loops | 6, **all bounded** — lock loops raise typed `LockBusyError`/`StageBusyError` |
| Ratchet baseline vs actual | **203 vs 201** → **2 handlers of silent regrowth pre-authorized** |
| True silent (after correcting the ratchet's 44 false positives) | **≈157**, not 201 |

### The register — ranked by blast radius

| path:line | trigger | actual behavior | logs? | state mutation on failure | severity |
|---|---|---|---|---|---|
| **`studio/actions.py:403`** | `Ledger.transaction` latching `failed` after an oversize preflight fails (e.g. daemon holds the lock) | `except Exception: pass` — **the compensating latch silently doesn't land** | **No** | post stays **`queued`, no `error_reason`** → armed for `publish_due` retry **and** invisible to the oversize recovery lane (`:1020-1021` filters `state in (failed, error)`) — **falls out of both tubs** | **HIGH** |
| **`studio/golive.py:650`→`:664`** | `_dual_unset("FANOPS_POSTER")` OSError **after** `FANOPS_LIVE=1` already persisted | returns `ok=False` — **reports failure while the system is LIVE** | n/a | live switch **on**; operator concludes the flip was refused; daemon publishes next tick. `:661` `read_text()` unguarded → Flask 500 in the same window | **HIGH** — fix = scrape `FANOPS_POSTER` **before** the flip |
| **`accounts.py:335`** | `Personas.load` raises `ControlFileError` on **any** malformed persona row (`personas.py:81`) | **bare `return`** — swallows a *loud* error | **No** | all 6 hydration writes (`:341-351`) skipped for **every linked account**; persona edits silently stop taking effect. Violates the repo's own norm (`src/fanops/CLAUDE.md:45`: *"When adding one, log first"*). Live `personas.json` has 8 heterogeneous records — **loaded gun, not theory** | **HIGH** |
| **`studio/actions_run.py:175`** (`:208`, `:453` same shape) | `_archive_staged`/`write_digest` raise — **outside** the committed txn, **inside** the same `try` | returns *"ingest failed"* | No | **Partial: ledger already committed.** Retry recomputes `_batch_id(name, now_iso)` with a fresh `now_iso` → **duplicate orphan batch** | **HIGH** |
| **`golive.py:533`** | 3 sequential `accounts.json` writes, **no enclosing txn** | per-row `ok=False` | No | **Partial:** account created + id-mapped but **unrouted**; `adopted` already incremented → **the count lies**. Lands in the *safe* direction | MED-HIGH |
| **`golive.py:726`** | `cutover_metrics`/`cutover_lift` raise **after** `cutover_post` already published a real throwaway post | `ok=False` | No | **Unretractable network side effect — the operator never learns a real post shipped** | MED-HIGH |
| **`caption.py:176`** | as `accounts.py:335` | `return {}` | **No** | every account loses `intake.genre` → the hashtag niche seed is gone | MED |
| **`meta_graph.py:61,80,306`** | torn `accounts.json`; `load_accounts_safe` **returns** the error | `_err` **discarded by the caller** | No | empty registry → falls back to the global `META_IG_USER_ID` → **Graph reads attribute to the wrong handle**, silently re-opening the per-handle-creds gap. (`track.py:270`, `reconcile.py:502`, `cli.py:347` do it right) | MED |
| **`studio/actions.py:917`** | `write_digest` after the committed metrics txn | `except: pass` then `return ok=True` | No | metrics committed, **digest silently stale, reported as full success** | MED |
| **`framing.py:157`** | detector runtime crash mid-loop | returns a **partial** list | No | caller records `FACES_DETECTED` with a partial count instead of `DETECTOR_RUNTIME_FAILED` (`:239`) — **a crash recorded as a legitimate "no face" conclusion** | MED |
| **`ledger_sqlite.py:102`** | `os.chmod(db_path, 0o600)` fails | `except OSError: pass` | **No** | ledger DB may stay world-readable | MED |
| `views.py:926`/`:1009` | torn ledger / daemon read | `0` counts / `None` | No | Home renders **"no work pending"**; the daemon banner **hides** — degrades to a *reassuring* value, not a neutral one | LOW-MED |

**A claim checked and rejected:** `actions.py:808-813` (`randomize_account_schedule`) uses `with fail_open(...): raise exc` then `return ok=False`. Executed: `fail_open` catches, logs **with traceback**, the `with` exits normally, and **the `ok=False` return IS reached.** Ugly, correct, **not a bug.**

### Intentional resilience — verified, do not "fix"

- `errors.fail_open` (`errors.py:130-140`) logs with `exc_info` and **never swallows `KeyboardInterrupt`/`SystemExit`**. Correct primitive.
- **The ledger is fail-CLOSED**: all 3 broad handlers (`ledger.py:387,431,462`) **re-raise** as `ControlFileError`. **Zero swallowed ledger writes.**
- Meta Graph budget **fails CLOSED + LOUD** (`meta_graph.py:488-506`).
- Reconcile verifiers **fail closed** (`:187`, `:231`) — an erroring seam is never a "live" verdict.
- `Accounts.load` **per-row leniency** (`accounts.py:163-169`) → `skipped_rows` → `validate():266` → doctor. Never silent.

### The documented FAIL-CLOSED gate — verified, with a naming correction

The cv2 gate is **real and holds**, but it is **not `require_cv2`**. `framing.require_cv2` (`framing.py:106`) has **zero production callers — test-only**. The production gate is **`framing._framing_runtime_or_raise` (`framing.py:67`)**, called at `framing.py:862` and `clip.py:832`. `_cv2():34` and `_detector():52` both swallow to `None` — **neutralized** by `:79,83,88,92-103`, which raise `ToolchainMissingError` when `detector is None` ("*swallowed a build failure → still refuse*"). Re-raise is defended at both capture sites (`framing.py:868,895`) and downstream (`clip.py:1157-1161`) → `cli.py:633/647` → **exit 2**. **The boundary holds.** But `CLAUDE.md` and `errors.py:71` both attribute it to `require_cv2` — **stale naming** `R-CLM-046`.

---

## 20. Contract, Architecture, Governance, and Invariant Conformance

**The governance layer is unusually honest about itself.** `docs/REPOSITORY_CONSTITUTION.md` stamps every rule with a four-value enforcement vocabulary, measured across the file:

| Self-declared status | Count |
|---|---|
| **enforced** | **47** |
| **documented-only** | **10** |
| **partially-enforced** | **8** |
| **accepted-residual** | **1** |

**66 stamped rules; the doc openly concedes 18 are not fully enforced.** `docs/ARCHITECTURAL_LAWS.md` carries **45 `LAW-*`**. All **5 ADRs** (`0100`–`0104`) read `status: accepted`.

### Conformance matrix (material rules, independently tested)

| Rule | Defining source | Implementation | Enforcement (and is it **required**?) | Conformance | Conf. |
|---|---|---|---|---|---|
| **No-auto-publish** — every `Post` born `awaiting_approval`; only `Ledger.approve_post` promotes to `queued`; publish iterates `queued` only | `src/fanops/CLAUDE.md`; `crosspost.py:234`; `ledger.py:586,601` | **3 mint sites, all `awaiting_approval`** | `test_post_approval.py`, `test_dryrun_boundary.py` → **required `unit`** | **CONFORMS** — live ledger: 277 awaiting / 68 queued / **0 auto-published** | High |
| **Two independent dryrun/live gates** | `post/run.py:158,165`; `post/__init__.py:19` | both present; `get_poster` **raises** rather than build a DryRunPoster when live | required `unit` | **CONFORMS** | High |
| **Bias actuators amplify-only, validation-frozen** | `src/fanops/CLAUDE.md`; `validation_gate.py:22` | `p4_dim_bias`/`variant_amplify`/`timing_bias` call only `adjust.amplify` or write an isolated prior | required `unit` | **PARTIALLY CONFORMS** — the amplify-only property holds, but **`timing_bias`'s kill switch gates the wrong thing** (D-01) and **`cutover.json` is stale-true** (D-02), so "validation-frozen" rests on an unfalsifiable stamp | Med |
| **Cascade protection** (`_PROTECTED_POST_STATES`) | `ledger.py:671,695` | present | required `unit` | **CONFORMS** | High |
| **Generated artifacts are a pure function of source; no wall-clock stamp** | `MANIFEST.json` determinism contract; ARCH-007/IMPL-007 | `tools/arch` content-digests, no timestamp | `tools.arch ci` (**not required**) **+ `test_arch_governance.py` in required `unit`** | **CONFORMS** — 132/132, gate green | High |
| **Every policy rule has a firing negative control** | `architecture.yml:143-145` | **25 controls (NC-01…NC-25) over 21 rules** | `@slow` → **required `e2e`** | **CONFORMS for `tools/arch`** | High |
| — the same discipline for **CI shell validators** | registry `controls:` | — | `test_ci_registry_validator.py:49-53` asserts against a **hardcoded DC set**, never the registry's control list | **VIOLATES** — `CI-UNIT-LOCKDRIFT`/`CI-UNIT-SECRETSCAN` are `required` with **zero** negative controls | High |
| **Registry `evidence_status: verified-this-session`** (24 occurrences), incl. *"check-locks.sh exits non-zero when pyproject deps change without lock regen"* (`:163`) | `ci-control-registry.yml` | — | — | **CONTRADICTED — disproved by execution** (E-01), reproduced independently twice | High |
| **`ci_env_probe.py` "exits non-zero on mismatch"** (registry `:183`) | registry | `main()` has **no `sys.exit`** | — | **CONTRADICTED** | High |
| **`NIGHTLY-PIPAUDIT`/`NIGHTLY-ASR` `status: active`** (registry `:575-617`) | registry | files exist | **workflow `disabled_manually`** | **CONTRADICTED** — and `NIGHTLY-ASR`'s own `deletion_consequence` is already the live state | High |
| **Required-context set** = registry `current_required_contexts` = live GitHub | ADR-0100/0101; `docs/ci/freeze/2026-07-15/branch-protection.json` | — | live BP | **CONFORMS — zero drift across all three planes** (byte-identical: contexts, `strict`, `enforce_admins`, review count, linear-history, conversation-resolution, force-push) | **High** |
| **`tools/ci` validator mechanizes precedence** — *"not yet a required gate"* | `REPOSITORY_CONSTITUTION.md`; `ARCHITECTURAL_LAWS.md:46` | `tools/ci` exists | **CLI invoked by no workflow**; library reached only via the required unit test; **DC-3 never runs automatically** | **CONFORMS TO ITS OWN CLAIM** — the doc says `partially-enforced`, and that is exactly true | High |
| **`LAW-STATE-03` / GB-5** — a `Moment` is mutated by setattr, never `model_copy` | `ARCHITECTURAL_LAWS.md:121`; `IMPLEMENTATION_CONTRACT.md:65` | `cast_add`/`cast_remove` use setattr | required `unit` | **CONFORMS** — and the untracked draft that would invert it is quarantined (§16.1) | High |
| **`.claude/workflows/*.js` are "load-bearing build workflows"** | `CLAUDE.md` | 10 files, tracked | **zero in-repo references; the 3 corroborating docs all cite `CLAUDE.md` — circular** | **NOT OBSERVABLE from inside the repo** — they are external-runner agent definitions | Med |
| **`.reports/issue-register-2026-07-03.md` — "read FIRST for any MOL task"** | `CLAUDE.md`; `src/fanops/CLAUDE.md` | file exists **locally** | — | **VIOLATES** — the file is **gitignored/untracked**; a clone has no such file | **High** |
| **"Full 108-module map"** | `CLAUDE.md` | `full-trace-index.md` claims 109/109 and 108/108 | — | **VIOLATES** — tree = **132**; ≥23 modules outside the trace (G-01/G-02) | **High** |
| **`src/fanops/CLAUDE.md`: "Anchors verified against source 2026-07-03"** | `src/fanops/CLAUDE.md:1` | — | **no gate covers `CLAUDE.md`** — `grep -rln "CLAUDE.md" tools/ .github/workflows/` → nothing | **VIOLATES** — **~24 of 27 anchors stale** (`post/CLAUDE.md` 7/8; `studio/CLAUDE.md` 9/10; `src/fanops/CLAUDE.md` 8/9). E.g. `publish_due` 337→**466** (off by 129). **Semantics all hold — only the coordinates rotted.** Corroborated by `CYCLE2_EXTENSION.md:259` (INV-20) | **High** |
| **`src/fanops/CLAUDE.md`: MOL-79 — "`Accounts.load` has a broad except with no per-row guard while `Personas.load` is defensive"** | `src/fanops/CLAUDE.md:41-42` | — | — | **CONTRADICTED AND INVERTED.** `Accounts.load` is at **`:141`** (not `:98`) and **is** defensive (`:163-169`, MOL-79 fixed); **`Personas.load` (`personas.py:66-83`) is now the laggard** — whole-loop `try`, one malformed row kills the registry. **Independently found by two investigators.** | **High** |
| **`studio/CLAUDE.md`: route modules are "Blueprints"** | `studio/CLAUDE.md:13` | — | — | **CONTRADICTED** — `grep "Blueprint" src/` → **0**; that doc line is the only occurrence in the tree | High |
| **`field_authority.json:87`: "ARCH-008/ARCH-009 fail CI while they disagree"** | `.reports/architecture/governance/field_authority.json:87` | — | **`policy.py:144` sets ARCH-008 to `WARNING`** — a WARNING never fails | **CONTRADICTED, AND LIVE.** They **disagree right now**: `kb/side_effects.json:15` declares `subprocess_call_sites: 35`, code has **37**; `:19` declares `rmtree_sites: 3`, code has **5** — and the gate **verdict is PASS**. This is the repo's own signature defect (`field_authority.json:7`: *"THE DOC NAMES A MECHANISM THAT DOES NOT EXIST"*) occurring **inside the Declaration of Canonical Authority**. C3.4 `:66`, LAW-ARCH-05 `:75` and AR-8 `:296` all correctly say WARNING — only this file lies. **Verified directly.** | **High** |
| **C16.3 / LAW-DOC-01: generated doc "enforced (byte-compare)"** | `REPOSITORY_CONSTITUTION.md:275`; `ARCHITECTURAL_LAWS.md:312` | `stale_docs()` exists and works | **no required owner** (§14.4 gap 1b) | **VIOLATES *as blocking*** — the check is real, runs only in the non-required `gate`, and the negative control fires only into a temp root | **High** |
| **C7.3 / LAW-FAIL-03: `CI-BASEINSTALL` "enforced"** | `REPOSITORY_CONSTITUTION.md:124`; `ARCHITECTURAL_LAWS.md:176` | `base_install_smoke.py` is real and strict | **standalone job** `ci.yml:97` — in neither required lane | **VIOLATES** — and `LAWS:177` concedes *"not yet a live required context (advisory today)"* **one line after** claiming `enforced`. `ENGINEERING_STANDARDS.md:75` marks the same rule `partially-enforced` — the correct call | High |
| **`ARCHITECTURAL_LAWS.md:13` tally: "24 enforced · 8 partially · 3 proposed · 1 dormant"** | `ARCHITECTURAL_LAWS.md:13` | — | mechanical count = **45 rows** | **CONTRADICTED — stale by ~10, inside the document that legislates LAW-SOT-03 against exactly this.** `STANDARDS_ENFORCEMENT_MATRIX.md:82-86` tallies 26 against 29 rows, and uses `violated` as a status **absent from its own declared vocabulary** (`ENGINEERING_STANDARDS.md:39-40`). **No check catches a tally-vs-rowcount mismatch** — a trivially mechanical predicate, simply absent | High |
| **`CI_CONTROL_INVENTORY.md` is a `generated_view`** | `ci-control-registry.yml:23` | — | **no generator exists**; `tools/ci/common.py:11` `GEN_VIEW` is **defined and never read**; `tools/ci/cli.py` exposes only static/deployed/reconcile/selftest | **VIOLATES** — self-discloses as "Provisional generated view" (`:1-4,11`), so honest, but it is the signature defect **inside the CI-governance layer** | High |
| **`ledger.py:2`: the ledger is "git-versioned"** | `ledger.py:2` | — | `.gitignore:10` ignores `MohFlow-FanOps/` wholesale | **CONTRADICTED** — the live ledger is untracked | High |
| **`ci-control-registry.yml` header: "STATUS: proposed — inert… No workflow reads this file yet"** (`:14-15`) | registry | field `:22` says `status: accepted` | — | **INTERNALLY CONTRADICTORY** — the `status` field is `accepted`; the "no workflow reads it" half is **still true** (a required *test* reads it, no workflow does) | High |
| **`FANOPS_LIVE` may be set ONLY by `studio/golive.go_live`** | `src/fanops/CLAUDE.md` | `go_live` is the sole setter; never writes `FANOPS_POSTER` | required `unit` | **CONFORMS** (see the `golive.py:650` ordering defect, §19, which is a sequencing bug not a rule violation) | High |
| **`docs/CONFIG.md` is the env-var authority** | `CLAUDE.md` | — | — | **CONFORMS** — a mechanical diff of every `getenv("LITERAL")` in `src/` returns **zero undocumented** vars. Sole exception: `FANOPS_DAEMON_INTERVAL` (E-04) | High |

### Summary

**Implemented truth ≈ enforced truth ≈ operational truth.** The code does what the laws say; the required lanes carry the invariants; the three CI planes agree byte-for-byte. **The documented truth is where the divergence lives**, and it has two distinct shapes:

1. **Misattributed mechanism (systemic, mostly benign).** A dozen-plus rows across `STANDARDS_ENFORCEMENT_MATRIX.md:29,53,61,67`, `ENGINEERING_SCORECARD.md:31,33`, `REPOSITORY_CONSTITUTION.md:41,47` and 13 LAW rows credit **`ARCH-GATE`** for properties that genuinely block — but via **`CI-UNIT-ARCHGOV`** in the required unit lane. **The outcome claim is true; the named mechanism is wrong.** (`ENGINEERING_SCORECARD.md:31,33` — "the next UNKNOWN blocks CI" — is simply **true**.) This matters only when someone reasons *from* the mechanism: `CONSTITUTION_MAINTENANCE.md:72` offers "the fast unit lane **or** the `gate` job" as interchangeable hosts — they have **opposite merge consequences**, so an implementer following that design gets a non-blocking check while believing otherwise.

2. **Properties with no required owner (real gaps).** Two rules claim `enforced` and are **not**: the generated-doc byte-compare (C16.3/LAW-DOC-01) and `CI-BASEINSTALL` (C7.3/LAW-FAIL-03). Plus **`field_authority.json:87` asserts a `fail CI` mechanism that is a `WARNING` — and the censuses it governs are drifting right now, green.**

**The detector for this entire class is designed and unbuilt.** `CONSTITUTION_MAINTENANCE.md:42` specifies **CM-8**: *"a rule claims `enforced` but its cited CI control is `advisory`/absent"* — precisely the above. `:100` concedes: *"**No executable code** is written here."* It is gated on DC-3 landing, and DC-3 never runs (`R-CLM-014`).

**And it clusters in the always-loaded `CLAUDE.md` family** — stale anchors, a stale module count, an untracked "read-FIRST" pointer, an inverted MOL-79 claim, a "Blueprints" claim, a "git-versioned" claim. None of these break the running system; **all of them mislead the next agent**, which in a repo built around agent execution is a first-order defect.

**The honest counterweight, stated plainly:** this layer marks 10 rules `documented-only` and 8 `partially-enforced` against 47 `enforced`; the registry declares its own required-context gap as `transitioning`; `LAWS:177` concedes advisory status one line after claiming enforcement; `CI_CONTROL_INVENTORY.md` labels itself provisional. **A governance layer that self-reports 18 of 66 rules as not-fully-enforced is not one that overclaims** — it is one whose remaining defects are *specific and findable*, which is exactly what this section did.

---

## 21. Optional Cross-Report Comparison Appendix

**No external reconciliation report was supplied. The repository reconstruction was completed independently.**

One clarification, recorded because a reader will see it in the same directory. A peer document — `docs/reconciliation/04_APPLIED_PROGRAMS_RECONSTRUCTION.md` (untracked, 148,819 B, mtime **2026-07-16 22:51 +04**) — **materialized on disk mid-investigation**, written by a **concurrent session** executing Prompt 04 (21 `claude` processes were resident at the time). It was:

- **not supplied as an input** to this assignment;
- **never read** at any point — independence is intact, not merely asserted;
- **out of scope for comparison** on its own terms: it reconstructs the *applied programs* (reframing, hashtags), not present-state repository reality, so there is no overlapping claim set to reconcile. This document's handoff to that agent is §26.2.

A comparison against it would also be unsound in principle here: it was authored by a live, concurrent session, so it is a **moving target** — comparing a frozen reconstruction to an in-flight one produces disagreements that are artifacts of timing, not of fact.

**Concurrency caveat for the Final Integration Director:** because ≥1 other session was writing this repository during the observation window, §3's frame is authoritative **for this document only**. Any peer reconciliation document in this directory was produced under a *different* frame and may disagree for that reason alone. Reconcile frames before reconciling findings.

---

## 22. Integrity Findings Register

| ID | Category | Issue | Affected | Repro | Impact | Likelihood | Containment | Blocks closeout? | History? | Program? |
|---|---|---|---|---|---|---|---|---|---|---|
| **F-01** | runtime / external dep | **Zernio `/media/upload` returns HTTP 405** — 0 published, 2 failed, **68 queued TikTok posts exposed** | `post/zernio.py:161`; live ledger | ledger + client source (**API not probed**) | **Total publish outage** on the only exercised path | **Certain** — 2 already failed | per-post `failed`, re-queueable; no run-halt | **YES — THE BLOCKER** | yes | reframe/hashtag: no |
| **F-02** | governance / test | **`check-locks.sh:12` cannot fire** — `rg -n` prefixes `N:` so `^\+` never matches; the `required` lock-drift gate collapses to a substring test | `scripts/check-locks.sh` | **proven by execution, twice, independently** | stale hashed locks merge silently | High | none — 0 tests, 0 negative controls | **YES** | yes | no |
| **F-03** | documentation | **`CLAUDE.md` → `.reports/issue-register-2026-07-03.md` is gitignored/untracked** | `CLAUDE.md`, `src/fanops/CLAUDE.md` | `git ls-files` → absent | every agent's "read FIRST" pointer resolves to nothing on a clone | Certain | none | **YES** | yes | both |
| **F-04** | generated drift | **Codemap claims `109/109` (and `108/108`) coverage of a 132-module tree**; `CLAUDE.md` advertises "Full 108-module map" | `full-trace-index.md:3,34,51,179`; `CLAUDE.md` | measured | ≥23 modules — incl. the ledger backend and **both applied programs' newer surfaces** — outside the "zero-omission" trace | Certain | freeze banner discloses staleness, not magnitude | **YES** | yes | **both** |
| **F-05** | documentation | **~24 of 27 `CLAUDE.md` line anchors stale**, while `src/fanops/CLAUDE.md:1` claims "verified 2026-07-03"; **no gate covers `CLAUDE.md`** | 3 rulebooks | measured | agents jump to wrong lines; semantics hold | Certain | none | no | yes | both |
| **F-06** | contract | **`src/fanops/CLAUDE.md` MOL-79 claim is inverted** — `Personas.load` is the laggard, and `accounts.py:335` swallows its error with **no log** | `personas.py:66-83`; `accounts.py:335` | source | one malformed persona row → **every** linked account silently loses voice/corpus/levers | Med | none | no | yes | hashtag |
| **F-07** | data (latent) | **`timing_bias` kill switch gates the wrong thing** — `timing_bias_hour_for` never reads `cfg.timing_bias` | `timing_bias.py:36,65-77` | source | `FANOPS_TIMING_BIAS=off` biases the schedule anyway | **Dated** — activates at ≥8 analyzed posts | inert today (0 analyzed) | no | no | no |
| **F-08** | data (latent) | **`cutover.json metrics_confirmed` stale-true** — certifies evidence that no longer exists; no wipe path resets it | `cutover.json`; `validation_gate.py:22` | live file vs ledger | the learning correctness gate is unfalsifiable once stamped | Med | `p4_unlocked` backstops p4; **`variant_amplify` gates on `learning_validated` alone** | no | yes | no |
| **F-09** | silent failure | **`actions.py:403`** — swallowed `failed` latch strands a post **`queued` + armed** and **invisible to both recovery lanes** | `studio/actions.py:403` | source | double-publish risk / stuck post | Med | none | no | no | no |
| **F-10** | silent failure | **`golive.py:650`** — reports `ok=False` **while the system is LIVE** | `studio/golive.py:650,664` | source | operator believes go-live was refused; daemon publishes next tick | Low-Med | none | no | no | no |
| **F-11** | reachability | **Render limb unreachable** — `add_render` 0 callers, `crosspost.py:225` hardcodes `render_id=None`; **0 rows, 108 orphaned `render_*.mp4` on disk** | `ledger.py:571`; `crosspost.py:225` | live + source | 87+ lines dead; files unreclaimable (no GC driver) | Certain | — | no | **yes** | reframe |
| **F-12** | duplicate | **Shadow config surface** — `Settings` never constructed; `runtime_load` dead; `fanops config` introspects a parser the runtime doesn't use | `settings.py:141,374` | **re-verified here** | operator introspection lies; a "cleanup" reintroduces a real boundary bug | Med | `contract/prompts/C6-S02.md:139` says "DO NOT unify" | no | yes | no |
| **F-13** | CI | **`nightly` disabled while the registry says `active`** → `[asr]` toolchain + pip-audit **dark since 2026-07-14** | `nightly.yml`; registry `:575-617` | `gh api` | asr regressions ship silently; no dependency audit | Certain | none — DC-1..6 blind to workflow state | no | yes | no |
| **F-14** | CI | **`jsonschema` installed nowhere** → the 8,366-byte registry schema never validates | `tools/ci/registry.py:35` | grep | schema decorative | Certain | fail-soft; hand-rolled checks still run | no | no | no |
| **F-15** | CI | **Arch deep-gate FAIL-OPEN unreachable** — the `>` redirect creates the file before git runs | `architecture.yml:120`; `select.py:10` | **proven by execution** | negative controls silently skip instead of failing open | Med | `fetch-depth: 0` + cron reconcile runs selftest unconditionally | no | no | no |
| **F-16** | test | **Swallow ratchet miscounts both ways** — 44 false positives; **2 handlers of silent regrowth pre-authorized**; nested-funcdef guard decorative | `tests/test_swallow_ratchet.py:25,37,78` | measured | the silent-failure metric is not trustworthy | Certain | — | no | no | no |
| **F-17** | local-only | **Live ledger + 61 KB RUNTIME.md on a pushed branch of a PUBLIC repo**, forced past `.gitignore:10` | `origin/cursor/cloud-agent-…f4sx9` | verified | **18 rows, 0 credentials** → hygiene violation, low sensitivity | Certain | main is clean (0 tracked files there) | **operator action** | yes | no |
| **F-18** | local-only | **`fix/darwin-test-gate`** — only copy lives in another session's `/private/tmp` scratchpad | wt-dtg | verified | loss of a complete, tested feature | Med | none | **operator action** | no | no |
| **F-19** | wiring | **Plist fixed-point is PATH-dependent** → operator `fanops up` kills the pump mid-pass; keeper reverts ≤120 s later | `daemon.py:352,66` | on-disk plists differ exactly as predicted | restart oscillation | Med — arms on next bring-up | not firing now | no | no | no |
| **F-20** | environment | **`[keyring]` in no lock and no CI job**; **`demucs` has no presence check** (silent quality degrade) | `pyproject.toml:39`; `vocals.py:49` | grep | untested extra; silent ASR degrade | Med | — | no | no | no |
| **F-21** | external dep | **R2 secrets bypass the keychain** (bare `os.getenv`) while sibling API keys use it | `config.py:367-374` | source | HMAC signing key in plaintext `.env` | Low | **knowingly documented**; SigV4 sends a derived signature | no | no | no |
| **F-22** | persistence | **Atomicity gaps** — raw writes into `controlio`-governed stores (`meta_graph.py:533` locked-but-raw; `autopilot.py:46,68` fixed `.tmp`); **`or []` at `meta_graph.py:520` rewrites a torn budget file as clean** | 7 sites | source | torn control files; 7-day budget history loss | Low-Med | — | no | no | hashtag |
| **F-23** | reachability | **`hashtag` reach loop has produced zero data** — `hashtags.json` = `{tags:[18], reach:{}}`, the **floor** path; 347 posts → **28 distinct tag-sets** (one handle: 76 posts → **4** sets) | `fanops_hashtags.py:85` vs `:127` | live file | the reach-ranked lifecycle runs on pins + the frozen floor | Certain | by design without a Meta token | no | no | **hashtag** |
| **F-24** | orphan | `retry_transient_failures`, `Settings.runtime_load`, `health.zernio_health`, `health_model.daemon_liveness_check` unreachable; `fi`/`focus_idx` broken end-to-end; 8 test-only fns; `check-full.sh` called by nothing | various | full sweep | dead surface | Certain | — | no | yes | no |
| **F-25** | governance | **Only 2 required checks; `enforce_admins: false`; 0 required reviews** → CI is the only gate and is **advisory for the sole operator**. 3 unique controls (`base install`, `impact report`, `lane-guard`) run **without power to block** | live BP | `gh api` | a red unique control merges | Med | **DISCLOSED** — registry `rollout.phase: transitioning`, `current` vs `intended` | no | no | no |
| **F-26** | environment | **40 GB data root bound solely by an untracked plist's `WorkingDirectory`** (`root_source='cwd'`; `FANOPS_ROOT` set nowhere) | `config.py:145`; plists | verified | a cwd change silently relocates the root | Low | `daemon.root_divergence` warns | no | no | no |
| **F-27** | governance | **`field_authority.json:87` asserts "ARCH-008/ARCH-009 fail CI while they disagree" — `policy.py:144` is `WARNING`. And they DISAGREE NOW** (kb 35 vs code **37** subprocess sites; kb 3 vs code **5** rmtree) with verdict **PASS** | `field_authority.json:87`; `policy.py:144`; `kb/side_effects.json:15,19` | **verified directly** | the repo's signature defect **inside its Declaration of Canonical Authority**; the census silently rots | **Certain — live** | ARCH-008 still *reports* the drift each run | no | yes | no |
| **F-28** | governance | **The generated-doc byte-compare (ARCH-006) has no required owner** — `stale_docs()` runs only in the non-required `gate`; the required test calls `stale_artifacts()` only; NC-04 fires into a temp root | `drift.py:74,204`; `test_arch_governance.py:38,115` | **verified directly** | a hand-edited `docs/ARCHITECTURE_GOVERNANCE.md` merges; C16.3/LAW-DOC-01 claim `enforced` | Med | the gate still goes red (visibly, non-blocking) | no | no | no |
| **F-29** | governance | **`CI-BASEINSTALL` claimed `enforced`; it is a standalone job in neither required lane** — `LAWS:177` concedes this **one line after** claiming it | `REPOSITORY_CONSTITUTION.md:124`; `ARCHITECTURAL_LAWS.md:176-177`; `ci.yml:97` | verified | the base-packaging contract can rot unnoticed | Med | `ENGINEERING_STANDARDS.md:75` records the correct status | no | no | no |
| **F-30** | governance | **`ARCHITECTURAL_LAWS.md:13` tally is stale by ~10** (declares 36, has **45** rows) — inside the doc legislating LAW-SOT-03. `STANDARDS_ENFORCEMENT_MATRIX.md:82-86` tallies 26 vs 29 rows and uses a status absent from its own vocabulary. **No check catches tally-vs-rowcount** | as cited | measured | the governance layer's own headline numbers rot | Certain | — | no | no | no |
| **F-31** | governance | **`CI_CONTROL_INVENTORY.md` is declared a `generated_view` with no generator**; `tools/ci/common.py:11` `GEN_VIEW` is defined and never read | `ci-control-registry.yml:23`; `tools/ci/common.py:11` | verified | signature defect inside the CI-governance layer | Certain | self-labels "Provisional" | no | no | no |
| **F-32** | generated drift | **`kb/` (14) + `contract/` (8) are DECLARED artifacts stamped `git_head: fcffa73` — 58 commits / 26 `src/` commits behind HEAD.** *Not* the self-invalidating-stamp defect (they are never regenerated, so the stamp is legitimate provenance) — the defect is **staleness** | `kb/*.json:4-5` | measured | policy rules read stale declared counts (→ F-27) | Certain | ARCH-008/009 report the drift as WARNING | no | no | no |

---

## 23. Canonical Present-State Technical Model

**FanOps at `6d21749` is a 132-module Python engine that clips long-form video into per-persona social posts, driven by one console script, one launchd daemon, and a Flask cockpit, against a 40 GB out-of-repo data root** `R-CLM-030`.

- **Active executable surfaces:** `fanops` (45 verbs + 17 subcommands); `com.fanops.run` (PID 9121, 600 s loop); `com.fanops.studio` (PID 9123, :8787, 149 routes); `com.fanops.keeper` (120 s adopt timer); `python -m fanops._fwrun` (ASR subprocess); `python -m tools.arch` (CI) `R-CLM-020`.
- **Active subsystem graph:** CLI/ops → ingest → clip/framing → moments/personas → caption/hashtags → crosspost → publish → reconcile → track → learning, over a single SQLite/WAL ledger gateway `R-CLM-030`.
- **Active data flows:** inbox → staged copy → `Source(pending)` → human promotion → LLM-answered gates → moments (single-owner) → hooks → per-account clips → `Post(awaiting_approval)` → Review approval → `queued` → `_publish_one`. **The flow terminates at publish**: 0 published, 2 failed, 68 exposed `R-CLM-031`, `R-CLM-032`.
- **Active stores:** `ledger.sqlite` (v11, 1,063 rows, `integrity_check: ok`), 8 JSON control files, 38 GB media. **All untracked, outside the repo** `R-CLM-066`.
- **Active external integrations:** Postiz (IG), **Zernio (TikTok — failing 405)**, Meta Graph (IG insights, sole IG metric reader), Cloudflare R2 (SigV4), `claude -p` (LLM gates), ffmpeg/ffprobe/whisper/yt-dlp. **Every HTTP call has a timeout** `R-CLM-032`.
- **Active tests:** 5,379 collected; **5,377 execute per PR**; 2 (`asr`) never run `R-CLM-015`.
- **Active CI enforcement:** exactly **2** required contexts; 9 of 11 jobs advisory; `enforce_admins: false` `R-CLM-011`. The arch gate's invariants are nonetheless carried by the **required** unit lane, and its 25 negative controls by the **required** e2e lane `R-CLM-013`.
- **Active runtime:** running **HEAD exactly**; adopted code 11× today `R-CLM-021`.
- **Non-main extensions:** exactly **2** units of unmerged code (`fix/darwin-test-gate`, `fix/cursor-all-route`); 1 public-branch hygiene violation; 1 quarantined superseded doc draft `R-CLM-070`, `R-CLM-072`.
- **Known unreachable/shadow:** the Render limb (0 rows / 108 orphaned files) `R-CLM-061`; the `Settings` shadow config surface; 4 unreachable functions; `timing_bias.json` write-only `R-CLM-064`.
- **Known unresolved:** the Zernio 405's server-side cause `R-Q-01`; whether the keeper fix holds beyond one day `R-Q-02`; the live `.env` key set `R-Q-04`.

---

## 24. Closeout Classification

### **Coherent on main, materially divergent in runtime.**

**Main is clean, current, and self-consistent** — HEAD == `origin/main`, no open PRs, CI green 20/20, the three governance planes byte-identical `R-CLM-010`, the derived artifacts current at 132/132 `R-CLM-013`, the daemon running that exact revision `R-CLM-020`. The invariants that matter (no-auto-publish, dual dryrun/live gates, cascade protection, amplify-only bias) all **hold in code and in the live ledger**.

**The runtime is live and delivering nothing.** `FANOPS_LIVE=1`, 347 posts, **0 ever published**, 2 failed on a Zernio 405, 68 more queued behind the same broken call `R-CLM-031`, `R-CLM-032`. That is not a code-coherence problem — it is an external contract that moved under a reverse-engineered client. It is nonetheless the thing standing between this repository and a closed loop.

### Closeout blockers

| # | Blocker | Why it blocks |
|---|---|---|
| **1** | **F-01 — Zernio 405; 0 published / 68 exposed** | The system's entire purpose is unrealized; the learning half of the loop can never start (0 analyzed posts → F-07/F-08 stay unfalsifiable). |
| **2** | **F-02 / F-27 / F-28 — three validators that cannot fire, all asserted as working** | `check-locks.sh` (a `required` gate asserted `verified-this-session`), ARCH-006's doc byte-compare (no required owner), and `field_authority.json:87`'s false `fail CI` claim — **whose censuses are drifting live, green**. Until each is fixed **and given a negative control**, no claim resting on them is trustworthy. The class detector (**CM-8**) is specified and unbuilt `R-CLM-084`. |
| **3** | **F-03 — `CLAUDE.md`'s "read FIRST" pointer is untracked** | Every future agent's entry instruction resolves to nothing on a clone. |
| **4** | **F-04 — codemap claims 109/109 of a 132-module tree** | The map handed to the applied-program agents omits ≥23 modules, **including both programs' newer surfaces**. |
| **5** | **F-17 / F-18 — operator dispositions** | A public-branch hygiene violation, and a complete tested feature whose only copy is in temp storage. |

### Non-blocking residuals

F-05, F-06, F-09..F-16, F-19..F-26 — all bounded, all evidenced, none load-bearing for main's coherence. **F-07 and F-08 are dated, not dormant**: they activate the moment publishing resumes and 8 analyzed posts accrue. Fixing F-01 without fixing them ships the bias bugs live.

### Operator-only evidence gaps

`R-Q-04` (live `.env` key set — read denied), `R-Q-01` (Zernio server-side cause — live-verb rule), lock re-resolution (§3.6).

---

## 25. Recommended Next Actions

*(Bounded investigations/remediations. No patches. No execution prompts.)*

| ID | Class | Required outcome | Reason | Evidence | Prereq | Owner | Change type | Validation | Risk | Closeout impact |
|---|---|---|---|---|---|---|---|---|---|---|
| **A-01** | runtime verification | Determine the Zernio `/media/upload` **current** contract (verb/path/field) and why step 2 405s while step 1 succeeds | F-01 | `zernio.py:141-161`; 2 failed posts | operator consent for a live call | operator + publish | investigation | one probe against a throwaway asset | **live network** | **Unblocks #1** |
| **A-02** | code | Restore the lock-drift guard **and give it a negative control** that fails when a dep is added inside an existing block | F-02 | proven twice | — | CI | fix + test | the control must fail before the fix and pass after | low | **Unblocks #2** |
| **A-03** | CI correction | Make the meta-test enumerate the **registry's** `controls:` list instead of a hardcoded DC set | F-02 | `test_ci_registry_validator.py:49-53` | A-02 | CI | test | a `required` control with no control ⇒ red | low | Prevents recurrence |
| **A-04** | documentation | Decide the fate of `.reports/issue-register-2026-07-03.md`: **track it, or stop citing it as authority** | F-03 | `git ls-files` | — | governance | doc or gitignore | a clone can follow every `CLAUDE.md` pointer | low | **Unblocks #3** |
| **A-05** | generated-artifact reconciliation | Either re-scope the codemap's completeness claim to the truth, or re-run coverage to 132 | F-04 | measured | — | governance | doc | claimed count == `derived/modules.json` | low | **Unblocks #4** |
| **A-06** | CI correction | Gate the `CLAUDE.md` family — anchors are evidence and rot like any other derived number | F-05 | ~24/27 stale | — | `tools/arch` | validator | a moved symbol reddens the gate | med | Stops the largest doc-rot source |
| **A-07** | code | Port MOL-79's per-row guard to `personas.py:70-82`; **log the swallow** at `accounts.py:335`; correct the inverted claim | F-06 | 2 independent finds | — | personas | fix + doc | a malformed row degrades one persona, logged | low | — |
| **A-08** | contract correction | Decide `timing_bias`: wire the file's reader **or** delete it; gate `timing_bias_hour_for` on `cfg.timing_bias` | F-07 | `timing_bias.py:36` | — | learning | fix | switch OFF ⇒ provably no bias | **med — dated** | Must precede #1's fix |
| **A-09** | data migration | Reset `cutover.json` on wipe, **or** gate on evidence that still exists | F-08 | live file vs ledger | — | learning | fix | the gate is falsifiable | med | Must precede #1's fix |
| **A-10** | operator action | Dispose of `origin/cursor/cloud-agent-…f4sx9` (its work already landed) | F-17 | verified | — | operator | branch deletion | `MohFlow-FanOps/` absent from all refs | **destructive — operator only** | Closes #5a |
| **A-11** | operator action | Land or archive `fix/darwin-test-gate` before its scratchpad is reaped | F-18 | verified | rebase (66 behind) | operator | merge | — | med | Closes #5b |
| **A-12** | CI correction | Re-enable `nightly` or delete it and drop the registry's `active` claim | F-13 | `gh api` | — | CI | workflow/registry | registry state == GitHub state | low | — |
| **A-13** | dead-code adjudication | Decide the Render limb (0 rows, 108 orphaned files) and the 4 unreachable functions | F-11, F-24 | full sweep | — | arch | delete or wire | — | low | — |
| **A-14** | test correction | Fix the ratchet's allowlist (locally-bound `log`) and its `ast.walk` guard; re-baseline to the true count | F-16 | measured | — | tests | fix | baseline == actual | low | Restores the metric |
| **A-15** | evidence acquisition | Read the live `.env` key set under operator supervision | `R-Q-04` | denied | operator | operator | none | — | low | Closes a gap |
| **A-16** | contract correction | Correct `field_authority.json:87` to say **WARNING**, **or** raise ARCH-008/009 to BLOCKING — then reconcile `kb/side_effects.json` (35→37, 3→5) | F-27 | verified | decide which | arch | doc **or** severity | the census drift must change the verdict, or the doc must stop claiming it does | **med — raising it reddens the required lane today** | Closes #2 |
| **A-17** | CI correction | Give ARCH-006's doc byte-compare a **required** owner (call `stale_docs()` from the unit-lane test), or downgrade C16.3/LAW-DOC-01 to `partially-enforced` | F-28 | verified | — | arch | test **or** doc | a hand-edited generated doc must redden a required lane | low | Closes #2 |
| **A-18** | governance | Build **CM-8** — the mechanical predicate "a rule claims `enforced` but its cited control is advisory/absent" — and a tally-vs-rowcount check | F-27..F-31 | `CONSTITUTION_MAINTENANCE.md:42,100` | unblock DC-3 | governance | validator | it must fire on F-28/F-29 before the fix | med | Prevents recurrence of the whole class |
| **A-19** | documentation | Re-point the ~13 `ARCH-GATE` attributions at `CI-UNIT-ARCHGOV`; fix `CONSTITUTION_MAINTENANCE.md:72`'s "unit lane **or** gate" (they have opposite merge consequences) | `R-CLM-081` | verified | — | governance | doc | — | low | Stops implementers picking a non-blocking host |

---

## 26. Handoff Contract

### 26.1 Program and Decision History Agent

- **Reconstruct:** why the **Render limb** was built and abandoned (`add_render` 0 callers, `crosspost.py:225` hardcodes `render_id=None`, 0 rows, **108 orphaned files**) — F-11.
- **Unclear supersession:** `_catalogue_file` (dead) vs `_stage_candidate`/`_mint_candidate` (live) — the codemap still names the dead one (`R-CLM-060`). `.run-kick` superseded per `docs/adr/README.md:1172` yet still documented as current at `C9:462`.
- **Unexplained duplicates:** `_is_pinned` (byte-identical across the eager cycle); `_enabled_strategies` (knowingly duplicated, unpinned by any test); the `config.py`/`settings.py` split — **`contract/prompts/C6-S02.md:139` says "DO NOT unify"; find out why, because `runtime_load` is dead** (F-12).
- **Branches with unknown lineage:** `fix/darwin-test-gate` (unmerged, in temp storage), `fix/cursor-all-route` ≡ `fix/cursor-agent-trust-flag` (identical trees, two branches).
- **Deleted/renamed architecture:** `moment_casting`/`AccountSelection` (gone, but the v8→v10 migration hops still build-then-drop the map **by design**); `variant_hook` (field deleted, **name survives as a live view-model alias**).
- **Intentionally preserved anomalies:** `_postiz_permalink` always `None`; `DryRunPoster.publish` retained as the protocol fallback; the `AccountSelection` migration hop-chain.
- **Evidence gaps:** **0 open PRs** and 168 unmerged remote branches — PR metadata cannot explain them; **squash-merge destroyed patch-id**, so `git cherry` over-reports and only blob comparison settles merge status (`R-CLM-071`).

### 26.2 Applied Programs Agent

**Reframing:**
- Entry points: `fanops reframe --dry-run|--apply|--status|--resume|--rollback|--cleanup` (`cli.py:1182`, exactly-one-verb guard `:1199`); render path `clip.render_moment` → `framing._resolve` (`framing.py:834`).
- **The real fail-closed gate is `framing._framing_runtime_or_raise` (`framing.py:67`), NOT `require_cv2` (`framing.py:106`, test-only)** — `CLAUDE.md` and `errors.py:71` are stale on this (F/`R-CLM-046`).
- Shared contracts: `MANIFEST_SCHEMA_VERSION = 1` (`reframe.py:49`), `RUN_SCHEMA_VERSION = 1` (`reframe_apply.py:58`), `_REFRAME_GEOM_V`, `_render_fingerprint` (skips ffmpeg on match).
- Shared stores: `03_clips` (1,864 dirs), `<src>.detect.json`, `.render.json` sidecars, `reframe.lock`, `_OWNED_RUN_ID` (`reframe_apply.py:159,181`).
- Tests: `test_smart_framing.py`, `test_keyframes.py`, `test_reframe*.py` (16 files) + the `slow`/integration lanes.
- **Residuals:** `reframe.py`/`reframe_apply.py` are **outside the codemap trace** (F-04); the Render limb is dead (F-11); `framing.py:157` records a detector crash as a legitimate "no face" (§19).

**Hashtags:**
- Entry points: `fanops hashtags refresh|discover|migrate` (`cli.py:1302`); `refresh_store_if_due` in the daemon post-loop (`cli.py:1067`) — **not** live-gated, 12 h throttle.
- **Runtime truth: the reach loop has produced ZERO data** — `hashtags.json` = `{tags:[18], reach:{}}`, the **floor** path (`fanops_hashtags.py:85`), never the measured path (`:127`). Measured diversity: **347 posts → 28 distinct tag-sets**; one handle: **76 posts → 4 sets** (F-23).
- Shared contracts: `vet_hashtags(..., corpus=)`; persona `hashtag_corpus`; provenance `graph-reach`; the 30/7-day Graph budget (**fails closed + loud**).
- Shared stores: `hashtags.json`, `hashtag_budget.json` (**locked but raw-write**, F-22), `hashtag_bans.json` (the best-behaved store).
- **Residuals:** `hashtag_hygiene.py`/`hashtag_migrate.py`/`studio/hashtags.py` are **outside the codemap trace** (F-04); `hashtag_migrate.py:176,192` **bypasses the store lock**; `caption.py:176` swallows a `Personas.load` failure and drops `intake.genre` — the niche seed (F-06); the `or []` at `meta_graph.py:520` can destroy the 7-day budget history (F-22).
- **Attribution invariant holds:** post insights attribute to hook/clip/account, never the hashtag (`test_hashtag_attribution_severance.py`).

**Shared daemon path:** both ride `_cmd_run_pass` (`cli.py:981`); both are inert without the corresponding live evidence.

### 26.3 Final Integration Director

- **Canonical model:** §23. **Classification:** §24 — *coherent on main, materially divergent in runtime.*
- **Confirmed contradictions** (code ↔ tests ↔ CI ↔ docs ↔ config ↔ runtime): F-02 (registry asserts a validator that cannot fire), F-03/F-04/F-05/F-06 (the `CLAUDE.md` family), F-13 (registry `active` vs GitHub `disabled`), `ci-control-registry.yml:14` vs `:22`, `ledger.py:2` "git-versioned", `studio/CLAUDE.md:13` "Blueprints".
- **Closeout blockers:** §24 (5).
- **Non-main state needing disposition:** F-17 (public-branch ledger), F-18 (`darwin-test-gate`), `fix/cursor-all-route` vs its duplicate, `cursor/mol-476` (**must not land**), `docs/constitution/` (**leave quarantined**), 24 residue worktrees + ~168 remote branches.
- **Runtime drift:** **none** `R-CLM-020` — but the control is 5 hours old with one day's evidence `R-Q-02`.
- **Operator-only decisions:** A-01, A-10, A-11, A-15.
- **All claim/evidence IDs:** §27/§28 ledgers below.

---

## 27. Unresolved Questions

| ID | Question | Why it matters | Evidence inspected | Missing evidence | Responsible | Closeout impact |
|---|---|---|---|---|---|---|
| **R-Q-01** | What changed server-side at Zernio such that `POST /media/upload?token=…` 405s while `/media/upload-token` still 200s? | **The closeout blocker.** Determines whether this is a verb change, a path change, a field rename, or account-level revocation | `zernio.py:123-173`; 2 failed posts; `media_urls: []` | one live probe (**not run — live-verb rule**) | operator + Zernio | **Blocks #1** |
| **R-Q-02** | Does the keeper's adopt path hold beyond one day? | The no-drift verdict rests on **one day's** evidence and a **5-hour-old** fix (#688/#689) | 11 adoptions, 9 kickstarts, the storm-guard countdown | ≥1 week of `daemon-keeper.out` | ops | Confidence in `R-CLM-020` |
| **R-Q-03** | Why does the live `.env` set `FANOPS_OPERATOR_TZ=America/New_York` while the host is UTC+04 and the artist is Gulf-based? | `timing_bias` stamps `publish_hour`/`publish_dow` in this tz; wrong ⇒ the (dated) timing bias learns the wrong hours | `config.py:992`; `post/run.py`; `reconcile.py` | operator intent | operator | Interacts with F-07 |
| **R-Q-04** | What is the live `.env` key set? | Determines whether any required key is missing/stale | `config.py` read sites; subagent probes | **read denied by the permission layer** | operator | Bounds §5.3 |
| **R-Q-05** | Was the 347-post ledger rebuilt? (`published=0` yet `06_published/2026-06-29..07-05/` holds 39+ archived records, and `archive/ledger-rebuild-*` tags exist) | Decides whether "never published" is literal or an artifact of a rebuild — changes F-01's history | ledger; `06_published/`; tags | rebuild provenance | history agent | Frames F-01 |
| **R-Q-06** | Are the 168 unmerged remote branches all squash residue? | 96% of the local sample was; the remainder was not enumerated individually | 63-branch local blob-level sample | per-branch blob comparison | history agent | Bounds §16.4 |

---

## 28. Completion Attestation

I attest that:

- **All 28 required sections exist**, in the mandated order, plus all 33 required tables/registers (§7 lists the diagram-equivalents; every diagram has a tabular or textual twin).
- **`origin/main`, checkout, local, PR, and runtime states are distinguished throughout** (§3), and never described as one another.
- **Every active-use claim carries invocation evidence** — a caller, a workflow line, a plist, a registration, a subprocess string, or a live PID. Where source alone cannot prove use (`.claude/workflows/*.js`), it is marked **not observable**, not "active".
- **Test claims distinguish presence from execution**: 5,379 collected vs **5,377 executed per PR** vs **2 never run**. The suite was **never executed locally** (project rule); collection only.
- **CI claims distinguish existence from requirement**: 11 jobs exist, **2 are required**, 9 are not — enumerated by exact context name.
- **Runtime claims identify revision**: the daemon's own heartbeat carries `code = 6d21749…` == HEAD, corroborated by an editable install, `ps` start time vs commit time, and 11 logged adoptions.
- **Dead-code claims account for dynamic and external invocation**: every candidate was swept for bare strings, lazy imports, aliases, Jinja registration, dict dispatch, `_MIGRATIONS`, and subprocess module strings. **Nothing is recommended for deletion on static non-reference alone** — `_fwrun.py` (0 imports, live) is the standing counterexample.
- **Every material architecture/contract/governance/invariant claim derived from primary evidence was verified or classified** (§20), including claims that **contradict** the repository's own documentation, and claims where the documentation is **right and honest** (the registry's `transitioning` disclosure; the constitution's 10 self-declared `documented-only` rules).
- **All known integrity findings are disclosed** (§22, **32 findings**), including five where an investigator's initial claim was **corrected by direct measurement** rather than relayed: the exposed ledger's severity (**18 rows, 0 credentials** — a hygiene violation, *not* the credential leak first reported), the `.env` location (data root, not repo root), the Flask "Blueprints" claim, `.env` precedence (`cli.py:845`, **not** the dead `Settings.runtime_load`), and the `ARCH-GATE` enforcement story (**misattributed mechanism**, not a blanket false claim — the invariants really do block, via `CI-UNIT-ARCHGOV`). One finding was raised **against this document's own draft** (a loose `.reports/` inventory row marking dead orphan residue as gate-verified) and corrected.
- **Claims that survived an attempt to refute them** are recorded as such, not silently upgraded: `field_authority.json:87` (checked against `policy.py:144` **and** the live census), ARCH-006's missing required owner (checked against the actual test body at `test_arch_governance.py:38`), and the daemon's revision (checked four independent ways).
- **No unauthorized mutation occurred.** No implementation, test, workflow, configuration, ADR, codemap, contract, schema, generated artifact, runtime state, repository setting, branch, worktree, PR, issue, deployment, database, store, daemon, cache, or operator setting was modified. Two read-only **copies** of SQLite databases were taken into the session scratchpad (outside the repo) to avoid WAL locks; the sources were never opened for write. One blocked edit (`block-operator-gate-hedging`) was resolved by **rephrasing the document**, never by disabling the rule.
- **Only the target document was created by this assignment**: `docs/reconciliation/02_REPOSITORY_REALITY_AND_INTEGRITY.md` (and its parent directory). **Disclosure:** a second file now sits beside it — `04_APPLIED_PROGRAMS_RECONSTRUCTION.md`, written by a **concurrent session** at 22:51 +04, **not by this assignment and never read by it** (§21). It is named here so its presence is not mistaken for scope creep by this agent, and so the next reader knows the directory had two authors.
- **A concurrency caveat applies to the whole document.** ≥1 other session was writing this repository during the observation window. §3's frame is authoritative for this document only; peer documents were produced under different frames (§21).

**Observed SHA:** `6d21749ffc49c77383f537d93b028cca0d69a447` (checkout == `origin/main`).
**Final closeout classification:** **Coherent on main, materially divergent in runtime.**

---

## Evidence Ledger

| ID | Class | State | Location | Observation | Authority | Limitation |
|---|---|---|---|---|---|---|
| R-HIST-001 | history | origin/main | `git rev-parse origin/main` | `6d21749…`; 0 ahead/0 behind; merge-base == HEAD | Frame identity | Snapshot at 18:19Z |
| R-EXT-001 | external | live GitHub | `branches/main/protection` | contexts = exactly 2; `enforce_admins:false`; reviews 0 | The live gate | — |
| R-EXT-002 | external | live GitHub | `…/rulesets` → `[]` | no rulesets | Classic BP is the only gate | — |
| R-CI-001 | CI | main | `docs/ci/freeze/2026-07-15/branch-protection.json` | **byte-identical to R-EXT-001** | Declared == live | — |
| R-CI-002 | CI | main | `ci-control-registry.yml:40-53` | `current` == live; `intended` = 5; `phase: transitioning` | Gap is disclosed | Registry ≠ enforcement |
| R-CI-003 | CI | live GitHub | `actions/workflows` | `nightly` = `disabled_manually`; 3 registered files absent from main | Workflow state | — |
| R-CI-004 | CI | main | `check-locks.sh:11-13` | `rg -n` ⇒ `^\+` unmatchable; **reproduced twice** | Guard cannot fire | — |
| R-GEN-001 | generated | main (tracked) | `derived/modules.json`, `MANIFEST.json` | **132** modules; content digests; no wall-clock | Live arch layer current | — |
| R-GEN-002 | generated | **untracked** | `.reports/structural_index.json` | **108** modules, Jul 3 | Dead residue | gitignored |
| R-GEN-003 | generated | main | `full-trace-index.md:3,34,51,179` | claims 109/109 **and** 108/108 | Stale completeness claim | frozen 2026-07-11 |
| R-CODE-001 | code | main | `ledger.py:190` | `SCHEMA_VERSION = 11` | Schema identity | — |
| R-CODE-002 | code | main | `zernio.py:123-173` | 2-step upload "DISCOVERED LIVE 2026-06-29"; `:161` raises on ≥300 | The 405 site | — |
| R-CODE-003 | code | main | `grep "Settings(" src/` | only the class def `:141`; `runtime_load` 0 callers | Shadow config | — |
| R-CODE-004 | code | main | `grep "Blueprint" src/` → **0** | no Flask blueprints | Refutes `studio/CLAUDE.md:13` | — |
| R-OPS-001 | operational | runtime | `ps -o lstart -p 9121` | started `16:49:48Z`; HEAD committed `16:39:07Z` | Daemon post-dates HEAD | start time ≠ import identity alone |
| R-OPS-002 | operational | runtime | `direct_url.json` | `{"editable": true}` → repo `src/`; no site-packages copy | Daemon imports this tree | — |
| R-OPS-003 | operational | runtime | `daemon.err` 18:30:13Z | `code=6d21749…`, `v0.4.0`, `last_published_age_hours:"None"` | **Revision + never-published** | self-reported |
| R-OPS-004 | operational | runtime | `daemon-keeper.out` | 11 adoptions, 9 `kickstart_stale_code`, storm guard 241→603 s vs 720 s | Adopt path works | one day |
| R-DATA-001 | data | live ledger (copy) | `ledger_meta` / `ledger_rows` | schema 11; 347 posts: 277/68/2/**0 published** | Live funnel state | snapshot 18:32Z |
| R-DATA-002 | data | live ledger (copy) | failed-post payload | `Zernio upload failed (405)`, `media_urls: []`, platform `tiktok` | The blocker | — |
| R-DATA-003 | data | live control file | `accounts.json` | 3× IG→postiz; **`backlikeineverleft`/`hrmny-blog` tiktok→zernio** | All 68 queued route to Zernio | — |
| R-LOCAL-001 | local | untracked | `docs/constitution/` | never in any of 338 refs; self-marked SUPERSEDED; §4.2 inverts GB-5 | Dead residue | — |
| R-LOCAL-002 | local | remote branch | `…f4sx9:MohFlow-FanOps/00_control/ledger.sqlite` | `SQLite format 3`, 57,344 B; **18 rows; 0 credential strings**; main tracks 0 there; repo **public** | Hygiene violation, low sensitivity | — |
| R-LOCAL-003 | local | worktree | `fix/darwin-test-gate` @ `9107c07` | 2 new files +201/-23; **absent from main**; in `/private/tmp` | Unmerged work at risk | — |
| R-TEST-001 | test | main | `pytest --collect-only -m …` | 5322 + 24 + 30 + 1 + 2 = **5379**; PR runs 5377 | Execution reality | collection only |
| R-CI-005 | CI | main | `python -m tools.arch ci` (read-only) | `0 stale, 0 BLOCKING, unknowns 8/8, verdict: PASS`; regen → `tempfile.mkdtemp()` (`drift.py:47-49`), `git status` byte-identical before/after | Gate is green; invoking it is non-mutating | point-in-time |
| R-CI-006 | CI | main | `test_arch_governance.py:32-197` unmarked | 8 arch invariants collected by `-m "not integration and not slow"` ⇒ **required unit lane**; `:203` `@slow` ⇒ required e2e | **The real blocking owner is `CI-UNIT-ARCHGOV`** | — |
| R-CI-007 | CI | main | `drift.py:74,204`; `test_arch_governance.py:38,115`; `ci.yml:97` | `stale_docs()` callers = non-required `gate` + a temp-root negative control; required test calls `stale_artifacts()` only; ARCH-006 emits no Finding; `base install` is a standalone job | Two `enforced` claims have no required owner | — |
| R-CI-008 | CI | main | `field_authority.json:87` vs `policy.py:144` vs `kb/side_effects.json:15,19` | doc says "fail CI"; rule severity is **`WARNING`**; declared 35/3 vs code **37/5**; gate **PASS** | Proven false mechanism claim, live | — |

## Claim Ledger

| ID | Claim | State | Status | Evidence | Counter | Conf. | Integrity consequence |
|---|---|---|---|---|---|---|---|
| **R-CLM-010** | The declared freeze snapshot, the registry's `current_required_contexts`, and live GitHub branch protection are **byte-identical** | live + main | **Confirmed** | R-CI-001, R-EXT-001, R-CI-002 | None | High | The CI governance plane is genuinely reconciled |
| **R-CLM-011** | Only **2** checks are merge-blocking; 9 of 11 jobs cannot block; `enforce_admins:false` + 0 reviews ⇒ CI is advisory for the sole operator — **and this is disclosed** | live | **Confirmed** | R-EXT-001, R-CI-002 | None | High | A bounded, declared residual — not concealment |
| **R-CLM-012** | `check-locks.sh` **cannot fire**, while the registry asserts it `verified-this-session` | main | **Confirmed** | R-CI-004 | None | High | A `required` gate that manufactures confidence |
| **R-CLM-013** | The tracked arch-derived artifacts are **current (132/132)**, content-hashed, determinism-contracted, no wall-clock stamp | main | **Confirmed** | R-GEN-001 | None | High | The live governance evidence base is trustworthy |
| **R-CLM-014** | `python -m tools.ci` is invoked by **no workflow**; DC-3 never runs automatically; the library is reached only via a required unit test | main | **Confirmed** | grep; R-CI-002 | None | High | Matches the docs' own `partially-enforced` claim |
| **R-CLM-015** | `nightly` is **disabled**; the 2 `asr` tests have not run since 2026-07-14; the registry says `active` | live | **Confirmed** | R-CI-003 | None | High | Registry ≠ GitHub state |
| **R-CLM-016** | `.claude/workflows/*.js` have **zero in-repo references**; corroboration is circular | main | **Not observable** | grep | — | Med | `CLAUDE.md`'s "load-bearing" claim is unprovable in-repo |
| **R-CLM-020** | The resident daemon executes **`6d21749` == HEAD == `origin/main`**. Runtime drift = **zero** | runtime | **Confirmed** | R-OPS-001/002/003 | None | **High** | Refutes the standing "stale daemon" risk **at this instant** |
| **R-CLM-021** | The keeper adopted new code **11×** today (9 kickstarts); storm guard observed 241→603 s vs a 720 s settle | runtime | **Confirmed** | R-OPS-004 | None | High | #688/#689 work in production |
| **R-CLM-022** | The "live daemon runs stale code" memory was **true until ~13:50Z today** (`ps -o etimes` is a GNU keyword absent on BSD) and is now **obsolete** | runtime | **Confirmed** | R-OPS-004; `daemon.py:250-266` | None | Med-High | The control is **5 hours old**; one day of evidence (`R-Q-02`) |
| **R-CLM-030** | Live: `FANOPS_LIVE=1`, ledger schema 11, 1,063 rows, 347 posts, 40 GB root, `integrity_check: ok` | runtime | **Confirmed** | R-DATA-001 | None | High | The system is real and active |
| **R-CLM-031** | **0 posts have ever published** (`last_published_age_hours:"None"`); `public_url`/`published_at` = 0 across all 347 | runtime | **Confirmed** | R-OPS-003, R-DATA-001 | `06_published/` holds 39+ archived records ⇒ possible rebuild (`R-Q-05`) | High | The loop has never closed |
| **R-CLM-032** | The Zernio `/media/upload` **405** is the blocker: all 68 queued posts are TikTok; both TikTok accounts route to Zernio; 2 already failed | runtime | **Confirmed** | R-DATA-002/003, R-CODE-002 | None | **High** | **Closeout blocker #1** |
| **R-CLM-033** | The remaining 68 are exposed as they come due (earliest 18:57Z) | runtime | **Confirmed** | R-DATA-001 | None | High | Progressive failure |
| **R-CLM-034** | The Zernio contract was **reverse-engineered** ("DISCOVERED LIVE 2026-06-29"); a 405 on a previously-working POST signals server-side drift | main | **Partially confirmed** | R-CODE-002 | API not probed | Med | Cause inferred, not reproduced (`R-Q-01`) |
| **R-CLM-040** | `CLAUDE.md` cites `.reports/issue-register-2026-07-03.md` as "read FIRST" — it is **gitignored/untracked** | main | **Confirmed** | `git ls-files`; `.gitignore:62` | None | High | Local-only authority in the always-loaded file |
| **R-CLM-041** | `docs/CONFIG.md` survives a mechanical audit: **zero undocumented** env vars | main | **Confirmed** | subagent diff | `FANOPS_DAEMON_INTERVAL` (E-04) | High | The env authority is real |
| **R-CLM-042** | The codemap claims **109/109** (and 108/108) coverage of a **132**-module tree; ≥23 modules are outside it | main | **Confirmed** | R-GEN-003, R-GEN-001 | freeze banner discloses staleness | High | Both applied programs' newer surfaces are untraced |
| **R-CLM-043** | `studio/CLAUDE.md:13` calls the route modules "Blueprints"; there are **none** | main | **Confirmed** | R-CODE-004 | None | High | Doc contradicts code |
| **R-CLM-044** | The MOL-79 claim is **inverted**: `Accounts.load` is defensive; `Personas.load` is the laggard; `accounts.py:335` swallows its error unlogged | main | **Confirmed** | 2 independent finds | None | High | One bad row silently de-personas every account |
| **R-CLM-045** | `FANOPS_CREATIVE_VARIATION=1` is set in the live `.env` with **0 readers** in `src/` | runtime | **Confirmed** | grep; subagent | — | Med | Dead flag live in production |
| **R-CLM-046** | The production cv2 fail-closed gate is `_framing_runtime_or_raise`, **not** `require_cv2` (test-only); `CLAUDE.md`+`errors.py:71` are stale | main | **Confirmed** | `framing.py:67,106,862` | None | High | The boundary holds; the naming misleads |
| **R-CLM-050** | `PROBE.sh` is an orphan (full sweep → 0 refs) | main | **Confirmed** | sweep | — | High | Root-level residue |
| **R-CLM-051** | `SIBLING_POLL_AGENTS` declares 2 agents the repo **cannot install**; `media-sync` exists only out-of-tree | main | **Confirmed** | `daemon.py:414-416` | — | High | Permanent "not installed" rows |
| **R-CLM-060** | `_catalogue_file` is **dead**; the live path is `_stage_candidate`/`_mint_candidate` | main | **Confirmed** | `ingest.py:283,305,427` | None | High | The codemap names the dead one |
| **R-CLM-061** | The Render limb is unreachable: `add_render` 0 callers, `render_id=None` hardcoded, **0 rows, 108 orphaned files** | main + runtime | **Confirmed** | `ledger.py:571`; `crosspost.py:225` | None | High | 87+ dead lines; unreclaimable files |
| **R-CLM-062** | The hashtag reach loop has produced **zero** data (`reach: {}`, floor path); 347 posts → **28** tag-sets | runtime | **Confirmed** | live `hashtags.json` | None | High | The reach-ranked lifecycle is unproven live |
| **R-CLM-063** | `cutover.json metrics_confirmed` is **stale-true** — its certifying evidence no longer exists; no wipe path resets it | runtime | **Confirmed** | live file vs ledger | None | High | An unfalsifiable correctness gate |
| **R-CLM-064** | `timing_bias.json` is **write-only**; `timing_bias_hour_for` never reads `cfg.timing_bias` | main | **Confirmed** | `timing_bias.py:36,65-77,103-107` | None | High | The kill switch gates a no-op |
| **R-CLM-065** | The legacy JSON snapshot is **unrestorable** — `snapshot_is_restorable` rejects `.json`, contradicting `ledger.py:533` | main | **Confirmed** | `ledger_wipe.py:224` | None | Med | Break-glass needs manual staging |
| **R-CLM-066** | `ledger.py:2` calls the ledger "git-versioned" — **false**; `.gitignore:10` ignores the whole data root | main | **Confirmed** | `.gitignore:10` | None | High | Doc contradicts reality |
| **R-CLM-070** | `docs/constitution/` never landed in any of 338 refs, self-marks SUPERSEDED, is adjudicated in a tracked doc, and its §4.2 **inverts GB-5** | local | **Confirmed** | R-LOCAL-001 | None | High | **Do not revive.** No decision needed |
| **R-CLM-071** | Squash-merge destroyed patch-id ⇒ `git cherry`/`git diff --stat` **over-report**; only blob comparison settles merge status | main | **Confirmed** | subagent method | None | High | Method note for later agents |
| **R-CLM-072** | A real SQLite DB (57,344 B) sits on a **public** remote branch past `.gitignore` — but holds **18 rows and 0 credentials** | remote | **Confirmed** | R-LOCAL-002 | None | High | Hygiene violation, **not** a credential leak |
| **R-CLM-080** | The arch gate **currently PASSES** (`0 stale, 0 BLOCKING, unknowns 8/8, verdict: PASS`) and regenerates **into a tempdir only** — invoking it mutates nothing | main | **Confirmed** | R-CI-005 | None | High | The advisory gate is green; the risk is latent, not active |
| **R-CLM-081** | The arch invariants **do block — via `CI-UNIT-ARCHGOV`** (unmarked tests in the required unit lane), **not** via the non-required `ARCH-GATE`. Docs crediting ARCH-GATE **misattribute the mechanism, not the outcome** | main | **Confirmed** | R-CI-006 | None | High | Most "ARCH-GATE enforces X" rows are true-but-misattributed |
| **R-CLM-082** | **Two rules claim `enforced` with NO required owner:** ARCH-006's doc byte-compare (`stale_docs` runs only in the non-required gate) and `CI-BASEINSTALL` (standalone job) | main | **Confirmed** | R-CI-007 | `LAWS:177` self-concedes CI-BASEINSTALL | High | The genuine enforcement gaps, distinct from misattribution |
| **R-CLM-083** | **`field_authority.json:87` asserts a `fail CI` mechanism that is a `WARNING`, and the censuses it governs are drifting live** (35→37, 3→5) with verdict PASS | main | **Confirmed** | R-CI-008 | None | **High** | The signature defect inside the Declaration of Canonical Authority |
| **R-CLM-084** | **The detector for this class is designed and unbuilt** — `CONSTITUTION_MAINTENANCE.md:42` specifies CM-8 ("a rule claims `enforced` but its cited control is advisory/absent"); `:100` concedes no executable code; it is gated on DC-3, which never runs | main | **Confirmed** | `CONSTITUTION_MAINTENANCE.md:42,100`; R-CLM-014 | None | High | The gap is known and blocked on a validator that is itself unwired |
| **R-CLM-085** | The governance layer **self-reports 18 of 66 rules as not-fully-enforced** (10 `documented-only`, 8 `partially-enforced`, 1 `accepted-residual` vs 47 `enforced`) | main | **Confirmed** | measured | None | High | It underclaims more than it overclaims |



