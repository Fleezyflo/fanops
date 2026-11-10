# Pack: C6 — crosspost, publish & post

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Entry symbols re-read 2026-10-18: `crosspost` assert `_JITTER_MAX < _STEP_MIN`, `post/run.py` `_NET_POST_FIELDS` and `_publish_one`, `postiz.py` YouTube `Post.caption` versus `posted_text_for`, `zernio.py` `posted_text_for`. `postiz.py` and `reconcile.py` were not edited. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C6-F01 | low | invariant | closed on origin/main | Module-level `assert _JITTER_MAX < _STEP_MIN` | `crosspost.py` module-level `assert _JITTER_MAX < _STEP_MIN` |
| C6-F02 | medium | persistence | closed on origin/main | Immediate `submitted`→`published` promotion persists `publish_hour` and `publish_dow` through `_NET_POST_FIELDS` | `post/run.py` `_NET_POST_FIELDS` includes `publish_hour` and `publish_dow`. Dry-run calls `_handle_dryrun_boundary` and does not POST. Postiz IG/TT and Zernio `content` are `posted_text_for`. Postiz YouTube `content` is `Post.caption` |

## Evidence commands run

```
rg '_JITTER_MAX|_STEP_MIN' src/fanops/crosspost.py
tests/test_post_run.py tests/test_responder.py tests/test_dryrun_boundary.py
```

## Full checklist 2026-10-17

Subject `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e`. Product code was read with `git show HEAD:path` and the working tree. `crosspost.py`, `pipeline.py`, `postiz_lifecycle.py`, `stranded_posts.py`, `gate_keys.py`, `escalation.py`, `post/providers.py`, `post/publish_requeue.py`, `post/__init__.py`, `doctor.py` backlog loop, and `studio/golive.py` `_drainable_past_due` are clean against HEAD; those citations are HEAD. `post/run.py`, `post/postiz.py`, `post/zernio.py`, `post/dryrun.py`, `post/publish_archive.py`, `reconcile.py`, `caption_compose.py` are dirty; the publish-selection and park quotes below are the HEAD text (`git diff` hunks do not touch them). The caption-wire quotes name which copy.

`publish_due` / `needs_reconcile` entry / drainable membership are unchanged versus HEAD. Finalize now also writes `sent_content` (already dirty) and `publish_hour` / `publish_dow` (C6-F02). The working tree changes the unbound-TikTok caption search from `posted_text_for` to `caption_shown`. That is a lookup string, not the park predicate.

### Dry-run versus live send

HEAD `post/run.py` `_post_provider`: not live returns `"dryrun"` for every channel. Live returns `accounts.effective_provider`. `publish_due` on `"dryrun"` calls `_handle_dryrun_boundary` and leaves the post `queued` (`not_distributed`). It does not call `_publish_one`. `publish_post` does the same and returns `queued`.

HEAD `post/__init__.py` `get_poster`: live plus backend `dryrun` raises `RuntimeError` and does not build `DryRunPoster`. Production `publish_due` never reaches that raise. `providers.py` `_postiz_poster` / `_zernio_poster` / `_dryrun_poster` are live lazy factories (`get_poster` → `get_provider`). `src/fanops/CLAUDE.md` names that alias trap. Nothing in `post/` was deleted.

HEAD `write_preview` stores `posted_text_for` as `text` and writes no state, id, or URL. Working tree `write_preview` stores `send_caption` text (YouTube also writes the tag labels). Live Postiz YouTube `content` is `Post.caption` on both copies, so the working-tree preview matches the live YouTube description. HEAD dry-run preview for YouTube was the composed string.

### needs_reconcile / ambiguous park

HEAD `_publish_one`, after `poster.publish` returns `submitted`: a non-empty `public_url` promotes to `published`; an empty URL sets `needs_reconcile` with reason `submitted_awaiting_permalink` and does not set `failed`. The exception handler refuses to downgrade an existing `needs_reconcile`. A transient error with a real `submission_id` parks `needs_reconcile`; without one it sets `failed` / `ErrorKind.transient`.

