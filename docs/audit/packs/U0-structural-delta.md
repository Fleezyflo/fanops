# Pack: U0 — structural delta

## Findings

| ID | Severity | Class | Observed | Required | Evidence | Status |
|----|----------|-------|----------|----------|----------|--------|
| U0-F01 | high | doc-rot | `full-trace-index.md` stays a frozen 2026-07-11 ledger. The 2026-10-17 banner on `d2cded31` records the live count 196 and marks that file explicitly stale. Cluster prose was left in place. | Explicit stale mark: that banner plus `docs/CODEMAPS/subsystem-traces/STALE.md`. `partition.json` stays the C-cluster map (`indexed_cluster_stems` 124, `live_py_count` 196, 196 module keys). | Banner in `docs/CODEMAPS/full-trace-index.md`; `docs/CODEMAPS/subsystem-traces/STALE.md`; `docs/CODEMAPS/partition.json` counts 196 / 124. | closed by explicit stale mark |

## Evidence commands run

```
git rev-parse HEAD
git ls-files ':(glob)src/fanops/**/*.py' | wc -l
git ls-files src/fanops | grep -c '\.py$'
git log -1 --format='%H %ci' -- docs/CODEMAPS/subsystem-traces
```

`git rev-parse HEAD` is `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e`. Both live-count commands print 196. Bare `git ls-files 'src/fanops/**/*.py'` prints 58: without `:(glob)`, `**` does not match top-level `src/fanops/*.py`. Subsystem traces last commit is `f11d0af7` (2026-09-09).
