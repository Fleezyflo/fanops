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
fanops reconcile
fanops track  [--window 30d]
fanops adjust [--winner-pct 0.3] [--retire-pct 0.2] [--lift-floor 20.0]
fanops gc     [--keep-days 30]
fanops resolve <post_id> <published|failed> [--url U]
fanops unhold <clip_id>
fanops retry-source <source_id>
fanops retry-metrics <post_id>
fanops digest
fanops run    [--base-time T]
```

Environment variables (read at runtime from `.env`, see `src/fanops/config.py`):

| Var | Values | Effect |
|---|---|---|
| `FANOPS_POSTER` | `dryrun` (default) \| `rest` \| `mcp` | Which publish backend. `dryrun` writes `file://` media URLs and never hits the network. |
| `FANOPS_RESPONDER` | `manual` (default) \| `llm` | `manual` = a human/cron writes the response files; `llm` = the LLM responder answers gates. With `llm`, `advance`/`run` **hard-fail (exit 2) unless `ANTHROPIC_API_KEY` is set** — the cutover-safety preflight (see below). |
| `BLOTATO_API_KEY` | string | Required for `rest`, `mcp`, and `track`. Absent ⇒ those refuse/skip cleanly. With `FANOPS_POSTER` in `{rest, mcp}`, `advance`/`run` also hard-fail (exit 2) when it is unset. |
| `ANTHROPIC_API_KEY` | string | Required for `FANOPS_RESPONDER=llm`. The responder shells `claude --bare`, which reads **only** this var (never OAuth/keychain). Absent with `llm` ⇒ `advance`/`run` exit 2 (the silent-zero-output guard). |
| `FANOPS_ARTIST_NAME` | string (optional) | Artist **display name** used as the YouTube title fallback when a post has no explicit title (audit h). Default `"Moh Flow"` (unchanged). Distinct from the `@mohflow` caption mention (`tagging.ARTIST_HANDLE`). |
| `FANOPS_BURN_SUBS` | `1`/`true`/… (default **ON**) \| `0`/`false`/`no`/`off` | Burn the transcript-derived subtitles + top-third hook into each rendered clip. **DEFAULT ON** — an unset env burns subs, so the feature is live with no operator action; only the explicit off-words `0`/`false`/`no`/`off` (case-insensitive) disable it. **Fail-open**: if this ffmpeg lacks the text filter or the source has no transcript, the clip still renders (plain), logging one `subs_skipped` line. Requires a **text-capable ffmpeg (libass)** — see the note below. |
| `FANOPS_SUBTITLE_FONT` | string (optional) | Font face for the `.ass` subtitles. Default `"Arial Unicode MS"` — an Arabic-capable face so RTL captions render. Override if the host lacks that font or you prefer another Unicode/Arabic typeface. |
| `FANOPS_CREATIVE_VARIATION` | `1`/`true`/`yes`/`on` \| unset (default **OFF**) | Per-account creative variation (backlog (j), v1). When ON, each active account gets a genuinely different caption + burned-in on-screen hook per clip (the caption agent returns a per-surface `hook`; crosspost burns it onto the shared base clip via a cheap per-account overlay pass and stamps `Post.variant_key`/`variant_hook`). The digest's "Lift by variant" section then attributes which creative wins. **DEFAULT OFF (opt-in)** — only the on-words `1`/`true`/`yes`/`on` (case-insensitive) enable it. **Fail-open**: toggle off, no per-surface hook, or a non-text-capable ffmpeg (no libass) ⇒ today's shared-clip behavior. Requires a **text-capable ffmpeg (libass)** for the burn. |
| `FANOPS_ESCALATION_BUDGET_USD` | float (optional) | Spend cap knob. |

**Optional override file — `00_control/tuning.json`** (audit b). An operator can re-tune the
brand-risk HOLD gate and the optimization target **without a code change** by writing this
OPTIONAL file (`cfg.tuning()`, `src/fanops/config.py`):

```jsonc
{ "offbrand_en": ["...regex...", "..."],   // REPLACES caption._OFFBRAND_EN when present
  "offbrand_ar": ["...regex...", "..."],   // REPLACES caption._OFFBRAND_AR when present
  "lift_weights": { "saves": 4.0, "shares": 4.0, "retention": 3.0, "reach": 0.001, "likes": 0.05 } }
```

