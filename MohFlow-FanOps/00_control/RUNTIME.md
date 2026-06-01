# RUNTIME.md — operating the Moh Flow Fan-Ops pipeline

How to run the system day to day, how the unattended loop behaves, the two model
seams (LLM responder, MCP poster), the human-only steps, the integration checkpoints
to confirm before a live run, and the backlog of deferred work.

The pipeline is a stage DAG (`src/fanops/pipeline.py::advance`). `advance` runs the
deterministic chain as far as it can and **pauses at each agent gate** — the moment
decision and the caption set. Those gates are answered either by a human writing the
response files, or by the LLM responder. Everything else (ingest, transcribe, signals,
render, crosspost, publish) is automatic.

CLI surface (all commands):

```
fanops status
fanops ingest
fanops pull <url>
fanops advance [--base-time T]
fanops respond
fanops track  [--window 30d]
fanops adjust [--winner-pct 0.3] [--retire-pct 0.2] [--lift-floor 20.0]
fanops gc     [--keep-days 30]
fanops digest
fanops run    [--base-time T]
```

Environment variables (read at runtime from `.env`, see `src/fanops/config.py`):

| Var | Values | Effect |
|---|---|---|
| `FANOPS_POSTER` | `dryrun` (default) \| `rest` \| `mcp` | Which publish backend. `dryrun` writes `file://` media URLs and never hits the network. |
| `FANOPS_RESPONDER` | `manual` (default) \| `llm` | `manual` = a human/cron writes the response files; `llm` = the LLM responder answers gates. |
| `BLOTATO_API_KEY` | string | Required for `rest`, `mcp`, and `track`. Absent ⇒ those refuse/skip cleanly. |
| `FANOPS_ESCALATION_BUDGET_USD` | float (optional) | Spend cap knob. |

---

## Daily loop (manual responder)

This is the default, human-in-the-loop cadence (`FANOPS_RESPONDER=manual`).

1. **`fanops advance`** — ingests any drops, transcribes, detects signals, and opens the
   pending **moment** requests. It pauses there; `status`/`digest` show
   `awaiting_moments`.
2. **Answer the pending requests** in `04_agent_io/requests/` by writing the matching
   `*.response.json` files. A moment response is a `MomentDecision`; a caption response
   is a `CaptionSet`. (Each request carries a `request_id` you echo back so the response
   correlates — see `agentstep.py`.) The creative instructions you are answering *to*
   live in `context.md` and are injected into each request as `guidance`.
   - Or, with `FANOPS_RESPONDER=llm` and `claude` on `PATH` (authenticated), run
     **`fanops respond`** to answer them automatically (the autonomous LLM responder).
3. **`fanops advance`** again — ingests the moment decisions, renders the per-aspect
   clips, and opens the **caption** requests. Answer those the same way (or `respond`),
   then `advance` once more to crosspost and publish what is due.
4. **On a cadence (e.g. daily/weekly): `fanops track` then `fanops adjust`** — pull
   performance and amplify winners / retire losers (see *The feedback loop* below).
5. **`fanops gc` weekly** — reclaims disk by deleting the `.mp4` files of
   retired/analyzed clips older than `--keep-days` (default 30). The ledger record and
   the post's cached media URL persist; only the dead local file is removed. Transcripts
   are tiny and left in place.

`fanops digest` rewrites `00_control/ledger_digest.md` (counts, holds, failures,
pending) on demand; most commands also refresh it.

---

## Unattended loop

```
fanops run [--base-time T]
```

`run` drives the gates with the configured responder and advances until the pipeline is
**stable** (no pending moments **and** no pending captions), up to **10 iterations**. It
only makes progress if the responder actually answers gates — so for unattended
operation set `FANOPS_RESPONDER=llm` with `claude` on `PATH` and authenticated (the
autonomous LLM responder — see below); with the default `manual` responder there is
nothing to drain and it will stop after one pass.

