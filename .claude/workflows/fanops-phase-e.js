export const meta = {
  name: 'fanops-phase-e',
  description: 'Phase E (learning loop autonomy + dead-man\'s-switch): E1-E4 deterministic TDD with independent verify + adversarial mutation proofs',
  phases: [
    { title: 'Preflight', detail: 'confirm merged A/B/C/D base, 272/1, real bodies, deps' },
    { title: 'Frame', detail: 'per-task structured plans + locked design judgments' },
    { title: 'Implement', detail: 'STRICT TDD in one worktree; E1->E2 sequential, then E3, E4' },
    { title: 'Verify', detail: 'different agent re-runs each task tests + suite + special checks' },
    { title: 'Adversarial', detail: '>=2 independent skeptics per task, mutation proofs; classify impl-bypass vs test-quality' },
    { title: 'Harden', detail: 'strengthen committed tests the skeptics found hollow, mutation-proven (only if flagged)' },
    { title: 'Re-Adversarial', detail: 'confirm the hardened committed tests now catch the bug' },
    { title: 'Integrate', detail: 'true barrier: full suite, compose-check E1 pass + E2 heartbeat' },
    { title: 'Close', detail: 'real-CLI dryrun smoke x2 (heartbeat ts changes), docs, push, CI watch, handoff drafts' },
  ],
}

const ROOT = '/Users/molhamhomsi/Moh Flow Fanops'
const VENV = `cd "${ROOT}" && source .venv/bin/activate`
const COAUTHOR = 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>'

// ───────────────────────── schemas ─────────────────────────
const PREFLIGHT_SCHEMA = {
  type: 'object',
  required: ['base_ok', 'baseline_count', 'deps_present', 'version_absent', 'real_body_notes', 'blockers'],
  properties: {
    base_ok: { type: 'boolean', description: 'HEAD is on merged A/B/C/D main (39420c2) with clean tree' },
    head: { type: 'string' },
    baseline_count: { type: 'string', description: 'e.g. "272 passed, 1 skipped"' },
    deps_present: {
      type: 'object',
      description: 'each named dep -> present boolean',
      additionalProperties: { type: 'boolean' },
    },
    version_absent: { type: 'boolean', description: 'fanops.__version__ is EXPECTED ABSENT before E2 (true = correctly absent)' },
    real_body_notes: {
      type: 'object',
      required: ['run_reconcile_guard', 'advance_summary_shape', 'amplify_signature', 'digest_pending_section', 'reconcile_branches'],
      properties: {
        run_reconcile_guard: { type: 'string', description: 'how run/advance currently guards the reconcile pass (E1 mirrors it)' },
        advance_summary_shape: { type: 'string', description: 'exact keys in advance() summary dict (E2 extends)' },
        amplify_signature: { type: 'string', description: 'current amplify() signature (E1 adds max_amplify_per_source)' },
        digest_pending_section: { type: 'string', description: 'whether write_digest already lists pending gates and how (E3 strengthens)' },
        reconcile_branches: { type: 'string', description: 'the branches in reconcile_posts (E4 logs each)' },
      },
    },
    blockers: { type: 'array', items: { type: 'string' }, description: 'any missing dep the plan tests need (other than __version__); empty if none' },
  },
}

const FRAME_SCHEMA = {
  type: 'object',
  required: ['tasks', 'design_locks', 'responder_backoff_decision'],
  properties: {
    tasks: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'files', 'failing_test', 'impl', 'ordering_deps'],
        properties: {
          id: { type: 'string', enum: ['E1', 'E2', 'E3', 'E4'] },
          files: { type: 'array', items: { type: 'string' } },
          failing_test: { type: 'string', description: 'the literal failing test(s) name + what it asserts' },
          impl: { type: 'string', description: 'concrete implementation plan against the real bodies' },
          ordering_deps: { type: 'string' },
        },
      },
    },
    design_locks: {
      type: 'object',
      required: ['learning_pass_guard', 'published_in_run_semantics', 'heartbeat_changes', 'amplify_cap'],
      properties: {
        learning_pass_guard: { type: 'string' },
        published_in_run_semantics: { type: 'string' },
        heartbeat_changes: { type: 'string' },
        amplify_cap: { type: 'string' },
      },
    },
    responder_backoff_decision: {
      type: 'object',
      required: ['decision', 'reasoning'],
      properties: {
        decision: { type: 'string', enum: ['in-scope-and-TDD', 'deferred-with-reasoning'] },
        reasoning: { type: 'string' },
      },
    },
  },
}

const IMPL_SCHEMA = {
  type: 'object',
  required: ['task_id', 'red', 'green', 'suite_count', 'files', 'commit_sha', 'commit_subject'],
  properties: {
    task_id: { type: 'string' },
    red: { type: 'string', description: 'literal RED: the failing-test output BEFORE impl (quote the failure)' },
    green: { type: 'string', description: 'literal GREEN: the passing-test output AFTER impl (quote the pass)' },
    suite_count: { type: 'string', description: 'full-suite count after this task, e.g. "275 passed, 1 skipped"' },
    files: { type: 'array', items: { type: 'string' } },
    commit_sha: { type: 'string' },
    commit_subject: { type: 'string' },
    notes: { type: 'string' },
  },
}

const VERIFY_SCHEMA = {
  type: 'object',
  required: ['task_id', 'verified', 'count', 'asserts_real_behavior', 'special_checks', 'notes'],
  properties: {
    task_id: { type: 'string' },
    verified: { type: 'boolean' },
    count: { type: 'string', description: 'quoted full-suite count this agent observed' },
    asserts_real_behavior: { type: 'boolean', description: 'the tests assert real behavior, not tautologies' },
    special_checks: {
      type: 'object',
      description: 'task-specific independent checks; key -> {passed, evidence}',
      additionalProperties: {
        type: 'object',
        properties: { passed: { type: 'boolean' }, evidence: { type: 'string' } },
      },
    },
    notes: { type: 'string' },
  },
}

const SKEPTIC_SCHEMA = {
  type: 'object',
  required: ['task_id', 'lens', 'refuted', 'bypass_kind', 'mutation_proven', 'any_bypass', 'evidence'],
  properties: {
    task_id: { type: 'string' },
    lens: { type: 'string' },
    refuted: { type: 'boolean', description: 'true = you found a problem (EITHER an implementation bypass OR a test-quality hole). Use bypass_kind to say which.' },
    bypass_kind: {
      type: 'string',
      enum: ['none', 'implementation', 'test-quality'],
      description: 'none = impl correct AND its committed test binds the guarantee. implementation = the SOURCE has a real bypass (a true BLOCK). test-quality = the SOURCE is correct but the COMMITTED test is hollow (passes with the guard/invariant removed) — fixable by strengthening the test, NOT a source block.',
    },
    mutation_proven: { type: 'boolean', description: 'revert guard/inject mutation -> the relevant test FAILS -> restore; throwaway never committed' },
    any_bypass: { type: 'string', description: 'describe the implementation bypass OR the hollow-test gap, or "none"' },
    evidence: { type: 'string', description: 'quoted mutation before/after states' },
  },
}

// Hardening: strengthen the committed test(s) the skeptics found hollow, mutation-proven load-bearing.
const HARDEN_SCHEMA = {
  type: 'object',
  required: ['task_id', 'tests_strengthened', 'mutation_red', 'mutation_restored_green', 'suite_count', 'commit_sha', 'notes'],
  properties: {
    task_id: { type: 'string' },
    tests_strengthened: { type: 'array', items: { type: 'string' }, description: 'the test name(s) added/strengthened' },
    mutation_red: { type: 'string', description: 'LITERAL: inject the bug the hollow test missed -> the STRENGTHENED test now FAILS (quote it)' },
    mutation_restored_green: { type: 'string', description: 'LITERAL: restore source -> the strengthened test PASSES (quote it); git status clean' },
    suite_count: { type: 'string', description: 'full-suite count after this hardening commit' },
    commit_sha: { type: 'string' },
    notes: { type: 'string' },
  },
}

const INTEGRATE_SCHEMA = {
  type: 'object',
  required: ['suite_count', 'compose_ok', 'dryrun_exit0', 'heartbeat_reflects_pass', 'no_double_print', 'regressed', 'blocked', 'evidence'],
  properties: {
    suite_count: { type: 'string', description: 'final full-suite count incl. integration marker' },
    compose_ok: { type: 'boolean', description: 'E1 pass + E2 heartbeat in the SAME run invocation compose correctly' },
    dryrun_exit0: { type: 'boolean' },
    heartbeat_reflects_pass: { type: 'boolean', description: "heartbeat's published_in_run reflects what the learning pass did" },
    no_double_print: { type: 'boolean' },
    regressed: { type: 'boolean' },
    blocked: { type: 'boolean' },
    evidence: { type: 'string' },
  },
}

