export const meta = {
  name: 'fanops-phase-d',
  description: 'Phase D (live Blotato seam): D1 client token, D2 robust id extraction, D3 MCP auth, D4 jittered backoff, D5 read-only live contract verification — deterministic TDD with independent verify + adversarial mutation proofs',
  phases: [
    { title: 'Preflight' },
    { title: 'Frame' },
    { title: 'Implement' },
    { title: 'Verify' },
    { title: 'Adversarial' },
    { title: 'Integrate' },
    { title: 'Close' },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared constants. ONE persistent worktree off main; every agent cd's in and
// activates the worktree's own venv (python3.12.8, fanops editable-installed there).
// ─────────────────────────────────────────────────────────────────────────────
const WT = '/Users/molhamhomsi/fanops-phase-d'
const VENV = `cd "${WT}" && source .venv/bin/activate`
const COAUTHOR = 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>'

// The verified-live Blotato contract FACTS the orchestrator captured from the connected
// MCP tool SCHEMAS (the MCP server itself is NOT reachable from agents this session — confirmed
// via 4 probes; agents must NOT attempt live MCP calls or fabricate results). These facts are
// authoritative for D5.
const BLOTATO_FACTS = `
VERIFIED LIVE BLOTATO CONTRACT (from connected MCP tool SCHEMAS — FACTS, do not re-guess; the MCP
server is NOT reachable from workflow agents this session, so D5 verifies against THESE, honestly
labelled "MCP not reachable in this run — verified against tool schemas"):
- Submission id field is postSubmissionId. blotato_create_post RETURNS it; blotato_get_post_status
  TAKES it as the required param. FanOps already uses postSubmissionId everywhere — CORRECT.
- Status enum: in-progress -> published | scheduled | failed. On published: get_post_status returns
  publicUrl. On scheduled: scheduledTime (UTC ISO8601). On failed: errorMessage. "Most failures are
  permanent — retrying the same submission is NOT recommended."
- REAL DIVERGENCE (the valuable D5 finding): blotato_list_posts returns each item's state.type in
  {scheduled,published,failed} with the published URL under postUrl — NOT publicUrl. So the SAME
  concept (live post URL) is publicUrl on get_post_status but postUrl on list_posts.
- blotato_list_accounts returns each account's numeric id, platform, username, subaccounts (FB
  pageId, LinkedIn company pages, YT playlists) and per-platform required fields (TikTok
  privacyLevel+flags, Pinterest boardId, YouTube title+privacyStatus, Facebook pageId).
- blotato_create_presigned_upload_url response keys: presignedUrl + publicUrl.
- blotato_create_post requires only (accountId, platform, text) and PUBLISHES to a REAL account —
  no dry-run. NEVER fire it autonomously.`

const PRIME_DIRECTIVE = `
PRIME DIRECTIVE (every agent must honour): NO ambiguous/maybe-live post may EVER be marked \`failed\`
(failed => re-queueable => double-post to a REAL fan account). A 2xx with no recognizable id, and a
non-auth MCP error, must become \`needs_reconcile\`, never \`failed\`. The state machine: run.py only
re-drives \`queued\`; \`needs_reconcile\` is parked, surfaced by the digest, never auto-requeued.`

const ENV_RULES = `
ENVIRONMENT (non-negotiable): work ONLY in the worktree ${WT} on branch phase-d-blotato. ALWAYS run
pytest as: ${VENV} && python -m pytest ...  (bare pytest mis-reports the \`mocker\` fixture — a known
project false-alarm). Never edit the parent repo /Users/molhamhomsi/Moh Flow Fanops. Your final
message IS your return value (JSON when a schema is given) — not a human message.`

// ─── JSON schemas (force structured outputs on every decision-feeding agent) ──────────────────
const PREFLIGHT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['base_ok', 'head', 'baseline_count', 'deps_present', 'missing_deps', 'real_body_notes'],
  properties: {
    base_ok: { type: 'boolean' },
    head: { type: 'string' },
    baseline_count: { type: 'string', description: 'literal pytest summary line, e.g. "258 passed, 1 skipped"' },
    deps_present: { type: 'boolean' },
    missing_deps: { type: 'array', items: { type: 'string' } },
    real_body_notes: { type: 'string', description: 'key facts about the CURRENT bodies that affect the impl' },
  },
}

const FRAME_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['tasks', 'state_machine_ok', 'state_machine_evidence'],
  properties: {
    state_machine_ok: { type: 'boolean', description: 'true iff run.py re-drives only queued AND digest surfaces needs_reconcile AND nothing auto-requeues needs_reconcile' },
    state_machine_evidence: { type: 'string', description: 'file:line citations proving the above' },
    tasks: {
      type: 'array', minItems: 4,
      items: {
        type: 'object', additionalProperties: false,
        required: ['id', 'files', 'failing_test', 'impl', 'ordering_deps'],
        properties: {
          id: { type: 'string' },
          files: { type: 'array', items: { type: 'string' } },
          failing_test: { type: 'string', description: 'the literal test name(s) that will be RED first' },
          impl: { type: 'string', description: 'concrete minimal impl plan against the real body' },
          ordering_deps: { type: 'array', items: { type: 'string' } },
        },
      },
    },
  },
}

