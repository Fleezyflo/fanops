# C7: Metrics, Reconcile & Learning

## Files covered

All 12 files were read in full via the Read tool (not from memory), and cross-checked against `.reports/call_graph.json` (`called_by_in_repo`) and `.reports/structural_index.json`:

1. `src/fanops/reconcile.py` (509 lines) — read
2. `src/fanops/track.py` (350 lines) — read
3. `src/fanops/meta_graph.py` (537 lines) — read
4. `src/fanops/metrics_schedule.py` (57 lines) — read
5. `src/fanops/validation_gate.py` (47 lines) — read
6. `src/fanops/learn_doctor.py` (117 lines) — read
7. `src/fanops/moment_hook_learning.py` (50 lines) — read
8. `src/fanops/variant_learning.py` (78 lines) — read
9. `src/fanops/variant_amplify.py` (188 lines) — read
10. `src/fanops/variant_transfer.py` (81 lines) — read
11. `src/fanops/p4_dim_bias.py` (93 lines) — read
12. `src/fanops/timing_bias.py` (123 lines) — read

## Metrics pipeline (publish → reconcile → pull insights → attribute → validate)

```
1. PUBLISH (post/run.py, outside this cluster)
   → post born `submitting`, stamped a client idempotency token `submission_id="fanops_..."`

2. RECONCILE (reconcile.py) — resolves stranded submissions
   heal_stranded_submitting()    submitting + no submission_id + >15min → queued (crash heal)
   reconcile_due()               pre-polls (network, lock-free) → reconcile_posts() (apply, under Ledger lock)
   reconcile_posts()             per-post poll of postiz/zernio status → published | failed | left-parked
   resolve_media_ids()           (Leg 2, called inside pull_metrics) match public_url → Graph media_id + product_type
   project_imported_media()      (ledger-rebuild M2) live-media-not-authored-here → ImportedMedia rows

3. PULL METRICS (track.py)
   pull_metrics()                 → resolve_media_ids() → _default_list_posts() (postiz/zernio/Graph per-platform)
                                     → record_metrics() per matched row (due_offset gates time-series append)
   pull_imported_insights()       → same shape for ImportedMedia rows (Graph media_insights, sole IG source)

4. ATTRIBUTE / SCORE
   lift_score()                   weighted sum over _W (saves/shares/retention high; reach/likes low)
   record_metrics()                merges row, carries forward dropped primary keys (CULM-6), stamps
                                    lift_degraded + lift_missing_keys (T4/MOL-18b), flips published→analyzed

5. VALIDATE / UNFREEZE (auto, no operator step)
   _auto_validate_metrics_shape() (called at the end of every pull_metrics) — first real, non-degraded
                                    analyzed row → cutover.json metrics_confirmed=True
   validation_gate.learning_validated()  reads that flag
   validation_gate.p4_unlocked()  = learning_validated AND enough_attributed_signal (≥8 posts × ≥2 values)

6. LEARN / BIAS (all amplify-only, C1-isolated, default-OFF, VALIDATION-FROZEN)
   variant_learning.best_hooks / ucb_rank   — per-surface hook-variant scoring (v2/v3)
   variant_amplify.apply_variant_amplify    — sustained-streak-gated re-amplify of a winning hook's source
   variant_transfer.transferred_hooks       — cross-account cold-start hook borrowing (caption-side read only)
   moment_hook_learning.proven_hook_styles  — same winners surfaced to the vision/moment prompt
   p4_dim_bias.apply_p4_dim_bias            — cross-account reach bias on first_frame_kind/clip_profile/top_bias
   timing_bias.apply_timing_bias            — reach-winning publish_hour persisted as a schedule prior

7. DIAGNOSTICS (read-only, non-actuating)
   learn_doctor.cmd_learn_doctor  — CLI-invoked field-shape PASS/FAIL/NO-DATA verdict (own sidecar, separate gate)
   metrics_schedule.due_offset    — pure cadence-offset selector consumed by pull_metrics
```

## Per-file breakdown

### `reconcile.py`
Purpose: resolves posts stranded in `submitting`/`submitted`/`needs_reconcile` by polling each backend for terminal status; also runs the Leg-2 media-id/product-type identify pass and the ledger-rebuild M2 inverse "imported media" projection.

