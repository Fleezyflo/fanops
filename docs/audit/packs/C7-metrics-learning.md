# Pack: C7 — metrics, reconcile & learning

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C7-F01 | medium | silent-fail | Corrupt hashtag budget file → `None` with no log | Log like `insights_blocked_signal` | `meta_graph._read_queries` |

## Evidence commands run

```
rg '_read_queries' src/fanops/meta_graph.py
tests/test_metrics.py tests/test_reconcile.py
```
