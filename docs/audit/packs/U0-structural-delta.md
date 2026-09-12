# Pack: U0 — structural delta

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| U0-F01 | high | doc-rot | `full-trace-index.md` cluster table lists 124 stems at 2026-07-11 | Live 196 `.py` paths keyed in `partition.json` | `git ls-files 'src/fanops/**/*.py' \| wc -l`; `docs/CODEMAPS/partition.json` |

## Evidence commands run

```
git ls-files 'src/fanops/**/*.py' | wc -l
rg '^src/fanops/' docs/CODEMAPS/partition.json | wc -l
python3 scripts/codemap_extract/ast_extract.py src > .codemap-cache/structural_index.json
```
