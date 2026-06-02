# MOH FLOW FAN OPS

An autonomous fan-account engine for **Moh Flow** — a bilingual (EN/AR) rapper. It ingests
his own videos, **decides which moments are worth posting** (transcript + audio/scene signals
→ an agent decision that records *why*), cuts **platform-ready vertical clips** with
**agent-written captions**, and **cross-posts to every fan account on every platform** via
[Blotato](https://blotato.com), then pulls real performance back to **make more of what
works**.

The whole system is one deterministic stage DAG with two agent gates. Everything between the
gates (ingest, transcribe, signal-detect, render, crosspost, publish) is automatic and
crash-safe; the gates (which moments, which captions) are answered by a human or an LLM.

---

## The unit chain

Four content-addressed units flow through one git-versioned JSON ledger
(`MohFlow-FanOps/00_control/ledger.json`):

```
Source ──▶ Moment ──▶ Clip ──▶ Post
```

- **Source** — one of Moh's own videos, identified by its **content SHA-256** (not its path,
  so the same file dropped twice is one source). Probed for width/height/duration at ingest,
  transcribed (Whisper), and signal-scanned (ffmpeg). Audio-only files are rejected — this is
  a *video*-clip pipeline.
- **Moment** — a span `(start, end)` inside a source that the agent judged worth posting, with
  a **required `reason`** and the transcript excerpt. Moments are content-addressed by their
  timestamps, so re-deciding a source upserts the set and cascade-deletes dropped lineage.
- **Clip** — a rendered, platform-ready file (e.g. `1080×1920` 9:16) cut from a moment with a
  frame-accurate ffmpeg seek + a safe reframe chosen from the *probed* source dimensions. One
  clip per distinct aspect the active platforms need.
- **Post** — one clip fanned out to one `(account, platform)` surface, in *that* platform's
  aspect, with that surface's caption, a deterministic per-surface schedule time, and the
  resolved **numeric** Blotato `account_id`.

The ledger is the single source of truth. Each `advance()` pass runs inside **one
`Ledger.transaction`** that holds an `fcntl.flock` across the **whole load → mutate → save**
cycle, and writes are **atomic** (temp file + `os.replace`). The lock is acquired *before* the
load (not just around the final write), so two overlapping cron runs can never lose each
other's updates (the old save()-only lock left a lost-update window — a published post could
vanish, or a `submitting` post revert into a double-post). A second live run is excluded for
the pass and gets a typed `LockBusyError` (one-line message, no traceback), never a silent
overwrite. Every unit has an `error` state for per-unit quarantine — one bad source/moment/clip
is skipped, never wedging the whole pass.

---

## The agent-gate model + the two responders

Two steps are **generative** and cannot be hard-coded: deciding the moments
(`decide_moments`) and writing the captions (`write_captions`). They cross a **file contract**
under `MohFlow-FanOps/04_agent_io/requests/`:

1. Code writes `<kind>__<key>.request.json`, stamped with a fresh `request_id`.
2. The agent writes `<kind>__<key>.response.json`, echoing that `request_id`.
3. Code validates the response against its Pydantic schema **and** checks the id matches the
   latest request (a stale or torn response can never be applied), then resumes.

Who writes the response files is the responder (`FANOPS_RESPONDER`):

- **Manual responder** (default) — a human (or an external cron) writes the response files.
  Use `fanops respond` as a no-op placeholder, or hand-edit / script the JSON.
- **LLM responder** (`FANOPS_RESPONDER=llm`) — answers the gates autonomously by calling the
  Claude Code CLI in headless mode (`claude -p`, via `src/fanops/llm.py`) with a committed
  prompt template (`src/fanops/prompts.py`) and the gate's exact JSON schema, validates the
  model's output against `MomentDecision` / `CaptionSet`, and writes the response. Requires the
  `claude` binary on `PATH` (authenticated); each gate is quarantined so one bad/failed request
  logs and stays pending without halting the rest. The model callable is injectable for tests
  (no network/subprocess). See `RUNTIME.md` → *the autonomous LLM responder*.

