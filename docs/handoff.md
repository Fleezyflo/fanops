# HANDOFF — MOH FLOW FAN OPS

This file is the cross-session source of truth. It is **rewritten each session** (the §Now block is point-in-time, not an append-only log). Frozen provenance lives in `docs/archive/handoff-history.md` (created on first archive). The build plan is `docs/superpowers/plans/2026-05-31-fanops-real-build-v2.md`. Deviations from that plan made during the build are recorded in the auto-memory file `fanops-build-deviations.md`.

## §State (re-verify the Check commands before quoting)

- **Branch:** `main`. Check: `git branch --show-current`.
- **Working tree:** clean. Check: `git status -sb`.
- **HEAD:** `5a60c1b` (+ this handoff commit on top). Check: `git log --oneline -1`.
- **Remote:** `origin` → `github.com/Fleezyflo/fanops` (**PRIVATE**), `main` tracks `origin/main`. Pushed. Check: `git remote -v && gh repo view Fleezyflo/fanops --json isPrivate`.
- **Unit tests:** 184 passed, 4 deselected. Check: `source .venv/bin/activate && python -m pytest -q -m "not integration"`.
- **Integration tests:** 3 passed, 1 skipped (live Blotato smoke skips without creds; the publish→track→analyzed loop test + the real-tooling E2E both run). Check: `source .venv/bin/activate && python -m pytest -q -m integration`. (The E2E pins its own whisper model in-test — no `FANOPS_WHISPER_MODEL` needed; it skips cleanly if no checkpoint is cached.)
- **Module/test parity:** 31 src modules, 33 test files (3 integration: `test_e2e_real.py`, `test_blotato_smoke.py`, `test_publish_track_loop.py`; rest unit, incl. `test_ledger_lock.py` for H6). Check: `ls src/fanops/*.py src/fanops/post/*.py | wc -l && ls tests/*.py tests/integration/*.py | wc -l`.
- **Real tooling present:** ffmpeg 8.0.1, ffprobe, whisper CLI, `say` (macOS TTS) all on PATH; whisper `tiny.pt` cached (model downloads blocked by proxy in this env). The E2E pins `tiny` itself, so no env var is required; to override the model for a real `fanops` run, set `FANOPS_WHISPER_MODEL`. Check: `for b in ffmpeg ffprobe whisper say; do command -v $b; done`.
- **Posting backend default:** `dryrun` (writes payload JSON to `05_scheduled/`, posts nothing). Switch via `FANOPS_POSTER=rest|mcp` + `BLOTATO_API_KEY`. Check: `grep -n "FANOPS_POSTER" src/fanops/config.py`.

## 1. What this is

An autonomous fan-account engine for Moh Flow (bilingual EN/AR rapper). It ingests his videos, decides which moments are worth posting (transcript + audio/scene signals → an agent decision with a recorded reason), cuts platform-ready clips with agent-written EN/AR captions, cross-posts to every fan account × platform via Blotato (staggered for opsec, with a subtle non-synchronized artist @mention), then pulls real performance back to make more of what works. See `README.md` (front door), `MohFlow-FanOps/00_control/RUNTIME.md` (operating loop + production seams), `MohFlow-FanOps/00_control/RISK.md` (recorded operator risk-acceptance for the multi-account model), `MohFlow-FanOps/00_control/context.md` (functional — injected as agent guidance).

## 2. Architecture