const CLOSE_SCHEMA = {
  type: 'object',
  required: ['smoke_run1_heartbeat', 'smoke_run2_heartbeat', 'timestamps_differ', 'learning_pass_skipped_dryrun', 'runlog_has_heartbeats',
             'docs_synced', 'deviations_appended', 'pushed', 'ci_status', 'handoff_written', 'summary'],
  properties: {
    smoke_run1_heartbeat: { type: 'string', description: 'the FULL heartbeat JSON line from the 1st real fanops run' },
    smoke_run2_heartbeat: { type: 'string', description: 'the FULL heartbeat JSON line from the 2nd real fanops run' },
    timestamps_differ: { type: 'boolean', description: 'the two heartbeat timestamps differ (dead-man\'s-switch working for real)' },
    learning_pass_skipped_dryrun: { type: 'boolean' },
    runlog_has_heartbeats: { type: 'boolean' },
    docs_synced: { type: 'array', items: { type: 'string' }, description: 'docs updated (README, RUNTIME.md)' },
    deviations_appended: { type: 'boolean' },
    pushed: { type: 'string', description: 'branch pushed / PR opened (url or branch name)' },
    ci_status: { type: 'string', description: 'GitHub Actions watched result, e.g. "completed success run 12345"' },
    handoff_written: { type: 'boolean' },
    summary: { type: 'string' },
  },
}

// ───────────────────────── helper: real-body excerpts for prompts ─────────────────────────
const BODY_CONTEXT = `
GROUND TRUTH (verified by the orchestrator — do NOT re-derive, but DO read the files to confirm before editing):
- src/fanops/cli.py: \`run\` (cli.py:160-179) loops respond+advance up to range(10), breaks when awaiting.moments==0 AND awaiting.captions==0, then \`print(s); return 0\`. The reconcile-guard PATTERN E1 mirrors is in pipeline.py:103-110: \`reconcilable = (...); if reconcilable and cfg.poster_backend != "dryrun" and cfg.blotato_api_key: try: led = reconcile_posts(led, cfg) except Exception as e: log("reconcile","-","error",...)\`. cli.py already imports: \`from fanops.track import pull_metrics\`, \`from fanops.adjust import classify_outcomes, amplify, retire\`, \`from fanops.ledger import Ledger\`, \`import json\` is NOT yet imported in cli.py (it imports argparse, sys) — E2 needs \`import json\` + \`import fanops\` + datetime.
- src/fanops/pipeline.py: advance() runs the whole pass inside \`with Ledger.transaction(cfg) as led:\` (pipeline.py:38). The summary dict is built at pipeline.py:123-136 INSIDE that with-block; keys: sources, moments, clips, posts, published, failed, needs_reconcile, holds, errors, awaiting{moments,captions}. E2 captures \`before = {p.id for p in led.posts_in_state(PostState.published)}\` right after \`with Ledger.transaction(cfg) as led:\` opens (BEFORE ingest_drops), and computes published_in_run + last_published_age_hours just before/inside the summary dict. PostState is already imported in pipeline.py.
- src/fanops/adjust.py: \`amplify(led, cfg, winner_post_ids)\` (adjust.py:31) walks post->clip->moment->src, writes a moments request, sets src state moments_requested. E1 adds \`*, max_amplify_per_source: int = 3\`, reads \`int(src.meta.get("amplify_count", 0))\`, \`if used >= cap: continue\`, and on a successful amplify sets \`src.meta["amplify_count"] = used + 1\`.
- src/fanops/digest.py: render_digest ALREADY emits an "Awaiting agent (request written, no response yet)" section listing \`- moments: {k}\` / \`- captions: {k}\` per pending key (digest.py:58-62). test_digest.py::test_lists_pending_agent_steps asserts \`"Awaiting agent" in md and "moments: s1" in md\`. So E3 must STRENGTHEN: add an explicitly-titled "Pending agent gates" section listing pending(cfg, kind=...) per kind WITH the kind AND key, and the new test must assert the kind+key in a way the existing "Awaiting agent" line does NOT already trivially satisfy. The plan's literal test asserts \`"pending" in text.lower() and "moments" in text.lower()\` against the WRITTEN file (write_digest -> cfg.digest_path). Read digest.py and decide the cleanest non-duplicative strengthening (e.g. a "Pending agent gates" header containing "pending" + per-kind list "moments: s1") — keep the existing "Awaiting agent" test green.
- src/fanops/reconcile.py: reconcile_posts (reconcile.py:39-71) branches: (a) skip \`if not post.submission_id: continue\` [skipped-no-id]; (b) on poll exception -> set error_reason, continue [left/error]; (c) status=="published" -> published; (d) status=="failed" -> failed; (e) else in-progress/scheduled -> left. E4 adds \`log = get_logger(cfg)\` and one \`log("reconcile", post.id, <status_or_skip_reason>)\` per branch (promoted/failed/left/skipped-no-id). get_logger is \`from fanops.log import get_logger\`.
- src/fanops/log.py: \`get_logger(cfg)\` returns \`log(stage, unit_id, outcome, **fields)\` which appends a TAB-joined line to cfg.log_path AND prints to stderr.
- src/fanops/config.py: cfg.log_path = 07_reports/run.log; cfg.poster_backend = os.getenv("FANOPS_POSTER") or "dryrun"; cfg.blotato_api_key = stripped BLOTATO_API_KEY or None.
- Post.scheduled_time: Optional[str] (ISO, may be None). Post.metrics: dict. Source.meta: dict. Ledger.posts_in_state(PostState.published) -> list[Post]. Ledger.transaction(cfg) yields led, saves once on clean exit.
- TESTS: always run with \`${VENV} && python -m pytest ...\` (bare pytest mis-reports the mocker fixture). test_pipeline.py has a \`_ff(mocker)\` ffmpeg/whisper mock fixture and imports json, Path, Config, Ledger, Source, SourceState. test_adjust.py imports Post, Clip, Moment, Source, PostState, ClipState, MomentState, SourceState, Platform, MomentDecision, MomentPick. test_cli.py imports json + main. test_digest.py imports render_digest (+ write_digest inline). test_reconcile.py uses get_status=lambda injection + a _post helper.
`

// ════════════════════════ PHASE 1: PREFLIGHT ════════════════════════
phase('Preflight')
const preflight = await agent(
  `You are the Preflight gate for FanOps Phase E (a deterministic TDD workflow). Working dir: "${ROOT}".

CONFIRM (read files + run commands; do NOT work from memory):
1. \`cd "${ROOT}" && git rev-parse HEAD\` is 39420c2 (merged A/B/C/D main) and \`git status --porcelain\` is CLEAN.
2. Baseline suite: \`${VENV} && python -m pytest -q 2>&1 | tail -3\` reports 272 passed, 1 skipped (the 1 skip is the creds-gated REST smoke test).
3. Read the REAL bodies: src/fanops/cli.py, src/fanops/pipeline.py, src/fanops/adjust.py, src/fanops/digest.py, src/fanops/reconcile.py, src/fanops/log.py, src/fanops/config.py, src/fanops/__init__.py, and the test files tests/test_cli.py tests/test_pipeline.py tests/test_adjust.py tests/test_digest.py tests/test_reconcile.py.
4. Verify the named deps the plan's literal tests use are PRESENT: adjust.amplify, adjust.classify_outcomes, adjust.retire, track.pull_metrics, Source.meta, Post.metrics, Post.scheduled_time, cfg.log_path, cfg.digest_path, cfg.blotato_api_key, cfg.poster_backend, Ledger.posts_in_state, Ledger.transaction, agentstep.pending, log.get_logger.
5. CONFIRM fanops.__version__ is ABSENT (src/fanops/__init__.py is essentially empty) — this is EXPECTED (E2 adds it). Set version_absent=true if it is correctly absent.

${BODY_CONTEXT}

Emit the structured object. A missing dep the plan's tests need (OTHER than __version__) goes in \`blockers\` — that would STOP the run. real_body_notes must capture: how run/advance currently guards the reconcile pass (E1 mirrors it), the exact advance() summary-dict keys (E2 extends), the current amplify() signature, whether the digest already lists pending gates and how (E3 strengthens), and the reconcile_posts branches (E4 logs each).`,
  { label: 'preflight', schema: PREFLIGHT_SCHEMA, agentType: 'general-purpose' }
)

if (!preflight || !preflight.base_ok || (preflight.blockers && preflight.blockers.length > 0)) {
  log(`PREFLIGHT BLOCKED: base_ok=${preflight?.base_ok} blockers=${JSON.stringify(preflight?.blockers)}`)
  return { blocked: true, phase: 'Preflight', preflight }
}
log(`Preflight OK — HEAD ${preflight.head}, baseline ${preflight.baseline_count}, __version__ absent=${preflight.version_absent}`)

