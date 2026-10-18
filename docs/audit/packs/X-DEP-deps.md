# Pack: X-DEP — external deps

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Finding status below is unchanged. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Findings

| ID | Status | Owner | Evidence |
|----|--------|-------|----------|
| OPS-ZERNIO | closed | code | `health_probes.zernio_dep_health` (`require_2xx=True`). 2026-10-18 `uv run fanops doctor`: `[PASS] Zernio backend auth ok`; `[ok] zernio: reachable`. |
| OPS-POSTIZ | closed | closed | 2026-10-18 doctor: `[PASS] Postiz backend reachable`, `[ok] postiz: reachable`, `[ok] docker: daemon up`. `GET http://localhost:4007/api/public/v1/integrations` HTTP 401 `{"msg":"No API Key found"}`, `X-Powered-By: Express`. [OPEN_GAPS.md](../OPEN_GAPS.md#ops-postiz). |
| OPS-QUEUE | closed | closed | 2026-10-18 same drainable loop as `doctor._daemon_liveness_check`: `0 drainable past-due (up to 0.0h); 82 parked on non-active accounts; 0 retired lineage; 837 total queued`. Doctor queue check `[PASS]`. Ledger not wiped. [OPEN_GAPS.md](../OPEN_GAPS.md#ops-queue). |

## Audit

`zernio_dep_health` uses `_http_reachable(..., require_2xx=True)`. A 2xx is up. 401 and 5xx are down. A connection error is unreachable. No creds stays skipped.

OPS-POSTIZ and OPS-QUEUE are closed on the 2026-10-18 doctor in the table above. The block below is an earlier down transcript, not the close.

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
