# Remaining weaknesses & gaps

Assessment date **2026-10-18**. Subject **`origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`**. Score headline from [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md): **78/100, Launchable With Caveats**.

The single operator index for everything still weak, gapped, or caveat-bearing after the 2026-10-18 audit, with a live remeasurement taken 2026-10-19.

## Live measurement

Remeasured 2026-10-19. `origin/main`, the Studio fingerprint `sha`, and the pump heartbeat are one commit. Doctor disk on this checkout is not that commit.

| Fact | 2026-10-19 |
|------|------------|
| Fingerprint `sha` | `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` (reachable) |
| `origin/main` | `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` |
| Pump heartbeat `_last_heartbeat_code` | `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` |
| Doctor disk `_version_signal` | `7aa5d3239a1c61f54613d03d864375ba4420cadf` (`git-head`) |
| Queue | `0 drainable past-due; 88 parked on non-active accounts; 0 retired lineage; 831 queued; 4 needs_reconcile` |
| `post_5854bb63fefb` | state `analyzed`; public_url `https://www.instagram.com/reel/Deoy82Qjp8j/` |
| `awaiting_approval` | 16 |
| Doctor | exit 1; completed ~6m11s / 370.9s; sole FAIL is deploy-code |

Disk ≠ pump, so `doctor._deploy_code_check` FAILs. That is the expected docs-ahead deploy-code miss. It is not a register row. Closure = merge → `git pull --ff-only` on the live checkout → `fanops up`. That is not a restart of launchd. The live checkout matches the pump.

`post_5854bb63fefb`: 2026-10-18 it was the standing caveat (needs_reconcile, no URL); 2026-10-19 remeasurement shows it reconciled to `analyzed` with public_url `https://www.instagram.com/reel/Deoy82Qjp8j/` — caveat CLEARED by the live reconciler. The headline stays 78 because the score ledger only moves on a register/ledger row, not on a live post reconciling (per `PRODUCTION_SCORE.md`).

Deltas vs the 2026-10-18 sheet:

- Parked on non-active accounts: 86 → 88.
- `post_5854bb63fefb`: needs_reconcile (no URL) → `analyzed` (public URL present). Caveat cleared.
- `awaiting_approval`: 16 (unchanged).
- Drainable 0, retired lineage 0, queued 831, and needs_reconcile 4 match the prior sheet. `origin/main`, fingerprint `sha`, pump heartbeat, and doctor disk SHA match the prior sheet.

`fanops doctor` exceeded the 6-minute wall clock and then completed at ~6m11s / 370.9s (exit 1). The live queue, approval, and post facts are the direct ledger reads. They are corroborated by the completed transcript in [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md) (~5 min, exit 1; sole FAIL = deploy-code pump `430ece73` vs disk `7aa5d3239a1c`; queue draining PASS; 16 awaiting approval) and reproduced by that fresh run (same deploy-code FAIL, queue-draining PASS, 16 awaiting approval).

## Open register weaknesses

**`WEAKNESS_REGISTER.md` has zero open rows** (every row is closed or resolved).

IDs and statuses are the [WEAKNESS_REGISTER.md](WEAKNESS_REGISTER.md) `## Rows` table. Remaining is `None` on every closed row. The two resolved rows keep the residual note.

| ID | Status | Remaining |
|----|--------|-----------|
| U0-F01 | closed | None |
| C1-F01 | closed | None |
| C2-F01 | closed | None |
| C2-F02 | closed | None |
| C2-F03 | closed | None |
| C2-F04 | closed | None |
| C3-F01 | closed | None |
| C3-F02 | closed | None |
| C3-F03 | closed | None |
| C4-F01 | closed | None |
| C4-F02 | closed | None |
| C4-F03 | closed | None |
| C4-F04 | closed | None |
| C5-F01 | closed | None |
| C5-F02 | closed | None |
| C6-F01 | closed | None |
| C6-F02 | closed | None |
| C7-F01 | closed | None |
| C7-F02 | closed | None |
| C8-F01 | closed | None |
| C9-F01 | closed | None |
| C9-F02 | closed | None |
| C9-F03 | closed | None |
| C10-F01 | closed | None |
| C10-F02 | closed | None |
| C10-F03 | closed | None |
| C10-F04 | closed | None |
| H-CI-F01 | closed | None |
| H-DOCS-F01 | closed | None |
| H-INT-F01 | resolved | dual map — operator edits of `reconcile.py` are C6, `partition.json` keeps C7, `post/metrics/*` C6 in partition / C7 for operator audit, no retag |
| SEC-IGPW | closed | None |
| SEC-ENV | closed | None |
| OPS-ZERNIO | closed | None |
| OPS-SHA | closed | None |
| OPS-POSTIZ | closed | None |
| OPS-QUEUE | closed | None |
| REL-UNDEPLOYED | closed | None |
| REL-PROT | closed | None |
| REL-CI-E2E | closed | None |
| REL-CI-ASR | resolved | nightly `asr-smoke` is CI hygiene, not in `required_contexts`, monitor run `39107733972` (failed on subject), product does not read hub token, `doctor` probes `transcribe._fw_available` |
| REL-CI-ARCH | closed | None |
| X-DATA-F01 | closed | None |