const IMPL_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['tasks', 'final_suite_count', 'all_green'],
  properties: {
    all_green: { type: 'boolean' },
    final_suite_count: { type: 'string', description: 'literal full-suite summary after all 4 tasks' },
    tasks: {
      type: 'array', minItems: 4,
      items: {
        type: 'object', additionalProperties: false,
        required: ['task_id', 'red', 'green', 'suite_count', 'files', 'commit_sha', 'commit_msg'],
        properties: {
          task_id: { type: 'string' },
          red: { type: 'string', description: 'literal failing-test output proving RED before impl' },
          green: { type: 'string', description: 'literal passing-test output after impl' },
          suite_count: { type: 'string', description: 'full-suite count after this task committed' },
          files: { type: 'array', items: { type: 'string' } },
          commit_sha: { type: 'string' },
          commit_msg: { type: 'string', description: 'first line of the commit message' },
        },
      },
    },
  },
}

const VERIFY_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['tasks', 'full_suite_count', 'shared_helper_ok', 'shared_helper_evidence', 'overall_ok'],
  properties: {
    overall_ok: { type: 'boolean' },
    full_suite_count: { type: 'string' },
    shared_helper_ok: { type: 'boolean', description: 'true iff blotato_mcp.py IMPORTS _extract_submission_id from blotato_rest (no duplicate definition)' },
    shared_helper_evidence: { type: 'string', description: 'the import line + grep proving no second def' },
    tasks: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['task_id', 'verified', 'count', 'asserts_real_behavior', 'notes'],
        properties: {
          task_id: { type: 'string' },
          verified: { type: 'boolean' },
          count: { type: 'string', description: 'literal count for this task\'s tests' },
          asserts_real_behavior: { type: 'boolean', description: 'tests assert behaviour, not tautologies' },
          notes: { type: 'string' },
        },
      },
    },
  },
}

const SKEPTIC_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['task_id', 'lens', 'refuted', 'confidence', 'reasoning', 'prime_directive_safe'],
  properties: {
    task_id: { type: 'string' },
    lens: { type: 'string' },
    refuted: { type: 'boolean', description: 'true if you can REFUTE the claim (find a real hole)' },
    confidence: { type: 'string', enum: ['low', 'medium', 'high'] },
    prime_directive_safe: { type: 'boolean', description: 'true iff NO path marks a maybe-live post failed' },
    reasoning: { type: 'string', description: 'concrete evidence; if refuted, the exact failing case' },
  },
}

const MUTATION_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['task_id', 'mutation_applied', 'test_failed_under_mutation', 'restored_clean', 'evidence'],
  properties: {
    task_id: { type: 'string' },
    mutation_applied: { type: 'string', description: 'what guard was reverted / what mutation injected' },
    test_failed_under_mutation: { type: 'boolean', description: 'true iff the new test went RED under the mutation' },
    restored_clean: { type: 'boolean', description: 'true iff git status is clean after restore (throwaway never committed)' },
    evidence: { type: 'string', description: 'literal RED output under mutation + git status after restore' },
  },
}

const INTEGRATE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['full_unit_count', 'integration_count', 'compose_ok', 'compose_evidence',
             'd5_report', 'd5_mismatches', 'd5_fix_applied', 'd5_fix_commit', 'regressed', 'blocked'],
  properties: {
    full_unit_count: { type: 'string' },
    integration_count: { type: 'string' },
    compose_ok: { type: 'boolean', description: 'D1 token + D2 extraction + D3 MCP interlock compose; no path marks a maybe-live post failed' },
    compose_evidence: { type: 'string' },
    regressed: { type: 'boolean' },
    blocked: { type: 'boolean', description: 'true iff any task unconfirmed or suite regressed — STOP' },
    d5_report: {
      type: 'array', description: 'one row per FanOps field/assumption -> live reality -> match|MISMATCH',
      items: {
        type: 'object', additionalProperties: false,
        required: ['fanops_reader', 'fanops_key', 'live_endpoint', 'live_key', 'verdict'],
        properties: {
          fanops_reader: { type: 'string', description: 'file:line, e.g. reconcile.py:45' },
          fanops_key: { type: 'string', description: 'the key FanOps reads, e.g. publicUrl' },
          live_endpoint: { type: 'string', description: 'which Blotato endpoint that reader hits' },
          live_key: { type: 'string', description: 'the key that endpoint actually returns' },
          verdict: { type: 'string', enum: ['match', 'MISMATCH'] },
        },
      },
    },
    d5_mismatches: { type: 'array', items: { type: 'string' } },
    d5_fix_applied: { type: 'boolean' },
    d5_fix_commit: { type: 'string', description: 'sha if a TDD fix was committed, else ""' },
  },
}

const CLOSE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['cli_smoke_ok', 'cli_smoke_evidence', 'token_present', 'no_regression',
             'readme_drift', 'runtime_drift', 'deviation_note', 'commits'],
  properties: {
    cli_smoke_ok: { type: 'boolean' },
    cli_smoke_evidence: { type: 'string', description: 'literal: a crossposted Post carries a fanops_ token; advance exit 0 on a scratch root' },
    token_present: { type: 'boolean' },
    no_regression: { type: 'boolean' },
    readme_drift: { type: 'string', description: 'exact README lines that need updating (or "none")' },
    runtime_drift: { type: 'string', description: 'exact RUNTIME.md lines that need updating, incl the INTEGRATION CHECKPOINT comments to mark verified (or "none")' },
    deviation_note: { type: 'string', description: 'the full Phase D deviation-memo section text to append to fanops-build-deviations.md' },
    commits: { type: 'array', items: { type: 'string' }, description: 'all commit shas+first-lines made across the phase' },
  },
}

