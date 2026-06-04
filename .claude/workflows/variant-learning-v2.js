export const meta = {
  name: 'variant-learning-v2',
  description: 'Build Creative Variation v2 (close the A/B learning loop) via sequential TDD + adversarial safety verification',
  whenToUse: 'Execute the v2 feedback-loop plan with multi-agent verification of the trust-gate and amplify-isolation safety claims',
  phases: [
    { title: 'Implement', detail: 'one agent per plan task, SEQUENTIAL (shared working tree), full RED->GREEN->VERIFY->commit each' },
    { title: 'Verify', detail: 'adversarial skeptics on the trust-gate + amplify-isolation safety claims, read-only, parallel' },
    { title: 'Synthesize', detail: 'completeness critic + final full-suite/ruff gate' },
  ],
}

// ---------------------------------------------------------------------------
// CONTEXT shared by every agent. The plan + spec are on disk (merged in PR #13).
// ---------------------------------------------------------------------------
const REPO = '/Users/molhamhomsi/Moh Flow Fanops'
const PLAN = 'docs/superpowers/plans/2026-06-04-creative-variation-v2-feedback.md'
const SPEC = 'docs/superpowers/specs/2026-06-04-creative-variation-v2-feedback-design.md'

const COMMON = `You are working in the git repo at ${REPO} on branch \`feat-variant-learning-impl\`.
The implementation plan is \`${PLAN}\` and the design spec is \`${SPEC}\` — READ BOTH before doing anything.

NON-NEGOTIABLE PROJECT RULES (from the repo's standing wisdom — violating these fails the task):
- ALWAYS activate the venv first: \`source .venv/bin/activate\`. Run tests as \`python -m pytest -q\` (NEVER bare \`pytest\` — a bare run falsely reports "pytest-mock not installed"). Lint as \`ruff check src/\`.
- Strict TDD: write the FAILING test first, RUN it and confirm it fails for the right reason, THEN write minimal code, THEN confirm green. Do not write impl before a red test.
- Baseline before you start this task: 363 passed, 1 skipped. Your task must end with the FULL suite green (your new tests added) — run \`python -m pytest -q\` and quote the literal final line.
- Determinism is the #1 historical bug class: NO \`random\`, NO builtin \`hash()\`, NO wall-clock in any logic. Content-addressed / pure functions only.
- Fail-open: a variant-learning failure must NEVER block a caption, hold a clip, or fail a post.
- C1 SAFETY INVARIANT: nothing you write may make \`track.py\` or \`pipeline.py\` import or depend on \`variant_learning\` — the amplify/delete-cascade path MUST stay blind to the learner.
- Match existing code style: before writing a test or impl, GREP the neighbouring file for how it builds Config/Ledger/fixtures and how similar config toggles (e.g. FANOPS_CREATIVE_VARIATION) are implemented, and mirror it exactly. The plan's code blocks are a GUIDE — adapt to what's actually in the files (e.g. the real truthy-helper name, the real fixture style).
- Commit with the plan's commit message for your task, ending with: Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

const IMPL_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['task', 'committed', 'commit_sha', 'suite_result', 'files_changed', 'notes'],
  properties: {
    task: { type: 'string', description: 'which plan task (e.g. "Task 2: config")' },
    committed: { type: 'boolean', description: 'true only if you actually git-committed' },
    commit_sha: { type: 'string', description: 'the short sha of your commit, or "" if not committed' },
    suite_result: { type: 'string', description: 'the LITERAL final line of python -m pytest -q (e.g. "365 passed, 1 skipped in 12.1s")' },
    files_changed: { type: 'array', items: { type: 'string' }, description: 'paths you created or modified' },
    notes: { type: 'string', description: 'any deviation from the plan and WHY, or "none"' },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['claim', 'holds', 'evidence', 'attack_tried'],
  properties: {
    claim: { type: 'string' },
    holds: { type: 'boolean', description: 'true if the safety claim survives your attack' },
    evidence: { type: 'string', description: 'literal command output / file excerpt proving your verdict' },
    attack_tried: { type: 'string', description: 'the specific way you tried to break or disprove the claim' },
  },
}

// ---------------------------------------------------------------------------
// PHASE 1 — IMPLEMENT (SEQUENTIAL: each task edits the same working tree).
// Execution order per the plan's Self-Review: 2 -> 1 -> 3 -> 4 -> 5.
// We run them one-at-a-time (await in series) — NOT parallel — because they
// share one working tree and each builds on the previous commit.
// ---------------------------------------------------------------------------
phase('Implement')

const tasks = [
  { id: 'Task 2', label: 'config', prompt: `${COMMON}\n\nDO ONLY **Task 2** from the plan: add the \`FANOPS_VARIANT_LEARNING\` config toggle (default OFF) plus \`FANOPS_VARIANT_MIN_POSTS\` (default 3) and \`FANOPS_VARIANT_MIN_GAP\` (default 10.0) to \`src/fanops/config.py\`, TDD via \`tests/test_config.py\`. This runs FIRST because later tasks read these config fields. Follow the plan's Task 2 steps exactly; mirror the existing toggle implementation in config.py.` },
  { id: 'Task 1', label: 'scorer', prompt: `${COMMON}\n\nDO ONLY **Task 1** from the plan: create \`src/fanops/variant_learning.py\` with the pure read-only \`best_hooks(led, cfg, account, platform) -> list[str]\` gated scorer, TDD via \`tests/test_variant_learning.py\`. The config fields it reads (\`cfg.variant_min_posts\`, \`cfg.variant_min_gap\`) ALREADY EXIST (Task 2 landed them). This is the LOAD-BEARING SAFETY UNIT — test the gate hardest: below-min-posts -> [], enough-posts-but-gap-too-small -> [] (noise guard), clear-winner -> [hook], other-surface-isolated, empty, deterministic. Do NOT add the amplify-isolation grep test here yet (that's Task 5).` },
  { id: 'Task 3', label: 'prompt', prompt: `${COMMON}\n\nDO ONLY **Task 3** from the plan: make \`caption_prompt\` in \`src/fanops/prompts.py\` render a learned-hint block when \`payload["learned_hooks"]\` is present (with a "lean toward this style, do NOT copy verbatim" instruction), TDD via \`tests/test_prompts.py\`. Absent learned_hooks -> prompt byte-identical to today.` },
  { id: 'Task 4', label: 'wire', prompt: `${COMMON}\n\nDO ONLY **Task 4** from the plan: in \`src/fanops/caption.py\`, make \`request_captions\` inject the gated learned-hook hint into the request payload (call \`variant_learning.best_hooks\` per surface when \`cfg.variant_learning\` is on, dedup, add \`learned_hooks\` to payload; the prompt block lands via Task 3). FAIL-OPEN: wrap the learning call in try/except, log once, no hint on error — and the clip must still advance. TDD via \`tests/test_caption.py\` — grep that file for its existing led/cfg/clip fixture style and reuse it. This is the task where the LOOP ACTUALLY CLOSES: one test must assert the winning hook reaches the request payload's guidance/learned_hooks ON DISK.` },
  { id: 'Task 5', label: 'isolation+integration+docs', prompt: `${COMMON}\n\nDO ONLY **Task 5** from the plan: (1) add the amplify-isolation grep test to \`tests/test_variant_learning.py\` proving \`variant_learning\` is NOT imported by \`track.py\`/\`pipeline.py\`; (2) add the optional digest gate-state line (reuse best_hooks so gate logic has one home) + its test; (3) add the REAL integration test \`tests/integration/test_variant_learning_real.py\` that builds a real on-disk ledger where one hook clearly out-lifts another over >= min_posts, runs request_captions with learning ON, and asserts the ACTUAL request file on disk carries the winning hook; (4) run sync-docs updates: add the env vars to \`MohFlow-FanOps/00_control/RUNTIME.md\` and flip backlog (j) to note v2 closes the loop on the caption-bias side (amplify auto-propagation still out of scope). Do NOT update docs/handoff.md (the orchestrator owns the handoff). End with full suite + integration + ruff all green and quote each literal result line.` },
]

