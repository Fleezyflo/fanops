# FanOps production score

Production audit: 78/100, Launchable With Caveats, with the open nightly ASR smoke (`HF_TOKEN` unset) and no scheduled e2e or nightly on origin/main `430ece73` as the risks to fix before public launch.

Assessment date 2026-10-18. Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` (merge of #1316). The ledger below is the only arithmetic. It stays **78**. REL-CI-ASR is the only open register row and is ledger delta 0, so this is not Strong.

The 69 cap does not bind. `FANOPS_IG_SCRAPE_PASSWORD` is masked by `config_introspect._is_secret` (`_PASSWORD` suffix) and `secret_provider.is_secret_env_key`. This pass did not find that value in a client bundle, a log, or a committed file. Studio listens on `127.0.0.1`. Publish idempotency stays `_publish_one` (claim before POST) plus Zernio `x-request-id`. Rollback remains the “Rollback / safety” section in `docs/GOLIVE.md`. There is no payment webhook.

The 84 cap is not a subtraction. `unit (fast, no toolchain)` on the subject push is green (run `39074067963`). One live Postiz send of `post_5854bb63fefb` exists on that SHA. The scheduled e2e and nightly transcripts are still headSha `f65411d5806cce087a475261bf4c96c98f0bcf97`. REL-CI-ASR stays open.

## Blockers

None on the shipped SHA. The merge is what production runs. Studio `GET /_fingerprint`, the pump heartbeat, and the live-checkout disk are that SHA.

## High-value fixes

- Set `HF_TOKEN` on the repository. The next scheduled `[asr] toolchain smoke (nightly)` has to pass with `awaiting.moments == 1`. Do not dispatch it. Do not change `doctor.py`.
- Let the next `ci-e2e.yml` and `nightly.yml` schedules run on `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. Neither has a transcript on that SHA yet.

## Evidence checked

Subject and branch:

```
git rev-parse origin/main
430ece7377bf33cc5c5e72876c13d6b78b8e6ca9

git status --short --branch
## audit/production-audit-2026-09-close...origin/audit/production-audit-2026-09-close

git diff --stat origin/main -- src
(empty)
```

`git ls-files src/fanops/` Python count is 196. `partition.json` `live_py_count` is 196 and every value is C1–C10. Cluster counts: C1 15, C2 14, C3 18, C4 10, C5 25, C6 32, C7 16, C8 25, C9 29, C10 12. `partition.json` has no `indexed_cluster_stems` field. The stale banner still says 124 stems.

Studio, pump, and disk are three measurements:

```
curl -sS http://127.0.0.1:8787/_fingerprint
{"generation":"246c325b4dc81a3106fce99abe982f90","label":"FanOps","pid":65307,"sha":"430ece7377bf33cc5c5e72876c13d6b78b8e6ca9","start_time":"2026-10-17T22:25:22.259438+00:00"}

pump daemon._last_heartbeat_code
430ece7377bf33cc5c5e72876c13d6b78b8e6ca9

live worktree .worktrees/live-origin-main git rev-parse HEAD
430ece7377bf33cc5c5e72876c13d6b78b8e6ca9

doctor disk daemon._version_signal from this branch
c3ad57a4567720fa35afc9fc4bd29d5f89fc2568
```

`uv run fanops doctor` exit 1. The queue check passed. The failing line is `doctor._deploy_code_check`: pump `430ece7377bf` versus this branch’s disk `c3ad57a4`. The live checkout matches the pump. The pump was not restarted. Also `[ok] docker: daemon up`, `[ok] postiz: reachable`, `[PASS] Zernio backend auth ok`, `poster backend: postiz, zernio (LIVE)`.

Queue, same loop as `doctor._daemon_liveness_check`: `0 drainable past-due (up to 0.0h); 82 parked on non-active accounts; 0 retired lineage; 837 total queued`. That is the historical shape (0 drainable, 837 queued). Parked was 81 on 2026-10-17. The ledger was not wiped.

`GET http://localhost:4007/api/public/v1/integrations` returned HTTP 401 `{"msg":"No API Key found"}` with `X-Powered-By: Express`.

`post_5854bb63fefb` is still `needs_reconcile` on Instagram, with a submission id and no `public_url`. Log `2026-10-17T22:16:23Z` `stage=publish` `outcome=publish_missing_url` `backend=postiz`.

`gh secret list` names `CURSOR_CODEMAP_SYNC_WEBHOOK_TOKEN`, `CURSOR_CODEMAP_SYNC_WEBHOOK_URL`, and `PROTECTION_READ_TOKEN`. `HF_TOKEN` is absent.

CI, latest three each, then the subject push:

```
ci.yml
39075108641 success pull_request audit/production-audit-2026-09-close
39074258273 success pull_request audit/production-audit-2026-09-close
39074204825 success pull_request dependabot/github_actions/actions/download-artifact-8.0.2

ci.yml --branch main
39074067963 success push headSha 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9
  unit (fast, no toolchain) success

architecture.yml
39075108685 success pull_request
39074333775 success workflow_dispatch main headSha 430ece7377bf33cc5c5e72876c13d6b78b8e6ca9
  [SKIP] DC-3 · HTTP 403, job success, no [FAIL] DC-8
39074258276 success pull_request

ci-e2e.yml
39016845493 success schedule headSha f65411d5806cce087a475261bf4c96c98f0bcf97 2026-10-17T08:18:05Z
38876056441 success schedule headSha f65411d5806cce087a475261bf4c96c98f0bcf97 2026-10-16T08:47:37Z
38709140058 success schedule headSha f65411d5806cce087a475261bf4c96c98f0bcf97 2026-10-15T08:55:22Z

nightly.yml
39012855146 failure schedule headSha f65411d5806cce087a475261bf4c96c98f0bcf97 2026-10-17T07:13:50Z
  [asr] toolchain smoke failure; dependency audit success
  tests/integration/test_e2e_real.py awaiting.moments == 1
  whisper produced no JSON (rc=1), unauthenticated HF Hub
38868030626 failure schedule headSha f65411d5806cce087a475261bf4c96c98f0bcf97 2026-10-16T07:33:59Z
38700956871 failure schedule headSha f65411d5806cce087a475261bf4c96c98f0bcf97 2026-10-15T07:45:54Z
```