HEAD `PostizPoster.publish`: `RequestException` and HTTP 5xx set `needs_reconcile` and return (no second POST). 429 retries inside the loop (tests call a 429 pre-processing). Other 4xx set `failed` only when the row is not already `needs_reconcile`. `_postiz_permalink` returns `None`, so a fresh Postiz publish parks.

HEAD `ZernioPoster.publish`: `Created` and `IdempotentReplay` set `submitted` plus the returned id. `ReconciliationRequired` (409, 5xx, lost response, 429 past the deadline) sets `needs_reconcile` and writes `reconcile_candidate_id`, never `submission_id`. `TerminalFailure` is the only `failed` verdict.

`reconcile_due` reads outside the flock and applies inside one transaction. `reconcile_posts` polls `_RECONCILABLE` and held rows that already have a real id. `publish_due` iterates `queued` only, so a parked row is not re-POSTed.

### Idempotency of a retried publish

Claim inside `_publish_one` publishes only while state is still `queued`, and refuses a real `submission_id` before the claim commits (`skip_resubmit_existing_id`, left `queued`). A real id that appears after the claim leaves the row `submitting` for reconcile. Zernio sends the same `x-request-id` (`_request_id`: version, `post.id`, `created_at`, platform, `account_id`) on every attempt inside the deadline. Postiz has no idempotency key: it GETs a matching integration plus `content` before POST and again after a lost response, then adopts that id. `failed` with no real id is the re-queueable state (`publish_requeue.py`, bounded). `needs_reconcile` is not.

### posted_text_for versus Post.caption

HEAD `posted_text_for` is `compose_posted_caption(post.caption, ship_from_lock(picks, lock)[:4])`. Empty lock returns `Post.caption` with hashes kept. HEAD `PostizPoster.publish`: `content = post.caption if youtube else posted_text_for(...)`. YouTube tags are `_youtube_tags(post.hashtags)` on `settings.tags`. HEAD `ZernioPoster._create` sets `content=posted_text_for(...)` (TikTok). `Post.caption` stays the stored sentence.

Working tree (dirty, not reverted): `send_caption` is the send decision. IG/TT is `_posted_ship` (the string `posted_text_for` returns). YouTube text is `Post.caption` raw. `PostizPoster` still uses that same youtube-versus-`posted_text_for` split, then sets `post.sent_content = content` before adopt or POST. `ZernioPoster._create` still sends `posted_text_for` and stamps `sent_content`. `caption_shown` is `sent_content` when it is a string, else `send_caption` text. `reconcile.py` `_vendor_lookup_candidate_ids` searches `caption_shown(...)[:80]` (HEAD searched `posted_text_for(...)[:80]`). `_NET_POST_FIELDS` includes `sent_content` so finalize keeps the stamp.

### Zernio content / mediaItems

HEAD and working tree `build_zernio_payload` (identical): `content` is the argument, `publishNow: True`, no `scheduledFor`. Empty media raises `ValueError` matching `no media`. Otherwise `mediaItems` is `[{"type": "video", "url": u}]` after `_zernio_media_url`. TikTok adds `platformSpecificData.tiktokSettings`. The caller (`ZernioPoster._create`) passes `posted_text_for` as `content`.

### publish_due / needs_reconcile / drainable on HEAD

`publish_due` (HEAD `run.py`): load queued posts, keep `_due_or_fail` (missing `scheduled_time` stays queued; unparseable time becomes `failed`). Drop `not can_promote` (`skipped_retired_lineage`). Drop `_non_active_row` (`skip_account_not_active`). `ensure_up` only when something remains. Per post: no provider stays queued; `dryrun` previews and stays queued; else `_publish_one`. Return counts `due`, `published`, `no_provider`, `no_integration_id`, `not_distributed`, `skipped_existing_id`, `not_live_ready`, `skipped_retired_lineage`, `skipped_not_active`. A `published` count is only the value `PostState.published` after finalize. `needs_reconcile` is not counted as published.

`needs_reconcile` entry is the park section above. It is not a `publish_due` input state.

Drainable, HEAD and this tree (both files clean in the relevant functions):

