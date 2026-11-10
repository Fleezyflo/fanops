# Pack: H-DOCS — operator doc drift

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Entry symbols re-read 2026-10-18: `docs/CODEMAPS/subsystem-traces/STALE.md`. Traces were not regenerated. Live count 196, indexed stems 124. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| H-DOCS-F01 | medium | doc-rot | closed by explicit stale mark | Traces last touched 2026-09-09 (`f11d0af7`). `subsystem-traces/STALE.md` records 196 live modules vs 124 indexed stems on `430ece73`. C1–C10 trace files exist; semantic layer not regenerated | `docs/CODEMAPS/subsystem-traces/STALE.md`; `docs/CODEMAPS/partition.json` `live_py_count`; `docs/CODEMAPS/full-trace-index.md` cluster table |

## Evidence commands run

```
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:docs/CODEMAPS/subsystem-traces/STALE.md
git ls-tree -r --name-only 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9 docs/CODEMAPS/subsystem-traces/
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:docs/CODEMAPS/partition.json  # live_py_count 196, 10 clusters, trace paths
git ls-tree -r --name-only 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9 src/fanops | rg '\.py$' | wc -l
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:README.md | rg 'docs/audit/MASTER_MAP|partition.json|PRODUCTION_SCORE'
```
