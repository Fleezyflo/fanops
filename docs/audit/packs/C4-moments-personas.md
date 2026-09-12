# Pack: C4 — moments, casting & personas

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C4-F01 | low | swallow | Hashtag store load failure previously unlogged | Log + narrow catch (partially done) | `persona_directives.persona_facts` |

## Evidence commands run

```
rg 'persona_facts' src/fanops/persona_directives.py
tests/test_persona_levers.py tests/test_persona_corpus.py
```
