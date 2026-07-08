<!-- Generated: 2026-07-08 | Method: deterministic AST extraction (.reports/ast_extract.py) + derived call/import graphs (.reports/build_graphs.py) + hand-verified semantic sync | Files scanned: 109/109 src/fanops/*.py @ 0bf6ab0 | Token estimate: ~2400 -->
# FanOps Full-Codebase Trace Index

Master index for a zero-omission, function-by-function trace of every module under `src/fanops/`.
Built in two layers: **deterministic** (AST-parsed structural index → import graph → name-based
reverse call graph → ruff lint, all stdlib-only, in `.reports/`) plus **semantic** (10 Sonnet-agent
traces, one per subsystem cluster, each documenting every function's behavior/side-effects/callers
with file:line citations, in `docs/CODEMAPS/subsystem-traces/`). This file is the map between them — for
narrative product/architecture context see [architecture.md](architecture.md) and [data.md](data.md);
this file is the raw coverage ledger and anomaly index.

## Method & deterministic artifacts

| Artifact | What it is | Location |
|---|---|---|
| `structural_index.json` | Every module's imports/functions/classes/methods/module-level calls/line numbers, AST-parsed | `.reports/` |
| `import_graph.json` | Per-module `imports_from` / `imported_by`, resolved incl. relative imports | `.reports/` |
| `call_graph.json` | Name-based reverse call graph: 1,318 callables, each with `calls`/`called_by_in_repo` | `.reports/` |
| `unreferenced_candidates.json` | 55 best-effort dead-code leads (excludes dunders/decorated/tests) — **leads, not verdicts**; see Dead-code below | `.reports/` |
| `ruff_report.json` | Full-repo `ruff check` — **0 findings against src/** (2 historical findings were in the analysis scratch script itself, fixed) | `.reports/` |
| `ast_extract.py` / `build_graphs.py` | The two extractor scripts themselves (stdlib-only, re-runnable) | `.reports/` |

No third-party call-graph tool was available in this environment (pyan3/pydeps/snakefood/pycg/jedi
all absent, confirmed via `pip list`) — the extractor above was purpose-built. Its call graph is
**name-based, not type-resolved**: it cannot see dynamic dispatch (dict-of-callables, e.g.
`responder.py`'s `_PROMPT[kind](payload)`), Jinja template filter/macro registration, or
argparse `type=` callbacks. Every cluster trace below cross-checked its cluster's
`unreferenced_candidates.json` entries against real usage by hand — see Dead-code section.

## The 10 clusters — zero-gap coverage

Every one of the 109 modules under `src/fanops/` is assigned to exactly one cluster below
(verified programmatically: `structural_index.json`'s 109 paths − cluster union = ∅, cluster
union − 109 paths = ∅, zero paths assigned twice).

| # | Cluster | Files | Trace doc | Lines |
|---|---|---|---|---|
| C1 | Core data model & persistence | models, ledger, ledger_wipe, ids, config, controlio, control, errors, log, stage_lock (10) | [C1_data_model.md](subsystem-traces/C1_data_model.md) | 386 |
| C2 | Ingest & source acquisition | ingest, discover, adjust, bands, frames, audio_energy, vocals, intro_match, transcribe (9) | [C2_ingest.md](subsystem-traces/C2_ingest.md) | 180 |
| C3 | Clip production & framing | clip, framing, keyframes, stitch_render, overlay, impact_cut, compose, produce (8) | [C3_clip_production_framing.md](subsystem-traces/C3_clip_production_framing.md) | 434 |
| C4 | Moments, casting & personas | moments, casting, personas, persona_directives, persona_levers, persona_research, persona_store, accounts, batches (9) | [C4_moments_casting_personas.md](subsystem-traces/C4_moments_casting_personas.md) | 235 |
| C5 | Caption, hooks & hashtags | caption, hashtags, fanops_hashtags, tagging, hookcheck, hookscore, text, prompts, llm (9) | [C5_caption_hooks_hashtags.md](subsystem-traces/C5_caption_hooks_hashtags.md) | 277 |
| C6 | Crosspost, publish & post | crosspost, pipeline, router, responder, signals, agentstep, autopilot, postiz_lifecycle, post/{__init__,compress,dryrun,media,metrics,postiz,providers,run,zernio} (17) | [C6_crosspost_publish_post.md](subsystem-traces/C6_crosspost_publish_post.md) | 417 |
| C7 | Metrics, reconcile & learning | reconcile, track, meta_graph, metrics_schedule, validation_gate, learn_doctor, moment_hook_learning, variant_learning, variant_amplify, variant_transfer, p4_dim_bias, timing_bias (12) | [C7_metrics_learning.md](subsystem-traces/C7_metrics_learning.md) | 203 |
| C8 | Ops, CLI & daemon | cli, daemon, doctor, cutover, cutover_postiz, health, digest, audit, timeutil, _fwrun, __init__ (11) | [C8_ops_cli_daemon.md](subsystem-traces/C8_ops_cli_daemon.md) | 320 |
| C9 | Studio backend (Flask routes + actions) | studio/{__init__,app,app_routes_golive,app_routes_live,app_routes_personas,app_routes_review,app_routes_run,app_routes_schedule,actions,actions_approve,actions_casting,actions_common,actions_run,actions_wipe,golive,personas,preview_media} (17) | [C9_studio_backend.md](subsystem-traces/C9_studio_backend.md) | 892 |
| C10 | Studio views (read-only projections) | studio/{views,views_common,views_live,views_results,views_review} (5) | [C10_studio_views.md](subsystem-traces/C10_studio_views.md) | 302 |

**109/109 modules covered. 3,646 total lines of per-function trace documentation.**

## Data-flow spine (cluster → cluster)

```
C2 ingest ──Source(catalogued)──> C1 ledger
C2 ──transcript+signals──> C3 clip production ──rendered Clip/Render──> C1
C4 moments/casting/personas ──owner-stamped Moment.affinities + affinity_admits──> C3, C5, C6
C3 ──rendered Clip (fingerprinted mp4)──> C5 caption gate
C5 ──captioned Clip (hashtags ≤4, per-account hook)──> C6 crosspost
C6 ──Post(awaiting_approval)──> C1 ledger ──[operator approves via C9]──> Post(queued)
C6 ──publish_due/publish_now──> real network POST (Postiz/Zernio) ──published──> C7
C7 metrics/learning ──analyzed metrics, lift_score──> C1
C7 ──validated bias artifacts (amplify-only)──> learned/transferred hooks (C5), hour_hint (C6), p4_dim amplify (C3)
C8 ops/CLI/daemon ──drives the advance loop──> C2 through C7, end to end
C9 Studio backend ──every browser-triggered mutation, one Ledger.transaction each──> C1
C10 Studio views ──pure read projections of C1 (Ledger.load, no writes)──> Jinja templates via C9 routes
```

C1 (`models.py`/`ledger.py`) is the hub every other cluster reads and writes through — the single
JSON ledger, flock-guarded, atomic-replace. No cluster talks to another cluster's state directly;
all cross-cluster communication is mediated by ledger unit state transitions (see
[data.md](data.md) for the full Source→Moment→Clip→Post lifecycle).

## Consolidated safety-critical verdicts

These are the properties an adversarial trace was specifically tasked with proving or disproving,
extracted verbatim from each cluster's audit section. See [anomalies.md](anomalies.md) for the
full anomaly ledger.

| Property audited | Cluster | Verdict |
|---|---|---|
| No-auto-publish gate (a Post can never reach a real network POST without an explicit operator approval) | C6 | **HOLDS.** Only `PostizPoster.publish`/`ZernioPoster.publish` ever touch the network, both called exclusively from `post/run.py:_publish_one`, gated on `post.state is PostState.queued`. The sole `Post(...)` construction site (`crosspost.py:269`) hardcodes `state=PostState.awaiting_approval`. `Ledger.approve_post` is the sole promoter to `queued` and is never called from within C6's 17 files. |
| Approval lifecycle never bypasses the ledger | C9 | **HOLDS.** Every approve/reject/batch-approve path in `actions_approve.py` funnels through `led.approve_post`; no direct state mutation found. |
| Dryrun→live boundary cannot be silently crossed | C6, C9 | **HOLDS**, via two independent gates: `_post_provider` returns `"dryrun"` unconditionally when `not cfg.is_live`; `get_poster()` separately refuses to construct a `DryRunPoster` when `cfg.is_live`. `FANOPS_LIVE=1` is settable only through `studio/golive.py:go_live`, itself behind accounts-validate → live-ready-channels → past-due-backlog-gate → explicit confirm. Postiz API key confirmed write-only (never rendered back to any template/response). |
| Ledger wipe requires multi-step operator confirmation | C9 | **HOLDS with one caveat.** Four-gate order verified in code (typed word "REMOVE" → mandatory snapshot → snapshot-restorability check → `execute_wipe`'s own re-check), every terminal outcome logged. Caveat: `app_routes_live.py:29-34`'s `do_wipe_confirm` has no *server-side* check that `do_wipe_preview` ran first — "preview before confirm" is a UI convention, not a server-enforced invariant. The destructive-action gates themselves are unaffected. |
| Re-ingest/reconcile can never drop an in-review or approved post | C1 | **HOLDS.** `ledger._delete_moment_cascade` checks `_PROTECTED_POST_STATES` (live states + awaiting_approval + queued + retired) at both the post-loop check and the clip-drop check. |
| Bias/learning actuators are amplify-only, never touch retire/cascade/publish | C7 | **HOLDS, no violations found.** Every live actuator (`variant_amplify`, `p4_dim_bias`, `timing_bias`) imports exclusively `adjust.amplify` or writes an isolated prior file — never a retire/state-setter/publish call. (`casting_bias` removed P11.) All independently kill-switched, all validation-frozen behind `learning_validated(cfg)` (`cutover.json["metrics_confirmed"]`, thresholds `_MIN_ATTRIBUTED_N=8`/`_MIN_VALUES=2`). |
| `cutover.py`/`cutover_postiz.py` never touch the ledger | C8 | **HOLDS, grep-proven.** `grep -n "Ledger\|led\." cutover.py cutover_postiz.py` returns zero matches; sole write path is `cutover.py:38 write_json_atomic(cfg.cutover_path, ...)`. |
| Upload ingestion path is traversal-safe and size-capped | C9 | **HOLDS, verified beyond the CLAUDE.md summary.** Extension validation, secure_filename + raw-and-sanitized traversal check, inbox-bound `is_relative_to` resolve (independent second check), atomic `.uploadpart`→`os.replace`, `MAX_CONTENT_LENGTH` cap, filename-collision discriminator, failed-probe cleanup — all confirmed at cited lines. |
| Timezone resolution fails closed to UTC, never silently wrong-zone | C8 | **HOLDS.** `timeutil.py:38-39 _operator_zone` — confirmed exactly matching CLAUDE.md's claim. |
| Studio views layer is pure-read (no ledger/control-file mutation) | C10 | **HOLDS, with two narrow documented exceptions**, neither a layering violation: (1) `views_common.postiz_health_for_banner` performs one live network GET behind a 30s module-level cache; (2) `views_results.lineage_stats` mutates its own transient argument objects in place (never ledger/control-file state) — this second one **does violate the project's own immutability coding-style rule**, flagged as a follow-up, not a safety issue. |

## Dead-code candidates (55 raw leads → triaged)

`unreferenced_candidates.json` flags 55 top-level functions with zero in-repo callers by
name-based matching. Each cluster triaged its own candidates by hand against actual usage
(grep for dict-dispatch, decorator registration, Jinja filters, template macros, argparse
callbacks). Outcome:

- **Confirmed false positives** (real callers the AST tool structurally cannot see — dispatch tables,
  default-parameter injection, Jinja-filter/argparse registration, and **aliased or lazy imports**):
  - `prompts.py`'s 3 live prompt-builders (`moment_pick_prompt`/`moment_hook_prompt`/`caption_prompt`) via `responder.py:_PROMPT[kind]` dict-dispatch (C5). (`moment_casting_prompt` removed P11.)
  - `compose.py`'s `_moviepy_prepend_render`/`_moviepy_render`/`_probe` — default-parameter values (C3).
  - `timeutil.to_local_display`/`to_local_input` — Jinja filter registration (C8); `cli._http_url` — argparse `type=` callback (C8).
  - `llm.claude_json` — called via `studio/actions.py:138-139` (`from fanops.llm import claude_json`) (C5).
  - `post/compress.py:persist_post_shrink` — **called via a lazy in-function import at `studio/actions.py:395-396`** (C6). *(Corrected on validation — the first pass mislabeled this as genuinely dead.)*
  - `accounts.py:set_backend`, `ensure_channel`, `set_status`, `set_ig_user_id` — **all called via aliased imports (`... as _accounts_set_backend`) in `studio/golive.py`** (188/506, 501, 543/558, 381 respectively) (C4). *(Corrected on validation — the first pass mislabeled all four as dead; the name-based call graph cannot resolve the `_accounts_*` alias.)*
- **Confirmed genuinely dead** (no caller anywhere — re-verified against source with an alias-and-lazy-import sweep):
  - `accounts.py:set_channel_routing`, `set_framing` (C4) — two unwired account-mutation primitives; `set_channel_routing` is notable as "the documented fix for the cisumwolfhom incident" per its own docstring, never actually wired into a route.
  - `persona_levers.py:is_exempt`, `channels` (C4) — the latter's own docstring claim of being read by "the M4 manifest" is inaccurate (manifest calls `channels_of` instead).
  - `learn_doctor.py:load_verdict` (C7) — either dead or a sidecar-file direct-read bypasses it.
  - `timing_bias.py:timing_prior_hour` (C7) — docstring claims a caller that doesn't exist.
  - `caption.py:normalize_variation_axis`, `coherent_variation` (C5) — dormant P2 creative-variation-axis machinery, a tracked follow-up not an oversight.
  - `timeutil.py:is_past_due` (C8) — sibling `is_due_or_past` is used instead.
  - `ingest.py:download_source` (C2) — own docstring says "kept for any direct caller/test"; the real CLI path composes `download_url`+`ingest_drops` separately.
- **Plausible-but-unconfirmed** (likely template-only Jinja consumers, not verified against
  `templates/` line-by-line in this pass): `views.py:run_next_step`, `zero_post_clips`,
  `metrics_stale_hint`; `views_common.py:accounts_in`, `term_def`; `views_results.py:operator_error`,
  `bar_pct`; `views_review.py:provenance_chips`, `group_review_by_account_surface`,
  `group_review_by_batch` (all C10). Of these, `zero_post_clips` is separately confirmed as a **real
  bug** below (referenced by a template but never passed by the view), not merely unreferenced.

Full per-candidate detail with file:line citations lives in each cluster's own trace document.

## Real bugs found (not just anomalies — confirmed logic defects)

1. **`views.zero_post_clips` is dead-on-arrival wiring** (C10/C9 boundary) — the function exists
   and `home.html` references `{% if zero_post_clips %}`, but `app.py`'s `render_template` call for
   the Home route never passes it as a kwarg. The conditional block silently never renders. This is
   the one place the trace found a genuine template/view wiring defect, not merely an anomaly.
2. **CLAUDE.md documentation drift on the Go-Live mechanism** — the project notes describe
   `FANOPS_POSTER=postiz` as being set through the Go-Live confirm path; current code
   (`studio/golive.py:go_live`, comment "D12: go_live NEVER writes FANOPS_POSTER") never writes that
   variable at all. The real per-channel live-routing setter is `set_account_backend`. The
   underlying safety property (no live routing without creds+confirm) still holds — this is a docs
   staleness issue, not a code defect. CLAUDE.md project notes should be updated to reflect the
   current per-channel mechanism (this Go-Live wording predates the per-platform-integration-id
   change documented in `fanops-account-onboarding-hardening`).

## Silent-failure inventory (unlogged exception swallows)

The codebase is disciplined about logging before fail-open (confirmed cluster by cluster), with a
small number of exceptions where a broad `except Exception` swallows with **zero log trail**:

| File:line | Function | Cluster | Risk |
|---|---|---|---|
| `ledger_wipe.py:188` | `snapshot_is_restorable` | C1 | Low — fail-closed direction is safe, but the error reason itself is lost |
| `vocals.py:35-36` | `_demucs_env` | C2 | Low — should narrow to `ImportError`, not `Exception` |
| `persona_directives.py:287` | `persona_facts` | C4 | Low-medium — only unlogged swallow in C4; every sibling fail-open path logs |
| `meta_graph.py:337-338` | `_read_queries` | C7 | Low — fail-closed safe, but a corrupt hashtag-budget file is undetectable from logs |
| `preview_media.py:31-32,36-38` | preview resolution ladder | C9 | Low (read-only) — a stale/wrong WYSIWYG preview has zero debug trail |
| `app.py:158-160` | `_account_arg` | C9 | Low — same read-helper-layer pattern as preview_media |
| `views.py` (4 of 5 blocks in) | `build_system_strip` | C10 | **Medium — the one real legibility gap in C10.** Runs on every page load; 4 of 5 internal try/excepts are silent (`pipeline_status`, posts-scan, `insights_blocked`, `half_live`) while sibling read-models in the same file all log. A persistent bug in any of the 5 sub-computations degrades the nav-strip warning badges invisibly. |

None of these are correctness bugs in the sense of producing wrong *output* — every one fails
toward a safe/degraded default. They are diagnosability gaps: if the swallowed condition recurs
persistently, there is no log line to find it by. Ranked by how likely a recurring failure is to
go unnoticed in production, `build_system_strip` (C10) is the one worth a follow-up fix; the rest
are low-traffic paths (wipe-safety check, preview rendering, one persona-store load).

## Cluster-specific stats (functions traced)

| Cluster | Files | Approx. traced symbols |
|---|---|---|
| C1 | 10 | ~90 (full state-machine + ~55 env vars enumerated) |
| C2 | 9 | ~55 |
| C3 | 8 | ~95 (incl. full reframe/render ladder) |
| C4 | 9 | ~75 |
| C5 | 9 | ~68 |
| C6 | 17 | ~140 (largest single cluster by file count) |
| C7 | 12 | ~68 |
| C8 | 11 | ~85 (37-verb CLI inventory) |
| C9 | 17 | ~150 (largest single cluster by trace length, 892 lines) |
| C10 | 5 | ~60 |

Totals reconcile against the deterministic count: 1,138 top-level functions + 180 class methods
(111 classes) = 1,318 callables in `call_graph.json`, matching the AST extractor's structural
index exactly (109/109 modules parsed with zero AST errors).

## How to regenerate

```bash
cd "/Users/molhamhomsi/Moh Flow Fanops"
python3 .reports/ast_extract.py src > .reports/structural_index.json
python3 .reports/build_graphs.py
ruff check src/
```

Both scripts are pure stdlib, deterministic, and safe to re-run after any code change — they do
not require the 10 subsystem trace documents to be regenerated (those are the semantic layer, run
via Sonnet agents per cluster, only needed when the intent of a specific area changes materially,
not on every edit).