// ════════════════════════ PHASE 2: FRAME ════════════════════════
phase('Frame')
const frame = await agent(
  `You are the Frame gate for FanOps Phase E. Produce a per-task structured plan for E1, E2, E3, E4 and LOCK the design judgments.

Preflight notes (ground truth):
${JSON.stringify(preflight.real_body_notes, null, 2)}

${BODY_CONTEXT}

THE FOUR TASKS (implement EXACTLY per docs/superpowers/plans/2026-06-01-fanops-live-autonomous.md Phase E, lines ~1426-1745; read that section):
- E1 (cli.py + adjust.py; tests test_cli.py + test_adjust.py): close the learning loop in \`run\` (a guarded track->adjust pass) + per-source amplify budget. Commit subject: "feat (audit A2): close the learning loop in run (track->adjust), with per-source amplify budget".
- E2 (__init__.py + pipeline.py + cli.py; tests test_pipeline.py + test_cli.py): __version__="0.3.0"; advance summary carries published_in_run (this-run delta) + last_published_age_hours; run/advance print a CHANGING heartbeat JSON line (heartbeat ISO ts, fanops_version, published_in_run, last_published_age_hours) AND append it to cfg.log_path. Commit subject: "feat (audit B5): dead-man's-switch — heartbeat + this-run deltas in run output".
- E3 (digest.py; test test_digest.py): STRENGTHEN the digest to list pending agent gates by kind/key under a "Pending agent gates" section. Commit subject: "feat (audit H3): surface pending/unanswered agent gates in the digest".
- E4 (reconcile.py; test test_reconcile.py): per-post reconcile logging — log("reconcile", post.id, status_or_skip_reason) at EVERY branch. Commit subject: "feat (audit): per-post reconcile logging".

LOCK THESE DESIGN JUDGMENTS (design_locks):
(a) learning_pass_guard = EXACTLY the reconcile guard (cfg.poster_backend != "dryrun" AND cfg.blotato_api_key), ONCE per run invocation, AFTER the respond+advance loop converges, inside a \`with Ledger.transaction(cfg) as led:\`, wrapped in try/except that LOGS and CONTINUES (a metrics hiccup must NOT crash run). The pass: led = pull_metrics(led, cfg); r = classify_outcomes(led); led = amplify(led, cfg, r["winners"]); led = retire(led, r["losers"]). It must NEVER run in dryrun.
(b) published_in_run_semantics = a THIS-RUN delta (published-post-ids at transaction ENTRY vs EXIT), NOT cumulative. Seed an already-published post -> it must NOT be counted.
(c) heartbeat_changes = the heartbeat line MUST change every run (the ISO timestamp), so a monitor distinguishes alive-idle from cron-dead. A frozen/static ts is the exact silent-death failure B5 targets.
(d) amplify_cap = src.meta["amplify_count"] tracks per-source amplification; skip at >= max_amplify_per_source (default 3); missing meta key defaults to 0; increment only on a successful amplify.

RESPONDER-BACKOFF DECISION (responder_backoff_decision): the recorded "bounded-retry-without-backoff" item — \`fanops run\` retries a persistently-failing gate up to range(10)/invocation with no backoff. E2's heartbeat (published_in_run=0) + E3's pending-gates digest make a persistently-failing responder VISIBLE. DECIDE: is a light backoff (or a "stop retrying a deterministically-rejected gate") IN SCOPE for E's four tasks (then it must be TDD'd as part of E1/E2), OR deferred-with-reasoning? Recommend deferred-with-reasoning UNLESS you find a clean, low-risk, testable addition that fits E1/E2's literal tests — the four tasks are observability/autonomy-closing, and E2/E3 already make the failure visible (which is the operator-actionable win); a retry-policy change is a distinct behavioral change with its own product question (what to do with a persistently-bad gate) and risks scope-creep into the run loop. Justify whichever you pick.

Emit the structured object.`,
  { label: 'frame', schema: FRAME_SCHEMA, agentType: 'general-purpose' }
)

if (!frame || !frame.tasks || frame.tasks.length !== 4) {
  log(`FRAME BLOCKED: got ${frame?.tasks?.length} tasks`)
  return { blocked: true, phase: 'Frame', frame }
}
log(`Frame OK — responder-backoff: ${frame.responder_backoff_decision.decision}`)

// ════════════════════════ PHASE 3: IMPLEMENT ════════════════════════
// One shared worktree off main. E1 -> E2 SEQUENTIAL (shared cli.py/pipeline.py/test_cli.py).
// E3, E4 are DISJOINT and carry no ordering dependency (their "parallel/disjoint" nature) — but
// their git commits are SERIALIZED after E2 because parallel `git commit` into ONE working tree
// corrupts the index. The parallel agent work (independent Verify + Adversarial skeptics) runs
// concurrently in later phases. Serial commits of disjoint files produce the identical tree.
phase('Implement')

const frameById = Object.fromEntries(frame.tasks.map(t => [t.id, t]))
const designLocks = JSON.stringify(frame.design_locks, null, 2)

function implPrompt(taskId, extra) {
  const t = frameById[taskId]
  return `You are the Implement agent for FanOps Phase E task ${taskId}, using STRICT TDD (superpowers:test-driven-development). Work in worktree dir: WORKTREE_DIR (you will be told the path — it is a git worktree off main; run all commands there, with \`source .venv/bin/activate\` for pytest — the .venv is shared from the main checkout via the absolute path "${ROOT}/.venv", so use \`source "${ROOT}/.venv/bin/activate"\`).

TASK ${taskId} PLAN (from Frame):
${JSON.stringify(t, null, 2)}

DESIGN LOCKS (binding):
${designLocks}

${BODY_CONTEXT}

STRICT TDD STEPS (non-negotiable):
1. Write the literal failing test(s) per the plan into the named test file(s). Read the existing test file first and ADD (do not overwrite existing tests).
2. Run ONLY the new test(s) and capture the LITERAL RED output (quote the failure — e.g. the missing kwarg / missing key / AttributeError). If a test passes trivially before impl (e.g. a regression-guard), note that explicitly in \`red\`.
3. Write the minimal concrete implementation against the REAL bodies (per the design locks). Do NOT add unrelated changes.
4. Run the new test(s) again and capture the LITERAL GREEN output.
5. Run the FULL suite (\`source "${ROOT}/.venv/bin/activate" && cd WORKTREE_DIR && python -m pytest -q 2>&1 | tail -3\`) and capture the count. It must be baseline+new, no regressions.
6. Commit ONLY this task's files with the exact commit subject "${t && t.id ? '' : ''}" — use the subject from the plan, a blank line, a 1-3 line body, a blank line, then exactly:
${COAUTHOR}
   Use \`git add <only this task's files>\` then \`git commit\`. Report the resulting \`git rev-parse HEAD\` as commit_sha.

${extra || ''}

If ANY bug surfaces, root-cause it (superpowers:systematic-debugging) — no symptom patch. Emit the structured object with the LITERAL red/green outputs quoted.`
}

// Create the shared worktree once (off main) and run E1 in it.
const e1 = await agent(
  implPrompt('E1',
    `SETUP (you create the worktree): from "${ROOT}", run:
  git worktree add -b phase-e "${ROOT}/../fanops-phase-e" main
Then WORKTREE_DIR = "${ROOT}/../fanops-phase-e". cd there for all work. (If the worktree/branch already exists from a prior resume, reuse it: \`cd "${ROOT}/../fanops-phase-e"\` and continue — do NOT delete committed work.)

E1 SPECIFICS:
- tests/test_adjust.py: ADD test_amplify_respects_per_source_budget (per plan lines 1458-1463): seed a winner whose Source has meta={"amplify_count": 3}, call amplify(led, cfg, ["p1"], max_amplify_per_source=3), assert the source state is STILL SourceState.moments_decided (NOT re-requested at the cap). Use the plan's _winner helper.
- tests/test_cli.py: ADD test_run_learning_pass_is_guarded_to_live_backends (per plan lines 1468-1477): in dryrun (default), \`main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0\` — the learning pass is skipped (no metrics source), run still exits 0. (This is a regression guard; it may pass once the guard is correct.)
- adjust.py: add \`*, max_amplify_per_source: int = 3\` to amplify; read \`used = int(src.meta.get("amplify_count", 0))\`; \`if used >= max_amplify_per_source: continue\`; on successful amplify set \`src.meta["amplify_count"] = used + 1\`.
- cli.py: in \`run\`, AFTER the respond+advance loop converges (after the for-loop, before \`print(s); return 0\`), add the guarded learning pass per design lock (a): only if cfg.poster_backend != "dryrun" and cfg.blotato_api_key, inside \`with Ledger.transaction(cfg) as led:\`, wrapped in try/except that logs (use get_logger(cfg)) and continues. Keep it OUT of the inner loop (once per invocation).
- IMPORTANT for E2 ordering: do NOT add the heartbeat here — that is E2. Leave \`print(s); return 0\` as-is for now.`),
  { label: 'impl:E1', phase: 'Implement', schema: IMPL_SCHEMA, agentType: 'general-purpose' }
)
if (!e1 || !e1.commit_sha) { log(`E1 IMPLEMENT FAILED`); return { blocked: true, phase: 'Implement', task: 'E1', e1 } }
log(`E1 done — ${e1.suite_count}, commit ${e1.commit_sha}`)

