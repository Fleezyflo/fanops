# HANDOFF — MOH FLOW FAN OPS

This file is the cross-session source of truth. It is **rewritten each session** (the §Now block is point-in-time, not an append-only log). Frozen provenance lives in `docs/archive/handoff-history.md` (created on first archive). The build plan is `docs/superpowers/plans/2026-05-31-fanops-real-build-v2.md`. Deviations from that plan made during the build are recorded in the auto-memory file `fanops-build-deviations.md`.

## §State (re-verify the Check commands before quoting)

- **Branch:** `main`. Check: `git branch --show-current`.
- **Working tree:** clean. Check: `git status -sb`.
- **HEAD:** `19c7b50`. Check: `git log --oneline -1`.
- **Unit tests:** 163 passed, 3 deselected. Check: `source .venv/bin/activate && python -m pytest -q -m "not integration"`.
- **Integration tests:** 2 passed, 1 skipped (live Blotato smoke skips without creds). Check: `source .venv/bin/activate && python -m pytest -q -m integration`. (The E2E now pins its own whisper model in-test — no `FANOPS_WHISPER_MODEL` needed; it skips cleanly if no checkpoint is cached.)
- **Module/test parity:** 30 src modules, 30 test files. Check: `ls src/fanops/*.py src/fanops/post/*.py | wc -l && ls tests/*.py tests/integration/*.py | wc -l`.
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
- **Two accepted auto-unrecoverable stuck states** (both surfaced by the digest, no silent loss): a post stranded in `submitting` by a mid-publish crash (never re-driven, to avoid double-posting); a `published` post Blotato never returned metrics for (can't fabricate metrics). A reconcile/poll step for the former is on the backlog.
- **Whisper model:** default `turbo`; falls back to the best cached `~/.cache/whisper/*.pt` when the requested model can't be downloaded (offline/proxied). In THIS env only `tiny` is cached.

## 4. Health checks

Run the §State Check commands. The integration E2E (`tests/integration/test_e2e_real.py`) is the golden path: real `say` TTS → real whisper transcript (asserts "slept") → real ffmpeg 1080×1920 vertical clip → 2 dryrun posts published. It pins the `tiny` whisper model in-test (no env var needed) and skips cleanly on a host with no cached checkpoint, so a fresh checkout / CI runner sees a clear skip rather than a cryptic `assert 0 == 1`.

## 5. Now (rewritten each session)

**As of 2026-06-01 (rev 2).**

**Most recent shipped:** E2E golden-path hardening — `19c7b50` (`fix(e2e): pin whisper model in-test so the golden path can't silently rot off-host`). `tests/integration/test_e2e_real.py` previously passed here only because `tiny.pt` is cached AND the runner remembered `FANOPS_WHISPER_MODEL=tiny`; on a fresh checkout / CI / proxied host the default `turbo` checkpoint can't download, the source goes to `error` state, and the test failed with a cryptic `assert 0 == 1` (reproduced via empty `XDG_CACHE_HOME`). Fix pins `tiny` in-test via `monkeypatch` (self-contained, no env var) and skips cleanly with a clear reason when no checkpoint is cached. Full suite green both ways: `163 passed, 3 deselected` (unit) + `2 passed, 1 skipped` (integration), with and without the env var. Prior shipped: the **entire FAN OPS v2 build** (all 26 plan tasks, `f44284d` and earlier) — real-tooling E2E runs real whisper + real ffmpeg, so the green suite is proven NOT to be just mocks.

**What works right now:**
- End-to-end pipeline runs: `fanops advance` drives ingest → transcribe → signals → moment gate → clip render → caption gate → crosspost → publish (dryrun), pausing at each agent gate.
- The feedback loop is reachable and correct: `fanops track` (pulls metrics, needs `BLOTATO_API_KEY`) → `fanops adjust` (amplify winners / retire losers) — and amplifying a winner now PRESERVES its live published lineage (the Critical fix).
- Per-unit error quarantine: one bad source/moment/clip → `error` state, the pass continues. The unattended `fanops run` loop degrades cleanly (exit 1 + stderr) on a fatal Blotato auth error instead of crashing.
- The digest (`00_control/ledger_digest.md`) surfaces counts, brand-risk holds, failures/errors, pending agent steps, and published-but-unmeasured posts.

**Live state caveats:** Nothing has been run against the REAL Blotato API — posting is `dryrun` by default. The four `INTEGRATION CHECKPOINT`s (media `/uploads` contract, the `postSubmissionId` response key, the metrics endpoint, the MCP tool name/args) are UNVERIFIED against live Blotato; `tests/integration/test_blotato_smoke.py::test_live_auth_and_schedule` confirms them but is skipped (needs `BLOTATO_API_KEY` + `BLOTATO_SMOKE_ACCOUNT_ID`). `accounts.json` holds only `@TBD-1`/`@TBD-2` placeholders (`status: planned`) — no real accounts connected.

**Open items, in priority order (all backlog — the build is done):**
1. **Before first LIVE run:** create fan accounts → connect each in Blotato, paste the numeric `account_id` into `MohFlow-FanOps/00_control/accounts.json` and set `status: active` → set `FANOPS_POSTER=rest` (or `mcp`) + `BLOTATO_API_KEY` → run the live smoke test to confirm the 4 integration checkpoints.
2. **Submitting-recovery / reconcile step** (backlog item a) — a `submitting`-stranded post is never re-driven; needs a poll step.
3. **Backlog** (in `RUNTIME.md §Backlog` + `fanops-build-deviations.md`): externalize brand-risk lists + lift weights to config; REST backoff jitter + retry on network Timeout; per-platform max-duration clamp (the deleted false `PLATFORM_MAX_SECONDS`); `fanops unhold <clip_id>` command; per-source ranking in adjust; media size cap; cover-art-audio slips `has_video_stream`; add `ruff` + consolidate duplicated `_parse`/`BASE_URL` helpers; plus the 6 plan enhancements (subtitle overlay, trending audio, daypart scheduling, best-window learning, multi-artist, secrets manager).

**Pick up here:**
- The build is shippable as-is for dry-run operation. To go LIVE: do Open item 1 (the three human-only steps + confirm integration checkpoints via the live smoke test). Reference: `MohFlow-FanOps/00_control/RUNTIME.md` "three human-only steps" + "integration checkpoints".
- To extend the system: pick from the backlog (Open item 3); each is scoped in `fanops-build-deviations.md`.
- Standing: nothing to push (no remote configured / not pushed); `main` is the only branch; working tree clean.
