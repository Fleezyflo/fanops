# FanOps production audit — master map (working tree on `d2cded31`)

Assessed 2026-10-17. The remediated tree sits on base `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e` (`audit/production-audit-2026-09`). Studio `GET http://127.0.0.1:8787/_fingerprint` returned `f65411d5806cce087a475261bf4c96c98f0bcf97` (pid 1225, generation `246c325b4dc81a3106fce99abe982f90`, `start_time` `2026-10-16T15:57:11.959355+00:00`). That SHA equals `origin/main`. This tree is not an ancestor of it. Component verdicts below are the working tree. The fingerprint column is the deploy SHA, not a second code audit.

## This assessment

| Artifact | Path |
|----------|------|
| Ship score | [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md) |
| Weakness register | [WEAKNESS_REGISTER.md](WEAKNESS_REGISTER.md) |
| Module index | [AUDIT_MODULE_INDEX.md](AUDIT_MODULE_INDEX.md) |
| Rollback | [docs/GOLIVE.md](../GOLIVE.md) section “Rollback / safety” |
| Protection registry | `.github/ci-control-registry.yml` `required_contexts` on this tree: `unit (fast, no toolchain)` |

## Component verdicts

| Component | This working tree | Fingerprint `f65411d5` |
|-----------|-------------------|------------------------|
| U0 structural delta | closed by stale mark | not re-audited; Studio serves this SHA |
| C1 data model | closed — `get_logger` on the wipe path | not re-audited |
| C2 ingest | closed — `vocals._demucs_env` | not re-audited |
| C3 clip production | closed — `framing._compute_track` | not re-audited |
| C4 moments/personas | closed — `persona_directives.persona_facts` | not re-audited |
| C5 caption/hashtags | closed — `hashtags.ship_from_lock`; `vet_hashtags` absent | not re-audited |
| C6 crosspost/publish | closed — `assert _JITTER_MAX < _STEP_MIN` | not re-audited |
| C7 metrics/learning | closed — `meta_graph._read_queries` absent | not re-audited |
| C8 ops/CLI/daemon | closed — `health_projectors.half_live_state` | not re-audited |
| C9 studio backend | closed — `actions_wipe.confirm_wipe` | not re-audited |
| C10 studio views | closed — `app_routes_home` passes `zero_post_clips` | not re-audited |
| H-CI gates | closed — `tests/test_production_audit_gates.py` | not re-audited |
| H-DOCS drift | closed by `subsystem-traces/STALE.md` | not re-audited |
| H-INT invariants | accepted — ARCH-001 vs C-map; `reconcile.py` is C7 in `partition.json` and C6 for edits | not re-audited |
| X-SEC | closed — SEC-IGPW, SEC-ENV | not re-audited |
| X-DEP | OPS-ZERNIO closed; OPS-POSTIZ and OPS-QUEUE deferred to the operator | not re-probed |
| X-REL | OPS-SHA closed on doctor and `daemon.ensure`; REL-PROT and REL-CI-E2E closed on the 2026-10-17 re-read; REL-UNDEPLOYED, REL-CI-ASR, REL-CI-ARCH deferred to the operator | this column is that deploy |
| X-DATA | closed — preview token and `snapshot_is_restorable` before wipe | not re-audited |

`partition.json` was not given X-keys. Publish owns `reconcile.py`. This pass did not edit it.

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

## Packs

| Pack | Path |
|------|------|
| U0 | [packs/U0-structural-delta.md](packs/U0-structural-delta.md) |
| C1 | [packs/C1-data-model.md](packs/C1-data-model.md) |
| C2 | [packs/C2-ingest.md](packs/C2-ingest.md) |
| C3 | [packs/C3-clip-production.md](packs/C3-clip-production.md) |
| C4 | [packs/C4-moments-personas.md](packs/C4-moments-personas.md) |
| C5 | [packs/C5-caption-hashtags.md](packs/C5-caption-hashtags.md) |
| C6 | [packs/C6-crosspost-publish.md](packs/C6-crosspost-publish.md) |
| C7 | [packs/C7-metrics-learning.md](packs/C7-metrics-learning.md) |
| C8 | [packs/C8-ops-cli.md](packs/C8-ops-cli.md) |
| C9 | [packs/C9-studio-backend.md](packs/C9-studio-backend.md) |
| C10 | [packs/C10-studio-views.md](packs/C10-studio-views.md) |
| H-CI | [packs/H-CI-gates.md](packs/H-CI-gates.md) |
| H-DOCS | [packs/H-DOCS-drift.md](packs/H-DOCS-drift.md) |
| H-INT | [packs/H-INT-invariants.md](packs/H-INT-invariants.md) |
| X-SEC | [packs/X-SEC-secrets.md](packs/X-SEC-secrets.md) |
| X-DEP | [packs/X-DEP-deps.md](packs/X-DEP-deps.md) |
| X-REL | [packs/X-REL-release.md](packs/X-REL-release.md) |
| X-DATA | [packs/X-DATA-ledger.md](packs/X-DATA-ledger.md) |
