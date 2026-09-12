# Pack: C1 — data model & persistence

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C1-F01 | medium | mis-surfacing | Wipe restorability gate logs via stdlib `logging` | Surfaced `get_logger` on wipe path | `ledger_wipe.snapshot_is_restorable`; `anomalies.md` C1 note |

## Evidence commands run

```
rg 'snapshot_is_restorable' src/fanops/ledger_wipe.py
rg 'get_logger' src/fanops/ledger_wipe.py
tests/test_ledger.py tests/test_studio_wipe.py
```