Either way the gate is the same files on disk, so you can mix and match (LLM for captions, a
human spot-check for moments) without changing the pipeline.

---

## Install

Requires **Python 3.12** (`>=3.12,<3.14`), **ffmpeg ≥ 6**, the **Whisper** CLI, and
**yt-dlp** (URL ingest, pulled in as a dependency). For the **autonomous LLM responder**
(`FANOPS_RESPONDER=llm`) you also need the **`claude`** CLI (Claude Code) on `PATH`, invoked
headlessly (`claude -p --bare`). **Auth caveat (important):** because it uses `--bare` for
cron-safety, Anthropic auth is **strictly `ANTHROPIC_API_KEY`** — `--bare` does **not** read an
OAuth/`claude login`/keychain session. Export `ANTHROPIC_API_KEY` in the environment that runs
`fanops` (a `claude login` alone will NOT work and every gate will fail "Not logged in"). Not
needed for the default `manual` responder.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,transcribe]'      # dev = pytest+pytest-mock; transcribe = openai-whisper
brew install ffmpeg                      # macOS; ffmpeg ≥ 6 (ffprobe ships with it)
```

The `whisper` CLI lands on PATH with `openai-whisper`. The default model is **`turbo`**
(downloads ~1.5 GB on first run). For offline / air-gapped / CI hosts, pin a smaller model
that's already cached with `FANOPS_WHISPER_MODEL` (e.g. `tiny`, `base`, `small`); if the
requested model isn't downloadable, the transcriber falls back to whatever checkpoint is
already on disk rather than failing the run.

`say` (macOS) or `espeak` (Linux) is only needed for the real-tooling E2E test, which
synthesizes a spoken sample.

---

## The daily loop

```bash
fanops advance                       # run the DAG; pauses at the moment + caption gates
# → answer the gates: either the LLM responder, or write the response JSON yourself
fanops respond                       # (LLM responder) drain all pending gates
fanops advance                       # resume: render clips, crosspost, publish DUE posts
# … on a cadence (e.g. nightly):
fanops reconcile                     # resolve stranded submitting/needs_reconcile posts (GET /v2/posts/:id)
fanops track  --window 30d           # pull real metrics back onto published posts
fanops adjust                        # amplify winners (more moments like them), retire losers
# … weekly:
fanops gc --keep-days 30             # reclaim disk: drop local .mp4s of retired/analyzed clips
```

**Publishing uses the real wall-clock now.** `--base-time` is the **schedule anchor** for
the non-synchronized per-surface timing, *not* the publish cutoff — `publish_due` only submits
posts whose computed `scheduled_time` is `<= now`. (Set `--base-time` in the past to publish
immediately; leave it at "today" to schedule across the day.)

### Unattended loop

```bash
fanops run --base-time <T>           # responder.answer → advance, repeated until no progress
```

`fanops run` is the cron/launchd entry point. It degrades cleanly: per-unit failures are
quarantined inside `advance`, and a fatal auth error (bad/missing `BLOTATO_API_KEY`, 401)
halts the loop instead of burning the queue.

**The learning loop now closes inside `run`.** After the respond→advance loop converges,
`run` runs one `track → adjust` pass (`pull_metrics → classify_outcomes → amplify → retire`)
in its own lock-safe `Ledger.transaction`, so an unattended deployment makes more of what
works without a separate `track`/`adjust` cron. It is **guarded to live backends + a key** by
the exact reconcile guard (`cfg.poster_backend != "dryrun" and cfg.blotato_api_key`): in the
default **dryrun** backend the pass is **never entered** (no metrics fetch, no amplify), so the
offline pipeline is unchanged. Any hiccup in the pass is logged (`learn error`) and swallowed —
it can never crash the unattended run (exit stays 0). Amplification is bounded per source (see
*The feedback loop* → the `max_amplify_per_source` budget) so the autonomous responder can't grow
one source endlessly.

**Heartbeat / dead-man's-switch.** Every `run` and `advance` emits one heartbeat line — to
stdout as JSON and appended to `07_reports/run.log` — so an **external** monitor (a `cron`+`mail`
job, or PagerDuty) can tell *alive-but-idle* from *cron is dead*:

```json
{"heartbeat": "2026-06-02T10:03:24.582671+00:00", "fanops_version": "0.3.0", "published_in_run": 0, "last_published_age_hours": null}
```

- **`heartbeat`** — a **live** ISO-8601 UTC timestamp; it changes every invocation. A monitor
  that diffs consecutive run.log lines and sees the **same** `heartbeat` (or no new line) knows
  the **cron itself is dead** — the process never ran — which a frozen-but-present file would hide.
- **`fanops_version`** — the running build, so a stale binary is visible in the alert stream.
- **`published_in_run`** — posts published **this run** (a set-difference delta, not the cumulative
  total). `0` across N consecutive runs is the signal for *the pipeline is stuck*: alert on
  "0 published in N runs".
- **`last_published_age_hours`** — hours since the most-recent published post's scheduled time
  (2 dp; `null` when nothing has published or the time is unparseable). Alert on
  "last post age > threshold".

Because the responder runs `claude --bare` (which ignores OAuth — auth is **strictly**
`ANTHROPIC_API_KEY`, see *Install*), a responder that is **running but silently unauthed** (the key
was never exported) clears no gates and publishes nothing. The dead-man's-switch is how you catch
it: `published_in_run` stays `0` forever **and** the digest's "Pending agent gates" section names
the unanswered gates — the heartbeat says the cron is alive, the delta + the digest say it is making
no progress, so the human knows to check the key, not the cron.

### Full command list

| Command | What it does |
|---|---|
| `fanops status` | counts (sources/moments/clips/posts/published/failed/needs_reconcile) + pending gates + backend |
| `fanops ingest` | catalogue new drops in `01_inbox` (SHA-256 identity, PII filename exclusion) |
| `fanops pull <url>` | yt-dlp a URL into the inbox, then ingest |
| `fanops advance [--base-time T]` | run the DAG to the next gate / completion |
| `fanops respond` | responder drains pending agent gates (manual = no-op) |
| `fanops reconcile` | resolve stranded `submitting`/`needs_reconcile` posts via `GET /v2/posts/:id` (needs a key; id-less posts stay parked for human reconcile) |
| `fanops track [--window 30d]` | pull metrics; mark posts analyzed with a whitelisted lift score |
| `fanops adjust [--winner-pct 0.3] [--retire-pct 0.2] [--lift-floor 20.0]` | amplify winners / retire losers |
| `fanops gc [--keep-days 30]` | delete local clip files of retired/analyzed clips older than N days |
| `fanops resolve <post_id> <published\|failed> [--url U]` | operator escape hatch: force a post stranded in `needs_reconcile`/`submitting` to ground truth after a hand-check (sets state; `--url` records the live post URL on `published`) |
| `fanops unhold <clip_id>` | clear a brand-risk HOLD after human review — resets `held` and re-enters the clip into the caption gate (`captions_requested`); no ledger hand-edit |
| `fanops retry-source <source_id>` | requeue a quarantined (`error`) source from the top — back to `catalogued` and forces a real re-transcribe |
| `fanops retry-metrics <post_id>` | re-pull metrics for a `published` post on the next `track` pass (no-op flip; exits 2 if the post isn't published) |
| `fanops digest` | rewrite the human-readable ledger digest (incl. a `## Pending agent gates` section naming each unanswered gate by kind+key) |
| `fanops run [--base-time T]` | unattended: respond + advance until stable, then a live-only `track`+`adjust` learning pass; emits a heartbeat line every run |

