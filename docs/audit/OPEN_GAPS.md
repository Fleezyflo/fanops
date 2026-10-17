# Open gaps

Single source for work that is not closed. A register row stays **open** until its closure steps below are done and the evidence column holds a fresh transcript. Ledger delta can stay 0. The status word is **open**.

Chosen closures already applied on this tree: [C3-F01](#c3-f01), [H-INT-F01](#h-int-f01), [U0-F01 / H-DOCS-F01](#u0-f01--h-docs-f01).

## REL-UNDEPLOYED

**Current evidence.** Closed. Merge commit `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` of PR #1316 is `origin/main`. On head `6f355d09b2dea3275101434d8c03c96bc7692d08`, which that merge contains, `unit (fast, no toolchain)` run `39073814011` passed (https://github.com/Fleezyflo/fanops/actions/runs/39073814011/job/117516718718), `impact report` run `39073814128` passed (https://github.com/Fleezyflo/fanops/actions/runs/39073814128/job/117516719132), and `lane file-ownership + cross-PR collision` run `39073814368` passed (https://github.com/Fleezyflo/fanops/actions/runs/39073814368/job/117516720083). `GET /_fingerprint` returned `{"generation":"246c325b4dc81a3106fce99abe982f90","label":"FanOps","pid":20339,"sha":"430ece7377bf33cc5c5e72876c13d6b78b8e6ca9","start_time":"2026-10-17T22:13:09.098405+00:00"}`. That `sha` equals the merge commit.

**Closure work.** Done.

**Who.** `both`

**Register row text after close.** Fingerprint SHA equals the merge commit of #1316, and `unit (fast, no toolchain)`, `impact report`, and `lane file-ownership + cross-PR collision` are green on that SHA.

## OPS-POSTIZ

**Current evidence.** 2026-10-17 `uv run fanops doctor` exit 1: probe `localhost:4007` `GET /api/public/v1/integrations` max retries; `[DOWN] docker: daemon down`; `[DOWN] postiz: unreachable`. Ledger was not drained. Repo probes stay `health_probes` / the X-DEP pack. `postiz.py` was not edited.

**Closure work.**

1. Operator: Docker up.
2. Postiz on `localhost:4007` answers `GET /api/public/v1/integrations`.
3. `uv run fanops doctor` shows Postiz up.
4. Repo: none beyond the doctor probes already in `health_probes`.

**Who.** `operator`

**Register row text after close.** A fresh `uv run fanops doctor` transcript shows Postiz up on `localhost:4007` `/api/public/v1/integrations`.

## OPS-QUEUE

**Current evidence.** 2026-10-17 `fanops doctor` exit 1: `5 drainable past-due (up to 463.9h); 76 parked on non-active accounts; 0 retired lineage; 851 total queued`. Ledger was not drained. `AGENTS.md` forbids a ledger wipe.

**Closure work.**

1. Operator choice, one of: controlled publish/reconcile (`fanops run` or Studio Publish after Go Live) so drainable past-due posts move; or park/retire accounts via Studio / `00_control/accounts.json` (never commit) so the drainable count hits 0.
2. Do not wipe the 851 queued rows.
3. Evidence: a fresh `fanops doctor` line for queue stats with drainable count 0.

**Who.** `operator`

**Register row text after close.** A fresh `fanops doctor` transcript shows 0 drainable past-due, and the 851 queued rows were not wiped.

## REL-CI-ASR

**Current evidence.** Nightly run `38868030626`, event `schedule`, conclusion `failure`, headSha `f65411d5806cce087a475261bf4c96c98f0bcf97` (`2026-10-16T07:33:59Z`). Job `[asr] toolchain smoke (nightly)` failed. `dependency audit (pip-audit)` succeeded. `tests/integration/test_e2e_real.py` line 68 expected `awaiting.moments == 1`; the log was `whisper produced no JSON (rc=1)`. `.github/workflows/nightly.yml` already exits when `secrets.HF_TOKEN` is empty on schedule (the require step and the pre-fetch / smoke steps). `doctor.py` was not changed.

**Closure work.**

1. `secrets.HF_TOKEN` is non-empty on the schedule event.
2. `[asr] toolchain smoke (nightly)` runs `python -m pytest -q -m "integration and asr"` and passes, including `awaiting.moments == 1` after real whisper JSON.
3. Repo option if CI can use a cached model without the Hub: set that in the nightly job env. Do not change `doctor.py`.

**Who.** `both`

**Register row text after close.** A nightly schedule run after this tree shows `[asr] toolchain smoke (nightly)` success and `awaiting.moments == 1`.

## REL-CI-ARCH

**Current evidence.** Schedule run `38253871938` failed because that tree treated branch-protection HTTP 403 as a hard fail. Cron is `17 5 * * 1`. `workflow_dispatch` is already declared. `checks.dc3_deployed_state` renders an unreadable probe as `[SKIP] DC-3` (`skipped=True`, not blocking). Committed HEAD `967b9c92` still puts that error in `cmd_deployed`'s hard list (`("DC-3", err)`), so `--require-live` exits 1. This working tree removes DC-3 from that list; only an unreadable DC-8 fails `--require-live`. Workflow permissions stay `contents: read` plus `actions: read` on the reconcile job. `GITHUB_TOKEN` only. No dispatch was run: the pushed SHA would repeat the 403 hard-fail. A dispatch URL does not exist yet.

**Closure work.**

1. Commit and push the `tools/ci/cli.py` change that leaves `hard = [("DC-8", wf_err)]`.
2. `gh workflow run architecture.yml --ref audit/production-audit-2026-09` (or `main` after merge).
3. The reconcile step runs `python -m tools.ci deployed --require-live`, prints `[SKIP] DC-3` on the 403, and exits 0 when DC-8 is clean.
4. Paste that run id here. Run `38253871938` is not this proof.

**Who.** `repo`

**Register row text after close.** A `workflow_dispatch` or schedule run of `architecture.yml` on this policy prints `[SKIP] DC-3` and exits 0.

## C3-F01

**Current evidence.** Closed on this tree. `uv run python -m pytest -q tests/test_framing_grid_fusion.py::test_ct_multi_resolve_one_real_ffmpeg` passed (1 passed). It drives `clip._resolve_framing` → `framing._resolve` → CT_MULTI on a generated mp4, stubs the detector as two faces, and counts one real `fanops.keyframes.subprocess.run` whose `-vf` is `fps=9.0` with `framing._KF_WIDTH`, the same `source_id`, and the same `cfg`. The stub count `test_ct_multi_resolve_extracts_one_grid` is still in the file and is not this close. `clip.py` was not edited.

**Closure work.** Done. The integration test above is the transcript.

**Who.** `repo`

**Register row text after close.** `test_ct_multi_resolve_one_real_ffmpeg` counts one real ffmpeg at `framing._FRAMING_GRID_FPS` (9.0) on the CT_MULTI path through `clip._resolve_framing`.

## H-INT-F01

**Current evidence.** Resolved. Chosen closure: keep the dual map. Operator edits of `reconcile.py` are C6 (publish). `partition.json` keeps `reconcile.py` in C7. `post/metrics/*` is C6 in the partition and C7 for operator audit. `partition.json` was not retagged.

**Closure work.** Done. Do not retag.

**Who.** `repo`

**Register row text after close.** Operator edits of `reconcile.py` are C6; `partition.json` keeps it in C7. No retag.

## U0-F01 / H-DOCS-F01

**Current evidence.** Closed by policy. `docs/CODEMAPS/subsystem-traces/STALE.md` stays. `full-trace-index.md` still records live count 196 and indexed stems 124. Traces were not regenerated. Score ledger delta is +0. Decision 4 is this choice, not an open regenerate-or-keep fork.

**Closure work.** Done. Do not regenerate `docs/CODEMAPS/` on this branch unless a later edit changes the stems.

**Who.** `repo`

**Register row text after close.** Closed by the stale mark in `subsystem-traces/STALE.md`. Live count 196, indexed stems 124. Traces were not regenerated.

## Cap ≥84

**Current evidence.** Closed. Headline stays **78**. Fingerprint `sha` is `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`, the merge commit of #1316. On head `6f355d09b2dea3275101434d8c03c96bc7692d08`, which that merge contains, `unit (fast, no toolchain)` run `39073814011` passed (https://github.com/Fleezyflo/fanops/actions/runs/39073814011/job/117516718718), `impact report` run `39073814128` passed (https://github.com/Fleezyflo/fanops/actions/runs/39073814128/job/117516719132), and `lane file-ownership + cross-PR collision` run `39073814368` passed (https://github.com/Fleezyflo/fanops/actions/runs/39073814368/job/117516720083). `GET /_fingerprint` returned `{"generation":"246c325b4dc81a3106fce99abe982f90","label":"FanOps","pid":20339,"sha":"430ece7377bf33cc5c5e72876c13d6b78b8e6ca9","start_time":"2026-10-17T22:13:09.098405+00:00"}`. That `sha` equals the merge commit. This does not close 85+.

**Closure work.** Done. The headline stays 78 until a ledger row moves.

**Who.** `both`

**Register row text after close.** Not a register row. The score caveat clears when the fingerprint equals the merge SHA and the required check is green on that SHA. The headline stays 78 until a ledger row moves.

## 85+

**Current evidence.** Not started. `post/run.py` `_post_provider` returns `"dryrun"` when the tree is not live. `docs/GOLIVE.md` dry-run posts nothing.

**Closure work.**

1. After the ≥84 merge: a live publish on the shipped SHA per `docs/GOLIVE.md` Path A, or the cutover probe.
2. Record that run here and in the score caveats. A dry-run does not count.

**Who.** `operator`

**Register row text after close.** Not a register row. The score records a live publish transcript on the shipped SHA.
