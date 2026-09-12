# Pack: H-DOCS — operator doc drift

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| H-DOCS-F01 | medium | doc-rot | Subsystem traces frozen 2026-07; +87 modules unaudited semantically | Regen traces or mark stale per cluster | `docs/CODEMAPS/subsystem-traces/` |

## Evidence commands run

```
git ls-files docs/CODEMAPS/subsystem-traces/
git ls-files 'src/fanops/**/*.py' | wc -l
```
