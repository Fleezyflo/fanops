# WP0 — CPDP inventory freeze

**BID:** `CPDP-WP0-inventory-freeze` · **HEAD:** `4c56edee` (2026-08-13) · **no behavior change**

| Prereq | Evidence |
|---|---|
| S1 config-truth `#962` | DONE — merge `70a4329d` “unify env truth plane behind Config façade” |
| S2 machine-health `#971` | DONE — merge `4c56edee` “MOL-965: machine-health contract (WP1–WP4)” |

Landed health symbols present: `health_model.HealthReport`, `health_model.build_health_report`, `probe_policy`, `health.SnapshotFreshness`. `Config.auto_adopt` remains a getenv façade property (not a bypass site).

## CPDP-01..14 vs tree

| ID | Status | Symbol evidence |
|---|---|---|
| CPDP-01 | **CLOSED** | `Config` façade + `settings._REGISTRY` / `env_registry()` landed (#962) |
| CPDP-02 | **PARTIAL** | `build_health_report` + `SnapshotFreshness` landed; still `refresh_runtime_snapshots` write-on-read + dual `ensure_up` (WP3+) |
| CPDP-03 | **OPEN** | `doctor._hashtag_scrape_check` → `fanops_hashtags._persist_cooldown` |
| CPDP-04 | **OPEN** | `doctor` module claims READ-ONLY; `learn_doctor` still `controlio.write_json_atomic` |
| CPDP-05 | **OPEN** | Dual policy bodies: `health.ensure_up` and `postiz_lifecycle.ensure_up` |
| CPDP-06 | **OPEN** | `post.run.publish_due` / publish-now → `postiz_lifecycle.ensure_up` |
| CPDP-07 | **OPEN** | `studio.actions_run.kick_prepare`; `studio.golive.validate_learning` |
| CPDP-08 | **OPEN** | `daemon.root_divergence`; `FANOPS_ROOT` shell-only by design |
| CPDP-09 | **OPEN** | `studio.golive` dual-write vs daemon per-tick dotenv / one-shot CLI |
| CPDP-10 | **OPEN** | `health_model.daemon_liveness_check` → `doctor._daemon_liveness_check` |
| CPDP-11 | **OPEN** | `docs/CODEMAPS/.../C8_ops_cli_daemon.md` still “doctor pure” / old `postiz_health` |
| CPDP-12 | **CLOSED** | `Settings` docstring: Config = live façade; no “Built fresh per Config()” folklore |
| CPDP-13 | **OPEN** | `autopilot.set_env_var` atomic `.env.tmp` + `os.replace`; **no flock** |
| CPDP-14 | **OPEN** | No arch/test affect-graph ratchet for observe-must-not-write yet |

## Census — `health.refresh_runtime_snapshots` callers

Verified `rg 'refresh_runtime_snapshots(' src/fanops` → three call sites (plus def):

1. `health.ensure_up` (post bring-up refresh)
2. `studio.app_routes_golive` (`/golive/health` — write-on-read)
3. `cli` daemon `--loop` tick (post-pass strip refresh)

## Census — product-knob getenv / environ bypasses

`rg 'os.getenv(' src/fanops --glob '!config.py' --glob '!settings.py'` plus known `os.environ` slug/process keys:

| Site | Key / pattern | Class |
|---|---|---|
| `doctor` / `daemon` | `FANOPS_POSTIZ_ONDEMAND` via `os.getenv` | **bypass** (registered bootstrap; still raw getenv) |
| `studio.app` | `FANOPS_STUDIO_GENERATION` via `os.environ.get` | **infra** (process generation stamp) |
| `ig_hashtag_scrape` | per-user slug keys via `os.environ` membership/subscript | **bypass** (dynamic secret key; not `cfg.*`) |

No other product-knob `os.getenv` hits outside `config.py` / `settings.py`.

## Explicit

This WP freezes inventory only — **zero** src/tests edits; no mutation of `health.ensure_up`, `postiz_lifecycle.ensure_up`, `doctor._hashtag_scrape_check` / `_persist_cooldown`, `Config.auto_adopt`, or `health_model.build_health_report`.

## PROOF run (worktree)

| Check | Exit |
|---|---|
| `test -f .orchestration/briefs/control-data-plane-wp0-inventory.md` | 0 |
| `git diff --name-only -- src tests` | 0 (empty) |
| `rg -n 'def refresh_runtime_snapshots' src/fanops/health.py` | 0 (1 hit) |
| `rg -n 'refresh_runtime_snapshots(' src/fanops` | 0 (def + 3 callers) |
| `rg -n 'def ensure_up' src/fanops/{health,postiz_lifecycle}.py` | 0 (2 defs) |
| `rg -n '_persist_cooldown' src/fanops/doctor.py` | 0 (import + call) |
| `rg -n 'os.getenv(' src/fanops --glob '!config.py' --glob '!settings.py'` | 0 (doctor/daemon + scrape comment) |

## Post-land update (WP2–WP5 on this branch)

| ID | Now | Evidence |
|---|---|---|
| CPDP-03 | **CLOSED** | `doctor._hashtag_scrape_check` no longer calls `_persist_cooldown` / `_freeze_for` |
| CPDP-05 | **CLOSED** | sole `FunctionDef ensure_up` = `postiz_lifecycle.ensure_up`; `health.ensure_up` deleted |
| CPDP-06 | **PARTIAL** | publish still *requests* `postiz_lifecycle.ensure_up` (intentional thin call, not second policy) |
| CPDP-11 | **CLOSED** | C8 purged; doctor/`learn_doctor` plane tags accurate |
| CPDP-14 | **CLOSED** | AST ratchets in `test_doctor` / `test_health` / `test_postiz_lifecycle` |
| CPDP-02 | **PARTIAL** | strip writer named; `/golive/health` + `cli --loop` still write-on-read callers |
| CPDP-04 | **OPEN** | `learn_doctor` sidecar write remains (`assay.dangerous`) |

`refresh_runtime_snapshots` callers now: `do_golive_health`, `cli` `--loop` only (no `health.ensure_up`).
