# HANDOFF — MOH FLOW FAN OPS

This file is the cross-session source of truth. It is **rewritten each session** (the §Now block is point-in-time, not an append-only log). Frozen provenance lives in `docs/archive/handoff-history.md` (created on first archive). The build plan is `docs/superpowers/plans/2026-05-31-fanops-real-build-v2.md`. Deviations from that plan made during the build are recorded in the auto-memory file `fanops-build-deviations.md`.

## §State (re-verify the Check commands before quoting)

- **Branch:** `main`. Check: `git branch --show-current`.
- **Working tree:** clean. Check: `git status -sb`.
- **HEAD:** `a8fe1a2` (+ this handoff commit on top). Check: `git log --oneline -1`.
- **Remote:** `origin` → `github.com/Fleezyflo/fanops` (**PRIVATE**), `main` tracks `origin/main`. Pushed. Check: `git remote -v && gh repo view Fleezyflo/fanops --json isPrivate`.
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

**As of 2026-06-01 (rev 3).**

**Most recent shipped (this session, 4 commits):**
1. `19c7b50` — E2E golden-path hardening: `test_e2e_real.py` pins `tiny` in-test via `monkeypatch` (self-contained, no `FANOPS_WHISPER_MODEL` needed) and skips cleanly when no whisper checkpoint is cached, instead of failing `assert 0 == 1` on a fresh/CI/proxied host (reproduced via empty `XDG_CACHE_HOME`).
2. `bdc3fea` — **security fix (audit C3):** gitignored `00_control/{ledger.json,ledger.lock,ledger_digest.md,run.log}` so a future `git add -A` can't commit the account roster + transcripts + private filenames into history. Tracked control files (accounts.json, RISK/RUNTIME/context.md, .gitkeep) stay tracked. Verified with `git check-ignore`.
3. `32feba0` — **fix (audit M6):** clean one-line `<file> invalid: <reason>` + exit 2 on a corrupt ledger.json/accounts.json (was a raw traceback); active-account-missing-id now caught before a run via `_check_accounts`. New `fanops.errors.ControlFileError`. Verified via the real CLI. Suite 165 → **175 passed, 1 skipped** (+10 tests). [Provenance: an audit subagent over-stepped its read-only scope and drafted this; I verified + completed + own it — see `fanops-build-deviations.md`.]
4. **Remote set up + pushed:** created **private** repo `github.com/Fleezyflo/fanops` via `gh`; `main` tracks `origin/main`. Verified private + no PII artifact in the pushed tree.

Tests green: **175 passed, 1 skipped** (full suite). Unit-only: `163 passed, 3 deselected` + the new corrupt-control-file tests.

**Also this session — DEEP AUDIT (not yet remediated):** ran a 6-lens multi-agent audit with 3-skeptic adversarial verification (31 confirmed, 13 refuted). Full findings + fixes + the solid/refuted lists are in `fanops-build-deviations.md` (§Deep audit findings). Headline: the deterministic core is genuinely strong, but the cross-stage / real-API / opsec paths are weaker than the prose claims. **C3 is now FIXED (above); C1/C2/C4 remain.**

**What works right now:**
- End-to-end pipeline runs (dryrun): ingest → transcribe → signals → moment gate → clip render → caption gate → crosspost → publish, pausing at each agent gate. Per-unit error quarantine inside the 3 explicit loops works.
- Audit-confirmed solid: SHA content-addressed ids, request_id agent correlation, atomic ledger writes, cascade-preserves-live-lineage, clean secret handling, crash-between-submit-and-save (F11).

**Live state caveats:** Nothing run against REAL Blotato — `dryrun` default. The 4 INTEGRATION CHECKPOINTs are UNVERIFIED (smoke test skipped, needs `BLOTATO_API_KEY`+`BLOTATO_SMOKE_ACCOUNT_ID`). `accounts.json` = `@TBD` placeholders. **Audit C4 (verified): in dryrun the money loop is DEAD** — dryrun sets no `submission_id`, so `track` never matches → nothing ever reaches `analyzed` → amplify/retire never fire. So even staging today proves nothing about the learning loop until C4 is fixed.

**Open items, in priority order:**
1. **Audit blockers before LIVE (see `fanops-build-deviations.md` for fixes):** C1 (idempotency key — first gateway timeout double-publishes to real accounts), C2 (per-account media derivative — identical bytes/URL across accounts defeats opsec), C4 (dryrun `submission_id` + publish→track→analyzed test — money loop dead in dryrun). Highest-leverage single action: run `test_blotato_smoke.py` against the Blotato sandbox (resolves C1/M5 + the whole unverified-contract risk).
2. **Audit HIGH:** H4 (`submitting` reconcile + digest surfacing), H6 (self-healing ledger lock — orphaned lock hard-downs cron), H7 (wrap LLM responder — `fanops run` tracebacks if it raises), H8 (typed `BlotatoAuthError`), H1/H2 (staggering math), H3 (tag-cluster gate), H9 (pin lift_score weights), H10 (CI that fails when toolchain absent). *(M6 corrupt-control-files — DONE this session, `32feba0`.)*
3. **Pre-existing backlog** (`RUNTIME.md §Backlog`): externalize brand-risk lists + lift weights; per-platform max-duration clamp; `fanops unhold`; ruff + helper consolidation; the 6 plan enhancements.

**Pick up here:**
- **Recommended next:** start remediating audit blockers, fastest-first — **C4** (dryrun `submission_id` gap + the missing publish→track→analyzed integration test, TDD) then **C1** (idempotency key on the Post + safe-retry split). Both are scoped with exact file:line + fix in `fanops-build-deviations.md`.
- Before any LIVE run: the 3 human-only steps (create accounts → connect in Blotato → paste account_ids + set active) + run the sandbox smoke test. Reference: `RUNTIME.md` "three human-only steps".
- Standing: `main` pushed to private `origin` (Fleezyflo/fanops); working tree clean. The audit workflow is saved at `.claude/workflows/fanops-deep-audit.js` (re-runnable).
