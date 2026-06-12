<!-- Generated: 2026-06-13 | Files scanned: models.py, ledger.py, config.py, accounts.py | Token estimate: ~700 -->
# FanOps Data

No database. ONE JSON ledger + operator-editable control files, all under the data tree.

## Data tree (config.py — `<root>/MohFlow-FanOps/`)

```
00_control/   ledger.json ledger.lock accounts.json context.md tuning.json ledger_digest.md
00_review/    manifest.json intaken.json *.jpg + approved/   (discover/intake staging)
01_inbox/     dropped/pulled media awaiting ingest
02_sources/   content-addressed source copies (src_<sha>.mp4)
03_clips/     rendered clips + per-account variant renders (Studio serves ONLY inside this tree)
04_agent_io/  agentstep request/response JSONs (moments/captions)
05_scheduled/ 06_published/  (reserved)
07_reports/   run.log (TAB columns: ts\tstage\tunit\toutcome\textra)
```

## Ledger (ledger.py — single state store)

- Concurrency: `fcntl.flock` on ledger.lock (self-heals orphans), 30s bounded wait -> typed
  LockBusyError. `Ledger.transaction()` holds the lock across load→mutate→save.
- Writes: tmp file + `os.replace` (atomic). Reads in Studio are lock-free (atomic replace
  guarantees a complete file). Malformed JSON -> typed ControlFileError (clean exit 2).
- Doc shape: 4 unit maps keyed by content-addressed id + `variant_streaks` + `tag_log`.
  No schema_version (known gap — pydantic `extra="ignore"` drops unknown fields on old-binary load).

## Units & lifecycles (models.py, pydantic)

```
Source: catalogued -> transcribed -> signalled -> moments_requested -> moments_decided | error
Moment: decided -> clipped | retired | error
Clip:   rendered -> captions_requested -> captioned -> queued -> published -> analyzed
        | held | retired | error
Post:   queued -> submitting -> submitted -> published -> analyzed
        | failed (definitely-not-posted, re-queueable) | needs_reconcile (MAY be live — poll,
        never blind re-POST) | error
```

Key fields: parent_id lineage Post→Clip→Moment→Source; `Post.submission_id` (content-addressed
client idempotency token, stamped at birth); `Post.media_urls` ([] -> uploaded at publish;
`file://` variant renders uploaded on live backends); `Post.metrics[LIFT_SCORE]` (models.py
constant — written only by track.record_metrics); `Clip.media_url` (per-clip upload cache);
`Source.meta.amplify_count` (E1 budget vs MAX_AMPLIFY_PER_SOURCE=3); `variant_streaks[key] =
{hook, fingerprint, streak}` (untyped dict — known gap).

## Control files (operator-editable; malformed -> ControlFileError, exit 2)

- accounts.json: handle/account_id/platforms/status/persona per account; validate() pre-run.
- tuning.json (OPTIONAL, fail-open): lift_weights override for track.lift_score.
- context.md: free-text guidance injected into moment requests.

## Cascade-safety invariant (C1)

`ledger._delete_moment_cascade` preserves LIVE descendants (`_LIVE_CLIP_STATES` /
`_LIVE_POST_STATES`); a re-decided source retires the old moment instead of deleting when a
live post/clip survives. Retired moments are never resurrected.