// ═════════════════════════════════════════════════════════════════════════════
// PHASE 1 — PREFLIGHT
// ═════════════════════════════════════════════════════════════════════════════
phase('Preflight')
const preflight = await agent(`You are the PREFLIGHT agent for FanOps Phase D.
${ENV_RULES}

Do, IN THE WORKTREE ${WT}:
1. Confirm git HEAD and branch: \`cd "${WT}" && git rev-parse HEAD && git branch --show-current && git status --porcelain\`. Branch must be phase-d-blotato; HEAD must be a2a5d4e (merged A/B/C main); tree clean.
2. Confirm baseline: \`${VENV} && python -m pytest -q 2>&1 | tail -3\`. Expect "258 passed, 1 skipped" (the 1 skip = creds-gated Blotato smoke).
3. Verify named deps EXIST in the committed worktree code (grep):
   - src/fanops/crosspost.py, src/fanops/post/run.py, src/fanops/post/blotato_rest.py, src/fanops/post/blotato_mcp.py, src/fanops/reconcile.py, src/fanops/track.py, src/fanops/post/metrics.py, src/fanops/post/media.py all present
   - reconcile_posts (reconcile.py), Post.public_url + PostState.needs_reconcile + Post.submission_id (models.py), BlotatoAuthError (errors.py), Clip.meta_captions (models.py), _hash (ids.py), Fmt.r9x16 (models.py)
4. Read the CURRENT bodies of blotato_rest.py, blotato_mcp.py, crosspost.py and note the exact lines that the impl will touch (the 2xx block in blotato_rest.publish; the result.get in blotato_mcp.publish; the Post(...) construction in crosspost_clips; the 429 branch). Report these as real_body_notes.

If any dep the plan's tests need is MISSING, set deps_present=false and list it in missing_deps (do NOT invent it). Return the schema JSON.`, { phase: 'Preflight', schema: PREFLIGHT_SCHEMA, label: 'preflight' })

if (!preflight.base_ok || !preflight.deps_present) {
  log(`PREFLIGHT FAILED: base_ok=${preflight.base_ok} deps_present=${preflight.deps_present} missing=${JSON.stringify(preflight.missing_deps)}`)
  return { status: 'blocked', phase: 'Preflight', preflight }
}
log(`Preflight OK — HEAD ${preflight.head}, baseline ${preflight.baseline_count}`)

// ═════════════════════════════════════════════════════════════════════════════
// PHASE 2 — FRAME
// ═════════════════════════════════════════════════════════════════════════════
phase('Frame')
const frame = await agent(`You are the FRAME agent for FanOps Phase D. Produce a per-task structured plan AND lock the key design judgment.
${ENV_RULES}
${PRIME_DIRECTIVE}

Read the plan's Phase D section in the MAIN repo at /Users/molhamhomsi/Moh Flow Fanops/docs/superpowers/plans/2026-06-01-fanops-live-autonomous.md (lines ~1032-1423) for the literal tests/impl, and the CURRENT worktree bodies. Preflight notes: ${JSON.stringify(preflight.real_body_notes)}.

The four tasks (the plan is authoritative; impl is concrete against the real bodies):
- D1 (crosspost.py + blotato_rest.py): stamp submission_id=f"fanops_{_hash('idemp', pid)}" on every Post at creation in crosspost_clips (so a crash/timeout never strands an id-less maybe-live post). In blotato_rest._reconcile, capture the body postSubmissionId OVER the client token (real id beats token). The plan shows _reconcile currently guards \`if resp is not None and not post.submission_id\` — that \`not post.submission_id\` now blocks capture because the token is always set, so the guard must change to capture the body id even when a token exists. Tests: tests/test_crosspost.py, tests/test_reconcile.py.
- D2 (blotato_rest.py + blotato_mcp.py): add shared _extract_submission_id(body)->str|None accepting ("postSubmissionId","submissionId","id") + nested data; use on the REST 2xx path. A 2xx with NO recognizable id -> needs_reconcile (may be live), NEVER failed (this REPLACES the current failed behaviour at blotato_rest.py:81-85 — note this is a behaviour change the existing test test_2xx_without_submission_id_marks_failed encodes, so that test must be REWRITTEN to assert needs_reconcile, same "rewrite the test that encoded the old behaviour" move used in prior phases). Tests: tests/test_blotato_rest.py.
- D3 (blotato_mcp.py): wrap the self._call; auth-class failures (401/403/unauthorized/forbidden/invalid token/api key in the lowercased message) -> raise BlotatoAuthError; non-auth -> per-post needs_reconcile (NOT a raw raise, NOT failed). IMPORT and REUSE D2's _extract_submission_id (no divergent copy). A no-id MCP result -> needs_reconcile. NOTE the existing test test_mcp_no_submission_id_marks_failed encodes the OLD failed behaviour -> rewrite to needs_reconcile. Tests: tests/test_blotato_mcp.py.
- D4 (blotato_rest.py): import random; change the 429 branch to time.sleep(delay + random.uniform(0, delay)); delay *= 2. Test: tests/test_blotato_rest.py.

LOCK THE DESIGN JUDGMENT (verify against the real worktree code, cite file:line):
- run.py publish_due iterates ONLY PostState.queued (a needs_reconcile post is never re-driven/auto-requeued).
- digest.py has a "Needs reconcile" section surfacing needs_reconcile posts.
- Therefore "2xx-no-id -> needs_reconcile, never failed" and "MCP non-auth -> needs_reconcile, never failed" are SAFE (parked + surfaced + never auto-requeued = the no-double-post invariant). Set state_machine_ok accordingly with evidence.

Return the FRAME_SCHEMA JSON. Do NOT write any code.`, { phase: 'Frame', schema: FRAME_SCHEMA, label: 'frame' })

