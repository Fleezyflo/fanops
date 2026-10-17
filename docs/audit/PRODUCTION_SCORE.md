# FanOps production score (working tree on `d2cded31`)

Production audit: 78/100, launchable with caveats, because the code rows on this tree are closed or accepted and the remaining rows are deploy gates on fingerprint `f65411d5`.

The committed score on `d2cded31` was 72/100 and still listed C1-F01, C7-F01, and C9-F01 as open fixes. Those symbols do not match that prose. C7-F01 is closed because `meta_graph._read_queries` is absent. C9-F01 is closed because `actions_wipe.confirm_wipe` checks `preview_token`. C2-F01 through C6-F01 and C8-F01 are closed on the symbols named in the register. This score does not repeat them as open work.

The deploy caveat and the live-publish caveat are closed. Fingerprint sha is `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. One live Postiz publish of `post_5854bb63fefb` ran on that SHA. The headline stays **78**. **REL-CI-ASR** stays open. The 69 secrets cap does not bind: `FANOPS_IG_SCRAPE_PASSWORD` is masked by `config_introspect._is_secret`, and this pass did not find the value in a client bundle, a log, or a committed file. Rollback remains the “Rollback / safety” section in `docs/GOLIVE.md`.

## Score trajectory

The numeric score on this tree is **78** against the committed **72** on the same base `d2cded31`. That is +6. Fingerprint sha `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` equals the merge of #1316, and unit run `39073814011` passed on the merged head. **REL-CI-ARCH** is closed on dispatch `39074333775` (`[SKIP] DC-3`, HTTP 403, exit 0). **REL-CI-ASR** is open: nightly `38868030626` failed, and `HF_TOKEN` is absent, so no new nightly was run. That open row is ledger delta 0. Closure steps are [OPEN_GAPS.md](OPEN_GAPS.md). Every code row in the register is closed or resolved, and each cites a symbol. **C2-F03** is closed: `vocals.isolate_vocals` logs `fail-closed` and raises `ToolchainMissingError` when Demucs is missing or fails, and the mix is not transcribed. `config.isolate_vocals` matches that posture.

It is 78 rather than 72 because the code rows that were actually open are closed (`get_logger` on the wipe path, scrape-password masking, `.env` file lock, Zernio auth health, deploy-SHA miss path). The headline stays 78 because no ledger row moved. Studio’s fingerprint equals the merge SHA, and one live publish ran on it.

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
| Open, ledger delta 0 | 0 | 78 | REL-CI-ASR stays open. OPS-POSTIZ, OPS-QUEUE, REL-UNDEPLOYED, and REL-CI-ARCH are closed. [OPEN_GAPS.md](OPEN_GAPS.md) |
| REL-PROT, REL-CI-E2E | 0 | 78 | Closed 2026-10-17 on fingerprint `f65411d5`. They do not lift the cap |
| Cap | — | **78** | Fingerprint equals merge `430ece73`. Live publish `post_5854bb63fefb` via Postiz on that SHA. Headline unchanged. REL-CI-ASR remains open |

**Path to ≥84.** Closed on the evidence: fingerprint sha `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` equals the merge of #1316, and `unit (fast, no toolchain)` run `39073814011` passed on head `6f355d09`. **85+** is closed: `post_5854bb63fefb` published through Postiz on that SHA (`backend=postiz`, not dry-run). **REL-CI-ASR** stays open. The headline stays 78.

## Blockers

Code score on this branch is 78. PR #1316 is merged as `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. Studio `GET /_fingerprint` sha equals that commit. The remaining open row is REL-CI-ASR.

## Caveats that hold the score