const e2 = await agent(
  implPrompt('E2',
    `WORKTREE_DIR = "${ROOT}/../fanops-phase-e" (the worktree E1 created; E1 is already committed there). cd there.

E2 SPECIFICS:
- src/fanops/__init__.py: add \`__version__ = "0.3.0"\` (the file is currently empty).
- tests/test_pipeline.py: ADD test_advance_reports_run_delta_and_last_post_age (per plan lines 1565-1575): monkeypatch.delenv("FANOPS_POSTER"), Config(root=tmp_path), write accounts.json, call advance(cfg, base_time="2026-06-02T18:00:00Z"), assert "published_in_run" in s AND "last_published_age_hours" in s. (Needs mocker? The plan signature includes mocker; advance with no sources/clips won't shell ffmpeg, so mocker may be unused — match the plan's literal signature but only use _ff if the test actually triggers tooling. Read test_pipeline.py for the accounts.json pattern.)
- tests/test_cli.py: ADD test_run_prints_heartbeat_with_version (per plan lines 1579-1590): main(["run", ...]) == 0, capsys out contains fanops.__version__ AND "heartbeat".
- src/fanops/pipeline.py: inside advance(), capture \`before = {p.id for p in led.posts_in_state(PostState.published)}\` immediately after the \`with Ledger.transaction(cfg) as led:\` opens (before ingest_drops). After the pass, compute \`after = led.posts_in_state(PostState.published)\`; \`published_in_run = len([p for p in after if p.id not in before])\`; newest = max parsed scheduled_time among after with a scheduled_time, else None; \`last_published_age_hours = None if newest is None else round((datetime.now(timezone.utc) - newest).total_seconds()/3600, 2)\`. Add a \`_parse\` helper + \`from datetime import datetime, timezone\` at the top. Add both keys to the summary dict (still inside the with-block).
- src/fanops/cli.py: in \`run\` (after the loop, AFTER E1's learning pass) AND in the \`advance\` command path, build \`hb = {"heartbeat": datetime.now(timezone.utc).isoformat(), "fanops_version": fanops.__version__, "published_in_run": s.get("published_in_run", 0), "last_published_age_hours": s.get("last_published_age_hours")}\`, \`print(json.dumps(hb))\`, and append hb to cfg.log_path (run.log) — open(cfg.log_path, "a") and write json.dumps(hb)+"\\n" (ensure cfg.reports dir exists; get_logger or a small inline mkdir). Add \`import json\`, \`import fanops\`, \`from datetime import datetime, timezone\` to cli.py. The heartbeat MUST change run-to-run (the ts). Be careful the \`advance\` command (cmd path in _dispatch) also prints a heartbeat — but advance currently does \`print(advance(cfg, ...)); return 0\`; restructure so s is captured then heartbeat printed. Do NOT double-print in \`run\`.
- COMPOSE CHECK: in \`run\`, the heartbeat's published_in_run must reflect the FULL run (incl. E1's learning pass effect on the final advance summary). Ensure the heartbeat is computed from the final \`s\`.`),
  { label: 'impl:E2', phase: 'Implement', schema: IMPL_SCHEMA, agentType: 'general-purpose' }
)
if (!e2 || !e2.commit_sha) { log(`E2 IMPLEMENT FAILED`); return { blocked: true, phase: 'Implement', task: 'E2', e2 } }
log(`E2 done — ${e2.suite_count}, commit ${e2.commit_sha}`)

const e3 = await agent(
  implPrompt('E3',
    `WORKTREE_DIR = "${ROOT}/../fanops-phase-e" (E1+E2 already committed there). cd there.

E3 SPECIFICS (STRENGTHEN — the digest already has an "Awaiting agent" section):
- tests/test_digest.py: ADD test_digest_surfaces_pending_gates (per plan lines 1668-1676): Config(root=tmp_path), write_request(cfg, kind="moments", key="s1", payload={...full MomentRequest-ish dict...}), write_digest(led, cfg), read cfg.digest_path, assert \`"pending" in text.lower() and "moments" in text.lower()\`. Make the assertion require BOTH the literal word "pending" (the existing "Awaiting agent" section does NOT contain "pending") AND the kind. Do NOT weaken or break the existing test_lists_pending_agent_steps.
- src/fanops/digest.py: ADD a "## Pending agent gates" section to render_digest listing pending(cfg, kind=...) per kind WITH kind+key (e.g. \`- moments: s1\`). It can coexist with the existing "Awaiting agent" section (or you may consolidate — but if you consolidate, you MUST keep test_lists_pending_agent_steps green, which asserts the "Awaiting agent" header + "moments: s1"). SAFEST: add the new "Pending agent gates" section header (containing the word "pending") in addition. Read digest.py and follow its established format.
- This task is DISJOINT from E1/E2 (digest.py only) — but commit it AFTER E2 (serialized in one worktree).`),
  { label: 'impl:E3', phase: 'Implement', schema: IMPL_SCHEMA, agentType: 'general-purpose' }
)
if (!e3 || !e3.commit_sha) { log(`E3 IMPLEMENT FAILED`); return { blocked: true, phase: 'Implement', task: 'E3', e3 } }
log(`E3 done — ${e3.suite_count}, commit ${e3.commit_sha}`)

const e4 = await agent(
  implPrompt('E4',
    `WORKTREE_DIR = "${ROOT}/../fanops-phase-e" (E1+E2+E3 already committed there). cd there.

E4 SPECIFICS:
- tests/test_reconcile.py: ADD test_reconcile_logs_each_post (per plan lines 1713-1719): seed a post in needs_reconcile with submission_id="fanops_t", reconcile_posts(led, cfg, get_status=lambda sid: {"status":"published","publicUrl":"u"}), read cfg.log_path, assert "reconcile" in log and "p1" in log.
- src/fanops/reconcile.py: get \`log = get_logger(cfg)\` (\`from fanops.log import get_logger\`) and emit one \`log("reconcile", post.id, <outcome>)\` at EVERY branch: skipped-no-id (continue branch), poll-error (the except branch — outcome like "poll-error"), published, failed, and the in-progress/scheduled left branch. The skipped-no-id branch (the irreducible human-reconcile residue) MUST be logged so it is visible.
- This task is DISJOINT from E1/E2/E3 (reconcile.py only) — commit it AFTER E3 (serialized in one worktree).`),
  { label: 'impl:E4', phase: 'Implement', schema: IMPL_SCHEMA, agentType: 'general-purpose' }
)
if (!e4 || !e4.commit_sha) { log(`E4 IMPLEMENT FAILED`); return { blocked: true, phase: 'Implement', task: 'E4', e4 } }
log(`E4 done — ${e4.suite_count}, commit ${e4.commit_sha}`)

const implByTask = { E1: e1, E2: e2, E3: e3, E4: e4 }
const WT = `${ROOT}/../fanops-phase-e`

// ════════════════════════ PHASE 4: VERIFY ════════════════════════
// A DIFFERENT agent than each implementer independently re-runs each task's tests + full suite.
// E1/E2 are sequential in code but their VERIFY is independent of each other -> run all 4 in parallel.
phase('Verify')

const VENV_WT = `source "${ROOT}/.venv/bin/activate" && cd "${WT}"`

