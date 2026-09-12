# Pack: C3 — clip production & framing

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C3-F01 | low | cost | Two-speaker clips run two ffmpeg grid extractions | Documented bounded cost; optional fusion later | `framing._compute_track`; anomalies C3 cost note |

## Evidence commands run

```
rg '_compute_track' src/fanops/framing.py
tests/test_clip.py tests/test_smart_framing.py
```