if (!frame.state_machine_ok) {
  log(`FRAME BLOCKED: state machine not safe — ${frame.state_machine_evidence}`)
  return { status: 'blocked', phase: 'Frame', frame }
}
log(`Frame OK — ${frame.tasks.length} tasks planned; state machine safe (${frame.state_machine_evidence})`)

// ═════════════════════════════════════════════════════════════════════════════
// PHASE 3 — IMPLEMENT (one sequential agent: D1 -> D2 -> D3 -> D4)
// All four touch shared files (blotato_rest.py for D1/D2/D4; blotato_mcp.py for D2/D3) and
// D3 imports D2's helper. Running them in ONE agent guarantees strict ordering and avoids the
// concurrent-shared-file-edit corruption the Phase C deviation note documents. Per-task literal
// RED -> minimal impl -> literal GREEN -> full suite -> commit is still enforced inside.
// ═════════════════════════════════════════════════════════════════════════════
phase('Implement')
const impl = await agent(`You are the IMPLEMENTER for FanOps Phase D. Execute STRICT TDD for D1 -> D2 -> D3 -> D4, IN THAT ORDER, in the worktree.
${ENV_RULES}
${PRIME_DIRECTIVE}

Frame plan: ${JSON.stringify(frame.tasks)}

Use \`superpowers:test-driven-development\` discipline. For EACH task, in order:
  (a) Write the failing test(s) per the plan (mirror the existing test file's setup/imports; APPEND).
      For D2 and D3, also REWRITE the existing tests that encode the now-obsolete \`failed\` behaviour
      (test_2xx_without_submission_id_marks_failed -> assert needs_reconcile + token preserved;
       test_mcp_no_submission_id_marks_failed -> assert needs_reconcile). These are the "rewrite the
       test that encoded the old behaviour" move — REQUIRED, not optional.
  (b) Run ONLY the new/changed test(s): \`${VENV} && python -m pytest <paths> -v\`. Capture the LITERAL
      RED output (it MUST fail for the right reason before you write impl). If it does not fail, your
      test is wrong — fix the test, do not proceed.
  (c) Write the minimal impl against the REAL body.
  (d) Run the same test(s) -> LITERAL GREEN.
  (e) Run the FULL suite: \`${VENV} && python -m pytest -q 2>&1 | tail -3\`. Must be green; record the count.
  (f) Commit ONLY that task's files with the plan's message + the co-author trailer:
      \`cd "${WT}" && git add <files> && git commit -m "<msg>\\n\\n${COAUTHOR}"\` then \`git rev-parse HEAD\`.

Exact impl notes (concrete against the real bodies):
- D1: in crosspost_clips, add submission_id=f"fanops_{_hash('idemp', pid)}" to the Post(...) (import _hash from fanops.ids — it is NOT currently imported in crosspost.py; crosspost imports child_id, surface_key from fanops.ids, so add _hash there). pid is the post id computed just above. In blotato_rest._reconcile, change the guard so the body postSubmissionId is captured even when post.submission_id is already set (the token): drop the \`and not post.submission_id\` condition so a real body id OVERWRITES the token. Commit msg: "fix (audit H1): stamp a client idempotency token as submission_id at crosspost".
- D2: add module-level _extract_submission_id(body) to blotato_rest.py (accept postSubmissionId/submissionId/id + recurse into nested dict under "data"; ignore non-str/empty). Replace the 2xx block: sid=_extract_submission_id(resp.json()) inside try; if not sid -> post.state=needs_reconcile, error_reason="2xx but no recognizable submission id: <text[:200]>", preserve the client token (do NOT clear submission_id), return led; else submitted + submission_id=sid. Commit msg: "fix (audit B2): a 2xx with no recognizable submission id is needs_reconcile, never failed".
- D3: in blotato_mcp.py import _extract_submission_id from fanops.post.blotato_rest and BlotatoAuthError from fanops.errors. Wrap self._call in try/except: on except, lowercased message contains any of (401,403,unauthorized,forbidden,invalid token,api key) -> raise BlotatoAuthError(...); else post.state=needs_reconcile (NOT failed), error_reason="MCP publish error (may be live): <...>", return led. After a successful call: sid=_extract_submission_id(result); if not sid -> needs_reconcile (NOT failed); else submitted. Commit msg: "fix (audit B3): MCP poster maps auth failures to BlotatoAuthError, others to a per-post park".
- D4: add \`import random\` at top of blotato_rest.py; 429 branch -> time.sleep(delay + random.uniform(0, delay)); delay *= 2; continue. The plan's D4 test patches fanops.post.blotato_rest.random.uniform and time.sleep and asserts all sleeps>0 and sleeps[0]!=1.0. Commit msg: "fix: jitter the 429 backoff (avoid thundering-herd across surfaces)".

Sanity for D4's test: with the existing test_429_retries_then_succeeds (429 then 200) and test_retry_exhaustion_marks_failed (4x429) — adding jitter must NOT break them (they patch time.sleep, and now random.uniform too if needed — if an existing 429 test does NOT patch random.uniform it will call the real one, which is fine: a real uniform(0,delay) is deterministic-enough not to assert against; only the NEW test pins jitter). Verify all blotato_rest tests stay green.

Return the IMPL_SCHEMA JSON with literal RED/GREEN snippets + commit shas per task.`, { phase: 'Implement', schema: IMPL_SCHEMA, label: 'implement(D1-D4)' })