function verifyPrompt(taskId) {
  const impl = implByTask[taskId]
  const specials = {
    E1: `SPECIAL CHECKS (run them, capture evidence):
- DRYRUN-SKIP: prove the E1 learning pass is REALLY skipped in dryrun. Inspect the cli.py \`run\` code: the pass is gated by \`cfg.poster_backend != "dryrun" and cfg.blotato_api_key\`. Run \`main(["run", ...])\` in a tmp dir with default (dryrun) backend and NO key (the default) and confirm exit 0 AND no metrics were pulled (no crash, no fabricated metrics). You can add a throwaway check: monkeypatch pull_metrics to raise and confirm \`run\` in dryrun still exits 0 (the pass is never reached). Report passed + evidence.
- The per-source cap test asserts the source stays moments_decided at the cap.`,
    E2: `SPECIAL CHECKS (run them, capture evidence):
- HEARTBEAT-CHANGES: run the real CLI TWICE in a scratch dir and confirm the heartbeat timestamps DIFFER (not a static string). Do: \`mkdir -p /tmp/fanops_verify_e2/MohFlow-FanOps/00_control && cd /tmp/fanops_verify_e2 && echo accounts... \` — write a minimal accounts.json with one active account, then run \`source "${ROOT}/.venv/bin/activate" && cd /tmp/fanops_verify_e2 && python -m fanops.cli run --base-time 2026-06-02T18:00:00Z\` twice (Config resolves from cwd, so RUN FROM the scratch dir). Capture both heartbeat lines and confirm the "heartbeat" ts field differs. (If \`python -m fanops.cli\` is not runnable, use the installed \`fanops\` entrypoint from the scratch cwd — but you must run from the scratch dir, NOT the repo, or Config resolves the wrong root.)
- PER-RUN-DELTA: confirm published_in_run is this-run not cumulative. Seed a ledger with an already-published post (scheduled in the past) and confirm a fresh advance reports published_in_run that does NOT count the pre-existing published post. Quote the evidence.`,
    E3: `SPECIAL CHECKS:
- Confirm the digest lists the gate KIND+key (not just a count). Confirm a CLEARED gate (response written matching the request_id) is NOT shown. Quote the digest text.`,
    E4: `SPECIAL CHECKS:
- Confirm a log line per post at EVERY branch (promoted/failed/left/skipped-no-id). Especially confirm the skipped-no-id branch (post with no submission_id) IS logged. Drive reconcile_posts with injected get_status returning each status + a post with no submission_id, and grep run.log.`,
  }
  return `You are the INDEPENDENT Verify agent for FanOps Phase E task ${taskId}. You are NOT the implementer — re-verify from scratch; do not trust the implementer's claims.

The implementer reported: ${JSON.stringify({ red: impl.red?.slice(0, 200), green: impl.green?.slice(0, 200), suite_count: impl.suite_count, files: impl.files, commit_sha: impl.commit_sha })}

Working dir: the git worktree "${WT}" (branch phase-e, all four E-commits present). Use \`${VENV_WT} && python -m pytest ...\`.

DO:
1. Re-run THIS task's specific test(s) by name from the worktree and confirm they PASS. Quote the output.
2. Re-run the FULL suite (\`${VENV_WT} && python -m pytest -q 2>&1 | tail -3\`) and QUOTE the count.
3. Read the task's test(s) and confirm they assert REAL behavior (not tautologies / not asserting the value the code trivially returns). Set asserts_real_behavior.
4. ${specials[taskId]}

${BODY_CONTEXT}

Emit the structured object. special_checks is a map of check-name -> {passed, evidence}. Set verified=true ONLY if the task's tests pass, the full suite is green, the tests assert real behavior, AND every special check passes.`
}

const verifyResults = await parallel(
  ['E1', 'E2', 'E3', 'E4'].map(tid => () =>
    agent(verifyPrompt(tid), { label: `verify:${tid}`, phase: 'Verify', schema: VERIFY_SCHEMA, agentType: 'general-purpose' })
  )
)
const verifyByTask = {}
for (const v of verifyResults.filter(Boolean)) verifyByTask[v.task_id] = v
const unverified = ['E1', 'E2', 'E3', 'E4'].filter(t => !verifyByTask[t]?.verified)
if (unverified.length) {
  log(`VERIFY BLOCKED: unverified tasks ${unverified.join(',')}`)
  return { blocked: true, phase: 'Verify', unverified, verifyResults }
}
log(`Verify OK — all 4 tasks independently verified`)

// ════════════════════════ PHASE 5: ADVERSARIAL ════════════════════════
// >=2 independent skeptics per task as parallel refuters, majority vote, each doing a MUTATION PROOF
// (revert guard / inject mutation in a THROWAWAY copy -> confirm the new test FAILS -> restore;
// never committed). Confirmed only on majority NOT-refuted.
phase('Adversarial')

const adversarialAngles = {
  E1: [
    `Try to make amplify EXCEED the per-source cap by ANY path: off-by-one at the cap (used==cap should skip); a winner whose Source has NO "amplify_count" meta key (defaults to 0 — does it then grow unbounded across repeated runs?); a winner whose source is already at the cap but is re-requested anyway; two winners on the SAME source in one call (does the second see the incremented count?). MUTATION PROOF: in a throwaway copy of adjust.py, RAISE the cap (e.g. change default to 99 or remove the \`>= cap\` guard) and confirm test_amplify_respects_per_source_budget FAILS; restore. Also: does the E1 learning pass EVER run in dryrun (it must not — fabricating/None metrics)? Read cli.py \`run\` and prove the guard. Does a metrics exception crash \`run\` (it must log+continue)? Inject a pull_metrics that raises and confirm \`run\` does not crash.`,
    `Independently: is the learning pass inside a Ledger.transaction (lock-safe vs the next advance)? Could the pass run BEFORE the loop converges or MORE than once per invocation? MUTATION PROOF on the cap as above (independent throwaway). Confirm the guard is EXACTLY the reconcile guard (live backend + key), not a weaker check.`,
  ],
  E2: [
    `Is published_in_run actually THIS-RUN (not cumulative)? MUTATION PROOF: seed a ledger with an already-published post; confirm published_in_run does NOT count it. Then in a throwaway copy of pipeline.py, change the delta to cumulative (e.g. \`published_in_run = len(after)\`) and confirm the per-run-delta test FAILS; restore. Is last_published_age_hours None when never-published and a correct float otherwise?`,
    `Does the heartbeat REALLY change run-to-run? This is the EXACT B5 failure (a frozen ts makes a dead cron look alive). MUTATION PROOF: in a throwaway copy of cli.py, FREEZE the heartbeat ts to a constant string and confirm the heartbeat-changes evidence/test would FAIL (run twice -> identical ts); restore. Is the heartbeat APPENDED to run.log (open the file after a run)? Is it printed exactly ONCE in \`run\` (no double-print)?`,
  ],
  E3: [
    `Does the digest list the gate KIND+key (not just a count that hides WHICH gate is stuck)? MUTATION PROOF: in a throwaway copy of digest.py, weaken the section to print only a count (drop the key) and confirm test_digest_surfaces_pending_gates FAILS; restore. Is a CLEARED gate (a response written matching the latest request_id) NOT shown?`,
    `Independently re-verify the existing test_lists_pending_agent_steps STILL passes (E3 must not break it). MUTATION PROOF on the new section as above (independent throwaway). Confirm the new section genuinely adds the word "pending" + the kind, distinct from the pre-existing "Awaiting agent" section.`,
  ],
  E4: [
    `Is there a log line per post at EVERY branch (promoted/failed/left/skipped-no-id) — not just the happy path? MUTATION PROOF: in a throwaway copy of reconcile.py, REMOVE the log call from the skipped-no-id branch and confirm a test asserting that branch's log FAILS; restore. Drive reconcile with: a published status, a failed status, an in-progress status, and a post with NO submission_id — confirm run.log has a line for each.`,
    `Independently: is the skipped-no-id branch (the irreducible human-reconcile residue) logged so it is visible? MUTATION PROOF as above (independent throwaway). Confirm the poll-error branch (except) also logs.`,
  ],
}

// Each skeptic works on a per-task throwaway COPY so parallel mutations don't collide.
// They mutate files in their OWN scratch (cp the file, or git stash-free: edit then `git checkout --`),
// never committing. To be safe under parallelism, each skeptic copies the target file to /tmp, mutates
// the COPY's logic conceptually, and uses `git checkout -- <file>` to restore after an in-place mutate.
function skepticPrompt(taskId, angle, idx) {
  return `You are INDEPENDENT adversarial skeptic #${idx + 1} for FanOps Phase E task ${taskId}. Your job is to REFUTE the implementation — find a bypass or prove a guarantee is hollow. Default to refuted=true if you find ANY hole.

Working dir: the git worktree "${WT}" (branch phase-e). Use \`${VENV_WT} && python -m pytest ...\`.

YOUR ANGLE:
${angle}

MUTATION PROOF PROTOCOL (mandatory — this is the TDD "watch it fail" for adversarial verification):
- To mutate-prove a test, edit the SOURCE file in the worktree to inject the bug (revert the guard / freeze the ts / weaken the section / remove the log), run the specific test, CAPTURE that it FAILS (quote it), then IMMEDIATELY restore with \`git checkout -- <file>\` (the throwaway mutation is NEVER committed). Confirm \`git status --porcelain\` is clean after restore.
- CRITICAL: because other skeptics may run concurrently on the same worktree, mutate ONE file at a time, restore it IMMEDIATELY before touching another, and verify clean status. If you find the tree dirty from another agent, do NOT commit or stash — just run your read-only checks and note it.
- Prefer NON-mutating verification where possible (drive the function with crafted inputs and assert behavior) — only do an in-place source mutation for the specific mutation-proof, and restore at once.

${BODY_CONTEXT}

CLASSIFY your finding precisely with bypass_kind:
- "implementation" = you found a REAL SOURCE bypass (the guard/invariant is actually wrong/defeatable in the code) — this is a true BLOCK.
- "test-quality" = the SOURCE is correct (you confirmed it with crafted inputs), but the COMMITTED test is HOLLOW (it passes even with the guard/invariant removed). This is fixable by strengthening the test; it is NOT a source block.
- "none" = the source is correct AND its committed test genuinely binds the guarantee (the committed test FAILS when you inject the bug).
Set refuted=true if bypass_kind is "implementation" OR "test-quality" (you found something worth acting on). Set refuted=false only when bypass_kind="none".

Emit the structured object: refuted, bypass_kind, mutation_proven (true if you confirmed the relevant test FAILS under the injected bug then restored — for a test-quality finding, mutation-prove BOTH that your stronger probe catches the bug AND that the committed test does NOT), any_bypass (describe the impl bypass OR the hollow-test gap, or "none"), evidence (quote the mutation before/after, incl. the committed-test-still-green result for a test-quality finding).`
}