- **`_parked_age(post, now)`** — `now - parse_iso(post.scheduled_time)`, or `None` if unschedulable/unparseable. Pure. Callers: `heal_stranded_submitting`, `reconcile_posts`.
- **`_is_fake_token(post)`** — True iff `submission_id` still starts with the birth token prefix `"fanops_"`. Pure. Callers: `reconcile_posts`.
- **`_is_giveup(post)`** — True iff `error_reason` starts with the `GAVE UP:` sentinel. Pure. Callers: `reconcile_posts`.
- **`_norm_permalink(url)`** — canonicalizes a public URL for matching. Pure. Callers: `resolve_media_ids`, `project_imported_media`.
- **`_pick_media(cands, post)`** — picks nearest-timestamp Graph media candidate. Pure. Callers: `resolve_media_ids`.
- **`resolve_media_ids(led, cfg, *, get=None)`** — network (Graph), mutates `led.posts` with `media_id`/`product_type`. Callers: `track.pull_metrics`, `cli.cmd_map_media`.
- **`project_imported_media(led, cfg, *, get=None)`** — network (Graph), upserts `led.imported_media`. Callers: `cli.cmd_map_media`.
- **`_status_client_for(cfg, backend, led)`** — builds the per-backend `GetStatus` closure. Callers: `_default_get_status`.
- **`_reconcilable_routing(cfg, led)`** — disk read (accounts), returns `{submission_id: backend}`. Callers: `_default_get_status`, `reconcile_due`.
- **`_poll_backend_for_sid(cfg, routing, sid)`** — resolves live backend for one submission id; raises `RuntimeError` if none live. Callers: `_default_get_status`, `reconcile_due`.
- **`_default_get_status(cfg, led=None)`** — builds the composite poll function. Callers: `reconcile_due`, `reconcile_posts`.
- **`heal_stranded_submitting(cfg, *, now=None)`** — disk write (Ledger.transaction), flips a stranded `submitting` post back to `queued` after 15min. Callers: `reconcile_due`.
- **`reconcile_due(cfg)`** — orchestrator: load → heal → network polls (lock-free) → `postiz_lifecycle.ensure_up` (subprocess) → Ledger.transaction wrapping `reconcile_posts`. Raises `AuthError` on fatal auth. Callers: `cli.cmd_reconcile`, `pipeline._reconcile_safe`, `studio.actions.reconcile_inflight`.
- **`reconcile_posts(led, cfg, *, get_status=None, now=None)`** — the state machine: poll→`published`/`failed`/parked-with-escalation (XC-1/XC-2/XC-6). Mutates `led.posts`. Callers: `reconcile_due`.

### `track.py`
Purpose: pull per-post analytics, score lift, merge into ledger, and auto-validate the metrics field shape.

- **`_metrics_trackable(cfg, sid)`** — True iff a real (non-birth-token) submission id. Pure. Callers: `pull_metrics`.
- **`_platform_delivers(platform, key)`** — platform-capability check. Pure. Callers: `_missing_high_weight`, `_shape_proves_learning`.
- **`_shape_proves_learning(metrics, *, weights=None, platform=None, require_ig_retention=False)`** — proves a real, non-degraded metrics shape. Pure. Callers: `_auto_validate_metrics_shape`.
- **`_missing_high_weight(metrics, weights, platform=None)`** — lists absent high-weight keys. Pure. Callers: `_shape_proves_learning`, `pull_imported_insights`, `record_metrics`.
- **`lift_score(metrics, weights=None)`** — weighted sum over `_W`. Pure. Callers: `cutover.cutover_lift`, `pull_imported_insights`, `record_metrics`.
- **`_captured_offsets(post)`** — set of offsets already captured. Pure. Callers: `pull_metrics`, `record_metrics`.
- **`record_metrics(led, post_id, metrics, *, weights=None, offset=None, captured_at=None)`** — merges metrics (carry-forward), stamps lift_score/degraded flags, appends time-series row, flips published→analyzed. Mutates `led.posts` in-memory. Callers: `pull_metrics`.
- **`pull_imported_insights(led, cfg, *, get=None, now=None)`** — same pattern for `ImportedMedia`. Network (Graph). Callers: `cli.cmd_map_media`.
- **`_metrics_client_for(cfg, backend, submission_ids)`** — lazy factory for Postiz/Zernio metrics clients. Callers: `_default_list_posts`.
- **`_default_list_posts(cfg, *, submission_ids=None, posts=None)`** — composite per-platform fetcher (IG always via Graph). Callers: `cli._learn_pass`, `cli.cmd_track`, `studio.actions.pull_metrics_studio`, `pull_metrics`.
- **`pull_metrics(led, cfg, *, list_posts=None, window="30d", now=None, resolve_media=None)`** — orchestrator: resolve_media → fetch → match → record_metrics → auto-validate. Network + mutation + log. Callers: `cli._learn_pass`, `cli.cmd_track`, `studio.actions.pull_metrics_studio`.
- **`_auto_validate_metrics_shape(led, cfg)`** — auto-unfreeze: first proving analyzed row on a live backend stamps `cutover.json["metrics_confirmed"]=True`. Callers: `pull_metrics` (always, at end).

