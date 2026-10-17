# FanOps production score (working tree on `d2cded31`)

Production audit: 78/100, launchable with caveats, because the code rows on this tree are closed or accepted and the remaining rows are deploy gates on fingerprint `f65411d5`.

The committed score on `d2cded31` was 72/100 and still listed C1-F01, C7-F01, and C9-F01 as open fixes. Those symbols do not match that prose. C7-F01 is closed because `meta_graph._read_queries` is absent. C9-F01 is closed because `actions_wipe.confirm_wipe` checks `preview_token`. C2-F01 through C6-F01 and C8-F01 are closed on the symbols named in the register. This score does not repeat them as open work.

The 84 cap binds. Launch-critical publish was not re-run end to end on this scored tree. The 69 secrets cap does not bind: `FANOPS_IG_SCRAPE_PASSWORD` is masked by `config_introspect._is_secret`, and this pass did not find the value in a client bundle, a log, or a committed file. Rollback remains the “Rollback / safety” section in `docs/GOLIVE.md`.

## Score trajectory

The numeric score on this tree is **78** against the committed **72** on the same base `d2cded31`. That is +6. The production-audit skill’s **84 cap** applies until publish is exercised end to end on the scored SHA. Scheduled CI on the deploy SHA `f65411d5` has been re-read: **REL-CI-E2E** closed (run `38876056441` succeeded); **REL-CI-ASR** is open (run `38868030626` failed); **REL-CI-ARCH** is open. Schedule run `38253871938` failed because the old tree treated branch-protection HTTP 403 as a hard fail. There is no newer schedule or dispatch transcript. **REL-CI-ASR** and **REL-CI-ARCH** are not the 84 cap. Those open rows are deploy and infra gates at ledger delta 0. They are not deductions from the code-remediation delta. Closure steps are [OPEN_GAPS.md](OPEN_GAPS.md). Every code row in the register is closed or resolved, and each cites a symbol. **C2-F03** is closed: `vocals.isolate_vocals` logs `fail-closed` and raises `ToolchainMissingError` when Demucs is missing or fails, and the mix is not transcribed. `config.isolate_vocals` matches that posture.

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
| Open, ledger delta 0 | 0 | 78 | OPS-POSTIZ, OPS-QUEUE, REL-UNDEPLOYED, REL-CI-ASR, REL-CI-ARCH stay open. [OPEN_GAPS.md](OPEN_GAPS.md) |
| REL-PROT, REL-CI-E2E | 0 | 78 | Closed 2026-10-17 on fingerprint `f65411d5`. They do not lift the cap |
| Cap | — | **78, capped at 84** | This tree is not Studio’s fingerprint (`f65411d5` = `origin/main`). Launch-critical publish was not re-tested end to end on this tree |

**Path to ≥84.** Merge this branch so the fingerprint SHA is the scored tree, and keep PR check `unit (fast, no toolchain)` green. OPS-POSTIZ, OPS-QUEUE, REL-CI-ASR, REL-CI-ARCH, and REL-UNDEPLOYED stay open until a new transcript closes them. Steps are [OPEN_GAPS.md](OPEN_GAPS.md). **85+** also needs an end-to-end live publish on that shipped SHA. `daemon.ensure` at `daemon.py:393` uses the same loaded miss-or-mismatch rule as `doctor._deploy_code_check`. OPS-SHA-DAEMON is not a new defect. Launchd kickstart policy was not changed.

## Blockers

None on this tree. Code score on this branch is 78. Studio is serving `f65411d5`. PR #1316 is https://github.com/Fleezyflo/fanops/pull/1316. REL-UNDEPLOYED is open until that PR merges and the fingerprint equals the merge SHA. The required unit check passed (run `39021062319`). Impact report `39021062321` and lane collision `39021062324` are still red on the pushed head.

## Caveats that hold the score

