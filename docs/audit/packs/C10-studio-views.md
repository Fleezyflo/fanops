# Pack: C10 — studio views

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C10-F01 | low | doc-rot | closed on this tree | `app_routes_home` passes `zero_post_clips`. The frozen unwired claim in `full-trace-index.md` and `anomalies.md` is marked stale | `app_routes_home` |
| C10-F02 | medium | dead-after-swap | closed on this tree | `schedule.js` listens on `document` and re-reads `#schedule-body` per event. Binding the dialog and drag handlers to that node dies on the htmx `outerHTML` swap | `static/schedule.js` `panel()` |
| C10-F03 | medium | pagination | closed on this tree | Posted Show more keeps `source`, `delivery`, and `failure`, and is omitted when `next_offset` is none | `_posted_panel.html` |
| C10-F04 | medium | error-state | closed on this tree | A failed re-burn, regenerate-hook, or tag swap with `swap=card` or `swap=feed` re-renders that card and shows the error inside it | `app_routes_review._render_swap_target` |

## Evidence commands run

```
git rev-parse HEAD
git show HEAD:src/fanops/studio/app_routes_home.py
git show HEAD:src/fanops/studio/app_routes_review.py
git show HEAD:src/fanops/studio/app_routes_schedule.py
git show HEAD:src/fanops/studio/app.py
rg 'zero_post_clips|active_source|active_failure|page.next_offset' src/fanops/studio/
rg ' as |import ' src/fanops/CLAUDE.md
```

Node harness against `schedule.js` before the fix: `{"opensOnFirstPanel":1,"opensAfterSwap":0}`. After the fix: `{"opensOnFirstPanel":1,"opensAfterSwap":1}`. Pytest was not run.

## Full checklist 2026-10-17

Subject `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e`. Product code read with `git show HEAD:path`. Unrelated dirty files were left in place. `views_common.py` was not edited.

### Home, review, schedule, posted against route kwargs

| Surface | Route | View calls | Template receives | Result |
|---------|-------|------------|-------------------|--------|
| Home | `index` | `home_status`, `home_accounts_panel`, `home_source_gallery`, `home_week_calendar`, `zero_post_clips` | `status`, `accounts_panel`, `gallery`, `calendar`, `zero_post_clips`, `tab` | Match. `home.html` reads those fields. Gallery partial reads `gallery.entries/page/pages`. |
| Home partials | `home_gallery`, `home_daemon_health`, home posts | `home_source_gallery(page=)`, `daemon_health_strip`, action results | `gallery` or `daemon` or `result`+`cfg` | Match. `cfg` is also a context processor. |
| Review | `review` / `_review_context` | `review_buckets`, `review_feed_rows`, `review_matrix`, `account_lanes`, chips | cards, page, counts, feed, matrix, lanes, `active` from `_card_chips` | Match. `active_source`, `active_state`, `active_view`, `compact`, `ultra`, `cast_state`, `cfg` come from `_inject_nav_account`. |
| Schedule | `_schedule_panel` | `schedule_rows`, `schedule_calendar_month`, `schedule_bucket_split`, `schedule_lanes`, `due_publish_plan`, `schedule_cockpit`, `inflight_watch` | rows, lanes, calendar, bucket, cockpit, `active_source`, `due_plan`, `auto_ship`, `backend` | Match. `cockpit` is none without `?account=` and `_schedule_cockpit.html` guards it. |
| Posted | `_posted_panel` | `posted_library`, `posted_archive_rows`, `failure_rollup`, `paginate`, `lift_rows` on full GET | rows, page, `active_source`, `active_delivery`, `active_failure`, lift ctx | Route passed the filters. The Show more link did not. C10-F03. |

No call in these routes passes a keyword the view function does not accept (AST check of HEAD signatures).

### Empty, error, and permission states

- **Empty.** Home: onboarding when there are no accounts and no sources; account tiles and the source gallery each have their own empty line. The week grid always renders seven dated columns. Review: switcher prompt, per-account feed empty, and "No footage yet" / "No work for {account}". Schedule: "No approved posts yet" and "No scheduled posts for {account}"; a month with chips elsewhere names `calendar.offscreen`. Posted: "Nothing in this view yet."; Lift and What's working use their empty-reason strings.
- **Error.** `ControlFileError` renders standalone `error.html` with `message`. Home status, gallery, and week calendar log and return empty counts rather than a 500. Daemon snapshot `unknown` warns; `daemon_health_strip` returning none stays silent (the non-darwin path, already covered by `test_daemon_health_none_is_silent_not_500`). Review and Posted surface `result.error` through `operator_error`. A vanished post re-renders `_result.html`.
- **Permission.** Studio is localhost with no auth, stated in `src/fanops/studio/CLAUDE.md`. There is no permission-denied state. That is the design, not a gap.