### `meta_graph.py`
Purpose: read-only, budget-aware Meta Graph client — hashtag trend sampling, per-account creds, sole-source IG media/insights read path.

- **`_env_slug(handle)`** — handle→env-slug. Pure. Callers: `per_account_token_env_key`.
- **`per_account_token_env_key(handle)`** — per-handle Graph token env key name. Callers: `resolve_meta_creds`, `studio.golive.set_meta_creds`, `studio.views.golive_accounts`.
- **`resolve_meta_creds(cfg, *, handle=None)`** — resolves per-handle creds with global fallback; never raises. Disk read (accounts). Callers: `enumerate_scoped_media`, `list_user_media`, `media_insights`, `post.metrics.GraphInsightsClient._default_insights`, `track.pull_imported_insights`.
- **`_graph_get(cfg, path, params, *, get=None, token=None)`** — shared GET wrapper, fail-soft to `None`. Network. Callers: `harvest_cooccurring`, `hashtag_id`, `list_user_media`, `trend_score`.
- **`hashtag_id(cfg, tag, *, get=None)`** — resolves `#tag`→Graph node id. Callers: `harvest_cooccurring`, `trend_score`.
- **`trend_score(cfg, tag, *, get=None)`** — sums engagement over top_media. Callers: `discover_candidates`, `sample_trends`, `tag_metrics`.
- **`list_user_media(cfg, *, get=None, creds=None)`** — paginated media list, capped 50 pages. Callers: `enumerate_scoped_media`.
- **`_next_path(cfg, next_url)`** — strips base URL from paging cursor. Callers: `list_user_media`.
- **`credentialed_ig_handles(cfg)`** — active handles with own `ig_user_id`. Disk read. Callers: `reconcile.project_imported_media`, `reconcile.resolve_media_ids`.
- **`enumerate_scoped_media(cfg, handles, *, get=None)`** — flattens media across handles, fail-open per-handle. Callers: `reconcile.project_imported_media`, `reconcile.resolve_media_ids`.
- **`insights_metrics_for(product_type)`** — metric-list builder by product type. Pure. Callers: `media_insights`.
- **`_is_scope_error(body)`** — classifies permission-refusal vs transient. Pure. Callers: `media_insights`.
- **`media_insights(cfg, media_id, product_type, *, get=None, creds=None)`** — the sole IG analytics read; raises `MetaInsightsScopeError` on real permission refusal. Callers: `post.metrics.GraphInsightsClient._default_insights`, `track.pull_imported_insights`.
- **`insights_blocked_signal(cfg)`** — reads persisted scope-blocked breadcrumb. Callers: `doctor.doctor_report`, `studio.views.build_system_strip`.
- **`_set_insights_blocked(cfg)`/`_clear_insights_blocked(cfg)`** — write/delete the breadcrumb. Callers: `post.metrics.GraphInsightsClient.list_posts`.
- **`_read_queries(cfg)`** — reads hashtag-budget log; `None` on corrupt (fail-closed). Callers: `budget_remaining`, `record_query`, `sample_trends`.
- **`budget_remaining(cfg, *, now=None)`** — `30 - queried-in-7d`. Callers: `discover_candidates`, `harvest_cooccurring`, `sample_trends`, `tag_metrics`.
- **`record_query(cfg, tag, *, now=None)`** — appends+prunes budget log under flock. Callers: `discover_candidates`, `harvest_cooccurring`, `sample_trends`, `tag_metrics`.
- **`tag_metrics(cfg, tag, *, get=None, now=None)`** — operator on-demand single-tag read. Callers: `studio.personas.recommend_tag`.
- **`sample_trends(cfg, candidates, *, get=None, now=None)`** — budget-bounded trend sampling. Callers: `fanops_hashtags.refresh_store`.
- **`harvest_cooccurring(cfg, seed_tags, *, get=None, now=None)`** — co-occurrence tally, capped 5000 tags. Callers: `fanops_hashtags.refresh_store`, `discover_candidates`.
- **`discover_candidates(cfg, seeds, *, known=(), measure_k=0, get=None, now=None)`** — ranks harvest, drops known, optionally measures top-K reach. Callers: `persona_research.discover_corpus`.

