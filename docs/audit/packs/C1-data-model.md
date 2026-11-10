# Pack: C1 — data model & persistence

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Entry symbols re-read 2026-10-18: `ledger_wipe.snapshot_is_restorable` calls `get_logger(cfg)` with outcome `snapshot_unrestorable`. `ledger.py` was not edited. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C1-F01 | closed | mis-surfacing | Unrestorable snapshot calls `get_logger` with outcome `snapshot_unrestorable`. Manifest write failure is `logger.warning` and the wipe proceeds. There is no `wipe_manifest_unwritten` outcome. | Surfaced `get_logger` on the unrestorable path | `ledger_wipe.snapshot_is_restorable`, `ledger_wipe.execute_wipe` |

## Evidence commands run

```
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/ledger_wipe.py  # snapshot_is_restorable, execute_wipe
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/ledger.py  # _migrate, _MIGRATIONS, _file_lock, Ledger.transaction
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/ledger_sqlite.py  # SqliteLedgerStore.lock -> _file_lock
rg 'snapshot_is_restorable|get_logger' src/fanops/ledger_wipe.py
rg -n 'SCHEMA_VERSION|_MIGRATIONS|_file_lock' src/fanops/ledger.py
git diff origin/main -- src  # empty at re-verify
tests/test_ledger_wipe_log.py
```
