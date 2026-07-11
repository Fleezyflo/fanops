# CI duration SLO

Instrumentation from **MOL-452** measures pytest wall-clock on every run. **MOL-460** enforces a
blocking SLO gate on the unit job.

## What we measure

| Job step | Field in `ci-timing.json` | Notes |
|----------|---------------------------|-------|
| unit pytest | `unit_pytest_s`, `test_count`, `xdist` | `-n auto` (xdist) since PR #515 |
| e2e integration | `e2e_integration_s` | real ffmpeg + whisper toolchain |
| e2e slow | `e2e_slow_s` | hermetic cross-face proofs (`@pytest.mark.slow`) |

Post-#515 baselines are **xdist wall-clock**, not serial. Do not compare pre-#515 timings to
current runs.

## Baselines (main, post-#515, Jul 2026)

Measured over 14 `main` runs after xdist landed (PR #515):

| Metric | Median | p95 | Range |
|--------|--------|-----|-------|
| `unit_pytest_s` | 91s | 115s | 78–115s |

E2e integration + slow steps remain advisory-only; thresholds TBD.

## Enforced thresholds (MOL-460)

| Event | Budget | Derivation |
|-------|--------|------------|
| pull_request | **135s** | p95 (115s) + ~17% margin |
| push to `main` | **140s** | p95 (115s) + ~20% margin |

The unit job runs `scripts/ci_slo_gate.py` after pytest + the advisory timing report. It parses
`pytest-unit.log`, compares wall seconds to `CI_UNIT_PYTEST_BUDGET_S`, and **fails the job** when
over budget (not `continue-on-error`).

## Where to read results

1. **GitHub Actions job summary** — each pytest step appends seconds + test count via
   `scripts/ci_timing_report.py` → `$GITHUB_STEP_SUMMARY`.
2. **Unit pytest log** — top 25 slow tests (`--durations=25 --durations-min=1.0`); advisory only.
3. **`ci-timing.json` artifact** — uploaded on **`main` pushes only** (merged partials from unit +
   e2e jobs). Example:

```json
{
  "sha": "abc123…",
  "unit_pytest_s": 91.0,
  "e2e_integration_s": 27.0,
  "e2e_slow_s": 6.0,
  "test_count": 3944,
  "xdist": true
}
```

## What regresses

- **`unit_pytest_s` climbing** above the enforced budget — runner variance exists, but sustained
  drift usually means new slow tests, lost parallelism, or fixture creep.
- **Top `--durations` entries** — a single test suddenly dominating the list.
- **E2e steps** — toolchain or whisper cache misses (first-run fetch) inflate integration time.

## Pass/fail

- **Unit pytest SLO** — **blocking** via `scripts/ci_slo_gate.py` (135s PR / 140s main).
- **E2e timing + slow-test report** — advisory only (`continue-on-error` on timing report steps).
