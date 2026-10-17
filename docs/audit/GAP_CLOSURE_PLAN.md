# Gap closure plan

Operator plan for the production-audit score. It does not assign new point weights. The only numeric steps already written are in [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md): headline **78**, cap **84**, **85+** after an end-to-end publish on the shipped SHA. The score ledger’s per-row deltas stay there.

Re-measured 2026-10-17 after PR #1316 existed:

| Fact | Value |
|------|--------|
| `git rev-parse HEAD` | `c1d892df86a02e57e3a9243109a00702c1bdfce8` |
| Branch | `audit/production-audit-2026-09` tracking its origin. Working tree is dirty on top of that SHA. |
| PR #1316 | `OPEN`. `headRefOid` equals HEAD. |
| Required check | `unit (fast, no toolchain)` **FAILURE** on run `39019468576`. Protection still requires only that context (`REL-PROT`). |
| Also red on the same rollup | `impact report` (run `39019468512`) and `lane file-ownership + cross-PR collision` (run `39019468622`). Neither is the required context. |
| `GET /_fingerprint` | `f65411d5806cce087a475261bf4c96c98f0bcf97` (pid 1225, generation `246c325b4dc81a3106fce99abe982f90`) |

## Why capped at 84

- **Ceiling, not a subtraction.** The production-audit skill caps the report at **84** when CI is not green or the launch-critical path was not tested end to end. The ledger headline stays **78** (+6 over the committed **72** on `d2cded31`). **78, capped at 84** means the number stays 78 until more ledger rows close. The product is not **85+** launch-verified until the cap conditions clear. The five deferred rows (**REL-UNDEPLOYED**, **OPS-POSTIZ**, **OPS-QUEUE**, **REL-CI-ASR**, **REL-CI-ARCH**) are ledger delta **0** (score ledger “Deferred, no points”; Part 1 below). They are caveats. They are not why the sheet says capped at 84. The **69** secrets cap does not bind.
- **Two binding conditions.** **REL-UNDEPLOYED:** the scored tree is not what Studio serves. Fingerprint `f65411d5806cce087a475261bf4c96c98f0bcf97`. PR #1316 head `c1d892df86a02e57e3a9243109a00702c1bdfce8`. **Launch-critical publish** was not re-run end to end on the scored SHA. Dry-run does not count: `docs/GOLIVE.md` posts nothing in dry-run, and `post/run.py` `_post_provider` returns `"dryrun"` when the tree is not live. **REL-CI-E2E** closed on `f65411d5` does not lift this cap. **REL-CI-ASR** and **REL-CI-ARCH** stay deferred for honesty and have no cap mechanics.
- **What lifts ≥84 versus 85+.** **≥84** is the score’s “Path to ≥84”: merge so the fingerprint equals the merge SHA, and `unit (fast, no toolchain)` is green. That clears the deploy mismatch in the cap narrative. It does not raise 78 without new ledger rows. **85+** also needs a documented **live** publish smoke on that shipped SHA (“Path to 100” in [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md); live versus dry-run in `src/fanops/post/CLAUDE.md` and `docs/GOLIVE.md`).

The committed baseline on `d2cded31` is **72/100**. This branch is **78/100**. The cap is a ceiling on that 78. The five deferred rows are delta 0. Neither is a drop from 72.

Nothing in this pass drains the ledger, starts Docker, or merges.

## Part 1 — Why the headline is 78, not 100

