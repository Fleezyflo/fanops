# C9: Studio Backend (Flask App Layer)

## Files covered (all 17 read in full)

1. `src/fanops/studio/__init__.py` (3 lines)
2. `src/fanops/studio/app.py` (555 lines)
3. `src/fanops/studio/app_routes_golive.py` (220 lines)
4. `src/fanops/studio/app_routes_live.py` (34 lines)
5. `src/fanops/studio/app_routes_personas.py` (87 lines)
6. `src/fanops/studio/app_routes_review.py` (289 lines)
7. `src/fanops/studio/app_routes_run.py` (100 lines)
8. `src/fanops/studio/app_routes_schedule.py` (166 lines)
9. `src/fanops/studio/actions.py` (995 lines)
10. `src/fanops/studio/actions_approve.py` (404 lines)
11. `src/fanops/studio/actions_casting.py` (52 lines)
12. `src/fanops/studio/actions_common.py` (46 lines)
13. `src/fanops/studio/actions_run.py` (320 lines)
14. `src/fanops/studio/actions_wipe.py` (59 lines)
15. `src/fanops/studio/golive.py` (710 lines)
16. `src/fanops/studio/personas.py` (212 lines)
17. `src/fanops/studio/preview_media.py` (47 lines)

Total: 4,299 lines. Cross-referenced against C10 (`views*.py`, already traced separately — not
re-traced here, only referenced by name) and grepped against `cli.py`, `daemon.py`, and every
`src/fanops/studio/templates/*.html` for external callers.

## Cluster overview

This cluster is the entire Flask web layer of FanOps Studio — the local, single-operator cockpit
that replaces hand-editing JSON control files and running CLI verbs. It has three architectural
layers, cleanly separated:

1. **Route groups** (`app.py` + 6 `app_routes_*.py` files) — own the HTTP surface: URL rules,
   HTTP methods, request-arg parsing, and `render_template` calls. They contain almost no business
   logic; every route either reads via `views*.py` (cluster C10, pure reads) or mutates via one of
   the `actions*.py` modules (this cluster, all mutations gated behind `Ledger.transaction`).
2. **Action modules** (`actions.py` + 5 `actions_*.py` files) — the entire mutation surface of the
   Studio. Every mutating function returns a frozen `ActionResult(ok, error, detail)` and is
   documented, in its own docstring, never to raise into a Flask 500. Mutations are lock-safe: each
   opens exactly one `Ledger.transaction(cfg)` and re-validates state *inside* the lock so a
   concurrent CLI/daemon pass can't lose-update against a Studio click (or vice versa). Slow I/O
   (ffmpeg renders, LLM calls, network publish) runs *outside* the lock wherever possible, with an
   in-lock re-guard immediately before the write — the docstrings call this the "M1: never ffmpeg
   under the flock" invariant.
3. **Support modules** (`golive.py`, `personas.py`, `preview_media.py`, `actions_wipe.py`,
   `actions_common.py`, `actions_casting.py`) — self-contained operator-facing surfaces over deeper
   subsystems (`fanops.autopilot`, `fanops.post.postiz`, `fanops.ledger_wipe`, `fanops.personas`).