An **absent file or a missing key falls back to the in-code default** (so existing behavior is
unchanged and no new REQUIRED file is introduced). It is read at most once per stage and **not
cached**, so an edit takes effect on the next stage with no process restart. Unlike a control
file (`accounts.json`/`ledger.json`), a corrupt/unreadable `tuning.json` **never crashes** an
autonomous run — it logs a warning and falls back to all defaults.

### Cutover-safety preflight — the silent-zero-output guard (SAFETY feature)

`fanops advance` and `fanops run` call `_check_preflight(cfg)` (`src/fanops/cli.py`) **before**
doing any work, right after `_check_accounts`. It **refuses to run (exit 2, one-line message to
stderr, no traceback)** for the two env mismatches that would otherwise make the pipeline do
credentialless *nothing* — the #1 cutover trap:

- **`FANOPS_RESPONDER=llm` but `ANTHROPIC_API_KEY` unset** — the `--bare` responder reads no
  OAuth/keychain, so it would fail every gate "Not logged in", clear nothing, and publish
  nothing **without crashing**. This is the guaranteed-silent failure the heartbeat/dead-man's
  switch is designed to *detect after the fact*; the preflight catches it **up front** instead.
- **`FANOPS_POSTER` in `{rest, mcp}` but `BLOTATO_API_KEY` unset** — publishing would 401.

The **default `dryrun` + `manual` config (no keys) trips neither and passes cleanly (exit 0)**,
so the offline pipeline is unaffected. This is a *safety* feature: a misconfigured live cutover
now fails loudly and immediately rather than running green-but-empty until a monitor notices.

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
pending) on demand; most commands also refresh it. Among its sections is an explicit
**`## Pending agent gates (responder has not cleared)`** block (E3) that lists every
unanswered gate by **kind + key** (`- moments: <key>` / `- captions: <key>`) — the word
"pending" is the searchable signal an operator/monitor greps for. A gate the responder has
**cleared** (its `*.response.json` echoes the latest `request_id`) drops out of the section,
so a gate that **persists** there across runs is a real stuck/unanswered gate (see *Heartbeat
/ dead-man's-switch* — a persistently-pending gate + `published_in_run==0` is the signature of a
silently-unauthed responder).

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

**The learning loop closes inside `run` (E1).** After the respond→advance loop converges,
`run` runs one `track`+`adjust` pass — `pull_metrics → classify_outcomes → amplify → retire`
(`src/fanops/cli.py`) — so an unattended deployment makes more of what works without a
separate `track`/`adjust` cron. It is **gated by the identical reconcile guard**
(`cfg.poster_backend != "dryrun" and cfg.blotato_api_key`, byte-identical to the live portion
of the reconcile guard at `pipeline.py`): in the default **dryrun** backend (or with no key) the
pass is **never entered** — no metrics fetch, no amplify, no state change — so the offline
pipeline behaves exactly as before. The pass runs in its **own** `Ledger.transaction` (lock-safe;
it cannot race the next advance) and runs **once**, textually after the bounded respond/advance
loop, so it can never fire before convergence or twice. Any failure (`pull/classify/amplify/retire`
hiccup) is logged as `learn error` to `run.log` and **swallowed** — it can never crash the
unattended run; the exit code stays 0. Amplification is bounded per source (see *The feedback
loop* → `max_amplify_per_source`) so the autonomous responder can't grow one source endlessly.

### Heartbeat / dead-man's-switch (B5 / E2)

Every `run` and `advance` emits **one** heartbeat line: printed to **stdout** as JSON **and**
appended (mode `a`) to `07_reports/run.log` via `get_logger`. It is emitted once per command —
never inside the `range(10)` loop. An **external** monitor (a `cron`+`mail` job, or PagerDuty)
diffing consecutive run.log lines uses it to distinguish *alive-but-idle* from *cron is dead*:

```json
{"heartbeat": "2026-06-02T10:03:24.582671+00:00", "fanops_version": "0.3.0", "published_in_run": 0, "last_published_age_hours": null}
```