const implResults = []
for (const t of tasks) {
  const r = await agent(t.prompt, { label: t.label, phase: 'Implement', schema: IMPL_SCHEMA })
  implResults.push(r)
  if (r) log(`${t.id} (${t.label}) -> committed=${r.committed} sha=${r.commit_sha} | ${r.suite_result}`)
  // Hard stop the chain if a task failed to commit or left the suite red —
  // later tasks build on this one, so continuing would compound a broken state.
  if (!r || !r.committed) {
    log(`HALT: ${t.id} did not commit cleanly; stopping the implementation chain so later tasks don't build on a broken tree.`)
    break
  }
}

const allCommitted = implResults.length === tasks.length && implResults.every(r => r && r.committed)

// ---------------------------------------------------------------------------
// PHASE 2 — ADVERSARIAL VERIFY (read-only, parallel). Only if impl completed.
// Independent skeptics attack the two safety claims that the whole design rests
// on. Read-only -> safe to fan out. Each is told to TRY TO BREAK the claim.
// ---------------------------------------------------------------------------
let verdicts = []
if (allCommitted) {
  phase('Verify')
  verdicts = await parallel([
    () => agent(`${COMMON}\n\nYou are an ADVERSARIAL SKEPTIC. Do NOT trust the implementation. Your job is to BREAK this safety claim:\n\nCLAIM: "variant_learning.best_hooks NEVER emits a winning hook on thin or noisy data — it requires >= min_posts analyzed posts AND a real lift gap >= min_gap."\n\nAttack it: read \`src/fanops/variant_learning.py\` and \`tests/test_variant_learning.py\`. Then write and RUN throwaway probe cases (in a scratch test file you delete after, or a python -c one-liner with a Config + in-memory Ledger) that TRY to get a hint out with: exactly min_posts-1 posts; a gap exactly at/just-below min_gap; ties; a single dominant post; mixed hooks where the leader has few posts but huge lift. If ANY of these leaks a non-empty result when it shouldn't, the claim is FALSE — report holds=false with the literal leaking output. Quote real command output. Activate the venv first.`,
      { label: 'skeptic:trust-gate', phase: 'Verify', schema: VERDICT_SCHEMA }),
    () => agent(`${COMMON}\n\nYou are an ADVERSARIAL SKEPTIC. Your job is to BREAK this safety claim:\n\nCLAIM: "The amplify / delete-cascade path stays completely blind to variant_learning — nothing in track.py or pipeline.py imports or transitively depends on it."\n\nAttack it: grep \`src/fanops/track.py\` and \`src/fanops/pipeline.py\` for any reference to \`variant_learning\` or \`best_hooks\` (direct). Then check TRANSITIVE imports: does either file import a module that itself imports variant_learning? Trace the import graph (e.g. \`python -c "import ast"\` over the files, or grep the import lines and follow them). Also confirm the isolation TEST that's supposed to guard this actually fails if you (temporarily, in a scratch copy — do NOT commit) add an import of variant_learning to track.py. If the amplify path can reach the learner by ANY path, holds=false with evidence. Quote literal output. Activate the venv first. Revert any scratch edit you make.`,
      { label: 'skeptic:amplify-isolation', phase: 'Verify', schema: VERDICT_SCHEMA }),
    () => agent(`${COMMON}\n\nYou are an ADVERSARIAL SKEPTIC verifying FAIL-OPEN + DETERMINISM. Break this claim:\n\nCLAIM: "A failure inside the learning path never blocks a caption (request still written, clip advances), AND best_hooks is deterministic (no random/hash/wall-clock)."\n\nAttack it: (1) grep variant_learning.py and caption.py for \`random\`, \`hash(\`, \`datetime.now\`, \`time.time\`, \`uuid\` — any of these in the learning logic = non-deterministic = holds=false. (2) Read the try/except in request_captions — does an exception in best_hooks truly leave the request written and the clip state advanced, or could a partially-built payload escape? Run the fail-open test the impl added and confirm it actually exercises a raise. (3) Call best_hooks twice on the same ledger and confirm identical output. Quote literal output. Activate the venv first.`,
      { label: 'skeptic:failopen-determinism', phase: 'Verify', schema: VERDICT_SCHEMA }),
  ])
  verdicts = verdicts.filter(Boolean)
  for (const v of verdicts) log(`verdict [${v.claim.slice(0, 48)}...] holds=${v.holds} | attack: ${v.attack_tried.slice(0, 60)}`)
}

