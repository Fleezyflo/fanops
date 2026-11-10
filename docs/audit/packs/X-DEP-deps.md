# Pack: X-DEP — external deps

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Entry symbols re-read 2026-10-18: `health_probes._http_reachable`, `health_probes.zernio_dep_health`, `health_probes.postiz_dep_health`, `doctor._daemon_liveness_check` drainable loop, `postiz_health_probe`. `postiz.py` and `zernio.py` were not edited. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Findings

| ID | Severity | Class | Observed | Required | Evidence | Status |
|----|----------|-------|----------|----------|----------|--------|
| OPS-ZERNIO | low | probe | On subject SHA, `zernio_dep_health` delegates to `_http_reachable(..., require_2xx=True)` with `requests.get(..., timeout=3)`. A 2xx is up; any other HTTP status becomes `HTTP {code}` down; `RequestException` is unreachable. With creds configured, bounded live probe returned reachable (2026-10-18). Doctor’s separate `doctor._zernio_reach_check` uses `zernio_check_auth` → `GET /accounts` (30s timeout), not the dep strip probe. | Zernio dep strip must not treat non-2xx as healthy; probe must bound socket wait. | `health_probes.zernio_dep_health`, `health_probes._http_reachable`. Live: `.venv/bin/python` + `load_dotenv` + `health_probes.zernio_dep_health(cfg)` within 8s SIGALRM (ok, detail reachable). Full `fanops doctor` not run — parent confirmed vendor reachability can block >8m. | closed |
| OPS-POSTIZ | medium | ops | On subject SHA, Postiz dep/doctor path shares `postiz_health_probe`: connection/`Max retries` errors → unhealthy `status_code=None` (local stack down); `PostizAuthError` → 401 unhealthy (bad key, backend answered); other HTTP via `_status_of` → unhealthy with code (backend/API fault). `postiz_dep_health` maps healthy → reachable; status_code set → `answers HTTP but API unhealthy`. Process recovery when localhost:4007 is down is operator closure ([OPEN_GAPS.md](../OPEN_GAPS.md#ops-postiz)), not a probe code defect. Bounded live `postiz_dep_health` returned reachable 2026-10-18. | Postiz-local connection failures must be distinguishable from API status answers; nginx-only container health must not be the doctor probe. | `post.postiz.postiz_health_probe`, `health_probes.postiz_dep_health`, `health_probes.postiz_doctor_check`. Live bounded probe as OPS-ZERNIO row. | closed |
| OPS-QUEUE | low | invariant | On subject SHA, `doctor._daemon_liveness_check` counts **drainable** queued posts: `PostState.queued` only (not `needs_reconcile`), `is_due_or_past`, age **>** `2 * daemon_interval`, `Ledger.can_promote`, active account (`_non_active_row is None`). `publish_due` uses the same promote/active filters on due queued posts but **no** grace window and additionally skips dryrun/no-provider/integration (posts stay queued). `studio.golive._drainable_past_due` matches promote+active+due with **no** grace. Live read-only census 2026-10-18: 831 queued, 4 `needs_reconcile`, 0 doctor-drainable (0.0h oldest), 86 parked non-active, 0 golive-drainable; interval 600s, grace 1200s. | Backlog FAIL must not conflate parked/inactive or grace-window due posts with a wedged pump; reconcile-parked posts must not enter drainable count. | `doctor._daemon_liveness_check`, `post.run.publish_due`, `studio.golive._drainable_past_due`. Live python loop mirroring doctor symbols (see Evidence commands). | closed |

## Audit slice 2026-10-18 (post-C6)

**Drainable vs publish_due.** `publish_due` iterates `Ledger.posts_in_state(PostState.queued)` with `scheduled_time <= now`, drops non-`can_promote` (retired lineage) and non-active accounts, then for each remainder resolves provider: `"dryrun"` writes preview and leaves `queued`; missing provider/integration skips leave `queued`. It never reads `needs_reconcile`. Doctor backlog uses the same due/promote/active predicates but only increments `backlog_n` when due age exceeds **two** install intervals (default 600s → 1200s grace), so a post due “now” is not drainable in doctor until it is stale past grace. Go-Live refuses flip when **any** golive-drainable past-due queued post exists (no grace).

**Postiz-local vs code errors.** Connection refused / pool errors surface from `postiz_health_probe` as logged `ConnectionError` with `PostizHealth(False, None, …)` — operator fixes Docker/compose ([POSTIZ_OPS.md](../../POSTIZ_OPS.md)). HTTP 401 from Express is an authenticated API answer (bad/missing key), not “container down”. Doctor `postiz_doctor_check` and dep `postiz_dep_health` share `_postiz_probe` → `postiz_health_probe`.

**Zernio status codes.** Dep strip: only 2xx is green under `require_2xx`. Doctor auth check: bool from `/accounts`; 401 → `ZernioAuthError` FAIL. `_http_reachable` timeout is 3s — not the source of multi-minute doctor hangs; full doctor was intentionally not executed this pass.

## Evidence commands run

```
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/health_probes.py
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/doctor.py  # _daemon_liveness_check
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/post/run.py  # publish_due
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/studio/golive.py  # _drainable_past_due
git show 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9:src/fanops/post/postiz.py  # postiz_health_probe
.venv/bin/python  # read-only ledger + doctor drainable loop (831 queued, 4 needs_reconcile, 0 drainable, 86 parked)
.venv/bin/python  # load_dotenv + health_probes.zernio_dep_health / postiz_dep_health (8s SIGALRM; both reachable)
```

## Historical down transcript (not current close)

```
Postiz health probe failed (treating as unhealthy): ConnectionError: HTTPConnectionPool(host='localhost', port=4007): Max retries exceeded with url: /api/public/v1/integrations (Caused by NewConnectionError("H
  [FAIL] Postiz backend reachable (real /integrations probe, not the nginx health-check)  -> Postiz backend unreachable — the container's health check is nginx-only and can lie; see docs/POSTIZ_OPS.md.
  [FAIL] publish daemon alive + queue draining (heartbeat + past-due backlog)  -> 5 drainable past-due (up to 463.5h); 76 parked on non-active accounts; 0 retired lineage; 851 total queued
  [DOWN] docker: daemon down
  [DOWN] postiz: unreachable
  [ok] zernio: reachable
  [PASS] Zernio backend auth ok (authenticated /accounts read)
```
