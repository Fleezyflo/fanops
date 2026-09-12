# Pack: C2 — ingest & source acquisition

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C2-F01 | low | swallow | `_demucs_env` catches bare `Exception` around certifi import | Narrow to `ImportError` or log | `vocals.py` `_demucs_env` |

## Evidence commands run

```
rg 'except Exception' src/fanops/vocals.py
git ls-files 'src/fanops/ingest.py'
tests/test_ingest.py tests/test_transcribe.py
```
