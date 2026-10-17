# Pack: H-DOCS — operator doc drift

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| H-DOCS-F01 | medium | doc-rot | closed by explicit stale mark | Traces last touched 2026-09-09 (`f11d0af7`). `subsystem-traces/STALE.md` records 196 live modules vs 124 indexed stems on `d2cded31`. Not regenerated | `docs/CODEMAPS/subsystem-traces/STALE.md` |

## Evidence commands run

```
git ls-files docs/CODEMAPS/subsystem-traces/
git ls-files 'src/fanops/**/*.py' | wc -l
```