**Graceful degradation on a fatal auth error.** If a fatal Blotato auth error escapes
the publish step (bad or missing `BLOTATO_API_KEY`, or a `401`), `run` does **not** crash
with a traceback. It prints `run halted: <Error>: <message>` to **stderr** and exits with
**code 1**. This is deliberate: a bad key would otherwise fail every post in turn, so the
run **halts rather than burning the queue** — fix the key and re-run. (A *per-post*
failure such as a media-upload 5xx does not halt; that post is marked `failed`,
retryable, and the run continues.)

**Scheduling** is out of repo scope — the repo ships no cron/launchd entries. Schedule
`run` externally. Example crontab entry (every 30 minutes):

```cron
*/30 * * * * cd /path/to/repo && ./.venv/bin/fanops run >> run.out 2>&1
```

The `cd /path/to/repo` is **mandatory, not cosmetic**: `fanops` resolves its data dirs
(ledger, lock, accounts) from the current working directory — there is no `FANOPS_ROOT`
override — so invoking it from the wrong cwd silently reads/writes the wrong ledger.

**Overlapping runs are safe.** Each `advance()` pass — and **every standalone write command**
(`track`, `reconcile`, `adjust`, `ingest`, `pull`) — runs inside **one `Ledger.transaction`** that
holds the ledger `fcntl.flock` across the **entire load → mutate → save**, not just the final write.
Acquiring the lock *before* the load closes the lost-update window the old save()-only lock left
open (two overlapping writers both loaded a stale snapshot, last save() won, the other's updates —
a published post, a `submitting` flip — vanished silently, audit B4). **Slow I/O stays OUTSIDE the
lock:** the up-to-30s Blotato calls in `track` (metrics fetch) and `reconcile` (per-post status
polls), and the `yt-dlp` download in `pull`, all run *before* the transaction; only the in-memory
apply runs under the flock — so a slow network call never serializes behind the ledger lock
(mirrors how `publish_due` uses the unlocked save mid-loop). So you can safely run `fanops adjust`
or `fanops track` while cron's `run` is mid-pass: the second writer waits briefly, then either
proceeds or skips with a clean `LockBusyError` — never a clobber. The lock is an `fcntl.flock` (not a delete-able sentinel): if a run is killed
mid-pass, the kernel releases it on process death, so the next invocation acquires it
immediately — **no orphaned lock can wedge the loop** (audit H6). If a *previous* `run`
genuinely overruns the interval and is still inside its pass when the next fires, the new
process waits briefly, then exits 1 with a one-line `ledger lock busy …` message (a typed
`LockBusyError`, no traceback) and the following tick retries — so a slow run never corrupts
state, never loses an update, and never crash-dumps; it just skips a beat. (The slow
`claude -p` responder call runs *outside* this lock — the agent-gate files are correlated by
`request_id`, with a capture-and-recheck guard against a mid-call re-seed, audit A3 — so the
autonomous brain is never serialized behind the ledger lock.)

On macOS a launchd `StartInterval` agent is the equivalent. Note that creating those
scheduled jobs (CronCreate / system scheduled-tasks) is an environment concern, **not**
something this repo manages.

---

## Scheduling vs publishing — IMPORTANT (deviation from the original plan)

The original plan implied `--base-time` *was* publish time. **It is not.** The real
behavior:

- **`--base-time T` is the schedule ANCHOR only.** Crosspost
  (`src/fanops/crosspost.py::surface_time`) staggers each surface's `scheduled_time` to a
  point **after** T — a per-(account, platform, clip) deterministic offset (anchor up to
  ~50 min) plus a fixed 40-min step per index with a bounded 0–29 min jitter. The step
  strictly exceeds the jitter so the schedule is **monotonic** in index (a later post can
  never land before an earlier one), and the clip is part of the seed so two clips never
  collide on the same minute on one surface (AUDIT H1/H2). Staggering is an **opsec**
  requirement: the "independent" accounts must not post in lockstep.