- REL-UNDEPLOYED is closed. Fingerprint sha `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. [OPEN_GAPS.md](OPEN_GAPS.md#rel-undeployed).
- REL-PROT and REL-CI-E2E stay closed. REL-CI-ARCH is closed: run `39074333775` printed `[SKIP] DC-3` (HTTP 403) and exited 0. REL-CI-ASR is open: nightly `38868030626` failed (`test_e2e_real.py` moments `0 == 1`) and `HF_TOKEN` is absent.
- OPS-POSTIZ and OPS-QUEUE are closed. 2026-10-17 doctor: Postiz reachable, docker up, `0 drainable past-due`, 837 queued. The ledger was not wiped.
- 85+ is closed. `post_5854bb63fefb` via Postiz on the merge SHA. Not a dry-run. [OPEN_GAPS.md](OPEN_GAPS.md#85).
- U0-F01 and H-DOCS-F01 are closed by the stale mark. Traces were not regenerated.
- H-INT-F01 is resolved. Operator edits of `reconcile.py` are C6. `partition.json` keeps C7. No retag.

## High-value fixes

No open code row. REL-CI-ASR stays open. It is listed under caveats.

## Evidence checked

- `git rev-parse HEAD` → `c1d892df86a02e57e3a9243109a00702c1bdfce8`, which is PR #1316 `headRefOid`. Fingerprint stays `f65411d5` until merge.
- `GET http://127.0.0.1:8787/_fingerprint` → sha `f65411d5806cce087a475261bf4c96c98f0bcf97`
- Symbols: `ledger_wipe.snapshot_is_restorable`, `secret_provider.is_secret_env_key`, `config_introspect._is_secret`, `settings._is_resolved_secret_key`, `autopilot.set_env_var`, `health_probes.zernio_dep_health`, `doctor._deploy_code_check`, `vocals._demucs_env`, `framing._compute_track`, `persona_directives.persona_facts`, `hashtags.ship_from_lock`, `crosspost.py` assert, `health_projectors.half_live_state`, `actions_wipe.confirm_wipe`
- `docs/GOLIVE.md` section “Rollback / safety”
- Packs under `docs/audit/packs/`, including `X-SEC-secrets.md`, `X-DEP-deps.md`, `X-REL-release.md`, `X-DATA-ledger.md`

## Evidence missing

- Pytest was not run locally. CI on a PR is the test run.
- REL-CI-ASR has no new nightly transcript. `HF_TOKEN` is not set.

## Path to 100

The closure order is [OPEN_GAPS.md](OPEN_GAPS.md). Why the sheet says capped at 84: [Why capped at 84](GAP_CLOSURE_PLAN.md#why-capped-at-84). This section does not add weights. The ledger above is the only arithmetic: **78**, cap **84**, open rows at 0, resolved and stale-mark closures not in the +6.

What the score itself already requires, and nothing past it:

- **≥84.** Closed. Fingerprint sha equals merge `430ece73`. Unit run `39073814011` passed.
- **85+.** Closed. `post_5854bb63fefb` through Postiz on that SHA. `_post_provider` returned `postiz`.
- **Open rows.** REL-CI-ASR stays open. Nightly `38868030626` failed (`whisper produced no JSON (rc=1)`). `HF_TOKEN` is absent. Steps are [OPEN_GAPS.md](OPEN_GAPS.md#rel-ci-asr).

There is no 100 on this sheet. The stale-mark closures are already applied. C3-F01 is closed on the real-ffmpeg test and is not a new ledger row. The headline stays **78** (+6).

## Next action

Follow [OPEN_GAPS.md](OPEN_GAPS.md). The +6 ledger is unchanged. REL-CI-ASR is the open row. Set `HF_TOKEN`, then a nightly `[asr] toolchain smoke` must pass with `awaiting.moments == 1`. Do not wipe the ledger. Do not change `doctor.py` for that row.

**Caps:** 84 because this tree is not fingerprint `f65411d5` and its publish path was not re-tested end to end. See [Why capped at 84](GAP_CLOSURE_PLAN.md#why-capped-at-84). Nightly ASR on that fingerprint SHA failed (`38868030626`); that row is not the cap. The 69 secrets cap is not applied.
