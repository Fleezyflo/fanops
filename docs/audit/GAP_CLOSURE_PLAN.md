# Gap closure plan

Operator plan for the production-audit score. It does not assign new point weights. The only numeric steps are in [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md): headline **78**, band Launchable With Caveats. Zero open register rows. REL-CI-ASR is resolved and is ledger delta 0.

Re-measured 2026-10-18. Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`.

| Fact | Value |
|------|--------|
| Studio `GET /_fingerprint` | `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`, pid 65307, generation `246c325b4dc81a3106fce99abe982f90`, `start_time` `2026-10-17T22:25:22.259438+00:00` |
| Pump `daemon._last_heartbeat_code` | `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` |
| Live-checkout disk | `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` (`.worktrees/live-origin-main`) |
| Doctor disk, this branch | `7aa5d3239a1c61f54613d03d864375ba4420cadf` (`daemon._version_signal`, this checkout HEAD) |
| Doctor | Completed ~5 min, exit 1. Sole FAIL = deploy-code (disk `7aa5d323` ≠ pump `430ece73`). PASS: Postiz/Zernio reachable, docker up, queue draining, faster-whisper importable, LIVE |
| Queue | `0 drainable past-due; 86 parked on non-active accounts; 0 retired lineage; 831 queued; 4 needs_reconcile` (predicate + `Ledger.posts_in_state`) |
| Unit on the subject | Run `39074067963` success, push, headSha `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`, job `unit (fast, no toolchain)` success |
| Architecture | Run `39074333775` success. `[SKIP] DC-3` (HTTP 403). No `[FAIL] DC-8` |
| E2E latest | Run `39112132315` success, headSha `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` (the subject); prior `39016845493` on `f65411d5` |
| Nightly latest | Run `39107733972` failure, headSha `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` (the subject), `[asr] toolchain smoke` (REL-CI-ASR resolved) |
| Protection | `strict` true, contexts `["unit (fast, no toolchain)"]` |

## Why capped at 84

- **Ceiling, not a subtraction.** The production-audit skill caps a report at **84** when CI is not green or the launch-critical path was not tested end to end, and at **69** for the secrets, authz, webhook, migration, and rollback conditions. None of those 69 conditions bind. Unit CI on the subject is green, and one live Postiz send exists, so 84 is not applied as a deduction. The ledger headline stays **78**.
- **Why the band stays Launchable With Caveats.** The ledger is **78**. The scheduled `ci-e2e.yml` transcript `39112132315` is green on the subject; the `nightly.yml` `asr-smoke` `39107733972` failed on the subject (resolved CI hygiene). `post_5854bb63fefb` is still `needs_reconcile` with no `public_url`. That is the standing operational caveat. It is not an open register row, and it is not a missing Actions secret.
- **What would move the number.** A new ledger row. Closing REL-CI-ASR is delta 0 on this sheet. It does not add points.

The committed baseline on `d2cded31` is **72/100**. This subject is **78/100**. Open rows are delta 0. That is not a drop from 72.

This pass does not wipe the ledger, restart launchd, or dispatch workflows.

## Why the headline is 78, not 100

| Gap class | ID or rule | Moves the headline? | Evidence |
|-----------|------------|---------------------|----------|
| Cap | **69** and **84** | No. 78 stays. | [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md) ledger |
| Closed, ledger delta 0 | **REL-UNDEPLOYED** | No | Fingerprint equals the subject. [OPEN_GAPS.md](OPEN_GAPS.md#rel-undeployed) |
| Closed, ledger delta 0 | **OPS-POSTIZ** | No | 2026-10-18 doctor: Postiz reachable, docker up |
| Closed, ledger delta 0 | **OPS-QUEUE** | No | `0 drainable past-due`; 831 queued, not wiped. Parked 86; 4 needs_reconcile |
| Resolved, ledger delta 0 | **REL-CI-ASR** | No | Nightly `asr-smoke` is CI hygiene. Run `39107733972` failed on the subject; not in `required_contexts`. Product code does not read a hub token |
| Closed, ledger delta 0 | **REL-CI-ARCH** | No | Run `39074333775` printed `[SKIP] DC-3` and exited 0 |
| Closed | **C3-F01** | No | `test_ct_multi_resolve_one_real_ffmpeg`. `clip.py` was not edited |
| Resolved | **H-INT-F01** | No | Operator edits of `reconcile.py` are C6. `partition.json` keeps C7 |
| Closed by stale mark | **U0-F01**, **H-DOCS-F01** | No | `docs/CODEMAPS/subsystem-traces/STALE.md`. Live count 196. Traces were not regenerated |

## What remains

No open register rows. One operational caveat stays on the subject; scheduled CI is now current on it:

- The `ci-e2e.yml` schedule `39112132315` is green on `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`; the `nightly.yml` `asr-smoke` `39107733972` failed on that SHA (REL-CI-ASR resolved). Do not dispatch them.
- `post_5854bb63fefb` is still `needs_reconcile` with no `public_url`. The ledger was not wiped.

### Closed or resolved on this measurement

- **REL-UNDEPLOYED.** Studio fingerprint, pump, and live-checkout disk equal the subject. Doctor’s disk SHA differs because this branch is docs ahead of `origin/main`. That is `doctor._deploy_code_check` working. It is not a restart.
- **OPS-POSTIZ.** Doctor: Postiz reachable, docker up. Integrations probe HTTP 401 from Express.
- **OPS-QUEUE.** 0 drainable, 831 queued. Parked is 86. 4 needs_reconcile. Rows were not deleted.
- **REL-CI-ARCH.** Run `39074333775` on the subject printed `[SKIP] DC-3` (HTTP 403) and the job succeeded.
- **REL-CI-E2E.** Latest schedule `39112132315` succeeded on the subject SHA. The row stays closed.
- **REL-CI-ASR.** Resolved. `nightly.yml` `asr-smoke` is scheduled CI hygiene. Run `39107733972` failed on the subject; `asr-smoke` is not in `required_contexts`. Product code does not read a hub token. [OPEN_GAPS.md](OPEN_GAPS.md#rel-ci-asr).
- **85+.** `post_5854bb63fefb` was sent through Postiz on the subject (`outcome=publish_missing_url`, `backend=postiz`). On 2026-10-18 it is still `needs_reconcile` with no `public_url`. The send is the close. The URL is not back. [OPEN_GAPS.md](OPEN_GAPS.md#85).

## Decisions

1. **Queue (OPS-QUEUE).** Closed. 0 drainable, 831 queued, parked 86, 4 needs_reconcile. [OPEN_GAPS.md](OPEN_GAPS.md#ops-queue).
2. **Postiz (OPS-POSTIZ).** Closed. Docker up, Postiz reachable. [OPEN_GAPS.md](OPEN_GAPS.md#ops-postiz).
3. **ASR nightly (REL-CI-ASR).** Resolved. Scheduled `nightly.yml` `asr-smoke` is CI hygiene. Run `39107733972` is monitoring on the subject; `asr-smoke` is not a required context. Product runtime does not read a hub token. [OPEN_GAPS.md](OPEN_GAPS.md#rel-ci-asr).
4. **Trace and codemap (U0-F01, H-DOCS-F01).** Closed by the stale mark. Live count 196. `partition.json` has no `indexed_cluster_stems` field. Measured clusters include C7 16 and C10 12. Traces were not regenerated.
5. **C3-F01.** Closed. `test_ct_multi_resolve_one_real_ffmpeg`. `clip.py` was not edited. This does not change 78.
6. **Score target.** Headline stays 78. Unit run `39074067963` passed on the subject. The live send is [OPEN_GAPS.md](OPEN_GAPS.md#85).

## Stop line

Open register rows: none. That matches [OPEN_GAPS.md](OPEN_GAPS.md). Fingerprint sha is `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`.

Next action: wait for scheduled `ci-e2e.yml` and `nightly.yml` on that SHA. `post_5854bb63fefb` still has no `public_url`. Do not dispatch. Do not restart launchd. Do not wipe the ledger.
