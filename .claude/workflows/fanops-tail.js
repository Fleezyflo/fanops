export const meta = {
  name: 'fanops-tail',
  description: 'The credential-free tail: preflight auth-check + 5 backlog-hardening tasks (b externalize lists, e media size cap, g per-platform duration clamp, h externalize YT title, i ruff+dedup). Strict TDD, independent verify, adversarial mutation proofs. NOT the C2 feature.',
  whenToUse: 'After the live-autonomous plan is complete; the last credential-free work before the operator-gated cutover. Resume on failure via {scriptPath, resumeFromRunId}.',
  phases: [
    { title: 'Preflight', detail: 'confirm base state + worktree/venv targets' },
    { title: 'Implement', detail: 'STRICT TDD, sequential in one worktree: T1 preflight, T2 externalize, T3 media-cap, T4 duration-clamp, T5 yt-title, T6 ruff+dedup; commit per task' },
    { title: 'Verify', detail: 'independent re-run of each task + full suite' },
    { title: 'Adversarial', detail: '>=2 skeptics per task, majority vote, mutation proofs' },
    { title: 'Integrate', detail: 'full suite + real-CLI smoke + ruff clean' },
    { title: 'Close', detail: 'sync-docs (backlog status), push, PR, CI watch' },
  ],
}

const ROOT = '/Users/molhamhomsi/Moh Flow Fanops'
const WT = '/Users/molhamhomsi/Moh Flow Fanops-tail'
const BRANCH = 'tail-hardening'
const VENVRUN = `cd "${WT}" && source .venv/bin/activate &&`

// ── schemas ───────────────────────────────────────────────────────────────
const PREFLIGHT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['base_ok', 'head', 'baseline_count', 'no_open_prs', 'targets_present', 'notes', 'stop'],
  properties: {
    base_ok: { type: 'boolean' },
    head: { type: 'string' },
    baseline_count: { type: 'string' },
    no_open_prs: { type: 'boolean' },
    targets_present: { type: 'boolean', description: 'all the files/symbols the 6 tasks edit exist' },
    notes: { type: 'string' },
    stop: { type: 'boolean' },
  },
}
const IMPL_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['task_id', 'red', 'red_evidence', 'green', 'green_evidence', 'suite_count', 'files', 'commit_sha', 'commit_subject'],
  properties: {
    task_id: { type: 'string' },
    red: { type: 'boolean' },
    red_evidence: { type: 'string' },
    green: { type: 'boolean' },
    green_evidence: { type: 'string' },
    suite_count: { type: 'string' },
    files: { type: 'array', items: { type: 'string' } },
    commit_sha: { type: 'string' },
    commit_subject: { type: 'string' },
  },
}
const VERIFY_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['task_id', 'verified', 'count', 'notes'],
  properties: {
    task_id: { type: 'string' },
    verified: { type: 'boolean' },
    count: { type: 'string' },
    notes: { type: 'string' },
  },
}
const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['task_id', 'skeptic', 'real', 'mutation_proven', 'mutation_evidence', 'any_bypass', 'notes'],
  properties: {
    task_id: { type: 'string' },
    skeptic: { type: 'string' },
    real: { type: 'boolean' },
    mutation_proven: { type: 'boolean' },
    mutation_evidence: { type: 'string' },
    any_bypass: { type: 'string' },
    notes: { type: 'string' },
  },
}
const INTEGRATE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['suite_count', 'real_cli_status_exit0', 'ruff_clean', 'regressions', 'blocked', 'evidence'],
  properties: {
    suite_count: { type: 'string' },
    real_cli_status_exit0: { type: 'boolean' },
    ruff_clean: { type: 'boolean', description: 'ruff check src/ passes (task i added ruff)' },
    regressions: { type: 'string' },
    blocked: { type: 'boolean' },
    evidence: { type: 'string' },
  },
}
const CLOSE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['docs_updated', 'pushed', 'pr_url', 'ci_status', 'ci_run_url', 'notes'],
  properties: {
    docs_updated: { type: 'array', items: { type: 'string' } },
    pushed: { type: 'boolean' },
    pr_url: { type: 'string' },
    ci_status: { type: 'string' },
    ci_run_url: { type: 'string' },
    notes: { type: 'string' },
  },
}