The four **recovery verbs** (`resolve`, `unhold`, `retry-source`, `retry-metrics`) are the
operator's manual-intervention surface for the states the automatic pipeline cannot resolve on
its own (an ambiguous post fate, a human-cleared brand-risk hold, a quarantined source, a post
whose metrics never landed). Each is a tight, local-only `Ledger.transaction` — no network — and
exits 2 with a one-line message if the target id doesn't exist or is in the wrong state. See
`RUNTIME.md` → *Recovery verbs*.

**Adjust knobs.** `--winner-pct` = top fraction of analyzed posts (by lift) to amplify;
`--retire-pct` = bottom fraction *eligible* to retire; `--lift-floor` = a post is only retired
if it's both bottom-ranked **and** below this absolute lift. The floor decouples retirement
from winners so a single hit doesn't drain the catalogue.

**Per-source amplify budget.** `amplify` caps how many times a single source can be re-mined at
`max_amplify_per_source` (default **3**), tracked on `src.meta["amplify_count"]` (a missing key
counts as 0). At/over the cap the source is skipped entirely — no fresh moment request, no state
flip — so the autonomous learning loop inside `fanops run` can't grow one viral source without
bound. Only a *successful* amplify increments the count.

---

## The three human-only steps

Everything is automated except the parts only a human can do:

