# Pack: C9 — studio backend

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C9-F01 | medium | guard-gap | Wipe confirm route lacks server-side preview prerequisite | Enforce preview token/session | `app_routes_live.do_wipe_confirm` |

## Evidence commands run

```
rg 'do_wipe_confirm|do_wipe_preview' src/fanops/studio/app_routes_live.py
tests/test_studio_wipe.py
```
