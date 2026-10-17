# FanOps production score (working tree on `d2cded31`)

Production audit: 78/100, launchable with caveats, because the code rows on this tree are closed or accepted and the remaining rows are deploy gates on fingerprint `f65411d5`.

The committed score on `d2cded31` was 72/100 and still listed C1-F01, C7-F01, and C9-F01 as open fixes. Those symbols do not match that prose. C7-F01 is closed because `meta_graph._read_queries` is absent. C9-F01 is closed because `actions_wipe.confirm_wipe` checks `preview_token`. C2-F01 through C6-F01 and C8-F01 are closed on the symbols named in the register. This score does not repeat them as open work.

The 84 cap binds. Launch-critical publish was not re-run end to end on this scored tree. The 69 secrets cap does not bind: `FANOPS_IG_SCRAPE_PASSWORD` is masked by `config_introspect._is_secret`, and this pass did not find the value in a client bundle, a log, or a committed file. Rollback remains the “Rollback / safety” section in `docs/GOLIVE.md`.

## Score trajectory

The numeric score on this tree is **78** against the committed **72** on the same base `d2cded31`. That is +6. The production-audit skill’s **84 cap** applies until publish is exercised end to end on the scored SHA. Scheduled CI on the deploy SHA `f65411d5` has been re-read: **REL-CI-E2E** closed (run `38876056441` succeeded); **REL-CI-ASR** stays deferred (run `38868030626` failed); **REL-CI-ARCH** stays deferred (no `schedule` event in the last architecture runs). Those deferred rows are deploy and infra gates. They are not deductions from the code-remediation delta, and they are not blockers. Every code row in the register is closed or accepted, and each cites a symbol. **C2-F03** is closed: `config.isolate_vocals` states that a missing or failed Demucs run raises `ToolchainMissingError` and the mix is not transcribed, matching `vocals.isolate_vocals`.

It is 78 rather than 72 because the code rows that were actually open are closed (`get_logger` on the wipe path, scrape-password masking, `.env` file lock, Zernio auth health, deploy-SHA miss path). It is under 84 because this tree is not what Studio is serving and its publish path was not re-tested end to end.

## Score ledger

The headline did not fall. Commit `d2cded31` scores **72/100** (`git show d2cded31:docs/audit/PRODUCTION_SCORE.md`, 2026-09-12). The uncommitted re-verdict of `088702ca` is not a score file at that SHA and is not what Studio serves. This branch scores **78/100**, which is **+6**.

The prior pass stated +6 for five code closures and did not publish per-row weights. This ledger attributes **+2** to C1 (the only 72-sheet high-value item whose code changed) and **+1** each to the four closures that were not on the 72 sheet. C7-F01 and C9-F01 were already true on `d2cded31`; closing that stale prose is **+0**.

| Step | Delta | Running | Basis |
|------|------:|--------:|-------|
| Committed 2026-09-12 sheet | — | 72 | C1-F01, C7-F01, C9-F01, H-DOCS-F01 listed as open high-value fixes |
| C7-F01, C9-F01 | +0 | 72 | Symbols already match a close on `d2cded31` (`_read_queries` absent; `confirm_wipe` checks `preview_token`) |
| C1-F01 | +2 | 74 | `ledger_wipe` unrestorable and manifest-failure paths call `get_logger(cfg)` |
| SEC-IGPW | +1 | 75 | `FANOPS_IG_SCRAPE_PASSWORD` masked; `_PASSWORD` is a secret suffix. Value not found in a client bundle, log, or commit |
| SEC-ENV | +1 | 76 | `.env` rewrite holds `ledger._file_lock` |
| OPS-ZERNIO | +1 | 77 | `zernio_dep_health` follows the authenticated doctor check. Any-HTTP-up is no longer the Zernio verdict |
| OPS-SHA (doctor) | +1 | 78 | Loaded daemon fails `_deploy_code_check` when either SHA is missing or the two differ |
| C2-F04 | +0 | 78 | Opened and closed this pass. Native scan was minting `01_inbox/.pull` files as `drop`; the scan now skips that child stage |
| C3-F03 | +0 | 78 | Opened and closed this pass. An empty rc-0 temp no longer publishes over the previous mp4 |
| C4-F03 | +0 | 78 | Opened and closed this pass. A Latin hook on a non-en/ar source is no longer nulled at ingest |
| C4-F04 | +0 | 78 | Opened and closed this pass. Studio `regenerate_hook` uses `_hook_script_mismatch` |
| C9-F03 | +0 | 78 | Opened and closed this pass. An imminent queued post is refused before `render_moment` |
| C10-F04 | +0 | 78 | Opened and closed this pass. A failed card or feed swap re-renders the card with the error inside it |
| C5-F02 | +0 | 78 | Opened and closed this pass. A miss-only source-tag pass writes the sidecar so the next tick moves on |
| H-DOCS-F01, U0-F01 | +0 | 78 | Stale-mark closure. Traces were not regenerated |
| Deferred, no points | 0 | 78 | OPS-POSTIZ, OPS-QUEUE, REL-UNDEPLOYED, REL-CI-ASR, REL-CI-ARCH stay deferred with command output in the register |
| REL-PROT, REL-CI-E2E | 0 | 78 | Closed 2026-10-17 on fingerprint `f65411d5`. They do not lift the cap |
| Cap | — | **78, capped at 84** | This tree is not Studio’s fingerprint (`f65411d5` = `origin/main`). Launch-critical publish was not re-tested end to end on this tree |