### `metrics_schedule.py` — pure cadence selector, no I/O
- **`offset_seconds(offset)`** — `'4h'`→14400. Pure. Callers: `due_offset`.
- **`_parse_pub(published_at)`** — tolerant ISO parse. Pure. Callers: `due_offset`.
- **`due_offset(published_at, captured, now)`** — the single cadence offset newly due at `now`. Pure. Callers: `track.pull_metrics`.

### `validation_gate.py` — the correctness gate, pure reads only
- **`learning_validated(cfg)`** — reads `cutover.json["metrics_confirmed"]`, `False` on any error. Callers: `caption._transferred_hooks`, `casting_bias.casting_reach_prior`, `digest.gate_state`, `doctor.doctor_report`, `p4_dim_bias.apply_p4_dim_bias`, `studio.views.golive_status`, `timing_bias.apply_timing_bias`, `track._auto_validate_metrics_shape`, `variant_amplify.apply_variant_amplify`, `p4_unlocked`.
- **`enough_attributed_signal(led, dim, *, min_n=8, min_values=2)`** — ≥8 attributed posts in ≥2 distinct dim-values. Callers: `p4_unlocked`.
- **`p4_unlocked(led, cfg, dim)`** — `learning_validated AND enough_attributed_signal`. Callers: `digest._reach_by_dim`, `p4_dim_bias.dim_bias_candidates`, `timing_bias.timing_bias_winner`.

### `learn_doctor.py` — separate read-only diagnostic sidecar
- **`_sampled_submission_ids(led)`** — pure. Callers: `_default_fetch`.
- **`_default_fetch(led, cfg)`** — lazy Postiz client factory. Callers: `field_shape_report`.
- **`field_shape_report(led, cfg, *, window="30d", list_posts=None)`** — PASS/FAIL/NO-DATA verdict on `reach` signal. Network. Callers: `cmd_learn_doctor`.
- **`_mapped_lift_keys()`** — lazy import of Postiz label map. Callers: `field_shape_report`.
- **`load_verdict(cfg)`** — reads persisted verdict sidecar. **Zero in-repo callers** — see Anomalies.
- **`_persist_verdict(cfg, report)`** — atomic write. Callers: `cmd_learn_doctor`.
- **`cmd_learn_doctor(cfg, *, list_posts=None)`** — CLI entry, narrow except, always returns 0. Callers: `cli._dispatch`.

### `moment_hook_learning.py` — pure read, feeds moment/vision prompt
- **`proven_hook_styles(led, cfg, accounts)`** — cross-surface gated winning-hook union, filtered through `hookscore.narration_signature`. `[]` on flags-off or any error (fail-open, logged). Callers: `moments.request_moment_hooks`.

### `variant_learning.py` — pure, must never be imported by track.py/pipeline.py
- **`_collect_lifts(led, account, platform)`** — groups analyzed lifts by hook. Pure. Callers: `best_hooks`, `ucb_rank`.
- **`best_hooks(led, cfg, account, platform)`** — v2 greedy winner (min posts + min gap vs runner-up). Pure. Callers: `digest.gate_state`, `variant_amplify.amplify_candidates`/`update_streaks`, `variant_transfer.transferred_hooks`.
- **`ucb_rank(led, cfg, account, platform)`** — v3 deterministic UCB1 bandit argmax. Pure. Callers: `digest.gate_state` (plus indirect dispatch from `caption.py`/`moment_hook_learning.py`).

