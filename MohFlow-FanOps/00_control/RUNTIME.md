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
fanops discover <folder>
fanops intake
fanops ingest
fanops pull <url>
fanops advance [--base-time T]
fanops respond
fanops reconcile
fanops track  [--window 30d]
fanops adjust [--winner-pct 0.3] [--retire-pct 0.2] [--lift-floor 20.0]
fanops amplify-variants                 # v3 variant-gated amplification (inert unless FANOPS_VARIANT_AMPLIFY=1)
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
| `FANOPS_POSTER` | `dryrun` (default) \| `postiz` \| `zernio` | Legacy global publish backend hint. **Per-channel routing in `accounts.json` is the source of truth** — set backends in the Studio Go-Live tab. `dryrun` never hits the network. |
| `FANOPS_LIVE` | `0` (default) \| `1` | Global live/dryrun switch. Set **only** via Studio Go-Live (`golive.go_live`). When unset/`0`, every publish path halts at the dryrun boundary even if a channel is routed live. |
| `POSTIZ_URL` | URL | Base URL of your Postiz instance (required when any channel routes to `postiz`). |
| `POSTIZ_API_KEY` | string | Postiz public API key (`x-api-key`). Required when any channel routes to `postiz`. |
| `ZERNIO_API_KEY` | string | Zernio API key. Required when any channel routes to `zernio` (TikTok). |
| `FANOPS_RESPONDER` | `manual` (default) \| `llm` | `manual` = a human/cron writes the response files; `llm` = the LLM responder answers gates via plain `claude -p` (the operator's existing Claude subscription/login — NO API key). With `llm`, `advance`/`run` **hard-fail (exit 2) unless `claude` is on PATH** — the cutover-safety preflight (see below); the operator must `claude login` once on the host. |
| `claude` (CLI, logged in) | — | Required for `FANOPS_RESPONDER=llm`. The responder shells plain `claude -p` (NOT `--bare`), so it uses the host's `claude login` session — **no `ANTHROPIC_API_KEY` needed**. `claude` absent with `llm` ⇒ `advance`/`run` exit 2 (the silent-zero-output guard). `--strict-mcp-config --allowedTools ""` keep it a clean no-tool/no-MCP generator. (`ANTHROPIC_API_KEY` is NOT required; if set, `claude` will use it, but the subscription login is the supported path.) |
| `FANOPS_ARTIST_NAME` | string (optional) | Artist **display name** used as the YouTube title fallback when a post has no explicit title (audit h). Default `"Moh Flow"` (unchanged). Distinct from the `@mohflow` caption mention (`tagging.ARTIST_HANDLE`). |
| `FANOPS_BURN_SUBS` | `1`/`true`/… (default **ON**) \| `0`/`false`/`no`/`off` | Burn the transcript-derived subtitles + top-third hook into each rendered clip. **DEFAULT ON** — an unset env burns subs, so the feature is live with no operator action; only the explicit off-words `0`/`false`/`no`/`off` (case-insensitive) disable it. **Fail-open**: if this ffmpeg lacks the text filter or the source has no transcript, the clip still renders (plain), logging one `subs_skipped` line. Requires a **text-capable ffmpeg (libass)** — see the note below. |
| `FANOPS_SUBTITLE_FONT` | string (optional) | Font face for the `.ass` subtitles. Default `"Arial Unicode MS"` — an Arabic-capable face so RTL captions render. Override if the host lacks that font or you prefer another Unicode/Arabic typeface. |
| `FANOPS_CREATIVE_VARIATION` | `1`/`true`/`yes`/`on`/unset (default **ON**) \| `0`/`false`/`no`/`off` | Per-account creative variation (M3d: now the system's default, per-account differentiation is its purpose). When ON, each active account gets a genuinely different caption + burned-in on-screen hook per clip (+ its own length/framing cut under M2): the caption agent returns a per-surface `hook`, and crosspost burns it onto a per-account cut via the overlay pass, stamping `Post.variant_key`/`variant_hook`. The digest's "Lift by variant" section attributes which creative wins (no auto-propagation). **DEFAULT ON** — set `FANOPS_CREATIVE_VARIATION=0` to pin the legacy fan-to-all single-clip path (the OFF code path is retained, §7 firewall). **Fail-open**: no hook / no libass text filter ⇒ today's shared-clip behavior. Requires a **text-capable ffmpeg (libass)** for the burn. |
| `FANOPS_ACCOUNT_CASTING` | `1`/`true`/`yes`/`on`/unset (default **ON**) \| `0`/`false`/`no`/`off` | Account-First per-account MOMENT casting (Face 3). When ON, each active account is cast up to `FANOPS_CAST_PICK_BUDGET` of its best persona-fit moments (LLM-driven selection, bounded by the batch target); crosspost then fans a cast moment ONLY to its accounts (an uncast moment falls through to fan-to-all). **DEFAULT ON** — set `FANOPS_ACCOUNT_CASTING=0` to restore the legacy fan-to-all path. **Fail-open**: any casting error ⇒ the moment fans to all (today's behavior). |
| `FANOPS_VARIANT_LEARNING` | `1`/`true`/`yes`/`on` (default **OFF**) \| unset/`0`/`false`/… | Creative variation **v2** — closes the A/B loop on the caption-bias side (backlog j follow-up). When ON, `request_captions` asks the gated scorer (`variant_learning.best_hooks`) for each surface's trustworthy winning hook and appends a `learned_hooks` style cue to the caption request (`caption_prompt` renders it as "lean toward this STYLE, do NOT copy verbatim"), so the next caption is biased toward what already won. INDEPENDENT of `FANOPS_CREATIVE_VARIATION`. **DEFAULT OFF** — opt-in. **Fail-open**: gate not met / any error / old ledger ⇒ no hint, today's behavior (a learning failure can never block a caption or hold a clip). **Reversible**: flip OFF and the very next request reverts (nothing persisted but this opt-in hint). Touches **none** of the amplify/`_delete_moment_cascade` path (C1) — auto-propagation into amplify is still out of scope. The digest's "Lift by variant" section shows each surface's loop state ("learning ACTIVE" vs "gathering data") via the same scorer. |
| `FANOPS_VARIANT_MIN_POSTS` | int (default **3**) | Trust-gate part 1 for `FANOPS_VARIANT_LEARNING`: the minimum analyzed posts a hook variant must carry before its measured lift is trusted enough to bias the next caption. The early-noise guard (with 2 accounts, acting on 1–2 data points is the noise-amplification trap). A non-int value falls back to the default. |
| `FANOPS_VARIANT_MIN_GAP` | float (default **10.0**) | Trust-gate part 2 for `FANOPS_VARIANT_LEARNING`: the leader's mean `lift_score` must beat the runner-up's by at least this margin to emit a hint (same lift_score scale as the HOLD-gate lift floor — a real margin, not noise). Below it ⇒ no hint, the loop stays open for that surface until data accrues. A non-float value falls back to the default. |
| `FANOPS_VARIANT_AMPLIFY` | `1`/`true`/`yes`/`on` (default **OFF**) \| unset/`0`/`false`/… | Creative variation **v3 — variant-gated AMPLIFICATION** (the auto-propagate follow-up, backlog j). The **first** feature to touch the amplify/`_delete_moment_cascade` path (audit C1), so it is the **KILL SWITCH** and **DEFAULT OFF**. When ON, a per-account hook variant that has earned a **SUSTAINED** win auto-amplifies its source via the existing `adjust.amplify` (reopen source → mine more moments), injecting the winning hook as `extra_guidance` so new moments inherit the winning creative. Runs as a SEPARATE, independently-guarded pass in `fanops run` (its own flag + the live-backend+key guard + its own try/except), and is also runnable via **`fanops amplify-variants`**. **AMPLIFY-ONLY** — it never calls `retire`/`_delete_moment_cascade`/`set_*_state(retired)` (AST-proven), so a wrong "this won" signal can never delete/unpublish LIVE content; the C1 cascade's live-lineage preservation is inherited unchanged (the amplify it triggers retires the already-posted winning *moment* with its live post preserved, exactly as the v1/v2 learn-loop already does). **Fail-SAFE**: flag off / gate unmet / any error ⇒ ledger CONTENT byte-identical (the streak counters are inert when off). **Reversible**: flip OFF and v3 stops acting immediately. The digest's "Variant amplification" section shows each surface's streak state ("amplified" / "building streak (n/MIN)" / "gathering data"). |
| `FANOPS_VARIANT_AMPLIFY_MIN_POSTS` | int (default **8**) | v3 trust-gate part 1 (stronger than v2's `FANOPS_VARIANT_MIN_POSTS`=3): the winning hook must carry at least this many analyzed posts before its win is trusted enough to AMPLIFY (a far more consequential act than v2's caption-bias). A non-int value falls back to the default. |
| `FANOPS_VARIANT_AMPLIFY_MIN_GAP` | float (default **25.0**) | v3 trust-gate part 2 (stronger than v2's `FANOPS_VARIANT_MIN_GAP`=10): the winner's mean `lift_score` must beat the runner-up's by at least this margin. A non-float value falls back to the default. |
| `FANOPS_VARIANT_AMPLIFY_MIN_STREAK` | int (default **3**) | v3 trust-gate part 3 — the core NEW safety property (no v2 analogue): the SAME hook must have led the gate across at least this many DISTINCT evidence windows (new analyzed-post batches) before amplifying. `≥ 2` means "never act on a single window". A non-int value falls back to the default. |
| `FANOPS_VARIANT_UCB` | `1`/`true`/`yes`/`on` (default **OFF**) \| unset/`0`/`false`/… | Creative-variation **v3**: select the deterministic **UCB1 bandit** (`variant_learning.ucb_rank`) as the OWN-surface caption-bias allocator instead of v2 gated-greedy `best_hooks` (balances explore vs exploit; never silent once any variant data exists). INDEPENDENT of `FANOPS_VARIANT_LEARNING` (still the master gate — UCB is inert if learning is OFF). Does **NOT** affect amplify (keeps the `best_hooks` floor — the exploratory bandit can never become a C1 amplify authorization; tested + mutation-proven). **DEFAULT OFF** — opt-in; unset/empty/other ⇒ v2 greedy behavior. |
| `FANOPS_VARIANT_UCB_C` | float (default **√2 ≈ 1.414**) | UCB exploration weight `c` in `score = mean_lift + c·sqrt(ln N / n)`. `0` = pure greedy (mean decides); larger = more exploration of under-sampled hooks. Negative/unparseable ⇒ default. |
| `FANOPS_VARIANT_TRANSFER` | `1`/`true`/`yes`/`on` (default **OFF**) \| unset/`0`/`false`/… | Cross-account / cross-surface learning **transfer** (v2 follow-up, backlog j). When ON, `request_captions` may bias a **COLD** recipient surface (no trustworthy winner of its own yet) toward a hook STYLE proven on **other same-platform** surfaces — fed in as `learned_hooks_transferred` (a SEPARATE, weaker payload key than v2's `learned_hooks`; `caption_prompt` renders it BELOW the own-surface block as a lighter "working elsewhere on this platform — don't copy verbatim" nudge). Gate is STRICTER than v2's: a style must be the v2-gated winner on `≥ FANOPS_VARIANT_TRANSFER_MIN_DONORS` (default **2**) distinct donor surfaces, capped at `FANOPS_VARIANT_TRANSFER_MAX_HOOKS` (default **2**). INDEPENDENT of `FANOPS_CREATIVE_VARIATION`/`FANOPS_VARIANT_LEARNING`. **DEFAULT OFF** — opt-in. **Fail-open**: flag off / no qualifying donor / no accounts registry / any error / old ledger ⇒ no prior, today's behavior. **Anti-homogenization**: a surface with its OWN winner borrows nothing (own-wins); transfer is STYLE not verbatim; persona-ranked (deterministic). Touches **none** of the amplify/`_delete_moment_cascade` path (C1) — enforced by the isolation tests. The digest's "Lift by variant" section shows a cold surface as "borrowing platform signal". |
| `FANOPS_VARIANT_TRANSFER_MIN_DONORS` | int (default **2**) | Transfer gate (stricter than v2's): a hook style transfers to a cold recipient only if it is the v2-gated winner on at least this many DISTINCT other same-platform donor surfaces. One surface's local win is not yet a platform-level signal. A non-int value falls back to the default. |
| `FANOPS_VARIANT_TRANSFER_MAX_HOOKS` | int (default **2**) | Cap on how many borrowed styles a single caption request may carry (anti-homogenization — even a popular style-cluster cannot flood one caption). A non-int value falls back to the default. |
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

- **`FANOPS_RESPONDER=llm` but `claude` is not on PATH (or not logged in)** — the responder
  shells plain `claude -p` (your existing subscription/login; no API key). With no `claude`
  binary it would fail every gate, clear nothing, and publish nothing **without crashing**
  (the preflight hard-blocks the binary-absent case; a logged-out `claude` surfaces via the
  `run halted`/heartbeat path). This is the guaranteed-silent failure the heartbeat/dead-man's
  switch is designed to *detect after the fact*; the preflight catches it **up front** instead.
- **`FANOPS_POSTER=postiz` but `POSTIZ_URL` / `POSTIZ_API_KEY` unset** — publishing would fail auth.
- **Any channel routed to `zernio` but `ZERNIO_API_KEY` unset** — TikTok publish would fail auth.

The **default `dryrun` + `manual` config (no keys) trips neither and passes cleanly (exit 0)**,
so the offline pipeline is unaffected. This is a *safety* feature: a misconfigured live cutover
now fails loudly and immediately rather than running green-but-empty until a monitor notices.

---

## Content discovery + review intake (pre-ingest, optional)

Before the pipeline ever runs, you can scan a folder of your own footage for candidates and
**approve only the keepers** — so rejects never cost a clip/transcribe/LLM cycle. This stage is
**cheap by design**: a filesystem scan + one `ffprobe` + one thumbnail frame per candidate, and
**no transcription, no LLM, no signal detection** (that expensive work happens only after intake,
on approved items). It does not touch the existing pipeline — it only decides what reaches
`01_inbox/`.

1. **`fanops discover <folder>`** — scans `<folder>` for media files (the same media-extension
   + **PII-filename exclusion** as `ingest`, so a `passport scan.jpg` or `tax return.mp4` is never
   listed). For each **new** candidate it writes a `<id>.jpg` thumbnail + a cheap-metadata entry
   (bytes, mtime, dimensions, duration) into `00_review/manifest.json`. The originals are **not**
   copied (least cost). Re-scanning is idempotent and **dedups against the ledger** — content whose
   SHA-256 is already a known Source is skipped, so you never re-review what's already ingested. An
   unknown/empty `<folder>` exits 2 with a one-line message (no traceback).
2. **Review `00_review/` in Finder** and **move the keeper thumbnails into `00_review/approved/`**.
   The thumbnail filename is the entry id, so moving it is the approval — drop the rejects, keep the
   winners. (ffmpeg absent ⇒ the candidate is still listed from metadata, just without a thumb.)
3. **`fanops intake`** — for each approved thumbnail, resolves its original via the manifest and
   **copies that original into `01_inbox/`**. Only approved content enters the pipeline; rejects
   never do. Intake is **idempotent** (an already-intaken entry is recorded in
   `00_review/intaken.json` and not re-copied) and **missing-safe** (a stale/vanished original is
   reported as `missing`, never a crash).
4. **`fanops advance` / `fanops run`** then clips + captions + schedules the approved originals
   through the normal loop below.

```bash
fanops discover ~/Footage/raw   # scan a folder -> thumbnails + metadata into 00_review/ (CHEAP: no LLM)
# … browse 00_review/ in Finder, move keepers into 00_review/approved/ …
fanops intake                   # copy ONLY the approved originals into 01_inbox/
fanops advance                  # now the normal pipeline clips/captions/schedules them
```

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
(`cfg.is_live and cfg.live_route_exists`, matching the live reconcile guard at `pipeline.py`): in the default **dryrun** backend (or with no key) the
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

**Catching a silently-unauthed responder.** Because the responder shells plain `claude -p` using
the host's `claude login` session (see *the autonomous LLM
responder* below) — a responder that is **running but logged out** (`claude login` was never run
on the host) fails every gate with "Not logged in", clears nothing, and publishes nothing, **without
crashing** (each gate is quarantined and logged). The dead-man's-switch is how you catch this failure:
`published_in_run` stays **0 forever** *and* the digest's **"Pending agent gates"** section keeps
naming the same unanswered gates. The heartbeat proves the cron is alive; the zero delta + the
pending-gates list prove it is making no progress — so the operator knows to fix the **key**
(grep `run.log` for repeated `responder … error … Not logged in`), not the scheduler.

**Graceful degradation on a fatal auth error.** If a fatal Postiz/Zernio auth error escapes
the publish step (bad or missing API key, or a `401`), `run` does **not** crash
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
lock:** the up-to-30s Postiz/Graph calls in `track` (metrics fetch) and `reconcile` (per-post status
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
| `fanops resolve <post_id> <published\|failed> [--url U]` | a post is stuck in `needs_reconcile`/`submitting` and `fanops reconcile` can't auto-resolve it (the scheduler never returns a terminal status, or the post has no real submission id) — the operator checks the platform by hand | forces the post's state to ground truth. `published` (optionally records the live URL via `--url`) or `failed` (safe to re-queue). The documented human-reconcile escape hatch (audit H1). |
| `fanops unhold <clip_id>` | a clip is parked `held` by the brand-risk gate and a human has reviewed/corrected the caption | clears `held`/`held_reason` and resets the clip to `captions_requested` so the next `advance` re-ingests the corrected captions. Replaces the old hand-edit of `ledger.json` (backlog (f), now done). |
| `fanops retry-source <source_id> [--from-stage auto\|catalogued\|transcribed] [--force]` | a source is quarantined in `error` / `moments_empty`, or a terminal source (`moments_decided`, …) needs a full T0 rewind | **AUTO** (default): an errored source with a good transcript resumes at `transcribed` (re-enters at signals — MOL-121). `--from-stage catalogued` clears `meta["transcribed"]` for a full re-transcribe (but on-disk whisper JSON may still short-circuit adoption). **`--force --from-stage catalogued`** (MOL-471): explicit operator gate — purge transcript/signals/vocals caches, discard `moments` + `moment_hooks` gates, `reconcile_moments` away (respects protected posts), clear transcript/language, rewind to `catalogued`. Refuses terminal sources without `--force` (exit **2**). |
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

- On a **live backend with creds**, pulls IG metrics via the Meta Graph and moves published posts to
  `analyzed`. If no live route / Graph token, it **skips cleanly** —
  `track skipped: ...` — rather than erroring.
- Matches metric rows to posts by `submission_id`; **failed posts are skipped** (they
  have no real lift and must never enter the winners pool).
- Computes a **`lift_score`** that weights **saves / shares / retention over likes**
  (likes are near-noise). The default weights live in `track._W`
  (`saves 4.0, shares 4.0, retention 3.0, reach 0.001, likes 0.05`). Re-weight either in code,
  or — without a code change — via `00_control/tuning.json` → `lift_weights` (audit b; see
  *Optional override file* above), if the Graph exposes different fields or you want
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
`claude -p "<prompt>" --output-format json --json-schema '<schema>' --allowedTools "" --strict-mcp-config`
(plain `claude -p`, NOT `--bare`, so it uses the host's `claude login` subscription — no API key).
Chosen over the SDK to keep one toolchain (no second SDK dependency) and fit the codebase's
shell-a-binary idiom (like ffmpeg/whisper) — `claude` is just one more absence-guarded binary.
`--allowedTools ""` makes it a pure generator (no tool use / file access). The prompt is built
from a **committed template** (`src/fanops/prompts.py::moment_prompt` / `caption_prompt`) and
paired with the gate's exact pydantic JSON schema, so most "LLM returned malformed JSON" risk
collapses into `structured_output`.

**Requirement — `claude` on `PATH` AND logged in (the EXISTING subscription; load-bearing).**
We shell **plain `claude -p` (NOT `--bare`)** — operator decision 2026-06-04: use the existing
Claude subscription, not an API key. **Why not `--bare`:** under `--bare`, Anthropic auth is
STRICTLY `ANTHROPIC_API_KEY` and OAuth/keychain are NEVER read — so a `claude login` session would
fail "Not logged in" (verified on the host). Plain `claude -p` uses that login. To stay a clean
generator without `--bare` we pass **`--strict-mcp-config --allowedTools ""`** (no MCP servers, no
tool use). So the host needs a **`claude login`** session (cron inherits the user's `~/.claude`),
**NOT `ANTHROPIC_API_KEY`**. Failure modes, both **quarantined per request** (not a crash): if
`claude` is absent, `claude_json` raises `ToolchainMissingError` (and the preflight hard-blocks the
run); if `claude` is present but logged OUT, `claude -p` exits non-zero with `"Not logged in ·
Please run /login"` → `RuntimeError` → the gate logs `error` and stays pending (so a logged-out
host yields **zero autonomous content**, silently but loggedly — check `run.log` for repeated
`responder … error … Not logged in`, and run `claude login`). *(If `ANTHROPIC_API_KEY` happens to
be exported, `claude` will use it — fine for a 3P/Bedrock setup — but the subscription login is the
supported default path.)*

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

## Publish backends (Postiz / Zernio)

Live publishing uses **Postiz** (self-hosted, Instagram) and/or **Zernio** (hosted TikTok). The
Blotato `rest`/`mcp` backends were removed — valid `FANOPS_POSTER` values are `dryrun | postiz | zernio`.

**Per-channel routing in `accounts.json` is the source of truth** (set in Studio Go-Live). The global
`FANOPS_POSTER` env var is a legacy bridge only.

- **Postiz** — `POSTIZ_URL` + `POSTIZ_API_KEY`; each `(handle × platform)` maps to a Postiz
  `integration_id`. See `docs/POSTIZ_SETUP.md` and `docs/RUNBOOK.md`.
- **Zernio** — `ZERNIO_API_KEY`; TikTok channels route to the Zernio backend per account.
- **IG metrics** — read from the Meta Graph (`GraphInsightsClient`), not from Postiz analytics alone.

Setup walkthrough: `docs/GOLIVE.md` (choose a path) and `docs/RUNBOOK.md` (linear first run).

---

## Three human-only steps

These cannot be automated and gate a live run:

1. **Create the fan accounts** on the platforms (Instagram, TikTok).
2. **Connect Postiz and/or Zernio in the Studio Go-Live tab** — add handles, map each channel to
   its Postiz integration id (IG) or Zernio backend (TikTok), then go live when readiness checks pass.
   See `docs/RUNBOOK.md` steps 1–8. Until an account is `active` with mapped integrations, it is not
   a posting surface.
3. **Review brand-risk HOLDs in the digest.** Clips that tripped the caption guardrails
   (begging / "official" / "link in bio", EN or AR) are held with a reason and never
   post until a human clears them.

   **Clearing a brand-risk hold:** a clip in `held` state is paused for human review (it appears in the digest's "Brand-risk holds" section with the matched reason). To clear it: (1) edit the offending caption(s) in the clip's `*.response.json` under `04_agent_io/requests/` so they pass the EN+AR brand-risk screen, then (2) run **`fanops unhold <clip_id>`** — it clears the hold and resets the clip to `captions_requested`; the next `fanops advance` re-ingests the corrected captions. (See *Recovery verbs* above. The old manual `ledger.json` edit for step 2 is no longer needed.)

---

## Operator runbook — fresh checkout → first real post

The bridge from "code-complete" to "live." Everything below the dashed line in step 5 is
already built and tested; steps 1–3 and the credential exports are the **only** work that
cannot be automated (real accounts, Postiz/Zernio connections, and secrets). Run the steps
**in order** from the repo root — the canonical script is **`docs/RUNBOOK.md`**.

**1. Create the real fan accounts** *(human-only)* — make the actual Instagram / TikTok
   accounts you intend to post from. This is off-platform; FanOps never creates accounts.

**2. Stand up Postiz + connect in Studio** *(human-only)* — follow `docs/POSTIZ_SETUP.md`, then
   Studio Go-Live → **Connect Postiz** (URL + API key). For TikTok, connect Zernio and paste
   `ZERNIO_API_KEY`.

**3. Add + map accounts in Studio** — Go-Live → add handles, map each channel to its Postiz
   integration (Refresh from Postiz → Save). Per-platform integration ids live in `accounts.json`.

**4. Set publish env in `.env`.** Copy `.env.example` → `.env` and fill Postiz/Zernio keys:

```bash
cp .env.example .env
# POSTIZ_URL=https://postiz.example.com
# POSTIZ_API_KEY=...
# FANOPS_POSTER=postiz   # legacy hint; per-channel backends are set in Go-Live
```

**5. Ensure `claude` is logged in on the host** *(required for the autonomous LLM responder)* —
   the responder shells plain `claude -p` (NOT `--bare`), so it uses your **existing Claude
   subscription / `claude login` session** — **no API key needed**. (We dropped `--bare`
   precisely because `--bare` ignores OAuth/keychain and would force an `ANTHROPIC_API_KEY`; the
   call stays a clean generator via `--strict-mcp-config --allowedTools ""`.) Just log in once:

```bash
claude login                              # one-time: authenticate the subscription on this host
echo 'FANOPS_RESPONDER=llm' >> .env       # opt into the autonomous responder (default `manual` is a no-op — a human/cron writes the response files)
claude -p 'say ok' --output-format json   # smoke: confirms the logged-in session works headless (no API key)
```
   NOTE: cron/launchd runs as your user and inherits the same `~/.claude` login, so a `claude
   login` done once in your shell is available to the scheduled `fanops run`. No `ANTHROPIC_API_KEY`
   export is required (if one happens to be set, `claude` will use it — but it is not needed).
   ──────────────────────────────────────────────────────────────────────────────
   *Everything below is already built — these are verification + scheduling steps.*

**6. Run `fanops doctor` and a dryrun pipeline pass** — confirm toolchain + accounts readiness
   before going live in Studio (Go-Live → readiness checks). Postiz connectivity is tested when
   you Save & test in the Go-Live tab.

```bash
fanops doctor
fanops run    # dryrun default — schedules payloads, posts nothing
```
   Resolve any readiness failures **before** flipping live in Studio.

**7. Schedule `fanops run` + a dead-man's-switch monitor.** Add a cron (or launchd
   `StartInterval`) entry that `cd`s into the repo (mandatory — `fanops` resolves its data dirs
   from cwd, there is no `FANOPS_ROOT`) and runs `fanops run` on your interval. `run` responds
   to gates, advances the pipeline, and — on a live backend with a key — closes the learning
   loop (`track`+`adjust`) once per pass:

```cron
*/30 * * * * cd /path/to/repo && ./.venv/bin/fanops run >> run.out 2>&1
```
   (No `ANTHROPIC_API_KEY` in the cron line — the responder uses the host's `claude login` session.
   The cron job runs as your user and inherits `~/.claude`, so the one-time `claude login` from
   step 5 covers it.)
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

Confirm these via `fanops doctor` and the Studio Go-Live readiness panel:

- **Postiz connectivity** — `POSTIZ_URL` + `POSTIZ_API_KEY` set; Save & test succeeds in Go-Live.
- **Per-channel mapping** — every active channel has an integration id (Postiz) or zernio backend (TikTok).
- **Meta Graph (IG insights)** — `META_GRAPH_TOKEN` / per-handle tokens for lift feedback.
- **Metrics field shape** — IG reach/engagement keys match `track._W`; re-weight via `tuning.json` if needed.

Historical Blotato MCP/rest integration notes were removed — those backends no longer exist in `src/`.

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
  body) overwrites the token. A synthetic client token is not a real scheduler id, so polling it may **404**;
  `reconcile_posts` now **contains that per-post poll error** (mirrors `publish_due`): a
  `PostizAuthError` propagates (halt — every poll will 401), any other poll error leaves THAT post
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
  `time.sleep(delay + random.uniform(0, delay)); delay *= 2` (`post/postiz.py`), so retries no
  longer fire in lockstep across surfaces. Still bounded by `_MAX_RETRIES` (exhaustion → `failed`,
  re-queueable since a 429 is rejected pre-processing). *(Network-error handling was already resolved: C1 catches
  `requests.exceptions.RequestException` in `PostizPoster.publish` and parks the post in
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
  a single `timeutil.parse_iso`, and poster base URLs were consolidated — both
  formerly copy-pasted across `run.py`, `crosspost.py`, `tagging.py`, `media.py`,
  `post/postiz.py`, `metrics.py`.
- **(j) Per-account creative variation — v1 DONE (observe-only) + v2 DONE (caption-bias loop closed).**
  **v1** (`FANOPS_CREATIVE_VARIATION=1`): each active account gets a genuinely different caption +
  burned-in on-screen hook per clip (the caption agent returns a per-surface hook; crosspost burns it
  onto the shared base clip via a cheap per-account overlay pass). The `track → analyzed → adjust` lift
  loop attributes per-post; the digest's "Lift by variant" section shows which creative wins. **Default
  ON (M3d)** — `FANOPS_CREATIVE_VARIATION=0` pins the legacy fan-to-all path. Fail-open: no hook / no libass -> today's shared-clip behavior.
  **v2** (`FANOPS_VARIANT_LEARNING=1`, independent flag, default OFF): **closes the A/B loop on the
  cheap/reversible CAPTION-BIAS side.** Once a surface's hook variant earns a trustworthy win
  (>= `FANOPS_VARIANT_MIN_POSTS` analyzed posts AND beating the runner-up by >= `FANOPS_VARIANT_MIN_GAP`),
  `request_captions` feeds that winning hook back into the next caption request as a STYLE cue
  (`variant_learning.best_hooks` is the gate; `caption_prompt` renders "lean toward, don't copy
  verbatim"), so creative compounds instead of firing near-arbitrary hooks forever. Pure/read-only,
  fail-open, fully reversible (flip the flag off → next request reverts). The digest annotates each
  surface's loop state ("learning ACTIVE" vs "gathering data"). **v3 bandit allocator**
  (`FANOPS_VARIANT_UCB=1`, independent flag, default OFF): swaps the caption-bias allocator from
  gated-greedy `best_hooks` to a deterministic **UCB1 bandit** (`variant_learning.ucb_rank`,
  exploration weight `FANOPS_VARIANT_UCB_C`, default √2) — balances exploiting a proven hook against
  exploring under-sampled ones (and is never silent once any variant data exists). It governs ONLY
  this caption-bias side and **does NOT touch amplify** (which keeps the `best_hooks` floor — the
  exploratory bandit can never become a C1 amplify authorization; tested + mutation-proven).
  **Still out of scope:** auto-propagating
  winners into `amplify`/`_delete_moment_cascade` (the C1-risk cascade-delete path) — v2 deliberately
  stays on the caption-request side of that line; the amplify path remains blind to the learner
  (enforced by an isolation grep test).
  **v3 — variant-gated AMPLIFICATION** (`FANOPS_VARIANT_AMPLIFY=1`, independent flag, default OFF):
  the auto-propagate feature — the **first** to touch the amplify/`_delete_moment_cascade` path (C1).
  When a per-account hook variant earns a **SUSTAINED** win — v2's `best_hooks` gate as a FLOOR, plus
  `≥ FANOPS_VARIANT_AMPLIFY_MIN_POSTS` (8) posts, `≥ FANOPS_VARIANT_AMPLIFY_MIN_GAP` (25) lead, AND
  the same hook leading across `≥ FANOPS_VARIANT_AMPLIFY_MIN_STREAK` (3) DISTINCT evidence windows
  (never a single window) — its source is auto-amplified via the existing `adjust.amplify`, with the
  winning hook injected as `extra_guidance`. Runs as a separately-guarded pass in `fanops run` and via
  `fanops amplify-variants`. **AMPLIFY-ONLY**: `variant_amplify` never calls `retire`/
  `_delete_moment_cascade`/`set_*_state(retired)` (two AST isolation tests + a mutation-proven streak
  gate), so a wrong winner can never delete/retire real content; the C1 live-lineage preservation is
  inherited. Deterministic streak state (`Ledger.variant_streaks`, mirrors `tag_log`), fail-SAFE,
  reversible (flip OFF → stops acting). The digest's "Variant amplification" section shows each
  surface's streak state. **Still out of scope:** cross-PLATFORM transfer, bandit/decay scheduling
  (the bandit follow-up remains spec-only), and any change to the EXISTING single-snapshot
  `classify_outcomes`/amplify+retire trigger (v3 only ADDS a harder-gated amplify path).
  **v3 — cross-account/cross-surface transfer** (`FANOPS_VARIANT_TRANSFER=1`, independent flag,
  default OFF): a hook STYLE proven (v2-gated) on `≥ FANOPS_VARIANT_TRANSFER_MIN_DONORS` distinct
  same-platform surfaces is offered to a COLD recipient surface (no own winner) as a demoted,
  persona-ranked weak prior (`learned_hooks_transferred`, rendered below the own-surface block).
  Same-platform is a HARD gate; cross-platform is out of scope. Anti-homogenization: own-winner-wins,
  style-not-verbatim, `MAX_HOOKS` cap. Stays on the caption-request side; the amplify/C1 path remains
  blind (isolation tests extended to `variant_transfer` — both the data-flow check and a
  `transferred_hooks` positive-lock). The digest shows a cold surface as "borrowing platform signal".
  This is ORTHOGONAL to the amplify v3 above: amplify acts on a surface's OWN sustained winner via the
  C1 path; transfer is a cold-start caption-bias prior from OTHER surfaces. **Still out of scope:**
  cross-PLATFORM transfer, bandit/decay scheduling.
