# HANDOFF — MOH FLOW FAN OPS

This file is the cross-session source of truth. It is **rewritten each session** (the §Now block is point-in-time, not an append-only log). Frozen provenance lives in `docs/archive/handoff-history.md` (created on first archive). The build plan is `docs/superpowers/plans/2026-05-31-fanops-real-build-v2.md`. Deviations from that plan made during the build are recorded in the auto-memory file `fanops-build-deviations.md`.

## §State (re-verify the Check commands before quoting)

- **Branch:** `main`. Check: `git branch --show-current`.
- **Working tree:** clean. Check: `git status -sb`.
- **HEAD:** `5a60c1b` (+ this handoff commit on top). Check: `git log --oneline -1`.
- **Remote:** `origin` → `github.com/Fleezyflo/fanops` (**PRIVATE**), `main` tracks `origin/main`. Pushed. Check: `git remote -v && gh repo view Fleezyflo/fanops --json isPrivate`.
- **Unit tests:** 205 passed, 4 deselected. Check: `source .venv/bin/activate && python -m pytest -q -m "not integration"`.
- **Integration tests:** 3 passed, 1 skipped (live Blotato smoke skips without creds; the publish→track→analyzed loop test + the real-tooling E2E both run). Check: `source .venv/bin/activate && python -m pytest -q -m integration`. (The E2E pins its own whisper model in-test — no `FANOPS_WHISPER_MODEL` needed; it skips cleanly if no checkpoint is cached.)
- **Module/test parity:** 32 src modules (incl. `reconcile.py` for H4), 34 test files (3 integration: `test_e2e_real.py`, `test_blotato_smoke.py`, `test_publish_track_loop.py`; rest unit, incl. `test_ledger_lock.py` H6, `test_reconcile.py` H4). Check: `ls src/fanops/*.py src/fanops/post/*.py | wc -l && ls tests/*.py tests/integration/*.py | wc -l`.
- **Real tooling present:** ffmpeg 8.0.1, ffprobe, whisper CLI, `say` (macOS TTS) all on PATH; whisper `tiny.pt` cached (model downloads blocked by proxy in this env). The E2E pins `tiny` itself, so no env var is required; to override the model for a real `fanops` run, set `FANOPS_WHISPER_MODEL`. Check: `for b in ffmpeg ffprobe whisper say; do command -v $b; done`.
- **Posting backend default:** `dryrun` (writes payload JSON to `05_scheduled/`, posts nothing). Switch via `FANOPS_POSTER=rest|mcp` + `BLOTATO_API_KEY`. Check: `grep -n "FANOPS_POSTER" src/fanops/config.py`.
- **CI:** `.github/workflows/ci.yml` — unit job + an e2e job that installs the toolchain and runs the integration suite with `FANOPS_REQUIRE_E2E=1` (turns a tooling-absent skip into a failure, audit H10). UNRUN until pushed to a repo with Actions enabled; validated structurally only. Check: `ls .github/workflows/ci.yml && grep -n "FANOPS_REQUIRE_E2E" tests/integration/test_e2e_real.py`.

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

**As of 2026-06-01 (rev 7).**

**Most recent shipped (this session — ALL audit-HIGH findings H1–H10 resolved; C2 adjudicated; everything pushed to `origin/main`):**
- `a983fe5` — **C2 ADJUDICATED → REJECTED** as a non-defect (operator decision: shared per-clip `media_url` is fine; anti-correlation byte-scrambling unwanted; the real need is *creative* per-account variation = a backlog feature). All four original "Block LIVE" findings now resolved.
- `b489e0b` — **H6 self-healing ledger lock** (flock; kernel releases on process death → orphaned lock can't wedge cron; genuine contention → typed `LockBusyError` → clean exit 1). + `832ebba` RUNTIME doc (mandatory cron `cd`; overlapping runs safe).
- `50b7dc7` — **H7**: responder raise can't crash `fanops run` (moved the responder call inside the run loop's guard → clean halt, no traceback). *(Per-request isolation inside `answer_pending` deferred — needs a product call; backlog.)*
- `05b4e16` — **H8**: typed `BlotatoAuthError` replaces the `"401" in msg` string-match (fixed both under-fire/F52 and over-fire). Raised at REST 401 + missing-key + media presign 401; type-matched in `run.py`; `cli.main` exits 2 cleanly.
- `c946056` — **H3**: @mention de-cluster gate no longer defeated by tag_log overwrite (key per `(account,clip)` not per account; a `when`-relative prune was tried, found to reopen the hole under out-of-order eval, and removed).
- `7feb302` — **H1/H2**: monotonic, collision-free staggering (fixed 40-min step + bounded 0–29 jitter < step → provably monotonic; `clip_id` in the seed → two clips never collide on a surface). + RUNTIME doc-sync.
- `ce05685` — **H4**: reconcile step (`fanops reconcile` + auto-pass in `advance`). **Plan/reality discovery:** Blotato's only post lookup (`GET /v2/posts/:id`) needs a submission id the stranded posts usually lack → id-bearing posts auto-reconcile, id-less ones stay parked for *human* reconcile (irreducible; the REST poster now captures an id from a 5xx body when present to shrink that residue). Closes backlog item (a).
- `ac30de5` — **H9**: lift_score weights pinned exactly (mutation-proven: a dropped/sign-flipped weight fails the new tests while the old `hi>lo` still passed).
- `aa8c510` — **H10**: `.github/workflows/ci.yml` + `FANOPS_REQUIRE_E2E=1` gate so the real-tooling E2E *must* run in CI (a tooling-absent skip becomes a failure). CI YAML UNRUN until pushed to a repo with Actions enabled — validated structurally only.

