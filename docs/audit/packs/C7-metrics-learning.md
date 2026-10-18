# Pack: C7 — metrics, reconcile & learning

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Entry symbols re-read 2026-10-18: `meta_graph._read_queries` absent, `timing_bias.timing_bias_hour_for` returns None unless `cfg.timing_bias`. `reconcile.py` was not edited. `partition.json` keeps `reconcile.py` in C7 and `post/metrics/*` in C6. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C7-F01 | medium | silent-fail | closed on origin/main | `meta_graph._read_queries` is absent. Successor: none | `git grep _read_queries origin/main -- src/fanops/meta_graph.py` is empty |
| C7-F02 | high | kill-switch | closed on origin/main | `timing_bias_hour_for` returns None unless `cfg.timing_bias`. Crosspost applies that return and does not check the flag | `timing_bias.timing_bias_hour_for` returns None when `not cfg.timing_bias` |

## Evidence commands run

```
git rev-parse HEAD
git diff --stat -- src/fanops/meta_graph.py src/fanops/timing_bias.py
git log -S '_read_queries' --oneline -- src/fanops/meta_graph.py
git show 19053e44 -- src/fanops/meta_graph.py
rg '_read_queries|hashtag_budget' src/fanops
rg 'timing_bias_hour_for|timing_bias_path|cfg.timing_bias' src/fanops
rg -n 'timing_bias|p4_dim_bias|variant_amplify|validation_gate' src/fanops/CLAUDE.md
```

No pytest (FanOps: CI runs the new tests).

## Full checklist 2026-10-17

Subject `d2cded31`. Product code read from that tree (`git diff` empty on these modules before the C7-F02 edit). `src/fanops/CLAUDE.md` aliases checked before any delete: no symbol deleted. `reconcile.py` and `prompts.py` not edited.

### Successor of `_read_queries`

None. Commit `19053e44` deleted `_read_queries`, `budget_remaining`, `record_query`, and `hashtag_budget.json` with the local budget meter. `rg 'hashtag_budget|_read_queries' src/fanops` is empty. Nothing in `meta_graph.py` reads a budget file and returns `None`.

`insights_blocked_signal` is the older sibling that already logs a corrupt read (`meta_graph.py` around the `insights_blocked.json` load). It is not a rename. `learning_validated` reads `cutover.json` and returns False on a corrupt file with no log; that contract is tested (`test_learning_validated_still_fail_closed_on_corrupt_file`) and is not the budget reader.

### Where metric reads fail closed

- Hashtag Graph: `_hashtag_get` raises `GraphUnreachable`, `GraphRefused`, `GraphQuotaExhausted`, or `GraphThrottled`. `resolve_hashtag` returns None only for missing creds, an empty tag, or HTTP 200 with no id. The only production caller (`source_tags_scrape._graph_ready`) does not call it without creds, and a Graph error is logged there as `no_graph` rather than cached as "no such tag".
- `media_insights`: missing creds, unresolved product type, transport, non-scope HTTP, and a bad body return None (prior snapshot kept). A scope refusal raises `MetaInsightsScopeError`. `pull_imported_insights` catches that, writes the breadcrumb, logs `scope_blocked`, and continues.
- `resolve_ig_media` / `confirm_post_live`: no id, no token, or a non-confirming body → None / `{confirmed: false}`. No fabricated live.
- `track._metrics_client_for`: unknown backend raises `ValueError`.
- Postiz and Zernio metrics: 401 raises (`PostizAuthError` / `ZernioAuthError`) and aborts the pass. Missing API key raises in the client constructor.
- `reconcile_mirror._status_client_for` / `_poll_backend_for_sid`: unknown or non-live backend raises. A post with no provider is skipped and, if pending, logged `skipped: no live provider`.
- Zernio status: unknown state maps to `scheduled`, not `failed`. TikTok oEmbed (`verify_tiktok_permalink`) returns False on transport, non-200, bad JSON, or author mismatch.
- Learning actuators (`apply_variant_amplify`, `apply_p4_dim_bias`, `apply_timing_bias`) return the ledger unchanged until `learning_validated` and their own kill switch. Exceptions are logged.

### Where metric reads return empty

