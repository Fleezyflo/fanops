# Pack: C6 — crosspost, publish & post

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C6-F01 | low | invariant | `_JITTER_MAX < _STEP_MIN` enforced by comment only | Module-level assert or shared constant test | `crosspost.py` jitter constants |

## Evidence commands run

```
rg '_JITTER_MAX|_STEP_MIN' src/fanops/crosspost.py
tests/test_post_run.py tests/test_responder.py tests/test_dryrun_boundary.py
```