| Field | Meaning | How a monitor alerts on it |
|---|---|---|
| `heartbeat` | a **live** ISO-8601 UTC clock (`datetime.now(timezone.utc)`) — changes every invocation | the timestamp is **frozen / no new line** ⇒ **the cron itself is dead** (the process never ran). The changing ts is the load-bearing signal; a present-but-stale file would otherwise hide a dead scheduler. |
| `fanops_version` | the running build | a stale binary is visible in the alert stream |
| `published_in_run` | posts published **this run** (a set-difference THIS-RUN delta, **not** the cumulative total) | **`0` across N consecutive runs** ⇒ the pipeline is stuck — alert on "0 published in N runs" |
| `last_published_age_hours` | hours since the most-recent published post's scheduled time (2 dp; `null` when nothing published / the time is unparseable) | alert on "last post age > threshold" |

Example external monitor (out of repo scope, like the cron itself): a job that tails the last
two `\theartbeat\t` lines in `run.log` and pages if (a) the latest `heartbeat` ts equals the
prior one or is older than the cron interval (cron dead), (b) `published_in_run == 0` for the
last N lines (stuck pipeline), or (c) `last_published_age_hours` exceeds a threshold.

**Catching a silently-unauthed responder.** Because the responder shells `claude --bare`, which
**ignores OAuth/keychain** — auth is **strictly `ANTHROPIC_API_KEY`** (see *the autonomous LLM
responder* below) — a responder that is **running but unauthed** (the key was never exported)
fails every gate with "Not logged in", clears nothing, and publishes nothing, **without crashing**
(each gate is quarantined and logged). The dead-man's-switch is how you catch this exact failure:
`published_in_run` stays **0 forever** *and* the digest's **"Pending agent gates"** section keeps
naming the same unanswered gates. The heartbeat proves the cron is alive; the zero delta + the
pending-gates list prove it is making no progress — so the operator knows to fix the **key**
(grep `run.log` for repeated `responder … error … Not logged in`), not the scheduler.

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

## Recovery verbs — manual intervention for non-self-resolving states

Four verbs are the operator's hands-on surface for the states the automatic pipeline
**cannot** resolve on its own. Each is a tight, **local-only** `Ledger.transaction` (no
network call, so no auth needed), and each exits **2** with a one-line message if the
target id doesn't exist or is in the wrong state — never a traceback. Source:
`src/fanops/cli.py`.

| Verb | When | What it does |
|---|---|---|
| `fanops resolve <post_id> <published\|failed> [--url U]` | a post is stuck in `needs_reconcile`/`submitting` and `fanops reconcile` can't auto-resolve it (Blotato never returns a terminal status, or the post has no real submission id) — the operator checks the platform by hand | forces the post's state to ground truth. `published` (optionally records the live URL via `--url`) or `failed` (safe to re-queue). The documented human-reconcile escape hatch (audit H1). |
| `fanops unhold <clip_id>` | a clip is parked `held` by the brand-risk gate and a human has reviewed/corrected the caption | clears `held`/`held_reason` and resets the clip to `captions_requested` so the next `advance` re-ingests the corrected captions. Replaces the old hand-edit of `ledger.json` (backlog (f), now done). |
| `fanops retry-source <source_id>` | a source is quarantined in `error` (a transient transcribe/probe failure) | resets it to `catalogued`, clears `error_reason`, and sets `meta["transcribed"]=False` so the next `advance` does a real re-transcribe from the top. |
| `fanops retry-metrics <post_id>` | a `published` post's metrics never landed in a `track` pass | a no-op state confirmation — the post stays `published` so the next `track` re-pulls its metrics. Exits **2** if the post isn't `published` (nothing to re-pull). |

These are the manual counterpart to the automatic reconcile/quarantine machinery: the
pipeline quarantines a bad unit (per-unit `error` state) and parks an ambiguous post
(`needs_reconcile`) on its own, but a **human** decides when the underlying problem is
fixed and the unit should re-enter the flow. Because they only mutate the local ledger
under the flock, they are safe to run while cron's `run` is idle (and will wait briefly,
then `LockBusyError`-skip, if a `run` is mid-pass — see *Overlapping runs are safe*).

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
  (likes are near-noise). The default weights live in `track._W`
  (`saves 4.0, shares 4.0, retention 3.0, reach 0.001, likes 0.05`). Re-weight either in code,
  or — without a code change — via `00_control/tuning.json` → `lift_weights` (audit b; see
  *Optional override file* above), if Blotato exposes different fields or you want
  engagement-rate over raw reach.

**`fanops adjust [--winner-pct 0.3] [--retire-pct 0.2] [--lift-floor 20.0]`** ranks the
analyzed posts and acts on the tails (`src/fanops/adjust.py`):

