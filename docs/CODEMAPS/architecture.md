<!-- Generated: 2026-06-16 | Files scanned: 58 src + 70 test | Token estimate: ~1250 -->
# FanOps Architecture

Single-operator local CLI (`fanops`) that turns long-form source video into scheduled
cross-posted clips. Pure-Python src layout (`src/fanops/`), one JSON ledger as the only
state store, external heavy lifting via subprocesses (ffmpeg/whisper/yt-dlp), the
Blotato REST API, or Postiz (self-hosted). Autonomous learning features are default-OFF, fail-safe.
Optional Flask-based Studio web cockpit (imported lazily; core install Flask-free).
Optional MoviePy produced-clip compositing with template cards + overlays (imported lazily; core install MoviePy-free).

## Pipeline (the `advance` pass, pipeline.py — runs INSIDE one ledger flock)

```
01_inbox media ──ingest──> Source(catalogued)
  ──transcribe(whisper)──> transcribed ──signals(ffmpeg)──> signalled
  ──moments(agent req/resp via agentstep+llm)──> moments_decided -> Moment(decided)
  ──[hookedit: feed-aware, VISION-grounded RETENTION-hook editor; opt-in FANOPS_HOOK_EDITOR]──> Moment(hook_edited)
  ──clip(ffmpeg render per aspect; burns the on-screen RETENTION hook top-center)──> Clip(rendered)
  ──caption(agent + brand gate; hashtags VETTED to ≤4 from a reach-ranked set)──> captioned
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
| Decide/render | moments.py, clip.py (+fit_window/snap), overlay.py (hook/subtitle burn, build_ass), caption.py (brand gate + hashtag vet), prompts.py (moment/hookedit/caption, shared `_hook_spec`) |
| Hook + hashtag quality | hookedit.py (feed-aware vision hook editor, chunked gates), keyframes.py (source-frame extraction = the editor's eyes), hookcheck.py (deterministic weak-hook guard), hashtags.py (vet_hashtags ≤4 reach-vetted), text.py (em-dash sanitizer). Sourced knowledge: `.claude/skills/fanops-hook-hashtag/SKILL.md` |
| Compositing (optional [compose]) | compose.py (MoviePy produced clip layer w/ template cards, fail-open to base clip) |
| Agent I/O | agentstep.py (request/response files), llm.py (`claude -p`, 180s cap), responder.py |
| Schedule/post | crosspost.py (deterministic schedule), tagging.py, post/{run,media,payload,blotato_rest,blotato_mcp,postiz,dryrun,metrics}.py |
| Publishing | post/run.py (_submit_one, publish_due, publish_post — the Publish-now engine) |
| Learn (default OFF) | track.py (writes LIFT_SCORE), adjust.py (classify/amplify/retire), variant_learning.py (best_hooks/ucb_rank), variant_amplify.py, variant_transfer.py |
| State/infra | ledger.py (flock+atomic JSON), models.py (pydantic units + LIFT_SCORE), accounts.py (+ atomic write_account_id), ids.py (SHA1 content-addressing), timeutil.py (single parse site), log.py (TAB-column run.log), errors.py, digest.py (+public gate_state), validation_gate.py |
| Autonomous ops | autopilot.py (one-cmd: enable llm responder + launchd daemon), daemon.py (launchd supervisor around `run`), doctor.py (readiness pre-flight checks), cutover.py (Blotato: auth/post/metrics/lift prover) |
| Studio (optional [studio]) | studio/app.py (Flask factory, lazily imported), studio/views.py (read models), studio/actions.py (one transaction per mutation), studio/golive.py (Postiz connect/config surface) |

## CLI verbs (cli.py)

**Core pipeline:** `run` (cron entrypoint: respond+advance loop + learning passes) · `advance` · `status` ·
`ingest` · `pull <http(s) url>` · `discover` / `intake` · `respond` · `digest` · `track` ·
`adjust` · `amplify-variants` · `reconcile` · `gc`.

**Recovery:** `resolve` / `unhold` / `retry-source` / `retry-metrics`.

**Autonomous ops:** `autopilot` (enable llm responder + install daemon) · `daemon {install,status,stop}`
(launchd supervisor) · `doctor` (readiness pre-flight) · `cutover {auth,post,metrics,lift}` (Blotato verify).

**Publishing:** `compose` (optional [compose] extra; MoviePy produced-clip render outside the flock).

**Studio:** `studio` (Flask on 127.0.0.1:8787, debug=False; optional [studio] extra).

Typed-error catch ladder -> one clean stderr line + exit 1/2, never a traceback.

## Studio routes (studio/app.py)

```
GET  /                  -> redirect /review
GET  /review|/schedule|/lift|/run|/candidates|/publish|/gates|/golive   (lock-free Ledger.load per request)
GET  /media/<post_id>, /clips/<clip_id>, /review-thumb/<eid>   (send_file, bounded INSIDE cfg.base)

