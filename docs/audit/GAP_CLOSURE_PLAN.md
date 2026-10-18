# Gap closure plan

Operator plan for the production-audit score. It does not assign new point weights. The only numeric steps are in [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md): headline **78**, band Launchable With Caveats. REL-CI-ASR is the only open row and is ledger delta 0.

Re-measured 2026-10-18. Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`.

| Fact | Value |
|------|--------|
| Studio `GET /_fingerprint` | `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`, pid 65307, generation `246c325b4dc81a3106fce99abe982f90`, `start_time` `2026-10-17T22:25:22.259438+00:00` |
| Pump `daemon._last_heartbeat_code` | `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` |
| Live-checkout disk | `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` (`.worktrees/live-origin-main`) |
| Doctor disk, this branch | `c3ad57a4567720fa35afc9fc4bd29d5f89fc2568` |
| Doctor | Exit 1 on that SHA difference only. Queue check passed. Docker up. Postiz reachable. Zernio auth ok |
| Queue | `0 drainable past-due (up to 0.0h); 82 parked on non-active accounts; 0 retired lineage; 837 total queued` |
| `HF_TOKEN` | Absent from `gh secret list` |
| Unit on the subject | Run `39074067963` success, push, headSha `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` |
| Architecture | Run `39074333775` success. `[SKIP] DC-3` (HTTP 403). No `[FAIL] DC-8` |
| E2E latest | Run `39016845493` success, headSha `f65411d5806cce087a475261bf4c96c98f0bcf97` |
| Nightly latest | Run `39012855146` failure, headSha `f65411d5806cce087a475261bf4c96c98f0bcf97`, `[asr] toolchain smoke` |
| Protection | `strict` true, contexts `["unit (fast, no toolchain)"]` |

## Why capped at 84

- **Ceiling, not a subtraction.** The production-audit skill caps a report at **84** when CI is not green or the launch-critical path was not tested end to end, and at **69** for the secrets, authz, webhook, migration, and rollback conditions. None of those 69 conditions bind. Unit CI on the subject is green, and one live Postiz send exists, so 84 is not applied as a deduction. The ledger headline stays **78**.
- **Why this is not Strong.** REL-CI-ASR is open. The latest nightly and the latest e2e schedule are still headSha `f65411d5`, not the subject. An open ASR row stays a caveat. The band is Launchable With Caveats.
- **What would move the number.** A new ledger row. Closing REL-CI-ASR is delta 0 on this sheet. It clears the caveat. It does not add points by itself.

The committed baseline on `d2cded31` is **72/100**. This subject is **78/100**. Open rows are delta 0. That is not a drop from 72.

This pass does not wipe the ledger, restart launchd, or dispatch workflows.

## Why the headline is 78, not 100

| Gap class | ID or rule | Moves the headline? | Evidence |
|-----------|------------|---------------------|----------|
| Cap | **69** and **84** | No. 78 stays. | [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md) ledger |
| Closed, ledger delta 0 | **REL-UNDEPLOYED** | No | Fingerprint equals the subject. [OPEN_GAPS.md](OPEN_GAPS.md#rel-undeployed) |
| Closed, ledger delta 0 | **OPS-POSTIZ** | No | 2026-10-18 doctor: Postiz reachable, docker up |
| Closed, ledger delta 0 | **OPS-QUEUE** | No | `0 drainable past-due`; 837 queued, not wiped. Parked 82 |
| Open, ledger delta 0 | **REL-CI-ASR** | No | Nightly `39012855146` failed. `HF_TOKEN` is absent |
| Closed, ledger delta 0 | **REL-CI-ARCH** | No | Run `39074333775` printed `[SKIP] DC-3` and exited 0 |
| Closed | **C3-F01** | No | `test_ct_multi_resolve_one_real_ffmpeg`. `clip.py` was not edited |
| Resolved | **H-INT-F01** | No | Operator edits of `reconcile.py` are C6. `partition.json` keeps C7 |
| Closed by stale mark | **U0-F01**, **H-DOCS-F01** | No | `docs/CODEMAPS/subsystem-traces/STALE.md`. Live count 196. Traces were not regenerated |

## What remains

### REL-CI-ASR

Open. `secrets.HF_TOKEN` is empty. Nightly `39012855146` failed `[asr] toolchain smoke (nightly)` (`whisper produced no JSON (rc=1)`, `awaiting.moments == 1`). The job has not run on the subject SHA. Do not change `doctor.py`. Do not dispatch. Steps: [OPEN_GAPS.md](OPEN_GAPS.md#rel-ci-asr).

### Closed on this measurement

- **REL-UNDEPLOYED.** Studio fingerprint, pump, and live-checkout disk equal the subject. Doctor’s disk SHA differs because this branch is docs ahead of `origin/main`. That is `doctor._deploy_code_check` working. It is not a restart.
- **OPS-POSTIZ.** Doctor: Postiz reachable, docker up. Integrations probe HTTP 401 from Express.
- **OPS-QUEUE.** 0 drainable, 837 queued. Parked is 82. Rows were not deleted.
- **REL-CI-ARCH.** Run `39074333775` on the subject printed `[SKIP] DC-3` (HTTP 403) and the job succeeded.
- **REL-CI-E2E.** Latest schedule `39016845493` succeeded on `f65411d5`. The row stays closed. It has not run on the subject.
- **85+.** `post_5854bb63fefb` was sent through Postiz on the subject (`outcome=publish_missing_url`, `backend=postiz`). On 2026-10-18 it is still `needs_reconcile` with no `public_url`. The send is the close. The URL is not back. [OPEN_GAPS.md](OPEN_GAPS.md#85).

## Decisions

1. **Queue (OPS-QUEUE).** Closed. 0 drainable, 837 queued, parked 82. [OPEN_GAPS.md](OPEN_GAPS.md#ops-queue).
2. **Postiz (OPS-POSTIZ).** Closed. Docker up, Postiz reachable. [OPEN_GAPS.md](OPEN_GAPS.md#ops-postiz).
3. **ASR nightly (REL-CI-ASR).** Open. `HF_TOKEN` is absent. Nightly `39012855146` failed. [OPEN_GAPS.md](OPEN_GAPS.md#rel-ci-asr).
4. **Trace and codemap (U0-F01, H-DOCS-F01).** Closed by the stale mark. Live count 196. `partition.json` has no `indexed_cluster_stems` field. Measured clusters include C7 16 and C10 12. Traces were not regenerated.
5. **C3-F01.** Closed. `test_ct_multi_resolve_one_real_ffmpeg`. `clip.py` was not edited. This does not change 78.
6. **Score target.** Headline stays 78. Unit run `39074067963` passed on the subject. The live send is [OPEN_GAPS.md](OPEN_GAPS.md#85).

## Stop line

Open: **REL-CI-ASR** only. That list matches [OPEN_GAPS.md](OPEN_GAPS.md). Fingerprint sha is `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`.

Next action: set `HF_TOKEN`, then the scheduled nightly ASR smoke has to pass with `awaiting.moments == 1`.