- **Winners — amplify the top `--winner-pct` (default 0.3).** For each winner it
  **re-opens a fresh moment search on that winner's SOURCE**, injecting the winning
  moment's signature as guidance ("a moment like X hit hard — find MORE in that vein,
  don't repeat the timestamps"). This is *make more of what works*: the next `advance`
  answers the fresh request and reconciles new clips into the set.
  - **Per-source amplify budget (E1).** `amplify(led, cfg, winners, *, max_amplify_per_source=3)`
    caps how many times a single source can be re-mined, tracked on
    `src.meta["amplify_count"]` (a **missing** key counts as 0, so existing sources keep
    amplifying until they hit the cap). At/over the cap the source is **skipped entirely** —
    no fresh moment request is written, no `moments_requested` state flip — so the autonomous
    learning loop inside `fanops run` cannot grow one viral source without bound. Only a
    **successful** amplify increments the count (a winner whose lineage no longer resolves to a
    source is skipped without consuming budget).
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
tool with the flat args the poster builds and return the tool's result dict. The id is matched
as `postSubmissionId` | `submissionId` | `id` (recursing into a nested `data` dict) via D2's
shared `_extract_submission_id`. An MCP 2xx with **no recognizable id is parked
`needs_reconcile`** (never `failed` — failed would be re-queueable and risk a double-post to a
real account); the D1 client token is preserved so the post stays pollable. An **auth failure**
must reach the poster as a typed `BlotatoAuthError` (the production wiring raises it) — it
propagates so `run.py` halts the queue by type (F52/H8); an untyped auth message is best-effort
substring-matched as a fallback. With no `tool_caller` wired the poster raises rather than
silently no-op.

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

   **Clearing a brand-risk hold:** a clip in `held` state is paused for human review (it appears in the digest's "Brand-risk holds" section with the matched reason). To clear it: (1) edit the offending caption(s) in the clip's `*.response.json` under `04_agent_io/requests/` so they pass the EN+AR brand-risk screen, then (2) run **`fanops unhold <clip_id>`** — it clears the hold and resets the clip to `captions_requested`; the next `fanops advance` re-ingests the corrected captions. (See *Recovery verbs* above. The old manual `ledger.json` edit for step 2 is no longer needed.)

---

## Operator runbook — fresh checkout → first real post

The bridge from "code-complete" to "live." Everything below the dashed line in step 5 is
already built and tested; steps 1–3 and the credential exports are the **only** work that
cannot be automated (real accounts, a human's Blotato connection, and secrets). Run the steps
**in order** from the repo root.

**1. Create the real fan accounts** *(human-only)* — make the actual Instagram / TikTok
   accounts you intend to post from. This is off-platform; FanOps never creates accounts.

**2. Connect each account in Blotato** *(human-only)* — in the Blotato dashboard, link each
   fan account so Blotato can post on its behalf. Each connected account gets a **numeric
   `account_id`** in Blotato.

**3. Paste each numeric `account_id` into `accounts.json` and set `status: active`.** The file
   ships seeded with `@TBD-1` / `@TBD-2` placeholders in `status: planned` — **fill these in,
   do not recreate the file.** An `active` account with an empty/zero `account_id` is caught by
   `fanops`' pre-run `_check_accounts` (exit 2 with the problem) before anything reaches Blotato.

```jsonc
// MohFlow-FanOps/00_control/accounts.json — example
{ "accounts": [
  { "handle": "@your.real.ig", "platform": "instagram", "account_id": "123456", "status": "active" },
  { "handle": "@your.real.tt", "platform": "tiktok",    "account_id": "789012", "status": "active" }
] }
```

**4. Set `BLOTATO_API_KEY` in `.env` (sandbox first).** Copy `.env.example` → `.env` and put
   the **sandbox** key in first to dry-run the live contract before touching real posting:

```bash
cp .env.example .env
echo 'BLOTATO_API_KEY=sk-sandbox-...'  >> .env   # sandbox key first; swap to the live key only after step 6 passes
echo 'FANOPS_POSTER=mcp'               >> .env   # the live backend (default is dryrun — see below)
```

**5. Ensure `claude` is authed on the host** *(required for the autonomous LLM responder)* —
   the responder shells `claude --bare`, which **ignores OAuth/keychain**. Auth is **strictly
   the `ANTHROPIC_API_KEY` env var**:

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # MUST be exported in the cron/launchd environment, not just your shell
echo 'FANOPS_RESPONDER=llm' >> .env      # opt into the autonomous responder (default `manual` is a no-op — a human/cron writes the response files)
claude --bare -p 'say ok' --output-format json   # smoke: confirms the key works headless
```
   ──────────────────────────────────────────────────────────────────────────────
   *Everything below is already built — these are verification + scheduling steps.*

**6. Run the `[GATED]` Blotato smoke test** (the live contract check, Phase D's Task D5) — it
   confirms the `postSubmissionId` key, the `/media/uploads` shape, and the analytics
   endpoint against your sandbox key. It is **creds-gated**, so it skips cleanly without a key:

```bash
BLOTATO_API_KEY=sk-sandbox-... BLOTATO_SMOKE_ACCOUNT_ID=<a sandbox account_id> \
  ./.venv/bin/python -m pytest tests/integration/test_blotato_smoke.py -v
```
   Resolve any field-name surprises here (re-weight `track._W` if the metrics fields differ —
   see *Integration checkpoints* below) **before** swapping the live key into `.env`.

**7. Schedule `fanops run` + a dead-man's-switch monitor.** Add a cron (or launchd
   `StartInterval`) entry that `cd`s into the repo (mandatory — `fanops` resolves its data dirs
   from cwd, there is no `FANOPS_ROOT`) and runs `fanops run` on your interval. `run` responds
   to gates, advances the pipeline, and — on a live backend with a key — closes the learning
   loop (`track`+`adjust`) once per pass:

```cron
*/30 * * * * cd /path/to/repo && ANTHROPIC_API_KEY=sk-ant-... ./.venv/bin/fanops run >> run.out 2>&1
```
   Then point an **external monitor** at the heartbeat (see *Heartbeat / dead-man's-switch*
   above): page if the `heartbeat` ts stops advancing (cron is dead), or if
   **`published_in_run == 0` for N consecutive runs** (the pipeline is alive but stuck — most
   often a silently-unauthed responder; grep `run.log` for repeated `Not logged in`). This
   monitor is the difference between "a 3am failure looks like it's working" and "you get paged."

After step 7 the system runs unattended. The recovery verbs (*Recovery verbs* above) are your
manual surface when the monitor fires: `unhold` a reviewed clip, `resolve` an ambiguous post,
`retry-source` a quarantined source, `retry-metrics` an unmeasured post.

---

## Integration checkpoints — confirm BEFORE the first live run

Most of these were **verified against the live Blotato MCP tool schemas (2026-06-02)** during
Phase D (the MCP server connected mid-session; its tool schemas are authoritative API docs, though
a data-returning call was auth-blocked — see "live verification" below). The one item still an
unverified assumption is the **metrics fields**.

- **Media upload contract** — VERIFIED 2026-06-02: `create_presigned_upload_url` returns
  `presignedUrl` + `publicUrl`, then a binary `PUT` to the presigned URL (`src/fanops/post/media.py`).
  A response missing those keys raises. (Live schema confirms both keys.)
- **Submission-id response key** — VERIFIED 2026-06-02: the id field **is `postSubmissionId`**
  (`blotato_create_post` returns it; `blotato_get_post_status` takes it). A 2xx **without** a
  recognizable id is parked **`needs_reconcile`, NOT `failed`** (it may be live — failed would be
  re-queued and risk a double-post); the posters accept `postSubmissionId` | `submissionId` | `id`
  (incl. nested `data`) via `_extract_submission_id` (`blotato_rest.py`, `blotato_mcp.py`).
- **Status enum + live-post URL key** — VERIFIED 2026-06-02: `in-progress → published | scheduled
  | failed`; the live-post URL is **`publicUrl` on `get_post_status`** (the single-post lookup
  `reconcile.py` uses) but **`postUrl` on `list_posts`** (a real API divergence). FanOps reads the
  URL only from `get_post_status` (`reconcile.py:64`), so it reads the correct key; `track.py` reads
  only `postSubmissionId` + `metrics` from `list_posts` rows, never a URL — no mismatch. (A future
  feature that reads a URL from a `list_posts` row WOULD need `postUrl`.)
- **Metrics endpoint / fields** — STILL A CHECKPOINT: `GET /v2/posts?window=...` returning rows
  keyed by `postSubmissionId` with a `metrics` dict (`src/fanops/post/metrics.py`). Which engagement
  fields are exposed is unconfirmed; if saves/shares/retention are **not** exposed, re-weight
  `track._W` on the fields that are.
- **MCP tool name / args** — VERIFIED 2026-06-02: the tool is `blotato_create_post` taking the flat
  args from `build_blotato_mcp_args`. (Live schema confirms the name + flat `accountId`/`platform`/
  `text`/`mediaUrls` shape.)
- **Signal weighting target** — saves/shares/retention > likes is the optimization
  target encoded in `track._W`. Re-weight there if Blotato's available fields differ or
  the desired target changes.

---

## §Backlog — deferred enhancements

Institutional memory of what was intentionally **not** built and why. Two groups:
deferred from the original plan, and surfaced during the build.

**Deferred from the plan**

- **Burned-in subtitle / hook overlay rendering — DONE.** `overlay.py` builds a styled
  `.ass` from the source transcript (rebased into clip time, Arabic/RTL-safe) plus a
  top-third hook (`Moment.hook`); `clip.py` burns it via the ffmpeg `subtitles` filter
  chained AFTER the reframe. Gated by `FANOPS_BURN_SUBS` (default **ON**). **Fail-open**:
  if this ffmpeg lacks the text filter or the source has no transcript, the clip renders
  plain (one `subs_skipped` log line) — a clip is never blocked on subtitles. **Requires a
  text-capable ffmpeg (libass)** — the project's `ffmpeg-full` build has the `subtitles`/`ass`
  filter; a stripped ffmpeg without it falls open (no on-screen text, logged). Font is
  `FANOPS_SUBTITLE_FONT` (default `"Arial Unicode MS"`).
- **Trending-audio selection** — no automatic choice of trending sounds per platform.
- **Timezone / daypart scheduling optimization** — staggering is opsec spread, not
  audience-time-of-day tuning.
- **Per-surface best-window learning** — no learned "best time to post" per surface.
- **Multi-artist tenancy** — single-artist only; no tenancy/isolation for more artists.
- **Richer secrets manager beyond `.env`** — secrets are read from `.env` only.

**Surfaced during the build**

- **(a) Reconcile step (`submitting` + `needs_reconcile`) — DONE (audit H4; refined in Phase D).**
  `fanops reconcile` (and an automatic pass inside `advance`/`run` before publishing) polls
  `GET /v2/posts/{postSubmissionId}` for any stranded post that **has a submission id** and
  resolves it: `published → published` (+ public_url), `failed → failed` (safe to re-queue),
  `in-progress`/`scheduled → left parked`. **Phase D (audit H1):** every crossposted post is now
  stamped at birth with a stable **client idempotency token** (`submission_id = f"fanops_{_hash('idemp',
  post.id)}"`), so a post parked after a pure network timeout is no longer id-less — it carries a
  `fanops_` token and IS polled. A real `postSubmissionId` from the response (2xx or an ambiguous-5xx
  body) overwrites the token. But a `fanops_` token is not a real Blotato id, so polling it **404s**;
  `reconcile_posts` now **contains that per-post poll error** (mirrors `publish_due`): a
  `BlotatoAuthError` propagates (halt — every poll will 401), any other poll error leaves THAT post
  **parked, never `failed`** (a poll error is not evidence the post failed — it may be live) and the
  loop continues so later posts still reconcile. The irreducible residue (a post with genuinely no
  `submission_id` at all) is skipped for **human** reconcile (the digest's "Needs reconcile" section).
  We never guess a post's fate — a wrong guess would drop a live post or double-publish one.
- **(b) Externalize the tunable lists to config — DONE (tail).** The brand-risk anti-pattern
  lists (`caption._OFFBRAND_EN` / `_OFFBRAND_AR`) and the lift weights (`track._W`) are now
  operator-overridable via the **optional `00_control/tuning.json`** (`cfg.tuning()`) →
  `offbrand_en` / `offbrand_ar` / `lift_weights`. When a key is present its list **REPLACES**
  the in-code default; an absent file or missing key keeps the default, so existing behavior is
  unchanged and no new REQUIRED file is introduced. The file is OPTIONAL: a corrupt/unreadable
  `tuning.json` logs a warning and falls back to defaults rather than crashing the run. See the
  *Optional override file* block under *Environment variables* above.
- **(c) REST backoff jitter — DONE (Phase D, D4).** The `429` backoff is now jittered:
  `time.sleep(delay + random.uniform(0, delay)); delay *= 2` (`blotato_rest.py`), so retries no
  longer fire in lockstep across surfaces. Still bounded by `_MAX_RETRIES` (exhaustion → `failed`,
  re-queueable since a 429 is rejected pre-processing). *(Network-error handling was already resolved: C1 catches
  `requests.exceptions.RequestException` in `BlotatoRestPoster.publish` and parks the post in
  `needs_reconcile` rather than letting it escape to `publish_due`. Network errors are **not**
  retried in-ladder on purpose — a timeout after the body was sent is ambiguous, so retrying
  could double-post.)*
- **(d) Per-source ranking in `adjust`.** Ranking is global; the `lift_floor` mostly
  neutralizes cross-source unfairness but does not fully solve it.
- **(e) Media size cap / size-aware upload timeout — DONE (tail).** `upload_media`
  (`src/fanops/post/media.py`) now **rejects an oversize file pre-network** (`_MAX_UPLOAD_BYTES`
  = 500 MB; raises before any HTTP call) and **scales the PUT timeout to the file size**
  (`_put_timeout_for`: ~2 s/MB over a 120 s base, clamped at a 600 s ceiling) so a flat 120 s no
  longer kills a large-but-valid upload mid-stream nor makes a tiny upload wait forever.
- **(f) Operator-recovery verbs (`unhold` / `resolve` / `retry-source` / `retry-metrics`) — DONE (Phase F).**
  The four states that previously required hand-editing `ledger.json` — a brand-risk `held` clip, an
  ambiguous `needs_reconcile`/`submitting` post, a quarantined `error` source, a `published`-but-unmeasured
  post — each now have a one-verb operator path (see *Recovery verbs* above). This closes the audit's
  recurring "operability gap" (every recovery needed a manual ledger edit) and H1's missing human-reconcile
  path. Each is a tight local-only `Ledger.transaction`; unknown id → exit 2, never a traceback.
- **(g) Per-platform duration clamp — DONE (tail), real enforcement.** Crosspost
  (`src/fanops/crosspost.py`) now enforces `PLATFORM_MAX_SECONDS` per surface: the clip's
  playable duration is its moment window (`end - start`), and if that duration is **known
  (> 0) and exceeds the platform's cap**, that **one surface is skipped** (the clip still posts
  to platforms whose cap it satisfies — conservative, never wedges the whole clip). **Unknown
  duration (None/≤0) or a platform with no cap → fail-open (do not skip)**, so the change can
  never silently drop a post on missing data. This replaces the removed false-contract dict
  with real enforcement.
