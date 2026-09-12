# FanOps production audit — master map (2026-09-12)

## Partition authority

- **C-cluster (operator):** [docs/CODEMAPS/partition.json](../CODEMAPS/partition.json) — every `src/fanops/**/*.py` → `C1`…`C10`
- **S-subsystem (CI):** `.reports/architecture/derived/modules.json` via `python -m tools.arch ci` (ARCH-001)
- **Codemap index:** [docs/CODEMAPS/full-trace-index.md](../CODEMAPS/full-trace-index.md) — live partition section + data-flow spine (`:56-71`)

## Rollup

| Artifact | Path |
|----------|------|
| Ship score | [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md) |
| Weakness register | [WEAKNESS_REGISTER.md](WEAKNESS_REGISTER.md) |
| Anomaly ledger | [docs/CODEMAPS/anomalies.md](../CODEMAPS/anomalies.md) |
| CI gate module | `tests/test_production_audit_gates.py` |

## Packs (14)

| Pack | Path |
|------|------|
| U0 structural delta | [packs/U0-structural-delta.md](packs/U0-structural-delta.md) |
| C1 data model | [packs/C1-data-model.md](packs/C1-data-model.md) |
| C2 ingest | [packs/C2-ingest.md](packs/C2-ingest.md) |
| C3 clip production | [packs/C3-clip-production.md](packs/C3-clip-production.md) |
| C4 moments/personas | [packs/C4-moments-personas.md](packs/C4-moments-personas.md) |
| C5 caption/hashtags | [packs/C5-caption-hashtags.md](packs/C5-caption-hashtags.md) |
| C6 crosspost/publish | [packs/C6-crosspost-publish.md](packs/C6-crosspost-publish.md) |
| C7 metrics/learning | [packs/C7-metrics-learning.md](packs/C7-metrics-learning.md) |
| C8 ops/CLI/daemon | [packs/C8-ops-cli.md](packs/C8-ops-cli.md) |
| C9 studio backend | [packs/C9-studio-backend.md](packs/C9-studio-backend.md) |
| C10 studio views | [packs/C10-studio-views.md](packs/C10-studio-views.md) |
| H-CI gates | [packs/H-CI-gates.md](packs/H-CI-gates.md) |
| H-DOCS drift | [packs/H-DOCS-drift.md](packs/H-DOCS-drift.md) |
| H-INT invariants | [packs/H-INT-invariants.md](packs/H-INT-invariants.md) |

## Operator docs inventoried (15 root `docs/*.md`)

- docs/CONFIG.md
- docs/CONTROL-FILES.md
- docs/ENFORCEMENT.md
- docs/ENGINEERING_STANDARDS.md
- docs/FLAGS.md
- docs/GOLIVE.md
- docs/INSTAGRAM_CONNECT.md
- docs/LEVER-THRESHOLDS.md
- docs/LEVERS.md
- docs/MACHINE_HEALTH.md
- docs/META_CREDS_OPS.md
- docs/POSTIZ_OPS.md
- docs/POSTIZ_SETUP.md
- docs/RUNBOOK.md
- docs/YOUTUBE_CONNECT.md