if (!impl.all_green) {
  log(`IMPLEMENT FAILED — not all tasks green. Final: ${impl.final_suite_count}`)
  return { status: 'blocked', phase: 'Implement', impl }
}
log(`Implement OK — ${impl.tasks.length} tasks committed; final suite ${impl.final_suite_count}`)

// ═════════════════════════════════════════════════════════════════════════════
// PHASE 4 — VERIFY (a DIFFERENT agent than the implementer; read-only re-run)
// ═════════════════════════════════════════════════════════════════════════════
phase('Verify')
const verify = await agent(`You are the INDEPENDENT VERIFIER for FanOps Phase D. You did NOT write this code. Re-run everything from scratch and check it honestly. READ-ONLY: do not edit code or commit.
${ENV_RULES}

The implementer reported these tasks/commits: ${JSON.stringify(impl.tasks.map(t => ({ id: t.task_id, sha: t.commit_sha, files: t.files })))}.

Do:
1. \`cd "${WT}" && git log --oneline -6\` — confirm the 4 task commits exist with the co-author trailer (\`git log -1 --format=%b <sha>\` shows it).
2. Re-run EACH task's tests independently and QUOTE the literal counts:
   - D1: \`${VENV} && python -m pytest tests/test_crosspost.py tests/test_reconcile.py -v 2>&1 | tail -15\`
   - D2+D4: \`${VENV} && python -m pytest tests/test_blotato_rest.py -v 2>&1 | tail -20\`
   - D3: \`${VENV} && python -m pytest tests/test_blotato_mcp.py -v 2>&1 | tail -15\`
3. Re-run the FULL suite: \`${VENV} && python -m pytest -q 2>&1 | tail -3\`. Quote it.
4. Confirm tests assert REAL behaviour (not tautologies): open the new tests and check each makes a substantive assertion about state/submission_id/error_reason, not e.g. assert True.
5. SPECIAL CHECK (shared helper): \`cd "${WT}" && grep -n "_extract_submission_id" src/fanops/post/blotato_mcp.py src/fanops/post/blotato_rest.py\`. blotato_mcp.py MUST have an IMPORT of _extract_submission_id from blotato_rest and NO local \`def _extract_submission_id\`. blotato_rest.py MUST have exactly ONE \`def _extract_submission_id\`. Set shared_helper_ok + paste the evidence.

Return VERIFY_SCHEMA JSON.`, { phase: 'Verify', schema: VERIFY_SCHEMA, label: 'verify' })

if (!verify.overall_ok || !verify.shared_helper_ok) {
  log(`VERIFY FAILED — overall_ok=${verify.overall_ok} shared_helper_ok=${verify.shared_helper_ok}: ${verify.shared_helper_evidence}`)
  return { status: 'blocked', phase: 'Verify', verify, impl }
}
log(`Verify OK — full suite ${verify.full_suite_count}; shared helper imported not duplicated`)

// ═════════════════════════════════════════════════════════════════════════════
// PHASE 5 — ADVERSARIAL (per task: 2 independent read-only refuting skeptics [parallel]
// + 1 serialized mutation-proof agent). Mutation proofs are destructive git ops on the shared
// tree, so they are SERIALIZED across tasks (outer sequential loop), each proving then restoring
// clean BEFORE the next task's proof — the Phase C destructive-git-serialization lesson.
// ═════════════════════════════════════════════════════════════════════════════
phase('Adversarial')

