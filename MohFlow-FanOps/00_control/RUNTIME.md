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
   - Or, with the LLM responder wired and `FANOPS_RESPONDER=llm`, run **`fanops respond`**
     to answer them automatically.
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
operation set `FANOPS_RESPONDER=llm` and wire the responder (below); with the default
`manual` responder there is nothing to drain and it will stop after one pass.

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

On macOS a launchd `StartInterval` agent is the equivalent. Note that creating those
scheduled jobs (CronCreate / system scheduled-tasks) is an environment concern, **not**
something this repo manages.

---

## Scheduling vs publishing — IMPORTANT (deviation from the original plan)

The original plan implied `--base-time` *was* publish time. **It is not.** The real
behavior:

- **`--base-time T` is the schedule ANCHOR only.** Crosspost
  (`src/fanops/crosspost.py::surface_time`) staggers each surface's `scheduled_time` to a
  point **after** T — a per-(account, platform) deterministic offset plus a spread of
  35–95 minutes per index. Staggering is an **opsec** requirement: the "independent"
  accounts must not post in lockstep.
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

## Wiring the LLM responder (F02 / F13)

The system can answer its own gates with a model. Set `FANOPS_RESPONDER=llm`. The
responder (`src/fanops/responder.py::LlmResponder`) reads each pending request, calls a
model, validates the model's output against the schema, and writes the response file.

Two ways to supply the model:

1. **Implement `LlmResponder._default_model(self, kind, payload) -> dict`** to call the
   Anthropic SDK with a **committed prompt template** built from the request `payload`.
   It currently raises `RuntimeError("LlmResponder needs a model callable wired ...")` —
   that is the seam to fill.
2. **Inject a model callable** instead: `LlmResponder(cfg, model=my_callable)`, where
   `my_callable(kind, payload) -> dict`. (Tests use this to avoid the network.)

Either way the returned dict must **validate against the schema** for the gate kind, or
the responder raises:

- **`kind == "moments"`** → must validate as **`MomentDecision`**. Request `payload`
  fields available to your prompt: `source_id`, `duration`, `transcript`,
  `signal_peaks`, `language`, `guidance`. Return `{"source_id": <copy from the request
  payload's `source_id`>, "picks": [{start, end, reason, transcript_excerpt,
  signal_score}, ...]}`. The responder stamps `request_id` automatically. **Both
  `source_id` and `request_id` are required by `MomentDecision`** (neither has a default),
  so a response that omits `source_id` fails validation and is silently treated as "no
  response yet" — the responder injects *only* `request_id`, not `source_id`.
- **`kind == "captions"`** → must validate as **`CaptionSet`**. Request `payload` fields:
  `clip_id`, `surfaces` (list of `{surface, platform}`), `transcript_excerpt`,
  `language`, `guidance`. Return `{"items": [{surface, caption, hashtags}, ...]}` answering
  **every** requested surface. The responder stamps `request_id` automatically
  (`CaptionSet` needs only `request_id` + `items`).

The `guidance` in both payloads is the verbatim text of `context.md` — your prompt
template should pass it through so the model follows the creative brief.

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
POSTs to `https://backend.blotato.com/v2/posts` with bounded retry/backoff on 429/5xx and
raises loudly on 401.)

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

- **(a) Submitting-recovery / reconcile step.** A post stranded in `submitting` by a
  mid-publish crash is **never re-driven** — by design, to avoid double-posting a live
  post. A reconcile step should **poll whether the submit actually landed** and either
  promote it `→ published` or reset it `→ queued`/`failed`. (`publish_due` iterates only
  `queued`; the `submitting` orphan is left for this future step.)
- **(b) Externalize the tunable lists to config / `context.md`.** The brand-risk
  anti-pattern lists (`caption._OFFBRAND_EN` / `_OFFBRAND_AR`) and the lift weights
  (`track._W`) are hardcoded. Moving them to config/`context.md` lets the operator tune
  the HOLD gate and the optimization target without a code change.
- **(c) REST backoff jitter + retry on network errors.** REST backoff is plain
  exponential and **un-jittered** (`1→2→4→8`, thundering-herd risk), and a `requests`
  `Timeout`/`ConnectionError` is **not** caught in `BlotatoRestPoster.publish` and is
  **not** folded into the bounded-retry ladder — it propagates to `publish_due`, whose
  `except Exception` lands the post in **`failed`** (retryable on the next run) rather than
  `error`. Add jitter and retry transient network errors inside the ladder.
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