const adversarialResults = await parallel(
  ['E1', 'E2', 'E3', 'E4'].flatMap(tid =>
    adversarialAngles[tid].map((angle, i) => () =>
      agent(skepticPrompt(tid, angle, i), { label: `skeptic:${tid}#${i + 1}`, phase: 'Adversarial', schema: SKEPTIC_SCHEMA, agentType: 'general-purpose' })
    )
  )
)
// Classify per task: an "implementation" bypass is a TRUE BLOCK (source is wrong). A "test-quality"
// hole means the source is CORRECT but its committed test is hollow -> route to the Harden phase
// (strengthen the test, mutation-proven). The implementation is confirmed iff NO skeptic found an
// implementation-level bypass AND at least one mutation-proved the source.
const adversarialByTask = {}
for (const r of adversarialResults.filter(Boolean)) {
  (adversarialByTask[r.task_id] ||= []).push(r)
}
const adversarialSummary = {}
const implBlocks = []        // tasks with a real source bypass -> hard block
const needsHardening = []    // tasks whose committed test is hollow -> Harden phase
for (const tid of ['E1', 'E2', 'E3', 'E4']) {
  const skeptics = adversarialByTask[tid] || []
  const implBypass = skeptics.filter(s => s.bypass_kind === 'implementation')
  const testQuality = skeptics.filter(s => s.bypass_kind === 'test-quality')
  const mutationProven = skeptics.some(s => s.mutation_proven)
  // impl confirmed = nobody found a source bypass AND the source was mutation-proven correct
  const implConfirmed = skeptics.length > 0 && implBypass.length === 0 && mutationProven
  adversarialSummary[tid] = {
    implConfirmed, mutationProven, total: skeptics.length,
    implBypassCount: implBypass.length, testQualityCount: testQuality.length,
    testQualityFindings: testQuality.map(s => s.any_bypass),
    skeptics,
  }
  if (!implConfirmed) implBlocks.push(tid)
  if (testQuality.length > 0) needsHardening.push(tid)
}
if (implBlocks.length) {
  log(`ADVERSARIAL BLOCKED (real implementation bypass): ${implBlocks.join(',')}`)
  return { blocked: true, phase: 'Adversarial', reason: 'implementation-bypass', implBlocks, adversarialSummary }
}
log(`Adversarial: implementations confirmed for all 4 tasks (no source bypass, mutation-proven). Test-quality holes to harden: ${needsHardening.join(',') || 'none'}`)

// ════════════════════════ PHASE 5.5: HARDEN ════════════════════════
// The skeptics found the SOURCE correct but some COMMITTED tests hollow (they pass with the
// guard/invariant removed — the exact "mutation-prove every new test" standard the project holds).
// Strengthen those committed tests so each is mutation-proven load-bearing, then commit. Tasks are
// disjoint by test file (E1+E2 share test_cli.py -> sequential; E3/E4 disjoint) -> serialize commits
// in the one worktree, same as Implement.
phase('Harden')

const hardenSpecs = {
  E1: `Strengthen the E1 guard test so it BINDS the "learning pass never runs in dryrun" guarantee (today tests/test_cli.py::test_run_learning_pass_is_guarded_to_live_backends only asserts exit==0, which passes even with the guard removed because the dryrun RuntimeError is swallowed by the try/except). ADD a test (e.g. test_run_learning_pass_not_entered_in_dryrun) that monkeypatches fanops.cli.pull_metrics (or the symbol the run loop calls) to a SPY recording calls, runs \`main(["run", ...])\` in dryrun (default, no key), and asserts the spy was NEVER called (the pass is not entered) AND exit==0. OPTIONALLY also assert that WITH a live backend+key (monkeypatch cfg.poster_backend via FANOPS_POSTER=rest + BLOTATO_API_KEY set, and monkeypatch pull_metrics/classify/amplify/retire to harmless spies so no network) the spy IS called once — proving the guard's positive branch. MUTATION PROOF: inject \`if True:\` at the guard (cli.py ~line 204), confirm the NEW spy test FAILS (spy called in dryrun), restore via git checkout. The pre-existing exit==0 test stays green. (Note: the cap test test_amplify_respects_per_source_budget is ALREADY load-bearing — it asserts amplify_count==3 + state unchanged; leave it.)`,
  E2: `Strengthen the E2 tests so they BIND (today test_pipeline.py::test_advance_reports_run_delta_and_last_post_age only asserts KEY PRESENCE — it passes even if published_in_run becomes cumulative; and no test asserts the heartbeat ts changes run-to-run). (1) STRENGTHEN test_advance_reports_run_delta_and_last_post_age (or add a sibling): seed an already-published post with a scheduled_time in the PAST (add it to the ledger + save BEFORE calling advance, so it is in \`before\` at txn entry), call advance, assert \`s["published_in_run"] == 0\` (this-run delta excludes the pre-existing published post) AND \`s["published"] >= 1\` AND \`isinstance(s["last_published_age_hours"], float)\` and it is > 0 (a real age from the past scheduled_time). MUTATION: change pipeline.py delta to \`len(after)\` -> this test FAILS; restore. (2) ADD test_run_heartbeat_timestamp_changes_between_runs: run \`main(["run", ...])\` TWICE (capsys), parse the two heartbeat JSON lines, assert the "heartbeat" ts fields DIFFER (the dead-man's-switch — a frozen ts is the exact B5 failure). MUTATION: freeze the ts in cli.py _heartbeat -> this test FAILS; restore. Use json.loads on the heartbeat line (find the line containing '"heartbeat"').`,
  E3: `Strengthen the E3 test so the kind+key is pinned SPECIFICALLY in the new "Pending agent gates" section (today test_digest.py::test_digest_surfaces_pending_gates asserts \`"moments: s1" in text\`, but that substring is also satisfied by the pre-existing "Awaiting agent" line, so weakening only the E3 section's list line to a count would still pass). Tighten: SLICE the digest text at the "Pending agent gates" header (e.g. \`section = text.split("Pending agent gates")[1]\`) and assert \`"moments: s1" in section\` (or "moments" + "s1" in that slice). Keep the existing \`"pending" in text.lower()\` assertion (it pins the section's existence). MUTATION: weaken the E3 section's list line from \`- moments: {k}\` to a bare count (drop the key) -> the strengthened test FAILS; restore. test_lists_pending_agent_steps stays green.`,
  E4: `Strengthen the E4 test so EVERY reconcile branch's log line is pinned (today test_reconcile.py::test_reconcile_logs_each_post exercises ONLY the 'published' branch; the skipped-no-id / poll-error / failed / in-progress branches are unpinned — dropping any of their log calls keeps the suite green). ADD test(s) (e.g. test_reconcile_logs_every_branch) that drive reconcile_posts with: (a) a post with NO submission_id (skipped-no-id), (b) a get_status raising RuntimeError (poll-error), (c) status "failed", (d) status "in-progress" (left). After each, read cfg.log_path and assert a "reconcile" line for that post id exists (and ideally the branch keyword: "skipped"/"poll-error"/"failed"/etc). Use separate posts/ledgers or clear the log between drives. MUTATION PROOF: remove the log call from the skipped-no-id branch (reconcile.py ~line 45) -> the new skipped-no-id assertion FAILS; restore. The published-branch test stays green.`,
}

async function hardenTask(taskId) {
  const subject = {
    E1: 'test (E1 harden): pin the dryrun learning-pass guard with a pull_metrics spy (mutation-proven)',
    E2: 'test (E2 harden): pin published_in_run this-run delta + heartbeat ts changes run-to-run (mutation-proven)',
    E3: 'test (E3 harden): pin pending-gate kind+key inside the new digest section (mutation-proven)',
    E4: 'test (E4 harden): pin every reconcile branch log line incl. skipped-no-id (mutation-proven)',
  }[taskId]
  return agent(
    `You are the Test-Hardening agent for FanOps Phase E task ${taskId}. The adversarial skeptics confirmed the SOURCE is CORRECT but found the COMMITTED test HOLLOW (it passes even with the guard/invariant removed). Your job: STRENGTHEN the committed test so it is mutation-proven load-bearing. Do NOT change the source (it is correct) — only the test file(s).

Worktree: "${WT}" (branch phase-e, all four E-commits + any prior harden commits present). Use \`source "${ROOT}/.venv/bin/activate" && cd "${WT}" && python -m pytest ...\`.

Skeptic test-quality findings for ${taskId}:
${JSON.stringify(adversarialSummary[taskId].testQualityFindings, null, 2)}

WHAT TO DO:
${hardenSpecs[taskId]}

PROTOCOL (mutation-prove the strengthened test — the project's non-negotiable standard):
1. Strengthen/add the test(s) in the named test file. Read the file first; ADD or tighten, do not break existing tests.
2. Run the strengthened test -> it must PASS on the (correct, unmodified) source. Quote it.
3. MUTATION PROOF: inject the bug the old hollow test missed (the guard removal / cumulative delta / frozen ts / dropped log / weakened section) into the SOURCE, run the strengthened test, CAPTURE that it now FAILS (mutation_red), then restore the source via \`git checkout -- <source file>\` and confirm the test PASSES again (mutation_restored_green). Confirm \`git status --porcelain\` shows ONLY your test-file change (source clean).
4. Run the FULL suite (\`... && python -m pytest -q 2>&1 | tail -3\`); quote the count (should be prior+new tests, no regressions).
5. Commit ONLY the test file(s) with subject "${subject}", a blank line, a 1-2 line body, a blank line, then exactly:
${COAUTHOR}
   Report \`git rev-parse HEAD\` as commit_sha.

${BODY_CONTEXT}

Emit the structured object with the LITERAL mutation_red (strengthened test FAILS under the bug) and mutation_restored_green (PASSES after restore).`,
    { label: `harden:${taskId}`, phase: 'Harden', schema: HARDEN_SCHEMA, agentType: 'general-purpose' }
  )
}

