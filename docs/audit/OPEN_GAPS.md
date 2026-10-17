# Open gaps

[REL-CI-ASR](#rel-ci-asr) is the only **open** row. `gh secret list` on 2026-10-18 has no `HF_TOKEN`, so the nightly smoke was not re-run. Every other subsection is **closed** and cites its transcript.

## REL-UNDEPLOYED

**Current evidence.** Closed. Merge commit `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` of PR #1316 is `origin/main`. On head `6f355d09b2dea3275101434d8c03c96bc7692d08`, which that merge contains, `unit (fast, no toolchain)` run `39073814011` passed (https://github.com/Fleezyflo/fanops/actions/runs/39073814011/job/117516718718), `impact report` run `39073814128` passed (https://github.com/Fleezyflo/fanops/actions/runs/39073814128/job/117516719132), and `lane file-ownership + cross-PR collision` run `39073814368` passed (https://github.com/Fleezyflo/fanops/actions/runs/39073814368/job/117516720083). `GET /_fingerprint` returned `{"generation":"246c325b4dc81a3106fce99abe982f90","label":"FanOps","pid":20339,"sha":"430ece7377bf33cc5c5e72876c13d6b78b8e6ca9","start_time":"2026-10-17T22:13:09.098405+00:00"}`. That `sha` equals the merge commit.

**Closure work.** Done.

**Who.** `both`

**Register row text after close.** Fingerprint SHA equals the merge commit of #1316, and `unit (fast, no toolchain)`, `impact report`, and `lane file-ownership + cross-PR collision` are green on that SHA.

## OPS-POSTIZ

**Current evidence.** Closed. 2026-10-17 `postiz-ondemand.sh ensure` exit 0 (`postiz: already up`) on `FANOPS_ROOT` `/Users/molhamhomsi/FanOps`. Unauthenticated `GET http://localhost:4007/api/public/v1/integrations` returned HTTP 401 `{"msg":"No API Key found"}` with `X-Powered-By: Express`. `uv run fanops doctor`: `[PASS] Postiz backend reachable (real /integrations probe, not the nginx health-check)`, `[ok] docker: daemon up`, `[ok] postiz: reachable`. `postiz.py` was not edited. Ledger was not wiped.

**Closure work.** Done.

**Who.** `closed`

**Register row text after close.** A fresh `uv run fanops doctor` transcript shows Postiz up on `localhost:4007` `/api/public/v1/integrations`.

## OPS-QUEUE

**Current evidence.** Closed. 2026-10-17T22:18Z `uv run fanops doctor` on `/Users/molhamhomsi/FanOps`: `[PASS] publish daemon alive + queue draining (heartbeat + past-due backlog)`. The same drainable loop doctor uses: `0 drainable past-due (up to 0.0h); 81 parked on non-active accounts; 0 retired lineage; 837 total queued`. Before that doctor the line was `3 drainable past-due (up to 485.9h); 78 parked on non-active accounts; 0 retired lineage; 838 total queued`. The three stale rows (`post_507a879b8b49`, `post_613357845954`, `post_10e9099dc150`) were not mass-published. `markmakmouly` and `cisumwolfhom` went from `active` to `planned` in local `00_control/accounts.json` (not committed). Queued rows were not deleted. 837 remains. Rollback is those two statuses back to `active`. Doctor still exits 1 on the daemon SHA check (`f65411d5806c` vs disk `94bdd3e53d32`). That check was not restarted.

**Closure work.** Done.

**Who.** `closed`

**Register row text after close.** A fresh `fanops doctor` transcript shows 0 drainable past-due, and the queued rows were not wiped.

## REL-CI-ASR

**Current evidence.** Open. Nightly run `38868030626`, event `schedule`, conclusion `failure`, headSha `f65411d5806cce087a475261bf4c96c98f0bcf97` (`2026-10-16T07:33:59Z`). Job `[asr] toolchain smoke (nightly)` failed. `tests/integration/test_e2e_real.py` expected `awaiting.moments == 1`; the log was `whisper produced no JSON (rc=1)`. 2026-10-18 `gh secret list` for `Fleezyflo/fanops` has no `HF_TOKEN`. The nightly was not dispatched. `doctor.py` was not changed.

**Closure work.**

1. `secrets.HF_TOKEN` is non-empty on the schedule event.
2. `[asr] toolchain smoke (nightly)` runs `python -m pytest -q -m "integration and asr"` and passes, including `awaiting.moments == 1` after real whisper JSON.

**Who.** `open`

**Register row text after close.** A nightly schedule run after this tree shows `[asr] toolchain smoke (nightly)` success and `awaiting.moments == 1`.

## REL-CI-ARCH

**Current evidence.** Closed. `workflow_dispatch` run `39074333775` (https://github.com/Fleezyflo/fanops/actions/runs/39074333775) on `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` concluded success. The reconcile step ran `python -m tools.ci deployed --require-live` and printed `[SKIP] DC-3 · -: NON-AUTHORITATIVE: live protection unreadable (gh: Resource not accessible by integration (HTTP 403))`. No `[FAIL] DC-8`. Run `38253871938` is not this proof. Run `39073191339` failed on `2a60aec4` because `GH_TOKEN` was unset; the merged workflow maps `GH_TOKEN: ${{ github.token }}` on that step.

**Closure work.** Done.

**Who.** `closed`

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

**Current evidence.** Closed. On merge `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`, `cfg.is_live` was already true (`FANOPS_LIVE=1`; doctor `poster backend: postiz, zernio (LIVE)`). `publish_now` sent `post_5854bb63fefb` (Instagram, `markmakmouly`). `_post_provider` returned `postiz`. Postiz accepted it at `2026-10-17T22:16:23Z` as `needs_reconcile` / submission `cmvcyhksd0008ld7veu7hchr9`. Log: `outcome=publish_missing_url`, `backend=postiz`. Not a dry-run.

**Closure work.** Done.

**Who.** `closed`

**Register row text after close.** Not a register row. The score records a live publish transcript on the shipped SHA.