1. **Create the fan accounts** on each platform.
2. **Connect each account in Blotato**, copy its **numeric** `account_id`, and paste it into
   `MohFlow-FanOps/00_control/accounts.json` with `"status": "active"`:
   ```json
   {"handle": "@mohflow.edits", "account_id": "98432",
    "platforms": ["instagram", "tiktok"], "status": "active"}
   ```
   (An empty `account_id` on an active account is caught before a run — it must never reach
   Blotato. A hand-edit typo that makes `accounts.json` or `ledger.json` unparseable is also
   caught up front: any command exits non-zero with a one-line `accounts.json invalid: <reason>`
   / `ledger.json invalid: <reason>` instead of a stack trace.)
3. **Set the poster + key** in `.env` (see `.env.example`): `FANOPS_POSTER=rest` (or `mcp`)
   and `BLOTATO_API_KEY=...`. Until then the default `dryrun` poster writes the exact payload
   it *would* send to `05_scheduled/` and posts nothing, so the whole pipeline runs offline. It
   stamps a synthetic `dryrun_<post_id>` submission id (mirroring the real `postSubmissionId`),
   so `track` → `adjust` can be exercised end-to-end offline by feeding metrics rows keyed on it.

---

## Integration checkpoints to confirm before going live

The **metrics** endpoint shape (`post/metrics.py`, `track.py`) is the one remaining
`INTEGRATION CHECKPOINT` to confirm against the live API before relying on the learning loop:
which engagement fields Blotato actually exposes (if saves/shares/retention are unavailable,
re-weight `track._W` on the fields that are).

**Confirmed against the live Blotato MCP tool schemas (2026-06-02), no longer checkpoints:**

- the **submission-id** response key **is `postSubmissionId`** (`blotato_create_post` returns it;
  `blotato_get_post_status` takes it) — `post/blotato_rest.py`, `post/blotato_mcp.py`. A 2xx with
  no recognizable id is parked **`needs_reconcile`** (never `failed`), and the posters also accept
  `submissionId` / `id` / nested `data.*` as defensive aliases.
- the **`create_presigned_upload_url`** contract returns `presignedUrl` + `publicUrl` — `post/media.py`.
- the **MCP** tool name (`blotato_create_post`) + flat arg shape — `post/blotato_mcp.py`, `post/payload.py`.
- the **status enum** `in-progress → published | scheduled | failed`, with the live-post URL under
  **`publicUrl` on `get_post_status`** but **`postUrl` on `list_posts`** (a real API divergence;
  FanOps reads the URL only from `get_post_status` — at `reconcile.py` — so it reads the right key).
