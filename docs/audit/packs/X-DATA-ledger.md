# Pack: X-DATA — ledger immutability

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Entry symbols re-read 2026-10-18: `actions_wipe.confirm_wipe` refuses an empty or stale preview token before `execute_wipe`. `ledger_wipe.snapshot_is_restorable` is required in `execute_wipe`. `cli.cmd_wipe` snapshots then calls `execute_wipe`; `cli.cmd_restore` calls `Ledger.restore_snapshot`. No `scripts/*.py` path resets ledger state. The ledger was not wiped. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Findings

| ID | Severity | Class | Observed | Required | Evidence | Status |
|----|----------|-------|----------|----------|----------|--------|
| X-DATA-F01 | high | control | On subject SHA, `AGENTS.md` forbids wipe-as-repair for agents. Operator wipe is gated: Studio `confirm_wipe` requires typed `CONFIRM_WORD`, a fresh preview token (MOL-71), a mandatory pre-wipe snapshot, `snapshot_is_restorable`, then `execute_wipe` (which re-checks `confirmed` and restorable snapshot). CLI `cmd_wipe` defaults to read-only preview; execute path snapshots then calls `execute_wipe` with sentence flags. `cmd_restore` exposes `Ledger.restore_snapshot` for rollback. No Python under `scripts/` mutates or resets the ledger. | Agents must not treat wipe as repair; any destructive fall-away requires operator gates and a verified restorable snapshot before mutation. | `AGENTS.md` guardrail "Never wipe or reset the ledger." `studio/actions_wipe.confirm_wipe` (preview token, snapshot, `snapshot_is_restorable`, `execute_wipe`). `ledger_wipe.snapshot_is_restorable`, `ledger_wipe.execute_wipe` (`WipeNotConfirmed`, `SnapshotRequired`). `cli.cmd_wipe`, `cli.cmd_restore`. `rg -n 'wipe|reset' scripts --glob '*.py'` (no ledger reset). `ledger.py` not edited. | closed |

## Evidence commands run

```
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:AGENTS.md
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/ledger_wipe.py
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/studio/actions_wipe.py
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/cli.py
rg -n "def snapshot_is_restorable|def execute_wipe|def confirm_wipe|def cmd_wipe|def cmd_restore" \
  <(git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/ledger_wipe.py) \
  <(git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/studio/actions_wipe.py) \
  <(git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/cli.py)
rg -n "wipe|reset" scripts --glob '*.py' -i
git diff origin/main -- src
```