### `variant_amplify.py` — v3, AMPLIFY-ONLY
- **`_surfaces(led)`** — distinct surfaces with analyzed variant posts. Pure. Callers: `digest._variant_amplify`, `amplify_candidates`, `update_streaks`.
- **`_evidence_fingerprint(led, account, platform)`** — content-addressed evidence hash. Pure. Callers: `update_streaks`.
- **`update_streaks(led, cfg)`** — the only mutator; touches only `led.variant_streaks`. Callers: `apply_variant_amplify`.
- **`_source_for_surface(led, account, platform, hook)`** — deterministic source pick. Pure. Callers: `amplify_candidates`.
- **`amplify_candidates(led, cfg)`** — full-gate pure read. Callers: `digest._variant_amplify`, `studio.views_results.lift_rows`, `apply_variant_amplify`.
- **`apply_variant_amplify(led, cfg)`** — actuator; kill switch + `learning_validated` gate; calls `adjust.amplify` only; fail-safe try/except. Callers: `cli._dispatch`, `cli.cmd_amplify_variants`.

### `variant_transfer.py` — pure cross-account cold-start
- **`_persona_tokens(persona)`** — word-set. Pure. Callers: `transferred_hooks`.
- **`transferred_hooks(led, cfg, accounts, account, platform)`** — donor-count + persona-overlap ranked hook borrowing, own-winner-first rule. Pure. Callers: `caption._transferred_hooks`.

### `p4_dim_bias.py` — P4(b), AMPLIFY-ONLY
- **`dim_bias_candidates(led, cfg)`** — pure read over `_P4_DIMS`, gated per-dim by `p4_unlocked`. Callers: `digest._culmination`, `apply_p4_dim_bias`.
- **`apply_p4_dim_bias(led, cfg)`** — actuator; kill switch + `learning_validated`; per-candidate isolated try/except around `adjust.amplify`. Callers: `cli._dispatch`, `cli.cmd_p4_bias`.

### `timing_bias.py` — Leg 3, writes a schedule prior (not an amplify)
- **`timing_bias_winner(led, cfg)`** — pure, gated by `p4_unlocked(..., "publish_hour")`. Callers: `digest._culmination`, `apply_timing_bias`, `timing_bias_hour_for`.
- **`_in_window(hour, window)`** — wrap-safe window check. Pure. Callers: `timing_bias_hour_for`.
- **`timing_bias_hour_for(led, cfg, handle)`** — window-clamped per-account suggestion. Callers: `crosspost._mint_surface_post`.
- **`apply_timing_bias(led, cfg)`** — actuator; kill switch + `learning_validated`; writes/deletes `cfg.timing_bias_path`; never touches ledger state. Callers: `cli._dispatch`.
- **`timing_prior_hour(cfg)`** — reads the persisted prior hour. **Zero in-repo callers** — see Anomalies.

## Learning-gate trace (exact thresholds, file:line)

1. **`learning_validated(cfg)`** — `validation_gate.py:22-29`. Reads `cutover.json["metrics_confirmed"]`; `False` on missing/error.
   - Stamped by `track._auto_validate_metrics_shape` (`track.py:322-349`), write at `track.py:348`.
   - Gate: `cfg.is_live` AND not-already-validated AND ≥1 `analyzed` post passes `_shape_proves_learning`.
2. **Thresholds** — `validation_gate.py:18-19`: `_MIN_ATTRIBUTED_N = 8`, `_MIN_VALUES = 2`.
3. **`enough_attributed_signal`** — `validation_gate.py:32-39`: ≥2 distinct dim-values each with ≥8 attributed posts.
4. **`p4_unlocked`** — `validation_gate.py:42-46`: `learning_validated AND enough_attributed_signal` — both required.
5. **Downstream per-dim gates**: `p4_dim_bias.py:38` gates each of `_P4_DIMS = ("first_frame_kind","clip_profile","top_bias")`; comparative-lead threshold `cfg.p4_min_reach_gap` at `p4_dim_bias.py:45`. `timing_bias.py:36` gates `"publish_hour"`; threshold at `timing_bias.py:43`.
6. **Variant-loop thresholds** (config-driven): `best_hooks` uses `cfg.variant_min_posts`/`cfg.variant_min_gap` (`variant_learning.py:42,51`); `amplify_candidates` stacks `variant_amplify_min_posts`/`require_full_objective`/`variant_amplify_min_gap`/`variant_amplify_min_streak`/`MAX_AMPLIFY_PER_SOURCE` (`variant_amplify.py:127,129-133,142,145,150`); `apply_variant_amplify` additionally gates on `cfg.variant_amplify` + `learning_validated` (`:164,166`).