// ════════════════════════════════════════════════════════════════════════
phase('Preflight')
const pf = await agent(
  `Preflight for the FanOps credential-free TAIL build. Read-only in "${ROOT}" (do NOT create the worktree yet).
1. base_ok/head: \`cd "${ROOT}" && git rev-parse HEAD\` (expect 1dee47d... on main), \`git status --porcelain\` empty.
2. baseline_count: \`cd "${ROOT}" && source .venv/bin/activate && python -m pytest -q 2>&1 | tail -3\` (expect "299 passed, 1 skipped").
3. no_open_prs: \`gh pr list --repo Fleezyflo/fanops --state open\` (expect empty).
4. targets_present — confirm these edit sites exist (Read/grep): cli.py (_check_accounts + _dispatch run/advance branches); caption.py _OFFBRAND_EN/_OFFBRAND_AR (lines ~15-20) + brand_risk_flag; track.py _W (line ~15) + lift_score; src/fanops/post/media.py upload_media (PUT timeout=120, line ~38); crosspost.py crosspost_clips surface loop (line ~71); src/fanops/post/payload.py title fallback ("title": title or "Moh Flow", line ~15); the _parse dup sites (crosspost.py:17, tagging.py:19, post/run.py:15, pipeline.py:28) + BASE_URL dup sites (post/media.py:15, post/metrics.py:14, post/blotato_rest.py:40). NOTE for T4 (duration clamp): clip playable duration = the MOMENT's window, i.e. \`Moment.start\`/\`Moment.end\` floats (models.py ~73-74) -> duration = end - start; and \`Source.duration\` (models.py:57) is the full source length. There is NO \`Clip.duration\` field (Clip = id/parent_id/state/path/aspect/held/held_reason/tagged_artist/media_url/meta_captions/error_reason) and line 115's \`duration\` is on \`MomentRequest\` (an agent contract), NOT a unit — confirm both facts. Do NOT require Clip.duration for targets_present.
5. config attrs for the preflight: confirm Config.responder_mode, Config.poster_backend, Config.blotato_api_key exist (config.py). Confirm Accounts.load(cfg).validate() exists (accounts.py) — the preflight reuses the account-validation seam.
stop=true only if a target is genuinely missing. Return ONLY structured JSON.`,
  { schema: PREFLIGHT_SCHEMA, phase: 'Preflight', label: 'preflight' }
)
log(`Preflight: base_ok=${pf.base_ok} baseline=${pf.baseline_count} no_open_prs=${pf.no_open_prs} targets=${pf.targets_present} stop=${pf.stop}`)
if (pf.stop || !pf.base_ok || !pf.targets_present) {
  return { blocked: true, phase: 'Preflight', preflight: pf }
}

// ════════════════════════════════════════════════════════════════════════
// IMPLEMENT — sequential in one worktree (git-safe; tasks are logically independent
// but several touch shared files: (g)+(i) both touch crosspost.py; (e)+(i) both touch
// media.py; so (i) dedup runs LAST after every file it consolidates is otherwise settled).
// ════════════════════════════════════════════════════════════════════════
phase('Implement')

const SETUP = `STEP 0 — WORKTREE + VENV (ONCE, you are the first implementer):
- \`cd "${ROOT}" && git worktree add "${WT}" -b ${BRANCH} main\` (if it already exists from a resume: \`cd "${WT}" && git status\` and skip).
- \`cd "${WT}" && python3.12 -m venv .venv && source .venv/bin/activate && pip install -q -e ".[dev]"\`. Verify pytest-timeout: \`pip show pytest-timeout | head -2\`.
- Baseline: \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` -> "299 passed, 1 skipped".
`

// ── T1: preflight auth-check ──────────────────────────────────────────────
const t1 = await agent(
  `T1 implementer for the FanOps tail build. ${SETUP}

