# Gap closure plan

Operator plan for the production-audit score. It does not assign new point weights. The only numeric steps already written are in [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md): headline **78**, cap **84**, **85+** after an end-to-end publish on the shipped SHA. The score ledger’s per-row deltas stay there.

Re-measured 2026-10-17 after PR #1316 existed:

| Fact | Value |
|------|--------|
| `git rev-parse HEAD` | `c1d892df86a02e57e3a9243109a00702c1bdfce8` |
| Branch | `audit/production-audit-2026-09` tracking its origin. Working tree is dirty on top of that SHA. |
| PR #1316 | `OPEN`. `headRefOid` equals HEAD. |
| Required check | `unit (fast, no toolchain)` **pass** on run `39021062319`. An older rollup `39019468576` failed. Protection still requires only that context (`REL-PROT`). |
| Also red on the latest rollup | `impact report` (run `39021062321`) and `lane file-ownership + cross-PR collision` (run `39021062324`). Neither is the required context. |
| `GET /_fingerprint` | `f65411d5806cce087a475261bf4c96c98f0bcf97` (pid 1225, generation `246c325b4dc81a3106fce99abe982f90`) |

## Why capped at 84

- **Ceiling, not a subtraction.** The production-audit skill caps the report at **84** when CI is not green or the launch-critical path was not tested end to end. The ledger headline stays **78** (+6 over the committed **72** on `d2cded31`). **78, capped at 84** means the number stays 78 until more ledger rows close. The product is not **85+** launch-verified until the cap conditions clear. The five open rows (**REL-UNDEPLOYED**, **OPS-POSTIZ**, **OPS-QUEUE**, **REL-CI-ASR**, **REL-CI-ARCH**) are ledger delta **0**. Steps are [OPEN_GAPS.md](OPEN_GAPS.md). They are caveats. They are not why the sheet says capped at 84. The **69** secrets cap does not bind.
- **Two binding conditions.** **REL-UNDEPLOYED:** the scored tree is not what Studio serves. Fingerprint `f65411d5806cce087a475261bf4c96c98f0bcf97`. PR #1316 head `c1d892df86a02e57e3a9243109a00702c1bdfce8`. **Launch-critical publish** was not re-run end to end on the scored SHA. Dry-run does not count: `docs/GOLIVE.md` posts nothing in dry-run, and `post/run.py` `_post_provider` returns `"dryrun"` when the tree is not live. **REL-CI-E2E** closed on `f65411d5` does not lift this cap. **REL-CI-ASR** and **REL-CI-ARCH** stay open and have no cap mechanics.
- **What lifts ≥84 versus 85+.** **≥84** is the score’s “Path to ≥84”: merge so the fingerprint equals the merge SHA, and `unit (fast, no toolchain)` is green. That clears the deploy mismatch in the cap narrative. It does not raise 78 without new ledger rows. **85+** also needs a documented **live** publish smoke on that shipped SHA (“Path to 100” in [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md); live versus dry-run in `src/fanops/post/CLAUDE.md` and `docs/GOLIVE.md`).

The committed baseline on `d2cded31` is **72/100**. This branch is **78/100**. The cap is a ceiling on that 78. The five open rows are delta 0. Neither is a drop from 72.

Nothing in this pass drains the ledger, starts Docker, or merges.

## Part 1 — Why the headline is 78, not 100