## Bias-injection points (kill switch, fail-safe, bias-only scope) — file:line

| Actuator | Kill switch | Gate | Fail-safe | Bias-only confirmed |
|---|---|---|---|---|
| `variant_amplify.apply_variant_amplify` (`:157-187`) | `cfg.variant_amplify` (`:164`) | `learning_validated` (`:166`) | try/except (`:177-186`), logs, no partial mutation | Confirmed — imports only `adjust.amplify` (`:33`); docstring bans retire/cascade imports |
| `p4_dim_bias.apply_p4_dim_bias` (`:56-92`) | `cfg.p4_dim_bias` (`:62`) | `learning_validated` (`:64`) | outer + per-candidate try/except (`:70-91`) | Confirmed — imports only `adjust.amplify` (`:18`) |
| `timing_bias.apply_timing_bias` (`:79-110`) | `cfg.timing_bias` (`:86`) | `learning_validated` (`:89`) | two separate try/except (`:93-97`,`:98-109`) | Confirmed — never calls `adjust.amplify`; writes only a schedule-prior file, never ledger state |
| `moment_hook_learning.proven_hook_styles` (`:25-49`) | `cfg.variant_learning` AND `cfg.moment_hook_learning` (`:34`) | none beyond `best_hooks`/`ucb_rank`'s own thresholds | try/except (`:36-49`) | Confirmed — pure read, returns `list[str]` only |
| `variant_transfer.transferred_hooks` (`:21-80`) | none dedicated (implicit via `best_hooks` gate + `accounts is None` short-circuit) | none | N/A (pure) | Confirmed — pure, zero mutation |

## Anomalies found (file:line)

1. **`learn_doctor.load_verdict`** (`learn_doctor.py:70-80`) — zero in-repo callers per call graph. Either dead or M4 reads the sidecar file directly rather than through this function. Candidate for removal or wiring into `doctor_report`.
2. **`timing_bias.timing_prior_hour`** (`timing_bias.py:113-122`) — docstring claims "the schedule seam calls this" but zero in-repo callers per call graph; `timing_bias_hour_for` (the actual consumer per the docstring) is called only by `crosspost._mint_surface_post` and does not appear to route through this function. Likely orphaned — worth a repo-wide grep to confirm.
3. **Broad `except Exception` in `pull_metrics`'s `resolve_media` call** (`track.py:291-294`) — intentional fail-open with a logged breadcrumb, but broader than `learn_doctor.cmd_learn_doctor`'s narrowed catch (`learn_doctor.py:105`); a genuine bug inside `resolve_media_ids` would be masked as a soft skip rather than surfacing.
4. **Similarly broad `except Exception` in every bias actuator's outer guard** (`variant_amplify.py:177`, `p4_dim_bias.py:70,79`, `moment_hook_learning.py:47`) — all deliberately broad per "fail-safe, not fail-silent" design; all log before swallowing. No bare `except: pass` found anywhere in the cluster.
5. **`meta_graph._read_queries`** (`meta_graph.py:337-338`) — `except (OSError, JSONDecodeError, ValueError, TypeError): return None` with NO logging, unlike its sibling `insights_blocked_signal` which does log. A corrupt hashtag-budget file is undetectable from the log stream (fail-closed direction is safe, but silent).
6. **No bias-scope violations found** — every amplify-only actuator imports exclusively `adjust.amplify`, never `retire`/cascade/state-setters; `timing_bias` never imports `adjust` at all and only writes a schedule-prior file. No TODO/FIXME/XXX markers anywhere in the 12 files.
7. **Asymmetric daemon-tick coverage**: `reconcile.resolve_media_ids` is called from the automatic `track.pull_metrics` path (`track.py:290`), but its documented "inverse" sibling `reconcile.project_imported_media` is called ONLY from the manual `cli.cmd_map_media` CLI verb — not part of the automatic daemon tick, despite the module docstring presenting them as a matched forward/inverse pair. May be an intentional scope choice (importing live-only media might be considered heavier/rarer) or a gap — worth confirming against `pipeline.py`'s daemon-tick call list.
