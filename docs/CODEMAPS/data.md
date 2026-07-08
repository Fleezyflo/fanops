<!-- Generated: 2026-07-08 | Files scanned: models.py, ledger.py, config.py, accounts.py, ingest.py, pipeline.py, crosspost.py, casting.py, post/run.py, studio/views.py | Token estimate: ~1080 | SCHEMA_VERSION=11 (v11 drops selection maps); born-awaiting_approval; post-P11 single-owner affinity routing -->
# FanOps Data

No database. ONE JSON ledger + operator-editable control files, all under the data tree.

## Data tree (config.py — `<root>/MohFlow-FanOps/`)

```
00_control/   ledger.json ledger.lock accounts.json accounts.lock personas.json personas.lock context.md tuning.json ledger_digest.md cutover.json
00_review/    manifest.json intaken.json *.jpg + approved/   (discover/intake staging)
01_inbox/     dropped/pulled media awaiting ingest (native — the pipeline cuts these)
01_thirdparty_inbox/   M1: PEER of 01_inbox (outside the native rglob) — handed-in third-party assets (video/photo), catalogued as origin_kind=third_party, INERT to clip-production
02_sources/   content-addressed source copies (src_<sha>.mp4; both native + third-party land here)
03_clips/     rendered clips + per-account variant renders + composed clips (Studio serves ONLY inside this tree)
04_agent_io/  agentstep request/response JSONs (moments/captions)
05_scheduled/ dryrun poster payloads (<post_id>.json, written by post/dryrun.py; swept by `gc` older than FANOPS_GC_KEEP_DAYS)
06_published/ content-lifecycle: day-bucketed <YYYY-MM-DD>/<post_id>.json record of every shipped post (fail-open archive via post/run._archive_published; `gc` NEVER touches it). WRITE-ONLY BY DESIGN (#5 resolved): a human-browsable on-disk audit trail — the Studio Posted tab reads the LEDGER, not these files. Having no in-app consumer is deliberate, not a gap; surfacing it in the UI is a future product call, not a wiring bug.
07_reports/   run.log (TAB columns: ts\tstage\tunit\toutcome\textra)
.env          (env vars: FANOPS_POSTER, POSTIZ_URL, POSTIZ_API_KEY, FANOPS_RESPONDER, etc.)
```

## Ledger (ledger.py — single state store)

- Concurrency: `fcntl.flock` on ledger.lock (self-heals orphans), 30s bounded wait -> typed
  LockBusyError. `Ledger.transaction()` holds the lock across load→mutate→save.
- Writes: tmp file + `os.replace` (atomic). Reads in Studio are lock-free (atomic replace
  guarantees a complete file). Malformed JSON -> typed ControlFileError (clean exit 2).
- Doc shape: 4 unit maps keyed by content-addressed id + `variant_streaks` + `tag_log` + `stitch_plans`
  (M3 structural-hooks) + `batches` (Account-First: named, account-targeted ingest groups) + `renders`
  (per-account Render foundation: the per-account shippable artifacts). Versioned:
  `SCHEMA_VERSION=11` + `_MIGRATIONS` hop-chain (ledger.py; v1→v2 injects the empty `stitch_plans` map;
  v2→v3 `_migrate_v3_created_at` backfills `created_at`; v3→v4 `_migrate_v4_metrics_series`; v4→v5 injects
  `batches`; v5→v6 injects `renders`; v6→v7 injects `selection_facts` (transient — dropped at v11);
  v7→v8 additive step; v8→v9 `_migrate_v8_account_selections` (transient `account_selections` lift);
  v10→v11 `_migrate_v11_drop_selection_maps` **drops** `account_selections` + `selection_facts` (P12/MOL-154);
  all idempotent, never raise). A NEWER on-disk version → `_NewerSchema` refuses to load (exit 2). New OPTIONAL
  entity fields (Moment.{hook_strategy, intro_matches, affinities}, StitchPlan.*, Source.{created_at, batch_id},
  Post.{created_at, published_at, batch_id, top_bias, publish_hour, publish_dow}, Batch.*, Render.*) ride pydantic
  defaults. (`Post.variant_hook` / `SelectionFact` removed — hook truth is `Moment.hook` + `Render.hook_text`;
  crosspost gate is `Moment.affinities` + `casting.affinity_admits`.) Inner dicts of variant_streaks/tag_log
  remain untyped (known gap).

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
Post:   awaiting_approval (BORN here at crosspost — the human approval gate; publish_due/publish_now iterate
        ONLY `queued`, so NOTHING ships until an operator approves) -> queued (approved + scheduled) ->
        submitting -> submitted -> published -> analyzed
        | rejected (operator discard of an awaiting_approval post — terminal) | failed (definitely-not-posted,
        re-queueable) | needs_reconcile (MAY be live — poll, never blind re-POST)
        | retired (M4 stitch supersede / cross-account base) | error
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
client idempotency token, stamped at birth); `Source.created_at`/`Post.created_at` (content-lifecycle: ISO-8601
UTC birth/ingest day, stamped at catalogue/crosspost — the day-bucket anchor for Review/Posted); `Post.published_at`
(content-lifecycle: TRUE publish time, stamped at the submitted→published transition in post/run._submit_one — the
Posted-archive day anchor; absent until shipped); `Post.media_urls` ([] -> uploaded at publish;
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
  `account_id` is a Postiz integration id (or a legacy numeric); the per-platform `integrations` map keys a
  handle's IG vs TikTok to their own ids (a handle's channels are different integrations).
  Writable atomically via `write_account_id()` (ecc audit: python + security). Guarded by `accounts.lock`.

- **personas.json:** first-class `Persona` records (`models`/`personas.py`) — `voice`/`tag_lean`/`hashtag_corpus`
  per persona; `Account.persona_id` links one and its voice/lean/corpus HYDRATE the account at load (fail-open,
  byte-identical when unlinked). Edited in the Studio Personas tab; mutated under `personas.lock` (reuses the ledger flock shape).

- **tuning.json** (OPTIONAL, fail-open): lift_weights override for track.lift_score.

- **context.md:** free-text guidance injected into moment requests.

- **cutover.json** (auto-written by `cutover` probe; not in the ledger): contains probe post state (cutover._probe_id, timestamp, etc.)
  for Postiz learning validation before going live — separate from the ledger so a stray probe never pollutes it.
  (Learning also auto-validates on the first real non-degraded live metric, so this probe is an optional early shortcut.)

## Cascade-safety invariant (C1)

`ledger._delete_moment_cascade` preserves descendants in `_PROTECTED_POST_STATES`
(`_LIVE_POST_STATES` + awaiting_approval + queued + retired — content-lifecycle wipe-safety: a
re-ingest/reconcile can NEVER drop an awaiting-approval or approved post the operator is mid-review on)
and `_LIVE_CLIP_STATES`; the guard fires at BOTH the post-loop check and the clip-drop `any(...)`. A
re-decided source retires the old moment instead of deleting when a protected post/clip survives. Retired
moments are never resurrected. M1 `retire_source` rides this same cascade (reconcile with an EMPTY keep-set),
so retiring a source preserves + retires any protected descendant rather than orphaning it; `rebuild_catalog`
never resurrects a retired source.