POST /run/{ingest,pull,advance,prepare}   (pipeline entry from browser; htmx returns _run_panel)
POST /publish/posted/<post_id>            (mark published manually)
POST /publish/now/<post_id>               (Milestone 5: ship one reviewed post immediately)
POST /reschedule/<post_id>, /caption/<post_id>   (edit existing post)
POST /regenerate/<post_id>                (Milestone 3: re-run caption model)
POST /snooze/<clip_id>                    (hold a clip from publishing)

POST /candidates/approve/<eid>            (approve discover footage for ingest)
POST /gates/answer/{moments,captions}/<key>   (answer agent gates from browser)

POST /golive/{config,refresh,map,live,dryrun}   (Milestone 5 operator-gated: Postiz integration)
```

All POST routes return ActionResult (ok + detail/error) wrapped in _result.html (htmx swap).
Gates/Run panels re-render on success with fresh status (lock-free Ledger.load).

## Output levers (what changes the produced clips/posts)

The control surface — every input that changes what the engine outputs:

| Lever | Where read | Changes |
|---|---|---|
| `context.md` (brand brief) | moments/caption `_guidance` | clip-pick + hook + caption voice (injected verbatim into every agent prompt) |
| `FANOPS_HOOK_EDITOR` | config → pipeline | on: feed-aware vision editor rewrites all hooks before render; off: per-clip hooks only |
| `prompts._hook_spec` | moment/hookedit/caption | the ONE retention-hook definition (open-loop/curiosity/comment-bait/POV, no hype) |
| `hashtags.py` vetted set + `vet_hashtags` | caption ingest | the ≤4 reach-vetted tags actually posted (model picks from the menu; code hard-caps) |
| `FANOPS_RESPONDER` (llm/manual) | pipeline/responder | who answers moment/caption/hookedit gates (llm = autonomous; manual = operator) |
| `FANOPS_CLIP_PROFILE` + bands.py | clip.fit_window | clip length band (talk 12-22s vs song 18-35s) + snap window |
| `burn_subs` | clip/overlay | transcript captions burned (default OFF; hook is NOT the transcript) |
| `tuning.json` offbrand_en/ar | caption brand gate | what HOLDS a caption as off-brand |
| `accounts.json` personas | caption per-surface | per-account voice/angle |
| `FANOPS_VARIANT_*` (learning/amplify/ucb/transfer) | caption bias + post-loop | hook A/B learning (default OFF, fail-safe) |
| `FANOPS_POSTER` | config → publish | dryrun (no-op) vs postiz/blotato (real posts) |

## Learning-gate seams (the C1-sensitive area)

caption.request_captions biases on `variant_learning.best_hooks` (or `ucb_rank` when UCB on);
`cli.run` executes TWO independent post-loop passes when `cfg.is_live_backend`:
classify→amplify/retire, then (own kill switch) `apply_variant_amplify`. Both share
`adjust.MAX_AMPLIFY_PER_SOURCE`. Isolation invariant: the amplify/cascade path never imports
the learner — enforced by AST tests in tests/test_variant_learning.py / test_variant_amplify.py.
