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
| C2 ingest | C2-F01 through C2-F04 closed. C2-F03 closed: `vocals.isolate_vocals` logs fail-closed and raises `ToolchainMissingError` (mix not transcribed) | not re-audited |
| C3 clip production | C3-F01 closed (`test_ct_multi_resolve_one_real_ffmpeg`, one real 9fps ffmpeg on CT_MULTI). C3-F02 closed. C3-F03 closed: empty rc-0 temp does not publish over the prior mp4 | not re-audited |
| C4 moments/personas | C4-F01 through C4-F04 closed. Ingest and studio `regenerate_hook` share `_hook_script_mismatch` | not re-audited |
| C5 caption/hashtags | C5-F01 closed. C5-F02 closed: a miss-only scrape pass writes the sidecar. Hook prompt still says “SEE them” | not re-audited |
| C6 crosspost/publish | C6-F01 and C6-F02 closed. Dirty-tree pass found no C6-F03. Dry-run does not POST | not re-audited |
| C7 metrics/learning | C7-F01 and C7-F02 closed. `_read_queries` has no successor. `reconcile.py` was not edited | not re-audited |
| C8 ops/CLI/daemon | C8-F01 closed. OPS-SHA closed. `daemon.ensure` matches the doctor miss-or-mismatch rule | not re-audited |
| C9 studio backend | C9-F01 through C9-F03 closed. Imminent queued regenerate is refused before `render_moment` | not re-audited |
| C10 studio views | C10-F01 through C10-F04 closed. A failed card or feed swap re-renders the card with the error inside it | not re-audited |
| H-CI gates | closed — `tests/test_production_audit_gates.py` | not re-audited |
| H-DOCS drift | closed by `subsystem-traces/STALE.md` | not re-audited |
| H-INT invariants | resolved — operator edits of `reconcile.py` are C6; `partition.json` keeps C7. No retag | not re-audited |
| X-SEC | closed — SEC-IGPW, SEC-ENV | not re-audited |
| X-DEP | OPS-ZERNIO, OPS-POSTIZ, and OPS-QUEUE closed ([OPEN_GAPS.md](OPEN_GAPS.md)) | 2026-10-17 doctor |
| X-REL | OPS-SHA, REL-PROT, REL-CI-E2E, REL-UNDEPLOYED, and REL-CI-ARCH closed. REL-CI-ASR open (`HF_TOKEN` absent). ([OPEN_GAPS.md](OPEN_GAPS.md)) | fingerprint `430ece73` |
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