`doctor._daemon_liveness_check` counts a queued post as drainable when `is_due_or_past`, age is past `2 * interval`, `can_promote`, and `_non_active_row` is None. It does not apply the dry-run, no-provider, or no-integration skips. The hint text is `{n} drainable past-due`.

`golive._drainable_past_due` is queued and `is_due_or_past` and `can_promote` and active row. A positive count refuses Go Live. No grace window.

`pipeline._stage_crosspost` calls `crosspost_clips` inside the pass and logs a non-auth error. `_publish_safe` calls `publish_due(cfg, now=None)` after the main transaction and re-raises `AuthError`. `surface_time` uses `_STEP_MIN = 40` and `_JITTER_MAX = 30` with the module assert (C6-F01, not reopened). `crosspost.py` was not edited.

### C6-F02

Input: queued post, poster returns `submitted` plus a non-empty `public_url`. `_publish_one` sets `publish_hour` / `publish_dow` from `publish_buckets(published_at)` on the throwaway ledger, then finalize used to copy only `_NET_POST_FIELDS`. HEAD omitted the pair, so the committed row stayed `None` (`published_at` was kept). Reproduced before the edit: state `published`, buckets `(22, 4)`, stored `(None, None)`. After adding both names to the tuple, the same harness stored `(22, 4)`. Regression: `tests/test_post_run.py` `test_publish_persists_timing_buckets`. Pytest was not run.

### Evidence commands

```
git rev-parse HEAD
git diff -U0 -- src/fanops/post/run.py src/fanops/post/postiz.py src/fanops/post/zernio.py src/fanops/post/dryrun.py src/fanops/reconcile.py src/fanops/doctor.py
git show HEAD:src/fanops/crosspost.py | sed -n '23,29p'
git show HEAD:src/fanops/post/run.py | rg -n 'def publish_due|def _publish_one|needs_reconcile|provider == .dryrun.'
git show HEAD:src/fanops/post/zernio.py | sed -n '287,311p'
git show HEAD:src/fanops/post/postiz.py | sed -n '545,551p'
git show HEAD:src/fanops/post/dryrun.py | sed -n '16,25p'
git show HEAD:src/fanops/caption_compose.py | sed -n '80,94p'
rg -n 'def _drainable_past_due|drainable past-due|def publish_due' src/fanops/doctor.py src/fanops/studio/golive.py src/fanops/post/run.py
rg -n 'aliased imports|providers.py' src/fanops/CLAUDE.md
rg -n 'def _postiz_poster|def get_poster|class DryRunPoster' src/fanops/post
```

## Wave 2 evidence (dirty tree)

Subject `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e`, working copies, not `git show HEAD`. `crosspost.py` is clean. Dirty in this lane: `caption_compose.py`, `post/dryrun.py`, `post/postiz.py`, `post/publish_archive.py`, `post/run.py`, `post/zernio.py`, `reconcile.py`. C6-F01 and C6-F02 stay closed. No new defect. Pytest was not run. Nothing in `post/` was deleted; `providers.py` `_postiz_poster` / `_zernio_poster` / `_dryrun_poster` are still the live lazy factories.

### Dry-run

`_post_provider` (`run.py:92`) returns `"dryrun"` when `not cfg.is_live`. `publish_due` (`run.py:483`) and `publish_post` (`run.py:525`) call `_handle_dryrun_boundary` and do not call `_publish_one`. `publish_dryrun.py` only calls `write_preview`. `dryrun.py` `write_preview` writes `<scheduled>/<id>.json` from `send_caption` (YouTube also writes the tag labels) and sets no state, id, or URL. `DryRunPoster.publish` only calls `write_preview`. `rg` for `requests.post` / `put` / `patch` in `dryrun.py` and `publish_dryrun.py` is empty. `get_poster` (`post/__init__.py:19`) raises `RuntimeError` when live and the backend is `dryrun`, and does not build `DryRunPoster`. `ensure_up` is not a content POST; `_backend_is_postiz` is false when not live unless `poster_backend` is already `postiz`.

### Idempotency