## Operational gaps (not register rows)

Standing caveats from [GAP_CLOSURE_PLAN.md](GAP_CLOSURE_PLAN.md) “What remains”, [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md) “Blockers” and “High-value fixes”, and [OPEN_GAPS.md](OPEN_GAPS.md) “Cap ≥84”, “85+”, and the OPS-QUEUE deploy-code paragraph. Where a 2026-10-18 sentence named `post_5854bb63fefb` or the parked count, the bullet uses the 2026-10-19 remeasurement.

- **Blockers.** None on the shipped SHA. The merge is what production runs. Studio fingerprint, pump heartbeat, and `origin/main` are `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`.
- **`post_5854bb63fefb`.** 2026-10-18 it was the standing caveat (needs_reconcile, no URL); 2026-10-19 remeasurement shows it reconciled to `analyzed` with public_url `https://www.instagram.com/reel/Deoy82Qjp8j/` — caveat CLEARED by the live reconciler. The headline stays 78 because the score ledger only moves on a register/ledger row, not on a live post reconciling (per `PRODUCTION_SCORE.md`). The 85+ close stays the Postiz send on the subject (`outcome=publish_missing_url`, `backend=postiz`, not a dry-run). The URL is back. That does not add a ledger row. [OPEN_GAPS.md](OPEN_GAPS.md#85).
- **Queue (remeasured).** `0 drainable past-due; 88 parked on non-active accounts; 0 retired lineage; 831 queued; 4 needs_reconcile`. The 2026-10-18 sheet had parked 86. Rows were not deleted. The ledger was not wiped. [OPEN_GAPS.md](OPEN_GAPS.md#ops-queue).
- **Approval.** **16 posts awaiting approval** (remeasured; unchanged from the doctor-reported 16).
- **Deploy-code.** `doctor._deploy_code_check` FAILs whenever this checkout’s disk SHA differs from the pump. On 2026-10-19 disk is `7aa5d3239a1c61f54613d03d864375ba4420cadf` and the pump is `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. EXPECTED on docs-ahead branches. Not a register row. Closure = merge → `git pull --ff-only` on the live checkout → `fanops up` (per the doctor message). Not a restart. REL-UNDEPLOYED stays closed: fingerprint, pump, and the live-checkout disk equal the subject. [OPEN_GAPS.md](OPEN_GAPS.md#ops-queue).
- **Headline.** Stays **78** until a register row moves. The band stays Launchable With Caveats per [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md).
- **84 cap.** The **84 cap is not applied as a subtraction**. The band stays Launchable With Caveats. The 69 cap does not bind. Unit run `39074067963` passed on the subject. [OPEN_GAPS.md](OPEN_GAPS.md) section Cap ≥84. [GAP_CLOSURE_PLAN.md](GAP_CLOSURE_PLAN.md#why-capped-at-84).
- **High-value fixes.** Scheduled `ci-e2e.yml` `39112132315` is green on the subject. Nightly `asr-smoke` `39107733972` failed on the subject and is resolved CI hygiene. Do not dispatch `ci-e2e` or `nightly` for audit closure. Do not restart launchd. Do not wipe the ledger.
- **OPS-POSTIZ.** Closed on the 2026-10-18 doctor: Postiz reachable, docker up. Not a remaining gap. [OPEN_GAPS.md](OPEN_GAPS.md#ops-postiz).

## Documentation / structural debt

- **U0-F01** and **H-DOCS-F01** are closed by `docs/CODEMAPS/subsystem-traces/STALE.md`. Live **196** Python modules vs **124** indexed trace stems. Traces were not regenerated. Do not treat the stale mark as regenerated traces. [OPEN_GAPS.md](OPEN_GAPS.md#u0-f01--h-docs-f01).
- **H-INT-F01** dual map — operator edits of `reconcile.py` are C6, `partition.json` keeps C7, `post/metrics/*` C6 in partition / C7 for operator audit, no retag. [OPEN_GAPS.md](OPEN_GAPS.md#h-int-f01).

## CI and infra monitoring

- **REL-CI-ASR** resolved. Nightly `asr-smoke` run `39107733972` failed on the subject SHA. It is not in `required_contexts`. [OPEN_GAPS.md](OPEN_GAPS.md#rel-ci-asr).
- **REL-CI-E2E** closed. Schedule run `39112132315` is green on the subject SHA.
- **REL-CI-ARCH.** `[SKIP] DC-3` on live-protection HTTP 403; run `39074333775` succeeded. [OPEN_GAPS.md](OPEN_GAPS.md#rel-ci-arch).
- Optional operator hygiene (housekeeping only, not a register row, not required for launch). If `.orchestration/state/ACTIVE` from the Sep-12 wave is still present and that wave is over, `python scripts/orchestrate.py stop` clears the marker. Conditional step only.

## Per-module remaining work

Each Remaining work cell is copied verbatim from the [AUDIT_MODULE_INDEX.md](AUDIT_MODULE_INDEX.md) executive table (2026-10-18).

| Module ID | Remaining work |
|-----------|----------------|
| U0 | None in product code. Do not treat the stale mark as a regenerated trace |
| C1 | `config.py` stays on publish. No C1 code wave in this pass |
| C2 | None. `vocals.isolate_vocals` logs fail-closed and raises |
| C5 | A miss-only scrape pass now writes the sidecar, so the next tick does not research the same names. The hook prompt still says “SEE them”. `prompts.py` was not edited |
| C3 | An empty rc-0 re-render no longer stamps the old mp4. `test_ct_multi_resolve_one_real_ffmpeg` counts one real 9fps ffmpeg on CT_MULTI. `clip.py` was not edited |
| C4 | Studio `regenerate_hook` uses the same script check as ingest. `prompts.py` is with C5 |
| C9 | A queued post that becomes imminent during hook regeneration is refused before `render_moment`. Repeated approve does not rewrite queued times |
| C10 | A failed card or feed swap re-renders the card with the error inside it. `schedule.js` still re-reads `#schedule-body` |
| C6 | 2026-10-18 re-read found no C6-F03. Dry-run does not POST. IG/TT and Zernio `content` are `posted_text_for`; Postiz YouTube `content` is `Post.caption` |
| C7 | 2026-10-18 re-read found no C7-F03. `_read_queries` has no successor. `reconcile.py` was not edited |
| C8 | `daemon.ensure` matches the doctor miss-or-mismatch rule. 2026-10-18 doctor exit 1 is this branch’s disk `7aa5d3239a1c` versus the pump. Launchd was not restarted |
| H-CI | Parent extends `tests/test_production_audit_gates.py` only for new pack finding IDs, last |
| H-DOCS | Traces were not regenerated. That is not full closure of the 196-vs-124 gap |
| H-INT | `partition.json` stays C1–C10. Operator edits of `reconcile.py` are C6; the partition keeps C7. No retag |
| X-SEC | 69 cap does not bind. Do not edit `config.py` |
| X-DEP | 2026-10-18: Postiz/Zernio bounded probes reachable, `0 drainable past-due`, 86 parked, 831 queued, 4 needs_reconcile. [OPEN_GAPS.md](OPEN_GAPS.md) |
| X-REL | Fingerprint `430ece73`. Scheduled e2e `39112132315` green on the subject; nightly `asr-smoke` `39107733972` failed on the subject (resolved). `post_5854bb63fefb` still `needs_reconcile` with no `public_url`. REL-CI-ARCH run `39074333775` succeeded. [OPEN_GAPS.md](OPEN_GAPS.md) |
| X-DATA | Ledger was not wiped. Do not edit `ledger.py` |

The X-REL cell is the 2026-10-18 snapshot; Live measurement carries the current state of `post_5854bb63fefb`.