// Serialize harden commits (one worktree). Only harden the tasks the skeptics flagged test-quality.
const hardenByTask = {}
for (const tid of needsHardening) {
  const h = await hardenTask(tid)
  if (!h || !h.commit_sha) { log(`HARDEN ${tid} FAILED`); return { blocked: true, phase: 'Harden', task: tid, h, adversarialSummary } }
  hardenByTask[tid] = h
  log(`Harden ${tid} — strengthened ${JSON.stringify(h.tests_strengthened)}, ${h.suite_count}, commit ${h.commit_sha}`)
}

// ════════════════════════ PHASE 5.6: RE-ADVERSARIAL (confirm hardened tests bind) ════════════════════════
// One independent skeptic per hardened task re-checks: the strengthened committed test now CATCHES
// the bug (mutation -> committed test FAILS), closing the hollow-test gap. This is the confirm that
// the Harden phase actually fixed what the first Adversarial pass flagged.
phase('Re-Adversarial')

const reAdvResults = needsHardening.length
  ? await parallel(needsHardening.map(tid => () =>
      agent(
        `You are an INDEPENDENT skeptic re-confirming the HARDENED committed test for FanOps Phase E task ${tid}. The first adversarial pass found the committed test hollow; a hardening commit then strengthened it. CONFIRM the strengthened committed test now BINDS the guarantee.

Worktree: "${WT}" (branch phase-e, all E-commits + harden commits present). Use \`source "${ROOT}/.venv/bin/activate" && cd "${WT}" && python -m pytest ...\`.

The hardening agent strengthened: ${JSON.stringify(hardenByTask[tid]?.tests_strengthened)} and claims commit ${hardenByTask[tid]?.commit_sha}.

DO:
1. Identify the strengthened committed test(s) for ${tid} (in ${tid === 'E1' || tid === 'E2' ? 'tests/test_cli.py / tests/test_pipeline.py' : tid === 'E3' ? 'tests/test_digest.py' : 'tests/test_reconcile.py'}). Run them -> PASS on the correct source. Quote.
2. MUTATION PROOF (the confirm): inject the SAME bug the hollow test missed (${tid === 'E1' ? 'remove the dryrun guard `if cfg.poster_backend != "dryrun" and cfg.blotato_api_key:` -> `if True:`' : tid === 'E2' ? 'change published_in_run to `len(after)` (cumulative) AND/OR freeze the heartbeat ts' : tid === 'E3' ? 'weaken the "Pending agent gates" section list line to a bare count (drop the key)' : 'remove the log call from the skipped-no-id branch in reconcile.py'}) into the SOURCE, run the strengthened COMMITTED test, and confirm it now FAILS (this is what was missing before). Restore via \`git checkout -- <source>\`; confirm clean + test green again.
3. Report refuted=false / bypass_kind="none" if the strengthened committed test now CATCHES the bug (the hole is closed). Report refuted=true / bypass_kind="test-quality" if it STILL passes under the mutation (hardening insufficient).

${BODY_CONTEXT}

Emit the structured object (reuse the skeptic shape: refuted, bypass_kind, mutation_proven, any_bypass, evidence).`,
        { label: `re-adv:${tid}`, phase: 'Re-Adversarial', schema: SKEPTIC_SCHEMA, agentType: 'general-purpose' }
      )
    ))
  : []

const reAdvByTask = {}
for (const r of reAdvResults.filter(Boolean)) reAdvByTask[r.task_id] = r
const stillHollow = needsHardening.filter(tid => {
  const r = reAdvByTask[tid]
  return !r || r.bypass_kind === 'test-quality' || !r.mutation_proven
})
if (stillHollow.length) {
  log(`RE-ADVERSARIAL BLOCKED: hardening insufficient for ${stillHollow.join(',')}`)
  return { blocked: true, phase: 'Re-Adversarial', stillHollow, reAdvByTask, adversarialSummary }
}
log(`Re-Adversarial OK — all hardened tests now bind (mutation-proven catching the bug)`)

// ════════════════════════ PHASE 6: INTEGRATE ════════════════════════
// TRUE barrier — all tasks done + confirmed. One agent: full suite (unit + integration marker),
// confirm 272 -> new total; check tasks COMPOSE — esp. E1's learning pass + E2's heartbeat in the
// SAME run invocation.
phase('Integrate')

const integrate = await agent(
  `You are the Integrate gate for FanOps Phase E. All four tasks (E1, E2, E3, E4) are implemented + independently verified + adversarially confirmed, committed on branch phase-e in the worktree "${WT}".

Implementer suite counts: E1=${e1.suite_count}, E2=${e2.suite_count}, E3=${e3.suite_count}, E4=${e4.suite_count}.

Working dir: "${WT}". Use \`${VENV_WT} && python -m pytest ...\`.

DO:
1. Run the FULL suite INCLUDING the integration marker: \`${VENV_WT} && python -m pytest -q 2>&1 | tail -3\` (the default already runs everything; if integration tests are marker-gated, also run \`python -m pytest -q -m integration 2>&1 | tail -5\` and note any skips — the creds-gated Blotato smoke + toolchain E2E may skip locally, that's expected). QUOTE the count. Confirm it is 272 -> the new total (baseline was 272 passed, 1 skipped; +new tests, no regressions).
2. COMPOSE CHECK — the critical one: confirm E1's learning pass + E2's heartbeat work in the SAME \`fanops run\` invocation:
   - Read cli.py \`run\`: the learning pass (E1) runs after convergence, the heartbeat (E2) is printed after, and the heartbeat's published_in_run reflects the FINAL advance summary \`s\` (so it reflects what the pass did). Confirm no double-print of the heartbeat. Confirm the learning pass is inside/around the transaction correctly and run still exits 0 in dryrun.
   - Run the real CLI in a scratch dir (dryrun): \`mkdir -p /tmp/fanops_integ/MohFlow-FanOps/00_control\`, write a minimal accounts.json (one active account), then from /tmp/fanops_integ run \`source "${ROOT}/.venv/bin/activate" && cd /tmp/fanops_integ && python -m fanops.cli run --base-time 2026-06-02T18:00:00Z\`. Confirm exit 0 AND a heartbeat JSON line is printed AND (dryrun) the learning pass was skipped (no metrics pull, no crash). Set dryrun_exit0, heartbeat_reflects_pass, no_double_print.
3. If any task is unconfirmed or the suite regresses -> set blocked=true with evidence.

${BODY_CONTEXT}

Emit the structured object.`,
  { label: 'integrate', schema: INTEGRATE_SCHEMA, agentType: 'general-purpose' }
)
if (!integrate || integrate.blocked || integrate.regressed || !integrate.compose_ok || !integrate.dryrun_exit0) {
  log(`INTEGRATE BLOCKED: ${JSON.stringify({ blocked: integrate?.blocked, regressed: integrate?.regressed, compose_ok: integrate?.compose_ok, dryrun_exit0: integrate?.dryrun_exit0 })}`)
  return { blocked: true, phase: 'Integrate', integrate }
}
log(`Integrate OK — ${integrate.suite_count}, compose verified`)

// ════════════════════════ PHASE 7: CLOSE ════════════════════════
phase('Close')