`_publish_one` claims only while state is `queued` (`run.py:241`) and refuses a real `submission_id` before the claim commits (`run.py:276`, `is_real_submission_id` excludes the birth `fanops_` token). `created_at` is not in `_NET_POST_FIELDS`. Zernio `_request_name` is still the JSON array `[ver, post.id, created_at, platform, account_id]`; `_create` sets `x-request-id` once and reuses that header on every in-deadline retry. A 409 returns `ReconciliationRequired` and `publish` writes `reconcile_candidate_id`, not `submission_id`, and does not set `failed`. Postiz still has no idempotency key: GET matching integration plus `content` before POST, and again after a lost response, then adopt; 5xx parks `needs_reconcile` and returns; 429 retries inside that loop. `crosspost.py:237` still pops `failed`/`rejected` and remints the same `post.id` with a fresh `created_at` (`crosspost.py:251`). `_JITTER_MAX` 30 `<` `_STEP_MIN` 40 with the module assert (`crosspost.py:27-29`).

### Caption wire and Zernio body

`send_caption` (`caption_compose.py:282`): YouTube text is `Post.caption` raw and tags are `_youtube_tags` labels; IG/TT is `_posted_ship`. `posted_text_for` (`caption_compose.py:300`) is that string. `PostizPoster.publish` (`postiz.py:540`) sets vendor `content` to `post.caption` for YouTube and `posted_text_for` otherwise, then `post.sent_content = content` before adopt or POST. `build_postiz_payload` puts `_youtube_tags(hashtags)` on YouTube `settings.tags`. `ZernioPoster._create` (`zernio.py:505`) passes `posted_text_for` as `content` and stamps `sent_content`. `build_zernio_payload` (`zernio.py:306-310`) sets `content` to that argument, `publishNow: True`, no `scheduledFor`; empty media raises `ValueError` matching `no media`; otherwise `mediaItems` is `[{"type": "video", "url": u}]`. `write_preview` stores `send_caption` text, so the preview matches the live YouTube description and the IG/TT `posted_text_for` string. `Post.caption` stays the stored sentence. `_NET_POST_FIELDS` still includes `sent_content`, `publish_hour`, and `publish_dow`.

### Evidence commands

```
git rev-parse HEAD
git status --short --branch
git diff --stat -- src/fanops/post/ src/fanops/crosspost.py src/fanops/reconcile.py src/fanops/caption_compose.py
git diff -U3 -- src/fanops/post/run.py src/fanops/post/postiz.py src/fanops/post/zernio.py src/fanops/post/dryrun.py src/fanops/post/publish_archive.py src/fanops/caption_compose.py src/fanops/reconcile.py
rg -n 'requests\.(post|put|patch)|httpx|urlopen' src/fanops/post/dryrun.py src/fanops/post/publish_dryrun.py
rg -n 'def _post_provider|return "dryrun"|provider == "dryrun"|_handle_dryrun_boundary|_publish_one' src/fanops/post/run.py
rg -n 'is_live|DryRunPoster|def get_poster' src/fanops/post/__init__.py
rg -n 'posted_text_for|send_caption|sent_content|post\.caption if' src/fanops/post/postiz.py src/fanops/post/zernio.py src/fanops/post/dryrun.py src/fanops/caption_compose.py
rg -n 'def build_zernio_payload|mediaItems|no media|publishNow|x-request-id|status_code == 409|def _request_name|def _request_id' src/fanops/post/zernio.py
rg -n 'skip_resubmit_existing_id|is_real_submission_id|PostState.queued' src/fanops/post/run.py
rg -n '_JITTER_MAX|_STEP_MIN|^assert ' src/fanops/crosspost.py
rg -n 'def _postiz_poster|def _zernio_poster|def _dryrun_poster' src/fanops/post/providers.py
rg -n 'aliased imports|providers.py' src/fanops/CLAUDE.md
```

Verdict: dry-run writes a preview and does not POST; a retried publish reuses the Zernio request id inside the deadline and will not claim a row that is no longer `queued` or that already has a real submission id; IG/TT and Zernio `content` are `posted_text_for`, and Postiz YouTube `content` is `Post.caption`.
