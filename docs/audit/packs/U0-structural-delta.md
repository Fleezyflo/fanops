# Pack: U0 — structural delta

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Entry symbols re-read 2026-10-18: `partition.json` `live_py_count` 196, clusters C1 15, C2 14, C3 18, C4 10, C5 25, C6 32, C7 16, C8 25, C9 29, C10 12. No `indexed_cluster_stems` field. `git ls-files` Python count is 196. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Findings

| ID | Severity | Class | Observed | Required | Evidence | Status |
|----|----------|-------|----------|----------|----------|--------|
| U0-F01 | high | doc-rot | `full-trace-index.md` stays a frozen 2026-07-11 ledger. The 2026-10-17 banner on `d2cded31` records the live count 196 and marks that file explicitly stale. Cluster prose was left in place. | Explicit stale mark: that banner plus `docs/CODEMAPS/subsystem-traces/STALE.md`. `partition.json` stays the C-cluster map (`live_py_count` 196, 196 module keys). It has no `indexed_cluster_stems` field. The stale banner still says 124 indexed stems. 2026-10-18 counts: C7 16, C10 12 (sum 196). | Banner in `docs/CODEMAPS/full-trace-index.md`; `docs/CODEMAPS/subsystem-traces/STALE.md`; `partition.json` `live_py_count` 196. | closed by explicit stale mark |

## Evidence commands run

```
git rev-parse HEAD
git ls-files ':(glob)src/fanops/**/*.py' | wc -l
git ls-files src/fanops | grep -c '\.py$'
git log -1 --format='%H %ci' -- docs/CODEMAPS/subsystem-traces
```

`git rev-parse HEAD` is `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e`. Both live-count commands print 196. Bare `git ls-files 'src/fanops/**/*.py'` prints 58: without `:(glob)`, `**` does not match top-level `src/fanops/*.py`. Subsystem traces last commit is `f11d0af7` (2026-09-09).
