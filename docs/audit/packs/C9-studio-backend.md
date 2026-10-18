# Pack: C9 — studio backend

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Entry symbols re-read 2026-10-18: `actions_wipe.confirm_wipe`, `actions_approve._approve_ids_with_render`, `actions_edit.regenerate_hook`. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C9-F01 | medium | guard-gap | closed on this tree | `confirm_wipe` refuses a missing or stale `preview_token`. `do_wipe_confirm` passes the token | `actions_wipe.confirm_wipe` |
| C9-F02 | medium | guard-gap | closed on this tree | `_approve_ids_with_render` skips a post that is not `awaiting_approval` before the count and the account respread. A repeated approve does not rewrite queued times | `actions_approve._approve_ids_with_render` |
| C9-F03 | medium | guard-gap | closed on origin/main | `regenerate_hook` reads the clock again after the model call and passes that clock into `reburn_hook`. A queued post that becomes imminent during the call is refused before `render_moment` | `actions_edit.regenerate_hook` calls `_actions._now(None)` and `_guard_editable_post` and returns before `reburn_hook` |

## Evidence commands run

```
git rev-parse HEAD
git grep -n 'def confirm_wipe\|preview_token\|wipe_refused_no_preview\|wipe_refused_stale' HEAD -- src/fanops/studio/actions_wipe.py src/fanops/studio/app_routes_live.py
git grep -n 'def approve_post' HEAD -- src/fanops/ledger.py
rg -n 'not PostState.awaiting_approval' src/fanops/studio/actions_approve.py
rg -n '@app\.(post|put)' src/fanops/studio/app.py src/fanops/studio/app_routes_*.py
```

Subject `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e`. Product code was read with `git show HEAD:path`. Unrelated dirty studio files were not reverted. No pytest. No new wipe. No symbol deleted (`src/fanops/CLAUDE.md` alias rule was not triggered).

## Full checklist 2026-10-17

Module verdict: the studio mutation surface is guarded for a loopback operator. C9-F01 stays closed. C9-F02 was a real write on a repeated approve and is closed on this tree. CSRF stays the recorded localhost assumption.

### Mutation routes

| Action | Route | Owner |
|---|---|---|
| Approve | `POST /posts/approve`, `/posts/approve-with-edits/<id>`, `/posts/approve-with-hook/<clip>`, `/posts/approve-as-is/<clip>`, `/posts/approve-batch/<batch>`, `/posts/approve-clip/<clip>`, `/posts/approve-account`, `/posts/approve-moment/<id>`, `/posts/approve-channel`, `/posts/reject`, `/posts/unapprove/<id>`, `/stitches/approve` | `actions_approve` via `app_routes_review.py` and `app.py` |
| Edit | `POST /caption/<id>`, `/regenerate/<id>`, `/swap-tag/<id>`, `/reburn-hook/<id>`, `/regenerate-hook/<id>`, `/restore-persona-hook/<id>` | `actions_edit.edit_caption`, `regenerate_caption`, `swap_shipped_tag`, `reburn_hook`, `regenerate_hook`; `actions.restore_persona_hook` |
| Schedule | `POST /schedule/shift/<handle>`, `/schedule/respread`, `/schedule/move/<id>`, `/schedule/clear/<id>`, `/schedule/unapprove/<id>`, `/schedule/accept-suggested/<handle>`, `/schedule/randomize/<handle>`, `/schedule/publish/<id>`, `/schedule/publish-due` | `actions_schedule` via `app_routes_schedule.py`. Review also posts `/reschedule/<id>` and `/clear/<id>` |
| Wipe | `POST /live-library/wipe/preview`, `POST /live-library/wipe/confirm` | `actions_wipe.preview_wipe`, `confirm_wipe`. `do_wipe_confirm` passes `preview_token` (`app_routes_live.py`) |
| Recover | `POST /posts/recover`, `/posts/resolve/<id>`, `/home/retry-rate-limit`, `/home/retry-oversize` | `actions_recover.recover_posts`, `resolve_post`, `retry_rate_limited_failures`, `retry_oversize_failures` |

`retry_transient_failures` has no route. It is re-exported from `actions.py` and called by tests. Not deleted.

### Server-side checks

Promotion goes through `Ledger.approve_post`, which returns without writing unless the post is `awaiting_approval` (`ledger.py` on this HEAD). The wrapper now applies that same state check before `can_promote`, the duration cap, the count, and the respread. Retired awaiting posts still increment `skipped_retired` and log `skipped_retired_lineage`. A selection larger than 15 requires `batch_confirm` on `POST /posts/approve`.

Edits use `_guard_editable_post`: awaiting, or queued and not imminent. `edit_caption` and `regenerate_caption` both run `brand_risk_flag`. Regenerate re-checks the guard inside a short transaction after the model call. `regenerate_hook` reads the clock again after the model call and passes that clock into `reburn_hook`, so a queued post that has become imminent is refused before `render_moment`. `swap_shipped_tag` uses the same editable guard and writes only the `ship_from_lock` result. `reschedule_post` refuses a time that is not strictly in the future. `clear_time` unapproves a queued post before clearing `scheduled_time`.