The whole app is created lazily via `fanops.studio.app.create_app(cfg)`, invoked only from
`cli.py`'s `studio` command dispatch (`cli.py:844`) — never imported at module top of anything
reachable from a core, no-`[studio]` install (per `__init__.py`'s own docstring, `__init__.py:1-3`).
Every route reads a fresh `Ledger.load(cfg)` (lock-free) for its render; every mutation writes
through a named `actions.*` function, never touching `led.posts[...]` directly from a route.

## Per-file breakdown

### `__init__.py`

- Module docstring only, no code (`__init__.py:1-3`). Deliberately Flask-free so
  `import fanops.studio` (and the read-model `views*`/`actions*` modules) works without the
  `[studio]` extra. No functions/classes.

### `app.py` — the Flask app factory + shared route helpers

- `_bounded(cfg, candidate)` (`app.py:41-48`) — **security-critical**. Resolves `candidate` to an
  absolute path and returns it only if `p.is_relative_to(cfg.base.resolve())`, else `None`. This is
  the single choke-point every file-serving route in this cluster uses to prevent a
  hand-edited/corrupt ledger path from turning the cockpit into an arbitrary-file server. Callers:
  `/media/<post_id>` (`app.py:489`), `/media-preview/<post_id>` (`app.py:497`),
  `/clips/<clip_id>` (`app.py:505`), `/clip-thumb/<clip_id>` (`app.py:520,523`),
  `/review-thumb/<eid>` (`app.py:438`).
- `_media_path_for_post(led, post_id)` (`app.py:51-79`) — **pure read**. Resolves the local file to
  serve for a post: (1) `post.render_id` → the per-account `Render`'s path (authoritative), (2) else
  a local `media_urls[0]` (bare path or `file://`), (3) else the shared base `clip.path`. An
  `http(s)` `media_urls` entry (already-published URL) falls through — not locally servable. Path
  always comes from the trusted ledger, never the request, so no traversal risk at this layer (the
  traversal defense is `_bounded`, applied by the caller). Called by `/media/<post_id>`
  (`app.py:489`).
- `_parse_gate_form(kind, form)` (`app.py:82-128`) — **pure read** (no I/O). Maps the raw Gates-tab
  HTML form into `answer_gate`'s expected data shape for each of the 3 gate kinds
  (`captions`/`moments`/`moment_hooks`). Values stay strings; Pydantic coerces and
  validates downstream (`actions.answer_gate`), so a non-numeric timestamp surfaces as a clean
  `ActionResult` error rather than a 500. Called by `/gates/answer/<kind>/<key>` (`app.py:484`).
- `_time_arg()` (`app.py:133-136`) — parses `request.form["new_time"]` (a `datetime-local` naive
  value) into canonical UTC via `local_input_to_utc_z`. Called by
  `do_reschedule`/`do_reschedule_surface` (`app_routes_review.py:218,225`), `do_schedule_move`
  (`app_routes_schedule.py:64`).
- `_offset_arg()` (`app.py:138-144`) — pagination offset from `?offset=`, clamped to `max(0, ...)`,
  garbage → 0. Called throughout route groups for `views.paginate`.
- `_account_arg()` (`app.py:146-161`) — the `?account=` filter, resolved via
  `views.resolve_account_handle` (a `current_app.config["FANOPS_CFG"]` lookup, fail-open to the raw
  string on any exception). Called pervasively (context processors, every route group).
- `_batch_arg()`, `_delivery_arg()`, `_failure_arg()`, `_compact_arg()`, `_ultra_arg()`,
  `_source_arg()`, `_state_arg()`, `_focus_arg()`, `_focus_idx_arg()`, `_view_arg()`
  (`app.py:163-222`) — stateless query-string parsers, each fail-safe (blank/garbage → a documented
  default, never a 500). Consumed by `app_routes_review.py`'s `_review_context` and
  `app_routes_schedule.py`'s `_schedule_panel`/`_posted_panel`.
- `_with_active(counts, active)` (`app.py:224-230`) — pure function; unions the chip-filter universe
  with the active filter so a just-emptied account filter stays visible/clearable.
- `_row_chips(rows, route, active)` / `_card_chips(cards, active)` (`app.py:232-245`) — pure
  functions building the per-tab account-chip context (`Counter` over rows/cards). Called by
  `publish_panel` (`app.py:455`), `app_routes_schedule.py` (`_schedule_panel`, `_posted_panel`),
  `app_routes_review.py`'s `_review_context`.
- `create_app(cfg)` (`app.py:247-555`) — **the Flask app factory**. Side effects:
  - Sets `app.config["FANOPS_CFG"] = cfg` and `app.config["MAX_CONTENT_LENGTH"] = cfg.upload_max_bytes`
    (`app.py:249-250`) — the upload-size cap enforced by Werkzeug before any view runs.
  - Registers Jinja filters/globals (`localdt`, `localinput`, `group_review_by_batch`,
    `group_schedule_by_account`, `group_review_by_account_surface`, `provenance_chips`,
    `run_next_step`, `bar_pct`, `term_def`, `operator_error`, `failure_label`) — all delegating to
    pure `views.*` functions (`app.py:255-279`).
  - 5 `@app.context_processor` functions injecting global template context on every render:
    `_inject_nav_account` (nav filter state, `app.py:282-309`), `_inject_system_strip`
    (`views.build_system_strip(cfg)`, `app.py:311-313`), `_inject_account_session`
    (`views.account_work_counts`, `app.py:315-321`), `_inject_inflight_watch` (only on
    `_INFLIGHT_SURFACES`, a `Ledger.load` + `views.inflight_watch`, fail-open to `[]` on exception,
    `app.py:323-332`), `_inject_spine` (the workflow stepper, only on `_SPINE_HERE` endpoints, reads
    `views.home_status`/`build_system_strip`/`review_nav_params`/`build_spine` directly rather than
    via `flask.g` so it survives error-page renders, `app.py:334-358`).
  - Registers ~20 direct routes (below) plus 6 sub-route-group registrations via
    `register_review_routes`, `register_schedule_routes`, `register_run_routes`,
    `register_live_routes`, `register_personas_routes`, `register_golive_routes` (each imported
    lazily inside `create_app`, `app.py:390-542`).
  - Registers a `ControlFileError` errorhandler (`app.py:546-553`) that renders a degraded
    `error.html` at **HTTP 200** (not 500) — explicitly because htmx 2.x drops non-2xx swap bodies,
    so a 500 on a malformed `accounts.json`/`ledger.json` would otherwise blank the whole tab. The
    template is documented as standalone (must not itself touch ledger/accounts context).
  - Returns the constructed `Flask` app. Called exactly once, from `cli.py:850`.

**Direct routes registered in `create_app`** (full table also in Cluster-specific analysis below):
`GET /` (`index`, `app.py:360-365`), `POST /home/pull-metrics` (`app.py:367-369`),
`POST /home/reconcile` (`app.py:371-373`), `POST /home/retry-rate-limit` (`app.py:375-377`),
`POST /home/retry-oversize` (`app.py:379-381`), `GET /home/daemon-health` (`app.py:383-388`),
`GET /stitches` (`app.py:402-406`), `POST /stitches/approve` (`app.py:412-414`),
`POST /stitches/dismiss` (`app.py:416-418`), `POST /stitches/release` (`app.py:420-423`),
`GET /candidates` (`app.py:425-428`), `POST /candidates/approve/<eid>` (`app.py:430-432`),
`GET /review-thumb/<eid>` (`app.py:434-441`), `GET /publish` (`publish_panel`,
`app.py:443-455`), `POST /publish/posted/<post_id>` (`app.py:457-460`),
`POST /publish/now/<post_id>` (`app.py:462-468`), `GET /reconcile-strip` (`app.py:470-474`),
`GET /gates` (`app.py:476-480`), `POST /gates/answer/<kind>/<key>` (`app.py:482-485`),
`GET /media/<post_id>` (`app.py:487-492`), `GET /media-preview/<post_id>` (`app.py:494-500`),
`GET /clips/<clip_id>` (`app.py:502-508`), `GET /clip-thumb/<clip_id>` (`app.py:510-534`).

### `app_routes_golive.py` — Go-Live route group

`register_golive_routes(app, cfg)` (`app_routes_golive.py:12-221`) registers 24 routes under their
original endpoint names. Every POST route is a thin wrapper: parse the form, call one
`golive.*` function, re-render `_golive_panel(result)` (`app_routes_golive.py:18-23`) — a helper
that re-fetches `views.golive_status(cfg)` fresh so the mode/readiness banner is always current
after a mutation.

Routes: `GET /golive` (`golive_view`, line 13), `POST /golive/config`
(`do_golive_config` → `golive.set_postiz_config`, line 25), `POST /golive/account/add`
(`golive.add_account`, line 29), `POST /golive/hooks` (`golive.set_per_account_hooks`, line 37),
`POST /golive/casting` (`golive.set_account_casting`, line 44), `POST /golive/clip-profile`
(`golive.set_clip_profile`, line 51), `POST /golive/responder` (`golive.set_ai_responder`, line
56), `POST /golive/daemon-install` (`golive.install_daemon`, line 62), `POST
/golive/daemon-uninstall` (`golive.uninstall_daemon`, line 68), `POST /golive/learning`
(`golive.set_variant_learning`, line 73), `POST /golive/amplify` (`golive.set_variant_amplify`,
line 79), `POST /golive/ucb` (`golive.set_variant_ucb`, line 84), `POST /golive/transfer`
(`golive.set_variant_transfer`, line 89), `POST /golive/zernio-config`
(`golive.set_zernio_config`, line 94), `POST /golive/account/meta-creds`
(`golive.set_meta_creds`, line 99), `POST /golive/account/backend`
(`golive.set_account_backend`, line 108), `POST /golive/account/persona` (`golive.set_persona`,
line 116), `POST /golive/account/promote` (`golive.promote_account`, line 121), `POST
/golive/account/remove` (`golive.remove_account`, line 126), `POST /golive/account/demote`
(`golive.demote_account`, line 131), `POST /golive/refresh` (`golive.refresh_integrations`, line
136), `POST /golive/discover` (`golive.discover_channels`, line 140), `POST /golive/adopt`
(`golive.adopt_channels`, line 147-156), `GET /golive/health` (`system_health`, direct call, not
via `golive.py`, line 158-164), `GET /golive/connect` / `GET /golive/accounts` / `GET
/golive/live` (three step pages of `golive_page.html`, lines 166-179), `POST /golive/map`
(`do_golive_map`, batch per-channel mapping via `golive.map_account`, lines 181-201), `POST
/golive/live` (`do_golive_live` → `golive.go_live` — **the only route that can set
`FANOPS_LIVE=1`**, lines 203-208), `POST /golive/dryrun` (`golive.go_dryrun`, lines 210-212),
`POST /golive/validate` (`golive.validate_learning`, lines 214-220).

### `app_routes_live.py` — Live library + wipe route group

`register_live_routes(app, cfg)` (`app_routes_live.py:12-35`).
- `_page(*, preview=None, result=None)` (lines 13-17) — shared renderer: `Ledger.load(cfg)` +
  `views.live_library`/`live_library_scope` + `actions_wipe.CONFIRM_WORD` passed to the template so
  the confirm-word hint can render without a second lookup.
- `GET /live-library` (`live_library`, line 19-21) — read-only.
- `POST /live-library/wipe/preview` (line 23-27) — calls `actions_wipe.preview_wipe(cfg)`
  (**read-only**, no ledger mutation) and re-renders with the preview populated.
- `POST /live-library/wipe/confirm` (line 29-34) — calls `actions_wipe.confirm_wipe(cfg,
  typed=request.form.get("confirm_text", ""))` — **the sole reachable trigger for a destructive
  ledger wipe** in this cluster. Full trace in the Wipe-safety audit below.

### `app_routes_personas.py` — Personas route group

`register_personas_routes(app, cfg)` (`app_routes_personas.py:11-87`), 12 routes.
- `GET /personas` (line 12-17) — `views.personas_page(cfg)` + the code-derived lever catalog
  (`_LEVERS`/`_LEVER_EFFECTS`/`_LEVER_REF` from `app.py`).
- `_personas_panel(result=None)` (lines 19-22) — shared re-render helper for every mutating route.
- `GET /personas/drawer/<pid>` (lines 24-32) — fail-open: an unknown `pid` renders `p=None` (a
  clean "not found" dialog), never a 404/500 (important because htmx swaps this into a body-level
  mount).
- `POST /personas/compose` (lines 34-39) — `studio_personas.preview_compose(cfg, request.form)` —
  **live, never-persisted** translation preview.
- `POST /personas/add` (lines 41-46) → `studio_personas.create_persona`.
- `POST /personas/edit` (lines 48-53) → `studio_personas.edit_persona`.
- `POST /personas/delete` (lines 55-57) → `studio_personas.delete_persona`.
- `POST /personas/corpus/add` / `remove` (lines 59-65) → `studio_personas.add_corpus_tag` /
  `remove_corpus_tag`.
- `POST /personas/research` (lines 67-71) → `studio_personas.research_corpus` (live Meta Graph
  co-occurrence discovery, budget-bounded, fail-open).
- `POST /personas/recommend` (lines 73-77) → `studio_personas.recommend_tag` (live single-tag Graph
  lookup — **read-only network call**, does not add).
- `POST /personas/connect` (lines 79-82) → `studio_personas.connect_account`.
- `POST /personas/migrate` (lines 84-87) → `studio_personas.run_migration`.

### `app_routes_review.py` — Review + per-surface editor route group

`register_review_routes(app, cfg)` (`app_routes_review.py:15-290`).
- `_review_context(*, result=None)` (lines 16-83) — **the single builder** for every Review render
  (full page and htmx swap body), assembling scope (account/batch/source/state/view), pagination,
  the moment×account matrix, the account-first pivot/lanes, and the chip context, all off the SAME
  scoped card list so the two render paths never drift. All I/O is `Ledger.load`/`Accounts.load`
  (read-only).
- `GET /review` (`review`, lines 85-88) — full page.
- `_review_panel(result=None)` (lines 90-92) — the htmx partial-swap re-render used by every
  mutating Review route below (`_review_body.html`).
- `GET /review/live` (lines 94-108) — the self-polling live-count strip; read-only, no mutation.
- `GET /review/refresh` (lines 110-112) — GET, no mutation, "load them" button.
- `POST /posts/approve` (lines 114-118) → `actions.approve_posts(cfg, ids,
  confirmed=bool(form.get("batch_confirm")))` — **the human approval gate** (bulk).
- `POST /posts/reject` (lines 120-122) → `actions.reject_posts`.
- `POST /posts/unapprove/<post_id>` (lines 124-128) → `actions.unapprove_post`.
- `POST /posts/approve-with-hook/<clip_id>` (lines 130-134) → `actions.approve_with_hook`.
- `POST /posts/approve-as-is/<clip_id>` (lines 136-140) → `actions.approve_as_is`.
- `POST /posts/approve-batch/<batch_id>` (lines 142-144) → `actions.approve_batch`.
- `POST /posts/approve-clip/<clip_id>` (lines 146-150) → `actions.approve_clip`.
- `POST /posts/approve-account` (lines 152-158) → `actions.approve_account`.
- `POST /posts/approve-moment/<moment_id>` (lines 160-164) → `actions.approve_moment`.
- `POST /posts/approve-channel` (lines 166-179) → `actions.approve_account` scoped to a matrix
  column — **guarded**: rejects (never silently widens) when `ch_account`/`ch_source` are missing
  (line 174-177), so a stale/hand-crafted POST can't sweep a sibling source.
- `POST /cast/add/<moment_id>` / `POST /cast/remove/<moment_id>` (lines 181-200) →
  `actions.cast_add`/`cast_remove` (operator override of `Moment.affinities`).
- `_render_surface_edit(post_id, result)` (lines 203-213) — shared re-render for the per-surface
  editor after a time/hook mutation.
- `POST /reschedule/<post_id>` (lines 215-219) — legacy back-compat route, inline result only.
- `POST /reschedule-surface/<post_id>` (lines 221-226) → `actions.reschedule_post` + fresh editor
  re-render.
- `POST /clear/<post_id>` (lines 228-233) → `actions.clear_time`.
- `POST /caption/<post_id>` (lines 235-238) → `actions.edit_caption`.
- `POST /regenerate/<post_id>` (lines 240-253) → `actions.regenerate_caption` (LLM call, gated on
  `cfg.responder_mode == "llm"` inside the action, not the route).
- `POST /restore-persona-hook/<post_id>` (lines 255-258) → `actions.restore_persona_hook`.
- `POST /reburn-hook/<post_id>` (lines 260-273) → `actions.reburn_hook` (ffmpeg only, no LLM).
- `POST /snooze/<clip_id>` (lines 275-278) → `actions.snooze_clip`.
- `POST /unhold/<clip_id>` (lines 280-289) → `actions.release_held_clip`; on success returns `""`
  (empty body) so the htmx outerHTML swap removes the card entirely.

### `app_routes_run.py` — Make/Run route group (ingest, upload, advance, prepare)

`register_run_routes(app, cfg)` (`app_routes_run.py:12-101`).
- `_run_handoff(result=None)` (lines 14-19) — `views.review_handoff` + batch-id override from a
  just-completed ingest result.
- `GET /run` (`run_panel`, lines 20-25) — read-only status.
- `GET /run/status` (lines 27-31) — self-polling status partial.
- `_run_panel(result)` (lines 33-37) — shared re-render for every mutating Run route.
- `POST /run/ingest` (lines 39-43) → `actions.run_ingest`.
- `POST /run/pull` (lines 45-47) → `actions.run_pull` (yt-dlp network call).
- `POST /run/upload` (lines 49-60) → `actions.save_uploads_and_ingest` — **the browser video-upload
  path**, full trace in Upload-safety audit below.
- `POST /run/advance` (lines 62-66) → `actions.run_advance`, `confirmed` derived from a checkbox
  the template shows *only* on a live backend.
- `POST /run/pull-metrics` (lines 68-70) → `actions.pull_metrics_studio`.
- `POST /run/prepare` (lines 72-77) → `actions.run_prepare` (auto-answers gates via the LLM
  responder, same confirm gate as advance).
- `GET /library` (lines 79-82) → `views.asset_catalog`.
- `POST /library/upload` (lines 84-91) → `actions.save_thirdparty_uploads` then, only if that
  succeeded, `actions.run_ingest_thirdparty`.
- `@app.errorhandler(RequestEntityTooLarge)` `_too_large` (lines 93-100) — re-renders the Run panel
  at **HTTP 200** with a friendly "file too large" `ActionResult`, because htmx 2.0.3 drops non-2xx
  swap bodies. The actual size cap is still enforced by Werkzeug (`MAX_CONTENT_LENGTH`); only the
  *status code* of the friendly response changes.

### `app_routes_schedule.py` — Schedule + Posted + Lift route group

`register_schedule_routes(app, cfg)` (`app_routes_schedule.py:16-166`).
- `_schedule_panel(result=None, *, full=False)` (lines 17-36) — shared builder for the Schedule
  cockpit, read-only assembly (`views.schedule_rows`/`schedule_lanes`/`group_schedule_by_account`/
  `due_publish_plan`/`schedule_cockpit`/`inflight_watch`).
- `GET /schedule` (lines 38-40).
- `POST /schedule/shift/<handle>` (lines 42-48) → `actions.shift_account_schedule`.
- `POST /schedule/respread` (lines 50-53) → `actions.reschedule_bucket`.
- `POST /schedule/unapprove/<post_id>` (lines 55-58) → `actions.unapprove_post`.
- `POST /schedule/move/<post_id>` (lines 60-64) → `actions.reschedule_post`.
- `POST /schedule/clear/<post_id>` (lines 66-70) → `actions.clear_time`.
- `POST /schedule/publish/<post_id>` (lines 72-78) → `actions.publish_now` — **live publish path**.
- `POST /schedule/reconcile` (lines 80-88) → `actions.reconcile_inflight`; branches its render
  target on the `HX-Target` header.
- `POST /schedule/accept-suggested/<handle>` (lines 90-92) → `actions.accept_suggested_account`.
- `POST /schedule/publish-due` (lines 94-97) → `actions.publish_due_bucket` — **bulk live publish
  path**.
- `GET /lift` (lines 99-111) — read-only, `views.lift_rows`/`lineage_stats`/`metric_peaks`.
- `_posted_panel(result=None, *, full=False)` (lines 113-131) — shared Posted builder.
- `GET /posted` (lines 133-135).
- `POST /posts/repost/<post_id>` (lines 137-140) → `actions.repost_post`.
- `POST /posts/resolve/<post_id>` (lines 142-146) → `actions.resolve_post`.
- `POST /posts/recover` (lines 148-152) → `actions.recover_posts`.
- `POST /posts/crosspost/<clip_id>` (lines 154-158) → `actions.crosspost_to_account`.
- `POST /posts/crosspost-all` (lines 160-165) → `actions.crosspost_all_to_account`.

### `actions.py` — the core mutation surface

- `_normalize_z(new_time)` (lines 30-36) — pure. Parses + coerces naive→UTC, re-emits canonical
  `...Z`. Raises `ValueError` on garbage (caught by callers).
- `_guard_editable_post(led, post_id, now)` (lines 39-52) — **the shared editability gate**. A post
  is editable iff `awaiting_approval` (always) or `queued` **and not imminent**
  (`views._imminent`). Called by `reschedule_post`, `clear_time`, `edit_caption`,
  `regenerate_caption`, `reburn_hook`.
- `reschedule_post(cfg, post_id, new_time, *, now=None)` (lines 55-66) — one `Ledger.transaction`;
  guards, then sets `p.scheduled_time = z` in place.
- `clear_time(cfg, post_id, *, now=None)` (lines 69-84) — if `queued`, **first**
  `led.unapprove_post(post_id)` (→ `awaiting_approval`), **then** clears the time, both inside the
  same transaction — so a post is never persisted queued-and-timeless (which `publish_due` would
  otherwise fire immediately).
- `edit_caption(cfg, post_id, caption, *, now=None)` (lines 87-94) — guards, sets `p.caption`.
- `regenerate_caption(cfg, post_id, guidance="", *, model=None, now=None)` (lines 97-169) — **LLM
  call**. Refuses outside the lock unless `cfg.responder_mode == "llm"` (line 135-137, the
  "no-haphazard-claude" root fix). Builds the production `caption_prompt`, calls `claude_json`
  (default) or an injected `model`, re-runs `caption.brand_risk_flag` on the result (same guard as
  ingest — no bypass), then re-guards + writes inside a **fresh** short transaction (the model call
  can take ~180s outside any lock).
- `reburn_hook(cfg, post_id, hook, *, now=None)` (lines 172-241) — **ffmpeg subprocess, no LLM**.
  Gated on `cfg.creative_variation` (line 183-184). Computes the content-addressed render id via
  `account_render_spec`, attempts an account-specific re-cut (`render_account_cut`) or falls back
  to `overlay.burn_hook_only` — both run *outside* the lock; the `Render` entity is
  added/pointed-at inside a short re-guarded transaction. `hook_burn_failed` is surfaced as
  `ok=True, detail.hook_burned=False` (a warning, not a rollback, since this is an edit not an
  approve).
- `approve_candidate(cfg, eid)` (lines 244-260) — **filesystem move only, no ledger**. Validates
  `eid` has no `/`, `\`, or `..` before constructing a path under `cfg.review`, then
  `src.rename(dst)` into `approved/`.
- `mark_published(cfg, post_id, url=None)` (lines 269-297) — **requires a non-empty `url`** (a hard
  R1/D9 invariant — "posted by hand" must carry a permalink). One transaction; rejects an
  already-terminal post; `write_audit` on success.
- `_studio_publish_guard(cfg, post=None)` (lines 301-314) — pure guard: refuses when `not
  cfg.is_live`, or (with a post) when the resolved provider is `dryrun`/unmapped. Called by
  `publish_now`, `publish_due_bucket`.
- `accept_suggested_account(cfg, handle, *, now=None)` (lines 317-332) — applies
  `views_common.suggest_times_for_batch` to every queued post of one account, in one transaction.
- `preflight_publish_media(cfg, post, led=None)` (lines 335-347) — pure check (delegates to
  `post.compress`) for oversize media before a network call.
- `reconcile_inflight(cfg)` (lines 350-359) — refuses off-live; delegates to
  `fanops.reconcile.reconcile_due` (network poll of the publish backend for permalinks).
- `publish_now(cfg, post_id, *, confirmed=True)` (lines 361-432) — **the single-post live-publish
  entry point**. Confirm-gated when `cfg.is_live` (line 370-373). Lock-free guard read, then
  `_studio_publish_guard`, then `preflight_publish_media` (failing → marks the post `failed` in a
  transaction), then `persist_post_shrink`, then calls `fanops.post.run.publish_post(cfg,
  post_id)` — **outside the ledger lock** (network round-trip). `AuthError` is caught and
  translated via `Config.auth_key_name_from_error` (never a generic message). On success,
  `write_audit`.
- `answer_gate(cfg, kind, key, data)` (lines 435-456) — validates `data` against the matching
  Pydantic model (`_GATE_MODELS`) and writes `response.json` only if valid — **no Ledger lock** (gate
  files live under `04_agent_io`, not the ledger).
- `snooze_clip(cfg, clip_id, *, now=None)` (lines 459-477) — bumps every non-imminent
  queued/awaiting post of a clip `SNOOZE_DAYS` (365) into the future, in one transaction.
- `repost_post(cfg, post_id)` (lines 480-508) — mints a fresh `awaiting_approval` post from the same
  clip+surface with a content-addressed repost-epoch id — re-enters the approval gate (never
  auto-approved).
- `_warm_target_aspect` / `crosspost_to_account` / `crosspost_all_to_account` (lines 510-611) —
  cross-account reuse minting, always birthing `awaiting_approval` posts, aspect-correct and
  duration-capped, per-(clip,surface) content-addressed dedup.
- `_seconds_away`, `reschedule_bucket`, `shift_account_schedule`, `reschedule_account` (lines
  613-693) — bulk respread operations, all `PostState.queued`-scoped, one transaction each.
- `publish_due_bucket(cfg, *, handle=None, batch=None, confirmed=True, now=None)` (lines 696-721) —
  **bulk live-publish entry point**. Computes `due_publish_plan` first; confirm-gated when live and
  `plan.due > 0`; delegates to `fanops.post.run.publish_due`.
- `resolve_post(cfg, post_id, status, *, url=None)` (lines 730-756) — operator force-a-state escape
  hatch (Studio twin of `cmd_resolve`); requires a URL for any terminal-requiring-URL state.
- `pull_metrics_studio(cfg, *, window="30d")` (lines 759-795) — refuses off-live; pulls analytics via
  `fanops.track.pull_metrics`, writes the digest.
- `bulk_send_to_review(cfg, post_ids, *, reason)` (lines 798-833) — the operator's bulk-revert API:
  moves posts back to `awaiting_approval` and clears post-publish telemetry, **blocked** for
  terminal states (`_REVIEW_REVERT_BLOCKED`: published/analyzed/needs_reconcile/submitting/
  submitted) so a live-shipped post can never be silently un-shipped in the ledger.
- `restore_persona_hook(cfg, post_id, *, now=None)` (lines 843-872) — restores a guard-stripped
  hook onto one surface, then delegates to `reburn_hook`.
- `retry_rate_limited_failures`, `retry_oversize_failures`, `recover_posts` (lines 875-982) —
  recovery-cockpit bulk verbs; each classifies failures (`views_results.classify_failure`) and
  atomically re-queues eligible posts, always audited.
- `release_held_clip(cfg, clip_id)` (lines 985-995) — browser twin of `fanops unhold`; rejects a
  non-held clip.

### `actions_approve.py` — the approval spine (burn-on-approve)

- `_acct_for(accts, handle)` (lines 24-25) — pure lookup.
- `_warm_renders(cfg, snap, ids, accts)` (lines 27-65) — **lock-free** parallel (`ThreadPoolExecutor`,
  up to 4 workers) ffmpeg pre-render of every distinct (clip, hook) variant a batch of posts needs,
  off a throwaway `Ledger.load` snapshot. Per-post fail-open: a render error is logged and just
  omitted from the returned plan map.
- `_adopt_render(led, cfg, post, plan, accts)` (lines 67-103) — **in-lock**. Adopts a warmed render
  (or leaves the post un-materialized if the hook changed mid-flight — never burns ffmpeg under the
  flock). Enforces the per-platform duration cap (`PLATFORM_MAX_SECONDS`) on an account-cut render —
  over-cap sets `post.error_reason` and returns without pointing the post at media (spine's
  no-media guard then skips approving it).
- `_approve_ids_with_render(cfg, *, resolve_ids, now, detail)` (lines 105-161) — **the shared
  approve engine**. Warms renders lock-free, then in one transaction: resolves ids *again* in-lock
  (guards against a concurrent state change), computes a batch-aware spread
  (`suggest_times_for_batch`), and for each id either adopts its render + calls
  `led.approve_post(pid, now_iso=..., suggested_iso=...)`, or (if the render couldn't materialize)
  stamps `RENDER_PENDING_REASON` and skips — **never queues a variant post without its burned
  file**. Audited on success (`write_audit`).
- `BULK_APPROVE_CONFIRM_AT = 15` (line 163) — bulk-approve confirm threshold.
- `approve_posts(cfg, ids, *, now=None, confirmed=False)` (lines 165-174) — **confirmed when count
  > 15**, else refused with an error naming the count. This is the human approval gate reached
  from `/posts/approve`.
- `reject_posts(cfg, ids)` (lines 176-185) — `led.reject_post` per id, one transaction.
- `unapprove_post(cfg, post_id)` (lines 187-196) — `led.unapprove_post`, one transaction.
- `_warm_hooked_render(cfg, moment_id, aspect, hook)` (lines 198-217) — lock-free pre-render of a
  restored-hook clip via `render_moment` on a throwaway snapshot; returns `False` only on a genuine
  ffmpeg failure (never silently swallowed — logged).
- `approve_with_hook(cfg, clip_id, *, now=None)` (lines 219-271) — **refuses outright when
  `cfg.creative_variation` is ON** (line 228-230 — per-surface hooks own the burn then). Restores
  `moment.hook`, re-renders (fingerprint-skip adopts the lock-free warm), **rolls back the whole
  transaction** if the render errors or if `rc.hook_burn_failed` (a successful-but-textless render
  would otherwise ship the post clean without the hook the operator explicitly asked for — the
  docstring calls this out as CRITICAL). Then approves every `awaiting_approval` post of the clip.
- `_approve_matching(cfg, pred=None, *, pred_for=None, now=None, detail=None)` (lines 273-287) —
  the shared spine for every scoped bulk-approve (`approve_clip`/`approve_batch`/`approve_account`/
  `approve_moment`), delegating to `_approve_ids_with_render`.
- `approve_batch`, `approve_clip`, `approve_account`, `approve_moment`, `approve_as_is` (lines
  289-347) — scoped bulk approvals, each a thin predicate over `_approve_matching`.
  `approve_account`'s source-scoped variant builds a `clip_id → source_id` map **once inside the
  transaction** so a dangling clip can never over-approve onto a foreign source (line 321-326).
- `approve_stitches`, `dismiss_stitches`, `release_stitches`, `_best_caption_sibling` (lines
  349-405) — the stitch-plan M3/M4 approval lifecycle; `release_stitches` is the **only** transition
  out of `ClipState.stitch_draft`, re-checked in-lock.

### `actions_casting.py` — operator cast override

- `cast_add(cfg, source_id, account, moment_id)` (lines 13-30) — one transaction; rejects a foreign moment; **appends `account` to `Moment.affinities`** (the sole gate input after P11). Idempotent re-add.
- `cast_remove(cfg, source_id, account, moment_id)` (lines 32-47) — removes one handle from `affinities`; empty set → fan-to-all path (`affinities==[]`), never a stuck record.

### `actions_common.py` — shared mutation-layer primitives

- `RENDER_PENDING_REASON` (module const, line 16) — the durable marker distinguishing a warm-miss
  from an ordinary not-yet-approved post.
- `_inherit_captions(meta)` (lines 19-24) — pure. **Deep**-copies a sibling clip's `meta_captions`
  so a later in-place edit to one clip's caption can never corrupt a sibling's (defends against a
  latent shallow-copy aliasing bug).
- `ActionResult` (`@dataclass(frozen=True)`, lines 27-42) — the universal action outcome type;
  frozen so no call site can mutate a result after construction. `success()`/`failure()` factory
  classmethods.
- `_now(now)` (lines 45-46) — pure; injects `datetime.now(timezone.utc)` when `now is None`.

### `actions_run.py` — ingest / upload / pipeline-driver mutations

- `_VIDEO_EXT` (line 21) + an import-time drift guard (line 22: `if not (_VIDEO_EXT <=
  ingest.MEDIA_EXT): raise ValueError(...)`) — asserts the upload allowlist stays a subset of the
  ingest-recognized extensions even under `-O` (uses `raise`, not `assert`).
- `_KICK_TTL_S = 300` (line 24) — debounce window for the ingest event-kick.
- `kick_prepare(cfg)` (lines 27-67) — **spawns a detached subprocess**
  (`subprocess.Popen([_fanops_bin(), "run", "--base-time", ...], start_new_session=True)`) so a
  fresh ingest starts processing immediately rather than waiting for the next daemon tick.
  Debounced via a PID-stamped lockfile (`cfg.control / ".run-kick"`) that probes liveness with
  `os.kill(pid, 0)` rather than a blind fixed TTL. **Fail-open**: every failure is logged and
  swallowed — this is an optimization, never a precondition (the daemon remains the guaranteed
  driver). Explicitly does **not** inject a responder default — the spawned process resolves
  `FANOPS_RESPONDER` itself from `.env`/`os.environ`.
- `run_ingest(cfg, *, batch_name="", target_accounts=(), burn_subs=None)` (lines 70-107) — one
  transaction: `ingest_drops` (catalogues `01_inbox`), optionally mints a named `Batch` via
  `create_batch` when `added >= 1`. Calls `kick_prepare(cfg)` on any successful ingest (line 98).
- `run_pull(cfg, url)` (lines 110-128) — rejects non-`http(s)://` URLs up front; `download_url`
  (network, no lock) then `ingest_drops` inside a transaction.
- `save_uploads(cfg, files, *, probe=True, allowed_ext=None, dest_dir=None)` (lines 131-182) —
  **the browser video-upload security boundary**. Full trace in Upload-safety audit below.
- `save_uploads_and_ingest(cfg, files, *, batch_name="", target_accounts=(), burn_subs=None)`
  (lines 185-203) — chains `save_uploads` → `run_ingest`; a save failure short-circuits before any
  ingest attempt.
- `save_thirdparty_uploads(cfg, files)` (lines 206-212) — delegates to `save_uploads` with
  `dest_dir=cfg.thirdparty_inbox` (a **peer** staging dir, never `01_inbox`) and the
  photo-inclusive `MEDIA_EXT` allowlist.
- `run_ingest_thirdparty(cfg)` (lines 215-234) — catalogues the third-party staging dir as
  `origin_kind="third_party"` Sources (inert to clip production).
- `run_advance(cfg, base_time=None, *, confirmed=True)` (lines 237-272) — **live-publish-gated**
  (`cfg.is_live and not confirmed` → refused, line 247-250); validates `Accounts.load(cfg).validate()`
  first; delegates to `fanops.pipeline.advance`.
- `run_prepare(cfg, base_time=None, *, confirmed=True)` (lines 275-320) — loops `responder.answer_pending`
  + `advance` up to 10 passes until no gate remains; same live-confirm + accounts guard as
  `run_advance`; in `llm` mode, failing to converge after 10 passes surfaces as `ok=False` (never a
  falsely-green "prepared").

### `actions_wipe.py` — the destructive ledger-wipe surface (MOL-33)

- `CONFIRM_WORD = "REMOVE"` (line 21) — the exact operator-typed word (case-insensitive, trimmed)
  required to execute a wipe.
- `preview_wipe(cfg)` (lines 24-32) — **read-only**. `Ledger.load` (fail-closed on a torn ledger,
  returning a clean error rather than a silent empty preview) then
  `ledger_wipe.wipe_preview(led)` — a pure computation of the would-remove id-set + per-entity
  counts. Never mutates.
- `confirm_wipe(cfg, *, typed)` (lines 35-59) — **the only mutation entry point in this file**.
  Gate order: (1) `typed.strip().upper() != CONFIRM_WORD` → refuse before any snapshot/removal
  (line 41-43, logged as `wipe_refused_bad_confirm`); (2) `Ledger.snapshot(cfg)` — mandatory,
  taken **before** any removal; a snapshot failure refuses (line 47-49, logged
  `wipe_refused_snapshot_failed`); (3) `ledger_wipe.snapshot_is_restorable(snap)` — the snapshot is
  verified restorable before proceeding; failing this refuses (line 50-52, logged
  `wipe_refused_snapshot_unverified`); (4) only then `ledger_wipe.execute_wipe(cfg, confirmed=True,
  snapshot_path=snap)` — which itself re-checks the snapshot + confirm in code (belt-and-braces,
  per the module docstring: "the typed word is a UI gate; `execute_wipe` is the code gate — both
  must hold"). Every outcome (refused/failed/done) is logged via `get_logger`. Full trace in the
  Wipe-safety audit below.

### `golive.py` — the Go-Live operator-facing surface

- `_dual_write(cfg, key, value)` (lines 44-56) — **the load-bearing durability primitive**. Writes
  `key=value` to `.env` (`set_env_var`) then `os.environ[key] = value`. On a durable-write failure,
  `os.environ` is **left untouched** (line 51-56) — never reflects a change that won't persist.
  Used by every toggle setter in this file (`set_postiz_config`, `set_zernio_config`,
  `set_per_account_hooks`, `set_account_casting`, `set_ai_responder`, `set_clip_profile`,
  `set_variant_learning`/`amplify`/`ucb`/`transfer`, `set_meta_creds`, `go_live`, `go_dryrun`).
- `_dual_unset(cfg, key)` (lines 59-66) — inverse of `_dual_write`.
- `_dotenv_assignment(env_path, key)` (lines 69-80) — pure read of one `.env` line's value (ignores
  comments/`export`).
- `set_postiz_config(cfg, url, key="")` (lines 83-111) — **writes then tests** the Postiz
  connection. Rejects a non-`http(s)` URL before any write (no partial state). The key is
  **write-only**: `_dual_write`s it, then calls `postiz.postiz_check_auth(cfg)` to test it, but
  **never returns or logs the key value** — a `PostizAuthError` is caught and replaced with a fixed
  message (line 101-107) specifically so the exception text (which could theoretically embed the
  key) can never leak through. The success `detail` carries only `key_set: bool`.
- `refresh_integrations(cfg)` (lines 114-125) — lists Postiz integrations; `PostizAuthError` → a
  fixed "FATAL auth failure — check POSTIZ_API_KEY" string (no `str(exc)`).
- `set_zernio_config(cfg, key)` (lines 129-148) — Zernio twin of `set_postiz_config` (hosted, no
  URL); same write-only-key discipline.
- `refresh_zernio_accounts(cfg)` (lines 151-160) — Zernio twin of `refresh_integrations`.
- `set_account_backend(cfg, handle, platform, backend, confirmed=False)` (lines 163-195) — routes
  one (handle, platform) channel to a backend. **Gated identically to the global go-live** when the
  target is a live backend: creds must exist (`cfg.backend_has_creds`), an explicit confirm is
  required, and (H3, line 181-186) the account must already have a real per-platform integration
  id — the shared legacy `account_id` is explicitly rejected as insufficient for a live route.
- `add_account(cfg, handle, platforms, persona="")` (lines 198-216) — onboards a new account via
  `accounts.add_account`; validates non-blank handle + ≥1 platform before any write.
- `set_per_account_hooks`, `set_account_casting`, `set_ai_responder`, `set_clip_profile` (lines
  219-296) — each a single `_dual_write` of one named env var, each documented as **not** a
  publish-affecting switch (creative_variation/account_casting/responder/clip_profile all
  orthogonal to `FANOPS_LIVE`). `set_ai_responder` is explicitly called out as "the ONLY intended
  way to turn the LLM responder on/off."
- `install_daemon(cfg, interval="10m")` / `uninstall_daemon(cfg)` (lines 256-280) — launchd
  install/uninstall via `fanops.daemon`; explicitly documented as scheduling-only, inheriting the
  ambient responder setting rather than forcing it on.
- `set_variant_learning`, `set_variant_amplify`, `set_variant_ucb`, `set_variant_transfer` (lines
  303-336) — four default-OFF intent-only flags for the A/B learning loop; each a single
  `_dual_write`.
- `map_account(cfg, handle, platform, integration_id)` (lines 339-359) — persists one
  (handle,platform)→Postiz-integration-id mapping via `write_integration` (atomic write to
  `accounts.json`).
- `set_meta_creds(cfg, handle, ig_user_id, token="")` (lines 362-394) — sets a **non-secret** IG
  user id (`accounts.json`, via `set_ig_user_id`, validated first so a bad handle never proceeds to
  the token write) and, if given, a **secret** Graph token dual-written to a **per-handle** `.env`
  key (`META_GRAPH_TOKEN__<SLUG>`) — never echoed. `detail` carries only `token_set: bool`.
- `DiscoveredChannel` (`NamedTuple`, lines 404-415), `_norm_handle` (418-424), `_match_channel`
  (427-440) — pure helpers for the M4 discover/adopt flow; matching is **deterministic** (exact id
  or exact normalized-handle match only — the docstring is explicit that FanOps "never merges two
  accounts on a guess").
- `discover_channels(cfg)` (lines 443-478) — **read-only**, per-provider fail-soft (a provider with
  no key is noted and skipped; a provider whose list call fails is noted, never aborts the other).
  Refuses only when neither provider is connected.
- `adopt_channels(cfg, selections, confirmed=False)` (lines 481-517) — per-row isolated: account
  creation + id mapping happen unconditionally (channel onboarded "born inert"); **only** the live
  provider routing is confirm+creds-gated (line 505-511) — so a stray POST can map a channel but
  never make it live.
- `remove_account`, `demote_account`, `promote_account`, `set_persona` (lines 520-578) — thin
  wrappers over `accounts.*` writers, each translating `KeyError`/generic exceptions into a clean
  `ActionResult`.
- `go_live(cfg, confirmed=False, *, now=None)` (lines 581-661) — **the sole setter of
  `FANOPS_LIVE=1`**. Full gate-order trace in the Go-Live flow audit below.
- `go_dryrun(cfg)` (lines 664-671) — the safe direction; `_dual_write("FANOPS_LIVE", "0")`, no
  confirm required, does not touch `FANOPS_POSTER` or any channel routing.
- `validate_learning(cfg, *, integration_id=None, confirmed=False)` (lines 674-710) — the optional
  M3 cutover probe: gated on live+Postiz-key, then the `integration_id` **must** be one of the
  operator's own mapped integrations (line 692 — never auto-picks a real channel), then confirm.
  Posts one real throwaway post via `cutover.cutover_post`. `PostizAuthError` → fixed string, never
  `str(exc)`.

### `personas.py` (studio layer) — Persona CRUD/editing surface

- `_intake(genre="")` (lines 15-20) — pure; builds the intake dict (genre is the only live field).
- `preview_compose(cfg, form)` (lines 23-58) — **transient only** — builds an in-memory `Persona`
  from the unsaved form (merging in an existing persona's saved corpus by id) and calls
  `core.compose_breakdown`; explicitly documented as **never** calling a persisting writer.
- `create_persona`, `edit_persona`, `delete_persona` (lines 61-109) — thin wrappers over
  `fanops.personas.add_persona`/`update_persona`/`delete_persona`, translating `ValueError`/
  `KeyError` into `ActionResult`.
- `add_corpus_tag`, `remove_corpus_tag` (lines 112-141) — thin wrappers, cap/duplicate errors
  surfaced verbatim.
- `connect_account(cfg, handle, persona_id)` (lines 144-162) — links/unlinks an account to a
  persona; a non-blank `persona_id` is checked to exist first (best-effort, not transactional —
  the docstring notes a possible dangling-id race is harmless since hydration falls open).
- `recommend_tag(cfg, pid, tag)` (lines 165-182) — **live single-tag Meta Graph lookup**
  (`meta_graph.tag_metrics`), read-only, does not add to the corpus.
- `research_corpus(cfg, pid, genre="")` (lines 185-202) — live co-occurrence discovery
  (`core.discover_corpus`), fail-open to the offline re-rank; persists the genre seed to intake
  first if given.
- `run_migration(cfg)` (lines 205-212) — one-click `core.migrate_from_accounts`, idempotent.

### `preview_media.py` — WYSIWYG preview media resolution

- `preview_media_path(cfg, led, post_id)` (lines 10-47) — **pure read + lock-free ffmpeg burn on
  demand**. Resolution ladder: (1) `post.render_id` → existing `Render.path` if it exists on disk;
  (2) if `post.variant_hook` and `cfg.creative_variation`, compute the deterministic render path
  via `account_render_spec` and return it if already rendered, else **actually render it now**
  via `render_account_file(..., caller="preview")` (a real ffmpeg call, not just a lookup) so the
  Review WYSIWYG can show the burned hook before approval; (3) fall back to `media_urls[0]` (local
  file only) or the base `clip.path`. All exception paths are swallowed with `except Exception:
  pass` (lines 31-32, 36-38) — fail-open to the next rung of the ladder, never a crash.

## Cluster-specific analysis

### Route table (method, URL, handler, action/view called, render target)

| Method | URL | Route fn | Calls | Renders |
|---|---|---|---|---|
| GET | `/` | `index` | `views.home_status` etc. | `home.html` |
| POST | `/home/pull-metrics` | `do_home_pull_metrics` | `actions.pull_metrics_studio` | `_publish_outcome.html` |
| POST | `/home/reconcile` | `do_home_reconcile` | `actions.reconcile_inflight` | `_publish_outcome.html` |
| POST | `/home/retry-rate-limit` | `do_home_retry_rate_limit` | `actions.retry_rate_limited_failures` | `_publish_outcome.html` |
| POST | `/home/retry-oversize` | `do_home_retry_oversize` | `actions.retry_oversize_failures` | `_publish_outcome.html` |
| GET | `/home/daemon-health` | `home_daemon_health` | `views.daemon_health` | `_daemon_health.html` (htmx poll) |
| GET | `/stitches` | `stitches` | `views.pending_stitches`/`pending_stitch_drafts` | `stitches.html` |
| POST | `/stitches/approve` | `do_approve_stitches` | `actions.approve_stitches` | `_stitches_panel.html` |
| POST | `/stitches/dismiss` | `do_dismiss_stitches` | `actions.dismiss_stitches` | `_stitches_panel.html` |
| POST | `/stitches/release` | `do_release_stitches` | `actions.release_stitches` | `_stitches_panel.html` |
| GET | `/candidates` | `candidates` | `views.review_candidates` | `candidates.html` |
| POST | `/candidates/approve/<eid>` | `do_approve_candidate` | `actions.approve_candidate` | `_result.html` |
| GET | `/review-thumb/<eid>` | `review_thumb` | filesystem (`_bounded`) | `send_file` (JPEG) |
| GET | `/publish` | `publish_panel` | `views.publish_queue` | `publish.html` |
| POST | `/publish/posted/<post_id>` | `do_mark_posted` | `actions.mark_published` | `_result.html` |
| POST | `/publish/now/<post_id>` | `do_publish_now` | `actions.publish_now` | `_result.html` |
| GET | `/reconcile-strip` | `reconcile_strip_partial` | `views.inflight_watch` | `_reconcile_strip.html` |
| GET | `/gates` | `gates` | `views.gate_rows` | `gates.html` |
| POST | `/gates/answer/<kind>/<key>` | `do_answer_gate` | `actions.answer_gate` | `_result.html` |
| GET | `/media/<post_id>` | `media` | `_media_path_for_post` + `_bounded` | `send_file` |
| GET | `/media-preview/<post_id>` | `media_preview` | `preview_media.preview_media_path` + `_bounded` | `send_file` |
| GET | `/clips/<clip_id>` | `clip_media` | `led.clips` + `_bounded` | `send_file` |
| GET | `/clip-thumb/<clip_id>` | `clip_thumb` | `discover.make_thumbnail` + `_bounded` | `send_file` (JPEG, cached) |
| GET/POST | `/review*` (16 routes) | `app_routes_review.py` | `views.review_*` / `actions.approve_*`/`cast_*`/`reschedule_post`/etc. | `review.html` / `_review_body.html` / `_surface_edit.html` |
| GET/POST | `/run*`, `/library*` (9 routes) | `app_routes_run.py` | `views.pipeline_status`/`asset_catalog` / `actions.run_*`/`save_uploads*` | `run.html` / `_run_panel.html` / `library.html` |
| GET/POST | `/schedule*`, `/lift`, `/posted*` (16 routes) | `app_routes_schedule.py` | `views.schedule_*`/`lift_rows`/`posted_library` / `actions.reschedule_*`/`publish_*`/`repost_post`/`crosspost_*` | `schedule.html` / `_schedule_panel.html` / `lift.html` / `posted.html` / `_posted_panel.html` |
| GET | `/live-library` | `live_library` | `views.live_library` | `live_library.html` |
| POST | `/live-library/wipe/preview` | `do_wipe_preview` | `actions_wipe.preview_wipe` | `live_library.html` (preview block) |
| POST | `/live-library/wipe/confirm` | `do_wipe_confirm` | `actions_wipe.confirm_wipe` | `live_library.html` (result block) |
| GET/POST | `/personas*` (12 routes) | `app_routes_personas.py` | `views.personas_page` / `studio_personas.*` | `personas.html` / `_personas_panel.html` / `_persona_drawer.html` / `_persona_compose.html` |
| GET/POST | `/golive*` (24 routes) | `app_routes_golive.py` | `views.golive_status` / `golive.*` | `golive.html` / `_golive_panel.html` / `golive_page.html` / `_health_pills.html` / `_golive_health.html` |

**htmx-driven partial swaps**: every `_*_panel.html`/`_*_body.html`/`_result.html`/`_surface_edit.html`
target is an htmx partial (outerHTML swap into a named mount). Two routes explicitly branch
rendering on the `HX-Request`/`HX-Target` headers: `/schedule/reconcile`
(`app_routes_schedule.py:80-88`, checks `request.headers.get("HX-Target")` for `"reconcile-strip"`
to pick between `_reconcile_strip.html` and the full schedule panel) and the two error-status
handlers (`app.py:546-553`, `app_routes_run.py:93-100`) which deliberately return **HTTP 200**
instead of 500/413 because "htmx 2.x drops non-2xx swap bodies" (stated explicitly in both
docstrings).

### Upload-safety audit (`actions_run.py:save_uploads`, lines 131-182)

Verified against the CLAUDE.md claim line by line:

1. **Video extension validation**: `allowed_ext` defaults to `_VIDEO_EXT` (line 140), and each
   file's extension is checked (`Path(name).suffix.lower() not in allowed_ext` → skip, line
   153-154). Confirmed.
2. **Traversal-safe naming**: the raw filename is checked for `/`, `\`, `..` **before**
   `secure_filename` is even trusted (line 150-152: `if not name or "/" in raw or "\\" in raw or
   ".." in raw: skip`) — i.e. it checks both the sanitized *and* the raw name, which is stricter
   than `secure_filename` alone. Confirmed, and slightly more defensive than the CLAUDE.md summary
   suggests.
3. **Inbox-bound resolve**: `dest = (inbox / name).resolve()`, then `if not
   dest.is_relative_to(inbox): skip` (lines 157-159) — a second, independent traversal check
   ("belt-and-braces" per the inline comment) after the name-based one. Confirmed.
4. **Atomic `.uploadpart` → `os.replace`**: `tmp = inbox / f"{name}.uploadpart"`;
   `f.save(str(tmp))` then `os.replace(tmp, dest)` (lines 163-166), with a cleanup of the partial
   temp on `OSError`. `.uploadpart` is explicitly not in `MEDIA_EXT`, so a leaked temp file is
   never picked up by a later ingest pass. Confirmed.
5. **`MAX_CONTENT_LENGTH` cap**: set in `create_app` from `cfg.upload_max_bytes`
   (`app.py:250`, comment: "FANOPS_UPLOAD_MAX_MB"). Werkzeug enforces this **before** the view runs
   (raises `RequestEntityTooLarge`, caught by the `_too_large` errorhandler,
   `app_routes_run.py:93-100`). Confirmed — this is a Werkzeug-level guarantee, not something the
   view code can circumvent.

Additional hardening beyond the CLAUDE.md summary: a filename collision (post-sanitization) is
never allowed to `os.replace` over a **different** file — a collision appends a random 8-hex-char
discriminator to the stem (lines 160-162, "ING-4"). A file that fails `has_video_stream` probing
(when `probe=True`) is deleted and skipped rather than kept-then-aborting the whole native ingest
pass later (lines 171-178, "ING-9"). An all-rejected upload batch returns `ok=False` (not a
misleading green "0 saved", line 180-181).

**Third-party upload path** (`save_thirdparty_uploads`, `actions_run.py:206-212`) reuses the exact
same `save_uploads` contract but redirects `dest_dir=cfg.thirdparty_inbox` — a **separate directory
tree** from `01_inbox`, so a native ingest pass can never accidentally pick up and mislabel a
third-party asset. Confirmed structurally isolated at the directory level, not just a flag.

### Go-Live flow trace (`app_routes_golive.py` + `golive.py`)

**Claim: `FANOPS_POSTER=postiz` is set only through this path, only behind an explicit confirm.**

This claim as literally stated is **stale/inaccurate for the current code** — `go_live` in the
current `golive.py` **does not write `FANOPS_POSTER` at all**. Per the docstring at
`golive.py:652-657` ("D12: go_live NEVER writes FANOPS_POSTER — per-channel accounts.json routing
is the source of truth") and the code at `golive.py:632-661`, `go_live` only:
1. Writes `FANOPS_LIVE=1` (the sole thing it sets, `golive.py:632-634`).
2. **Unsets** `FANOPS_POSTER` if it's currently `dryrun` on disk or in the live environment (lines
   642-646) — actively scrubbing a stale value, never setting it to `postiz`.

The actual per-channel publish routing is set by `set_account_backend` (`golive.py:163-195`),
reachable via `POST /golive/account/backend` (`app_routes_golive.py:108-114`). **This** is the
function that can route a channel to `"postiz"` (or `zernio`/`rest`/`mcp`), and it **is** gated:
creds must exist for that backend (`cfg.backend_has_creds(bk)`, line 175), an explicit
`confirmed=True` is required for any live backend (line 178-180, derived in the route from
`request.form.get("confirm") == "1"`), and the target must already carry a real per-platform
integration id (line 181-186, the H3 fix). So the underlying security property CLAUDE.md is
gesturing at — "no publish route flips live without creds + confirm" — **does hold**, just via
`set_account_backend` rather than by writing an env var literally named `FANOPS_POSTER=postiz`.

**Confirmed**: `FANOPS_LIVE=1` (the global dryrun↔live switch) is set **only** by `go_live`
(`golive.py:581-661`), reached **only** via `POST /golive/live` (`app_routes_golive.py:203-208`),
which is the **only** route in the entire cluster (grepped: no other write of `FANOPS_LIVE` exists
outside `golive.py`) that can flip it. The gate order inside `go_live` is, in code order:
1. `Accounts.load(cfg).validate()` — malformed/empty-id accounts refuse (lines 594-600).
2. `accounts.live_ready_channels()` — refuses if zero active channels have both a provider and
   creds (lines 601-604).
3. **M6 past-due backlog gate** (lines 605-628) — refuses if any `queued` post's `scheduled_time`
   is already due/past, specifically to prevent the daemon's first live tick from "machine-gunning"
   a backlog; a torn ledger here is logged and refused rather than silently skipped (lines
   615-621).
4. `if not confirmed: return ActionResult(ok=False, ...)` (lines 629-631) — the explicit human
   confirm, **last**, only after every structural gate has passed.
5. Only then `_dual_write(cfg, "FANOPS_LIVE", "1")` (line 632).

Every failing gate leaves `.env` and `os.environ` **completely untouched** (no partial writes on a
refused flip). `go_dryrun` (the reverse direction) needs no confirm, by design (line 664-671, "the
safe direction, always allowed").

**Claim: the Postiz API key is write-only — never rendered back.** Confirmed across every code path
that touches it:
- `set_postiz_config` (`golive.py:83-111`) writes the key via `_dual_write` but the returned
  `ActionResult.detail` carries only `{"url": ..., "key_set": bool, "auth": "ok"}` (line 111) — no
  key.
- A `PostizAuthError` raised during the test call is caught and replaced by a **fixed string**
  (line 106-107) — explicitly to prevent `str(exc)` from ever being able to leak a key even if a
  future exception implementation embedded it.
- `refresh_integrations` (line 122) likewise returns a fixed "FATAL auth failure — check
  POSTIZ_API_KEY" string, no `str(exc)`.
- Templates: grepped `templates/` for any Jinja reference to `postiz_api_key`/`api_key` rendering —
  none found; the Go-Live template only ever receives `views.golive_status(cfg)`, whose
  `GoLiveStatus` dataclass (per C10's trace) stores only a `key_set` boolean, never the key value.
- Same discipline is mirrored for `ZERNIO_API_KEY` (`set_zernio_config`, lines 129-148) and the
  per-handle Meta Graph token (`set_meta_creds`, lines 362-394 — `detail` carries `token_set: bool`
  only).

Confirmed sound.

### Approval-lifecycle audit (`actions_approve.py`)

Every approve path funnels through **one** primitive: `led.approve_post(pid, now_iso=...,
suggested_iso=...)`, called only from `_approve_ids_with_render` (`actions_approve.py:140`) — the
single shared engine every public approve function (`approve_posts`, `approve_batch`,
`approve_clip`, `approve_account`, `approve_moment`, `approve_as_is`) reduces to via
`_approve_matching`/direct call. `approve_with_hook` also calls `led.approve_post` at the same
line-140-equivalent (`actions_approve.py:267`), after its own render/rollback logic, so it is
covered by the same primitive.

Grepped every `.py` file in this cluster for a state write to `PostState.queued` that does **not**
go through `led.approve_post`: found only `actions.recover_posts` (`actions.py:963`, the S1
recovery cockpit, explicitly a *different* lifecycle transition — failed→queued for retry, not an
approval) and `actions.retry_rate_limited_failures`/`retry_oversize_failures`
(`actions.py:890,921`, same recovery-cockpit family). None of these bypass an approval gate — they
operate only on already-`failed`/`error` posts, never on `awaiting_approval` posts, so they cannot
be used to skip the human approve step. **Confirmed**: no path in this cluster promotes an
`awaiting_approval` post to `queued` without going through `led.approve_post`.

### Wipe-safety audit (`actions_wipe.py` + `app_routes_live.py`)

This is the most dangerous surface in the cluster; full trace of every gate:

**Reachability**: the wipe is reachable from exactly two POST routes, both in
`app_routes_live.py` (lines 23-34), both requiring the operator to already be on the `/live-library`
page. No other route, CLI verb, or daemon path in this cluster calls `actions_wipe.confirm_wipe`
(grepped: zero callers outside `/studio/`).

**Two-step flow**:
1. `POST /live-library/wipe/preview` → `actions_wipe.preview_wipe(cfg)`
   (`actions_wipe.py:24-32`) — **strictly read-only**: `Ledger.load(cfg)` (fail-closed to a clean
   error on a torn ledger, never a silent empty preview) then `ledger_wipe.wipe_preview(led)`, a
   pure computation returning the would-remove id-set + per-entity counts. No write of any kind.
   This is the "shown BEFORE the confirm form appears" step (per the template-wiring comment,
   `app_routes_live.py:25`).
2. `POST /live-library/wipe/confirm` → `actions_wipe.confirm_wipe(cfg, typed=...)`
   (`actions_wipe.py:35-59`) — the only mutating call. Gate order, verified in code:
   - **Gate A (typed word)**: `(typed or "").strip().upper() != CONFIRM_WORD` (`CONFIRM_WORD =
     "REMOVE"`, line 21) → refuse *before* touching the ledger at all, logged
     `wipe_refused_bad_confirm` (lines 41-43). This is a UI-layer gate — a wrong or blank word is
     the fast-refuse path.
   - **Gate B (mandatory snapshot)**: `Ledger.snapshot(cfg)` is taken **unconditionally** once
     Gate A passes, *before* any removal logic runs (lines 45-49). A snapshot failure refuses with
     `wipe_refused_snapshot_failed` logged — the wipe cannot proceed without a snapshot existing.
   - **Gate C (snapshot verified restorable)**: `ledger_wipe.snapshot_is_restorable(snap)` must
     return true, else refuse with `wipe_refused_snapshot_unverified` logged (lines 50-52) — an
     unverifiable snapshot blocks the wipe even though it was successfully written.
   - **Gate D (code-level re-check)**: `ledger_wipe.execute_wipe(cfg, confirmed=True,
     snapshot_path=snap)` (line 54) — outside this cluster (in `fanops/ledger_wipe.py`), but the
     module docstring (`actions_wipe.py:1-11`) states explicitly that `execute_wipe` "itself
     re-checks the snapshot + confirm in code," i.e. the confirm boolean is not trusted blindly
     from the Studio layer — there is a second gate inside the wiped-computation module itself.
   - Every terminal outcome (refused at any gate, failed, or done) is logged via
     `get_logger(cfg)(...)` (lines 42, 48, 51, 56, 58) — **never a silent removal**.

**No dry-run mode confusion**: `preview_wipe` and `confirm_wipe` are two distinct functions with
no shared code path that could accidentally skip the confirm — `confirm_wipe` never calls
`preview_wipe` internally, and there is no flag that turns `confirm_wipe` into a preview.

**No unauthenticated/single-click path**: reaching a successful wipe requires, at minimum: (1) an
operator on `/live-library`, (2) a first POST to see the preview (not strictly required by the
server — see note below — but the *only* UI path to reveal the confirm form), (3) typing the exact
word `REMOVE` into a form field and submitting a second POST.

**One structural note, not a defect but worth flagging**: `do_wipe_confirm`
(`app_routes_live.py:29-34`) does **not itself** require that `do_wipe_preview` was called first —
a client that already knows the URL and the literal string `"REMOVE"` could POST directly to
`/live-library/wipe/confirm` without ever hitting `/wipe/preview`. The *code-level* confirm gate
(Gate A) still holds regardless, so this is not a bypass of the destructive-action gate itself,
only of the "operator saw what will be removed" UX step — see Anomalies below.

**What it removes**: `actions_wipe.py` itself does not enumerate the removed entity types — that
computation lives in `fanops/ledger_wipe.py` (`wipe_preview`/`execute_wipe`), outside this cluster's
scope per the task's file list. The Studio-layer contract is: `preview_wipe` surfaces exactly what
`execute_wipe` will remove (same underlying `ledger_wipe` computation), so the preview is not
merely advisory-and-possibly-stale — it is the same function family.

### Anomaly hunting

1. **`app_routes_live.py:29-34` — wipe-confirm reachable without a prior preview call.**
   `do_wipe_confirm` performs no server-side check that `do_wipe_preview` was ever invoked in the
   same session; the "preview-first" UX is enforced only by the template hiding the confirm form
   until a preview result exists. A client (or a replayed/scripted POST) that already knows the
   confirm word can call `/live-library/wipe/confirm` directly. This does **not** bypass the typed-
   word/snapshot/restorability code gates (those hold regardless of route ordering), so it is not a
   safety bypass in the destructive sense — but it does mean "the operator must see the preview
   before confirming" is a UI convention, not a server-enforced invariant. Low severity given the
   code gates underneath, but worth naming since the task explicitly asked whether *any* wipe path
   is reachable without multi-step confirmation — this one technically is, if the two POSTs aren't
   sequenced by the server.

2. **`golive.py:452-478`, `discover_channels` — an unsupported platform is silently downgraded to a
   note, not surfaced as an error to the caller of `refresh_zernio_accounts`/similar.** Minor: a
   channel whose remote platform isn't in `_PLATFORM_VALUES` is appended to `notes` (line 470-471)
   rather than raising, which is the documented fail-soft behavior — not a bug, but it does mean a
   provider returning a platform FanOps doesn't model produces a silently-smaller `channels` list
   with only a best-effort textual note as the trail. Not flagged as a defect, just noted as a
   design choice worth being aware of when debugging "why didn't my channel show up in Discover."

3. **`preview_media.py:31-32, 36-38` — two bare `except Exception: pass` blocks in the WYSIWYG
   preview path.** Both are documented as intentional fail-open steps in a resolution ladder (fall
   through to the next rung — an existing render path, then `render_account_file`, then
   `media_urls`, then the base clip), and the function as a whole cannot 500 by construction. Still,
   this is the one place in the cluster where an exception is swallowed with zero logging at all
   (contrast with `actions_run.kick_prepare`'s `get_logger(...)("kick_failed", ...)` on its
   fail-open path, or `actions_approve._warm_renders`'s per-post logged failure). A silently-failing
   `render_account_file` call here means the WYSIWYG preview could keep showing a stale/wrong file
   with no trail to debug why the "live" render never appeared. Low severity (read-only preview
   path, not a mutation), but it is the one genuinely silent exception swallow in the cluster.

4. **`app.py:158-160`, `_account_arg` — a bare `except Exception: pass` around the
   `current_app.config.get("FANOPS_CFG")` handle-resolution call.** Fail-open to the raw
   query-string value (documented behavior: "never raises; an unknown handle simply matches zero
   rows"), so this is intentional and low-risk (worst case is a filter that doesn't normalize
   `@handle` vs `handle`), but it's another unlogged swallow, consistent with anomaly 3's pattern —
   this cluster generally logs its swallowed exceptions in the *mutation* layer (`actions*.py`) but
   not in the *read-helper* layer (`app.py`, `preview_media.py`).

5. **No TODO/FIXME markers found** in any of the 17 files (grepped `TODO|FIXME|XXX|HACK` — zero
   hits). No dead/unreachable functions found via cross-reference against C10's call graph and the
   `grep` sweep for external callers — every public function in every action module has at least
   one route caller inside this cluster.

6. **`golive.py:652-657` — the CLAUDE.md project-notes claim about `FANOPS_POSTER=postiz`
   being set through the Go-Live path is stale relative to the current code.** As traced above,
   `go_live` explicitly never writes `FANOPS_POSTER` (comment: "D12: go_live NEVER writes
   FANOPS_POSTER"); the actual live-routing setter is `set_account_backend`. This is a
   documentation/reality drift, not a code defect — the underlying safety property (creds + confirm
   before any channel goes live) holds under the current per-channel design; it's just enforced by a
   different function than the historical global-poster-variable model implies. Flagging so the
   next person tracing "where does FANOPS_POSTER get set to postiz" doesn't waste time — it doesn't,
   any more, on the current architecture; `set_account_backend`'s `accounts.json`
   `backends[platform]` field is the live source of truth per channel.
