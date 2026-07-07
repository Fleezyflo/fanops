# C10: Studio Views

## Files covered (all 5 read in full)

1. `src/fanops/studio/views.py` (824 lines) — read
2. `src/fanops/studio/views_common.py` (293 lines) — read
3. `src/fanops/studio/views_live.py` (56 lines) — read
4. `src/fanops/studio/views_results.py` (724 lines) — read (note: file is 725 lines including trailing content; every line read)
5. `src/fanops/studio/views_review.py` (698 lines) — read (699 lines including trailing content; every line read)

Cross-checked against `.reports/call_graph.json` (filtered to the 5 `fanops.studio.views*` modules — 96 matching entries) and `.reports/structural_index.json`. `views.py` is explicitly documented (line 1-2, 16-24) as a **facade**: it re-exports every public name from `views_common`, `views_review`, `views_results`, and `views_live` so `fanops.studio.views.X` keeps resolving for templates/routes/tests that import the old flat module. Route files (`app.py`, `app_routes_run.py`, `app_routes_review.py`, `app_routes_schedule.py`, `app_routes_golive.py`, `app_routes_personas.py`, `app_routes_live.py`) are OUTSIDE this cluster but were grepped for `render_template(` call sites to build the view→template mapping — they are not read in full, only grepped for the specific mapping evidence.

## Pipeline/data-flow overview (route -> view function -> template)

```
                         ┌─────────────────────────────────────────────┐
                         │   Flask route (app.py / app_routes_*.py)    │
                         │   OUTSIDE this cluster — owns HTTP verbs    │
                         └───────────────────────┬─────────────────────┘
                                                  │ calls
                                                  ▼
        ┌─────────────────────────────────────────────────────────────────────┐
        │                     C10: Studio Views (this cluster)                 │
        │                                                                      │
        │  views_common.py  (shared primitives: pagination, glossary,          │
        │                     time/suggestion math, batch-title lookup,        │
        │                     Postiz-health-banner probe — cached 30s)         │
        │        ▲                    ▲                    ▲                  │
        │        │ imported by        │ imported by        │ imported by      │
        │  views_review.py      views_results.py      views_live.py           │
        │  (Review tab:         (Schedule/Posted/Lift  (Live library tab —    │
        │   cards/matrix/       tabs: ScheduleRow,      ImportedMedia,         │
        │   lanes/pivot)         PostedRow, LiftRow)     disjoint from Posts)  │
        │        ▲                    ▲                                       │
        │        └──────────┬─────────┘                                       │
        │                   │ re-exported + extended by                       │
        │              views.py (facade: Home/Go-Live/Personas/Run/Library/   │
        │                       Stitches/Gates read-models + the facade       │
        │                       re-export block for views_common/_review/     │
        │                       _results/_live)                               │
        └─────────────────────────────┬───────────────────────────────────────┘
                                       │ returns dataclasses / dicts / lists
                                       ▼
                         ┌─────────────────────────────────────────────┐
                         │        Jinja templates (render_template)     │
                         │  home.html / review.html / schedule.html /   │
                         │  posted.html / lift.html / golive.html /     │
                         │  personas.html / live_library.html / etc.    │
                         └─────────────────────────────────────────────┘
```

Each Studio tab loads a fresh `Ledger.load(cfg)` (lock-free read) inside its view-model builder and assembles dataclasses/dicts; the route then hands those straight to `render_template`. **Mutations are explicitly documented (views.py:2, views_results.py header docstring) as living in `actions*.py`**, a sibling module OUTSIDE this cluster — the views layer is designed as a pure read/query layer that routes call before rendering, and actions modules the routes call in response to POSTs.

## View -> template mapping

| View function (module) | Route file (evidence) | Template(s) rendered | Studio tab/page |
|---|---|---|---|
| `home_status`, `home_batches`, `account_work_counts`, `review_handoff` | `app.py:365` | `home.html` | Home |
| `daemon_health` | `app.py:388` | `_daemon_health.html` | Home (htmx health pill) |
| `pending_stitches` | `app.py:405, 409` | `stitches.html`, `_stitches_panel.html` | Stitches |
| `review_candidates` | `app.py:428` | `candidates.html` | Footage (discover candidates) |
| `pipeline_status`, `asset_catalog` | `app_routes_run.py:24,31,36,82,91` | `run.html`, `_run_status.html`, `_run_panel.html`, `library.html`, `_library_panel.html` | Run (Make/prepare) + Library |
| `publish_queue`, `inflight_watch` | `app.py:452,473` | `publish.html`, `_reconcile_strip.html` | Publish (manual/by-hand tab) |
| `gate_rows` | `app.py:480` | `gates.html` | Gates |
| `review_buckets`, `review_counts`, `review_progress`, `source_universe`, `account_pivot_rows`, `review_matrix`, `account_lanes`, `review_awaiting_by_account`, `paginate`, `source_choices` (all via `_review_context` in `app_routes_review.py`) | `app_routes_review.py:88,92,108` | `review.html`, `_review_body.html`, `_review_live.html` | Review |
| `surface_for_post` | `app_routes_review.py:213,253,273` | `_surface_edit.html` | Review (per-surface editor swap) |
| `schedule_rows`, `schedule_lanes`, `group_schedule_by_account`, `due_publish_plan`, `schedule_cockpit`, `inflight_watch`, `schedule_auto_ship`, `_publish_mode_label` | `app_routes_schedule.py:30` | `schedule.html`, `_schedule_panel.html` | Schedule |
| `inflight_watch` | `app_routes_schedule.py:86` | `_reconcile_strip.html` | Schedule (reconcile strip) |
| `lift_rows`, `metric_peaks` | `app_routes_schedule.py:111` | `lift.html` | Lift (learning/results) |
| `posted_library`, `posted_batch_rollup`, `lineage_stats`, `group_posted_by_day` | `app_routes_schedule.py:128` | `posted.html`, `_posted_panel.html` | Posted |
| `live_library`, `live_library_scope` | `app_routes_live.py:15` | `live_library.html` | Live (MOL-27 "viewed there, not authored here") |
| `golive_status`, `golive_accounts`, `golive_demoted_accounts`, `daemon_health` | `app_routes_golive.py:15,22,168,173,178` | `golive.html`, `_golive_panel.html`, `golive_page.html` | Go Live |
| (health probe, not a C10 view fn — `postiz_health_probe` in `post/postiz.py`) | `app_routes_golive.py:163,164` | `_health_pills.html`, `_golive_health.html` | Go Live (health widget) |
| `personas_page` | `app_routes_personas.py:16,21` | `personas.html`, `_personas_panel.html` | Personas |
| (persona compose preview — `studio.personas.preview_compose`, NOT a C10 view fn) | `app_routes_personas.py:31,39` | `_persona_drawer.html`, `_persona_compose.html` | Personas (lever preview drawer) |
| `build_system_strip`, `build_spine` | `app.py:365` (passed into every full-page render via shared context, per `create_app`) | every full-page template (via the shared `_system_strip`/spine partial) | Global nav strip (all tabs) |