- **Unit chain:** `Source → Moment → Clip → Post`, over one git-versioned JSON ledger (`00_control/ledger.json`, atomic temp+os.replace write under a file lock).
- **Identity:** content-addressed SHA-based ids everywhere (`ids.py`: `make_id`/`child_id`/`surface_key`) — NEVER builtin `hash()` (the #1 v1 bug; cross-process stability is proven by a subprocess idempotency test).
- **Agent gates:** generative steps (`decide_moments`, `write_captions`) cross a file contract in `04_agent_io/requests/` — code writes a `*.request.json` (stamped with a `request_id`), the agent/responder writes a validated `*.response.json` echoing that id, code resumes. Stale responses can never be applied (request_id correlation).
- **Two responders** (`responder.py`): `ManualResponder` (no-op; a human/cron writes responses) vs `LlmResponder` (`FANOPS_RESPONDER=llm`; wraps an LLM call, validates against `MomentDecision`/`CaptionSet`).
- **Three posters** (`post/`): `dryrun` (default), `rest` (`BlotatoRestPoster`, retry/backoff/typed-errors), `mcp` (`BlotatoMcpPoster`, injected `tool_caller`).
- **Pipeline** (`pipeline.py` `advance()`): per-unit error quarantine (one bad source/moment/clip → `error` state, never wedges the pass). **CLI** (`cli.py`): `status/ingest/pull/advance/respond/track/adjust/gc/run/digest`.
- **State machines** (`models.py`): separate `SourceState`/`MomentState`/`ClipState`/`PostState`. `failed`≠`analyzed`; `held` (brand-risk) and `retired` are first-class.

## 3. Gotchas / non-obvious invariants

- **`--base-time` is the SCHEDULE ANCHOR, not the publish cutoff.** crosspost staggers posts to times AFTER base-time; `publish_due(now=None)` publishes whatever is due as of REAL wall-clock now. To publish in the same pass (backfill), pass a `--base-time` in the past. (This was a fixed plan contradiction.)
- **Conservative retirement:** `adjust` amplifies top `--winner-pct` (0.3) but retires only the bottom `--retire-pct` (0.2) AND below `--lift-floor` (20.0) — objectively-fine clips above the floor are never retired. Retiring a clip also retires its moment (so it can't be re-rendered).
- **Three surfaced-but-auto-unrecovered stuck states** (all in the digest + counts, no silent loss): a post stranded in `submitting` by a mid-publish crash; a post in **`needs_reconcile`** (REST hit an ambiguous 5xx/timeout after sending the body — it may be live, so it is NOT re-POSTed; Blotato has no idempotency key); a `published` post Blotato never returned metrics for. The first two need a poll step (`GET /v2/posts/:id` → promote/reset) — backlog item (a). `needs_reconcile` only ever arises under the `rest`/`mcp` backends, never `dryrun`.
- **`dryrun` posts are trackable** (audit C4): the dryrun poster stamps `submission_id = f"dryrun_<post_id>"`, so the `publish → track → analyzed → adjust` loop is fully exercisable offline by feeding `track` a metrics row keyed on that id. (CLI `track` still skips in dryrun — no live metrics source — but the seam is proven by `tests/integration/test_publish_track_loop.py`.)
- **Whisper model:** default `turbo`; falls back to the best cached `~/.cache/whisper/*.pt` when the requested model can't be downloaded (offline/proxied). In THIS env only `tiny` is cached.

## 4. Health checks

Run the §State Check commands. The integration E2E (`tests/integration/test_e2e_real.py`) is the golden path: real `say` TTS → real whisper transcript (asserts "slept") → real ffmpeg 1080×1920 vertical clip → 2 dryrun posts published. It pins the `tiny` whisper model in-test (no env var needed) and skips cleanly on a host with no cached checkpoint, so a fresh checkout / CI runner sees a clear skip rather than a cryptic `assert 0 == 1`.

## 5. Now (rewritten each session)

**As of 2026-06-01 (rev 6).**

**Most recent shipped (this session — 3 commits; `a983fe5` pushed, `b489e0b`+`832ebba` LOCAL/unpushed):**
1. `a983fe5` — **C2 (the audit's last "Block LIVE" finding) ADJUDICATED → REJECTED as a non-defect.** The audit framed the shared per-clip `media_url` (run.py:37-38) as an opsec hole. **Operator decision: that anti-correlation framing is wrong and unwanted** — byte/pHash-scrambling "throws people off the accounts' tracks," adds no creative value. What IS wanted is *creative* variation (different hook/text/edit per account, measured by the lift loop) — a separate backlog feature, NOT a byte tweak. `run.py`/`media.py` unchanged; shared media_url affirmed-correct. **With C2 rejected and C1/C3/C4 fixed, all four original "Block LIVE" findings are resolved.** Rationale in `fanops-build-deviations.md` ("Adjudicated — rejected").
2. `b489e0b` — **fix (audit H6): self-healing ledger lock, TDD + systematic-debugging.** The lock was an `O_EXCL` sentinel: a writer killed -9 between `os.open` and `os.unlink` left `ledger.lock` on disk with no liveness info → every later command stalled the 30s timeout then crashed with an **uncaught `TimeoutError`** (audit said "blocks forever"; real mechanism = 30s-stall-then-traceback-every-invocation, same total-outage effect). Reproduced first. **Fix = `fcntl.flock`** (NOT a PID/mtime heuristic): the kernel releases an flock on process death, so an orphaned lock is inert and the next process acquires instantly — self-heals with certainty. Verified empirically (kill -9 a holder → file persists but next flock succeeds). Genuine contention (overlapping cron) → bounded wait → typed `LockBusyError` → `cli.main` exits 1 with one clean line, no traceback. `_DEFAULT_LOCK_TIMEOUT` read at call time so it's tunable. 4 new tests (`test_ledger_lock.py`). **Verified through the REAL `fanops` CLI**: orphaned lock → `ingest` exit 0 in 0.24s (was 30s+crash); live holder → exit 1 in 0.67s, clean message, no traceback.
3. `832ebba` — **docs(RUNTIME):** the crontab `cd /path/to/repo` is **mandatory** (fanops resolves ledger/lock/accounts from cwd; no `FANOPS_ROOT`), and overlapping runs are now safe (flock self-heals; an overrunning prior run makes the next tick exit 1 cleanly, no corruption).

Tests green (re-derived this session): unit `184 passed, 4 deselected` (+4 from H6); integration `3 passed, 1 skipped`.

**What works right now:**
- Full pipeline runs (dryrun): ingest → transcribe → signals → moment gate → clip render → caption gate → crosspost → publish, pausing at each agent gate. Per-unit error quarantine inside the 3 explicit loops works.
- **The ledger lock self-heals an orphaned lock** (H6): a crash mid-write no longer wedges the cron loop; flock auto-releases on process death. Overlapping runs degrade cleanly (exit 1 + clean line), never corrupt or traceback.
- **The learning loop is exercisable end-to-end in dryrun** (C4): `publish → track → analyzed → adjust`, proven by `tests/integration/test_publish_track_loop.py`.
- **Real-API retry is safe** (C1): no blind re-POST on an ambiguous failure; ambiguous posts park in `needs_reconcile` and surface in digest + `status` + `advance()` summary.
- **A shared per-clip media URL across accounts is intended** (C2 adjudication): NOT a defect; do not "fix" it. The valuable successor is the per-account *creative*-variation feature (backlog).
- Audit-confirmed solid (don't "fix"): SHA content-addressed ids, request_id agent correlation, atomic ledger writes, cascade-preserves-live-lineage, clean secret handling, crash-between-submit-and-save (F11).

**Live state caveats:** **`b489e0b` + `832ebba` are NOT pushed** (`main` is ahead of `origin/main` by 2) — push before relying on the remote. Still nothing run against REAL Blotato — `dryrun` default; `accounts.json` = `@TBD` placeholders. The remaining INTEGRATION CHECKPOINTs (media-upload presign shape, metrics endpoint shape, MCP tool name) are UNVERIFIED (smoke test skips without `BLOTATO_API_KEY`+`BLOTATO_SMOKE_ACCOUNT_ID`). The `postSubmissionId` key + no-idempotency-key fact are CONFIRMED from Blotato's docs (not a live call). **No "Block LIVE" defect remains** — the gate to staging is the operator-gated smoke test + 3 human-only account steps, not code.

**Open items, in priority order:**
1. **Audit HIGH — the top CODE work doable now without credentials** (all open except H6, see `fanops-build-deviations.md`): H1/H2 (staggering math — rank-by-account + non-monotonic + two clips collide on a minute, crosspost.py:25-30,55,67), H3 (@mention de-cluster gate defeated by tag_log overwrite, tagging.py:20-32), H4 (the `submitting`+`needs_reconcile` reconcile/poll step — backlog item (a)), H7 (wrap LLM responder — `fanops run`, the required unattended mode, tracebacks if it raises), H8 (typed `BlotatoAuthError` vs the unpinned 401 string-match — under- AND over-fires), H9 (pin `lift_score` weights — single hi>lo assert lets sign-flip/weight-drop through), H10 (CI that fails when the toolchain is absent — the E2E "not mocks" proof silently skips today). **H6 — DONE this session.**
2. **LIVE prerequisite, OPERATOR-GATED (needs creds, not doable by an agent solo):** run `tests/integration/test_blotato_smoke.py` against the Blotato sandbox (`BLOTATO_API_KEY`+`BLOTATO_SMOKE_ACCOUNT_ID`) — resolves the remaining unverified contracts at once. Highest-leverage *external* action, blocked until the operator supplies sandbox creds.
3. **New feature (from C2 adjudication):** per-account creative variation for A/B content learning (hook/caption/edit variants per account/cohort, lift loop attributes the winner). Own spec → plan → build cycle when prioritized. Scoped in `fanops-build-deviations.md`.
4. **Pre-existing backlog** (`RUNTIME.md §Backlog`): REST backoff jitter (item c, network-error half done); externalize brand-risk lists + lift weights; per-platform max-duration clamp; `fanops unhold`; ruff + helper consolidation (`_parse`/`BASE_URL` triplication past rule-of-three); per-source ranking; media size cap; the plan enhancements.

**Pick up here:**
- **FIRST (housekeeping):** `git push origin main` — two local commits (`b489e0b` H6 fix, `832ebba` RUNTIME doc) are unpushed.
- **Recommended next code work (no creds needed):** **H7 — wrap the LLM responder so `fanops run` can't traceback in unattended mode.** It's the natural successor to H6: both harden the *required autonomous path* (H6 = lock can't wedge the loop; H7 = a responder exception can't crash it). Self-contained, TDD-able (`responder.py` `LlmResponder.answer_pending` raising → assert `run` degrades to a logged halt, not a stack dump — mirror the `_is_fatal_auth_error`/`run`-loop pattern already in `cli.py:132-138`). Alternatively H1/H2 (staggering math) if you'd rather harden opsec timing. All in `fanops-build-deviations.md`.
- **LIVE path is operator-gated, not agent-gated:** the only thing left before a staging run is the sandbox smoke test (item 2, needs creds) + the 3 human-only steps (create accounts → connect in Blotato → paste account_ids + set active; reference `RUNTIME.md` "three human-only steps").
- Standing: `main` on private `origin` (Fleezyflo/fanops); working tree clean after the handoff commit (but **2 commits ahead of origin — push**). Audit workflow at `.claude/workflows/fanops-deep-audit.js` (tighten its agents to read-only before re-running). **Do not re-raise C2 from a future audit without new evidence the operator's stance changed — it was adjudicated, not missed.** When verifying through the real CLI, `cd` to the project root first (or `Config()` resolves the wrong ledger — this caused two false-green checks this session; see H6 note in the deviation memo).
