# Pack: U0 — structural delta

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Entry symbols re-read 2026-10-18: `partition.json` `live_py_count` 196, clusters C1 15, C2 14, C3 18, C4 10, C5 25, C6 32, C7 16, C8 25, C9 29, C10 12. No `indexed_cluster_stems` field. `git ls-files` Python count is 196. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Findings

| ID | Severity | Class | Observed | Required | Evidence | Status |
|----|----------|-------|----------|----------|----------|--------|
| U0-F01 | high | doc-rot | `full-trace-index.md` stays a frozen 2026-07-11 ledger. The 2026-10-17 banner on `d2cded31` records the live count 196 and marks that file explicitly stale. Cluster prose was left in place. | Explicit stale mark: that banner plus `docs/CODEMAPS/subsystem-traces/STALE.md`. `partition.json` stays the C-cluster map (`live_py_count` 196, 196 module keys). It has no `indexed_cluster_stems` field. The stale banner still says 124 indexed stems. 2026-10-18 counts: C7 16, C10 12 (sum 196). | Banner in `docs/CODEMAPS/full-trace-index.md`; `docs/CODEMAPS/subsystem-traces/STALE.md`; `partition.json` `live_py_count` 196. | closed by explicit stale mark |

## Evidence commands run

```
git rev-parse 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9
git diff 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9 -- src
git ls-files ':(glob)src/fanops/**/*.py' | wc -l
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:docs/CODEMAPS/partition.json  # live_py_count, modules len, cluster counts
python3  # partition modules keys vs git ls-files set; cluster (N) vs partition; stems named in full-trace-index
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:docs/CODEMAPS/full-trace-index.md | head -5
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:docs/CODEMAPS/subsystem-traces/STALE.md
git log -1 --format='%H %ci' -- docs/CODEMAPS/subsystem-traces
```

Subject `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` resolves cleanly; `git diff 430ece73 -- src` is empty. Live Python count is 196 (`:(glob)`); `partition.json` `live_py_count` 196 with 196 `modules` keys matching that set exactly. Frozen cluster table `(N)` counts match partition per cluster (C1 15 … C10 12; sum 196); zero partition paths unnamed in `full-trace-index.md`. No `indexed_cluster_stems` key in `partition.json`. Stale banner plus `subsystem-traces/STALE.md` present on subject SHA. Subsystem traces last commit is `f11d0af7` (2026-09-09).