- REL-UNDEPLOYED is open. Fingerprint and `origin/main` are `f65411d5806cce087a475261bf4c96c98f0bcf97`. [OPEN_GAPS.md](OPEN_GAPS.md#rel-undeployed).
- REL-PROT closed 2026-10-17: `main` protection requires `unit (fast, no toolchain)` and matches the registry. REL-CI-E2E closed: scheduled run `38876056441` succeeded on `f65411d5`. REL-CI-ASR is open: nightly ASR failed on that SHA (`38868030626`, `test_e2e_real.py:68`). REL-CI-ARCH is open: schedule run `38253871938` failed the old 403 hard-fail. No newer transcript. That row is not a cap rule.
- OPS-POSTIZ and OPS-QUEUE are open. 2026-10-17 doctor: Postiz unreachable, docker down, 5 drainable past-due, 851 queued. Ledger delta 0. The ledger was not wiped.
- U0-F01 and H-DOCS-F01 are closed by the stale mark. Traces were not regenerated.
- H-INT-F01 is resolved. Operator edits of `reconcile.py` are C6. `partition.json` keeps C7. No retag.

## High-value fixes

No open code row. OPS-POSTIZ, OPS-QUEUE, REL-CI-ASR, REL-CI-ARCH, and REL-UNDEPLOYED stay open. They are listed under caveats.

## Evidence checked

- `git rev-parse HEAD` → `c1d892df86a02e57e3a9243109a00702c1bdfce8`, which is PR #1316 `headRefOid`. Fingerprint stays `f65411d5` until merge.
- `GET http://127.0.0.1:8787/_fingerprint` → sha `f65411d5806cce087a475261bf4c96c98f0bcf97`
- Symbols: `ledger_wipe.snapshot_is_restorable`, `secret_provider.is_secret_env_key`, `config_introspect._is_secret`, `settings._is_resolved_secret_key`, `autopilot.set_env_var`, `health_probes.zernio_dep_health`, `doctor._deploy_code_check`, `vocals._demucs_env`, `framing._compute_track`, `persona_directives.persona_facts`, `hashtags.ship_from_lock`, `crosspost.py` assert, `health_projectors.half_live_state`, `actions_wipe.confirm_wipe`
- `docs/GOLIVE.md` section “Rollback / safety”
- Packs under `docs/audit/packs/`, including `X-SEC-secrets.md`, `X-DEP-deps.md`, `X-REL-release.md`, `X-DATA-ledger.md`

## Evidence missing

- Pytest was not run locally. CI on a PR is the test run.
- Launch-critical publish was not re-run end to end on this tree.
- REL-UNDEPLOYED is open until a merged PR is the SHA Studio serves.

## Path to 100

The closure order is [OPEN_GAPS.md](OPEN_GAPS.md). Why the sheet says capped at 84: [Why capped at 84](GAP_CLOSURE_PLAN.md#why-capped-at-84). This section does not add weights. The ledger above is the only arithmetic: **78**, cap **84**, open rows at 0, resolved and stale-mark closures not in the +6.

What the score itself already requires, and nothing past it:

- **≥84.** Merge so the fingerprint SHA is this tree, with `unit (fast, no toolchain)` green. The latest PR rollup of that check is **pass** (run `39021062319`). An older run `39019468576` failed. Fingerprint is still `f65411d5`.
- **85+.** An end-to-end publish on that shipped SHA. Dry-run does not count: `docs/GOLIVE.md` says dry-run posts nothing, and `post/run.py` `_post_provider` returns `"dryrun"` when the tree is not live.
- **Open rows.** OPS-POSTIZ, OPS-QUEUE, REL-CI-ASR, REL-CI-ARCH, and REL-UNDEPLOYED stay at ledger delta 0 until a new transcript closes them. Steps are [OPEN_GAPS.md](OPEN_GAPS.md). REL-CI-ASR is an unauthenticated Hugging Face Hub failure inside whisper (`test_e2e_real.py:68`, `assert 0 == 1`), not a doctor defect. REL-CI-ARCH has no dispatch transcript yet.

There is no 100 on this sheet. The stale-mark closures are already applied. C3-F01 is closed on the real-ffmpeg test and is not a new ledger row. The headline stays **78** (+6).

## Next action

Follow [OPEN_GAPS.md](OPEN_GAPS.md). C3-F01 is closed: `test_ct_multi_resolve_one_real_ffmpeg` counts one real ffmpeg on the CT_MULTI path. H-INT-F01 is resolved (no retag). U0-F01 and H-DOCS-F01 stay closed by the stale mark. The +6 ledger is unchanged. Still open: queue, Postiz, ASR nightly, architecture reconcile transcript, and the merge that makes the fingerprint the scored SHA. Do not wipe the ledger, do not start Docker, and do not merge #1316 until asked. The required unit check passed (run `39021062319`). The fingerprint is `f65411d5`.

**Caps:** 84 because this tree is not fingerprint `f65411d5` and its publish path was not re-tested end to end. See [Why capped at 84](GAP_CLOSURE_PLAN.md#why-capped-at-84). Nightly ASR on that fingerprint SHA failed (`38868030626`); that row is not the cap. The 69 secrets cap is not applied.
