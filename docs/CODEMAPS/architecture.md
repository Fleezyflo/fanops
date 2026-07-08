<!-- Generated: 2026-06-19 | Files scanned: 74 src + 113 test | Token estimate: ~1700 | incl. M6 intro-tease + content-lifecycle (born-awaiting_approval gate, day-bucket archive, cross-account) -->
# FanOps Architecture

Single-operator local CLI (`fanops`) that turns long-form source video into scheduled
cross-posted clips. Pure-Python src layout (`src/fanops/`), one JSON ledger as the only
state store, external heavy lifting via subprocesses (ffmpeg/whisper/yt-dlp), publishing via
Postiz (self-hosted) or Zernio (TikTok), IG metrics via the Meta Graph. Autonomous learning features are default-OFF, fail-safe.
Optional Flask-based Studio web cockpit (imported lazily; core install Flask-free).
Optional MoviePy produced-clip compositing with template cards + overlays (imported lazily; core install MoviePy-free).
Asset memory (M1): every Source carries `origin_kind` — native (the artist's own footage the pipeline cuts) vs third_party (outside footage handed in: remembered + held aside, INERT to clip-production until chosen).
Content lifecycle: every Post is BORN `awaiting_approval` (the human approval gate — `publish_due`/`publish_now` iterate ONLY `queued`, so nothing ships unattended even on a live backend); the operator approves in Studio Review → `queued`, then publish stamps `published_at` and writes a day-bucketed `06_published/<day>/` archive. Re-ingest/reconcile can NEVER wipe an awaiting/approved/retired post (`_PROTECTED_POST_STATES`). A shipped post can be reposted (fresh awaiting_approval clone) or cross-posted onto another onboarded account (`crosspost_to_account`, repost-freely).
Structural hooks (M2–M6, all default-OFF): a read-only ROUTER classifies each clean Moment's `hook_strategy` (`text` | `clean_final` | `clean_awaiting_strategy:<key>` | `stitch:<format>`); a `stitch_plan` approval spine gates every structural hook behind a born-unpostable `stitch_draft` ClipState + two operator approvals. Two concrete formats: **impact-cut** (M4, deterministic cut-before-peak "wait for it" tease) and **intro-tease** (M6, an LLM-vision matcher pairs a clean clip with a third-party intro asset, then a compose-PREPEND lays the tease over a continuous music bed). `mine_suggestions` (M5) is the generic ranked/capped routine pass both formats feed; render-approved dispatches by `strategy_key`. Each format has its own env gate; a disabled format's reservations + approved plans FREEZE (forward-only kill-switch, never silently demoted).

Account-First Studio (all default-safe; an unbatched / casting-OFF run is render/post byte-identical): a **Batch** is a named, account-targeted ingest grouping (`Batch.target_accounts`; `[]` = all-active sentinel; SCHEMA_VERSION 8 — an earlier additive migration injects the empty `batches` map). `create_batch` mints a content-addressed id and (when an active-handle set is supplied) flags a zero-active-target batch via `Batch.error_reason` (advisory — still mints). The target is denormalized onto `Source.batch_id` → `Post.batch_id` and ENFORCED by a per-surface SKIP in `crosspost_clips` (the casting-OFF path) so `target_accounts=['@a']` is not a silent no-op — `@b`'s surfaces birth no post and a `batch_target_skip`/`batch_target_summary` breadcrumb records the exclusion (the only persistent record, since excluded surfaces never become posts). The default-ON `FANOPS_ACCOUNT_CASTING` routing flag (`casting.affinity_admits`) only NARROWS further via owner-stamped `Moment.affinities`; both skips fail-open. New surfaces: a status **Home** (accounts + connection + headline counts + a clickable `len(batches)` entry-point into Review + per-account post counts + a zero-result-batch warning); **Review** batch-grouped with a `?batch=` drill-in, a header showing targets/state/created_at, read-only cast-affinity chips, and inline hook **re-burn** (updates `Moment.hook` and re-renders the owner clip via `actions.reburn_hook` — never the dead `meta_captions['hook']`); **Schedule/Posted** now paginated, per-row batch-labelled, with a `?batch=` filter and a read-only per-batch "N posted · mean lift" rollup; a Go-Live **casting toggle**. Learning stays validation-frozen behind `learning_validated` — no batch flag unfreezes it (the per-batch rollup is a pure display read over `Post.metrics[lift_score]`, no writer). The orphan flags `account_first`/`batch_studio` were never built (dropped, not deferred).

## Pipeline (the `advance` pass, pipeline.py — short ingest tx → lock-free pre-warm → main commit tx)

```
01_inbox media ──ingest──> Source(catalogued)
  ──transcribe(whisper)──> transcribed ──signals(ffmpeg)──> signalled
  ──moments(agent req/resp via agentstep+llm; the per-handle on-screen RETENTION hook is authored here)──> moments_decided -> Moment(decided)
  ──clip(ffmpeg render per aspect; band→snap→strongest-FRAME start [P1, FANOPS_VISUAL_START]; burns the on-screen RETENTION hook top-center)──> Clip(rendered)
  ──caption(agent + brand gate; hashtags VETTED to ≤4 from a reach-ranked set)──> captioned
  ──crosspost(schedule per account×platform surface; stamps creative provenance + created_at)──> Post(awaiting_approval)
  ──[operator APPROVES in Studio Review — the human gate; publish iterates ONLY queued, nothing ships unattended]──> Post(queued)
  ──publish_due/publish_now(post/run.py)──> submitting -> submitted -> published (+published_at, +06_published/<day> archive)
  ──track(pull Graph/Postiz metrics)──> analyzed ──adjust──> amplify/retire
```

- Per-unit error quarantine: any stage failure parks THAT unit in `error` + reason; never wedges the pass.
- Crash-safe publish: `submitting` persisted BEFORE the network call; ambiguous results -> `needs_reconcile`, never blind re-POST (reconcile.py polls).
- Slow ops that must NOT hold the flock run outside transactions: yt-dlp download (`pull`), `claude -p` (responder.py), and (Phase D) the heavy subprocess stages — whisper, ffmpeg signals, ffmpeg render — which `pipeline._prewarm` runs lock-free into deterministic on-disk artifacts (transcript JSON, signals sidecar, `cid.render.json` fingerprint + mp4) BEFORE the main commit transaction re-runs them and SKIPS the warm subprocess. A multi-minute render no longer starves a concurrent Studio write / second pass.
- Asset memory (M1): the source loop SKIPS `origin_kind == "third_party"` sources (guarded in BOTH `_prewarm` and the in-lock advance), so handed-in footage is catalogued + remembered but never transcribed/clipped/posted. Third-party intake lands in a PEER `01_thirdparty_inbox` (outside the native `cfg.inbox.rglob`, so a native pass can't reach + mislabel it).
- Structural hooks (M2–M6, default OFF): AFTER the critic + BEFORE the render loop, `router.route_moments` annotates each clean Moment's `hook_strategy` (renders nothing; an existing `clean_awaiting_strategy:*` reservation is never re-routed away — forward-only). AFTER the render loop, `pipeline._enabled_strategies(cfg)` gates the producer/render block per format. **intro-tease (M6, `FANOPS_INTRO_TEASE` + `FANOPS_RESPONDER=llm`)** first opens an LLM-vision matcher gate (`intro_match.request_intro_match`/`ingest_intro_match`, an agentstep request/response gate; fail-open — no answer → no plan) that writes ranked pairings to `Moment.intro_matches`. `stitch_render.mine_suggestions` then runs BOTH producers (`_impact_cut_candidates` + `_intro_tease_candidates`) through one ranked/top-N-capped/deduped pass, re-routing each drained moment to `stitch:<key>`. APPROVED plans render LOCK-FREE (`_prewarm`→`prewarm_approved_stitches`, dispatched by strategy: impact_cut→ffmpeg cut-window, intro_tease→MoviePy compose-PREPEND) then commit in-lock (`render_approved_stitches`) — a COMMON supersede precheck (stale base fingerprint→auto-dismiss, live base post→block) then per-strategy adopt (impact_cut renders in-lock as fallback; intro_tease ONLY adopts a prewarmed composite via the compose fingerprint, never MoviePy under the flock — an un-prewarmed plan waits, with a retry-cap parking flaky pairs). A successful commit sets the plan `in_use` + retires the queued base post. A disabled format's approved plans freeze with a kill-switch warning. A SECOND operator gate releases the reviewed `stitch_draft`→`captioned`. The bare clip always ships regardless (fail-open).

## Module map (src/fanops/)

| Area | Files |
|---|---|
| Orchestration | cli.py (verbs+catch ladder), pipeline.py (advance), config.py (env+paths) |
| Ingest/discover | ingest.py, discover.py (00_review intake), vocals.py (Demucs vocal isolation before ASR, fail-open to raw audio), transcribe.py (faster-whisper [asr] engine; legacy `whisper` CLI fallback), signals.py |
| Decide/render | moments.py, clip.py (fit_window/snap + `pick_visual_start` strongest-frame cut, sidecar-cached for Phase D), frames.py (pure luma+contrast frame scoring from ffmpeg signalstats — no pixel lib), overlay.py (hook/subtitle burn, build_ass, `hook_legibility_warnings`), caption.py (brand gate + hashtag vet), prompts.py (moment/caption, shared `_hook_spec`) |
| Hook + hashtag quality | keyframes.py (source-frame extraction = the vision author's eyes), hookcheck.py (deterministic weak-hook guard — `is_weak_hook`), hashtags.py (vet_hashtags ≤4 reach-vetted), text.py (em-dash sanitizer). Sourced knowledge: `.claude/skills/fanops-hook-hashtag/SKILL.md` |
| Creative provenance (P1, for P3/P4 attribution) | one writer per field: Moment.hook_pattern (moments ingest), Clip.first_frame_kind/cut_seconds (clip render), Post.{hook_pattern,first_frame_kind,clip_profile,cut_seconds} (crosspost). The dims a future insight/learning pass groups reach by — currently STAMPED only (no learner reads them yet) |
| Asset memory (M1) | the `origin_kind` axis (native vs third_party) end-to-end: models.Source.origin_kind + SourceState.{discovered,retired}; ingest `_catalogue_file` spine (origin_kind/inbox threading, sha-conflict WARN, write-once); config.thirdparty_inbox (peer staging dir); pipeline source-loop inert guard; ledger.retire_source (cascade, file kept) + rebuild_catalog (disk↔ledger reconcile); studio.asset_catalog + save_thirdparty_uploads/run_ingest_thirdparty + /library tab |
| Structural hooks (M2–M6) | router.py (read-only Moment classifier: `STRATEGY_KEYS`, `route_moments`, `awaiting`/`stitched`; M6 reserves intro_tease for clean-no-peak + forward-only reservation guard); models.{StitchPlan(+rank_score/rationale/render_attempts), StitchState, ClipState.stitch_draft, PostState.retired, Moment.{hook_strategy, intro_matches}, IntroMatchItem/Decision} + `stitch_plan_id`; ledger stitch_plan ops (add/approve/dismiss — in-lock idempotent) + the v1→v2 stitch_plans migration step (now part of the v3 hop-chain) + reconcile preserves `clean_awaiting_strategy`; impact_cut.py (deterministic cut-before-peak planner); **intro_match.py (M6 LLM-vision matcher gate — request/ingest/pending agentstep gate; ephemeral per-(moment,candidate-set,version) key; fail-open)**; compose.py (M6 `_compose_fingerprint` + `prepend_intro` compose-PREPEND with continuous looped music bed, fail-open, lock-free); stitch_render.py (`mine_suggestions` ranked/capped/deduped pass over BOTH producers `_impact_cut_candidates`+`_intro_tease_candidates`; `prewarm`/`render_approved_stitches` dispatch by strategy_key + common supersede precheck + per-format `strategies` filter + `approved_disabled_count` kill-switch + `MAX_INTRO_RENDER_ATTEMPTS` retry-cap); clip.render_moment cut-window override + duration-validity; config.{hook_router, impact_cut, intro_tease}; pipeline `_enabled_strategies` gate + matcher wiring; studio stitches tab (strategy-agnostic: approve/dismiss ordered by rank + release drafts); digest surfaces stitch_plan errors |
| Compositing (optional [compose]) | compose.py (MoviePy produced clip layer w/ template cards, fail-open to base clip) |
| Agent I/O | agentstep.py (request/response files), llm.py (`claude -p`, 300s cap), responder.py |
| Schedule/post | crosspost.py (deterministic schedule), tagging.py, post/{run,media,payload,postiz,zernio,dryrun,metrics}.py |
| Publishing | post/run.py (_submit_one, publish_due, publish_post — the Publish-now engine; `_archive_published` day-bucketed 06_published record, fail-open) |
| Content lifecycle | born-awaiting_approval gate (crosspost + Ledger.approve_post/reject_post/unapprove); `_PROTECTED_POST_STATES` wipe-guard (ledger reconcile cascade); created_at/published_at stamps (models); day-bucketed Review + Posted (studio/views.{review_buckets day-sort, posted_library, group_posted_by_day}); cross-account onboard (studio/actions.{crosspost_to_account, crosspost_all_to_account}, repost_post); `gc` retention (cli + config.gc_keep_days, sweeps 05_scheduled); v2→v3 created_at migration (ledger._migrate_v3_created_at) |
| Learn (default OFF) | track.py (writes LIFT_SCORE), adjust.py (classify/amplify/retire), variant_learning.py (best_hooks/ucb_rank), variant_amplify.py, variant_transfer.py, p4_dim_bias.py (P4(b) cross-account reach dim-bias, autonomous via cli.run) |
| State/infra | ledger.py (flock+atomic JSON), models.py (pydantic units + LIFT_SCORE), accounts.py (+ atomic write_account_id), ids.py (SHA1 content-addressing), timeutil.py (single parse site), log.py (TAB-column run.log), errors.py, digest.py (+public gate_state), validation_gate.py |
| Autonomous ops | autopilot.py (one-cmd: enable llm responder + launchd daemon), daemon.py (launchd supervisor around `run`), doctor.py (readiness pre-flight checks), cutover.py + cutover_postiz.py (Postiz throwaway-probe learning prover) — both OPTIONAL early shortcuts: learning auto-unfreezes on the first real non-degraded live metric (track._auto_validate_metrics_shape; a row is degraded if a high-weight key is absent or present-but-null), so the cutover probe is no longer required |
| Studio (optional [studio]) | studio/app.py (Flask factory, lazily imported), studio/views.py (read models), studio/actions.py (one transaction per mutation), studio/golive.py (Postiz connect/config surface) |

## CLI verbs (cli.py)

**Core pipeline:** `run` (cron entrypoint: respond+advance loop + learning passes) · `advance` · `status` ·
`ingest` · `pull <http(s) url>` · `discover` / `intake` · `respond` · `digest` · `track` ·
`adjust` · `amplify-variants` · `reconcile` · `gc` (retention sweep: retired/analyzed renders + 05_scheduled
payloads older than FANOPS_GC_KEEP_DAYS [default 30]; refuses keep_days<1; never touches 06_published).

**Recovery:** `resolve` / `unhold` / `retry-source` / `retry-metrics`.

**Autonomous ops:** `autopilot` (enable llm responder + install daemon) · `daemon {install,status,stop}`
(launchd supervisor) · `doctor` (readiness pre-flight) · `cutover {auth,post,metrics,lift}` (Postiz verify).

**Publishing:** `compose` (optional [compose] extra; MoviePy produced-clip render outside the flock).

**Studio:** `studio` (Flask on 127.0.0.1:8787, debug=False; optional [studio] extra).

Typed-error catch ladder -> one clean stderr line + exit 1/2, never a traceback.

## Studio routes (studio/app.py)

```
GET  /                  -> redirect /review
Tabs (GET, lock-free Ledger.load per request):
  /review /review/live /review/refresh   (approval worklist + live auto-poll)   /schedule (approved bucket)   /lift
  /posted (all-time shipped library, day-bucketed)   /run /run/status   /library (M1 third-party)
  /stitches (M3/M4)   /candidates (discover)   /publish (produced-clip grid)   /gates   /golive
GET  /media/<post_id> /clips/<clip_id> /clip-thumb/<clip_id> /review-thumb/<eid>   (send_file, bounded INSIDE cfg.base)

Approval lifecycle (post-approval-lifecycle + content-lifecycle):
POST /posts/{approve,reject} · /posts/unapprove/<post_id>   (Review: promote awaiting→queued / discard / send back)
POST /schedule/{respread} · /schedule/move/<post_id> · /schedule/unapprove/<post_id>   (Schedule cockpit)
POST /posts/repost/<post_id>              (Posted: fresh awaiting_approval clone of a shipped clip)
POST /posts/crosspost/<clip_id> · /posts/crosspost-all   (Phase 4: onboard a shipped clip onto another account — repost-freely)

Pipeline + edit:
POST /run/{ingest,pull,upload,advance,prepare}   (pipeline entry from browser; htmx returns _run_panel)
POST /publish/posted/<post_id>            (mark published manually)
POST /publish/now/<post_id>               (ship one queued post immediately)
POST /reschedule/<post_id> · /caption/<post_id> · /regenerate/<post_id>   (edit a post; regenerate re-runs the caption model)
POST /snooze/<clip_id> · /unhold/<clip_id>   (hold / unhold a clip)

POST /candidates/approve/<eid>            (approve discover footage for ingest)
POST /gates/answer/<kind>/<key>           (answer moment/caption agent gates from browser)
POST /library/upload                      (M1: stage + catalogue a handed-in third-party asset; inert to clips)
POST /stitches/{approve,dismiss,release}  (M3/M4: operator-gate stitch_plans + release a rendered stitch_draft -> captioned)
POST /golive/{config,account/add,refresh,map,live,dryrun,validate}   (operator-gated Postiz onboarding + learning-cutover probe)
```

All POST routes return ActionResult (ok + detail/error) wrapped in _result.html (htmx swap).
Gates/Run panels re-render on success with fresh status (lock-free Ledger.load).

## Output levers (what changes the produced clips/posts)

The control surface — every input that changes what the engine outputs:

| Lever | Where read | Changes |
|---|---|---|
| `context.md` (brand brief) | moments/caption `_guidance` | clip-pick + hook + caption voice (injected verbatim into every agent prompt) |
| `prompts._hook_spec` | moment/caption | the ONE retention-hook definition (open-loop/curiosity/comment-bait/POV, no hype). The deterministic opening-template guard (hookcheck `is_weak_hook`) is the no-LLM floor after the author |
| `hashtags.py` vetted set + `vet_hashtags` | caption ingest | the ≤4 reach-vetted tags actually posted (model picks from the menu; code hard-caps) |
| `FANOPS_RESPONDER` (llm/manual) | pipeline/responder | who answers moment/caption gates (llm = autonomous; manual = operator) |
| `FANOPS_CLIP_PROFILE` + bands.py | clip.fit_window | clip length band (talk 12-22s vs song 18-35s) + snap window |
| `FANOPS_VISUAL_START` | clip.pick_visual_start | **default ON** (P1): refine the cut entry onto the strongest opening FRAME within a bounded shift (luma+contrast via ffmpeg signalstats); fail-open to the band/snap start; sidecar-cached so the in-lock commit re-probes nothing |
| `burn_subs` | clip/overlay | transcript captions burned (default OFF; hook is NOT the transcript) |
| `tuning.json` offbrand_en/ar | caption brand gate | what HOLDS a caption as off-brand |
| `accounts.json` personas | caption per-surface | per-account voice/angle |
| `FANOPS_VARIANT_*` (learning/amplify/ucb/transfer) | caption bias + post-loop | hook A/B learning (default OFF, fail-safe) |
| `FANOPS_HOOK_ROUTER` | config → pipeline | M2 (default OFF): read-only Moment `hook_strategy` classifier after the critic; renders nothing (observe-only annotation) |
| `FANOPS_IMPACT_CUT` | config → pipeline | M4 (default OFF): produce + render operator-approved impact-cuts (cut-before-peak); needs the router on to reserve moments |
| `FANOPS_INTRO_TEASE` | config → router/pipeline | M6 (default OFF): pair a clean clip with a third-party intro asset + compose-PREPEND a "wait for it" tease; needs the router on + `FANOPS_RESPONDER=llm` (the LLM-vision matcher gate) |
| `FANOPS_POSTER` | config → publish | dryrun (no-op) vs postiz (IG) / zernio (TikTok) — real posts |

## Learning-gate seams (the C1-sensitive area)

caption.request_captions biases on `variant_learning.best_hooks` (or `ucb_rank` when UCB on);
`cli.run` executes THREE independent post-loop passes when `cfg.is_live_backend`, each its own
kill switch + try/except: classify→amplify/retire (`_learn_pass`), then `apply_variant_amplify`
(`cfg.variant_amplify`), then `apply_p4_dim_bias` (`cfg.p4_dim_bias`, P4(b) cross-account reach
dim-bias — symmetric with variant_amplify, no longer manual-verb-only). All amplify-only and
validation-frozen (inert until `learning_validated`); all share `adjust.MAX_AMPLIFY_PER_SOURCE`.
Isolation invariant: the amplify/cascade path never imports the learner — enforced by AST tests in
tests/test_variant_learning.py / test_variant_amplify.py.
