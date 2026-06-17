<!-- Generated: 2026-06-18 | Files scanned: models.py, ledger.py, config.py, accounts.py, ingest.py, router.py, stitch_render.py, impact_cut.py, intro_match.py, compose.py, cutover.py | Token estimate: ~985 | incl. M6 intro-tease -->
# FanOps Data

No database. ONE JSON ledger + operator-editable control files, all under the data tree.

## Data tree (config.py — `<root>/MohFlow-FanOps/`)

```
00_control/   ledger.json ledger.lock accounts.json context.md tuning.json ledger_digest.md cutover.json
00_review/    manifest.json intaken.json *.jpg + approved/   (discover/intake staging)
01_inbox/     dropped/pulled media awaiting ingest (native — the pipeline cuts these)
01_thirdparty_inbox/   M1: PEER of 01_inbox (outside the native rglob) — handed-in third-party assets (video/photo), catalogued as origin_kind=third_party, INERT to clip-production
02_sources/   content-addressed source copies (src_<sha>.mp4; both native + third-party land here)
03_clips/     rendered clips + per-account variant renders + composed clips (Studio serves ONLY inside this tree)
04_agent_io/  agentstep request/response JSONs (moments/captions)
05_scheduled/ 06_published/  (reserved)
07_reports/   run.log (TAB columns: ts\tstage\tunit\toutcome\textra)
.env          (env vars: FANOPS_POSTER, POSTIZ_URL, POSTIZ_API_KEY, FANOPS_RESPONDER, etc.)
```

## Ledger (ledger.py — single state store)

- Concurrency: `fcntl.flock` on ledger.lock (self-heals orphans), 30s bounded wait -> typed
  LockBusyError. `Ledger.transaction()` holds the lock across load→mutate→save.
- Writes: tmp file + `os.replace` (atomic). Reads in Studio are lock-free (atomic replace
  guarantees a complete file). Malformed JSON -> typed ControlFileError (clean exit 2).
- Doc shape: 4 unit maps keyed by content-addressed id + `variant_streaks` + `tag_log` + `stitch_plans`
  (M3 structural-hooks). Versioned: `SCHEMA_VERSION=2` + `_MIGRATIONS` (ledger.py; v1→v2 injects the
  empty `stitch_plans` map — old ledgers load clean); a NEWER on-disk version → `_NewerSchema` refuses to
  load (exit 2) rather than silently drop fields. New OPTIONAL entity fields (Moment.{hook_strategy,
  intro_matches}, StitchPlan.*) ride pydantic defaults — no migration. Inner dicts of variant_streaks/tag_log remain
  untyped (known gap).

## Units & lifecycles (models.py, pydantic)

```
Source: catalogued -> transcribed -> signalled -> moments_requested -> moments_decided | error
        | retired (M1 retire_source: cascade-drop descendants, file KEPT on disk) | discovered (M1 rebuild_catalog orphan — inert until confirmed)
Moment: decided -> clipped | retired | error    (M2: router stamps .hook_strategy on a `decided` moment, renders nothing;
        M6: .intro_matches holds the LLM-vision matcher's ranked intro pairings for an intro_tease-reserved moment)
Clip:   rendered -> captions_requested -> captioned -> queued -> published -> analyzed
        | held | retired | error
        | stitch_draft (M3/M4: a stitched clip BORN here — absent from crosspost's `captioned` select AND
          _REUSABLE_CLIP_STATES, so STRUCTURALLY unpostable; only an operator RELEASE reaches `captioned`)
Post:   queued -> submitting -> submitted -> published -> analyzed
        | failed (definitely-not-posted, re-queueable) | needs_reconcile (MAY be live — poll,
        never blind re-POST) | retired (M4: a queued base post superseded by an approved stitch) | error
StitchPlan (M3 structural-hooks): suggested -> approved -> in_use | dismissed | error
        (suggested=an impact-cut/intro-tease idea; approved gates the lock-free render; in_use=rendered into a
        stitch_draft clip; dismissed/error terminal — e.g. "base superseded" on fingerprint drift, or M6
        "intro compose failed after N attempts" once render_attempts hits MAX_INTRO_RENDER_ATTEMPTS)
IntroMatchDecision (M6 agent-step, intro_match.py): ranked IntroMatchItem pairings {moment_id, asset_id,
        fit_score, rationale, tease_text} from the LLM-vision matcher; ephemeral gate, SEPARATE from the durable stitch_plan id
```

Key fields: parent_id lineage Post→Clip→Moment→Source; `Source.origin_kind` (M1: native|third_party;
write-once via add_source setdefault — the axis that gates clip-production, third_party is inert);
`Post.submission_id` (content-addressed
client idempotency token, stamped at birth); `Post.media_urls` ([] -> uploaded at publish;
`file://` variant renders uploaded on live backends); `Post.metrics[LIFT_SCORE]` (models.py
constant — written only by track.record_metrics); `Clip.media_url` (per-clip upload cache);
`Source.meta.amplify_count` (E1 budget vs MAX_AMPLIFY_PER_SOURCE=3); `variant_streaks[key] =
{hook, fingerprint, streak}` (untyped dict — known gap). `Moment.hook_strategy` (M2: optional router
reason — `text`/`clean_final`/`clean_awaiting_strategy:<key>`/`stitch:<format>`; None on old ledgers);
`StitchPlan` (M3: `id` content-addressed via `stitch_plan_id(clip_id, sorted asset_ids, strategy_key,
plan_params)` — the durable dedup key, NOT the render fingerprint; `clip_id` base, `strategy_key`,
`plan_params` {cut_start,cut_end} for impact-cut, `base_fingerprint` PINNED at suggest so a re-rendered
base auto-dismisses the plan, `state`, `error_reason`; M5 adds `rank_score` (fit the routine loop ranks
by) + `rationale` (operator-facing WHY) — both optional, ride defaults).

## Control files (operator-editable; malformed -> ControlFileError, exit 2)

- **accounts.json:** handle/account_id/platforms/status/persona per account; validate() pre-run.
  `account_id` is numeric for Blotato or a UUID for Postiz integrations (same field, different schema).
  Writable atomically via `write_account_id()` (ecc audit: python + security).
  
- **tuning.json** (OPTIONAL, fail-open): lift_weights override for track.lift_score.

- **context.md:** free-text guidance injected into moment requests.

- **cutover.json** (auto-written by `cutover` probe; not in the ledger): contains probe post state (cutover._probe_id, timestamp, etc.)
  for Blotato validation before going live — separate from the ledger so a stray probe never pollutes it.

## Cascade-safety invariant (C1)

`ledger._delete_moment_cascade` preserves LIVE descendants (`_LIVE_CLIP_STATES` /
`_LIVE_POST_STATES`); a re-decided source retires the old moment instead of deleting when a
live post/clip survives. Retired moments are never resurrected. M1 `retire_source` rides this same
cascade (reconcile with an EMPTY keep-set), so retiring a source preserves + retires any live
descendant rather than orphaning a live post; `rebuild_catalog` never resurrects a retired source.