Tests green (re-derived this session): unit `205 passed, 4 deselected` (180→205, +25 across the 8 fixes); integration `3 passed, 1 skipped`.

**What works right now:**
- Full pipeline runs (dryrun): ingest → transcribe → signals → moment gate → clip render → caption gate → crosspost → publish, pausing at each agent gate; per-unit quarantine inside the 3 explicit loops.
- **The unattended path is hardened end-to-end:** orphaned lock self-heals (H6); a responder exception degrades cleanly, never tracebacks (H7); auth failures halt-by-type with a clean message (H8); stranded posts auto-reconcile where the API allows (H4).
- **Opsec timing/tagging are sound:** staggering is monotonic + collision-free (H1/H2); the cross-account @mention window holds across re-tags (H3).
- **The learning loop** (`publish → track → analyzed → adjust`) is exercisable in dryrun (C4) and its scoring is pinned (H9).
- **A shared per-clip media URL across accounts is intended** (C2): NOT a defect; do not "fix" it.
- Audit-confirmed solid (don't "fix"): SHA content-addressed ids, request_id agent correlation, atomic ledger writes, cascade-preserves-live-lineage, clean secret handling, F11.

**Live state caveats:** Still nothing run against REAL Blotato — `dryrun` default; `accounts.json` = `@TBD`. The INTEGRATION CHECKPOINTs (media-upload presign shape, metrics endpoint shape, reconcile `GET /v2/posts/:id` shape, MCP tool name) are UNVERIFIED against a live call (smoke test skips without creds). The CI workflow has never executed (no push to an Actions-enabled repo yet). **No "Block LIVE" defect and no open audit-HIGH remain** — the gate to a staging run is the operator-gated smoke test + the 3 human-only account steps.

**Open items, in priority order:**
1. **Audit MEDIUM (the only open audit items left, both correctness, no creds needed; see `fanops-build-deviations.md` "Notable HIGH/MEDIUM"):** **M1** — `reconcile_moments` un-retires a retired moment (undoes `adjust.retire`); **M2** — `crosspost_clips`/`publish_due` run *outside* the per-unit quarantine, so one raise discards the whole pass's in-memory progress (`save()` never runs). M2 is the higher-value (data-loss-shaped) of the two.
2. **LIVE prerequisite, OPERATOR-GATED (needs creds):** run `tests/integration/test_blotato_smoke.py` against the Blotato sandbox (`BLOTATO_API_KEY`+`BLOTATO_SMOKE_ACCOUNT_ID`) — resolves the remaining unverified contracts (now including the reconcile endpoint shape) at once.
3. **New feature (from C2 adjudication):** per-account creative variation for A/B content learning (hook/caption/edit variants per account/cohort, lift loop attributes the winner). Own spec → plan → build cycle.
4. **Deferred follow-ups surfaced this session:** per-request isolation inside `LlmResponder.answer_pending` (H7 note — one bad gate currently halts the tick); the human-reconcile residue for id-less stranded posts (H4 — irreducible given the API).
5. **Pre-existing backlog** (`RUNTIME.md §Backlog`): REST backoff jitter (item c); externalize brand-risk lists + lift weights; per-platform max-duration clamp; `fanops unhold`; ruff + `_parse`/`BASE_URL` triplication consolidation; per-source ranking; media size cap.

**Pick up here:**
- **Recommended next code work (no creds needed): M2** — bring `crosspost_clips` + `publish_due` under the per-unit quarantine (or checkpoint `save()` between units) so one raise can't discard the whole pass's progress. It's the same robustness family as H6/H7 and the highest-value open item. TDD: a mid-loop raise must leave already-processed units persisted. Then **M1** (reconcile_moments must not un-retire a retired moment — guard the upsert against `MomentState.retired`). Both scoped in `fanops-build-deviations.md`.
- **LIVE path is operator-gated, not agent-gated:** sandbox smoke test (item 2, needs creds) + the 3 human-only steps (create accounts → connect in Blotato → paste account_ids + set active; `RUNTIME.md` "three human-only steps").
- **First real CI exercise:** the next push already happened, but GitHub Actions only runs if the repo has Actions enabled — confirm the `ci.yml` run is green on GitHub (it has never executed; structurally validated only).
- Standing: `main` pushed to private `origin` (Fleezyflo/fanops); working tree clean after the handoff commit. Audit workflow at `.claude/workflows/fanops-deep-audit.js` (tighten its agents to read-only before re-running). **Do not re-raise C2 from a future audit** — it was adjudicated, not missed. When verifying through the real CLI, **`cd` to the project root first** (`Config()` resolves the ledger from cwd; no `FANOPS_ROOT` — this caused false-green checks this session, see the H6/H4 notes in the deviation memo).
