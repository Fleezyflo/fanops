# Open gaps

**No open rows.** Zero open subsections. Assessment date 2026-10-18. Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. Every subsection is **closed** or **resolved** and cites its transcript.

## REL-UNDEPLOYED

**Current evidence.** Closed. Merge commit `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` of PR #1316 is `origin/main`. On head `6f355d09b2dea3275101434d8c03c96bc7692d08`, which that merge contains, `unit (fast, no toolchain)` run `39073814011` passed (https://github.com/Fleezyflo/fanops/actions/runs/39073814011/job/117516718718), `impact report` run `39073814128` passed (https://github.com/Fleezyflo/fanops/actions/runs/39073814128/job/117516719132), and `lane file-ownership + cross-PR collision` run `39073814368` passed (https://github.com/Fleezyflo/fanops/actions/runs/39073814368/job/117516720083). `GET /_fingerprint` on 2026-10-18 returned `{"generation":"246c325b4dc81a3106fce99abe982f90","label":"FanOps","pid":65307,"sha":"430ece7377bf33cc5c5e72876c13d6b78b8e6ca9","start_time":"2026-10-17T22:25:22.259438+00:00"}`. That `sha` equals the merge commit. Pump `daemon._last_heartbeat_code` and the live-checkout disk are the same SHA.

**Closure work.** Done.

**Who.** `both`

**Register row text after close.** Fingerprint SHA equals the merge commit of #1316, and `unit (fast, no toolchain)`, `impact report`, and `lane file-ownership + cross-PR collision` are green on that SHA.

## OPS-POSTIZ

**Current evidence.** Closed. 2026-10-18 `uv run fanops doctor`: `[PASS] Postiz backend reachable (real /integrations probe, not the nginx health-check)`, `[ok] docker: daemon up`, `[ok] postiz: reachable`. Unauthenticated `GET http://localhost:4007/api/public/v1/integrations` returned HTTP 401 `{"msg":"No API Key found"}` with `X-Powered-By: Express`. `postiz.py` was not edited. Ledger was not wiped.

**Closure work.** Done.

**Who.** `closed`

**Register row text after close.** A fresh `uv run fanops doctor` transcript shows Postiz up on `localhost:4007` `/api/public/v1/integrations`.

## OPS-QUEUE

**Current evidence.** Closed. 2026-10-18 the drainable loop in `doctor._daemon_liveness_check` counted `0 drainable past-due (up to 0.0h); 83 parked on non-active accounts; 0 retired lineage; 837 total queued`. Doctor’s queue check was `[PASS] publish daemon alive + queue draining (heartbeat + past-due backlog)`. Queued rows were not deleted. 837 remains. Parked was 82 on the prior sheet and 81 on 2026-10-17. Doctor exit 1 is `doctor._deploy_code_check`: pump `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` versus this branch’s disk `aab9de18879d29166c219191ef45b65cac15e4d9` (`daemon._version_signal`). The live checkout matches the pump. The pump was not restarted.

**Closure work.** Done.

**Who.** `closed`

**Register row text after close.** A fresh `fanops doctor` transcript shows 0 drainable past-due, and the queued rows were not wiped.

## REL-CI-ASR

**Current evidence.** Resolved. Not a production open gap. `nightly.yml` job `asr-smoke` (`[asr] toolchain smoke (nightly)`) is scheduled CI hygiene. It is not in `.github/ci-control-registry.yml` `required_contexts`. Merge gate **REL-PROT** is only `unit (fast, no toolchain)`, green on subject push run `39074067963` at `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. Failure run `39012855146` (`2026-10-17T07:13:50Z`, event `schedule`, headSha `f65411d5806cce087a475261bf4c96c98f0bcf97`) is monitoring on that old SHA: `tests/integration/test_e2e_real.py` expected `awaiting.moments == 1` and the log was `whisper produced no JSON (rc=1)`. Product runtime does not consume a hub token. `git grep -E 'HF_TOKEN|huggingface' src/` is empty. `fanops._fwrun._load_model` constructs `WhisperModel` with the model name, `device`, and `compute_type` only. `doctor._assemble_doctor_checks` probes `transcribe._fw_available` (`[PASS] faster-whisper importable ([asr] extra)` on 2026-10-18). `docs/ENFORCEMENT.md` documents `nightly.yml` `asr-smoke` and the Actions secret as GitHub Actions only, not product `CONFIG.md`. The nightly was not dispatched. `doctor.py` was not changed.

**Closure work.** Done.

**Who.** `repo`

**Register row text after close.** Resolved. Scheduled `nightly.yml` `asr-smoke` is CI hygiene on an old SHA, not a ship blocker. Product code does not read a hub token (`fanops._fwrun`, `doctor` faster-whisper probe, empty `src/` grep).

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

**Current evidence.** Closed. Headline stays **78**. Fingerprint `sha` is `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`, the merge commit of #1316. `unit (fast, no toolchain)` on the subject push run `39074067963` passed. `GET /_fingerprint` on 2026-10-18 returned `{"generation":"246c325b4dc81a3106fce99abe982f90","label":"FanOps","pid":65307,"sha":"430ece7377bf33cc5c5e72876c13d6b78b8e6ca9","start_time":"2026-10-17T22:25:22.259438+00:00"}`. That `sha` equals the merge commit. Pump `daemon._last_heartbeat_code` and the live-checkout disk are the same SHA. This does not close 85+.

**Closure work.** Done. The headline stays 78 until a ledger row moves.

**Who.** `both`

**Register row text after close.** Not a register row. The score caveat clears when the fingerprint equals the merge SHA and the required check is green on that SHA. The headline stays 78 until a ledger row moves.

## 85+

**Current evidence.** Closed. On merge `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`, `cfg.is_live` was already true (`FANOPS_LIVE=1`; 2026-10-18 doctor `poster backend: postiz, zernio (LIVE)`). `publish_now` sent `post_5854bb63fefb` (Instagram). `_post_provider` returned `postiz`. Log `2026-10-17T22:16:23Z`: `outcome=publish_missing_url`, `backend=postiz`. Not a dry-run. On 2026-10-18 the row is still `needs_reconcile`, with a submission id and no `public_url`.

**Closure work.** Done.

**Who.** `closed`

**Register row text after close.** Not a register row. The score records a live publish transcript on the shipped SHA.
