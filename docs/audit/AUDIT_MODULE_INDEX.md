# Audit module index

Subject of this index is the head of pull request #1316 on branch `audit/production-audit-2026-09`. The remediation commit is `f19d7e5e97f1a10bf4925a0bc4a5f38a1a078a67`. The scored base before that commit was `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e`. Measured 2026-10-17 from `/Users/molhamhomsi/Moh Flow Fanops`. `GET http://127.0.0.1:8787/_fingerprint` is still `f65411d5806cce087a475261bf4c96c98f0bcf97` (pid 1225, generation `246c325b4dc81a3106fce99abe982f90`, `start_time` `2026-10-16T15:57:11.959355+00:00`).

Finalize is complete against the register on this commit. Every row is closed, accepted, or deferred with an owner. C2–C10 packs carry a Full checklist 2026-10-17. Deferred rows that remain are OPS-POSTIZ, OPS-QUEUE, REL-UNDEPLOYED, REL-CI-ASR, and REL-CI-ARCH. REL-UNDEPLOYED closes when #1316 merges and the fingerprint equals that merge SHA.

| Plane | SHA | Relation to the subject |
|-------|-----|-------------------------|
| HEAD (audit subject) | PR #1316 | Remediation `f19d7e5e`. Scored base `d2cded31`. Fingerprint is `origin/main` |
| `origin/main` | `f65411d5806cce087a475261bf4c96c98f0bcf97` (merge of #1314) | Neither ancestor of HEAD nor descendant of HEAD |
| Studio `GET http://127.0.0.1:8787/_fingerprint` | `f65411d5806cce087a475261bf4c96c98f0bcf97` | Equals `origin/main`. pid 1225, generation `246c325b4dc81a3106fce99abe982f90`, `start_time` `2026-10-16T15:57:11.959355+00:00` |
| `088702ca3ef4048e217befbb703443d6c1f5fde9` | ancestor of `origin/main` | Named by the uncommitted working-tree copies of `MASTER_MAP.md`, `PRODUCTION_SCORE.md`, and `WEAKNESS_REGISTER.md`. Studio is not serving it |

The committed score on this branch is **78/100**. The score on `d2cded31` was 72/100. See the score ledger in [PRODUCTION_SCORE.md](PRODUCTION_SCORE.md). Studio’s fingerprint equals `origin/main`, not this branch.

The working tree is the audit subject for the Wave 1–2 re-audit. Dirty product files are in scope. `088702ca` is an ancestor of `origin/main` and is not this score. `partition.json` assigns every `src/fanops/**/*.py` path to C1–C10 only (`live_py_count` 196). X-modules overlap those files; they do not get a third key unless `tests/test_production_audit_gates.py` `test_live_partition_covers_every_module` is changed on purpose.

## Workflow

Three phases. A module is done only when its finalization gate exists on the audit-subject commit.

### Audit

Read-only evidence on `d2cded31`. Update the linked pack findings table, or append a findings table when the module has no pack (X-SEC, X-DEP, X-REL, X-DATA). Collect the production-audit checklist slice named on the module row: routes, auth, data writes, external calls, jobs, env, health, rollback, and the user path that module owns. Record the commands run. Do not edit product code. Do not treat the dirty working-tree audit docs as the subject.

### Remediate

One finding, one owner path, on a lane-respecting branch. A hot file in `.agents/lanes.json` owned by another lane is reported and left untouched. `./scripts/check.sh` before every commit. `python -m tools.arch regen` when a scanned source line moves. Tests are written with the change; GitHub CI runs them. No local `pytest`. Behaviour that is wrong goes through `fix-defect`. Doc-rot and stale register text stay in the owning module’s docs path.

### Finalize

Parent pass after the module gates exist. Merge rows into `docs/audit/WEAKNESS_REGISTER.md` so every ID on this index is present. Refresh `docs/audit/MASTER_MAP.md` component verdicts for this HEAD (the live fingerprint SHA is a separate column, already measured as `f65411d5`). Re-score `docs/audit/PRODUCTION_SCORE.md` from the closed and deferred rows. `H-CI` lands last so `tests/test_production_audit_gates.py` encodes the closed findings.

**All gaps finalized.** Every row in the working-tree `WEAKNESS_REGISTER.md` is closed with a symbol, accepted with the dual-map citation, or deferred with an owner. Deferred rows are OPS-POSTIZ, OPS-QUEUE, REL-UNDEPLOYED, REL-CI-ASR, and REL-CI-ARCH. C2-F03 is closed. Live machine facts stay deferred because this checkout is not fingerprint `f65411d5`.

### Dispatch order

Dependencies first. Audit may fan out after U0’s file list is trusted. Remediate stays serial on shared hot files.

| Wave | Modules | Parallelism |
|------|---------|-------------|
| 0 | **U0**, then **H-INT** | Serial. H-INT reads the partition U0 confirms. Do not retag `partition.json` with X-keys. |
| 1 | **C1**, **C2**, **C3**, **C4**, **C5**, **C7**, **C8**, **C9**, **C10**, **H-DOCS**, **X-SEC**, **X-REL**, **X-DATA** | Audit in parallel. Remediate: **C1** and **X-DATA** share `ledger.py` (picking) and run one after the other. **C4** and **C5** share `prompts.py` (picking and rfd) and do not run at the same time. **C10** edits `views_common.py` only on a publish branch. |
| 2 | **C6**, then **X-DEP** | Serial. X-DEP interprets the drainable queue only after C6 has stated what `publish_due` / `needs_reconcile` / Postiz / Zernio actually do on this HEAD. `reconcile.py` and `post/zernio.py` / `post/postiz.py` / `post/run.py` are publish hot files. `crosspost.py` is picking — a C6 change that needs both is two branches. |
| 3 | **H-CI**, then the parent Finalize pass | Last. Gates encode closed findings. Then register, master map, and score. |

`partition.json` puts `reconcile.py` and the learning modules in **C7**, and `post/metrics/*` in **C6**. This index follows the operator brief: **C6** owns `reconcile.py` and the publish wire; **C7** owns metrics and learning and does not edit `reconcile.py`. **H-INT** records that split; it does not silently retag the file.

### Subagent brief template

```text
Skill: production-audit
Phase: Audit | Remediate | Finalize
Module: <ID> — <title>
Subject: d2cded31e29e027e4ed55bfdb99b9a93ac4f024e (branch audit/production-audit-2026-09)
Paths: <primary paths>
Entry symbols: <symbols>
Pack: <docs/audit/packs/….md or none>
Register IDs: <IDs and the re-verified status in AUDIT_MODULE_INDEX.md>
Lane: <lane or unrestricted; hot files that must not be taken>
Return: findings-table diff (or “no pack change”) + evidence commands run + symbol citations. Remediate units use skill fix-defect and return the owner path (branch, files, check.sh, arch regen if scanned source moved). Do not commit unless asked. Do not run pytest.
```

Remediate units swap the first line to `Skill: fix-defect`.

## Executive table

Statuses match [WEAKNESS_REGISTER.md](WEAKNESS_REGISTER.md) as of the 2026-10-17 working tree. “Remaining work” is this wave, not the pre-remediate snapshot. A pack section titled “Full checklist 2026-10-17” is a symbol pass. It is not a substitute for re-reading the dirty files against `origin/main`.

| ID | Title | Pack | Register status | Remaining work |
|----|-------|------|-----------------|----------------|
| U0 | Codemap / partition / stem drift | [U0-structural-delta.md](packs/U0-structural-delta.md) | **U0-F01 closed** (stale mark, traces not regenerated) | None in product code. Do not treat the stale mark as a regenerated trace |
| C1 | Data model and persistence | [C1-data-model.md](packs/C1-data-model.md) | **C1-F01 closed** (`get_logger` on the unrestorable wipe path) | `config.py` stays on publish. No C1 code wave in this pass |
| C2 | Ingest | [C2-ingest.md](packs/C2-ingest.md) | **C2-F01 closed**, **C2-F02 closed**, **C2-F03 closed**, **C2-F04 closed** | None. `config.isolate_vocals` matches the raise. `vocals.py` was not edited |
| C5 | Caption, hooks, hashtags | [C5-caption-hashtags.md](packs/C5-caption-hashtags.md) | **C5-F01 closed** (`hashtags.ship_from_lock`) | None. Full checklist 2026-10-17 is on the pack. `prompts.py` was not edited |
| C3 | Clip production | [C3-clip-production.md](packs/C3-clip-production.md) | **C3-F01 accepted**, **C3-F02 closed**, **C3-F03 closed** | An empty rc-0 re-render no longer stamps the old mp4. A second `extract_frames_grid` pass is still reachable. `clip.py` was not edited |
| C4 | Moments and personas | [C4-moments-personas.md](packs/C4-moments-personas.md) | **C4-F01 closed**, **C4-F02 closed**, **C4-F03 closed**, **C4-F04 closed** | Studio `regenerate_hook` uses the same script check as ingest. `prompts.py` is with C5 |
| C9 | Studio backend | [C9-studio-backend.md](packs/C9-studio-backend.md) | **C9-F01 closed**, **C9-F02 closed**, **C9-F03 closed** | A queued post that becomes imminent during hook regeneration is refused before `render_moment`. Repeated approve does not rewrite queued times |
| C10 | Studio views and templates | [C10-studio-views.md](packs/C10-studio-views.md) | **C10-F01 closed**, **C10-F02 closed**, **C10-F03 closed**, **C10-F04 closed** | A failed card or feed swap re-renders the card with the error inside it. `schedule.js` still re-reads `#schedule-body` |
| C6 | Crosspost and publish | [C6-crosspost-publish.md](packs/C6-crosspost-publish.md) | **C6-F01 closed**, **C6-F02 closed** | Dirty-tree pass found no C6-F03. Dry-run does not POST. IG/TT and Zernio `content` are `posted_text_for`; Postiz YouTube `content` is `Post.caption` |
| C7 | Metrics and learning | [C7-metrics-learning.md](packs/C7-metrics-learning.md) | **C7-F01 closed**, **C7-F02 closed** | Dirty-tree pass found no C7-F03. `_read_queries` has no successor. `reconcile.py` was not edited |
| C8 | Ops, CLI, daemon, doctor | [C8-ops-cli.md](packs/C8-ops-cli.md) | **C8-F01 closed**. **OPS-SHA** closed. **OPS-SHA-DAEMON** is not a new defect | `daemon.ensure` (`daemon.py:393`) matches the doctor miss-or-mismatch rule. Launchd policy was not changed |
| H-CI | Gates | [H-CI-gates.md](packs/H-CI-gates.md) | **H-CI-F01 closed** | Parent extends `tests/test_production_audit_gates.py` only for new pack finding IDs, last |
| H-DOCS | Trace / CODEMAP drift | [H-DOCS-drift.md](packs/H-DOCS-drift.md) | **H-DOCS-F01 closed** by stale mark | Traces were not regenerated. That is not full closure of the 196-vs-124 gap |
| H-INT | Architecture policy | [H-INT-invariants.md](packs/H-INT-invariants.md) | **H-INT-F01 accepted** | Dual map stays. `reconcile.py` is C7 in `partition.json` and C6 for edits |
| X-SEC | Secrets | [X-SEC-secrets.md](packs/X-SEC-secrets.md) | **SEC-IGPW closed**, **SEC-ENV closed** | 69 cap does not bind. Do not edit `config.py` |
| X-DEP | External deps | [X-DEP-deps.md](packs/X-DEP-deps.md) | **OPS-ZERNIO closed**. **OPS-POSTIZ** and **OPS-QUEUE** deferred | 2026-10-17 `uv run fanops doctor` exit 1: docker down, Postiz unreachable, 5 drainable past-due (463.5h), 851 queued |
| X-REL | Release | [X-REL-release.md](packs/X-REL-release.md) | **REL-PROT** and **REL-CI-E2E** closed. **REL-UNDEPLOYED**, **REL-CI-ASR**, **REL-CI-ARCH** deferred | Protection re-read matches `unit (fast, no toolchain)`. ASR run `38868030626` failed. No architecture `schedule` event in the last 5 runs. REL-UNDEPLOYED stays open until a PR merges this branch to `main` |
| X-DATA | Ledger immutability | [X-DATA-ledger.md](packs/X-DATA-ledger.md) | **X-DATA-F01 closed** | Ledger was not wiped. Do not edit `ledger.py` |

Each module **Register.** bullet below matches [WEAKNESS_REGISTER.md](WEAKNESS_REGISTER.md) on this working tree. The committed 2026-09-12 register on `d2cded31` is the baseline those bullets replaced.

## U0 — codemap / partition / stem drift

- **Paths.** `docs/CODEMAPS/partition.json`, `docs/CODEMAPS/full-trace-index.md`, `docs/CODEMAPS/anomalies.md`, `scripts/codemap_extract/ast_extract.py`, `scripts/codemap_extract/build_graphs.py`. 196 `src/fanops/**/*.py` files on HEAD. `partition.json` keys equal that set (`live_py_count` 196, `indexed_cluster_stems` 124, `assigned_stale` empty). Cluster counts: C1 15, C2 14, C3 18, C4 10, C5 25, C6 32, C7 15, C8 25, C9 29, C10 13. Templates (71), `studio/static/`, `tools/`, `scripts/`, `.githooks/`, and `.github/workflows/` are outside the Python totality gate.
- **Entry symbols.** `partition.json` `modules`; `test_live_partition_covers_every_module`.
- **Pack.** [packs/U0-structural-delta.md](packs/U0-structural-delta.md).
- **Register.** **U0-F01 closed.** The 2026-10-17 banner marks the 2026-07-11 trace ledger stale. Live count 196. `partition.json` `live_py_count` 196, `indexed_cluster_stems` 124. Prose was not regenerated.
- **Lane.** Unrestricted.
- **Audit.** Diff the frozen cluster table against `partition.json` `modules`. List Python paths the trace index does not name. Confirm `git ls-files 'src/fanops/**/*.py'` is 196 on this SHA.
- **Remediate.** Doc path only: refresh or explicitly mark the frozen index. One owner. No product behaviour change in this module.
- **Finalize.** U0-F01 closed by the stale mark. Traces were not regenerated.

## C1 — data model and persistence

- **Paths.** `src/fanops/ledger.py` (`Ledger`, `_migrate`, `_file_lock`), `ledger_sqlite.py`, `ledger_bridge.py`, `ledger_wipe.py` (`snapshot_is_restorable`), `models.py`, `control.py`, `controlio.py`, `ids.py`, `errors.py`, `log.py`, `paths_rebase.py`, `stage_lock.py`, `config.py`, `config_introspect.py`, `settings.py`.
- **Pack.** [packs/C1-data-model.md](packs/C1-data-model.md).
- **Register.** **C1-F01 closed.** `execute_wipe` passes `cfg` into `snapshot_is_restorable`. The unrestorable path and the manifest failure call `get_logger(cfg)`. `ledger.py` was not edited.
- **Lane.** picking owns `ledger.py` and `models.py`. publish owns `config.py`. A C1 branch that needs `config.py` stops and reports.
- **Audit.** Schema migrate chain, lock around ledger writes, wipe snapshot restorability, and whether a failed restorability check is visible on `run.log`. Overlap with X-DATA (no destructive repair) and X-SEC (`config_introspect`, `settings`).
- **Remediate.** `fix-defect` on `ledger_wipe` logging if the audit still wants `get_logger`. Do not take `config.py` on a picking branch.
- **Finalize.** C1-F01 closed with `get_logger` on the unrestorable wipe path.

## C2 — ingest

- **Paths.** `ingest.py` (`stage_inbox_candidates`, `ingest_drops`, `download_url`), `ingest_shard.py`, `discover.py`, `transcribe.py`, `transcribe_engine.py`, `vocals.py` (`_demucs_env`, `isolate_vocals`), `media_probe.py`, `frames.py`, `audio_energy.py`, `bands.py`, `intro_match.py`, `speech_trust.py`, `adjust.py`, `artifacts.py`.
- **Pack.** [packs/C2-ingest.md](packs/C2-ingest.md).
- **Register.** **C2-F01 closed** (`vocals._demucs_env`). **C2-F02 closed** (`artifacts._valid_transcript_json`). **C2-F03 closed** (`config.isolate_vocals` matches the raise). **C2-F04 closed** (`ingest.stage_inbox_candidates` skips a child `.pull`).
- **Lane.** Unrestricted.
- **Audit.** Confirm the demucs env path, inbox staging, and the ASR failure posture (`no_video_stream`, missing transcript). The dirty register’s REL-CI-ASR nightly failure is a live CI fact on `088702ca`, not a C2 code verdict on this HEAD.
- **Remediate.** Register and pack status only, unless the audit finds a new swallow. No code change required for C2-F01.
- **Finalize.** C2-F01 through C2-F04 are closed. `vocals.py` was not edited.

## C3 — clip production

- **Paths.** `clip.py`, `clip_ffmpeg.py`, `framing.py` (`_compute_track`, `extract_frames_grid`), `framing_outcomes.py`, `keyframes.py`, `produce.py`, `compose.py`, `overlay.py`, `overlay_reburn.py`, `reframe.py`, `reframe_apply.py`, `reframe_vf.py`, `impact_cut.py`, `stitch_render.py`, `render_fingerprint.py`, `visual_start.py`, `window_math.py`, `apply_common.py`.
- **Pack.** [packs/C3-clip-production.md](packs/C3-clip-production.md).
- **Register.** **C3-F01 accepted.** The second `extract_frames_grid` pass is still reachable because the cache key includes fps. **C3-F02 closed** (`clip_ffmpeg.ffmpeg_supercut_cmd` emits `-fps_mode cfr`). **C3-F03 closed** (`clip_ffmpeg._finish_publish` does not adopt an empty rc-0 file). `clip.py` was not edited.
- **Lane.** picking (`clip.py`).
- **Audit.** Framing cost claim against `_compute_track`. ffmpeg invocation boundary in `clip_ffmpeg.py`. Fail-closed vs fail-open on missing frames.
- **Remediate.** Docs: pack and register. Code only if a second grid pass is still reachable from another symbol.
- **Finalize.** C3-F01 stays accepted. C3-F02 and C3-F03 are closed.

## C4 — moments and personas

- **Paths.** `moments.py` (`request_moments`, `ingest_moments`, `request_moment_hooks`, `ingest_moment_hooks`), `casting.py`, `personas.py`, `persona_store.py`, `persona_directives.py` (`persona_facts`), `persona_levers.py`, `persona_research.py` (`persona_terms`), `accounts.py`, `batches.py`, `lever_docs.py`.
- **Pack.** [packs/C4-moments-personas.md](packs/C4-moments-personas.md).
- **Register.** **C4-F01 closed** (`persona_directives.persona_facts`). **C4-F02 closed** (`lever_docs.archetype_crosswalk_rows`). **C4-F03 closed** (`moments._hook_script_mismatch`). **C4-F04 closed** (`actions_edit.regenerate_hook`). `prompts.py` was not edited.
- **Lane.** picking (`casting.py`, `moments.py`). rfd shares `moments.py`. Do not remediate C4 and an rfd branch together.
- **Audit.** Moment ingest, hook pass, persona facts. Shared-file note for `prompts.py` lives on C5; C4 reads persona data, it does not own that hot file unless the diff touches it.
- **Remediate.** Register and pack. No code change for C4-F01.
- **Finalize.** C4-F01 through C4-F04 are closed.

## C5 — caption, hooks, hashtags, source tags

- **Paths.** `caption.py`, `caption_compose.py` (`posted_text_for`), `caption_ingest.py`, `hashtags.py` (`ship_from_lock`), `fanops_hashtags.py`, `hashtag_refresh.py`, `hashtag_scrape_policy.py`, `hookcheck.py`, `hookscore.py`, `source_tags.py`, `source_tags_scrape.py`, `source_tags_shortlist.py`, `source_tags_sidecar.py`, `source_tags_walk.py`, `ig_hashtag_scrape.py`, `ig_safari_shell.py`, `ig_web_scrape.py`, `llm.py`, `llm_json.py`, `llm_errors.py`, `prompts.py`, `prompts_caption.py`, `recaption.py`, `tagging.py`, `text.py`. Skill: `.claude/skills/fanops-hook-hashtag/SKILL.md` (dirty in the working tree; read HEAD).
- **Pack.** [packs/C5-caption-hashtags.md](packs/C5-caption-hashtags.md).
- **Register.** **C5-F01 closed.** `vet_hashtags` is absent. `hashtags.ship_from_lock` is picks ∩ lock, cap n, empty lock yields `[]`, no floors, no backfill.
- **Lane.** `prompts.py` is picking and rfd. Hashtag modules are unrestricted. Do not run concurrent with C4 if both would edit `prompts.py`.
- **Audit.** Ship path from lock to `posted_text_for`. Source-lock scrape error versus `play_count` (`source_tags_scrape.ensure_source_lock` is the #1303 change on this HEAD). Hook authorship and language gate stay in the skill’s rules; this module collects whether the code matches them.
- **Remediate.** Register and pack drop `vet_hashtags`. A new caption-lock defect found in audit is a separate `fix-defect` on a branch that does not take `prompts.py` unless picking or rfd owns that wave.
- **Finalize.** C5-F01 closed with `ship_from_lock`. No C5-F02.

## C6 — crosspost and publish

- **Paths.** `src/fanops/post/` (`run.py` `_publish_one`, `postiz.py`, `zernio.py`, `dryrun.py`, `providers.py`, `publish_requeue.py`, `publish_archive.py`), `src/fanops/post/CLAUDE.md`, `crosspost.py` (`surface_time`, `_JITTER_MAX`, `_STEP_MIN`), `reconcile.py` (`reconcile_due`, `reconcile_posts`), `pipeline.py` (`_stage_crosspost`, `_publish_safe`), `postiz_lifecycle.py`, `stranded_posts.py`, `responder.py`, `gate_keys.py`, `escalation.py`. `post/metrics/*` is partitioned C6 and audited under C7.
- **Pack.** [packs/C6-crosspost-publish.md](packs/C6-crosspost-publish.md).
- **Register.** **C6-F01 closed.** `crosspost.py` has module-level `assert _JITTER_MAX < _STEP_MIN`. **C6-F02 closed.** `_NET_POST_FIELDS` includes `publish_hour` and `publish_dow`. `publish_due`, `needs_reconcile`, and drainable membership are unchanged versus HEAD.
- **Lane.** publish: `reconcile.py`, `post/run.py`, `post/postiz.py`, `post/zernio.py`, `post/__init__.py`. picking: `crosspost.py`. One branch does not edit both.
- **Audit.** Dry-run versus live send, ambiguous-response park (`needs_reconcile`), idempotency of a retried publish, and the vendor wire (`posted_text_for` versus `Post.caption`, Zernio `content` / `mediaItems`) per `post/CLAUDE.md`. This verdict is the input X-DEP uses for the drainable queue.
- **Remediate.** Register and pack for C6-F01. Any new publish defect is `fix-defect` on a publish branch, or a picking branch if the only file is `crosspost.py`.
- **Finalize.** C6-F01 and C6-F02 are closed. X-DEP was not re-opened.

## C7 — metrics and learning

- **Paths.** `meta_graph.py`, `learn_doctor.py`, `metrics_schedule.py`, `moment_hook_learning.py`, `variant_learning.py`, `variant_amplify.py`, `variant_transfer.py`, `timing_bias.py`, `p4_dim_bias.py`, `reach_ranking.py`, `field_shape.py`, `validation_gate.py`, `track.py`, `reconcile_mirror.py`, `post/metrics/postiz_read.py`, `post/metrics/zernio_read.py`. `reconcile.py` stays on C6 for edits.
- **Pack.** [packs/C7-metrics-learning.md](packs/C7-metrics-learning.md).
- **Register.** **C7-F01 closed.** `meta_graph._read_queries` is absent. No successor. **C7-F02 closed.** `timing_bias.timing_bias_hour_for` returns None unless `FANOPS_TIMING_BIAS` is on. `reconcile.py` was not edited.
- **Lane.** Unrestricted for the metrics files. publish if someone reaches for `reconcile.py` — stop and leave it on C6.
- **Audit.** Where metric reads fail closed, where they return empty, and whether a corrupt cache is logged. Confirm `_read_queries` is gone rather than renamed into a silent `None`.
- **Remediate.** Register, pack, and score bullet. Code only if a successor of `_read_queries` still swallows a corrupt file.
- **Finalize.** C7-F01 and C7-F02 are closed. The score no longer cites `_read_queries` as open work.

## C8 — ops, CLI, daemon, doctor

- **Paths.** `cli.py` (`main`, `cmd_doctor`, `cmd_daemon`, `cmd_up`), `cli_dispatch.py`, `cli_parser.py`, `cli_run.py`, `daemon.py` (`ensure`, `install`, `_last_heartbeat_code`, `_version_signal`), `daemon_studio.py`, `daemon_siblings.py`, `doctor.py` (`doctor_report`, `_deploy_code_check`, `_daemon_liveness_check`, `_postiz_reach_check`, `_zernio_reach_check`), `health.py`, `health_model.py`, `health_probes.py`, `health_projectors.py` (`half_live_state`), `health_types.py`, `init_flow.py`, `canary.py`, `cutover.py`, `digest.py`, `audit.py`.
- **Pack.** [packs/C8-ops-cli.md](packs/C8-ops-cli.md).
- **Register.** **C8-F01 closed** (`health_projectors.half_live_state`). **OPS-SHA closed** on `doctor._deploy_code_check` and `daemon.ensure` (loaded miss-or-mismatch).
- **Lane.** Unrestricted. No `src/fanops` hot file in this set.
- **Audit.** Doctor check list, daemon install path, and the documented start command (`fanops doctor`, `fanops up`). Startup failure posture for missing env. Health payload of Studio stays a fingerprint read; do not restart launchd.
- **Remediate.** Register and pack for C8-F01. OPS-SHA is the same loaded miss-or-mismatch rule on `daemon.ensure`.
- **Finalize.** C8-F01 closed with `half_live_state`. OPS-SHA closed with `daemon.ensure`. Full checklist 2026-10-17 is on the pack.

## C9 — studio backend

- **Paths.** `studio/app.py`, `studio/app_request.py`, `studio/actions.py`, `actions_approve.py`, `actions_edit.py`, `actions_wipe.py` (`confirm_wipe`), `actions_recover.py`, `actions_run.py`, `actions_schedule.py`, `actions_publish.py`, `actions_crosspost.py`, `actions_casting.py`, `actions_segments.py`, `actions_common.py`, `app_routes_live.py` (`do_wipe_confirm`), `app_routes_review.py`, `app_routes_schedule.py`, `app_routes_run.py`, `app_routes_home.py`, `app_routes_golive.py`, `app_routes_hashtags.py`, `app_routes_media.py`, `app_routes_personas.py`, `studio/golive.py`.
- **Pack.** [packs/C9-studio-backend.md](packs/C9-studio-backend.md).
- **Register.** **C9-F01 closed** (`actions_wipe.confirm_wipe`). **C9-F02 closed** (`actions_approve._approve_ids_with_render`). **C9-F03 closed** (`actions_edit.regenerate_hook` refuses an imminent queued post before `render_moment`).
- **Lane.** Unrestricted. These files are not in `lanes.json`.
- **Audit.** Mutation routes: approve, edit, schedule, wipe, recover. Server-side checks, CSRF or same-origin assumptions on `127.0.0.1`, and idempotency of a repeated approve. Wipe stays the operator path X-DATA cites; this module does not add a new wipe.
- **Remediate.** Register, pack, and score bullet. Code only for a new guard gap the audit names.
- **Finalize.** C9-F01 through C9-F03 are closed.

## C10 — studio views and templates

- **Paths.** `studio/views.py` (`zero_post_clips`), `views_common.py`, `views_home.py`, `views_review.py`, `views_schedule.py`, `views_run.py`, `views_posted.py`, `views_hashtags.py`, `views_live.py`, `views_golive.py`, `views_library.py`, `views_results.py`, `fanops_account_stats.py`, `src/fanops/studio/templates/` (71 files on HEAD), `src/fanops/studio/static/` (`studio.css`, `review.js`, `schedule.js`, `posted.js`, `gates.js`, `drawer.js`, `upload.js`). Templates and static are outside `partition.json`.
- **Pack.** [packs/C10-studio-views.md](packs/C10-studio-views.md).
- **Register.** **C10-F01 closed** (`app_routes_home` passes `zero_post_clips`). **C10-F02 closed** (`schedule.js` listens on `document`). **C10-F03 closed** (Posted Show more keeps filters and stops on the last page). **C10-F04 closed** (a failed card or feed swap re-renders the card with the error). `views_common.py` was not edited.
- **Lane.** publish owns `views_common.py`. Other view files are unrestricted.
- **Audit.** Home, review, schedule, and posted views against the route kwargs. Empty, error, and permission states on those templates. The residue in the two doc files is the finding.
- **Remediate.** Update the frozen bullets so they match the route. Publish lane if `views_common.py` must change; otherwise docs only.
- **Finalize.** C10-F01 through C10-F04 are closed.

## H-CI — gates

- **Paths.** `tests/test_production_audit_gates.py`, `tests/test_ci_registry_validator.py`, `tools/ci/` (`cli.py`, `checks.py`, `live.py`, `registry.py`), `scripts/check.sh`, `scripts/lane_guard.py`, `scripts/pr_collision_guard.py`, `.github/workflows/ci.yml` (job `unit`), `ci-e2e.yml` (`e2e`, `launchd-e2e`), `nightly.yml` (`dependency-audit`, `asr-smoke`), `architecture.yml` (`impact`, `controls`, `reconcile`), `lane-guard.yml` (`lane-guard`), `.github/ci-control-registry.yml` (`required_contexts`: `unit (fast, no toolchain)`), `.githooks/pre-commit`, `pre-push`, `post-merge`.
- **Pack.** [packs/H-CI-gates.md](packs/H-CI-gates.md).
- **Register.** **H-CI-F01 closed.** `tests/test_production_audit_gates.py` requires every pack finding ID to appear in this working-tree register.
- **Lane.** ci. No `src/fanops` hot files.
- **Audit.** After the other modules rewrite the register: the gate still sees 14 packs, every finding ID is in the register, and `partition.json` still totals C1–C10. Name the CI check that proves it (`unit (fast, no toolchain)` on the PR). Scheduled jobs are X-REL evidence, not this gate’s required context.
- **Remediate.** Extend the gate only for IDs this index adds (X-rows) if Finalize decides they must be machine-checked. Land this module last.
- **Finalize.** H-CI-F01 stays closed. The gate test is generic. No new assertion was added for the new pack IDs; they are covered because the register lists them.

## H-DOCS — trace / CODEMAP drift

- **Paths.** `docs/CODEMAPS/subsystem-traces/`, `docs/CODEMAPS/full-trace-index.md`, `docs/CODEMAPS/anomalies.md`, `docs/CODEMAPS/hashtag-lifecycle.md`, `README.md` links that `test_readme_links_partition_and_score` already requires.
- **Pack.** [packs/H-DOCS-drift.md](packs/H-DOCS-drift.md).
- **Register.** **H-DOCS-F01 closed** by `docs/CODEMAPS/subsystem-traces/STALE.md`. Last trace commit on this history is `f11d0af7` (2026-09-09). Traces were not regenerated. Live count 196, indexed stems 124.
- **Lane.** Unrestricted.
- **Audit.** Per cluster, either a trace exists or the index says the cluster is stale. C10’s `zero_post_clips` residue is shared with C10; H-DOCS owns the index, C10 owns the route citation.
- **Remediate.** Regen traces or mark each lagging cluster stale with an owner. Docs only.
- **Finalize.** H-DOCS-F01 closed by the stale mark. The 196-versus-124 gap is recorded, not regenerated.

## H-INT — architecture policy

- **Paths.** `tools/arch/policy/rules.py` (`ARCH-001`, “The subsystem partition is TOTAL”), `tools/arch/cli.py`, `.reports/architecture/derived/` (regen output; dirty in this working tree), `docs/CODEMAPS/partition.json`.
- **Pack.** [packs/H-INT-invariants.md](packs/H-INT-invariants.md).
- **Register.** **H-INT-F01 accepted.** Dual authority is still true: ARCH-001 is the S-subsystem totality; operator C-clusters are `partition.json`. The pack’s “partition.json is absent” clause is stale on this HEAD.
- **Lane.** ci.
- **Audit.** State both maps and the two disagreements this index cares about: `reconcile.py` is C7 in `partition.json` and C6 in the operator brief; `post/metrics/*` is C6 in `partition.json` and C7 here. X-modules must stay overlays. Confirm a scanned-source edit path is `python -m tools.arch regen` plus the architecture workflow.
- **Remediate.** Document the dual map and the C6/C7 file split in the pack. Do not merge the partitions and do not add X-keys.
- **Finalize.** H-INT-F01 stays accepted, with evidence from this HEAD. Pack table updated.

## X-SEC — secrets and config introspection

- **Paths.** `secret_provider.py` (`_SECRET_KEYS`, `is_secret_env_key`, `get_secret`), `config_introspect.py` (`_is_secret`, `_SECRET_SUFFIX`), `settings.py` (`FANOPS_IG_SCRAPE_PASSWORD`), `autopilot.py` (`set_env_var`, `unset_env_var`), `docs/CONFIG.md`, `scripts/scan-secrets.sh`. Overlaps C1 (`config_introspect`, `settings`) and C8 (`secret_provider`).
- **Pack.** None. Append a findings table under this heading or add `docs/audit/packs/X-SEC-secrets.md` in the audit phase.
- **Register.** **SEC-IGPW closed.** **SEC-ENV closed.** `config.py` was not edited for these rows.
- **Lane.** Unrestricted unless the fix edits `config.py` (publish).
- **Audit.** Secret suffix list versus Settings fields that hold credentials. Confirm the value is absent from `studio/static` and templates. Confirm `docs/CONFIG.md` write-only claim against `_is_secret`. No secret values in the findings table.
- **Remediate.** `fix-defect`: mask the scrape password in config introspection; lock `.env` rewrites. Two findings, two owner paths if they touch different files.
- **Finalize.** SEC-IGPW and SEC-ENV are closed. The 69 secrets cap does not bind.

## X-DEP — external deps as doctor exercises them

- **Paths.** `health_probes.py` (`postiz_dep_health`, `postiz_doctor_check`, `zernio_dep_health`, `_http_reachable`, `dep_health_list`), `doctor.py` (`_postiz_reach_check`, `_zernio_reach_check`, `_daemon_liveness_check`), `post/postiz.py`, `post/zernio.py`, `docs/POSTIZ_OPS.md`, `docs/POSTIZ_SETUP.md`. Overlaps C6 and C8.
- **Pack.** None.
- **Register.** **OPS-ZERNIO closed.** **OPS-POSTIZ deferred**, owner operator. **OPS-QUEUE deferred**, owner operator. 2026-10-17 doctor: docker down, Postiz unreachable, 5 drainable past-due, 851 queued. Ledger was not drained.
- **Lane.** publish for `postiz.py` and `zernio.py`. `doctor.py` and `health_probes.py` are unrestricted.
- **Audit.** After C6: what “drainable” means in `_daemon_liveness_check` versus `publish_due`, and which errors are Postiz-local versus code. Probe status-code handling for Zernio. Do not start docker or drain the ledger.
- **Remediate.** `fix-defect` for OPS-ZERNIO on `health_probes._http_reachable` / `zernio_dep_health`. OPS-POSTIZ process recovery is an operator action, deferred with owner “operator”, unless the audit finds the probe itself is wrong.
- **Finalize.** OPS-ZERNIO closed. OPS-POSTIZ and OPS-QUEUE stay deferred to the operator. X-DEP was not re-opened after C6.

## X-REL — deploy protection, rollback, undeployed gap

- **Paths.** `docs/GOLIVE.md` section “Rollback / safety”, `.github/ci-control-registry.yml` `required_contexts`, `tools/ci/live.py`, `doctor.py` `_deploy_code_check`, `daemon.py` `ensure`. Workflows listed on H-CI.
- **Pack.** None.
- **Register.** **OPS-SHA closed** on `doctor._deploy_code_check` and `daemon.ensure`. **REL-PROT closed.** **REL-CI-E2E closed.** **REL-UNDEPLOYED**, **REL-CI-ASR**, and **REL-CI-ARCH** are deferred to the operator.
- **Lane.** ci for the registry and workflows. `doctor.py` is unrestricted.
- **Audit.** Rollback section present. Required context list. Undeployed relation of this HEAD to the fingerprint. Deploy-code miss path. Do not change branch protection.
- **Remediate.** OPS-SHA is closed on `doctor._deploy_code_check` and `daemon.ensure`. Protection and scheduled-CI redness stay operator or ci-lane settings.
- **Finalize.** OPS-SHA, REL-PROT, and REL-CI-E2E are closed. REL-UNDEPLOYED, REL-CI-ASR, and REL-CI-ARCH stay deferred. The 84 cap still binds.

## X-DATA — ledger immutability

- **Paths.** `AGENTS.md` guardrail “Never wipe or reset the ledger”, `ledger.py`, `ledger_wipe.py`, `studio/actions_wipe.py` (`confirm_wipe`), `cli.py` `cmd_wipe` / `cmd_restore`. Overlaps C1 and C9.
- **Pack.** None.
- **Register.** **X-DATA-F01 closed.** Wipe stays operator-gated: preview token, then `snapshot_is_restorable`, then `execute_wipe`. `ledger.py` was not edited.
- **Lane.** picking (`ledger.py`). Wipe UI files are unrestricted.
- **Audit.** Agent-facing docs refuse wipe-as-repair. Operator wipe still requires the preview token C9 already enforces. No script in `scripts/` resets ledger state. Snapshots from `ledger_wipe` remain restorable (`snapshot_is_restorable`).
- **Remediate.** Open a new register ID only if the audit finds an unattended destructive path. Otherwise record “no finding” and leave the guardrail as the standing control.
- **Finalize.** X-DATA-F01 closed. The ledger was not wiped.
