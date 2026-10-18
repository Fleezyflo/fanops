# FanOps production audit — master map

Assessed 2026-10-18. Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. Studio `GET http://127.0.0.1:8787/_fingerprint` returned that SHA (pid 65307, generation `246c325b4dc81a3106fce99abe982f90`, `start_time` `2026-10-17T22:25:22.259438+00:00`). The pump heartbeat and the live-checkout disk are the same SHA. Doctor’s disk SHA on this branch is `bd0a88ac5ca1aa43c52b346fbec8975155789ae2` (`daemon._version_signal`, this checkout HEAD). Component verdicts are the subject. The fingerprint column is that deploy SHA.

## This assessment

| Artifact | Path |
|----------|------|
| Ship score | [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md) |
| Weakness register | [WEAKNESS_REGISTER.md](WEAKNESS_REGISTER.md) |
| Module index | [AUDIT_MODULE_INDEX.md](AUDIT_MODULE_INDEX.md) |
| Rollback | [docs/GOLIVE.md](../GOLIVE.md) section “Rollback / safety” |
| Protection registry | `.github/ci-control-registry.yml` `required_contexts` on this tree: `unit (fast, no toolchain)` |

## Component verdicts

| Component | `origin/main` `430ece73` | Fingerprint `430ece73` |
|-----------|------------------------------|------------------------|
| U0 structural delta | closed by stale mark. Live count 196. C7 16, C10 12 | same SHA; symbols re-checked 2026-10-18 |
| C1 data model | closed — `get_logger` outcome `snapshot_unrestorable`. Manifest failure is `logger.warning` | same SHA |
| C2 ingest | C2-F01 through C2-F04 closed. `vocals.isolate_vocals` logs fail-closed and raises `ToolchainMissingError` | same SHA |
| C3 clip production | C3-F01 closed (`test_ct_multi_resolve_one_real_ffmpeg`). C3-F02 closed. C3-F03 closed | same SHA |
| C4 moments/personas | C4-F01 through C4-F04 closed. `regenerate_hook` uses `_hook_script_mismatch` | same SHA |
| C5 caption/hashtags | C5-F01 closed. C5-F02 closed. Hook prompt still says “SEE them” | same SHA |
| C6 crosspost/publish | C6-F01 and C6-F02 closed. No C6-F03. Dry-run does not POST | same SHA |
| C7 metrics/learning | C7-F01 and C7-F02 closed. `_read_queries` has no successor. `reconcile.py` was not edited | same SHA |
| C8 ops/CLI/daemon | C8-F01 closed. OPS-SHA closed. Doctor exit 1 is this branch’s disk versus the pump | pump and Studio equal this SHA |
| C9 studio backend | C9-F01 through C9-F03 closed. Imminent queued regenerate is refused before `render_moment` | same SHA |
| C10 studio views | C10-F01 through C10-F04 closed. A failed card or feed swap re-renders the card with the error | same SHA |
| H-CI gates | closed — `tests/test_production_audit_gates.py` | same SHA |
| H-DOCS drift | closed by `subsystem-traces/STALE.md` | same SHA |
| H-INT invariants | resolved — operator edits of `reconcile.py` are C6; `partition.json` keeps C7. No retag | same SHA |
| X-SEC | closed — SEC-IGPW, SEC-ENV. 69 cap does not bind | same SHA |
| X-DEP | OPS-ZERNIO, OPS-POSTIZ, and OPS-QUEUE closed ([OPEN_GAPS.md](OPEN_GAPS.md)) | 2026-10-18 doctor: 0 drainable, 83 parked, 837 queued |
| X-REL | OPS-SHA, REL-PROT, REL-CI-E2E, REL-UNDEPLOYED, and REL-CI-ARCH closed. REL-CI-ASR resolved (scheduled CI hygiene, not a product gap). ([OPEN_GAPS.md](OPEN_GAPS.md)) | fingerprint equals this SHA. Scheduled e2e and nightly transcripts are still on `f65411d5`. `post_5854bb63fefb` is `needs_reconcile` with no `public_url` |
| X-DATA | closed — preview token and `snapshot_is_restorable` before wipe | same SHA |

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
