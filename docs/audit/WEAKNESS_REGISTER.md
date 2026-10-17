# Weakness register (working tree on `d2cded31`)

Base commit `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e` on `audit/production-audit-2026-09`. The rows below cite symbols in this working tree after the remediate program. They replace the committed 2026-09-12 register and the uncommitted re-verdict of `088702ca`. Studio `GET /_fingerprint` sha is `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`, the merge commit of PR #1316 and `origin/main` at that poll. The ledger was not modified.

## Rows

| ID | Status | Evidence on this working tree |
|----|--------|-------------------------------|
| U0-F01 | closed | Closed by the stale mark in `subsystem-traces/STALE.md`. Live count 196, indexed stems 124. Traces were not regenerated. See [OPEN_GAPS.md](OPEN_GAPS.md#u0-f01--h-docs-f01). |
| C1-F01 | closed | `execute_wipe` passes `cfg` into `snapshot_is_restorable`. The unrestorable path calls `get_logger(cfg)` with outcome `snapshot_unrestorable`. The manifest failure calls `get_logger(cfg)` with outcome `wipe_manifest_unwritten`. `cli.cmd_retire_source` and `actions_wipe.confirm_wipe` pass `cfg` as well. |
| C2-F01 | closed | `vocals._demucs_env` returns `certifi_ssl_env` and does not catch. |
| C2-F02 | closed | `artifacts._valid_transcript_json` requires an adoptable sidecar. A legacy segments list with no ASR quality keys does not resume as transcribed. |
| C2-F03 | closed | `vocals.isolate_vocals` logs `isolate_vocals fail-closed` and raises `ToolchainMissingError` when Demucs is missing, times out, exits nonzero, or leaves no stem. The source is set to error. The mix is not transcribed. `config.isolate_vocals` states the same posture. |
| C2-F04 | closed | Native `stage_inbox_candidates` skips a child `.pull`. A stranded pull and a disposed `.pull/.ingested` file are not minted `source_origin=drop`. A scan whose inbox is `.pull` still catalogues that stage. `tests/test_ingest_origin_correlation.py` `test_native_ingest_leaves_pull_stage_untouched`. |
| C3-F01 | closed | `test_ct_multi_resolve_one_real_ffmpeg` counts one real ffmpeg at `framing._FRAMING_GRID_FPS` (9.0) on the CT_MULTI path through `clip._resolve_framing`. The detector is stubbed as two faces. `clip.py` was not edited. See [OPEN_GAPS.md](OPEN_GAPS.md#c3-f01). |
| C3-F02 | closed | `clip_ffmpeg.ffmpeg_supercut_cmd` emits `-fps_mode cfr` after the maps, same slot as `ffmpeg_segments_cmd`. |
| C3-F03 | closed | `clip_ffmpeg._finish_publish` returns rc 1 when ffmpeg exits 0 but the temp is empty or missing, and does not `os.replace` onto a pre-existing dst. `tests/test_render_atomicity.py` `test_empty_rc0_rerender_does_not_adopt_prior_mp4`. |
| C4-F01 | closed | `persona_directives.persona_facts` returns `persona_terms` and does not catch. |
| C4-F02 | closed | `lever_docs.archetype_crosswalk_rows` keeps `content_focus` as one stripped string. `list(str)` no longer splits the sentence into characters. |
| C4-F03 | closed | `moments._hook_script_mismatch` keeps a non-Arabic hook when the source language is outside `{en, ar}`. English and Arabic mismatches still null the hook. `tests/test_hook_language_gate.py`. |
| C4-F04 | closed | `actions_edit.regenerate_hook` uses `moments._hook_script_mismatch`. A Latin hook on `fr` is kept. An Arabic-script hook on `fr` is still rejected. `tests/test_studio_regenerate_hook.py`. |
| C5-F01 | closed | `vet_hashtags` is absent. `hashtags.ship_from_lock` still states no floors and no backfill. |
| C5-F02 | closed | `source_tags_scrape.ensure_source_lock` writes the in-progress sidecar when a pass is only exact misses. The next tick stores those misses and searches the rest of the catalog. `tests/test_source_tags.py` `test_unattended_exact_miss_pass_persists_and_advances`. |
| C6-F01 | closed | `crosspost.py` has module-level `assert _JITTER_MAX < _STEP_MIN`. |
| C6-F02 | closed | `_NET_POST_FIELDS` includes `publish_hour` and `publish_dow`. `_publish_one` stamps them on the submitted→published promotion and finalize keeps them. |
| C7-F01 | closed | `meta_graph._read_queries` is absent. No successor. |
| C7-F02 | closed | `timing_bias.timing_bias_hour_for` returns None unless `FANOPS_TIMING_BIAS` is on. Crosspost applies that return and does not check the flag itself. `reconcile.py` was not edited. |
| C8-F01 | closed | `health_projectors.half_live_state` returns a surfaced hint on compute failure. Doctor emits that check. |
| C9-F01 | closed | `actions_wipe.confirm_wipe` refuses a missing or stale `preview_token`. `do_wipe_confirm` passes the token. |
| C9-F02 | closed | `actions_approve._approve_ids_with_render` skips a post that is not `awaiting_approval` before the count and the account respread. A repeated approve does not rewrite queued times. |
| C9-F03 | closed | `actions_edit.regenerate_hook` reads the clock after the model call and passes it into `reburn_hook`. A queued post that becomes imminent during the call is refused before `render_moment`. `tests/test_studio_regenerate_hook.py` `test_regenerate_hook_refuses_before_render_when_queued_post_becomes_imminent`. |
| C10-F01 | closed | `app_routes_home` passes `zero_post_clips`. The unwired claim in `full-trace-index.md` and `anomalies.md` is marked stale. |
| C10-F02 | closed | `static/schedule.js` listens on `document` and re-reads `#schedule-body` per event. Handlers bound to that node died on the htmx `outerHTML` swap. |
| C10-F03 | closed | Posted Show more keeps `source`, `delivery`, and `failure`, and is omitted when `next_offset` is none. |
| C10-F04 | closed | A failed re-burn, regenerate-hook, or tag swap with `swap=card` or `swap=feed` re-renders that card with the error inside it. `tests/test_studio_reburn_hook.py` `test_failed_swap_keeps_the_review_card`. |
| H-CI-F01 | closed | `tests/test_production_audit_gates.py` requires the pack set, including the X-packs, and requires every pack finding ID in this register. |
| H-DOCS-F01 | closed | Closed by the stale mark in `subsystem-traces/STALE.md`. Last trace commit `f11d0af7` (2026-09-09). Traces were not regenerated. See [OPEN_GAPS.md](OPEN_GAPS.md#u0-f01--h-docs-f01). |
| H-INT-F01 | resolved | Operator edits of `reconcile.py` are C6; `partition.json` keeps it in C7. `post/metrics/*` is C6 there and C7 for operator audit. No retag. See [OPEN_GAPS.md](OPEN_GAPS.md#h-int-f01). |
| SEC-IGPW | closed | `secret_provider.is_secret_env_key` is true for `FANOPS_IG_SCRAPE_PASSWORD` and for `FANOPS_IG_SCRAPE_PASSWORD_*`. `FANOPS_IG_SCRAPE_USER` is not a secret. `config_introspect._SECRET_SUFFIX` includes `_PASSWORD`. `settings._is_resolved_secret_key` and `_BASE_SECRET_KEYS` resolve the base key and a present dynamic key. `config.py` was not edited. |
| SEC-ENV | closed | `autopilot.set_env_var` and `unset_env_var` hold `ledger._file_lock` on `<name>.lock` around the read-modify-replace. A lock timeout surfaces as `OSError`. `ledger.py` was not edited. |
| OPS-ZERNIO | closed | `health_probes.zernio_dep_health` calls `_http_reachable` with `require_2xx=True`. A 2xx is up. 401 and 5xx are down. A connection error is unreachable. No creds stays skipped. |
| OPS-SHA | closed | `doctor._deploy_code_check` fails when the daemon is loaded and either SHA is missing or the two differ. Unloaded stays N/A. Both present and equal still pass. `daemon.ensure` kickstarts on that same miss-or-mismatch when the pump is loaded. |
| OPS-POSTIZ | open | 2026-10-17 `uv run fanops doctor` exit 1: Postiz unreachable, docker down. Ledger +0. Closure: [OPEN_GAPS.md](OPEN_GAPS.md#ops-postiz). |
| OPS-QUEUE | open | 2026-10-17 `fanops doctor` exit 1: `5 drainable past-due (up to 463.9h); 76 parked on non-active accounts; 0 retired lineage; 851 total queued`. Ledger +0, not wiped. Closure: [OPEN_GAPS.md](OPEN_GAPS.md#ops-queue). |
| REL-UNDEPLOYED | closed | Merge `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9` of #1316. Unit run `39073814011`, impact run `39073814128`, and lane-guard run `39073814368` passed on `6f355d09b2dea3275101434d8c03c96bc7692d08`. `GET /_fingerprint` sha equals that merge commit. See [OPEN_GAPS.md](OPEN_GAPS.md#rel-undeployed). |
| REL-PROT | closed | 2026-10-17 `gh api repos/Fleezyflo/fanops/branches/main/protection` exit 0: `strict` true, contexts `["unit (fast, no toolchain)"]`. Same context in `.github/ci-control-registry.yml`. |
| REL-CI-E2E | closed | 2026-10-17 `gh run list --workflow ci-e2e.yml -L 5`: run `38876056441`, event `schedule`, conclusion `success`, headSha `f65411d5806cce087a475261bf4c96c98f0bcf97`, created `2026-10-16T08:47:37Z`. |
| REL-CI-ASR | open | Nightly run `38868030626` failed `[asr] toolchain smoke (nightly)` (`whisper produced no JSON (rc=1)`, `test_e2e_real.py` moments `0 == 1`). Closure: [OPEN_GAPS.md](OPEN_GAPS.md#rel-ci-asr). |
| REL-CI-ARCH | open | Run `38253871938` failed a branch-protection HTTP 403. Working tree `cmd_deployed` no longer hard-fails that unreadable DC-3. No new dispatch transcript. Closure: [OPEN_GAPS.md](OPEN_GAPS.md#rel-ci-arch). |
| X-DATA-F01 | closed | Wipe stays operator-gated: preview token, then `snapshot_is_restorable`, then `execute_wipe`. `AGENTS.md` forbids wipe-as-repair. No script under `scripts/` resets the ledger. |

A row is finished when it is closed with a symbol on this tree, resolved with the dual-map citation, or open with a subsection in [OPEN_GAPS.md](OPEN_GAPS.md).