| Gap class | ID or rule | Moves the headline? | Evidence |
|-----------|------------|---------------------|----------|
| Cap | **84** | No. The headline stays 78. The cap is the ceiling until the conditions in the score’s “Path to ≥84” and “85+” sentences are true. | [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md) score ledger cap row and the 84-cap paragraph: this tree is not fingerprint `f65411d5`; launch-critical publish was not re-run end to end on the scored SHA. |
| Deferred, ledger delta 0 | **REL-UNDEPLOYED** | No. Score ledger row “Deferred, no points” is 0. | Register: PR #1316, fingerprint `f65411d5`. Closes only when the merge SHA is the fingerprint. |
| Deferred, ledger delta 0 | **OPS-POSTIZ** | No. | 2026-10-17 doctor: `localhost:4007` `/api/public/v1/integrations` max retries; `[DOWN] docker: daemon down`; `[DOWN] postiz: unreachable`. |
| Deferred, ledger delta 0 | **OPS-QUEUE** | No. | Same doctor: `5 drainable past-due (up to 463.9h); 76 parked on non-active accounts; 0 retired lineage; 851 total queued`. Ledger was not drained. |
| Deferred, ledger delta 0 | **REL-CI-ASR** | No. | Nightly run `38868030626`, schedule, failure, head `f65411d5`. Job `[asr] toolchain smoke (nightly)` failed. `dependency audit (pip-audit)` succeeded. Log: `tests/integration/test_e2e_real.py:68` `assert 0 == 1`, and produce `whisper produced no JSON (rc=1)` with an unauthenticated Hugging Face Hub warning. `ingest` also logged `sample.wav` skipped `no_video_stream` (`ingest.py` skip). The moments count is 0 because whisper returned no JSON, not because doctor is wrong. |
| Deferred, ledger delta 0 | **REL-CI-ARCH** | No. | Cron in `.github/workflows/architecture.yml` is `17 5 * * 1`, and `workflow_dispatch` is already declared. Schedule events exist: latest `38253871938` (2026-10-12, failure, head `088702ca`). The failed step is DC-3: `gh: Resource not accessible by integration (HTTP 403)`. The earlier “last 5 runs have no schedule event” line was the default run list, which is PR and push traffic. |
| Accepted, not a defect | **C3-F01** | No. Not in the +6. | `framing._compute_track` calls `extract_frames_grid` once at 9fps. `detect_window` already extracted at 4fps. The cache key includes fps, so the second grid still runs. `clip.py` was not edited. |
| Accepted, not a defect | **H-INT-F01** | No. | `partition.json` keeps `reconcile.py` in C7. Operator edits treat it as C6 (publish). `post/metrics/*` is the mirror split. No X-keys. `ARCH-001` stays the totality rule. |
| Closed by stale mark | **U0-F01**, **H-DOCS-F01** | No. Score ledger records both as +0. | `docs/CODEMAPS/subsystem-traces/STALE.md`. Live Python count 196, indexed stems 124. Last trace commit `f11d0af7` (2026-09-09). |

**C3-F01, resolve versus leave accepted.** Resolve means one `extract_frames_grid` pass serves both the 4fps window and the 9fps track, which means changing the cache key or the caller in `framing.py`, on a picking branch because `clip.py` is a picking hot file. Leave accepted means the second ffmpeg grid stays. The register already chose leave accepted. That choice does not change 78.

**H-INT-F01, resolve versus leave accepted.** Resolve means one map: either retag `reconcile.py` inside `partition.json` (and the totality test that forbids X-keys) or stop describing operator C6 as a second authority. Leave accepted means the dual map stays written down. The index says do not retag. That choice does not change 78.

**U0-F01 and H-DOCS-F01, regenerate versus keep the stale mark.** Regenerating `docs/CODEMAPS/` subsystem traces would replace the banner with prose that matches 196 files and 124 indexed stems. The score ledger already gives that work **+0**, so the headline stays 78 either way. Keeping `STALE.md` is the closure this branch shipped. U0’s remediate line is doc-only: `full-trace-index.md`, `anomalies.md`, `partition.json`, `scripts/codemap_extract/`. `python -m tools.arch regen` refreshes `.reports/architecture/derived/` when scanned source moves. It is not the trace rewrite.

## Part 2 — What remains

### A. Merge and deploy — REL-UNDEPLOYED

PR #1316 must go green on `unit (fast, no toolchain)`, then merge, then `GET /_fingerprint` must equal that merge SHA. A merge is the deploy. Do not restart launchd by hand.

