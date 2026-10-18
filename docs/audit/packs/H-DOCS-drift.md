# Pack: H-DOCS — operator doc drift

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Finding status below is unchanged. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| H-DOCS-F01 | medium | doc-rot | closed by explicit stale mark | Traces last touched 2026-09-09 (`f11d0af7`). `subsystem-traces/STALE.md` records 196 live modules vs 124 indexed stems on `d2cded31`. Not regenerated | `docs/CODEMAPS/subsystem-traces/STALE.md` |

## Evidence commands run

```
git ls-files docs/CODEMAPS/subsystem-traces/
git ls-files 'src/fanops/**/*.py' | wc -l
```
