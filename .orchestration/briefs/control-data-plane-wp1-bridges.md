# WP1 — Classify every CP/DP bridge

**BID:** `CPDP-WP1-classify-bridges` · six tags only · no src/tests.

Tags: `cp.config` · `cp.process` · `cp.observe` · `dp.execute` · `infra.lifecycle` · `assay.dangerous`.

**OPEN seams (not CLOSED):** observe-mutate `doctor._hashtag_scrape_check` → `_persist_cooldown` (CPDP-03 OPEN until WP2; not a 7th tag); snapshot-write dual writers via `health.refresh_runtime_snapshots` (CPDP-02 OPEN); dual `ensure_up` bodies (CPDP-05 OPEN; WP3).

## Named bridges

| Symbol | Tag | Notes |
|---|---|---|
| `studio.actions_run.kick_prepare` | `dp.execute` | CP→DP |
| `studio.actions_run.run_ingest` | `dp.execute` | CP→DP; §6.4 run |
| `studio.actions_run.run_ingest_thirdparty` / `run_prepare` / `run_advance` / `run_pull` | `dp.execute` | CP→DP |
| `studio.actions.publish_now` | `dp.execute` | also hidden `postiz_lifecycle.ensure_up`; WP3 |
| `cli.cmd_cutover` | `assay.dangerous` | live assay |
| `studio.golive.validate_learning` | `assay.dangerous` | live assay |
| `learn_doctor.cmd_learn_doctor` | `assay.dangerous` | sidecar write |
| `health.ensure_up` | `infra.lifecycle` | dual body OPEN; WP3 |
| `postiz_lifecycle.ensure_up` | `infra.lifecycle` | dual body OPEN; WP3 |
| `cli.cmd_doctor` | `cp.observe` | primary |
| `doctor._hashtag_scrape_check` → `_persist_cooldown` | `cp.observe` | mutate-under-observe; CPDP-03 OPEN until WP2; not a 7th tag |
| `studio.app_routes_golive.do_golive_health` `/golive/health` | `cp.observe` | write-on-read via `health.refresh_runtime_snapshots` |
| `health.refresh_runtime_snapshots` | (seam) | snapshot strip writer (`deps_health.json` / `daemon_strip.json` / `strip_metrics.json`); also `health.ensure_up` + `cli` `--loop` (WP0); dual writers OPEN CPDP-02 |

## Remaining verbs (same six tags)

| Tag | Verbs |
|---|---|
| `cp.config` | `golive.go_live` / `go_dryrun` / `_dual_write`; `cli.cmd_init`; studio approve/schedule/caption/accounts; restore; wipe/purge execute |
| `cp.process` | `daemon.install` / `stop` / `ensure`; autopilot; pause/resume; `fanops studio` |
| `cp.observe` | `cli.cmd_health`; status; config introspect; `daemon.status` (no scrape persist) |
| `dp.execute` | cli `run` / `ingest` / `digest` / `respond` / `advance` / `pull` / `reconcile` / `track` / `gc` / `compose` |
| `infra.lifecycle` | `cli.cmd_up` |
| `assay.dangerous` | cutover *; `validate_learning`; learn_doctor; canary * |

No `planes.py` / `ownership.py` / tests. WP2 doctor freeze and WP3 collapse `ensure_up` are out of scope.
