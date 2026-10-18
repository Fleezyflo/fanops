# Pack: C1 — data model & persistence

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Entry symbols re-read 2026-10-18: `ledger_wipe.snapshot_is_restorable` calls `get_logger(cfg)` with outcome `snapshot_unrestorable`. `ledger.py` was not edited. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C1-F01 | closed | mis-surfacing | Unrestorable snapshot calls `get_logger` with outcome `snapshot_unrestorable`. Manifest write failure is `logger.warning` and the wipe proceeds. There is no `wipe_manifest_unwritten` outcome. | Surfaced `get_logger` on the unrestorable path | `ledger_wipe.snapshot_is_restorable`, `ledger_wipe.execute_wipe` |

## Evidence commands run

```
rg 'snapshot_is_restorable' src/fanops/ledger_wipe.py
rg 'get_logger' src/fanops/ledger_wipe.py
tests/test_ledger.py tests/test_studio_wipe.py
```