- `list_user_media` / `enumerate_scoped_media`: no creds or a bad first page → `[]`, no log line. Later pages that fail return what was already collected.
- `account_overview`: None, and it logs `overview_fail`.
- `_graph_get`: None on transport, non-200, or non-object JSON, with no log of its own. Callers above decide.
- Postiz `list_posts` and Zernio `list_posts`: no submission ids → `[]` and no HTTP. A per-post 5xx/transport/non-JSON is logged (`fetch_failed` / `postiz_metrics` or `zernio_metrics`) and that id is omitted so `record_metrics` cannot replace a stored snapshot with `{}`. A 200 that maps to `{}` is still appended.
- `best_hooks`, `ucb_rank`, `transferred_hooks`, `proven_hook_styles`, `dim_bias_candidates`: `[]` when the flag is off, the sample is thin, or there is no comparative winner. `proven_hook_styles` logs `error` on an exception, then `[]`.
- `field_shape` verdict `NO-DATA` when the sample is empty. `learn_doctor` exits 0 and logs `missing_backend` or `fetch_failed` instead of raising.
- `due_offset`: None for a missing, naive, or not-yet-due `published_at`.
- `comparative_reach_leader`: None when fewer than two values or the gap is too small.

### Corrupt-cache logging

- `insights_blocked.json`: read failure logs `graph_insights signal read_failed` and returns False. Write and clear failures log `write_failed` / `clear_failed`.
- `cutover.json` via `learning_validated`: corrupt JSON returns False with no log. Next proof in `_auto_validate_metrics_shape` rewrites the file through `cutover._save_state`. Actuators stay off until that rewrite. Not a successor of `_read_queries`.
- No `hashtag_budget.json` reader remains.
- `timing_bias.json` is written by `apply_timing_bias` and has no reader under `src/fanops`. The schedule calls `timing_bias_hour_for`, which recomputes from the ledger. A torn file cannot move a slot.

### Data writes

- `record_metrics`: published or analyzed posts only. Merges carried-forward primaries and `reach`, stamps `lift_score`, and `lift_degraded` / `lift_missing_keys` when a high-weight key the platform can deliver is absent. Appends one `metrics_series` row per new cadence offset.
- `pull_imported_insights`: same merge onto `ImportedMedia`. Does not stamp `metrics_confirmed`.
- `_auto_validate_metrics_shape`: live only. Writes `cutover.json` `metrics_confirmed` when one analyzed post proves the shape (`reach` plus saves or shares, or a full primary set). Dry-run and all-degraded rows log `frozen_not_live` / `frozen_no_analyzed_metric` / `frozen_all_degraded` and do not write.
- `apply_variant_amplify`: `variant_streaks`, then `adjust.amplify` for gated candidates. No retire.
- `apply_p4_dim_bias`: `adjust.amplify` only.
- `apply_timing_bias`: creates or deletes `00_control/timing_bias.json`. Does not touch the ledger. `write_text`, not `controlio`.
- `insights_blocked.json`: `_set_insights_blocked` / `_clear_insights_blocked`.

### External calls

- Graph (`cfg.meta_graph_url`, IPv4 session): `ig_hashtag_search`, `{id}/top_media`, `{ig-user-id}/media`, `{media-id}`, `{media-id}/insights`. Token is the `access_token` param.
- Postiz: `GET /public/v1/analytics/post/{id}`, `GET /public/v1/posts` (status mirror, maximal window).
- Zernio: `GET /analytics?postId=`, `GET /posts/{id}`, `GET /posts` list. TikTok oEmbed for permalink verify.
- `learn doctor` uses the Postiz metrics client only.

### Env

Defaults off unless noted. Read in `config.py`.

