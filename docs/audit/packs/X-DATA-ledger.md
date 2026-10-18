# Pack: X-DATA — ledger immutability

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Finding status below is unchanged. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Findings

| ID | Status | Owner | Evidence |
|----|--------|-------|----------|
| X-DATA-F01 | closed | code | `AGENTS.md` forbids wipe-as-repair. `actions_wipe.confirm_wipe` refuses a missing or stale preview token before `execute_wipe`. `snapshot_is_restorable` is required. No `scripts/` path resets the ledger. The audit did not write the ledger. |

## Evidence commands run

```
rg -n "wipe|reset" scripts --glob '*.py'
rg -n "def confirm_wipe|preview_token" src/fanops/studio/actions_wipe.py
```