The unit job is red now: run `38986326066`, 28 failed. Failures include `tests/test_deploy_code_check.py`, `tests/test_caption.py`, `tests/test_ledger_wipe_log.py`, `tests/test_health_model.py`, `tests/test_hook_language_gate.py`, `tests/test_moments.py`, `tests/test_autopilot.py`, `tests/test_source_tags.py`, `tests/test_production_audit_gates.py` (`## Re-verification 2026-09-12` missing from `anomalies.md`), and several studio tests. That is not one owner file. It is not fixed in this pass.

### B. Operator and infra — OPS-POSTIZ, OPS-QUEUE

Doctor is red because Docker is down and Postiz on `localhost:4007` is unreachable, and because 5 queued posts are drainable (851 queued total). `AGENTS.md` forbids wiping or draining the ledger as a repair. This pass does not start Docker and does not publish.

### C. CI — REL-CI-ASR, REL-CI-ARCH

ASR: classified below. Not a doctor change.

Architecture: the cron is not misconfigured and Monday has not been skipped. The last ten `schedule` runs all failed. The newest failure is the DC-3 live probe’s `gh` 403. `workflow_dispatch` can re-prove the reconciler without waiting, and it will hit the same probe unless the token can call `gh`. This pass does not edit the workflow.

### D. Verification — 85+

[PRODUCTION_SCORE.md](PRODUCTION_SCORE.md) says **85+** needs an end-to-end publish on the shipped SHA. That SHA is the fingerprint after merge, not this branch.

`docs/GOLIVE.md` section “1. Produce content offline” runs `fanops advance` through the queue and states that dry-run writes payloads and posts nothing. `src/fanops/post/CLAUDE.md`: `_post_provider` returns `"dryrun"` when `not cfg.is_live`, and `_publish_one` is the only network POST. A dry-run does not satisfy the 85+ sentence.

A live post is `docs/GOLIVE.md` Path A step “GO LIVE — publish for real”, or the “Validate learning” probe (one 2099-scheduled throwaway, state in `00_control/cutover.json`, not a ledger wipe). Either one is an operator action after the fingerprint matches. This pass does not publish.

### E. Optional engineering debt

Only rows that are still accepted or explicitly stale, each with a symbol. Closed register rows are not reopened here.

| Item | Symbol | Owner path |
|------|--------|------------|
| C3-F01 | `framing._compute_track` / `extract_frames_grid` | picking (`clip.py`). Decision 5. |
| H-INT-F01 | `partition.json` versus operator C6 for `reconcile.py` | Do not retag. Decision is “leave accepted”. |
| U0-F01, H-DOCS-F01 | `docs/CODEMAPS/subsystem-traces/STALE.md` | Doc-only. Decision 4. |

No new C6 finding. `docs/audit/packs/C6-crosspost-publish.md` “Full checklist 2026-10-17” still says dry-run does not POST, `needs_reconcile` is not a publish input, Zernio reuses `x-request-id`, and Instagram/TikTok plus Zernio `content` are `posted_text_for` while Postiz YouTube `content` is `Post.caption`. This plan does not re-audit `post/run.py` or `reconcile.py`. A contradiction check belongs on the merged SHA, which does not exist yet.

## Part 3 — Repo work versus not

**Repo-implementable without an operator choice: none.** REL-CI-ASR is not a one-file defect. Every other open gap is a Part 4 decision or the multi-file unit failure that blocks decision 6.

