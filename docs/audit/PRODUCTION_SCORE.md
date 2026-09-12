# FanOps production score (2026-09-12)

**Score: 72/100 — launchable with caveats**

Core safety invariants (no-auto-publish, dryrun/live boundary, ledger cascade protection, bias amplify-only) are enforced in code and covered by CI/arch gates per packs C6, C7, C8 and `tools/arch` INVARIANT_AUDIT. Operator-facing codemap prose remains frozen while the tree grew 109→196 Python modules; partition.json closes the C-cluster visibility gap but semantic traces lag.

## Blockers

None for dryrun/local operator loop. Live publish requires Postiz/Zernio credentials, `[asr]` for transcript path, and explicit go-live — documented in CONFIG install extras and GOLIVE.

## High-value fixes

- C1-F01: surface wipe restorability failures on `run.log` via `get_logger`.
- C7-F01: log corrupt hashtag-budget reads in `meta_graph._read_queries`.
- C9-F01: server-side enforce wipe preview before confirm (UI-only today).
- H-DOCS-F01: regen or annotate subsystem traces for modules added since 2026-07.

## Evidence checked

- `git ls-files 'src/fanops/**/*.py'` → 196 modules; `partition.json` keys match (gate test).
- Pack spot reads + anomalies re-verification 2026-09-12 section.
- ARCH-001 totality via `.reports/architecture/derived/modules.json` (S-subsystem, separate authority).
- README/docs map row + MASTER_MAP inventory.

## Evidence missing

- Full E2E live publish on operator hardware this pass (scheduled CI e2e only).
- Per-cluster Sonnet trace regen for all 196 modules.

## Next action

Land audit branch; track open register rows in lane PRs; keep partition.json updated when adding `src/fanops/**/*.py`.

**Score caps applied in prose:** no auth-on-sensitive-data cap triggered (Studio/session model unchanged). No cap-69 secrets exposure found in audit packs. PR-check greenness assumed at merge time (cap-84 not applied to this doc-only score).