TASK T1 — STARTUP PREFLIGHT AUTH-CHECK (the silent-zero-output guard; recommended since Phase A). Add a credential-free preflight that warns/blocks when the operator's config would silently produce zero output:
- when FANOPS_RESPONDER=llm but ANTHROPIC_API_KEY is unset (claude --bare ignores OAuth -> the autonomous responder yields NOTHING but logs nothing loud) — this is the #1 cutover trap.
- when FANOPS_POSTER in {rest, mcp} but BLOTATO_API_KEY is unset (publishing will fail auth).
Design: mirror the existing _check_accounts(cfg) pattern in cli.py (it returns 0 clean / prints problems + returns 2). Add a sibling _check_preflight(cfg) -> int that checks the two conditions above and is called in BOTH the \`run\` and \`advance\` dispatch branches right next to \`if (rc := _check_accounts(cfg)): return rc\`. A misconfig prints a clear actionable line to stderr and returns 2 (config-level, same as _check_accounts). Decision: make ANTHROPIC_API_KEY-missing-with-llm a HARD exit 2 (it guarantees zero content), and BLOTATO_API_KEY-missing-with-rest/mcp a HARD exit 2 (publish will 401). Read os.environ for ANTHROPIC_API_KEY directly (there is no Config attr for it yet — add a Config.anthropic_api_key property mirroring blotato_api_key, reading ANTHROPIC_API_KEY, for symmetry/testability). Default dryrun+manual config must pass cleanly (exit 0).

STRICT TDD (write tests in tests/test_cli.py):
1. RED — add tests:
   - test_preflight_blocks_llm_without_anthropic_key: monkeypatch FANOPS_RESPONDER=llm, ensure ANTHROPIC_API_KEY unset (monkeypatch.delenv(..., raising=False)); a scratch tmp_path cfg with a valid active account so _check_accounts passes; assert main(["advance"]) (or a direct _check_preflight(cfg)) returns 2 and a clear message. (Use monkeypatch.chdir(tmp_path) + a minimal accounts.json with one active numeric-id account, OR call _check_preflight(Config(root=tmp_path)) directly to isolate from _check_accounts.)
   - test_preflight_blocks_rest_without_blotato_key: FANOPS_POSTER=rest, BLOTATO_API_KEY unset -> exit 2.
   - test_preflight_passes_default_dryrun_manual: no env set (delenv both) -> _check_preflight returns 0.
   Run \`${VENVRUN} python -m pytest tests/test_cli.py -k preflight -v 2>&1 | tail -20\` -> CONFIRM the new tests FAIL (no _check_preflight). red_evidence.
2. IMPL — add Config.anthropic_api_key property (config.py, mirror blotato_api_key reading ANTHROPIC_API_KEY); add _check_preflight(cfg) in cli.py (sibling to _check_accounts); call it in the run AND advance dispatch branches (after _check_accounts). Keep messages actionable ("FANOPS_RESPONDER=llm but ANTHROPIC_API_KEY is not set — the --bare responder reads no OAuth/keychain, so it will produce zero content. Export ANTHROPIC_API_KEY.").
3. GREEN — \`${VENVRUN} python -m pytest tests/test_cli.py -k preflight -v 2>&1 | tail -10\` PASS; full suite \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` (expect 299 + N new = e.g. "302 passed, 1 skipped"). Confirm a default dryrun \`cd $(mktemp -d) && python -m fanops.cli status\` still exits 0 (status doesn't gate on preflight; only advance/run do).
4. COMMIT — \`cd "${WT}" && git add -A && git commit -m "feat (cutover-safety): startup preflight blocks the silent-zero-output misconfig (llm responder w/o ANTHROPIC_API_KEY; rest/mcp poster w/o BLOTATO_API_KEY)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`. sha + subject.
Return ONLY structured JSON.`,
  { schema: IMPL_SCHEMA, phase: 'Implement', label: 'impl:T1-preflight' }
)
log(`T1 preflight: red=${t1.red} green=${t1.green} suite=${t1.suite_count} sha=${(t1.commit_sha||'').slice(0,8)}`)

// ── T2: externalize tunable lists (b) ─────────────────────────────────────
const t2 = await agent(
  `T2 implementer. Worktree "${WT}" exists with venv; T1 committed. \`cd "${WT}" && git log --oneline -2\`.

TASK T2 — EXTERNALIZE THE TUNABLE LISTS (backlog (b)): let the operator tune the HOLD gate + optimization target WITHOUT a code change. Two hardcoded constants:
- caption._OFFBRAND_EN / _OFFBRAND_AR (caption.py ~15-20) — the brand-risk regex anti-patterns.
- track._W (track.py ~15) — the lift weights.
Design (keep it SIMPLE + backward compatible): read overrides from a config file but fall back to the current hardcoded defaults when absent (so existing behavior is unchanged and no new required file). Recommended approach: a single optional JSON file at cfg.control / "tuning.json" (add cfg.tuning_path in config.py, mirroring context_path). Shape: {"offbrand_en": [...], "offbrand_ar": [...], "lift_weights": {"saves":4.0,...}}. If the file is absent or a key is missing, use the in-code default (the current values stay as the module-level DEFAULT constants). caption.brand_risk_flag and track.lift_score read the effective lists/weights. IMPORTANT: caption._RE is currently compiled at import; make the patterns resolvable at call time when an override exists (e.g. brand_risk_flag(caption, cfg=None) compiles from cfg overrides if given, else uses the precompiled default _RE — keep the no-cfg call working for existing callers/tests). Similarly lift_score(metrics, weights=None) uses weights or the default _W. Then thread cfg through the ONE caller of brand_risk_flag (ingest_captions in caption.py) and the relevant lift_score caller, OR keep the default-arg path so existing callers are untouched and only NEW behavior reads overrides — pick the minimal wiring that (a) keeps all existing tests green and (b) makes an override file actually take effect.

STRICT TDD (tests in tests/test_caption.py and tests/test_track.py — read them first to match style):
1. RED — add:
   - test_offbrand_lists_overridable_from_tuning_json: write a tuning.json with a custom offbrand_en containing a benign word (e.g. "bananas") and NOT containing "sorry"; assert brand_risk_flag(..., cfg) flags "bananas..." and does NOT flag a caption that only the DEFAULT list would catch — proving the override replaced the default. (Decide replace-vs-merge: REPLACE when an override key is present is the clearest contract; document it.)
   - test_lift_weights_overridable_from_tuning_json: tuning.json with lift_weights {"likes": 10.0}; assert lift_score({"likes":1}, weights-from-cfg) == 10.0 (vs default 0.05).
   - test_defaults_unchanged_without_tuning_json: no file -> brand_risk_flag default behavior + lift_score default _W intact.
   Run the new tests -> CONFIRM FAIL. red_evidence.
2. IMPL — config.py tuning_path + a small loader (e.g. cfg.tuning() -> dict, cached-safe, returns {} if absent/corrupt — corrupt should NOT crash a run; mirror the ControlFileError philosophy but for an OPTIONAL file, prefer returning {} + maybe a logged warning over raising). caption.py + track.py read overrides. Keep _OFFBRAND_EN/_AR/_W as the named DEFAULTS.
3. GREEN — new tests pass; \`${VENVRUN} python -m pytest tests/test_caption.py tests/test_track.py -q 2>&1 | tail -4\` green; FULL suite \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\`. Confirm NO existing caption/track test regressed.
4. COMMIT — \`cd "${WT}" && git add -A && git commit -m "feat (audit b): externalize brand-risk lists + lift weights to optional tuning.json (operator-tunable HOLD gate + optimization target; defaults unchanged)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`. sha + subject.
Return ONLY structured JSON.`,
  { schema: IMPL_SCHEMA, phase: 'Implement', label: 'impl:T2-externalize' }
)
log(`T2 externalize: red=${t2.red} green=${t2.green} suite=${t2.suite_count} sha=${(t2.commit_sha||'').slice(0,8)}`)

// ── T3: media size cap + size-aware timeout (e) ───────────────────────────
const t3 = await agent(
  `T3 implementer. Worktree "${WT}" exists; T1,T2 committed.