### Not findings

- `src/fanops/studio/CLAUDE.md` still cites `app.py:365` for `zero_post_clips`. The file says line anchors are not a promise. The symbol is passed from `app_routes_home.index`. C10-F01 stays closed.
- `docs/CODEMAPS/subsystem-traces/C10_studio_views.md` still says `zero_post_clips` has no caller. H-DOCS owns that trace. The route on this HEAD does pass it.
- Account chips reset other query args on purpose (`_account_filter.html` only passes `account` and `compact`). Source chips keep delivery and failure. Not changed.
- The Posted `{% elif page.offset > 0 %}` branch is unreachable when `total > items` (the last page takes the first branch). Left in place.
- No symbol was deleted, so the alias grep in `src/fanops/CLAUDE.md` did not block a removal.

### views_common.py

No fix needed that file. Publish lane. Skipped edits: none.

## Wave 1 evidence (dirty tree)

HEAD stayed `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e`. No worktree, no commit, no pytest, no push. `views_common.py`, `actions_*.py`, and `app_routes_*.py` were not edited. C10-F01, C10-F02, and C10-F03 stay closed. C10-F04 is open: the repair is the failure branch in `app_routes_review.py`, which this pass does not own.

Empty copy on a temp ledger (`create_app` test client, HTTP 200):

| GET | Copy present |
|-----|----------------|
| `/` | Get started, Connect accounts, No sources yet |
| `/review` | No footage yet |
| `/review?account=a` | No work for a — pick another account |
| `/review?account=all` | No footage yet |
| `/schedule` | No approved posts yet |
| `/schedule?account=a` | No untimed posts, No timed posts, and the schedule dialog. "No scheduled posts for a" is absent because `bucket` is always a dict when `account` is set. `_schedule_panel.html` is not in this dirty set |
| `/posted` and `/posted?delivery=failed&failure=rate_limit` and `/posted?account=a&delivery=failed` | Nothing in this view yet |

One awaiting post (`hook_burn_failed`, hook `look`): `/review?account=a` renders `#feed-p1`, the hook form `hx-target="#feed-p1" hx-swap="outerHTML"`, and the preburn overlay. No `#res-p1`. No tag select, because `shipped_tags` is empty without a lock. `/review?account=all` renders `hx-target="#card-ca"`.

`POST /swap-tag/p1` with `swap=card` and a tag that is not on the post returned 200 and only `<span class="err">✗ #missing is not on this post</span>`. `POST /swap-tag/p1` with empty tags returned the same shape (`tag swap needs the current tag and the replacement`). `POST /reburn-hook/does-not-exist` returned `<span class="err">✗ no such post: does-not-exist</span>`. A live reburn of an existing post was not finished: `render_moment` did not return and the process was killed. The route still sends every `not result.ok` reburn and regenerate-hook through `_focus_mutation_response`, which renders `_result.html`. The libass warning (`ok` and `hook_burned` false) is a different path: `_render_tag_swap` returns the card, and `_hook_edit.html` prints `.result.err`.

`schedule.js` still re-reads `#schedule-body`. `panel()` is `getElementById('schedule-body')` inside click, dragstart, dragover, and drop (4 call sites). Submit re-reads the dialog fields with `byId` at event time. Node + jsdom, after an `outerHTML` swap of the panel: `{"opensOnFirstPanel":1,"postOnFirst":"p1","dialogOpenImmediatelyAfterSwap":0,"opensAfterSwap":1,"postAfterSwap":"p2","cancelClosesAfterSwap":1,"emptyClickThrew":0}`.

### Commands

```
git rev-parse HEAD
git status --short --branch
git diff --stat -- src/fanops/studio/views_hashtags.py src/fanops/studio/views_posted.py src/fanops/studio/views_review.py src/fanops/studio/views_run.py src/fanops/studio/views_schedule.py src/fanops/studio/static/schedule.js src/fanops/studio/static/studio.css src/fanops/studio/templates/_card.html src/fanops/studio/templates/_posted_panel.html src/fanops/studio/templates/_review_feed_card.html src/fanops/studio/templates/_surface_edit.html
git diff -- src/fanops/studio/views_review.py src/fanops/studio/static/schedule.js src/fanops/studio/templates/_card.html src/fanops/studio/templates/_posted_panel.html src/fanops/studio/templates/_review_feed_card.html src/fanops/studio/templates/_surface_edit.html
PYTHONPATH=src python3  # create_app test client: empty GETs above, one-post review GET, POST /swap-tag/p1 and POST /reburn-hook/does-not-exist
NODE_PATH=/Users/molhamhomsi/pipeline/hrmny-pipeline/node_modules node  # jsdom harness for schedule.js
```

Pytest was not run. No product file was changed for C10-F04.