`publish_now` and `publish_due_bucket` refuse unless the post is queued, and when `cfg.is_live` they require the confirm flag. Both routes pass `confirmed` from the form. `go_live` requires valid accounts, at least one live-ready channel, no drainable past-due queued post, and `confirm=1`. `set_flag` refuses a key that is not in `OPERATOR_FLAGS`, which is how `FANOPS_LIVE` stays out of the generic toggle.

Wipe: typed word `REMOVE`, then a fresh preview token, then a snapshot, then `snapshot_is_restorable`, then `execute_wipe(confirmed=True, plan_ceiling=...)`. `execute_wipe` intersects the live plan with that ceiling, so a row that appears after the ceiling is kept (`test_execute_wipe_plan_ceiling_preserves_post_previewed_set`). Recover re-arms only failed or error posts, and `_refuse_retired` runs before shrink or `_rearm_to_queued`.

Media reads go through `_bounded` (`app_request.py`): the path must resolve inside `cfg.base`. Uploads use `secure_filename`, reject `..`, and require the destination to stay in the inbox (`actions_run._resolve_upload_dest`).

### Localhost CSRF / same-origin

There is no session and no CSRF token. `src/fanops/studio/CLAUDE.md` and `docs/ENGINEERING_STANDARDS.md` record that as the localhost decision. The install path refuses a non-loopback bind (`daemon_studio._studio_bind_host_refused`; hosts `127.0.0.1`, `localhost`, `::1`). That assumption holds only while Studio stays on loopback. Not a new finding.

### Idempotency of a repeated approve

On HEAD, `approve_post` did not double-promote, but `_approve_ids_with_render` still counted the id and called `_apply_schedule_for_account`. A second approve three hours later rewrote `p1` from `2026-01-01T00:26:00Z` to `2026-01-01T03:26:00Z` and moved an already-queued sibling the same way. Repro: `.venv/bin/python` against `approve_posts` with `now` then `now + 3h` (not pytest). After the guard, the second call reports `approved == 0` and both stamps stay put. The first approve still respreads that account (MOL-869). `reject_posts` already counted only awaiting ids. A second wipe confirm with the old token is refused as stale; this pass did not add a wipe.

### Data writes

Ledger mutations in these modules go through `Ledger.transaction`. Approve, reject, reschedule-bucket, recover, resolve, and publish append `write_audit` when they actually move rows. Go-Live env writes go through `golive._dual_write` (`.env` or the keyring, then `os.environ`). Wipe writes a snapshot, then `execute_wipe`. Uploads write an inbox temp and `os.replace`.

### Env

`go_live` is the only setter of `FANOPS_LIVE=1`. `go_dryrun` writes `FANOPS_LIVE=0` with no confirm. `go_live` unsets a stale `FANOPS_POSTER=dryrun` and does not write `FANOPS_POSTER`. `POSTIZ_API_KEY` and `ZERNIO_API_KEY` are write-only through `_dual_write`; the Postiz result exposes `key_set`, not the key. `create_app` sets `MAX_CONTENT_LENGTH` from `cfg.upload_max_bytes` and reads `FANOPS_STUDIO_GENERATION` for the fingerprint. Operator toggles are the `set_flag` registry.

## Wave 1 evidence (dirty tree)

HEAD `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e`. Owned dirty files were read from the working tree, not `git show HEAD`. No pytest. No commit. No symbol deleted (`src/fanops/CLAUDE.md` alias rule was not triggered).

```
git rev-parse HEAD
git diff --stat -- src/fanops/studio/actions.py src/fanops/studio/actions_approve.py src/fanops/studio/actions_edit.py src/fanops/studio/actions_wipe.py src/fanops/studio/app_routes_review.py src/fanops/studio/app_routes_schedule.py
rg -n 'not PostState.awaiting_approval' src/fanops/studio/actions_approve.py
rg -n 'def confirm_wipe|preview_token|wipe_refused_no_preview|wipe_refused_stale' src/fanops/studio/actions_wipe.py src/fanops/studio/app_routes_live.py
rg -n 'def regenerate_hook|def swap_shipped_tag|def edit_caption' src/fanops/studio/actions_edit.py
.venv/bin/python  # inline probe, not pytest
```

Probe, before the C9-F03 fix: a queued post at `2026-06-06T12:06:00Z`, entry clock `2026-06-06T12:00Z`, model call advances the clock 10 minutes. `regenerate_hook` returned the imminent error, `Moment.hook` stayed `OLD HOOK`, and `render_moment` ran once.

Probe, after the fix: the same setup returns the imminent error, `render_moment` is not called, hook and `scheduled_time` stay put. An awaiting post with a 2099 schedule still burns (`render_moment` once, hook `the pocket stays`).

Repeated approve at a later clock, sibling `q1` already queued: `approve_posts`, `approve_clip`, `approve_account`, `approve_moment`, `approve_batch`, and `approve_with_hook` each report `approved == 0` and leave both stamps unchanged. `approve_with_edits` on the same clock returns `post p1 is queued; only awaiting posts can be approved`, does not apply a new caption, and leaves both stamps unchanged.

Wipe: empty token logs `wipe_refused_no_preview` and keeps `pd`. A matching token removes `pd` and keeps `pk`. The same token on a second confirm logs `wipe_refused_stale_preview` and keeps `pk`. `do_wipe_confirm` still passes `preview_token`. Schedule `−1 day` can move a queued time into the past; that is the existing negative-shift control, not a new ID.