TASK T3 — MEDIA SIZE CAP + SIZE-AWARE UPLOAD TIMEOUT (backlog (e)). In src/fanops/post/media.py, upload_media currently does the binary PUT with a FIXED timeout=120 and no size cap. Add:
- A max-size guard: reject (raise a clear RuntimeError, NOT BlotatoAuthError) a file above a cap BEFORE uploading. Make the cap a module constant (e.g. _MAX_UPLOAD_BYTES) with a sensible default for short vertical clips (e.g. 500 MB = 500*1024*1024 — clips are short by design; pick a value that's generous but catches a runaway). Optionally allow an env override (cfg) but a constant is fine.
- A size-aware PUT timeout: instead of a flat 120s, scale with file size (e.g. base 60s + size_mb * a per-MB allowance, clamped to a max e.g. 600s) so a larger (but allowed) file isn't killed mid-upload while a tiny file isn't waited on forever. Keep the presign POST timeout as-is (30s).
Read media.py first (upload_media ~line 20-42). Keep dryrun + ensure_clip_media untouched.

STRICT TDD (tests in tests/test_media.py — read it first):
1. RED — add:
   - test_upload_rejects_oversize_file: monkeypatch a path whose stat().st_size exceeds the cap (or write a tiny file and monkeypatch _MAX_UPLOAD_BYTES down to e.g. 1 byte, OR mock Path.stat); assert upload_media raises RuntimeError mentioning size, and that requests.post/put are NEVER called (mock them, assert not called) — i.e. it fails BEFORE any network.
   - test_put_timeout_scales_with_size: mock requests.post (presign 200 returning presignedUrl/publicUrl) and requests.put (capture the timeout kwarg); assert the PUT timeout for a larger file > the timeout for a tiny file (or >= the 60s base, and clamped at the max). Use mocker to assert the timeout value passed.
   Run -> CONFIRM FAIL. red_evidence.
2. IMPL — add _MAX_UPLOAD_BYTES + a _put_timeout_for(size_bytes) helper; guard size before presign; pass the scaled timeout to the PUT.
3. GREEN — new tests pass; \`${VENVRUN} python -m pytest tests/test_media.py -q 2>&1 | tail -4\`; FULL suite. No regressions.
4. COMMIT — \`cd "${WT}" && git add -A && git commit -m "feat (audit e): media upload size cap + size-aware PUT timeout (reject runaway uploads pre-network; don't kill a large-but-valid upload mid-PUT)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`. sha + subject.
Return ONLY structured JSON.`,
  { schema: IMPL_SCHEMA, phase: 'Implement', label: 'impl:T3-media-cap' }
)
log(`T3 media-cap: red=${t3.red} green=${t3.green} suite=${t3.suite_count} sha=${(t3.commit_sha||'').slice(0,8)}`)

// ── T4: per-platform duration clamp (g) ───────────────────────────────────
const t4 = await agent(
  `T4 implementer. Worktree "${WT}" exists; T1-T3 committed. This task touches crosspost.py + models.py (NOT media.py).

TASK T4 — PER-PLATFORM DURATION CLAMP (backlog (g)). Enforce a per-surface max clip length at crosspost time. The earlier unenforced PLATFORM_MAX_SECONDS dict was REMOVED as a false safety contract (a 180s pick fanned to YouTube/Twitter at full length, unflagged) — so re-introduce it AS A REAL ENFORCEMENT this time. Read crosspost.py (crosspost_clips, the surface loop ~line 69-98) and models.py (Platform enum ~L20s; Moment.start/Moment.end floats ~L73-74; Source.duration ~L57).
IMPORTANT — there is NO Clip.duration field. A clip's PLAYABLE duration is its MOMENT's window: \`m = led.moments[clip.parent_id]; dur = m.end - m.start\` (the clip is rendered from [start,end] of the source). Use that as the primary duration. \`led.sources[m.parent_id].duration\` (the full source length) is available as a sanity bound but the moment window is the right value (the clip is only the window, not the whole source). Confirm both by reading models.py: Moment has start/end (L73-74), Source has duration (L57), Clip has NEITHER.
Design: define a PLATFORM_MAX_SECONDS mapping (Platform -> int) for platforms with hard caps (real limits: e.g. instagram reels ~90s, tiktok ~600s, youtube shorts ~60s, twitter ~140s, threads/bluesky/etc as applicable — use reasonable real values; document them; platforms with no meaningful short-form cap can be omitted = no clamp). At crosspost, for each (clip, surface): compute dur = end-start from the seed clip's moment; if dur is known (>0) AND exceeds PLATFORM_MAX_SECONDS[platform], DO NOT create a queued post for that surface — SKIP it (conservative; the "hold-vs-skip per surface" the backlog names; SKIP not hold, so the clip can still post to platforms whose cap it satisfies and the whole clip isn't wedged). If dur is None/0/unknown OR the platform has no cap, DO NOT skip (fail-open — never silently drop a post because duration wasn't probed; the old code posted regardless). The moment is ALWAYS present for a captioned clip (clip.parent_id -> a real moment), but guard a missing moment defensively (treat as unknown -> fail-open).

STRICT TDD (tests in tests/test_crosspost.py — read it first to match fixtures):
1. RED — add test_crosspost_skips_surface_when_clip_exceeds_platform_max: build a Source + a Moment whose (end - start) duration exceeds ONE platform's cap but is under another's (e.g. start=0.0, end=120.0 -> 120s: over youtube-shorts ~60s, under tiktok ~600s), then a captioned Clip whose parent_id is that moment (with meta_captions for both surfaces so a post would otherwise be created); cross-post to accounts spanning both platforms; assert NO post for the over-cap surface AND a post WAS created for the under-cap surface (per-surface, not all-or-nothing). Read existing test_crosspost.py fixtures to see how Source/Moment/Clip + Accounts are built (match that style; the moment must exist in the ledger so clip.parent_id resolves). Also add test_crosspost_posts_when_duration_unknown: a moment with start/end that yields an unknown/None-equivalent duration (or a clip whose moment is absent/0-length) -> posts to all surfaces (fail-open).
   Run -> CONFIRM FAIL (currently it posts to the over-cap surface). red_evidence.
2. IMPL — PLATFORM_MAX_SECONDS in models.py (or crosspost.py); the per-surface skip in crosspost_clips. Document the chosen limits + the fail-open-on-unknown rule in a comment.
3. GREEN — new tests pass; \`${VENVRUN} python -m pytest tests/test_crosspost.py -q 2>&1 | tail -4\`; FULL suite. No regressions (existing crosspost tests likely use short/None durations -> unaffected; VERIFY).
4. COMMIT — \`cd "${WT}" && git add -A && git commit -m "feat (audit g): per-platform duration clamp at crosspost (skip a surface whose platform cap the clip exceeds; fail-open on unknown duration) — replaces the removed false-contract dict with real enforcement

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`. sha + subject.
Return ONLY structured JSON.`,
  { schema: IMPL_SCHEMA, phase: 'Implement', label: 'impl:T4-duration-clamp' }
)
log(`T4 duration-clamp: red=${t4.red} green=${t4.green} suite=${t4.suite_count} sha=${(t4.commit_sha||'').slice(0,8)}`)

// ── T5: externalize YouTube title fallback (h) ────────────────────────────
const t5 = await agent(
  `T5 implementer. Worktree "${WT}" exists; T1-T4 committed. Touches src/fanops/post/payload.py + config.py.

TASK T5 — EXTERNALIZE THE HARDCODED YOUTUBE TITLE FALLBACK (backlog (h)). In src/fanops/post/payload.py:~15 the youtube target uses \`"title": title or "Moh Flow"\` — a hardcoded artist name. Move the fallback to config. Read payload.py first (the youtube branch). Also note tagging.py has ARTIST_HANDLE (the @handle) — the title fallback is the DISPLAY NAME, related but distinct; keep them separate unless they're obviously the same source.
Design: add a Config property (e.g. cfg.artist_name -> os.getenv("FANOPS_ARTIST_NAME") or "Moh Flow") so the default is unchanged but an operator can override. Thread cfg (or the resolved name) into build_blotato_payload's youtube branch. If build_blotato_payload doesn't currently receive cfg, pass the name down from its caller (read who calls it — likely the poster/publish path); the minimal change is to add an optional param defaulting to "Moh Flow" so existing callers/tests are unaffected, and have the real publish path pass cfg.artist_name.

STRICT TDD (tests in tests/test_payload.py — read it first):
1. RED — add test_youtube_title_fallback_from_config: call the youtube payload path with no explicit title but an artist_name override = "Custom Artist"; assert the payload title == "Custom Artist". And test_youtube_title_default_unchanged: no override -> "Moh Flow".
   Run -> CONFIRM FAIL (currently always "Moh Flow"). red_evidence.
2. IMPL — Config.artist_name; thread into payload.
3. GREEN — new tests pass; \`${VENVRUN} python -m pytest tests/test_payload.py -q 2>&1 | tail -4\`; FULL suite. No regressions.
4. COMMIT — \`cd "${WT}" && git add -A && git commit -m "feat (audit h): externalize the YouTube title fallback to config (FANOPS_ARTIST_NAME; default 'Moh Flow' unchanged)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`. sha + subject.
Return ONLY structured JSON.`,
  { schema: IMPL_SCHEMA, phase: 'Implement', label: 'impl:T5-yt-title' }
)
log(`T5 yt-title: red=${t5.red} green=${t5.green} suite=${t5.suite_count} sha=${(t5.commit_sha||'').slice(0,8)}`)

// ── T6: ruff + dedup _parse/BASE_URL (i) — RUNS LAST (touches the most files) ──
const t6 = await agent(
  `T6 implementer. Worktree "${WT}" exists; T1-T5 committed. This is the LAST task and touches the most files — run only after all others are settled.

TASK T6 — LINT (ruff) + DEDUP the duplicated helpers (backlog (i)). Two parts:
PART A — extract the duplicated helpers into ONE home each:
- \`_parse(ts)\` (datetime.fromisoformat(ts.replace("Z","+00:00"))) is duplicated in crosspost.py:17, tagging.py:19, post/run.py:15, pipeline.py:28. Create a shared module \`src/fanops/timeutil.py\` with \`parse_iso(ts) -> datetime\` (and consider \`iso_z(dt) -> str\` for the inverse used in crosspost.surface_time). Replace each local _parse with an import from timeutil (keep a module-level \`_parse = parse_iso\` alias if it minimizes churn, or just import parse_iso and use it). pipeline.py:28's \`_parse(ts)\` has no type annotation — unify it.
- \`BASE_URL = "https://backend.blotato.com/v2"\` is duplicated in post/media.py:15, post/metrics.py:14, post/blotato_rest.py:40. Put it in ONE place — e.g. a constant in a small shared module (src/fanops/post/__init__.py or a new src/fanops/post/blotato_base.py) — and import it in the three sites.
  CAUTION: T3 just edited media.py and T4 did not, but several modules changed; re-read each file's CURRENT content before editing (do NOT assume line numbers). Make the dedup a pure refactor: NO behavior change.
PART B — add ruff to dev deps + make the tree lint-clean:
- Add \`ruff\` to the [project.optional-dependencies] dev list in pyproject.toml, install it (\`${VENVRUN} pip install -q -e ".[dev]"\` then \`pip show ruff | head -2\`).
- Add a minimal ruff config to pyproject.toml ([tool.ruff] with a reasonable line-length e.g. 120 and a conservative select e.g. E,F,I or just the defaults; do NOT enable aggressive rules that would demand a huge diff — the goal is enforcement going forward, not a massive reformat).
- Run \`${VENVRUN} ruff check src/ 2>&1 | tail -30\`. Fix REAL issues (unused imports, undefined names) but if ruff flags a large stylistic set, NARROW the config (per-file-ignores or a tighter select) so \`ruff check src/\` passes CLEAN without a sweeping reformat. The deliverable is "ruff is wired + \`ruff check src/\` is green", not "every default rule satisfied".

STRICT TDD / verification (a refactor's "test" is the existing suite staying green + a structural assertion):
1. BEFORE: \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` (record the count). \`grep -rn "def _parse" src/fanops/\` (4 sites) and \`grep -rn 'BASE_URL = ' src/fanops/\` (3 sites).
2. IMPL Part A + B.
3. GREEN — \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` SAME count as before (a pure refactor adds no tests unless you add a tiny timeutil unit test — OPTIONAL: add tests/test_timeutil.py with parse_iso round-trip, which would +1). \`grep -rn "def _parse" src/fanops/\` now shows the helper defined ONCE in timeutil.py (+ optional aliases). \`grep -rn 'BASE_URL = ' src/fanops/\` shows ONE definition. \`${VENVRUN} ruff check src/ 2>&1 | tail -5\` -> "All checks passed!" (or no output / exit 0). Real-CLI smoke: \`cd "${WT}" && source .venv/bin/activate && cd $(mktemp -d) && python -m fanops.cli status; echo EXIT=$?\` -> 0.
4. COMMIT — \`cd "${WT}" && git add -A && git commit -m "refactor (audit i): consolidate _parse -> timeutil.parse_iso + BASE_URL to one home; add ruff lint (ruff check src/ green)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`. sha + subject. List ALL files touched.
Return ONLY structured JSON.`,
  { schema: IMPL_SCHEMA, phase: 'Implement', label: 'impl:T6-ruff-dedup' }
)
log(`T6 ruff-dedup: red=${t6.red} green=${t6.green} suite=${t6.suite_count} sha=${(t6.commit_sha||'').slice(0,8)}`)

const impls = { T1: t1, T2: t2, T3: t3, T4: t4, T5: t5, T6: t6 }
const allGreen = Object.values(impls).every(r => r && r.green && r.commit_sha)
if (!allGreen) {
  log(`IMPLEMENT BLOCKED: ${JSON.stringify(Object.fromEntries(Object.entries(impls).map(([k,v])=>[k,{red:v?.red,green:v?.green,sha:(v?.commit_sha||'').slice(0,8)}])))}`)
  return { blocked: true, phase: 'Implement', impls }
}

// ════════════════════════════════════════════════════════════════════════
phase('Verify')
const vtasks = [
  { id: 'T1', test: 'tests/test_cli.py -k preflight' },
  { id: 'T2', test: 'tests/test_caption.py tests/test_track.py -k "tuning or override or default"' },
  { id: 'T3', test: 'tests/test_media.py -k "oversize or timeout or size"' },
  { id: 'T4', test: 'tests/test_crosspost.py -k "duration or clamp or platform or unknown"' },
  { id: 'T5', test: 'tests/test_payload.py -k "title or artist"' },
  { id: 'T6', test: 'tests/' },
]
const verifies = await parallel(vtasks.map(t => () =>
  agent(
    `INDEPENDENT Verify agent for FanOps tail task ${t.id} (you did NOT implement it). Worktree "${WT}", venv .venv.
1. \`${VENVRUN} python -m pytest ${t.test} -v 2>&1 | tail -15\` — the task's tests PASS.
2. FULL suite: \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` — QUOTE the count.
${t.id === 'T6' ? `3. SPECIAL T6: \`${VENVRUN} ruff check src/ 2>&1 | tail -5\` is CLEAN (exit 0 / "All checks passed!"). \`grep -rn "def _parse" "${WT}/src/fanops/"\` -> the body defined ONCE (timeutil.py); \`grep -rn 'BASE_URL = ' "${WT}/src/fanops/"\` -> ONE definition. Real-CLI \`cd "${WT}" && source .venv/bin/activate && cd $(mktemp -d) && python -m fanops.cli status; echo EXIT=$?\` -> 0.` : `3. Confirm the change actually took effect (not a vacuous pass) — read the diff for this task's files: \`cd "${WT}" && git show --stat HEAD~${6 - Number(t.id.slice(1))}\` is NOT reliable; instead grep the implemented symbol exists.`}
verified=true ONLY if the task tests pass AND the special check holds. Return ONLY structured JSON.`,
    { schema: VERIFY_SCHEMA, phase: 'Verify', label: `verify:${t.id}` }
  )
))
for (const v of verifies.filter(Boolean)) log(`Verify ${v.task_id}: verified=${v.verified} count="${v.count}"`)

// ════════════════════════════════════════════════════════════════════════
phase('Adversarial')
const advSpecs = {
  T1: [
    `Skeptic A: Prove the preflight ACTUALLY blocks FANOPS_RESPONDER=llm with no ANTHROPIC_API_KEY (exit 2), and PASSES the default dryrun+manual. MUTATION: temporarily neuter the llm-check (make _check_preflight always return 0), confirm test_preflight_blocks_llm_without_anthropic_key FAILS, then \`git checkout\` to restore (confirm git diff clean).`,
    `Skeptic B: Prove it's wired into BOTH run AND advance (not just one), and that a misconfig prints to stderr + exit 2 (not a traceback). MUTATION: remove the _check_preflight call from the advance branch, confirm an advance-path test FAILS (or add a throwaway assertion), restore. Also confirm status/ingest do NOT gate (only run/advance), i.e. the guard isn't over-applied.`,
  ],
  T2: [
    `Skeptic A: Prove an override tuning.json REALLY changes behavior — a custom offbrand list flags a word the default doesn't AND stops flagging a default word (REPLACE semantics). MUTATION: make the loader ignore the file (always return {}), confirm the override test FAILS, restore.`,
    `Skeptic B: Prove the DEFAULTS are unchanged when no tuning.json (every existing caption/track test green) AND a corrupt tuning.json does NOT crash (returns {} / logs, run continues). MUTATION: break the default-fallback (e.g. make lift_score require the cfg weights), confirm the no-file test FAILS, restore.`,
  ],
  T3: [
    `Skeptic A: Prove an oversize file is rejected BEFORE any network call (requests.post/put never called). MUTATION: remove the size guard, confirm test_upload_rejects_oversize_file FAILS (now it would attempt the network), restore.`,
    `Skeptic B: Prove the PUT timeout actually scales with size (larger file -> larger timeout, clamped) and the presign POST timeout is unchanged (30s). MUTATION: hardcode the PUT timeout back to a constant, confirm the scaling test FAILS, restore.`,
  ],
  T4: [
    `Skeptic A: Prove the clamp is PER-SURFACE — an over-cap clip is skipped for the over-cap platform but STILL posts to an under-cap platform (not all-or-nothing). MUTATION: make the skip a \`continue\` on the whole clip (break out of the surface loop) or remove the skip, confirm the per-surface test FAILS, restore.`,
    `Skeptic B: Prove FAIL-OPEN on unknown duration — a clip with duration None posts to ALL surfaces (never silently dropped). MUTATION: make unknown-duration skip, confirm test_crosspost_posts_when_duration_unknown FAILS, restore. Confirm the limits used are real per-platform values, not arbitrary.`,
  ],
  T5: [
    `Skeptic A: Prove the YouTube title fallback comes from config (override -> custom title) AND the default is still "Moh Flow". MUTATION: hardcode the title back to "Moh Flow", confirm the override test FAILS, restore.`,
  ],
  T6: [
    `Skeptic A (dedup is pure): Prove _parse is defined ONCE (timeutil.parse_iso) and BASE_URL once, with NO behavior change (full suite same count as pre-T6). Read timeutil.py + confirm the 3 BASE_URL sites import the shared constant. MUTATION: change the shared parse_iso to a WRONG impl (e.g. drop the Z replacement), confirm time-dependent tests (crosspost scheduling) FAIL across ALL consumers (proving they share it), restore.`,
    `Skeptic B (ruff real): Prove \`ruff check src/\` is genuinely green (run it) and ruff is in pyproject dev deps. Confirm the ruff config wasn't made vacuous (e.g. select=[] or ignore-everything) — read [tool.ruff] and confirm it selects at least E/F (or defaults). MUTATION: introduce an unused import in a src file, confirm \`ruff check src/\` now FAILS (proving ruff actually runs + catches), then remove it.`,
  ],
}
const advTasks = []
for (const [id, prompts] of Object.entries(advSpecs)) {
  for (let i = 0; i < prompts.length; i++) advTasks.push({ id, idx: i, prompt: prompts[i] })
}
const verdicts = await parallel(advTasks.map(t => () =>
  agent(
    `INDEPENDENT adversarial skeptic for FanOps tail task ${t.id} (you did NOT implement/verify it). Default real=false unless you positively confirm. Worktree "${WT}", venv .venv. After ANY mutation you MUST \`cd "${WT}" && git checkout <file>\` and confirm \`git status --porcelain\` CLEAN before returning — NEVER commit a mutation.

${t.prompt}

real=true ONLY if it genuinely satisfies the contract. mutation_proven=true ONLY if you reverted/injected, watched the test FAIL (capture the line), and restored clean. any_bypass = how it misbehaves (empty if none). Return ONLY structured JSON.`,
    { schema: VERDICT_SCHEMA, phase: 'Adversarial', label: `adv:${t.id}#${t.idx}` }
  )
))
const advByTask = {}
for (const v of verdicts.filter(Boolean)) (advByTask[v.task_id] ||= []).push(v)
const advConfirmed = {}
for (const id of Object.keys(advSpecs)) {
  const vs = advByTask[id] || []
  const realCount = vs.filter(v => v.real).length
  const mutCount = vs.filter(v => v.mutation_proven).length
  const confirmed = vs.length > 0 && realCount >= Math.ceil(vs.length / 2) && mutCount >= 1
  advConfirmed[id] = { confirmed, realCount, total: vs.length, mutCount, bypasses: vs.map(v => v.any_bypass).filter(Boolean) }
  log(`Adversarial ${id}: confirmed=${confirmed} real=${realCount}/${vs.length} mut=${mutCount} bypasses=${JSON.stringify(advConfirmed[id].bypasses)}`)
}