// ---------------------------------------------------------------------------
// PHASE 3 — SYNTHESIZE: completeness critic + final gate.
// ---------------------------------------------------------------------------
let critic = null
if (allCommitted) {
  phase('Synthesize')
  critic = await agent(`${COMMON}\n\nYou are a COMPLETENESS CRITIC. The 5-task plan was implemented and 3 adversarial skeptics ran. Your job: find what's MISSING or WRONG, not re-confirm what works.\n\nDo all of: (1) run the FULL suite (\`source .venv/bin/activate && python -m pytest -q\`) and the integration tests (\`python -m pytest tests/integration -q\`) and \`ruff check src/\` — quote all three literal result lines. (2) Verify EACH of the plan's 5 tasks actually landed: the config fields, the variant_learning module + gate tests, the prompt block, the request_captions wiring (the loop closing), the isolation test + real integration test + RUNTIME.md doc update + backlog (j) flip. Name any task whose deliverable is absent or hollow. (3) Check the loop genuinely closes end-to-end: is there a test that proves a winning hook reaches the real on-disk caption request? (4) Confirm git log shows the expected commits on this branch. List concrete gaps as actionable items; if none, say so explicitly. Quote real output, do not paraphrase.`,
    { label: 'completeness-critic', phase: 'Synthesize', schema: {
      type: 'object', additionalProperties: false,
      required: ['suite_result', 'integration_result', 'ruff_result', 'tasks_landed', 'loop_closes_proven', 'gaps'],
      properties: {
        suite_result: { type: 'string' },
        integration_result: { type: 'string' },
        ruff_result: { type: 'string' },
        tasks_landed: { type: 'string', description: 'per-task: landed / hollow / missing' },
        loop_closes_proven: { type: 'boolean', description: 'is there a test proving a winning hook reaches the real request file?' },
        gaps: { type: 'array', items: { type: 'string' }, description: 'concrete actionable gaps, or empty if none' },
      },
    } })
}

return {
  implemented: implResults.filter(Boolean).map(r => ({ task: r.task, sha: r.commit_sha, suite: r.suite_result })),
  all_tasks_committed: allCommitted,
  safety_verdicts: verdicts.map(v => ({ claim: v.claim, holds: v.holds })),
  any_safety_failed: verdicts.some(v => v && !v.holds),
  completeness: critic,
}