Note: `_result.html`, `_publish_outcome.html`, `error.html` are rendered from **mutation results** (`actions.*` return values), not from a C10 view-model read — they appear in the grep output but are out of scope for this table (their inputs are `actions.py` results, not `views*.py` reads).

## Per-file breakdown

### `views.py` — the Home/Go-Live/Personas/Run/Library/Stitches/Gates read-models + facade

- `GoLiveChannel` (`@dataclass`) — one platform's Postiz integration id + optional backend override for a GoLiveAccount. Pure data holder.
- `GoLiveAccount` (`@dataclass`) — one active account's Go-Live row: handle, persona, channels, persona_id, ig_user_id, `meta_token_set` (BOOL only — token itself never carried). Pure data holder.
- `GoLiveStatus` (`@dataclass`) — the whole Go-Live tab read-model: mode/is_live/postiz_url/`key_set` (BOOL only)/accounts/checks/notes/zernio/learning_validated/creative_variation/account_casting/clip_profile/responder_mode/daemon/demoted/variant_* flags. Pure data holder — no secret value ever stored here (only booleans for "is it set").
- `HomeStatus` (`@dataclass`) — the `/` status-home read-model: mode/is_live/counts/accounts/by_account. Pure data holder.
- `HomeBatch` (`@dataclass`) — one batch row for Home's deep-link list: id/name/targets/state/created_at/posts_born/is_zero_result. Pure data holder.
- `review_candidates(cfg)` — **PURE-READ**. Globs `cfg.review/*.jpg` (top-level only, excludes `approved/`) for discover-candidate thumbnails. Filesystem read only, no ledger. Called by `app.create_app` (feeds `candidates.html`).
- `_MANUAL_QUEUE` (module const) — the set of `PostState`s the manual Publish tab surfaces.
- `publish_queue(cfg, *, now=None, account=None)` — **PURE-READ**. `Ledger.load` + filters posts to `_MANUAL_QUEUE`, computes `due`, sorts due-first, then by schedule; optional account filter. Called by `cli.cmd_publish_queue`, `app.create_app` (feeds `publish.html`).
- `pipeline_status(cfg)` — **PURE-READ**. `Ledger.load` + `agentstep.pending` counts; assembles the Run tab status dict (sources/clips/posts/awaiting/published/holds/pending gates/backend/accounts). Called by `app_routes_run.register_run_routes`, `views.build_system_strip`.
- `run_next_step(status)` — **PURE-READ** (pure function, no I/O — operates on an already-built dict). Derives the Make tab's single "do this next" CTA from `pipeline_status` counts via a fail-open `.get`-based ladder. Called by `run.html` template logic indirectly (call graph shows no in-repo Python caller — see Anomalies).
- `asset_catalog(cfg)` — **PURE-READ**, wrapped in a top-level `try/except Exception` that logs via `get_logger` and degrades to `{"native": [], "third_party": []}` rather than 500ing. Called by `app_routes_run.register_run_routes` (feeds `library.html`).
- `pending_stitches(cfg)` — **PURE-READ**, same fail-open-with-log pattern. Filters `led.stitch_plans` to `suggested` state, sorts by rank_score desc. Called by `app.create_app` (feeds `stitches.html`).
- `pending_stitch_drafts(cfg)` — **PURE-READ**, same fail-open-with-log pattern. Filters `led.clips` to `stitch_draft` state. Called by `app.create_app`.
- `PersonaCard` (`@dataclass`) — the Personas-page card shape: id/name/voice/corpus/intake/linked_handles/reach_tags/reach_means/lever fields/instruction/length_band/lead_tags/hook_text/caption_text/lever_manifest. Pure data holder.
- `PersonaAccountLink` (`@dataclass`) — one account's persona-link row for the connect dropdown. Pure data holder.
- `PersonasPage` (`@dataclass`) — `{personas, accounts}` container. Pure data holder.
- `personas_page(cfg, *, led=None)` — **PURE-READ**. Loads `Personas`/`Accounts`/hashtag store; wrapped in `try/except Exception` logging via `get_logger`, degrading to an empty `PersonasPage`. Builds cards via `resolved_cut_spec`, `compose_persona_instruction`, `hook_directive`, `caption_directive`, `manifest`, `persona_facts` — all reads from `fanops.personas`/`fanops.hashtags`. `led` param accepted for call-compat but the function never touches it (no ledger read here). Called by `app_routes_personas.register_personas_routes` (feeds `personas.html`/`_personas_panel.html`).
- `golive_accounts(cfg)` — **PURE-READ**, fail-open (`try/except Exception` → `[]`, logged as `accounts_error`). Reads `Accounts.load(cfg).active()` + `meta_graph.per_account_token_env_key` to compute `meta_token_set` as a bool. No secret ever placed on the dataclass. Called by `golive_status`, `home_status`.
- `golive_demoted_accounts(cfg)` — **PURE-READ**, same fail-open pattern, filters to `status.value == "planned"`. Called by `golive_status`.
- `_publish_mode_label(cfg)` — **PURE-READ**. Thin delegate to `cfg.effective_publish_mode()` — the one source of truth so every display/hx-confirm/error surface agrees (fixes a historical UI-lie where `cfg.poster_backend` printed "dryrun" on a live per-channel deployment). Called by `app.create_app`, `app_routes_schedule.register_schedule_routes`, `build_system_strip`, `golive_status`, `home_status`, `pipeline_status`.
- `_post_is_due(p, now)` — **PURE-READ** (pure predicate, no I/O). True iff a queued post's scheduled_time has passed or is absent. Called by `home_status`.
- `_post_live_today(p, now)` — **PURE-READ** (pure predicate). True iff a post's public_url classifies as "live" (via `views_results._classify_channel`) and was published/scheduled within the last 24h. Called by `home_status`.
- `build_system_strip(cfg)` — **PURE-READ**. Assembles the global nav-strip dict: mode, blocked-gate count, failed-post count, `insights_blocked` (Meta Graph signal), `half_live`/`half_live_hint` (derives the D15 half-live warning state from `cfg.is_live` + `cfg.live_route_exists`), and `postiz_down` (delegated to `views_common.postiz_health_for_banner`, 30s-cached). Every sub-computation individually wrapped in `try/except Exception` so one hiccup can't blank the whole strip. Called by `app.create_app` (feeds the shared nav-strip context on every full page).
- `resolve_account_handle(raw, cfg)` — **PURE-READ**. Maps a `?account=` query param to the canonical stored handle (`@`-agnostic), fail-open to the raw input on any load error. Called by `app._account_arg`, `app_routes_schedule.register_schedule_routes`.
- `_queued_has_future_schedule(p, now)` — **PURE-READ** (pure predicate). Called by `account_work_counts`.
- `schedule_auto_ship(cfg)` — **PURE-READ**. True iff live AND the launchd daemon is alive (`daemon_health`). Called by `app_routes_schedule.register_schedule_routes`.
- `review_handoff(cfg)` — **PURE-READ**. Finds the account with the most awaiting posts (via `account_work_counts`), then the dominant batch for that account (best-effort ledger scan wrapped in `try/except Exception: pass`). Called by `app.create_app`, `app_routes_run.register_run_routes`, `review_nav_params`.
- `zero_post_clips(cfg)` — **PURE-READ**, fail-open to `[]`. Surfaces up to 5 captioned/queued clips with zero posts born (the silent crosspost-drop signal). Called by (no in-repo caller found — see Anomalies).
- `metrics_stale_hint(cfg)` — **PURE-READ**, fail-open to `False`. True when ≥2 live trackable posts exist and half+ lack `lift_score`. Called by (no in-repo caller found — see Anomalies).
- `review_nav_params(cfg, account=None)` — **PURE-READ**. Builds the Review deep-link params (`view`, `focus`, `account`, `batch`) from `review_handoff` or a passed account. Called by `account_work_counts` (self-referential — see below) — actually called by nothing else in-repo directly per call graph other than `account_work_counts`.
- `account_work_counts(cfg)` — **PURE-READ**, fail-open (`try/except Exception: pass` leaves partial/empty `defaultdict`). Per-handle awaiting/scheduled/failed/inflight counts + `review_batch` (via `review_nav_params`). Called by `app.create_app`, `review_handoff`.
- `home_status(cfg)` — **PURE-READ**. The Home read-model: wraps a `Counter`-based tally of `led.posts` states in `try/except Exception`, logging via `get_logger` and degrading to zeroed counts (`batches=None` distinguishes "unknown" from "0") rather than 500ing. Called by `app.create_app` (feeds `home.html`).
- `daemon_health(cfg)` — **PURE-READ**. Fail-open (`try/except Exception: return None`) launchd-driver liveness probe; shells no subprocess itself (delegates to `fanops.daemon.status`, outside this cluster) — `daemon.status`/`installed_interval` may themselves probe `launchctl` via subprocess, which is a read-only OS query, not a mutation. Also computes `pending_gates` via `pipeline.pending_gate_count`, itself guarded. Called by `app.create_app`, `golive_status`, `schedule_auto_ship`.
- `home_batches(cfg)` — **PURE-READ**, fail-open with log. Lists every `led.batches` row with `posts_born` count + `is_zero_result` flag, newest-first. Called by `app.create_app` (feeds `home.html`).
- `SpineStage` (`@dataclass`) — one node of the Make→Review→Schedule→Posted workflow stepper. Pure data holder.
- `WorkflowSpine` (`@dataclass`) — the whole stepper + the single "next move" CTA. Pure data holder.
- `_SPINE_ORDER` (module const) — the 4-tuple ordering of spine stages.
- `build_spine(*, counts, has_accounts, here, inflight=0, blocked_gates=0, next_params=None)` — **PURE-READ** (pure function over already-computed counts, no I/O). Derives stage done/active/todo state + severity + the single next-CTA sentence. Called by `app.create_app`.
- `golive_status(cfg)` — **PURE-READ**. Assembles the full Go-Live tab dataclass: `_publish_mode_label`, `golive_accounts`, `doctor.doctor_report` (wrapped in `try/except Exception`, logs `doctor_error`, degrades to an empty report), `validation_gate.learning_validated`, and every operator-flag boolean from `cfg`. Called by `app_routes_golive.register_golive_routes`.
- `gate_rows(cfg)` — **PURE-READ**. Enumerates every pending agent gate (moments/moment_hooks/moment_casting/captions) via `agentstep.pending`/`request_path`, reading each request JSON; a torn/unreadable request file is silently `continue`d (fail-open, matches its own docstring — the corruption is already logged elsewhere by `latest_request_id`). Called by `app.create_app` (feeds `gates.html`).

