# Pack: X-DEP — external deps

## Findings

| ID | Status | Owner | Evidence |
|----|--------|-------|----------|
| OPS-ZERNIO | closed | code | `health_probes.zernio_dep_health`. 2026-10-17 `uv run fanops doctor`: `[PASS] Zernio backend auth ok`; `[ok] zernio: reachable`. |
| OPS-POSTIZ | open | operator | 2026-10-17 doctor: docker down, Postiz unreachable. Ledger +0. [OPEN_GAPS.md](../OPEN_GAPS.md#ops-postiz). |
| OPS-QUEUE | open | operator | 2026-10-17 doctor: 5 drainable past-due, 851 queued. Ledger +0, not wiped. [OPEN_GAPS.md](../OPEN_GAPS.md#ops-queue). |

## Audit

`zernio_dep_health` uses `_http_reachable(..., require_2xx=True)`. A 2xx is up. 401 and 5xx are down. A connection error is unreachable. No creds stays skipped.

OPS-POSTIZ and OPS-QUEUE are open. Closure is [OPEN_GAPS.md](../OPEN_GAPS.md). `command -v fanops` printed nothing. `uv` is `/Users/molhamhomsi/.local/bin/uv`. 2026-10-17 `uv run fanops doctor` exited 1. Ledger was not drained.

```
Postiz health probe failed (treating as unhealthy): ConnectionError: HTTPConnectionPool(host='localhost', port=4007): Max retries exceeded with url: /api/public/v1/integrations (Caused by NewConnectionError("H
  [FAIL] Postiz backend reachable (real /integrations probe, not the nginx health-check)  -> Postiz backend unreachable — the container's health check is nginx-only and can lie; see docs/POSTIZ_OPS.md.
  [FAIL] publish daemon alive + queue draining (heartbeat + past-due backlog)  -> 5 drainable past-due (up to 463.5h); 76 parked on non-active accounts; 0 retired lineage; 851 total queued
  [DOWN] docker: daemon down
  [DOWN] postiz: unreachable
  [ok] zernio: reachable
  [PASS] Zernio backend auth ok (authenticated /accounts read)
```

The stored probe line ends at `NewConnectionError("H` (209 characters). Zernio's two lines are success, so OPS-ZERNIO stays closed.