- **The publish step publishes whatever is due as of REAL wall-clock NOW**, *not* as of
  `base_time`. `advance` calls `publish_due(led, cfg, now=None)`, and `now=None` means
  `datetime.now(timezone.utc)` (`src/fanops/post/run.py`). A post is published only when
  its staggered `scheduled_time <= actual now`.

**Consequence:** a single `advance` (or `run`) **schedules into the future**; a *later*
pass — run after those staggered times have arrived in real time — is what actually
publishes them. You schedule now, you publish later.

**Same-pass publish (e.g. a backfill you want live immediately):** pass a `--base-time`
that is **in the past**, far enough back that even the staggered offsets (anchor + up to
~50 min + index spread) land *behind* real-now. Then the same pass that schedules them
also finds them due and publishes them. Picking `T = now` will **not** publish in the
same pass, because every staggered time is pushed after `T`.

---

## The feedback loop — `track` then `adjust`

**`fanops track [--window 30d]`** pulls per-post metrics and moves published posts to
`analyzed`:

- Needs `BLOTATO_API_KEY`. If the key is absent it **skips cleanly** —
  `track skipped: ...` — rather than erroring.
- Matches metric rows to posts by `submission_id`; **failed posts are skipped** (they
  have no real lift and must never enter the winners pool).
- Computes a **`lift_score`** that weights **saves / shares / retention over likes**
  (likes are near-noise). The weights live in `track._W`
  (`saves 4.0, shares 4.0, retention 3.0, reach 0.001, likes 0.05`). Re-weight there if
  Blotato exposes different fields or you want engagement-rate over raw reach.

**`fanops adjust [--winner-pct 0.3] [--retire-pct 0.2] [--lift-floor 20.0]`** ranks the
analyzed posts and acts on the tails (`src/fanops/adjust.py`):

- **Winners — amplify the top `--winner-pct` (default 0.3).** For each winner it
  **re-opens a fresh moment search on that winner's SOURCE**, injecting the winning
  moment's signature as guidance ("a moment like X hit hard — find MORE in that vein,
  don't repeat the timestamps"). This is *make more of what works*: the next `advance`
  answers the fresh request and reconciles new clips into the set.
- **Losers — retire the bottom `--retire-pct` (default 0.2) ONLY if its `lift_score` is
  below `--lift-floor` (default 20.0).** This retirement is **conservative and decoupled
  from winners**: a clip that clears the floor is **never** retired just for ranking low
  relative to a hit. The intent is to drop genuine duds without **draining the artist's
  catalogue** every pass.
- **Retiring suppresses the whole lineage.** `retire` calls `retire_clip`, and **if no
  sibling clip of that moment is still live, it retires the MOMENT too** — otherwise the
  render guard would re-render the moment into a fresh live clip on a later pass and
  silently undo the retirement.

> Ranking is currently **global** across all sources, not per-source. The `lift_floor`
> mostly neutralizes cross-source unfairness (a low-reach source's clips aren't retired
> if they clear the floor). Per-source ranking is in the backlog.

---

## The autonomous LLM responder (F02 / F13; audit B1 / H2 / N1)

The system answers its own gates autonomously with a model — no human in the loop. Set
`FANOPS_RESPONDER=llm`. The responder (`src/fanops/responder.py::LlmResponder`) reads each
pending request, calls the model, validates the output against the gate's schema, and writes
the response file. **`get_responder(cfg)` returns a working responder out of the box** — the
default model is the Claude Code CLI (`claude -p`); there is no stub to fill.

**Transport — `claude -p`, not the Anthropic SDK.** The default model
(`_default_claude_model`) shells the Claude Code CLI in headless print mode via
`src/fanops/llm.py::claude_json`:
`claude --bare -p "<prompt>" --output-format json --json-schema '<schema>' --allowedTools ""`.
Chosen over the SDK to keep one toolchain (no second SDK dependency) and fit the codebase's
shell-a-binary idiom (like ffmpeg/whisper) — `claude` is just one more absence-guarded binary.
`--allowedTools ""` makes it a pure generator (no tool use / file access). The prompt is built
from a **committed template** (`src/fanops/prompts.py::moment_prompt` / `caption_prompt`) and
paired with the gate's exact pydantic JSON schema, so most "LLM returned malformed JSON" risk
collapses into `structured_output`.

