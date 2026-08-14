# WP0 — CPDP inventory (truth on main)

**HEAD:** `b8788be8` (2026-08-13) · post `#972` / `#973` / `#974` / `#975`

| Prereq | Evidence |
|---|---|
| S1 config-truth `#962` | **LANDED** |
| S2 machine-health `#971` | **LANDED** |

## CPDP-01..14 vs tree

| ID | Status | Evidence |
|---|---|---|
| CPDP-01 | **CLOSED** | Config façade + registry (`#962`) |
| CPDP-02 | **CLOSED** | Golive health no longer calls `refresh_runtime_snapshots` (`#973`). Sole Call: `cli --loop`. Def: `health.refresh_runtime_snapshots` |
| CPDP-03 | **CLOSED** | `doctor._hashtag_scrape_check` observe-only (`#972`) |
| CPDP-04 | **CLOSED** | `learn_doctor` sidecar write deleted (`#975`) |
| CPDP-05 | **CLOSED** | Sole `def ensure_up` = `postiz_lifecycle.ensure_up` (`#972`) |
| CPDP-06 | **KEEP** | Publish/reconcile/studio still *call* `ensure_up` — intentional on-demand local stack |
| CPDP-07 | **OPEN** | Studio CP→DP verbs (`kick_prepare`, ingest/prepare/advance/pull, `publish_now`, `validate_learning`) — catalogued, not ownership-fixed |
| CPDP-08 | **CLOSED** | `cli.main` fail-closed on `root_divergence` (exit 2) except `daemon status`; `FANOPS_ROOT` shell-only unchanged |
| CPDP-09 | **OPEN** | Studio Go-Live dual-write vs daemon per-tick dotenv / one-shot CLI |
| CPDP-10 | **OPEN** | `health_model.daemon_liveness_check` ↔ `doctor._daemon_liveness_check` alias/dual path |
| CPDP-11 | **CLOSED** | C8 folklore purged (`#972`) |
| CPDP-12 | **CLOSED** | Settings docstring truth (`#962`) |
| CPDP-13 | **OPEN** | `autopilot.set_env_var` atomic write, no flock |
| CPDP-14 | **CLOSED** | AST ratchets for doctor persist / one `ensure_up` / strip readers (`#972`–`#973`) |

## Census — `refresh_runtime_snapshots`

- Def: `health.refresh_runtime_snapshots`
- Call: `cli` `--loop` only

## Census — product-knob getenv bypasses (unchanged note)

Raw getenv outside config/settings still includes doctor/daemon `FANOPS_POSTIZ_ONDEMAND`, studio generation stamp, dynamic scrape slug keys — not closed by CPDP wrap-up.