| Gap | Owning file / symbol | Invariant | Test pattern | This pass |
|-----|----------------------|-----------|--------------|-----------|
| REL-CI-ASR | `tests/integration/test_e2e_real.py` `test_real_transcript_drives_moment_and_real_clip_renders`; produce log `whisper produced no JSON (rc=1)` | The nightly job must get a real whisper JSON from the pinned `tiny` model, then `awaiting.moments == 1`. | Extend `tests/integration/test_e2e_real.py` or the nightly workflow env. Do not change `doctor.py`. | Not a one-file fix. Class: **env**. Unauthenticated Hugging Face Hub, whisper rc=1. The `sample.wav` / `no_video_stream` line is ingest skipping the TTS sidecar; the source that errored is the mp4’s `src_…`. Greening it needs a cached model or `HF_TOKEN` on the nightly job, which is decision 3. |
| REL-CI-ARCH | `.github/workflows/architecture.yml` `schedule` + `workflow_dispatch`; reconciler calls `tools/arch` with `--require-live` | Monday 05:17 UTC must run `scheduled reconciliation`, or `workflow_dispatch` must be able to. | Workflow change only if the cron is wrong. It is not. | Do not edit. Cron `17 5 * * 1` has fired (latest failure `38253871938`). Failure is `gh` HTTP 403 on the live probe, a token permission, not “no Monday yet”. |
| REL-UNDEPLOYED | No product symbol. PR #1316 and `/_fingerprint`. | Fingerprint equals the merge SHA. | CI `unit (fast, no toolchain)`. | Blocked. The unit job is red across many files. Not started here. |
| OPS-POSTIZ, OPS-QUEUE | Doctor only. | Unchanged. | None in repo. | Operator. Decisions 1 and 2. |
| C6 | `post/run.py`, `reconcile.py` | Pack checklist stands. | No new test. | No new finding. |

## Part 4 — Decisions

1. **Queue (OPS-QUEUE).** Leave the 5 drainable posts deferred: no ledger write, doctor stays red, headline stays 78. Or run a controlled publish/reconcile (`fanops run` or Studio Publish now) after Go Live: due queued posts can POST, `needs_reconcile` parks rather than deletes, and the production ledger changes. Or park/retire the accounts so `_non_active_row` drops them out of the drainable count: that edits live account state (Studio or `00_control/accounts.json`, never committed) and does not wipe the 851 queued rows. Ledger wipe and ledger drain are not options.

2. **Postiz (OPS-POSTIZ).** Start local Docker and Postiz so the `/integrations` probe succeeds: doctor can go green for this row, and it does not by itself publish. Or leave Postiz deferred. Zernio is already `[ok] zernio: reachable` and `[PASS] Zernio backend auth ok` on the 2026-10-17 doctor. A Zernio-only exercise does not close OPS-POSTIZ.

3. **ASR nightly (REL-CI-ASR).** Fix the nightly so whisper returns JSON (cache or `HF_TOKEN`) and the job goes green. Or leave the score narrative deferred while `unit (fast, no toolchain)` is the required PR check. The pip-audit half of that workflow already succeeded.

4. **Trace and codemap (U0-F01, H-DOCS-F01).** Regenerate `docs/CODEMAPS/` traces so the 196-versus-124 gap is written as current prose. Cost is the U0 path (`full-trace-index.md`, `anomalies.md`, `partition.json`, `scripts/codemap_extract/`) plus `python -m tools.arch regen` if scanned source moved. The score ledger stays +0. Or keep `subsystem-traces/STALE.md`.

5. **C3-F01.** Fuse the two `extract_frames_grid` passes in `framing.py` on a picking branch (`clip.py` is a picking hot file). Or keep the dual 9fps/4fps pass accepted. Accepted does not change 78.

6. **Score target.** Ship at **78, capped at 84**, after #1316’s unit check is green, the PR is merged, the fingerprint equals the merge SHA, and a documented publish smoke exists for the 85+ sentence. Or pursue **90+** by also closing the deferred CI and infra rows (decisions 1–3, and the architecture `gh` 403). The score file does not define a 90 weight. 90+ here means those deferred rows are closed with new command output, not a rescaled ledger.

## Stop line

Blocking decisions: **1, 2, 3, 4, 5, 6**. No repo edit in this pass is implementable without one of them. The unit failure on #1316 is real and blocks decision 6’s merge, and it is many files, so it is not started here either.

Next wave: this file, then [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md) “Next action”. Re-measure HEAD before any product edit. The working tree on `c1d892df` is already dirty.