**Requirement — `claude` on `PATH` AND `ANTHROPIC_API_KEY` exported (load-bearing).** We pass
`--bare` for cron-safety (it skips hooks/MCP/plugin-sync/auto-memory/keychain). **The catch:
under `--bare`, Anthropic auth is STRICTLY `ANTHROPIC_API_KEY` (or apiKeyHelper via `--settings`)
— OAuth and keychain are NEVER read.** So a `claude login` (OAuth) session is **NOT** sufficient
for the autonomous responder; the environment that runs `fanops` must **export `ANTHROPIC_API_KEY`**.
Failure modes, both **quarantined per request** (not a crash): if `claude` is absent, `claude_json`
raises `ToolchainMissingError`; if `claude` is present but `ANTHROPIC_API_KEY` is unset/invalid,
`claude -p` exits non-zero with `"Not logged in · Please run /login"` → `RuntimeError` → the gate
logs `error` and stays pending (so a misconfigured key yields **zero autonomous content**, silently
but loggedly — check `run.log` for repeated `responder … error … Not logged in`).

**Per-request quarantine (audit H2 / N1).** `answer_pending` isolates each gate: one bad
request logs and leaves *that* gate pending, and never halts the others (mirrors `advance()`'s
per-unit quarantine). A **present-but-schema-invalid** response (`ValidationError`) is logged
with outcome `invalid` and the gate stays pending — distinct from a transient/CLI **`error`**
(audit N1: a malformed answer must not look identical to an absent one). Validation happens
*before* the write, so a rejected response leaves **no** `*.response.json` on disk (never a
half-write). A persistently-failing gate is retried up to the `run` loop's bounded passes
(`for _ in range(10)`), then `run` exits cleanly with the gate still awaiting.

**Schemas / payloads** (the model returns a dict that must validate against the gate kind):

- **`kind == "moments"`** → must validate as **`MomentDecision`**. Request `payload` fields the
  prompt sees: `source_id`, `duration`, `transcript`, `signal_peaks`, `language`, `guidance`.
  The schema asks for `{"picks": [{start, end, reason, transcript_excerpt, signal_score}, ...]}`.
  The responder stamps **both** `request_id` (from the live request) and `source_id` (forced
  from the **gate** payload — the gate is authoritative, so a model-hallucinated `source_id`
  cannot win), since both are required by `MomentDecision`.
- **`kind == "captions"`** → must validate as **`CaptionSet`**. Request `payload` fields:
  `clip_id`, `surfaces` (list of `{surface, platform}`), `transcript_excerpt`, `language`,
  `guidance`. The schema asks for `{"items": [{surface, caption, hashtags, language?}, ...]}`
  answering **every** requested surface with the surface key verbatim. `CaptionItem` now carries
  an **optional per-item `language`** field that the model self-declares (committed
  `caption_prompt` requires it); `ingest_captions` validates each declared language against the
  source `language` — normalizing to the base IETF subtag (`en-US`/`EN` == `en`) — and **holds**
  the clip on a true mismatch (AUDIT H5). It is therefore no longer only a HARD RULE in the prompt
  but an enforced ingest check. The responder stamps `request_id` automatically (`CaptionSet`
  needs only `request_id` + `items`).

The `guidance` in both payloads is the verbatim text of `context.md` — the committed prompt
templates pass it through so the model follows the creative brief. Semi-trusted transcript text
is JSON-quoted inside the prompts (injection isolation), so a crafted transcript line cannot
forge instructions.

**To use a different model** (e.g. for tests or an alternate backend): inject a callable —
`LlmResponder(cfg, model=my_callable)`, where `my_callable(kind, payload) -> dict`. The default
`claude -p` path is used only when no model is injected.

