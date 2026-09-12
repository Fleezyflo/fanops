# Pack: C5 — caption, hooks & hashtags

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C5-F01 | low | design-note | Reserved hashtag floor uses cap window not full kept list | Keep inline comment when editing | `hashtags.py` `vet_hashtags` |

## Evidence commands run

```
rg 'vet_hashtags' src/fanops/hashtags.py
tests/test_hashtags.py tests/test_fanops_hashtags.py
```
