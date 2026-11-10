# Pack: X-DATA — ledger immutability

## Findings

| ID | Status | Owner | Evidence |
|----|--------|-------|----------|
| X-DATA-F01 | closed | code | `AGENTS.md` forbids wipe-as-repair. `actions_wipe.confirm_wipe` refuses a missing or stale preview token before `execute_wipe`. `snapshot_is_restorable` is required. No `scripts/` path resets the ledger. The audit did not write the ledger. |

## Evidence commands run

```
rg -n "wipe|reset" scripts --glob '*.py'
rg -n "def confirm_wipe|preview_token" src/fanops/studio/actions_wipe.py
```