const taskAttacks = {
  D1: {
    claim: 'Every crossposted Post has a stable fanops_<hash> submission_id from birth; the real Blotato id overwrites the token in _reconcile; reconcile can poll a token-only post.',
    lenses: [
      'Can a Post be created via ANY path in crosspost_clips with NO submission_id? Is the token stable across processes (SHA1 of pid, not salted hash())? Read crosspost.py end to end.',
      'Does a real body postSubmissionId actually OVERWRITE the client token in blotato_rest._reconcile now (the guard change)? Trace the 5xx-with-id path and the token path.',
    ],
    mutation: 'Remove the submission_id=f"fanops_{_hash(...)}" kwarg from the Post(...) in crosspost_clips (revert D1), run tests/test_crosspost.py::<the D1 token test> — it MUST go RED. Restore with git checkout.',
  },
  D2: {
    claim: 'A 2xx with an unrecognized id shape -> needs_reconcile (NEVER failed); a known id (incl nested data.id) -> submitted; the client token is preserved on the no-id path.',
    lenses: [
      'Construct a 2xx body whose id is under a key NOT in (postSubmissionId,submissionId,id) and NOT nested under data — does _extract_submission_id return None and the post become needs_reconcile (NOT failed)? This is the double-post hazard: prove it parks, never fails.',
      'Does nested {"data":{"id":"x"}} extract correctly? Does {"postSubmissionId":"y"} still -> submitted? Does the no-id path keep the client token (submission_id unchanged), so reconcile can poll it?',
    ],
    mutation: 'Change the no-id branch in blotato_rest.publish from needs_reconcile back to failed (revert D2 behaviour), run the D2 needs_reconcile test — it MUST go RED. Restore.',
  },
  D3: {
    claim: 'MCP auth failure -> BlotatoAuthError; non-auth failure -> per-post needs_reconcile (no raw raise, NOT failed); a no-id MCP 2xx -> needs_reconcile; reuses D2 helper; a real BlotatoAuthError from the caller still propagates.',
    lenses: [
      'A reworded auth error with NO matched substring (e.g. "credentials rejected") — does it WRONGLY become needs_reconcile instead of BlotatoAuthError (re-opening burn-the-queue)? AND: a non-auth 500 -> needs_reconcile (not raw-raise, not failed)? Check the substring set against realistic messages.',
      'If the injected tool_caller itself raises BlotatoAuthError directly, does it still propagate as BlotatoAuthError (run.py halts by type)? Does blotato_mcp IMPORT D2 helper (no copy)?',
    ],
    mutation: 'Comment out the auth-class branch (so all exceptions -> needs_reconcile), run the D3 auth test (test_mcp_auth_failure_raises_blotato_auth_error) — it MUST go RED (no BlotatoAuthError raised). Restore.',
  },
  D4: {
    claim: 'The 429 backoff is jittered (not bare powers of two) AND still bounded (no infinite hang).',
    lenses: [
      'Is the sleep delay + random.uniform(0,delay) (jittered) rather than bare delay? Does the loop stay bounded by _MAX_RETRIES (exhaustion -> failed, not hang)? Read the 429 branch + loop.',
      'Could random.uniform ever make a sleep <= 0 or unbounded? Confirm uniform(0,delay) is in [0,delay] and delay doubles but the loop is capped.',
    ],
    mutation: 'Revert the 429 branch to bare time.sleep(delay) (no jitter), run the D4 jitter test (test_429_backoff_is_jittered) — it MUST go RED. Restore.',
  },
}

const tasks = ['D1', 'D2', 'D3', 'D4']
const adversarial = {}
// SERIAL outer loop so the destructive mutation proofs never overlap on the shared worktree.
for (const tid of tasks) {
  const t = taskAttacks[tid]
  // 2 independent read-only refuters in parallel (no git writes -> safe to parallelize).
  const skeptics = await parallel(t.lenses.map((lens, i) => () =>
    agent(`You are an INDEPENDENT ADVERSARIAL SKEPTIC for FanOps Phase D task ${tid}. Your job is to REFUTE this claim, not confirm it. Default to refuted=true if you find ANY real hole. READ-ONLY — do NOT edit code or run git mutations (another agent owns mutation proofs).
${ENV_RULES}
${PRIME_DIRECTIVE}
${BLOTATO_FACTS}

CLAIM: ${t.claim}
YOUR LENS: ${lens}

Read the relevant worktree code and the tests. Try hard to construct a failing case. Run read-only experiments if useful: \`${VENV} && python -c "..."\` or \`python -m pytest <specific test> -v\`. Report refuted (true=you found a hole), confidence, prime_directive_safe (false if you found ANY path marking a maybe-live post failed), and concrete reasoning.`,
      { phase: 'Adversarial', schema: SKEPTIC_SCHEMA, label: `skeptic:${tid}.${i + 1}` })
  )).then(rs => rs.filter(Boolean))

  // 1 mutation-proof agent (destructive — runs alone, restores clean before the loop continues).
  const mutation = await agent(`You are the MUTATION-PROOF agent for FanOps Phase D task ${tid}. Prove the new test actually CATCHES the bug it targets. This is a DESTRUCTIVE edit-then-restore — you are the only agent touching the tree right now.
${ENV_RULES}

Steps (exactly):
1. \`cd "${WT}" && git status --porcelain\` — confirm clean before you start.
2. Apply the mutation: ${t.mutation}
3. Run the targeted test: \`${VENV} && python -m pytest <the specific test path::name> -v 2>&1 | tail -15\`. Capture the LITERAL output. It MUST FAIL (RED) under the mutation — that proves the test is load-bearing.
4. Restore: \`cd "${WT}" && git checkout -- <the file you mutated>\` (or git stash + drop). Then \`git status --porcelain\` MUST be clean (the throwaway mutation is NEVER committed).
5. Re-run the targeted test to confirm GREEN after restore.

Return MUTATION_SCHEMA JSON. If the test does NOT fail under the mutation, set test_failed_under_mutation=false and explain — that is a SERIOUS finding (the test is not actually testing the fix).`,
    { phase: 'Adversarial', schema: MUTATION_SCHEMA, label: `mutation:${tid}` })

  const refutedCount = skeptics.filter(s => s.refuted).length
  const confirmed = refutedCount < Math.ceil((skeptics.length + 1) / 2) // majority of (skeptics+1 implicit) must NOT refute
  adversarial[tid] = {
    verdicts: skeptics,
    mutation,
    confirmed: confirmed && skeptics.every(s => s.prime_directive_safe !== false),
    mutation_proven: mutation.test_failed_under_mutation === true && mutation.restored_clean === true,
    any_refutation: refutedCount > 0 || skeptics.some(s => s.prime_directive_safe === false),
  }
  log(`Adversarial ${tid}: ${skeptics.length} skeptics (${refutedCount} refuted), mutation_proven=${adversarial[tid].mutation_proven}, confirmed=${adversarial[tid].confirmed}`)
}

