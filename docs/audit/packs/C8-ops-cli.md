# Pack: C8 — ops, CLI & daemon

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C8-F01 | low | mis-report | Config parse failure → `half_live = False` in doctor | Surface degraded doctor row | `doctor.py` half_live fallback |

## Evidence commands run

```
rg 'half_live' src/fanops/doctor.py
tests/test_doctor.py tests/test_cli.py tests/test_daemon_keeper.py
```
