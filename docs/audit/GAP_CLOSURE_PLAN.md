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

- **Ceiling, not a subtraction.** The production-audit skill caps the report at **84** when CI is not green or the launch-critical path was not tested end to end. The ledger headline stays **78** (+6 over the committed **72** on `d2cded31`). **78, capped at 84** means the number stays 78 until more ledger rows close. The ≥84 fingerprint condition and the 85+ live publish are recorded. The headline stays 78. **REL-CI-ASR** is the open row and is ledger delta **0**. The deploy, ops, architecture, and live-publish transcripts are closed in [OPEN_GAPS.md](OPEN_GAPS.md). The **69** secrets cap does not bind.
- **Two binding conditions.** Both are closed. Fingerprint sha `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` equals the merge of #1316. Live publish `post_5854bb63fefb` went through Postiz on that SHA. **REL-CI-ASR** stays open and is not a cap mechanic. **REL-CI-ARCH** is closed on run `39074333775`.
- **What lifts ≥84 versus 85+.** Both transcripts exist. Fingerprint equals merge `430ece73` and unit run `39073814011` passed. `post_5854bb63fefb` is the live Postiz publish on that SHA. Neither raises 78 without a new ledger row.

The committed baseline on `d2cded31` is **72/100**. This branch is **78/100**. The cap is a ceiling on that 78. The five open rows are delta 0. Neither is a drop from 72.

Nothing in this pass drains the ledger, starts Docker, or merges.

## Part 1 — Why the headline is 78, not 100

| Gap class | ID or rule | Moves the headline? | Evidence |
|-----------|------------|---------------------|----------|
| Cap | **84** | No. The headline stays 78. The cap is the ceiling until the conditions in the score’s “Path to ≥84” and “85+” sentences are true. | [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md) score ledger cap row and the 84-cap paragraph: this tree is not fingerprint `f65411d5`; launch-critical publish was not re-run end to end on the scored SHA. |
| Closed, ledger delta 0 | **REL-UNDEPLOYED** | Yes. | Fingerprint equals merge `430ece73`. [OPEN_GAPS.md](OPEN_GAPS.md#rel-undeployed). |
| Closed, ledger delta 0 | **OPS-POSTIZ** | Yes. | 2026-10-17 doctor: Postiz reachable, docker up. |
| Closed, ledger delta 0 | **OPS-QUEUE** | Yes. | `0 drainable past-due`; 837 queued, not wiped. |
| Open, ledger delta 0 | **REL-CI-ASR** | No. | Nightly run `38868030626` failed (`whisper produced no JSON (rc=1)`). `HF_TOKEN` is absent. |
| Closed, ledger delta 0 | **REL-CI-ARCH** | Yes. | Run `39074333775` printed `[SKIP] DC-3` (HTTP 403) and exited 0. |
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

**Repo work on the merged SHA:** call-time `views_review` imports, `postiz.py` and `reconcile.py` matching `origin/main`, and the DC-8-only `cmd_deployed` hard list are in merge `430ece73`. C3-F01 is closed by `test_ct_multi_resolve_one_real_ffmpeg`. REL-CI-ASR still needs `HF_TOKEN` on the nightly job. The fingerprint equals that merge.

| Gap | Owning file / symbol | Invariant | Test pattern | This pass |
|-----|----------------------|-----------|--------------|-----------|
| REL-CI-ASR | `tests/integration/test_e2e_real.py` `test_real_transcript_drives_moment_and_real_clip_renders`; produce log `whisper produced no JSON (rc=1)` | The nightly job must get a real whisper JSON from the pinned `tiny` model, then `awaiting.moments == 1`. | Extend `tests/integration/test_e2e_real.py` or the nightly workflow env. Do not change `doctor.py`. | Not a one-file fix. Class: **env**. Unauthenticated Hugging Face Hub, whisper rc=1. The `sample.wav` / `no_video_stream` line is ingest skipping the TTS sidecar; the source that errored is the mp4’s `src_…`. Greening it needs a cached model or `HF_TOKEN` on the nightly job, which is decision 3. |
| REL-CI-ARCH | `tools/ci/cli.py` `cmd_deployed`; `.github/workflows/architecture.yml` | Unreadable DC-3 prints `[SKIP] DC-3` and does not fail `--require-live`. | Dispatch on the merged SHA. | Closed. Run `39074333775`. |
| REL-UNDEPLOYED | PR #1316 and `/_fingerprint`. | Fingerprint equals the merge SHA. | CI `unit (fast, no toolchain)`. | Closed. Fingerprint `430ece73`. Unit run `39073814011`. |
| OPS-POSTIZ, OPS-QUEUE | Doctor only. | Postiz up. Drainable past-due 0. Queued rows kept. | None in repo. | Closed. 2026-10-17 doctor. |
| C6 | `post/run.py`, `reconcile.py` | Pack checklist stands. | No new test. | No new finding. |

## Part 4 — Decisions

1. **Queue (OPS-QUEUE).** Closed. Doctor: `0 drainable past-due`; 837 queued. `markmakmouly` and `cisumwolfhom` are `planned` locally. [OPEN_GAPS.md](OPEN_GAPS.md#ops-queue).

2. **Postiz (OPS-POSTIZ).** Closed. Doctor: Postiz reachable, docker up. [OPEN_GAPS.md](OPEN_GAPS.md#ops-postiz).

3. **ASR nightly (REL-CI-ASR).** Open. `secrets.HF_TOKEN` is absent. Nightly `38868030626` failed. Do not change `doctor.py`. [OPEN_GAPS.md](OPEN_GAPS.md#rel-ci-asr).

4. **Trace and codemap (U0-F01, H-DOCS-F01).** Closed by policy. `subsystem-traces/STALE.md` stays. Live count 196, indexed stems 124. Traces were not regenerated.

5. **C3-F01.** Closed. `test_ct_multi_resolve_one_real_ffmpeg` counts one real ffmpeg at `framing._FRAMING_GRID_FPS` (9.0) on the CT_MULTI path. `clip.py` was not edited. This does not change 78.

6. **Score target.** Closed on the evidence. Fingerprint equals merge `430ece73`. Unit run `39073814011` passed. **85+** is the Postiz publish of `post_5854bb63fefb` ([OPEN_GAPS.md](OPEN_GAPS.md#85)). The headline stays 78.

## Stop line

Open: **3** (REL-CI-ASR). Decisions **1**, **2**, **4**, **5**, and **6** are closed. REL-CI-ARCH is closed on run `39074333775`. Fingerprint sha is `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`.

Next wave: the open rows in [OPEN_GAPS.md](OPEN_GAPS.md). Re-measure HEAD before the next product edit.
