# Machine health contract

FanOps has **one machine-health constructor** and **one primary operator channel**. Secondary UIs are projections — not parallel owners. Process liveness and bring-up stay named separate.

| Role | Surface | Owner symbols |
|------|---------|----------------|
| **Primary** | `fanops doctor` (human + `--json`) | `cli.cmd_doctor` → `health_model.build_health_report` |
| Alias | `fanops health` | `cli.cmd_health` — same report + exit semantics |
| Readiness | `fanops init` / `fanops autopilot` | `init_flow.run_init`, `autopilot.autopilot` — exit tracks `report_is_healthy` |
| Projections | Studio Home strip, Go-Live readiness, daemon strip, `/metrics` | `project_strip_health`, `project_golive_readiness`, `project_daemon_strip`, `render_prometheus_metrics` |
| **Not machine health** | `/healthz` | Process up only (`studio.app.healthz`) |
| **Not machine health** | `fanops up` | Bring-up / infra READY (`cli.cmd_up`) |
| **Not a third healthy** | `fanops status` | Backlog counters; point at doctor for health |

**Severity** (`health_model.Severity`) is the public check contract. Soft-lie `ok`+`warn` without `severity` is banned in CI (`tests/test_machine_health_channel_ratchet.py`).

**CI ratchets (MOL-965 WP4):**

- `_ALLOWED_HEALTH_CONSTRUCTOR_FILES` — new callers of `build_health_report` / `doctor_report` fail CI
- Soft-lie baseline (`ok`+`warn` dict keys) — shrink-only
- Studio must not import `doctor_report`; `doctor_report` remains a thin wrapper over `build_health_report`
- `/healthz` must not call the machine-health constructor

`doctor_report` is a compatibility view (`as_dict()`), not a second health owner.
