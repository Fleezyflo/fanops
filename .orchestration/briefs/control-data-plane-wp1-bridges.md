# WP1 — Classify every CP/DP bridge

**BID:** `CPDP-WP1-classify-bridges` · six tags only · no src/tests.

Tags: `cp.config` · `cp.process` · `cp.observe` · `dp.execute` · `infra.lifecycle` · `assay.dangerous`.

**Post-WP2/WP3:** CPDP-03 CLOSED (doctor no persist/freeze); CPDP-05 CLOSED (sole `postiz_lifecycle.ensure_up`). Still OPEN: CPDP-02 write-on-read callers of `health.refresh_runtime_snapshots` (`do_golive_health`, `cli --loop`).

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
| `postiz_lifecycle.ensure_up` | `infra.lifecycle` | sole bring-up policy (WP3 CLOSED) |
| `cli.cmd_doctor` | `cp.observe` | primary |
| `doctor._hashtag_scrape_check` | `cp.observe` | report-only; CPDP-03 CLOSED |
| `studio.app_routes_golive.do_golive_health` `/golive/health` | `cp.observe` | write-on-read via `health.refresh_runtime_snapshots` |
| `health.refresh_runtime_snapshots` | (seam) | named strip writer; callers `do_golive_health` + `cli --loop` (CPDP-02 PARTIAL) |

## Remaining verbs (same six tags)

| Tag | Verbs |
|---|---|
| `cp.config` | `golive.go_live` / `go_dryrun` / `_dual_write`; `cli.cmd_init`; studio approve/schedule/caption/accounts; restore; wipe/purge execute |
| `cp.process` | `daemon.install` / `stop` / `ensure`; autopilot; pause/resume; `fanops studio` |
| `cp.observe` | `cli.cmd_health`; status; config introspect; `daemon.status` (no scrape persist) |
| `dp.execute` | cli `run` / `ingest` / `digest` / `respond` / `advance` / `pull` / `reconcile` / `track` / `gc` / `compose` |
| `infra.lifecycle` | `cli.cmd_up` |
| `assay.dangerous` | cutover *; `validate_learning`; learn_doctor; canary * |

No `planes.py` / `ownership.py`. WP2/WP3 product land is on this branch; this file is the bridge catalog.