### `views_common.py` — shared read-model primitives (pagination, glossary, time math, Postiz-health cache)

- `IMMINENT_THRESHOLD_MINUTES`, `RECENT_WINDOW_HOURS`, `GRID_PAGE_SIZE` (module consts) — spec-referenced thresholds for edit-disabling, "recent" window, and page size.
- `GridPage` (`@dataclass`) — `items`/`total`/`offset`/`next_offset` — a paginated slice shape. Pure data holder.
- `paginate(rows, offset, *, page_size=GRID_PAGE_SIZE)` — **PURE-READ** (pure function, no I/O). Clamps offset, slices, computes `next_offset`. Called by `app.create_app`, `app_routes_review.register_review_routes`, `app_routes_schedule.register_schedule_routes`.
- `PREPARABLE_STATES` (module const) — the `ClipState` tuple that qualifies a post-less clip as "prepared" (rendered/captions_requested/captioned/queued).
- `TERM_DEFS` (module const dict) — the S9 plain-language glossary for insider terms (moment/cast/lever/batch/surface/variant/integration).
- `term_def(key)` — **PURE-READ** (pure lookup). Fail-soft: non-string key → `None`. Called by templates via the `_term.html` macro (no Python caller in call graph — template-only consumer).
- `accounts_in(rows)` — **PURE-READ** (pure function over an already-built row list). Distinct sorted account handles, dual-shape (dataclass `.account` or dict `["account"]`). Called by (no in-repo Python caller per call graph — template/`_card_chips` helper consumer, see Anomalies).
- `_imminent(scheduled_time, now, threshold_min=IMMINENT_THRESHOLD_MINUTES)` — **PURE-READ** (pure predicate). Fail-safe: any parse doubt → imminent (never editable). Called by `actions._guard_editable_post`, `actions.snooze_clip` (both outside this cluster, in the mutation layer — confirms the mutation layer READS this pure predicate rather than duplicating logic), `views_results.schedule_rows`, `views_review._surface`.
- `suggest_time(cfg, post, *, now)` — **PURE-READ** (pure function; the one local import of `fanops.crosspost.surface_time` and `fanops.timeutil.iso_z` is a function call, not a write). Deterministic single-post strictly-future suggestion, never the 40-min bulk stagger. Called by `actions_approve._approve_ids_with_render`, `actions_approve.approve_with_hook` (both in the mutation layer, again confirming actions.py READS this pure helper), `views_results.schedule_rows`, `views_review._surface`.
- `_BULK_APPROVE_MIN_GAP_MIN`, `_BULK_APPROVE_JITTER_MAX_MIN`, `_REALISTIC_MIN_GAP_MIN`, `_REALISTIC_JITTER_MAX_MIN` (module consts) — the M4/M2 cadence-floor and jitter bands.
- `_cadence_for(cfg)` — **PURE-READ** (pure function). Resolves `(STEP, JITTER_MAX)` from `cfg.realistic_cadence`. Called by `suggest_times_for_batch`.
- `suggest_times_for_batch(cfg, posts, *, now)` — **PURE-READ** (pure function; the "I/O" is only `cfg.account_window`, itself a JSON read at the config seam, not a write). Batch-aware, pairwise-distinct, per-account-gap-respecting suggestion spread — deterministic given (account, date) seed. Called by `actions.accept_suggested_account`, `actions.reschedule_bucket`, `actions_approve._approve_ids_with_render` (again, all mutation-layer callers reading this pure helper).
- `_roll_into_window(t, window, cfg)` — **PURE-READ** (pure function). Rolls a candidate time forward into an account's operator-local open-hour window (M7). Called by `suggest_times_for_batch`.
- `_batch_title(led, bid)` — **PURE-READ** (pure lookup, dict access only). Resolves `Post.batch_id` → `Batch.name`, `None` for a dangling id. Called by `views_results.posted_library`, `views_results.schedule_rows`.
- `_POSTIZ_HEALTH_TTL_S` (module const, 30.0s) — the cache TTL for the Postiz health probe.
- `_postiz_health_cache` (module-level mutable dict) — **process-local in-memory cache** keyed by `postiz_url` → `(timestamp, health_result)`. This IS a form of mutable module state — flagged explicitly in the purity audit below.
- `_any_channel_routes_to_postiz(cfg)` — **PURE-READ** (a config/registry read, not a ledger write). Fail-open to `False` on any exception (logged at debug). Called by `postiz_health_for_banner`.
- `postiz_health_for_banner(cfg, *, now=None)` — **READS THE NETWORK** (documented exception, see purity audit below). Performs a real HTTP health probe (`post.postiz.postiz_health_probe`) when the 30s cache is stale, caching the result in the process-local `_postiz_health_cache` dict. Fail-open to `{"show": False}` on any error. Called by `views.build_system_strip`.

