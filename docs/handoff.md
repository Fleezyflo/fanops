# HANDOFF — MOH FLOW FAN OPS

This file is the cross-session source of truth. It is **rewritten each session** (the §Now block is point-in-time, not an append-only log). Frozen provenance lives in `docs/archive/handoff-history.md` (created on first archive). The build plan is `docs/superpowers/plans/2026-05-31-fanops-real-build-v2.md`. Deviations from that plan made during the build are recorded in the auto-memory file `fanops-build-deviations.md`.

## §State (re-verify the Check commands before quoting)

- **Branch:** `main`. Check: `git branch --show-current`.
- **Working tree:** clean. Check: `git status -sb`.
- **HEAD:** `631e187` (+ this handoff commit on top). Check: `git log --oneline -1`.
- **Remote:** `origin` → `github.com/Fleezyflo/fanops` (**PRIVATE**), `main` tracks `origin/main`. Pushed. Check: `git remote -v && gh repo view Fleezyflo/fanops --json isPrivate`.
- **Unit tests:** 180 passed, 4 deselected. Check: `source .venv/bin/activate && python -m pytest -q -m "not integration"`.
- **Integration tests:** 3 passed, 1 skipped (live Blotato smoke skips without creds; the publish→track→analyzed loop test + the real-tooling E2E both run). Check: `source .venv/bin/activate && python -m pytest -q -m integration`. (The E2E pins its own whisper model in-test — no `FANOPS_WHISPER_MODEL` needed; it skips cleanly if no checkpoint is cached.)
- **Module/test parity:** 30 src modules, 31 test files (one extra: `tests/integration/test_publish_track_loop.py`). Check: `ls src/fanops/*.py src/fanops/post/*.py | wc -l && ls tests/*.py tests/integration/*.py | wc -l`.
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

**As of 2026-06-01 (rev 4).**