const allConfirmed = tasks.every(tid => adversarial[tid].confirmed && adversarial[tid].mutation_proven)
if (!allConfirmed) {
  const failing = tasks.filter(tid => !(adversarial[tid].confirmed && adversarial[tid].mutation_proven))
  log(`ADVERSARIAL BLOCKED — unconfirmed/unproven: ${failing.join(', ')}`)
  return { status: 'blocked', phase: 'Adversarial', adversarial, failing, impl, verify }
}
log(`Adversarial OK — all 4 tasks confirmed + mutation-proven`)

// ═════════════════════════════════════════════════════════════════════════════
// PHASE 6 — INTEGRATE + D5 LIVE-VERIFY (TRUE barrier: all tasks done, one agent)
// ═════════════════════════════════════════════════════════════════════════════
phase('Integrate')
const integrate = await agent(`You are the INTEGRATE + D5 agent for FanOps Phase D. All four tasks are implemented, verified, and adversarially confirmed. Now prove they COMPOSE and run the D5 read-only live-contract verification.
${ENV_RULES}
${PRIME_DIRECTIVE}
${BLOTATO_FACTS}

PART A — INTEGRATE:
1. Full unit suite: \`${VENV} && python -m pytest -q -m "not integration" 2>&1 | tail -3\`. Quote it (baseline was 258 passed / some deselected — expect the new total).
2. Integration marker: \`${VENV} && python -m pytest -q -m integration 2>&1 | tail -3\`. Quote it (the 1 creds-gated Blotato smoke should skip cleanly).
3. COMPOSE CHECK — write a SMALL throwaway python snippet (run via \`${VENV} && python -c "..."\`, do NOT commit it) OR reason from code that:
   (a) D1 token + D2 extraction interlock: a crossposted Post starts with a fanops_ token; a REST 2xx-no-id leaves it needs_reconcile WITH the token (so reconcile can poll); a real id overwrites the token.
   (b) D3 MCP interlock: a non-auth MCP error parks needs_reconcile (not failed); an auth error raises BlotatoAuthError.
   (c) NO path across D1/D2/D3 marks a maybe-live post failed. Cite the code.
   Set compose_ok + compose_evidence.

PART B — D5 LIVE CONTRACT VERIFICATION (READ-ONLY):
The Blotato MCP server is NOT reachable from this workflow run (the orchestrator confirmed via 4 probes: ToolSearch by exact name, by keyword, by server-id, and mcp-registry list_connectors — all empty). So per the plan's D5 fallback rule, verify FanOps's field assumptions against the SCHEMA-DOCUMENTED contract FACTS above, labelled honestly. Do NOT attempt an MCP call and do NOT fabricate one.

Produce d5_report: one row per FanOps reader -> the live reality -> match|MISMATCH. Cover at least:
- reconcile.py:45 reads info.get("publicUrl"). Which endpoint does reconcile hit? It uses BlotatoStatusClient.get_status -> GET /v2/posts/{id} (metrics.py:42-46), i.e. the get_post_status concept. The published URL on get_post_status IS publicUrl. -> Expected MATCH. Confirm by reading reconcile.py + metrics.py.
- track.py:48 / pull_metrics keys rows on row.get("postSubmissionId"); BlotatoMetricsClient.list_posts hits GET /v2/posts (metrics.py:19-27) — the LIST endpoint. Does track read a URL key at all? (It reads postSubmissionId + metrics, NOT a url.) So track does NOT read publicUrl/postUrl — confirm there is no URL-key mismatch in track. Report this explicitly.
- The KEY DIVERGENCE: list_posts returns the published URL under postUrl (NOT publicUrl). Does ANY FanOps reader parse a LIST-style row and read publicUrl from it (which would be a real MISMATCH -> wrong/empty url)? Grep for publicUrl and postUrl across src/fanops. reconcile reads publicUrl but from get_status (single-post), which is correct. metrics.list_posts returns rows but track only reads postSubmissionId+metrics from them. CONCLUSION to verify: is there a real mismatch or not? If FanOps never reads a URL from a list row, there is NO live bug — record that as the finding (the divergence exists in the API but FanOps happens to read the URL only from the endpoint where it's publicUrl). If you find a reader that DOES read publicUrl from a list row, that is a MISMATCH -> write a TDD one-line fix (RED first) and commit it with message "fix (live-verified): <reader> reads postUrl from list rows, not publicUrl" + the co-author trailer.
- postSubmissionId field: FanOps uses it in blotato_rest/blotato_mcp/track/reconcile/metrics. Live schema confirms create_post returns it and get_post_status takes it. -> MATCH.
- status enum: reconcile compares "published"/"failed"; get_status returns in-progress|published|scheduled|failed. -> MATCH.
- account id + per-platform required fields: read src/fanops/post/payload.py (build_blotato_payload / default_target_fields) and compare to the live list_accounts required fields (TikTok privacyLevel, Pinterest boardId, YouTube title+privacyStatus, Facebook pageId). Report match or note any platform whose required field FanOps does not build (a gap to note, not necessarily a Phase-D fix).
- presign keys: media.py:32 requires presignedUrl + publicUrl. Live create_presigned_upload_url returns presignedUrl + publicUrl. -> MATCH.

If you make a TDD fix, re-run the full suite and quote the new count; set d5_fix_applied + d5_fix_commit. If NO mismatch needs a code fix, set d5_fix_applied=false, d5_fix_commit="".

If any task is unconfirmed (it isn't — all 4 confirmed) or the suite regressed below the post-implement count, set blocked=true and STOP with evidence.

Return INTEGRATE_SCHEMA JSON.`, { phase: 'Integrate', schema: INTEGRATE_SCHEMA, label: 'integrate+d5' })