const close = await agent(
  `You are the Close agent for FanOps Phase E (superpowers:finishing-a-development-branch posture). All four tasks are implemented, verified, adversarially confirmed, and integrated on branch phase-e in worktree "${WT}". Suite: ${integrate.suite_count}.

Frame's responder-backoff decision: ${JSON.stringify(frame.responder_backoff_decision)}.
Adversarial summary (implementations confirmed — no source bypass, mutation-proven): ${JSON.stringify(Object.fromEntries(Object.entries(adversarialSummary).map(([k, v]) => [k, { implConfirmed: v.implConfirmed, mutationProven: v.mutationProven, testQualityFindings: v.testQualityFindings }])))}.
Harden phase (strengthened the committed tests the skeptics found hollow — the source was always correct; these tests now mutation-prove the guarantees): ${JSON.stringify(Object.fromEntries(Object.entries(hardenByTask).map(([k, v]) => [k, { tests: v.tests_strengthened, commit: v.commit_sha, suite: v.suite_count }])))}.
Re-Adversarial confirm (the hardened committed tests now CATCH the bug): ${JSON.stringify(Object.fromEntries(Object.entries(reAdvByTask).map(([k, v]) => [k, { bypass_kind: v.bypass_kind, mutation_proven: v.mutation_proven }])))}.

DO (in order):

A) REAL-CLI DRYRUN SMOKE on a SCRATCH ROOT (the dead-man's-switch working for real). Config() resolves from cwd (no FANOPS_ROOT), so RUN FROM the scratch cwd — running from the repo = false-green.
   - \`rm -rf /tmp/fanops_close && mkdir -p /tmp/fanops_close/MohFlow-FanOps/00_control\`
   - Write /tmp/fanops_close/MohFlow-FanOps/00_control/accounts.json = \`{"accounts":[{"handle":"@a","account_id":"1","platforms":["instagram"],"status":"active"}]}\`
   - Run TWICE: \`source "${ROOT}/.venv/bin/activate" && cd /tmp/fanops_close && python -m fanops.cli run --base-time 2026-06-02T18:00:00Z\` (run it, capture the FULL heartbeat line; then run it AGAIN, capture the second heartbeat line). (If \`python -m fanops.cli\` fails, install the worktree: \`source "${ROOT}/.venv/bin/activate" && cd "${WT}" && pip install -e . -q\` then use \`fanops run ...\` from /tmp/fanops_close.)
   - CONFIRM: (a) exit 0 BOTH times; (b) a heartbeat line printed BOTH times with a DIFFERENT "heartbeat" timestamp (the dead-man's-switch working — set timestamps_differ); (c) the learning pass skipped in dryrun (set learning_pass_skipped_dryrun); (d) /tmp/fanops_close/MohFlow-FanOps/07_reports/run.log contains the heartbeats (set runlog_has_heartbeats). Put the two FULL heartbeat lines in smoke_run1_heartbeat / smoke_run2_heartbeat.

B) SYNC DOCS (mechanical drift only; surface conceptual drift, don't invent). Edit in the worktree "${WT}":
   - README.md and MohFlow-FanOps/00_control/RUNTIME.md: document (1) the learning loop now CLOSES in \`run\` (guarded to live backends + key; skipped in dryrun); (2) the per-source amplify budget (max_amplify_per_source, src.meta["amplify_count"]); (3) the heartbeat / dead-man's-switch fields (heartbeat ISO ts, fanops_version, published_in_run, last_published_age_hours) + how an external monitor (cron+mail / PagerDuty) alerts on them ("0 published in N runs" / "last post age > threshold" / "cron itself dead" via the changing ts); (4) the pending-gates digest section; (5) THE NOTE connecting the heartbeat to catching a silently-unauthed \`--bare\`/ANTHROPIC_API_KEY responder (the dead-man's-switch — published_in_run=0 forever + the pending-gates digest — is how you'd catch a responder that's running but unauthed because --bare ignores OAuth and ANTHROPIC_API_KEY wasn't exported). Use the superpowers sync-docs discipline.
   - Commit the doc changes: \`git add README.md MohFlow-FanOps/00_control/RUNTIME.md\` (+ any other doc touched) and \`git commit -m "docs (Phase E): learning loop closes in run, amplify budget, dead-man's-switch heartbeat + monitor alerts, pending-gates digest, --bare/ANTHROPIC_API_KEY connection\\n\\n${COAUTHOR}"\`.

C) APPEND a "## Phase E" section to the auto-memory file /Users/molhamhomsi/.claude/projects/-Users-molhamhomsi-Moh-Flow-Fanops/memory/fanops-build-deviations.md documenting: every deviation from the plan's illustrative code; the skeptic findings + disposition (per task: implementation confirmed correct + mutation-proven, and the IMPORTANT episode that the FIRST adversarial pass found the SOURCE correct but FOUR committed tests HOLLOW — E1 guard test only asserted exit==0 [the swallow-net hid guard removal], E2 delta test only asserted key-presence [not this-run-vs-cumulative], E3 kind+key not pinned inside the new section, E4 only the published branch pinned — which BLOCKED the workflow [correctly, not green-washed] and triggered a Harden phase that strengthened each committed test to be mutation-proven load-bearing, then a Re-Adversarial pass confirmed the strengthened tests now CATCH the bug); the mutation proofs (E2 heartbeat-changes-run-to-run, E1 per-source cap, the hollow-test→hardened-test before/after); and the responder-backoff decision (done-and-TDD'd OR deferred-with-reasoning, with the reasoning). Note the lesson: "TDD green ≠ mutation-proven; an adversarial pass that mutation-tests the COMMITTED tests (not just the source) catches hollow tests that ship green." This is a memory file edit (Read it first, then Edit/append).

D) PUSH + CI. From the worktree "${WT}":
   - \`git push -u origin phase-e\` (private origin Fleezyflo/fanops). Then EITHER open a PR (\`gh pr create --repo Fleezyflo/fanops --base main --head phase-e --title "Phase E: learning loop autonomy + dead-man's-switch (E1-E4)" --body "..."\`) OR report the pushed branch for the human to merge (the HUMAN owns the merge — do NOT merge). Put the PR url or branch in \`pushed\`.
   - WATCH CI to completion: \`gh run list --repo Fleezyflo/fanops --limit 3\` to find the run for this push, then \`gh run watch <id> --repo Fleezyflo/fanops --exit-status\`. If the watch DROPS on a network blip, RE-QUERY \`gh run list\` — do NOT conclude failure from a dropped watch. Report the final status in \`ci_status\` (e.g. "completed success run <id>"). If CI is RED, capture the failing job log and set summary accordingly (do NOT green-wash).

E) HANDOFF WRITE. Invoke the handoff discipline (the \`handoff\` skill contract) to rewrite the project handoff doc's §State + §Now:
   - §State: new HEAD (the phase-e branch tip after ALL commits — the 4 impl commits + the harden commits + the doc commit; run \`git rev-parse HEAD\` in the worktree), suite count (${integrate.suite_count}), CI status. Note the commit shape: E1-E4 impl commits, then per-task test-harden commits, then the docs commit.
   - §Now: "Phase E done (learning loop autonomous + dead-man's-switch) — the last two block-live findings (A2, B5) closed → next Phase F (operator-recovery verbs), the LAST credential-free phase."
   - Carry forward: the \`--bare\`/ANTHROPIC_API_KEY deploy caveat (the responder needs ANTHROPIC_API_KEY exported; --bare ignores OAuth) + the Blotato live-post-needs-human-approval note from Phase D.
   Set handoff_written=true.

${BODY_CONTEXT}

Emit the structured object with the two FULL heartbeat lines and all the booleans. If A) fails (e.g. timestamps do NOT differ, or the learning pass runs in dryrun), set summary to BLOCKED with evidence and do NOT push.`,
  { label: 'close', schema: CLOSE_SCHEMA, agentType: 'general-purpose' }
)

log(`Close: pushed=${close?.pushed} ci=${close?.ci_status} ts_differ=${close?.timestamps_differ}`)

return {
  status: close && close.timestamps_differ && !/BLOCKED/i.test(close.summary || '') ? 'DONE' : 'NEEDS_REVIEW',
  worktree: WT,
  branch: 'phase-e',
  preflight: { head: preflight.head, baseline: preflight.baseline_count },
  frame: { responder_backoff: frame.responder_backoff_decision },
  implement: Object.fromEntries(Object.entries(implByTask).map(([k, v]) => [k, { red: v.red, green: v.green, suite_count: v.suite_count, commit_sha: v.commit_sha, commit_subject: v.commit_subject, files: v.files }])),
  verify: Object.fromEntries(Object.entries(verifyByTask).map(([k, v]) => [k, { verified: v.verified, count: v.count, special_checks: v.special_checks }])),
  adversarial: Object.fromEntries(Object.entries(adversarialSummary).map(([k, v]) => [k, { implConfirmed: v.implConfirmed, mutationProven: v.mutationProven, implBypassCount: v.implBypassCount, testQualityCount: v.testQualityCount, testQualityFindings: v.testQualityFindings }])),
  harden: Object.fromEntries(Object.entries(hardenByTask).map(([k, v]) => [k, { tests_strengthened: v.tests_strengthened, mutation_red: v.mutation_red, mutation_restored_green: v.mutation_restored_green, suite_count: v.suite_count, commit_sha: v.commit_sha }])),
  reAdversarial: Object.fromEntries(Object.entries(reAdvByTask).map(([k, v]) => [k, { bypass_kind: v.bypass_kind, mutation_proven: v.mutation_proven, any_bypass: v.any_bypass }])),
  integrate: { suite_count: integrate.suite_count, compose_ok: integrate.compose_ok, dryrun_exit0: integrate.dryrun_exit0 },
  close,
}