**Most recent shipped (this session, 2 commits, pushed to `origin/main`):**
1. `19e81c2` — **fix (audit C4 + C1), TDD.** Two LIVE-blockers remediated, both verified through the real CLI/poster (not just pytest):
   - **C4 (dryrun money loop was dead):** `DryRunPoster.publish` now stamps `submission_id = f"dryrun_<post_id>"` (1 line) so `track` binds → `analyzed` → amplify/retire fire. Chose the fake-side fix over patching `track` (the fake should emulate the real posters; precedent = `dryrun_media_url`). Proof: new **integration** test `tests/integration/test_publish_track_loop.py` spans the real `publish_due → pull_metrics → analyzed` seam. Real-CLI check: `published`(sid=`dryrun_demo`)→`analyzed`(lift_score 81.8).
   - **C1 (in-process retry could double-publish):** the audit said "idempotency key" but the live Blotato v2 API has NONE (verified via Context7 docs; Blotato's own troubleshooting confirms a publish timeout duplicates a post). **User confirmed the call** → new `PostState.needs_reconcile` + asymmetric retry: `429` retried (rejected pre-processing, safe); `5xx`/network-timeout-after-body → `needs_reconcile`, **no re-POST** (may be live). Also catches `RequestException` (previously escaped uncaught — closes the Task-19(b)/H5 gap). Digest surfaces it separately from Failures; cascade preserves it (`_LIVE_POST_STATES`); `advance()`/`status` count it. (The status/summary count was a deferred observability item I spawned as a background task; it ran in this tree, I verified it green + real-`status` output, folded it in. See `fanops-build-deviations.md`.)
2. `631e187` — **docs:** synced README + RUNTIME to the above (dryrun trackability, the confirmed no-idempotency-key fact + `needs_reconcile` behavior, corrected the stale "retry on 429/5xx" claim, updated §Backlog items (a)+(c)).

Tests green: **183 passed, 1 skipped** (full suite). Unit-only: `180 passed, 4 deselected`. Net +8 vs last session (175→183).

**What works right now:**
- Full pipeline runs (dryrun): ingest → transcribe → signals → moment gate → clip render → caption gate → crosspost → publish, pausing at each agent gate. Per-unit error quarantine inside the 3 explicit loops works.
- **The learning loop is now exercisable end-to-end in dryrun** (C4): `publish → track → analyzed → adjust`, proven by the new integration test (feed `track` a metrics row keyed on the `dryrun_*` id).
- **Real-API retry is now safe** (C1): no blind re-POST on an ambiguous failure; ambiguous posts park in `needs_reconcile` and surface in digest + `status` + `advance()` summary.
- Audit-confirmed solid (don't "fix"): SHA content-addressed ids, request_id agent correlation, atomic ledger writes, cascade-preserves-live-lineage, clean secret handling, crash-between-submit-and-save (F11).

**Live state caveats:** Still nothing run against REAL Blotato — `dryrun` default; `accounts.json` = `@TBD` placeholders. The remaining INTEGRATION CHECKPOINTs (media-upload presign shape, metrics endpoint shape, MCP tool name) are UNVERIFIED (smoke test skips without `BLOTATO_API_KEY`+`BLOTATO_SMOKE_ACCOUNT_ID`). The `postSubmissionId` key and the no-idempotency-key fact are now CONFIRMED from Blotato's published docs (not yet from a live call). C4 unblocks proving the loop in staging; **C2 still makes any multi-account LIVE run an opsec risk** (see below).

**Open items, in priority order:**
1. **Remaining LIVE blocker — C2 (opsec):** every fan account posts byte-identical media at one shared Blotato CDN url (one `media_url` cached per clip, `run.py:37-38`) — identical bytes + identical URL across "independent" accounts is the most trivial cross-account correlation signal; defeats the whole opsec premise. FIX: per-surface re-encoded media derivative seeded by `surface_key`, uploaded per-surface. Scoped in `fanops-build-deviations.md` §Deep audit findings. *(C1, C3, C4 — DONE; only C2 of the four "Block LIVE" findings remains.)*
2. **Highest-leverage single action:** run `tests/integration/test_blotato_smoke.py` against the Blotato sandbox (`BLOTATO_API_KEY`+`BLOTATO_SMOKE_ACCOUNT_ID`) — resolves the remaining unverified contracts (media-upload shape, metrics endpoint, MCP tool name) at once.
3. **Audit HIGH** (all still open, see `fanops-build-deviations.md`): H1/H2 (staggering math — rank-by-account + non-monotonic + two clips collide on a minute), H3 (tag-cluster gate defeated by tag_log overwrite), H4 (the `submitting`+`needs_reconcile` reconcile/poll step — backlog item (a)), H6 (self-healing ledger lock — orphaned lock hard-downs cron), H7 (wrap LLM responder — `fanops run` tracebacks if it raises), H8 (typed `BlotatoAuthError` vs the unpinned 401 string-match), H9 (pin `lift_score` weights), H10 (CI that fails when toolchain absent).
4. **Pre-existing backlog** (`RUNTIME.md §Backlog`): REST backoff jitter (item c, network-error half now done); externalize brand-risk lists + lift weights; per-platform max-duration clamp; `fanops unhold`; ruff + helper consolidation; per-source ranking; media size cap; the plan enhancements.

**Pick up here:**
- **Recommended next:** remediate **C2** (per-surface media derivative) — the last "Block LIVE" finding. It's an opsec defect, not a correctness one, so TDD around `run.py:ensure_clip_media`/the per-surface upload path: assert two surfaces of the same clip get *distinct* media URLs (and ideally distinct bytes). Scoped in `fanops-build-deviations.md`. After C2, the only thing between here and a safe staging run is the sandbox smoke test (item 2) + the 3 human-only steps.
- Alternatively, if you'd rather de-risk the unverified Blotato contracts first: run the sandbox smoke test (item 2) — it's cheap and collapses several unknowns.
- Before any LIVE run: the 3 human-only steps (create accounts → connect in Blotato → paste account_ids + set active). Reference: `RUNTIME.md` "three human-only steps".
- Standing: `main` pushed to private `origin` (Fleezyflo/fanops); working tree clean. The audit workflow is saved at `.claude/workflows/fanops-deep-audit.js` (re-runnable — but tighten its agents to read-only first, per the process-learning note in the deviation memo).
