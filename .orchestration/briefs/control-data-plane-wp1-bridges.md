# WP1 — Classify every CP/DP bridge

Tags: `cp.config` · `cp.process` · `cp.observe` · `dp.execute` · `infra.lifecycle` · `assay.dangerous`.

**Truth on main (`b8788be8`):** CPDP-02/03/04/05/11/14 CLOSED. CPDP-06 KEEP (on-demand `ensure_up` calls). Still OPEN: CPDP-07/08/09/10/13.

## Named bridges

| Symbol | Tag | Notes |
|---|---|---|
| `studio.actions_run.kick_prepare` | `dp.execute` | CP→DP |
| `studio.actions_run.run_ingest` | `dp.execute` | CP→DP |
| `studio.actions_run.run_ingest_thirdparty` / `run_prepare` / `run_advance` / `run_pull` | `dp.execute` | CP→DP |
| `studio.actions.publish_now` | `dp.execute` | may request `postiz_lifecycle.ensure_up` (CPDP-06 KEEP) |
| `cli.cmd_cutover` | `assay.dangerous` | live assay |
| `studio.golive.validate_learning` | `assay.dangerous` | live assay |
| `learn_doctor.cmd_learn_doctor` | `assay.dangerous` | print/verdict only; no sidecar write (`#975`) |
| `postiz_lifecycle.ensure_up` | `infra.lifecycle` | sole bring-up policy |
| `cli.cmd_doctor` | `cp.observe` | primary |
| `doctor._hashtag_scrape_check` | `cp.observe` | report-only |
| `studio.app_routes_golive.do_golive_health` `/golive/health` | `cp.observe` | read/live observe; no strip persist (`#973`) |
| `health.refresh_runtime_snapshots` | (seam) | strip writer; sole Call `cli --loop` |

## Remaining verbs (same six tags)

| Tag | Verbs |
|---|---|
| `cp.config` | `golive.go_live` / `go_dryrun` / `_dual_write`; `cli.cmd_init`; studio approve/schedule/caption/accounts; restore; wipe/purge execute |
| `cp.process` | `daemon.install` / `stop` / `ensure`; autopilot; pause/resume; `fanops studio` |
| `cp.observe` | `cli.cmd_health`; status; config introspect; `daemon.status` |
| `dp.execute` | cli `run` / `ingest` / `digest` / `respond` / `advance` / `pull` / `reconcile` / `track` / `gc` / `compose` |
| `infra.lifecycle` | `cli.cmd_up`; publish/reconcile on-demand `ensure_up` (KEEP) |
| `assay.dangerous` | cutover *; `validate_learning`; learn_doctor; canary * |

No `planes.py` / `ownership.py`.