- **(h) Externalize the hardcoded YouTube title fallback — DONE (tail).** The YouTube title
  fallback is now `cfg.artist_name` (`src/fanops/config.py`), set by the optional
  **`FANOPS_ARTIST_NAME`** env var; default `"Moh Flow"` is byte-identical to the old hardcoded
  value, so existing behavior is unchanged. (This is the artist **display name** — distinct from
  the `@mohflow` caption mention in `tagging.ARTIST_HANDLE`; the two are intentionally not
  unified.)
- **(i) Lint + dedup — DONE (tail).** `ruff` is now a dev dependency and lint is enforced
  (`ruff check src/` is green). The duplicated `_parse` ISO-8601 helpers were consolidated into
  a single `timeutil.parse_iso`, and the repeated Blotato `BASE_URL` was given one home — both
  formerly copy-pasted across `run.py`, `crosspost.py`, `tagging.py`, `media.py`,
  `blotato_rest.py`, `metrics.py`.
- **(j) Per-account creative variation — v1 DONE (observe-only).** With `FANOPS_CREATIVE_VARIATION=1`,
  each active account gets a genuinely different caption + burned-in on-screen hook per clip (the
  caption agent returns a per-surface hook; crosspost burns it onto the shared base clip via a cheap
  per-account overlay pass). The `track → analyzed → adjust` lift loop already attributes per-post;
  the digest's "Lift by variant" section shows which creative wins. Default OFF (opt-in). Fail-open:
  no hook / no libass / toggle off → today's shared-clip behavior. Auto-propagating winners into
  amplify is a documented follow-up (touches the C1-risk machinery; needs real lift-by-variant data).