**Path to ≥84.** Merge this branch so the fingerprint SHA is the scored tree, and get PR check `unit (fast, no toolchain)` green. OPS-POSTIZ, OPS-QUEUE, REL-CI-ASR, and REL-CI-ARCH stay deferred unless a new command transcript closes them; the 2026-10-17 doctor and `gh run` output already attached is the deferral, not a close. REL-UNDEPLOYED stays open until that PR is the SHA Studio serves. **85+** also needs an end-to-end publish run on that shipped SHA. `daemon.ensure` at `daemon.py:393` uses the same loaded miss-or-mismatch rule as `doctor._deploy_code_check`. OPS-SHA-DAEMON is not a new defect. Launchd kickstart policy was not changed.

## Blockers

None on this tree. Code score on this branch is 78 (remediation `f19d7e5e`). Studio is serving `f65411d5`. The deploy note is pull request #1316: https://github.com/Fleezyflo/fanops/pull/1316. REL-UNDEPLOYED stays deferred until that PR merges and the fingerprint equals the merge SHA.

## Caveats that hold the score

- REL-UNDEPLOYED, deferred to the operator. Fingerprint and `origin/main` are `f65411d5806cce087a475261bf4c96c98f0bcf97`. PR head is `f19d7e5e`. Doctor on 2026-10-17: pump `f65411d5806c`, disk `d2cded31e29e` (the checkout before the remediation commit).
- REL-PROT closed 2026-10-17: `main` protection requires `unit (fast, no toolchain)` and matches the registry. REL-CI-E2E closed: scheduled run `38876056441` succeeded on `f65411d5`. REL-CI-ASR stays deferred: nightly ASR failed on that SHA (`38868030626`, `test_e2e_real.py:68`). REL-CI-ARCH stays deferred: no schedule event in the last 8 architecture runs.
- OPS-POSTIZ and OPS-QUEUE, deferred to the operator. 2026-10-17 doctor: Postiz unreachable, docker down, 5 drainable past-due, 851 queued. The ledger was not drained.
- U0-F01 and H-DOCS-F01 are closed by an explicit stale mark, not by regenerating traces.
- H-INT-F01 stays accepted. `reconcile.py` remains C7 in `partition.json`. Publish owns that file. The commit touches a lookup string in it. The park predicate was not changed.

## High-value fixes

No open code row. OPS-POSTIZ, OPS-QUEUE, REL-CI-ASR, REL-CI-ARCH, and REL-UNDEPLOYED stay deferred. They are listed under caveats.

## Evidence checked

- `git rev-parse` remediation `f19d7e5e97f1a10bf4925a0bc4a5f38a1a078a67` on PR #1316. Fingerprint stays `f65411d5` until merge.
- `GET http://127.0.0.1:8787/_fingerprint` → sha `f65411d5806cce087a475261bf4c96c98f0bcf97`
- Symbols: `ledger_wipe.snapshot_is_restorable`, `secret_provider.is_secret_env_key`, `config_introspect._is_secret`, `settings._is_resolved_secret_key`, `autopilot.set_env_var`, `health_probes.zernio_dep_health`, `doctor._deploy_code_check`, `vocals._demucs_env`, `framing._compute_track`, `persona_directives.persona_facts`, `hashtags.ship_from_lock`, `crosspost.py` assert, `health_projectors.half_live_state`, `actions_wipe.confirm_wipe`
- `docs/GOLIVE.md` section “Rollback / safety”
- Packs under `docs/audit/packs/`, including `X-SEC-secrets.md`, `X-DEP-deps.md`, `X-REL-release.md`, `X-DATA-ledger.md`

## Evidence missing

- Pytest was not run locally. CI on a PR is the test run.
- Launch-critical publish was not re-run end to end on this tree.
- REL-UNDEPLOYED stays deferred until a merged PR is the SHA Studio serves.

## Next action

PR #1316 is open. Get `unit (fast, no toolchain)` green, then merge so the fingerprint can become this SHA. OPS-POSTIZ, OPS-QUEUE, REL-CI-ASR, and REL-CI-ARCH stay deferred on the 2026-10-17 doctor and `gh run list` output.

**Caps:** 84 because this tree is not fingerprint `f65411d5` and its publish path was not re-tested end to end. Nightly ASR on that fingerprint SHA failed (`38868030626`). The 69 secrets cap is not applied.