| Gap class | ID or rule | Moves the headline? | Evidence |
|-----------|------------|---------------------|----------|
| Cap | **84** | No. The headline stays 78. The cap is the ceiling until the conditions in the score’s “Path to ≥84” and “85+” sentences are true. | [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md) score ledger cap row and the 84-cap paragraph: this tree is not fingerprint `f65411d5`; launch-critical publish was not re-run end to end on the scored SHA. |
| Open, ledger delta 0 | **REL-UNDEPLOYED** | No. | PR #1316, fingerprint `f65411d5`. Closes only when the merge SHA is the fingerprint. [OPEN_GAPS.md](OPEN_GAPS.md#rel-undeployed). |
| Open, ledger delta 0 | **OPS-POSTIZ** | No. | 2026-10-17 doctor: `localhost:4007` `/api/public/v1/integrations` max retries; `[DOWN] docker: daemon down`; `[DOWN] postiz: unreachable`. |
| Open, ledger delta 0 | **OPS-QUEUE** | No. | Same doctor: `5 drainable past-due (up to 463.9h); 76 parked on non-active accounts; 0 retired lineage; 851 total queued`. Ledger delta 0. The ledger was not wiped. |
| Open, ledger delta 0 | **REL-CI-ASR** | No. | Nightly run `38868030626`, schedule, failure, head `f65411d5`. Job `[asr] toolchain smoke (nightly)` failed. `dependency audit (pip-audit)` succeeded. Log: `tests/integration/test_e2e_real.py:68` `assert 0 == 1`, and produce `whisper produced no JSON (rc=1)`. The moments count is 0 because whisper returned no JSON. |
| Open, ledger delta 0 | **REL-CI-ARCH** | No. | Schedule run `38253871938` failed the old 403 hard-fail. Working tree `cmd_deployed` no longer escalates an unreadable DC-3. No dispatch transcript yet. [OPEN_GAPS.md](OPEN_GAPS.md#rel-ci-arch). |
| Closed | **C3-F01** | No. Not in the +6. | `test_ct_multi_resolve_one_real_ffmpeg` counts one real ffmpeg at 9.0 on the CT_MULTI path. `clip.py` was not edited. |
| Resolved | **H-INT-F01** | No. | Operator edits of `reconcile.py` are C6. `partition.json` keeps C7. No retag. |
| Closed by stale mark | **U0-F01**, **H-DOCS-F01** | No. Score ledger records both as +0. | `docs/CODEMAPS/subsystem-traces/STALE.md`. Live Python count 196, indexed stems 124. This is the closure. Traces were not regenerated. |

**C3-F01 is closed.** `test_ct_multi_resolve_one_real_ffmpeg` counts one real `subprocess.run` at `framing._FRAMING_GRID_FPS` (9.0) on the CT_MULTI path. The stub count remains in the file and is not the close. `clip.py` was not edited. That does not change 78.

**H-INT-F01 is resolved.** Operator edits of `reconcile.py` are C6. `partition.json` keeps C7. No retag. That does not change 78.

**U0-F01 and H-DOCS-F01 are closed by policy.** `subsystem-traces/STALE.md` stays. Live count 196, indexed stems 124. Traces were not regenerated. The score ledger is +0. There is no remaining regenerate-or-keep choice on this branch.

## Part 2 — What remains

### A. Merge and deploy — REL-UNDEPLOYED

PR #1316 must go green on `unit (fast, no toolchain)`, then merge, then `GET /_fingerprint` must equal that merge SHA. A merge is the deploy. Do not restart launchd by hand.

The required check on the pushed head passed: run `39021062319`. Impact report `39021062321` and lane collision `39021062324` stay red on that push. This working tree puts the `views_review` imports back at call time (`impact --strict` exit 0) and restores `postiz.py` and `reconcile.py` to `origin/main`. Those checks go green only after that tree is pushed. Steps: [OPEN_GAPS.md](OPEN_GAPS.md#rel-undeployed).

### B. Operator and infra — OPS-POSTIZ, OPS-QUEUE

Doctor is red because Docker is down and Postiz on `localhost:4007` is unreachable, and because 5 queued posts are drainable (851 queued total). `AGENTS.md` forbids wiping or draining the ledger as a repair. This pass does not start Docker and does not publish.

### C. CI — REL-CI-ASR, REL-CI-ARCH

ASR: classified below. Not a doctor change.

Architecture: schedule run `38253871938` failed because that tree treated a branch-protection HTTP 403 as a hard fail. `checks.dc3_deployed_state` already renders `[SKIP] DC-3`. Committed HEAD still hard-fails that error under `--require-live`. This working tree removes DC-3 from that hard list. No dispatch was run against the pushed SHA, because that SHA would fail the same way. The row stays open until a dispatch or schedule transcript on the working-tree policy exits 0. [OPEN_GAPS.md](OPEN_GAPS.md#rel-ci-arch).

### D. Verification — 85+

[PRODUCTION_SCORE.md](PRODUCTION_SCORE.md) says **85+** needs an end-to-end publish on the shipped SHA. That SHA is the fingerprint after merge, not this branch.

`docs/GOLIVE.md` section “1. Produce content offline” runs `fanops advance` through the queue and states that dry-run writes payloads and posts nothing. `src/fanops/post/CLAUDE.md`: `_post_provider` returns `"dryrun"` when `not cfg.is_live`, and `_publish_one` is the only network POST. A dry-run does not satisfy the 85+ sentence.

A live post is `docs/GOLIVE.md` Path A step “GO LIVE — publish for real”, or the “Validate learning” probe (one 2099-scheduled throwaway, state in `00_control/cutover.json`, not a ledger wipe). Either one is an operator action after the fingerprint matches. This pass does not publish.

### E. Optional engineering debt

Only rows that are still accepted or explicitly stale, each with a symbol. Closed register rows are not reopened here.

| Item | Symbol | Owner path |
|------|--------|------------|
| H-INT-F01 | `partition.json` versus operator C6 for `reconcile.py` | Resolved. No retag. |
| U0-F01, H-DOCS-F01 | `docs/CODEMAPS/subsystem-traces/STALE.md` | Closed by the stale mark. |

No new C6 finding. `docs/audit/packs/C6-crosspost-publish.md` “Full checklist 2026-10-17” still says dry-run does not POST, `needs_reconcile` is not a publish input, Zernio reuses `x-request-id`, and Instagram/TikTok plus Zernio `content` are `posted_text_for` while Postiz YouTube `content` is `Post.caption`. This plan does not re-audit `post/run.py` or `reconcile.py`. A contradiction check belongs on the merged SHA, which does not exist yet.

## Part 3 — Repo work versus not

**Repo work still open on the pushed SHA:** the `views_review` call-time imports, the restore of `postiz.py` and `reconcile.py` to `origin/main`, and the `cmd_deployed` hard list that drops unreadable DC-3. Those are in this working tree and are not on the remote until committed. C3-F01 is closed by `test_ct_multi_resolve_one_real_ffmpeg`. REL-CI-ASR still needs `HF_TOKEN` or a cached model on the nightly job. The required unit check on the pushed head passed; the fingerprint is still `f65411d5`.

| Gap | Owning file / symbol | Invariant | Test pattern | This pass |
|-----|----------------------|-----------|--------------|-----------|
| REL-CI-ASR | `tests/integration/test_e2e_real.py` `test_real_transcript_drives_moment_and_real_clip_renders`; produce log `whisper produced no JSON (rc=1)` | The nightly job must get a real whisper JSON from the pinned `tiny` model, then `awaiting.moments == 1`. | Extend `tests/integration/test_e2e_real.py` or the nightly workflow env. Do not change `doctor.py`. | Not a one-file fix. Class: **env**. Unauthenticated Hugging Face Hub, whisper rc=1. The `sample.wav` / `no_video_stream` line is ingest skipping the TTS sidecar; the source that errored is the mp4’s `src_…`. Greening it needs a cached model or `HF_TOKEN` on the nightly job, which is decision 3. |
| REL-CI-ARCH | `tools/ci/cli.py` `cmd_deployed`; `.github/workflows/architecture.yml` `workflow_dispatch` | Unreadable DC-3 prints `[SKIP] DC-3` and does not fail `--require-live`. DC-8 still fails when unreadable. A protection payload that was read still fails when contexts differ. | Dispatch after the hard-list change is on the ref. | Open. Run `38253871938` is the old failure. No new transcript. |
| REL-UNDEPLOYED | No product symbol. PR #1316 and `/_fingerprint`. | Fingerprint equals the merge SHA. | CI `unit (fast, no toolchain)`. | The required unit check passed on run `39021062319`. Fingerprint is still `f65411d5`. Merge is the deploy. |
| OPS-POSTIZ, OPS-QUEUE | Doctor only. | Unchanged. | None in repo. | Operator. Decisions 1 and 2. |
| C6 | `post/run.py`, `reconcile.py` | Pack checklist stands. | No new test. | No new finding. |

## Part 4 — Decisions

1. **Queue (OPS-QUEUE).** Open. Run a controlled publish/reconcile (`fanops run` or Studio Publish after Go Live), or park/retire accounts via Studio / `00_control/accounts.json` (never commit), until doctor’s drainable count is 0. Do not wipe the 851 queued rows. Headline stays 78 either way. [OPEN_GAPS.md](OPEN_GAPS.md#ops-queue).

2. **Postiz (OPS-POSTIZ).** Open. Start local Docker and Postiz so the `/integrations` probe succeeds. A Zernio-only exercise does not close this row. [OPEN_GAPS.md](OPEN_GAPS.md#ops-postiz).

3. **ASR nightly (REL-CI-ASR).** Open. `secrets.HF_TOKEN` non-empty on schedule, or a cached model in the nightly job env, until whisper returns JSON and `awaiting.moments == 1`. Do not change `doctor.py`. [OPEN_GAPS.md](OPEN_GAPS.md#rel-ci-asr).

4. **Trace and codemap (U0-F01, H-DOCS-F01).** Closed by policy. `subsystem-traces/STALE.md` stays. Live count 196, indexed stems 124. Traces were not regenerated.

5. **C3-F01.** Closed. `test_ct_multi_resolve_one_real_ffmpeg` counts one real ffmpeg at `framing._FRAMING_GRID_FPS` (9.0) on the CT_MULTI path. `clip.py` was not edited. This does not change 78.

6. **Score target.** Ship at **78, capped at 84**, after #1316 merges, the fingerprint equals the merge SHA, and the required check is green on that SHA. **85+** needs a live publish on that SHA ([OPEN_GAPS.md](OPEN_GAPS.md#85)). Closing the open rows is extra transcripts, not a rescaled ledger.

## Stop line

Open: **1, 2, 3, 6**, plus REL-CI-ARCH. Decision **4** is closed by the stale mark. Decision **5** is closed by `test_ct_multi_resolve_one_real_ffmpeg`. The required unit check passed on run `39021062319`. The fingerprint is `f65411d5`.

Next wave: the open rows in [OPEN_GAPS.md](OPEN_GAPS.md). Re-measure HEAD before the next product edit.