### `views_live.py` — the Live library read-model (ImportedMedia only, disjoint from Posts)

- `LiveMediaRow` (`@dataclass`) — one live-only IG media row: media_id/permalink/product_type/timestamp/caption/account/imported_at/error_reason + the M3 metric breakdown (lift_score/saves/shares/retention/reach, each `Optional[float]`). Pure data holder.
- `live_library(led, cfg)` — **PURE-READ**. Lock-free read over `led.imported_media` ONLY (never `led.posts` — the module docstring is explicit that an `ImportedMedia` has no clip lineage and must never leak into the Posted library). Sorts newest-live-timestamp-first, unstamped last. Called by `app_routes_live.register_live_routes` (feeds `live_library.html`).
- `live_library_scope(cfg)` — **PURE-READ**. Returns a human-readable scope label naming the single credentialed IG handle (`cfg.meta_ig_user_id`) or a "not connected" message; never blank. Called by `app_routes_live.register_live_routes`.

### `views_results.py` — Schedule / Posted / Lift read-models

- `ScheduleRow` (`@dataclass`) — the Schedule tab's per-post row: post_id/scheduled_time/account/platform/clip_id/state/imminent/editable/integration_id/lane/delivery/submission_id/backend/error_reason/suggested_time/batch_id/batch_title/caption/variant_hook/ready/ready_reason/why_suggested. Pure data holder.
- `LiftRow` (`@dataclass`) — the Lift tab's per-variant row: variant_hook/account/platform/lift_score/loop_state/amplify_state/lift_degraded/lift_missing/scheduled_time/metric breakdown/clip_id/sibling_count/rank/delta_vs_best. Pure data holder.
- `LiftView` (`@dataclass`) — `{variant_rows, variant_empty_reason, amplify_present, amplify_rows, amplify_empty_reason}`. Pure data holder.
- `_SHIPPABLE_RENDER` (module const) — the `RenderState` tuple a shippable artifact must be in.
- `publish_readiness(led, post, cfg=None)` — **PURE-READ**. ADVISORY-only (explicit docstring: "NEVER a ledger write, NEVER a publish gate"). Checks render/clip existence, state, on-disk file presence, hook-drift, and (if `cfg` passed) an upload-size cap via `post.compress.publish_backend_for_post`/`upload_cap_bytes`/`media_path_for_post` — all reads. Fail-open to `(False, "unverified")` on any exception. Called by `schedule_rows`, `views_review._surface`.
- `explain_suggested_time(cfg, row)` — **PURE-READ** (pure function, no I/O). One plain-language sentence naming account/platform/lead-time for the suggested-time rationale. Called by `schedule_rows`.
- `schedule_rows(led, cfg, *, now, account=None, batch=None)` — **PURE-READ**. Builds the three-lane (due/upcoming/inflight) + optional recent Schedule rows from `led.posts`, calling `publish_readiness`, `suggest_time`, `explain_suggested_time`, `_batch_title`, `_imminent`, `classify_post_delivery`. Called by `app_routes_schedule.register_schedule_routes`, `schedule_cockpit`.
- `_schedule_lane(p, now)` — **PURE-READ** (pure predicate). Buckets one post into due/upcoming/inflight/recent. Called by `schedule_rows`.
- `DuePublishPlan` (`@dataclass`) — `{due, postiz_due, rate_per_min, est_minutes}`. Pure data holder.
- `due_publish_plan(cfg, *, handle=None, batch=None, now=None)` — **PURE-READ**. Counts due-now queued posts in scope + a Postiz throttle ETA, via `post.run._due_or_fail`/`_post_provider` (both reads). Called by `actions.publish_due_bucket` (mutation layer reading this pure planner), `app_routes_schedule.register_schedule_routes`.
- `ScheduleLanes` (`@dataclass`) — `{due, upcoming, inflight}` lists of `ScheduleRow`. Pure data holder.
- `schedule_lanes(rows)` — **PURE-READ** (pure function over already-built rows). Splits into the three lanes (recent excluded). Called by `app_routes_schedule.register_schedule_routes`.
- `ScheduleCockpit` (`@dataclass`, with `__post_init__` defaulting `next_times` to `[]`) — per-account schedule summary: due/upcoming/inflight/next_time/next_times/off_suggestion. Pure data holder; `__post_init__` is a dataclass default-mutable-avoidance idiom, not a side effect.
- `InflightWatchRow` (`@dataclass`) — one in-flight (awaiting-permalink) post's age/state/error. Pure data holder.
- `_schedule_needs_suggestion(scheduled_time, now)` — **PURE-READ** (pure predicate). Called by `schedule_cockpit`.
- `schedule_cockpit(led, cfg, account, *, now=None)` — **PURE-READ**. Calls `schedule_rows` and aggregates lane counts + next-slot times + off-suggestion count for one account. Called by `app_routes_schedule.register_schedule_routes`.
- `inflight_watch(led, cfg, *, account=None, now=None)` — **PURE-READ**. Lists posts awaiting a permalink with computed age in minutes. Called by `app.create_app`, `app_routes_schedule.register_schedule_routes`.
- `group_schedule_by_account(rows)` — **PURE-READ** (pure function over an already-sorted list). Groups already-time-sorted `ScheduleRow`s by account for per-account headers. Called by `app_routes_schedule.register_schedule_routes`.
- `PostedRow` (`@dataclass`) — the Posted library's per-post row: post_id/clip_id/account/platform/caption/public_url/scheduled_time/lift_score/published_at/metric breakdown/batch_id/batch_title/variant_hook/sibling_count/rank/delta_vs_best/posted_via/submission_id/error_reason/raw_state/failure_kind. Pure data holder.
- `_FAILURE_KINDS`, `_RETRYABLE_FAILURES`, `_FAILURE_LABELS` (module consts) — the failure taxonomy + operator-facing labels.
- `failure_label(kind)` — **PURE-READ** (pure lookup). Called by `operator_error`.
- `operator_error(msg, *, kind=None)` — **PURE-READ** (pure string classifier, no I/O). Translates raw backend error text into plain-language, backend-name-free messages. Called by (no in-repo Python caller found in call graph — likely template-only, see Anomalies).
- `classify_failure(post)` — **PURE-READ** (pure predicate). Buckets a failed/error post's `error_reason` string into a failure kind. Called by `actions.recover_posts`, `actions.retry_oversize_failures`, `actions.retry_rate_limited_failures` (mutation layer reading this pure classifier), `failure_rollup`, `posted_library`.
- `failure_rollup(led)` — **PURE-READ**. Counts failed/error posts by `classify_failure` bucket. Called by `app_routes_schedule.register_schedule_routes`, `views.home_status`, `delivery_audit`.
- `delivery_audit(led)` — **PURE-READ**. Read-only ops snapshot: live/inflight/queued/failed bucket counts. Called by `cli.cmd_recover_audit`.
- `classify_post_delivery(post)` — **PURE-READ** (pure predicate). Unified delivery label (live/inflight/dryrun/failed/queued/awaiting). Called by `actions.publish_now` (mutation layer reading this classifier), `posted_library`, `schedule_rows`.
- `_classify_channel(public_url)` — **PURE-READ** (pure string classifier). "live" only for an http(s) permalink; everything else (including a legacy `dryrun://`) → "dryrun". Called by `views._post_live_today`, `views.home_status`, `views.metrics_stale_hint`, `classify_post_delivery`, `delivery_audit`.
- `posted_library(led, cfg, *, account=None, batch=None, delivery=None, failure_kind=None)` — **PURE-READ**. The Posted library builder: filters by delivery class, sorts newest-first, builds `PostedRow`s. Called by `app_routes_schedule.register_schedule_routes`.
- `posted_batch_rollup(rows)` — **PURE-READ** (pure aggregation over an already-built list). `{posted, with_lift, mean_lift}` for a batch's rows. Called by `app_routes_schedule.register_schedule_routes`.
- `_BAR_METRICS` (module const) — the 4 metric names bar-chart-able (saves/shares/retention/reach).
- `lineage_stats(rows)` — **PURE-READ, but MUTATES ITS ARGUMENT IN PLACE** — see purity audit below (this is the one function in the whole cluster that mutates objects it's handed, not the ledger). Called by `app_routes_schedule.register_schedule_routes`.
- `metric_peaks(rows)` — **PURE-READ** (pure aggregation). Column-max of each bar metric across rows, for proportional bar widths. Called by `app_routes_schedule.register_schedule_routes`.
- `bar_pct(value, peak)` — **PURE-READ** (pure function). 0-100 bar width, fail-safe to 0. Called by (no in-repo Python caller found — template-only, see Anomalies).
- `group_posted_by_day(rows)` — **PURE-READ** (pure function over an already-built list). Groups by publish day, newest-first, "undated" last. Called by `app_routes_schedule.register_schedule_routes`.
- `_loop_state(led, cfg, accounts, post, cache=None)` — **PURE-READ**. Delegates to `digest.gate_state`; wrapped in `try/except Exception` that logs ONE breadcrumb per request via a shared `cache` dict flag (`_loop_state_logged`), degrading to `"gathering data"`. Called by `lift_rows`.
- `lift_rows(led, cfg, accounts=None, *, account=None)` — **PURE-READ**. Builds the per-variant Lift view (ranked by lift_score desc) + the optional amplify-candidate section (gated on `cfg.variant_amplify`, calling `variant_amplify.amplify_candidates` wrapped in `try/except Exception` logging `amplify_error`). Called by `app_routes_schedule.register_schedule_routes`.

### `views_review.py` — Review tab read-models (cards, matrix, lanes, account pivot)

- `_handle_display_map(acct_by_handle)` — **PURE-READ** (pure function). Maps normalized handle → display handle. Called by `_card`, `account_lanes`, `review_matrix`.
- `_display_handle(handle, by_norm)` — **PURE-READ** (pure lookup). Called by `_display_handles`, `account_lanes`.
- `_display_handles(handles, by_norm)` — **PURE-READ** (pure function). Called by `_card`, `account_lanes`, `review_matrix`.
- `SurfacePost` (`@dataclass`) — the per-account-per-platform Review surface row: post_id/account/platform/persona/caption/hashtags/scheduled_time/media_url/state/imminent/editable/suggested_time/hook_preburn/persona_hook_removed/variant_hook/length_label/is_account_cut/framing/hook_source/length_cause/framing_cause/cast_cause/day/tag_sources/thumb_url/ready/ready_reason. Pure data holder — the richest dataclass in the cluster (S2 provenance chips + M3a differentiation fields).
- `ReviewCard` (`@dataclass`) — one clip's Review card: clip_id/preview_url/source_name/label/moment_window/reason/language/subtitles_burned/held/held_reason/transcript_excerpt/surfaces/bucket/clip_state/day/hook_removed/batch fields/affinities/source_key. Pure data holder.
- `_personas(accounts)` — **PURE-READ** (pure lookup). `{handle: persona}` map. Called by `account_lanes`, `review_buckets`, `surface_for_post`.
- `_timecode(seconds)` — **PURE-READ** (pure function). Whole-second m:ss label, degrades non-finite to 0:00. Called by `_lineage_for_clip`.
- `_lineage_for_clip(led, clip)` — **PURE-READ**. Walks clip→moment→source for display fields, degrading missing links to "—"/None. Called by `_card`.
- `_length_label(profile)` — **PURE-READ**. Calls `bands.band_for` to render an operator-facing seconds range string. Called by `_surface`.
- `ProvChip` (`@dataclass`) — one `{value, cause, tone}` provenance chip. Pure data holder.
- `provenance_chips(surface, *, creative_variation=False)` — **PURE-READ** (pure function over an already-built surface, uses `getattr` so it never raises — wrapped in `try/except Exception: return chips` for belt-and-braces). Called by (no in-repo Python caller found — template-only via S4/S7/S8 macros, see Anomalies).
- `_cast_cause(led, post, affinities)` — **PURE-READ**. Names WHY an account got a moment from `Moment.affinities` (P13/MOL-152). Called by `_surface`.
- `_surface(post, *, persona, now, cfg, led, acct=None, affinities=())` — **PURE-READ**. The core per-post surface builder: computes editable/imminent state, length/framing/cast provenance causes, tag_sources, and — for editable surfaces — calls `views_results.publish_readiness` (a read) and `views_common.suggest_time` (a read). Called by `_card`, `account_lanes`, `review_matrix`, `surface_for_post`.
- `_card(led, clip, posts, bucket, cfg, personas, now, active_handles=frozenset(), acct_by_handle=None)` — **PURE-READ**. Assembles one `ReviewCard` from a clip + its posts, including the batch-target-exclusion computation (`batch_excluded`/`batch_excluded_names`). Called by `review_buckets`. (Call graph also lists `fanops.compose._moviepy_render` as a caller of `_card` — almost certainly a call-graph false positive from a shared local variable/name collision, not an actual cross-module call; `compose.py` is the MoviePy render module and has no plausible reason to call a Review card builder. Flagged, not asserted.)
- `_card_day(led, card)` — **PURE-READ**. Resolves a card's ingest day (clip→moment→source.created_at), "undated" on any broken lineage or parse failure. Called by `review_buckets`.
- `MatrixCell` (`@dataclass`) — one (moment × channel) grid cell: channel/account/platform/post_ids/lead_post_id/state/hook/length_label/framing/is_account_cut/hook_source/preview_url/thumb_url/multiplicity/length_cause/framing_cause/render_pending. Pure data holder.
- `MatrixRow` (`@dataclass`) — one moment's row across all channel columns + empty-cell reasons. Pure data holder.
- `MatrixView` (`@dataclass`) — `{source_id, source_name, columns, rows}`. Pure data holder.
- `_CH` (module const) — the channel-key separator (`\x1f`, unit-separator, never appears in a handle).
- `_source_label(src)` — **PURE-READ** (pure lookup). Called by `account_lanes`, `review_matrix`, `source_choices`.
- `source_choices(led)` — **PURE-READ**. Sources that have moments, newest-first. Called by `app_routes_review.register_review_routes`.
- `_pick_lead(posts)` — **PURE-READ** (pure function). Deterministic representative pick over reposts/ties (prefer awaiting, then newest, then highest id). Called by `account_lanes`, `review_matrix`.
- `_state_matches(post, state)` — **PURE-READ** (pure predicate). Called by `account_lanes`, `review_matrix`.
- `_empty_cell_reason(handle, platform, *, targets, affinities, acct)` — **PURE-READ** (pure function, fail-open to `None`). Names why a matrix cell is empty (off-target > budget > no-platform). Called by `review_matrix`.
- `review_matrix(led, accounts, cfg, *, source_id, now, state=None)` — **PURE-READ**. Builds the moment×account grid in one pass over moments/clips/posts, reusing `_surface` for cell content. Called by `app_routes_review.register_review_routes`.
- `LaneRow` (`@dataclass`) — one account-lane's per-moment row: moment_id/window/reason/hook/is_cast/preview_url/post. Pure data holder.
- `AccountLane` (`@dataclass`) — one account's full lane: account/rows/method/cast_count/moment_count/fans_all/zero_cast. Pure data holder.
- `LaneView` (`@dataclass`) — `{source_id, source_name, lanes}`. Pure data holder.
- `account_lanes(led, accounts, cfg, *, source_id, now, state=None)` — **PURE-READ**. Builds the RF6 account-first lane view: one lane per account showing every decided moment's cast state from `Moment.affinities` via `_affinity_index` (not post existence). Called by `app_routes_review.register_review_routes`.
- `_STATE_TO_BUCKET` (module const dict) — maps the `?state=` query word to a `ReviewCard.bucket` value.
- `review_buckets(led, accounts, cfg, *, now, account=None, batch=None, source=None, state=None)` — **PURE-READ**. The core three-bucket (editable/recent/held + prepared) card builder, with account/batch/source/state filters applied after sort. Called by `app_routes_review.register_review_routes`, `account_pivot_rows`.
- `review_counts(cards)` — **PURE-READ** (pure aggregation over an already-built list). `{awaiting, prepared, held}` bucket tallies. Called by `app_routes_review.register_review_routes`.
- `awaiting_moment_count(led)` — **PURE-READ**. Single source of truth for the "awaiting" headline (distinct non-held clips with ≥1 awaiting post, not raw post count). Called by `views.home_status`, `views.pipeline_status`.
- `review_awaiting_by_account(cards)` — **PURE-READ** (pure aggregation). Editable awaiting surface count per account. Called by `app_routes_review.register_review_routes`.
- `review_progress(cards)` — **PURE-READ** (pure aggregation). `{awaiting, approved, held, prepared}` scope counts. Called by `app_routes_review.register_review_routes`.
- `source_universe(cards)` — **PURE-READ** (pure function over an already-built list). Distinct sources in first-appearance order for the source-filter chips. Called by `app_routes_review.register_review_routes`.
- `account_pivot_rows(led, accounts, cfg, *, now, account, batch=None, source=None, state=None)` — **PURE-READ**. Flattens `review_buckets`' cards into one account's flat surface list, preserving day-sort; uses `dataclasses.replace` (an immutable copy, not a mutation) to stamp each row's `day`. Called by `app_routes_review.register_review_routes`.
- `group_review_by_account_surface(rows)` — **PURE-READ** (pure function). Groups the flat pivot rows by ingest day, first-appearance order. Called by (no in-repo Python caller found — template-only, see Anomalies).
- `surface_for_post(led, accounts, post_id, *, now, cfg)` — **PURE-READ**. Single-surface lookup for the per-post editor re-render after a mutation (in `actions*.py`, outside this cluster). Called by `app_routes_review.register_review_routes`.
- `group_review_by_batch(cards)` — **PURE-READ** (pure function). Groups editable cards by real Batch, unbatched group sorts last. Called by (no in-repo Python caller found — template-only, see Anomalies).

## Read/write purity audit

**Verdict: the C10 views layer is overwhelmingly PURE-READ, as designed. Two narrow, deliberate, well-documented exceptions exist — neither is a ledger/control-file mutation, and neither is a layering violation on inspection, though one (the in-memory Postiz-health cache) is a genuine piece of mutable module state worth naming explicitly.**

Every function across all 5 files was individually classified. Zero functions call `led.set_*`, `led.add_*`, `led.reconcile_*`, `write_json_atomic`, `Personas`/`Accounts` writers (`add_persona`, `update_persona`, `link_persona`, `set_backend`, etc.), or any other ledger/control-file mutation primitive. Zero functions write to disk. The two exceptions:

1. **`views_common.py:264-293` `postiz_health_for_banner(cfg, *, now=None)`** — performs a live network call (`post.postiz.postiz_health_probe`, an HTTP GET) when its 30-second cache is stale, and writes the result into the **module-level mutable dict `_postiz_health_cache`** (`views_common.py:243`, written at line 285: `_postiz_health_cache[key] = (t, health)`). This is explicitly flagged in the module docstring (lines 6-8) as "the ONE read-model here that touches the network." **Assessment: legitimate exception, not a layering violation.** It performs no ledger/control-file write — the only "mutation" is an in-process cache for an idempotent, side-effect-free health GET, which is a standard and appropriate optimization for a view-layer function that would otherwise hammer an external service on every page render. It does not persist anything across process restarts and does not affect ledger/pipeline state.

2. **`views_results.py:581-606` `lineage_stats(rows) -> None`** — **mutates its own argument objects in place**: `r.sibling_count = n`, `r.rank = ...`, `r.delta_vs_best = ...` (lines 597, 603-604) on each `PostedRow`/`LiftRow` passed in. This is the ONE function anywhere in the cluster that performs an in-place mutation rather than returning a new value. **Assessment: not a layering violation** — it mutates only transient, request-scoped read-model dataclasses that were JUST constructed by `posted_library`/`lift_rows` in the same request and are about to be handed to a template; it never touches the `Ledger`, `Accounts`, `Personas`, or any control file. It is, however, a violation of the project's own stated coding-style hard rule ("ALWAYS create new objects, NEVER mutate existing ones" — `~/.claude/rules/ecc/common/coding-style.md`, "Immutability (CRITICAL)"). The function's own docstring calls this out as intentional ("IN-PLACE annotate") for performance (avoiding rebuilding N dataclasses), and is defensively wrapped in `try/except Exception: pass` so a mutation failure degrades to the additive fields staying at their `None` defaults rather than crashing the page. Flagged as a deliberate but rule-inconsistent pattern — worth a follow-up to return new objects via `dataclasses.replace` (the same pattern `account_pivot_rows` already uses one file over, in `views_review.py:659`) instead of mutating in place, for consistency with the rest of the cluster and the project's own immutability rule.

No other function in any of the 5 files performs a write of any kind. Every "side effect" beyond these two is a `get_logger(cfg)(...)` call — a logging write, not a state mutation — used consistently across `views.py`'s fail-open branches (`asset_catalog`, `pending_stitches`, `pending_stitch_drafts`, `golive_accounts`, `golive_demoted_accounts`, `home_status`, `home_batches`, `golive_status`) to record a read failure without crashing the page. This is the correct, intentional pattern for a read-only surface that must never 500.

The mutation-layer functions actions.py/actions_approve.py/actions_common.py call INTO this cluster (`_imminent`, `suggest_time`, `suggest_times_for_batch`, `publish_readiness`, `classify_failure`, `classify_post_delivery`, `due_publish_plan`) are all themselves classified PURE-READ above — the mutation layer correctly reuses this cluster's pure predicates/classifiers rather than the views layer reaching into the mutation layer, confirming the dependency direction is one-way (actions → views, never views → actions for a write).

## Caching / expensive-computation-avoidance patterns

- **`views_common.py` `_postiz_health_cache`** — the only true cache in the cluster: a 30-second, process-local, `postiz_url`-keyed TTL cache around a real network health probe (`postiz_health_for_banner`), preventing every Studio page render from hammering the Postiz container. See purity audit above.
- **`views_results.py` `lift_rows`'s `gate_cache` dict** — a per-request (not cross-request) memoization dict passed through `_loop_state` so `digest.gate_state`'s scorer only runs once per `(account, platform)` per request rather than once per variant post — the docstring explicitly notes this fixes a prior regression ("stage-6 audit: digest had the cache, Lift lost it"). Also used to dedupe the one-breadcrumb-per-request error log (`_loop_state_logged` flag).
- **No memoization of `Ledger.load`** — every top-level view function that needs the ledger calls `Ledger.load(cfg)` fresh (explicitly documented cluster-wide as "lock-free" reads); there is no request-scoped or cross-request ledger cache anywhere in this cluster. This is a deliberate simplicity/correctness tradeoff (always-fresh reads, no staleness risk) rather than an oversight — consistent with the "no HTTP, no Flask, no lock" framing repeated in every file's module docstring.
- **`GRID_PAGE_SIZE = 24`** (`views_common.py:25-27`) — explicitly justified as a perf/usability guard: the comment states rendering all cards at once (observed at 164 `<video>` elements) is "a real perf + usability problem (the black-box-wall report)," and `paginate()` exists specifically to avoid that cost while keeping the true total visible (never silently truncating).
- **`review_matrix`/`account_lanes`** — both explicitly built as "ONE-PASS bucket maps (O(M+C+P), never the nested-accessor quadratic)" (`review_matrix` docstring, `views_review.py:371-372`) — pre-indexing clips-by-moment and posts-by-clip into dicts before the row loop, rather than re-scanning `led.clips`/`led.posts` per moment. This is an algorithmic-complexity optimization, not a caching one, but is directly responsive to the "expensive-computation-avoidance" focus area.

## Cross-reference: do these views call actions.py, or query the ledger/config independently?

**Confirmed: the dependency is strictly one-directional — `actions*.py` (C9, outside this cluster) imports and calls INTO C10's pure helpers; C10 never imports from `actions*.py`.**

Evidence from the call graph (section above) and direct reads of all 5 files:
- `views.py`, `views_common.py`, `views_results.py`, `views_live.py` import ONLY from `fanops.*` core modules (`config`, `accounts`, `ledger`, `models`, `timeutil`, `personas`, `hashtags`, `meta_graph`, `doctor`, `validation_gate`, `digest`, `variant_amplify`, `post.compress`, `post.postiz`, `post.run`, `crosspost`, `agentstep`, `log`, `daemon`, `pipeline`) plus sibling `views_*` submodules — never `actions.py`/`actions_approve.py`/`actions_common.py`/`actions_casting.py`/`actions_run.py`.
- The ONE exception is `views_review.py:19`: `from fanops.studio.actions_common import RENDER_PENDING_REASON` — this imports a single **string constant** (`"render unavailable — re-approve to retry the on-screen hook burn"`, `actions_common.py:16`), not a function or any mutating behavior. It is used read-only, as a sentinel to compare against `post.error_reason` (`views_review.py:406`) to flag a `MatrixCell.render_pending` badge. This is a legitimate shared-constant import, not a functional coupling to the mutation layer.
- Conversely, `actions.py`/`actions_approve.py` (outside this cluster, confirmed via the call-graph "called_by_in_repo" lists above) import and call `views_common._imminent`, `views_common.suggest_time`, `views_common.suggest_times_for_batch`, `views_results.classify_failure`, `views_results.classify_post_delivery`, `views_results.due_publish_plan` — i.e., the **mutation layer reuses this cluster's pure predicates/classifiers/schedulers** rather than duplicating logic, which is the correct direction for a read/query layer that must remain safely reusable by writers without becoming a writer itself.
- `views.py`'s facade re-export block (lines 20-24) additionally re-exports `views_common`, `views_review`, `views_results`, `views_live` symbols so that `fanops.studio.views.X` (the historical single-module import path used by templates, `app.py`, and tests) keeps resolving after the module was split — this is purely a namespace/import-compatibility mechanism, not a call-relationship.

## Anomalies found

**Zero-caller (dead code) candidates — confirmed via call_graph.json `called_by_in_repo: []`:**
- `src/fanops/studio/views.py:173` `run_next_step` — `called_by_in_repo: []`. Likely a template-only consumer (Jinja calls it directly as `views.run_next_step(status)` inside `run.html`), which the AST-based call graph (scoped to Python call sites) cannot see. Not asserted as truly dead — flagged for verification against the Jinja templates.
- `src/fanops/studio/views.py:528` `zero_post_clips` — `called_by_in_repo: []`. No template grep hit for `zero_post_clips` found either in the route files scanned; genuinely appears unreferenced. Worth confirming against `home.html`/its partials directly.
- `src/fanops/studio/views.py:547` `metrics_stale_hint` — `called_by_in_repo: []`. Same profile as `zero_post_clips` — worth confirming against templates.
- `src/fanops/studio/views_common.py:74` `accounts_in` — `called_by_in_repo: []`. Likely consumed by `app._card_chips` (a helper in `app.py`, outside this cluster, referenced in `app_routes_review.py`/`app_routes_schedule.py` imports) or by a template directly.
- `src/fanops/studio/views_common.py:68` `term_def` — `called_by_in_repo: []`. Explicitly documented (module docstring) as rendered via the `_term.html` Jinja macro — a template-only consumer, consistent with the docstring's own claim.
- `src/fanops/studio/views_results.py:422` `operator_error` — `called_by_in_repo: []`. Plausibly template-only (error message formatting in a partial), or possibly genuinely orphaned since `failure_label` (its sibling, used by `classify_failure`'s consumers) may have superseded it. Worth verifying against templates before removing.
- `src/fanops/studio/views_results.py:621` `bar_pct` — `called_by_in_repo: []`. Almost certainly template-only (the per-row micro-bar width, called inline in a Jinja loop alongside `metric_peaks`'s output) — both are documented together in the S6 comment block as a pair.
- `src/fanops/studio/views_review.py:162` `provenance_chips` — `called_by_in_repo: []`. Explicitly documented as "Consumed by S4/S7/S8" (template macros), consistent with a template-only call site.
- `src/fanops/studio/views_review.py:663` `group_review_by_account_surface` — `called_by_in_repo: []`. Docstring says it "mirrors group_review_by_batch / group_schedule_by_account" (both of which ARE called from route files) — likely a template-only or a recently-orphaned grouper; worth checking whether `_review_body.html` actually uses the account-pivot day-grouping or renders `pivot_rows` flat.
- `src/fanops/studio/views_review.py:684` `group_review_by_batch` — `called_by_in_repo: []`. Same profile — its docstring claims it groups "editable ReviewCards by the REAL Batch," but no Python call site was found; `_review_context` in `app_routes_review.py` does NOT appear to call it (only `review_buckets`, not a subsequent grouping step) — worth verifying whether the batch-grouped display in `_review_body.html` was refactored away and this became genuinely dead, since its docstring (`"for collapsible per-batch <details> sections"`) describes a specific rendered feature.

Given the high proportion of these (7 of 10) landing in `views_review.py`/`views_results.py` template-formatting helpers, most are very likely genuine template-only consumers (Jinja calling a module-level function directly is invisible to a Python AST call graph) rather than truly dead code — but `zero_post_clips`, `metrics_stale_hint`, `group_review_by_account_surface`, and `group_review_by_batch` in particular deserve a direct grep against the `templates/` directory before being treated as confirmed live, since their docstrings describe specific UI features that may have been superseded.

**Call-graph anomaly (probable false positive, not asserted):**
- `.reports/call_graph.json` lists `fanops.compose._moviepy_render` as a caller of `views_review._card`. This is almost certainly a name-collision false positive in the AST-based extraction (e.g., both define a local variable or nested function also named something that resolved ambiguously) — `compose.py` is the MoviePy clip-compositing module (per CLAUDE.md) and has no plausible reason to call a Studio Review-card builder. Flagged for the record, not treated as a real caller.

**Fail-open exception handlers (all intentional, logged where a real failure needs visibility — cited for completeness, none are silent-failure bugs):**
- `views.py:214` `asset_catalog` — `except Exception as exc:` logs via `get_logger(cfg)("library", ...)` before degrading to empty lists.
- `views.py:232` `pending_stitches`, `views.py:246` `pending_stitch_drafts` — same logged-fail-open pattern.
- `views.py:363` `golive_accounts`, `views.py:382` `golive_demoted_accounts` — logged `accounts_error`.
- `views.py:429`/`views.py:434`/`views.py:441` `build_system_strip` — three separate bare `except Exception: <default>` blocks with NO logging (pipeline_status failure → `blocked=0`; posts scan failure → `failed=0`; insights signal failure → `insights_blocked=False`). Unlike most of this cluster's fail-open branches, these three are genuinely silent (no `get_logger` call) — a real regression in `pipeline_status`, the posts scan, or `insights_blocked_signal` would silently zero out the nav-strip's warning badges rather than being recorded anywhere. Worth flagging as a legibility gap: every other multi-step read-model in `views.py` (asset_catalog, golive_accounts, home_status, golive_status) logs its failure; these three don't.
- `views.py:455` `build_system_strip`'s `half_live` computation — same pattern, silent `except Exception: half_live, half_live_hint = False, ""`.
- `views.py:462` `build_system_strip`'s `postiz_down` computation — silent `except Exception: postiz_down = {"show": False}` (this one is defensive belt-and-braces since `postiz_health_for_banner` itself is already internally fail-open — acceptable double-guard, not a new gap).
- `views.py:523`/`views.py:585` `review_handoff`, `account_work_counts` — bare `except Exception: pass`, undocumented-as-logged, but both are best-effort enrichments (a batch-affinity lookup, a per-post tally) where the function already has a sensible partial/empty result to fall back to — consistent with the cluster's general "never 500 the page" ethos, though also silent.
- `views_review.py:183` `provenance_chips` — `except Exception: return chips` (returns whatever chips were built before the failure, not empty) — a genuinely benign partial-degrade, explicitly by design per its docstring ("NEVER raises").
- `views_results.py:605` `lineage_stats` — `except Exception: pass`, silent, but as noted in the purity audit this only risks leaving additive fields at their already-set `None` defaults, never corrupting the base row.
- `views_results.py:718-721` `lift_rows`'s amplify-candidates block — `except Exception as exc:` DOES log via `get_logger(cfg)("lift", "-", "amplify_error", ...)` before degrading — the correctly-logged sibling of the pattern above.
- `views_results.py:656-664` `_loop_state` — logs once per request via a cache-flag dedup (`_loop_state_logged`), explicitly noting in-comment that this used to be a genuinely silent fail-open bug ("ECC fix #5: was a SILENT fail-open").

**No TODO/FIXME/XXX markers** found in any of the 5 files (manual scan during full read; no such comments present).

**No bare `except:`** anywhere in the cluster — every handler is typed `except Exception` (broad but typed) or narrower (`except (ValueError, TypeError)`, `except (TypeError, ValueError, AttributeError)`, `except OSError`). Consistent with the discipline observed in C2/C4.

**Silent-vs-logged inconsistency worth naming as the cluster's one real legibility gap**: within `views.py`'s `build_system_strip` (the function that feeds the global nav strip shown on every page), 4 of its 5 internal `try/except` blocks are silent (no `get_logger` call), while the sibling read-models earlier in the same file (`asset_catalog`, `golive_accounts`, `home_status`) all log on failure. Since `build_system_strip` runs on literally every page load, a persistent bug in any of its 5 sub-computations would degrade the nav strip's warning badges invisibly, with no log breadcrumb to diagnose it by — the one place in this cluster where the project's otherwise-consistent "log before you fail open" discipline lapses.