// ════════════════════════════════════════════════════════════════════════
phase('Integrate')
const allAdvOk = Object.keys(advSpecs).every(id => advConfirmed[id].confirmed)
const allVerifyOk = verifies.filter(Boolean).every(v => v.verified)
if (!allAdvOk || !allVerifyOk) {
  log(`PRE-INTEGRATE BLOCK: verifyOk=${allVerifyOk} advOk=${allAdvOk}`)
  return { blocked: true, phase: 'pre-Integrate', verifies, advConfirmed }
}
const integ = await agent(
  `Integrate agent for the FanOps tail build. Worktree "${WT}", venv .venv. All 6 tasks committed on branch ${BRANCH}.
1. FULL suite: \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` -> suite_count. regressions: any test green at baseline (299/1) now red? (expect none; count should be 299 + new task tests).
2. Real-CLI smoke (scratch cwd): \`cd "${WT}" && source .venv/bin/activate && cd $(mktemp -d) && python -m fanops.cli status; echo EXIT=$?\` -> real_cli_status_exit0. Also confirm the preflight works end-to-end: from a scratch dir with FANOPS_RESPONDER=llm and no ANTHROPIC_API_KEY + a valid active account, \`python -m fanops.cli advance\` exits 2 with the actionable message (quote it).
3. ruff: \`${VENVRUN} ruff check src/ 2>&1 | tail -5\` -> ruff_clean (exit 0).
4. Compose: confirm the 6 changes don't interfere — e.g. the dedup (T6) didn't break the duration clamp (T4) or media cap (T3); spot-check by reading that crosspost.py still has the clamp AND imports timeutil.
blocked=true if the suite regresses, ruff fails, or the preflight smoke doesn't exit 2. Return ONLY structured JSON.`,
  { schema: INTEGRATE_SCHEMA, phase: 'Integrate', label: 'integrate' }
)
log(`Integrate: suite=${integ.suite_count} status_exit0=${integ.real_cli_status_exit0} ruff_clean=${integ.ruff_clean} blocked=${integ.blocked}`)
if (integ.blocked) {
  return { blocked: true, phase: 'Integrate', integ, impls, verifies, advConfirmed }
}