if (integrate.blocked || integrate.regressed || !integrate.compose_ok) {
  log(`INTEGRATE BLOCKED — blocked=${integrate.blocked} regressed=${integrate.regressed} compose_ok=${integrate.compose_ok}`)
  return { status: 'blocked', phase: 'Integrate', integrate, adversarial, impl }
}
log(`Integrate OK — unit ${integrate.full_unit_count}, integration ${integrate.integration_count}; D5 report has ${integrate.d5_report.length} rows, ${integrate.d5_mismatches.length} mismatches, fix=${integrate.d5_fix_applied}`)

// ═════════════════════════════════════════════════════════════════════════════
// PHASE 7 — CLOSE (CLI smoke on a scratch root + doc/deviation DRAFTS).
// The orchestrator (main loop) performs the outward-facing steps itself: sync-docs skill,
// memory write, push/PR, CI watch, handoff. This agent does the scratch-cwd CLI smoke and
// drafts the README/RUNTIME drift + the deviation-memo section for the orchestrator to apply.
// ═════════════════════════════════════════════════════════════════════════════
phase('Close')
const close = await agent(`You are the CLOSE agent for FanOps Phase D. Do the real-CLI smoke and draft the docs/deviation updates. Do NOT push, do NOT open a PR, do NOT invoke skills — the orchestrator does those.
${ENV_RULES}

PART A — REAL-CLI SMOKE on a SCRATCH ROOT (Config resolves from cwd; running anywhere else is a false-green — this bit the project twice). Use a fresh temp dir as the FanOps root and cd INTO it:
  SCRATCH=$(mktemp -d)
  Set up a minimal accounts.json + a captioned clip the way tests/test_crosspost.py does, OR simpler: drive \`fanops advance\` in dryrun and confirm a crossposted Post carries a fanops_ submission_id token. Concretely, from the worktree venv but with cwd=SCRATCH:
    cd "$SCRATCH" && (source "${WT}/.venv/bin/activate"; <set up control dir + accounts + a captioned clip>; fanops advance --base-time 2026-06-02T18:00:00Z; fanops status)
  The cleanest proof: write a tiny python script using the worktree's fanops to build a Config(root=SCRATCH), seed one source/moment/captioned clip + an accounts.json, call crosspost_clips, and assert every Post.submission_id startswith "fanops_". Run it with the worktree venv. Capture the literal output.
  Confirm: (1) every crossposted Post has a fanops_ token; (2) nothing regresses; (3) the repo's own ledger under /Users/molhamhomsi/Moh Flow Fanops and ${WT} is UNTOUCHED (verify-the-verification: the scratch root, not the repo, holds the ledger).
  Set cli_smoke_ok + cli_smoke_evidence + token_present + no_regression.

PART B — DOC DRIFT DRAFTS (do NOT edit the files; just report exact changes for the orchestrator):
1. README: read /Users/molhamhomsi/Moh Flow Fanops/README.md (or the worktree copy) and identify any lines about submission-id handling / 2xx-no-id behaviour / MCP auth that are now stale (e.g. "2xx with no submission id -> failed" must become "-> needs_reconcile"). Quote the exact lines that need changing into readme_drift (or "none").
2. RUNTIME.md: read MohFlow-FanOps/00_control/RUNTIME.md. Identify: (a) the INTEGRATION CHECKPOINT comments / unverified-contract notes that D5 confirmed against the live schemas (these should be marked "verified against live MCP schemas 2026-06-02"); (b) any submission-id / publicUrl-postUrl / MCP-auth / needs_reconcile / client-token prose to add or correct. Quote exact lines into runtime_drift (or "none").

PART C — DEVIATION-MEMO SECTION: write the full markdown text of a "## Phase D — Live Blotato seam" section to append to the deviations memory. It MUST include: every deviation from the plan's literal code (esp. D4-not-parallel-but-serial-for-shared-file-safety, the rewritten failed->needs_reconcile tests, the _reconcile guard change, _hash import in crosspost); the per-task RED/GREEN + commit shas (from ${JSON.stringify(impl.tasks.map(t => ({ id: t.task_id, sha: t.commit_sha })))}); the verify counts (${verify.full_suite_count}, shared-helper OK); each skeptic verdict + mutation result; the Integrate suite counts (unit ${integrate.full_unit_count}, integration ${integrate.integration_count}); and the FULL D5 VERIFICATION REPORT incl the postUrl/publicUrl reconciliation finding and whether a live test post was done or deferred (it is DEFERRED — MCP unreachable + read-only default; record "live post verification: awaiting human-approved test account; contract verified against MCP tool schemas, server not reachable in this run"). Put the whole section text in deviation_note.

PART D: list ALL commit shas + first lines made across the phase (git log) into commits.

Return CLOSE_SCHEMA JSON.`, { phase: 'Close', schema: CLOSE_SCHEMA, label: 'close' })

log(`Close agent done — cli_smoke_ok=${close.cli_smoke_ok}, token_present=${close.token_present}`)

return {
  status: (close.cli_smoke_ok && close.token_present && close.no_regression) ? 'complete' : 'needs_attention',
  worktree: WT,
  branch: 'phase-d-blotato',
  preflight,
  frame: { tasks: frame.tasks.length, state_machine_ok: frame.state_machine_ok, evidence: frame.state_machine_evidence },
  implement: impl,
  verify,
  adversarial,
  integrate,
  close,
}
