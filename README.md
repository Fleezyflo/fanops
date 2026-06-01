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
(`FANOPS_RESPONDER=llm`) you also need the **`claude`** CLI (Claude Code) on `PATH` and
authenticated — it is invoked headlessly (`claude -p`) and reuses your existing Claude Code
auth (no separate API key). Not needed for the default `manual` responder.

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
| `fanops digest` | rewrite the human-readable ledger digest |
| `fanops run [--base-time T]` | unattended: respond + advance until stable |

**Adjust knobs.** `--winner-pct` = top fraction of analyzed posts (by lift) to amplify;
`--retire-pct` = bottom fraction *eligible* to retire; `--lift-floor` = a post is only retired
if it's both bottom-ranked **and** below this absolute lift. The floor decouples retirement
from winners so a single hit doesn't drain the catalogue.

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

A few Blotato-side contracts are marked **`INTEGRATION CHECKPOINT`** in the code and should be
confirmed against the live API before the first real post:

- the **`/media/uploads`** presign contract (`presignedUrl` / `publicUrl`) — `post/media.py`
- the **submission-id** response key (we expect **`postSubmissionId`**) — `post/blotato_rest.py`
- the **metrics** endpoint shape — `post/metrics.py`, `track.py`
- the **MCP** tool name + arg shape (the REST body is nested; the MCP args are flat) —
  `post/blotato_mcp.py`, `post/payload.py`

**Confirmed (no longer a checkpoint):** the Blotato v2 `POST /v2/posts` contract has **no
idempotency key** (the body accepts only `post` / `scheduledTime` / `useNextFreeSlot`), and
Blotato's own docs note a publish timeout can produce a duplicate post. So the REST poster does
**not** blindly retry an ambiguous failure: a `5xx` or a network timeout *after the request body
was sent* parks the post in **`needs_reconcile`** (it may already be live) instead of re-POSTing,
and the digest (plus the `fanops status` / `fanops run` count) surfaces it for a human to verify
via `GET /v2/posts/:id` before any resubmit.
Only a `429` (rejected pre-processing, so definitely not created) is retried. See `post/blotato_rest.py`.

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
  also held on EN+AR brand-risk patterns (begging, label linkage, "link in bio").

The recorded operator **risk-acceptance** for coordinated multi-account amplification lives in
`MohFlow-FanOps/00_control/RISK.md`. Day-to-day operations, the two model seams, and the
deferred-work backlog live in `MohFlow-FanOps/00_control/RUNTIME.md`.