- **No idempotency key** on `POST /v2/posts` (body accepts only `post` / `scheduledTime` /
  `useNextFreeSlot`), and a publish timeout can produce a duplicate post. So the REST poster does
  **not** blindly retry an ambiguous failure: a `5xx` or a network timeout *after the request body
  was sent* parks the post in **`needs_reconcile`** (it may already be live) instead of re-POSTing,
  and the digest (plus the `fanops status` / `fanops run` count) surfaces it for a human to verify
  via `GET /v2/posts/:id` before any resubmit. Only a `429` (rejected pre-processing, so definitely
  not created) is retried, with jittered backoff. Every crossposted post is also stamped at birth
  with a stable **client idempotency token** (`submission_id = f"fanops_{_hash('idemp', post.id)}"`),
  so an ambiguous publish is always reconcilable; a real `postSubmissionId` from the response
  overwrites it. See `post/blotato_rest.py`, `crosspost.py`.

A successful **data-returning** live verification (and any live test post) is still pending valid
Blotato auth + an operator-named throwaway test account — `blotato_create_post` publishes to a real
account with no dry-run, so it is never fired autonomously.

Confirm them by running the live smoke test **manually** (it schedules a post far in the
future so it can be deleted before it ever publishes):

```bash
BLOTATO_API_KEY=… BLOTATO_SMOKE_ACCOUNT_ID=… \
  pytest -q -m integration tests/integration/test_blotato_smoke.py
```

`test_payload_matches_confirmed_rest_shape` runs offline and locks the REST body shape
(confirmed vs help.blotato.com) so a regression is caught even without keys.
`test_live_auth_and_schedule` is skipped unless both env vars are set.

---

## Tests

```bash
source .venv/bin/activate                  # required: bare pytest mis-reports the mocker fixture
python -m pytest -q -m "not integration"   # the unit suite (hermetic, fast)
python -m pytest -q -m integration         # real-tooling E2E + Blotato smoke
```

The unit suite is fully mocked (no ffmpeg/whisper/network). The **integration** suite runs the
pipeline on **real tooling**: a `say`/`espeak`-synthesized spoken clip → **real Whisper
transcript** → **real ffmpeg** `1080×1920` render → dry-run publish — and skips cleanly when
ffmpeg/whisper/TTS aren't present.

**CI (`.github/workflows/ci.yml`)** runs the unit suite on every push/PR, plus a job that
installs the toolchain (ffmpeg + espeak + whisper, with the `tiny` checkpoint cached) and runs
the integration suite with **`FANOPS_REQUIRE_E2E=1`** — which turns the E2E's "tooling absent"
skip into a **failure**. So the real-tooling path is guaranteed to actually execute in CI, never
silently skipped (audit H10). Locally the skip stays graceful (set `FANOPS_REQUIRE_E2E=1` to opt
into the strict behavior).

---

## Opsec / PII guardrails

Some behaviors are **deliberate product decisions**, recorded so they're explicit, not buried
in code:

- **Non-synchronized, per-persona multi-account posting** and the **subtle, non-synchronized
  artist `@mention`** are intentional (opsec + platform fit), not bugs.
- **PII exclusion is filename-only** — necessary but *not sufficient*. A misnamed private file
  can slip through, so a **human reviews held / odd clips** before anything posts. Captions are
  also held on EN+AR brand-risk patterns (begging, label linkage, "link in bio"), on a declared
  caption **language** that doesn't match the source language (base IETF subtag, so `en-US`==`en`),
  and on any caption targeting a **surface key not in the requested set** (held with a reason
  naming the bad surface). Moment picks with non-finite (NaN/Inf) timestamps are rejected outright.

The recorded operator **risk-acceptance** for coordinated multi-account amplification lives in
`MohFlow-FanOps/00_control/RISK.md`. Day-to-day operations, the two model seams, and the
deferred-work backlog live in `MohFlow-FanOps/00_control/RUNTIME.md`.