---

## Wiring the MCP poster (F15)

To publish via the connected Blotato MCP tool, set `FANOPS_POSTER=mcp`. The runtime
supplies the poster with a tool-caller:

```python
BlotatoMcpPoster(cfg, tool_caller=...)
```

`tool_caller(name, args) -> dict` must invoke the connected **`blotato_create_post`** MCP
tool with the flat args the poster builds and return the tool's result dict. The result
**must contain `postSubmissionId`** — if it does not, the poster marks the post
**`failed`** (a post we can't track by submission id is surfaced, not parked). With no
`tool_caller` wired the poster raises rather than silently no-op.

(The `rest` backend, `FANOPS_POSTER=rest`, needs no wiring beyond `BLOTATO_API_KEY`; it
POSTs to `https://backend.blotato.com/v2/posts` and raises loudly on 401. Retry is
**asymmetric on purpose** (Blotato has no idempotency key, and a publish timeout can
duplicate a post): a `429` is retried with bounded backoff (rejected pre-processing, so the
post was definitely not created), but a `5xx` or a network timeout *after the body was sent*
is **ambiguous** — the post may be live — so it is parked in `needs_reconcile` and **not**
re-POSTed. The digest surfaces such posts to verify via `GET /v2/posts/:id` before resubmit.)

---

## Three human-only steps

These cannot be automated and gate a live run:

1. **Create the fan accounts** on the platforms (Instagram, TikTok).
2. **Connect each account in Blotato and paste its numeric `account_id` into
   `accounts.json`**, then flip that account's `status` to `active`. Until an account is
   `active` with a numeric id, it is not a posting surface. (`accounts.json` ships seeded
   with `@TBD-1` / `@TBD-2` placeholders in `status: planned` — fill these in, do not
   recreate the file.)
3. **Review brand-risk HOLDs in the digest.** Clips that tripped the caption guardrails
   (begging / "official" / "link in bio", EN or AR) are held with a reason and never
   post until a human clears them.

   **Clearing a brand-risk hold:** a clip in `held` state is paused for human review (it appears in the digest's "Brand-risk holds" section with the matched reason). To clear it: (1) edit the offending caption(s) in the clip's `*.response.json` under `04_agent_io/requests/` so they pass the EN+AR brand-risk screen, then (2) reset the clip's `state` from `held` back to `captions_requested` in `00_control/ledger.json` and re-run `fanops advance` — it will re-ingest the corrected captions. (A future `fanops unhold <clip_id>` command is on the backlog to automate step 2.)

---

## Integration checkpoints — confirm BEFORE the first live run

The following are marked `INTEGRATION CHECKPOINT` in the code. They are **assumptions
about Blotato's API shape, not verified facts** — confirm each against current Blotato
docs before trusting a live run, and fix the one spot noted if it differs:

- **Media upload contract** — `POST /v2/media/uploads` returning `presignedUrl` +
  `publicUrl`, then a binary `PUT` to the presigned URL (`src/fanops/post/media.py`). A
  response missing those keys raises.
- **Submission-id response key** — the publish response (REST and MCP) must carry
  **`postSubmissionId`**; a 2xx without it is failed, not parked
  (`blotato_rest.py`, `blotato_mcp.py`).
- **Metrics endpoint / fields** — `GET /v2/posts?window=...` returning rows keyed by
  `postSubmissionId` with a `metrics` dict (`src/fanops/post/metrics.py`). If
  saves/shares/retention are **not** exposed, re-weight `track._W` on the fields that are.
- **MCP tool name / args** — the tool is assumed to be `blotato_create_post` taking the
  flat args from `build_blotato_mcp_args`. Confirm the name and argument shape of the
  connected tool.
- **Signal weighting target** — saves/shares/retention > likes is the optimization
  target encoded in `track._W`. Re-weight there if Blotato's available fields differ or
  the desired target changes.

---

## §Backlog — deferred enhancements

Institutional memory of what was intentionally **not** built and why. Two groups:
deferred from the original plan, and surfaced during the build.

**Deferred from the plan**

- **Burned-in subtitle / hook overlay rendering** — clips currently carry no rendered
  on-screen text; the TikTok caption guidance assumes an on-screen hook that isn't yet
  produced.
- **Trending-audio selection** — no automatic choice of trending sounds per platform.
- **Timezone / daypart scheduling optimization** — staggering is opsec spread, not
  audience-time-of-day tuning.
- **Per-surface best-window learning** — no learned "best time to post" per surface.
- **Multi-artist tenancy** — single-artist only; no tenancy/isolation for more artists.
- **Richer secrets manager beyond `.env`** — secrets are read from `.env` only.

**Surfaced during the build**

- **(a) Reconcile step (`submitting` + `needs_reconcile`) — DONE (audit H4).** `fanops
  reconcile` (and an automatic pass inside `advance`/`run` before publishing) polls
  `GET /v2/posts/{postSubmissionId}` for any stranded post that **has a submission id** and
  resolves it: `published → published` (+ public_url), `failed → failed` (safe to re-queue),
  `in-progress`/`scheduled → left parked`. **Honest limit:** that endpoint is the only post
  lookup Blotato offers and it **requires the submission id** (no content/account search). A
  post stranded **without** an id (a pure network timeout, or a crash before the poster
  returned one) cannot be looked up programmatically, so it stays parked for **human**
  reconcile (the digest's "Needs reconcile" section). To shrink that residue, the REST poster
  now captures a `postSubmissionId` from an ambiguous-5xx body when one is present, making
  those posts auto-reconcilable. We never guess a post's fate — a wrong guess would drop a live
  post or double-publish one.
- **(b) Externalize the tunable lists to config / `context.md`.** The brand-risk
  anti-pattern lists (`caption._OFFBRAND_EN` / `_OFFBRAND_AR`) and the lift weights
  (`track._W`) are hardcoded. Moving them to config/`context.md` lets the operator tune
  the HOLD gate and the optimization target without a code change.
- **(c) REST backoff jitter.** The `429` backoff is plain exponential and **un-jittered**
  (`1→2→4→8`, thundering-herd risk if many surfaces rate-limit at once) — add jitter
  (`delay*2 + random.uniform(0, delay)`). *(Network-error handling is now resolved: C1 catches
  `requests.exceptions.RequestException` in `BlotatoRestPoster.publish` and parks the post in
  `needs_reconcile` rather than letting it escape to `publish_due`. Network errors are **not**
  retried in-ladder on purpose — a timeout after the body was sent is ambiguous, so retrying
  could double-post.)*
- **(d) Per-source ranking in `adjust`.** Ranking is global; the `lift_floor` mostly
  neutralizes cross-source unfairness but does not fully solve it.
- **(e) Media size cap / size-aware upload timeout.** The media PUT uses a fixed 120s
  timeout and no size cap; large files need a size-aware timeout (and a cap to reject
  oversize uploads).
- **(f) `fanops unhold <clip_id>` command.** Reset a held clip to `captions_requested`
  (currently a manual ledger edit — see "Clearing a brand-risk hold" above).
- **(g) Per-platform duration clamp.** Enforce a per-surface max clip length at crosspost
  time (hold-vs-skip per surface) — needs knowing clip duration at crosspost time. The
  earlier unenforced `PLATFORM_MAX_SECONDS` dict was removed as a false safety contract.
- **(f) Externalize the hardcoded YouTube title fallback.** The yt-dlp path has a
  hardcoded title fallback (`"Moh Flow"`); move it to config.
- **(g) Lint + dedup.** Add `ruff` to dev deps for lint enforcement, and consolidate the
  duplicated `_parse` / `BASE_URL` helpers (repeated across `run.py`, `crosspost.py`,
  `tagging.py`, `media.py`, `blotato_rest.py`, `metrics.py`) into shared modules.