- `META_GRAPH_TOKEN`, `META_GRAPH_TOKEN__<SLUG>` (`per_account_token_env_key`), `META_IG_USER_ID`, `META_GRAPH_URL`
- `POSTIZ_API_KEY`, `ZERNIO_API_KEY` (constructors raise when the routed client needs them)
- `FANOPS_LIVE` (`cfg.is_live`): auto-validate refuses to stamp when not live
- `FANOPS_VARIANT_LEARNING` (off), `FANOPS_VARIANT_MIN_POSTS` (3), `FANOPS_VARIANT_MIN_GAP` (10)
- `FANOPS_VARIANT_UCB` (off), `FANOPS_VARIANT_UCB_C` (sqrt 2; negative falls back)
- `FANOPS_VARIANT_TRANSFER` (off, frozen until validated), `FANOPS_VARIANT_TRANSFER_MIN_DONORS` (2), `FANOPS_VARIANT_TRANSFER_MAX_HOOKS` (2)
- `FANOPS_VARIANT_AMPLIFY` (off), `FANOPS_VARIANT_AMPLIFY_MIN_POSTS` (8), `FANOPS_VARIANT_AMPLIFY_MIN_GAP` (25), `FANOPS_VARIANT_AMPLIFY_MIN_STREAK` (3)
- `FANOPS_P4_DIM_BIAS` (off), `FANOPS_P4_MIN_REACH_GAP` (0; negative falls back)
- `FANOPS_TIMING_BIAS` (off)
- `FANOPS_MOMENT_HOOK_LEARNING` (off; also needs `FANOPS_VARIANT_LEARNING`)
- `FANOPS_IG_RETENTION_PROOF` (off). On this tree it does not tighten Instagram: `retention` is not in `_POSTIZ_LIFT_KEYS`, so `_platform_delivers(instagram, "retention")` is false and the branch in `_shape_proves_learning` does not run. Left as written. Turning it into a real IG requirement would mark Postiz rows degraded, which the capability map refuses.

### Launch-critical learning path

`cli_run` after a live run: `learn_pass` (outside these files) then, each behind its own flag and `is_live_backend`, `apply_variant_amplify`, `apply_p4_dim_bias`, `apply_timing_bias`. All three actuators also require `learning_validated` (`cutover.json` `metrics_confirmed`), stamped only by `_auto_validate_metrics_shape` on a live, non-degraded proof.

Crosspost does not read `timing_bias.json`. It calls `timing_bias_hour_for` and passes the result to `surface_time` as `hour_hint`. Before this pass that function ignored `FANOPS_TIMING_BIAS`, so a validated `publish_hour` winner moved new slots while the kill switch stayed at its default off. That is C7-F02. The gate is the early return in `timing_bias_hour_for`. `timing_bias_winner` stays ungated so the digest can still say "winner found (bias OFF)". `crosspost.py` was not edited (picking).

Caption and moment reads (`best_hooks` / `ucb_rank`, `transferred_hooks`, `proven_hook_styles`) do not amplify. Transfer and the moment-hook block are flag-gated; transfer is also validation-frozen at its caller. UCB is not validation-frozen; `config.variant_ucb` documents that as the reversible caption hint.

### C7-F02 tests

`tests/test_culmination_coverage.py`: `test_timing_hour_for_is_none_when_kill_switch_off`, `test_timing_hour_for_returns_winner_when_kill_switch_on`. The existing window-clamp test now sets `FANOPS_TIMING_BIAS=1` and builds two hours (`hot_hour=3`, `cold_hour=18`) so the None result is the 09–23 window, not a missing winner. Not run locally.

## Wave 1 evidence (dirty tree)

No C7-F03. C7-F01 and C7-F02 stay closed. The only dirty product file in this lane is `src/fanops/timing_bias.py` (the F02 early return). `meta_graph.py`, `learn_doctor.py`, `post/metrics/postiz_read.py`, `post/metrics/zernio_read.py`, `validation_gate.py`, `track.py`, `cutover.py`, `field_shape.py`, and `crosspost.py` are clean against HEAD. Nothing in this pass was deleted. Pytest was not run.

### Successor of `_read_queries`

None. Not renamed into a silent `None`.

`rg` over `src/fanops` finds no `def _read_queries`, `budget_remaining`, `record_query`, or `hashtag_budget_path`. `git log -S '_read_queries' -- src/fanops/meta_graph.py` stops at `19053e44`, which deleted the local meter. The parent of that commit logged `queries_read_error` and then returned `None`; the function is gone, not relocated. `timing_prior_hour` and `learn_doctor.load_verdict` are also absent.

`load_graph_tag_cache` (`source_tags_sidecar.py`) is a different reader. A corrupt `graph_hashtag_cache.json` returns `{}` inside `fail_open("source_tags.graph_cache")`, which logs. It does not return `None`. C5 owns that file. It is not a successor of `_read_queries`.

### Fail closed vs empty (re-read on this tree)

Fail closed (raise, or a false/None that does not invent a live or a schedule move):

