# Pack: C10 — studio views

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C10-F01 | low | doc-rot | Anomalies still cite `zero_post_clips` unwired from routes | Update ledger; code already passes kwarg | `app_routes_home.py`; `test_studio_gaps_closure.py` |

## Evidence commands run

```
rg 'zero_post_clips' src/fanops/studio/
tests/test_studio_gaps_closure.py tests/test_studio_views.py
```