// ════════════════════════════════════════════════════════════════════════
phase('Close')
const close = await agent(
  `Close agent for the FanOps tail build (superpowers:finishing-a-development-branch posture). All 6 tasks committed on ${BRANCH} in worktree "${WT}"; suite green (${integ.suite_count}); ruff clean. Work in the worktree.

DOCS (sync-docs — read then edit):
- MohFlow-FanOps/00_control/RUNTIME.md §Backlog: mark the now-DONE items — (b) externalize lists [DONE: tuning.json], (e) media size cap [DONE], (g) per-platform duration clamp [DONE: real enforcement, fail-open on unknown], (h) YouTube title fallback [DONE: FANOPS_ARTIST_NAME], (i) lint+dedup [DONE: ruff + timeutil.parse_iso + single BASE_URL]. Leave (d) per-source ranking and (j) C2 creative-variation OPEN. Add the new preflight to the operator runbook / env table (FANOPS_RESPONDER=llm now HARD-FAILS without ANTHROPIC_API_KEY at advance/run — document this as a SAFETY feature, the silent-zero-output guard).
- README.md: if it lists env vars / config, add FANOPS_ARTIST_NAME and the tuning.json override file and the preflight behavior. If there's a .env.example, add ANTHROPIC_API_KEY (commented) so the operator sees it's needed for the llm responder.
- docs_updated = files edited.
COMMIT docs: \`cd "${WT}" && git add -A && git commit -m "docs (tail): mark backlog (b)/(e)/(g)/(h)/(i) DONE; document the cutover-safety preflight + FANOPS_ARTIST_NAME + tuning.json

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`.

PUSH + PR + CI:
- \`cd "${WT}" && git push -u origin ${BRANCH} 2>&1\`. pushed.
- \`gh pr create --repo Fleezyflo/fanops --base main --head ${BRANCH} --title "Tail hardening: cutover-safety preflight + backlog (b)(e)(g)(h)(i)" --body "<short: T1 preflight silent-zero-output guard; b externalize brand-risk lists+lift weights to tuning.json; e media size cap+size-aware timeout; g per-platform duration clamp (real enforcement); h externalize YT title to FANOPS_ARTIST_NAME; i ruff+dedup _parse/BASE_URL. Suite ${integ.suite_count}; ruff clean. The credential-free tail; C2 creative-variation remains its own feature.>"\` -> pr_url.
- WATCH CI: \`gh run list --repo Fleezyflo/fanops --limit 3\` to find the ${BRANCH} run id, then \`gh run watch <id> --repo Fleezyflo/fanops --exit-status 2>&1 | tail -20\`. If the watch drops on a blip, RE-QUERY and re-watch — do NOT conclude failure from a dropped watch. ci_status = "completed success"/"failure"/other; ci_run_url = the run URL. Both jobs must be green.

deviations_recorded + handoff are the ORCHESTRATOR's job — do NOT touch ~/.claude memory. Return ONLY structured JSON.`,
  { schema: CLOSE_SCHEMA, phase: 'Close', label: 'close' }
)
log(`Close: docs=${JSON.stringify(close.docs_updated)} pushed=${close.pushed} ci=${close.ci_status}`)

return {
  blocked: false,
  preflight: pf,
  impls: Object.fromEntries(Object.entries(impls).map(([k, v]) => [k, { red: v.red, green: v.green, suite_count: v.suite_count, commit_sha: v.commit_sha, commit_subject: v.commit_subject, files: v.files }])),
  verifies: verifies.filter(Boolean),
  adversarial: advConfirmed,
  adversarial_verdicts: verdicts.filter(Boolean),
  integrate: integ,
  close,
  worktree: WT,
  branch: BRANCH,
}
