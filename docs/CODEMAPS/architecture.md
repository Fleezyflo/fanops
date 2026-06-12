<!-- Generated: 2026-06-13 | Files scanned: 45 src + 38 test | Token estimate: ~900 -->
# FanOps Architecture

Single-operator local CLI (`fanops`) that turns long-form source video into scheduled
cross-posted clips. Pure-Python src layout (`src/fanops/`), one JSON ledger as the only
state store, external heavy lifting via subprocesses (ffmpeg/whisper/yt-dlp) and the
Blotato REST API. Autonomous learning features are default-OFF, fail-safe.

## Pipeline (the `advance` pass, pipeline.py — runs INSIDE one ledger flock)

```
01_inbox media ──ingest──> Source(catalogued)
  ──transcribe(whisper)──> transcribed ──signals(ffmpeg)──> signalled
  ──moments(agent req/resp via agentstep+llm)──> moments_decided -> Moment(decided)
  ──clip(ffmpeg render per aspect)──> Clip(rendered)
  ──caption(agent + brand gate)──> captioned
  ──crosspost(schedule per account×platform surface)──> Post(queued)
  ──publish_due(post/run.py)──> submitting -> submitted -> published
  ──track(pull Blotato metrics)──> analyzed ──adjust──> amplify/retire
```

- Per-unit error quarantine: any stage failure parks THAT unit in `error` + reason; never wedges the pass.
- Crash-safe publish: `submitting` persisted BEFORE the network call; ambiguous results -> `needs_reconcile`, never blind re-POST (reconcile.py polls).
- Slow network ops that must NOT hold the flock run outside transactions: yt-dlp download (`pull`), `claude -p` (responder.py).

## Module map (src/fanops/)

| Area | Files |
|---|---|
| Orchestration | cli.py (verbs+catch ladder), pipeline.py (advance), config.py (env+paths) |
| Ingest/discover | ingest.py, discover.py (00_review intake), transcribe.py, signals.py |
| Decide/render | moments.py, clip.py, overlay.py (hook/subtitle burn), caption.py (gate), prompts.py |
| Agent I/O | agentstep.py (request/response files), llm.py (`claude -p`, 180s cap), responder.py |
| Schedule/post | crosspost.py (deterministic schedule), tagging.py, post/{run,media,payload,blotato_rest,blotato_mcp,dryrun,metrics}.py |
| Learn (default OFF) | track.py (writes LIFT_SCORE), adjust.py (classify/amplify/retire), variant_learning.py (best_hooks/ucb_rank), variant_amplify.py, variant_transfer.py |
| State/infra | ledger.py (flock+atomic JSON), models.py (pydantic units + LIFT_SCORE), accounts.py, ids.py (SHA1 content-addressing), timeutil.py (single parse site), log.py (TAB-column run.log), errors.py, digest.py (+public gate_state) |
| Studio (optional [studio]) | studio/app.py (Flask factory, lazily imported), studio/views.py (read models), studio/actions.py (one transaction per mutation) |

## CLI verbs (cli.py)

`run` (cron entrypoint: respond+advance loop + learning passes) · `advance` · `status` ·
`ingest` · `pull <http(s) url>` · `discover` / `intake` · `respond` · `digest` · `track` ·
`adjust` · `amplify-variants` · `reconcile` · `gc` · recovery: `resolve` / `unhold` /
`retry-source` / `retry-metrics` · `studio` (Flask on 127.0.0.1:8787, debug=False).
Typed-error catch ladder -> one clean stderr line + exit 1/2, never a traceback.

## Studio routes (studio/app.py)

```
GET  /            -> redirect /review
GET  /review|/schedule|/lift   (lock-free Ledger.load per request)
GET  /media/<post_id>, /clips/<clip_id>   (send_file, bounded INSIDE cfg.base)
POST /reschedule/<post_id>, /caption/<post_id>, /snooze/<clip_id>  (studio/actions, one txn each)
```

## Learning-gate seams (the C1-sensitive area)

caption.request_captions biases on `variant_learning.best_hooks` (or `ucb_rank` when UCB on);
`cli.run` executes TWO independent post-loop passes when `cfg.is_live_backend`:
classify→amplify/retire, then (own kill switch) `apply_variant_amplify`. Both share
`adjust.MAX_AMPLIFY_PER_SOURCE`. Isolation invariant: the amplify/cascade path never imports
the learner — enforced by AST tests in tests/test_variant_learning.py / test_variant_amplify.py.