Branch protection on 2026-10-18: `strict` true, contexts `["unit (fast, no toolchain)"]`.

Closed symbols re-read on this tree (it matches `origin/main` for `src/`): `ledger_wipe.snapshot_is_restorable`, `vocals.isolate_vocals`, `hashtags.ship_from_lock`, `crosspost` assert `_JITTER_MAX < _STEP_MIN`, `post/run.py` `_NET_POST_FIELDS`, `actions_wipe.confirm_wipe`, `health_probes.zernio_dep_health`. `meta_graph._read_queries` is absent. `vet_hashtags` is absent. `postiz.py` and `reconcile.py` match `origin/main`.

## Evidence missing

- Pytest was not run locally. CI on the PR is the test run.
- No `nightly.yml` or `ci-e2e.yml` schedule transcript on `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`.
- `post_5854bb63fefb` has no `public_url` yet. Reconcile has not promoted it.

## Next action

Set the `HF_TOKEN` Actions secret, then wait for the scheduled `[asr] toolchain smoke (nightly)` to pass with `awaiting.moments == 1`. Do not dispatch workflows. Do not restart launchd. Do not wipe the ledger.

## Score ledger

Commit `d2cded31` scores **72/100**. This subject scores **78/100**, which is **+6**. The prior pass attributed **+2** to C1 and **+1** each to the four closures that were not on the 72 sheet. C7-F01 and C9-F01 were already true on `d2cded31`; closing that stale prose is **+0**. No ledger row moves on 2026-10-18.

| Step | Delta | Running | Basis |
|------|------:|--------:|-------|
| Committed 2026-09-12 sheet | — | 72 | C1-F01, C7-F01, C9-F01, H-DOCS-F01 listed as open high-value fixes |
| C7-F01, C9-F01 | +0 | 72 | `_read_queries` absent; `confirm_wipe` checks the preview token |
| C1-F01 | +2 | 74 | `ledger_wipe.snapshot_is_restorable` calls `get_logger(cfg)` with outcome `snapshot_unrestorable` |
| SEC-IGPW | +1 | 75 | `FANOPS_IG_SCRAPE_PASSWORD` masked; `_PASSWORD` is a secret suffix |
| SEC-ENV | +1 | 76 | `.env` rewrite holds `ledger._file_lock` |
| OPS-ZERNIO | +1 | 77 | `zernio_dep_health` follows the authenticated doctor check |
| OPS-SHA (doctor) | +1 | 78 | Loaded daemon fails `_deploy_code_check` when either SHA is missing or the two differ |
| C2-F04 | +0 | 78 | Opened and closed earlier. Native scan skips a child `.pull` |
| C3-F03 | +0 | 78 | Opened and closed earlier. An empty rc-0 temp does not replace the previous mp4 |
| C4-F03 | +0 | 78 | Opened and closed earlier. `_hook_script_mismatch` |
| C4-F04 | +0 | 78 | Opened and closed earlier. Studio `regenerate_hook` uses that check |
| C9-F03 | +0 | 78 | Opened and closed earlier. An imminent queued post is refused before `render_moment` |
| C10-F04 | +0 | 78 | Opened and closed earlier. A failed card or feed swap re-renders the card with the error |
| C5-F02 | +0 | 78 | Opened and closed earlier. A miss-only source-tag pass writes the sidecar |
| H-DOCS-F01, U0-F01 | +0 | 78 | Stale-mark closure. Traces were not regenerated |
| Open, ledger delta 0 | 0 | 78 | REL-CI-ASR stays open. [OPEN_GAPS.md](OPEN_GAPS.md) |
| REL-PROT, REL-CI-E2E | 0 | 78 | Closed. They do not lift the number |
| Cap | — | **78** | 69 does not bind. 84 is not a subtraction. REL-CI-ASR stays open, so the band is not Strong |

**Path to ≥84.** Already true on the ledger’s own terms: fingerprint sha equals the subject, and unit run `39074067963` passed on that push. The headline stays 78 until a ledger row moves.

**85+.** The send of `post_5854bb63fefb` through Postiz on the subject SHA is the recorded live publish (`backend=postiz`, not dry-run). It is still `needs_reconcile` with no `public_url`. That does not add a ledger row.

## Caveats that hold the score

- REL-CI-ASR is open. Nightly `39012855146` failed. `HF_TOKEN` is absent. [OPEN_GAPS.md](OPEN_GAPS.md#rel-ci-asr).
- Scheduled e2e `39016845493` is green on `f65411d5`, not on the subject.
- OPS-POSTIZ and OPS-QUEUE are closed on the 2026-10-18 doctor. 0 drainable, 837 queued. The ledger was not wiped.
- U0-F01 and H-DOCS-F01 are closed by the stale mark. Traces were not regenerated.
- H-INT-F01 is resolved. Operator edits of `reconcile.py` are C6. `partition.json` keeps C7. No retag.

There is no 100 on this sheet. The headline stays **78**.

**Caps.** 69 does not bind. 84 is a ceiling that this 78 does not reach. See [Why capped at 84](GAP_CLOSURE_PLAN.md#why-capped-at-84).
