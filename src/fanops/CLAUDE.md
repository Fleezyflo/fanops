<!-- Generated: 2026-07-03 | Source: docs/CODEMAPS + docs/CODEMAPS/subsystem-traces | Maintained by hand hereafter -->
# src/fanops — package map & hard invariants

Pure-Python pipeline. Deep reference: `docs/CODEMAPS/` (index: `docs/CODEMAPS/README.md`).
Product rules + semantics live in the ROOT `CLAUDE.md`; THIS file = package structure + coder invariants.

## Data-flow spine (4 content-addressed units, one JSON ledger)

`Source → Moment → Clip → Post`, driven by `pipeline.advance(cfg, base_time)` (`pipeline.py`):
ingest (short txn) → produce (lock-free) → main-txn state-flip stages → reconcile (out of lock) →
publish (out of lock) → summary. The main txn saves only on clean exit; any raise rolls the whole
pass back by design. Four LLM gates on the way through:
1. **moments (pick)** `moments.request_moments`/`ingest_moments` — the FIRST content judgment; all
   heavy per-source compute (Whisper, ffmpeg signals, keyframes) has ALREADY run before it (Finding 1).
2. **moment hooks** `moments.request_moment_hooks` — per-pick on-screen retention hook.
3. **casting** `casting.request_moment_casting` — per-account moment SELECTION (default ON).
4. **captions** `caption.request_captions` — hashtags-only, ≤4, deterministically vetted.

## The 10 clusters (→ trace docs in `docs/CODEMAPS/subsystem-traces/`)

- **C1** core data model & persistence — `models, ledger, ledger_wipe, ids, config, controlio, control, errors, log, stage_lock`
- **C2** ingest & acquisition — `ingest, discover, adjust, bands, frames, audio_energy, vocals, intro_match, transcribe`
- **C3** clip production & framing — `clip, framing, keyframes, stitch_render, overlay, impact_cut, compose, produce`
- **C4** moments/casting/personas — `moments, casting, casting_bias, personas, persona_directives, persona_levers, persona_research, persona_store, accounts, batches`
- **C5** caption/hooks/hashtags — `caption, hashtags, fanops_hashtags, tagging, hookcheck, hookscore, text, prompts, llm`
- **C6** crosspost/publish/post — `crosspost, pipeline, router, responder, signals, agentstep, autopilot, postiz_lifecycle, post/*` (see `post/CLAUDE.md`)
- **C7** metrics/reconcile/learning — `reconcile, track, meta_graph, metrics_schedule, validation_gate, learn_doctor, moment_hook_learning, variant_learning, variant_amplify, variant_transfer, p4_dim_bias, timing_bias`
- **C8** ops/CLI/daemon — `cli, daemon, doctor, cutover, cutover_postiz, health, digest, audit, timeutil, _fwrun`
- **C9/C10** Studio — `studio/*` (see `studio/CLAUDE.md`)

`config.py`(C1) is the hub every cluster reads; `ledger.py`(C1) is the only cross-cluster state channel
(flock-guarded, atomic-replace). No cluster mutates another's state directly.

## Hard invariants — a coder must NEVER break these

- **No-auto-publish.** Every `Post` is BORN `PostState.awaiting_approval` at the SOLE construction site
  `crosspost._mint_surface_post` (`crosspost.py`). Only `Ledger.approve_post` promotes → `queued`. Publish
  paths iterate `queued` ONLY, so an unapproved post is structurally unpublishable even on a live backend.
- **Dryrun/live boundary.** Two independent gates: `_post_provider`→`"dryrun"` when `not cfg.is_live`, AND
  `get_poster` refuses to build a `DryRunPoster` when live. `FANOPS_LIVE=1` is settable only via
  `studio/golive.go_live` (accounts-valid → live-ready channels → past-due-backlog → confirm).
- **Ledger cascade protection.** `ledger._delete_moment_cascade` checks `_PROTECTED_POST_STATES`
  (live + awaiting_approval + queued + retired) — re-ingest/reconcile can never drop an in-review/approved post.
- **Bias actuators amplify-only + validation-frozen.** Every reach-loop actuator (`p4_dim_bias`,
  `timing_bias`, `casting_bias`, `variant_amplify`) imports only `adjust.amplify` or writes an isolated prior
  file — NEVER retire/state-set/publish. All default-OFF (`.env`/shell-only), all gated by
  `validation_gate.learning_validated(cfg)`. Adds no new auto-publish path (biases GENERATION + SCHEDULE only).
- **Never mass-reformat** (no `black`/`ruff format`); the compact one-liner style is deliberate. Never raise
  the 60s pytest timeout (deadlock guardrail). Never run live `fanops` verbs speculatively.

## House pattern & blind spot

- **Fail-open with a logged breadcrumb** is the norm: a subprocess/parse failure degrades to a safe default
  AND logs first. The exceptions (silent swallows, diagnosability gaps) are inventoried in
  `docs/CODEMAPS/anomalies.md` (9 sites) — none produce wrong output.
- **Env-var reference:** `docs/CONFIG.md` (64 vars; projection of `system-lens-map.md` §1.2).
- **Blind spot:** the name-based call graph in `.reports/` CANNOT see aliased imports
  (`from x import f as _y`), lazy in-function imports, dict-of-lambdas dispatch, Jinja filters, or argparse
  `type=` callbacks — so "zero callers" is a LEAD, not a verdict. Sweep for `<name> as <alias>` and lazy
  imports before declaring anything dead. 5 "dead" flags were live (see anomalies.md validation note).