- `_hashtag_get` raises `GraphUnreachable`, `GraphRefused`, `GraphQuotaExhausted`, or `GraphThrottled`. `resolve_hashtag` returns None only for missing creds, an empty tag, or HTTP 200 with no id. `source_tags_scrape._graph_ready` does not call it without creds. Those Graph errors log `no_graph` and are not cached as "no such tag".
- `media_insights` returns None for missing creds, an unresolved product type (logged `unresolved_type_skip`), transport, a non-scope HTTP error, or a bad body. `pull_imported_insights` keeps the prior snapshot. A scope refusal raises `MetaInsightsScopeError`; the caller writes the breadcrumb, logs `scope_blocked`, and continues.
- `resolve_ig_media` / `confirm_post_live`: no id, no token, or a non-confirming body → None / `{confirmed: false}`.
- `_metrics_client_for`: unknown backend raises `ValueError`.
- Postiz and Zernio `list_posts`: 401 raises (`PostizAuthError` / `ZernioAuthError`). A per-post 5xx, transport error, or non-JSON body is logged (`fetch_failed`) and that id is omitted, so `record_metrics` does not replace a stored snapshot with `{}`. A 200 that maps to `{}` is still appended; `record_metrics` carries forward high-weight keys and `reach`.
- `verify_tiktok_permalink` returns False on a bad URL, missing username, transport error, non-200, bad JSON, or author mismatch.
- `timing_bias_hour_for` returns None unless `cfg.timing_bias`. `crosspost.py` is clean and applies that return with no second flag check. `timing_bias_winner` stays ungated.

Return empty, prior state kept:

- `list_user_media` / `enumerate_scoped_media`: no creds or a bad first page → `[]`, no log. `_project_imported_media` returns the ledger unchanged when the list is empty, so a failed enumeration does not wipe imported rows. A later page that fails returns what was already collected.
- `account_overview`: None. Missing creds is silent. A Graph miss or a bad shape logs `overview_fail`.
- `_graph_get`: None on transport, non-200, or non-object JSON, with no log of its own.
- `_fetch_zernio_analytics_body`: None on 401, 202, 5xx, transport, or bad JSON, with no log. Callers use it as a permalink fallback. Reconcile logs only if that call raises. No URL leaves the post parked (`published_no_url`). It does not mark a post live.
- `learn_doctor` exits 0. Missing Postiz creds logs `missing_backend`. A fetch failure logs `fetch_failed`. `field_shape` verdict `NO-DATA` when the sample is empty.
- `best_hooks`, `ucb_rank`, `proven_hook_styles`: `[]` when the flag is off, the sample is thin, or there is no comparative winner. `proven_hook_styles` logs `error` then `[]`.
- `due_offset`: None for a missing, naive, or not-yet-due `published_at`.

### Corrupt-cache logging

- `insights_blocked.json`: read failure logs `graph_insights signal read_failed` and returns False (`meta_graph.py` `insights_blocked_signal`).
- `cutover.json` via `learning_validated`: corrupt JSON returns False with no log. That contract is `test_learning_validated_still_fail_closed_on_corrupt_file` and `test_unvalidated_on_corrupt_cutover`. Actuators stay off. `_save_state` rewrites through `cutover._load_state`, which logs via `fail_open`. Not a successor of `_read_queries`.
- `timing_bias.json` is written by `apply_timing_bias` (`write_text`) and has no reader under `src/fanops`. The schedule calls `timing_bias_hour_for`, which recomputes from the ledger. A torn file cannot move a slot.
- No `hashtag_budget.json` reader remains.

### Commands

```
git rev-parse HEAD
git diff --stat -- src/fanops/timing_bias.py src/fanops/meta_graph.py src/fanops/learn_doctor.py src/fanops/post/metrics/postiz_read.py src/fanops/post/metrics/zernio_read.py src/fanops/validation_gate.py src/fanops/track.py src/fanops/crosspost.py
git diff -- src/fanops/timing_bias.py
git log -S '_read_queries' --oneline -- src/fanops/meta_graph.py
git show 19053e44^:src/fanops/meta_graph.py
rg '_read_queries|budget_remaining|record_query|hashtag_budget_path|timing_prior_hour|load_verdict' src/fanops
rg 'timing_bias_path|timing_bias_hour_for' src/fanops
rg 'def load_graph_tag_cache|fail_open\("source_tags.graph_cache"\)' src/fanops/source_tags_sidecar.py
```

No pytest.
