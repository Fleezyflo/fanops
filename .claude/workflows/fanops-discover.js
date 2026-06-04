export const meta = {
  name: 'fanops-discover',
  description: 'Content discovery + folder-review intake (fanops discover/intake): scan a folder for media + CHEAP metadata (ffprobe + 1 thumbnail, NO transcription/LLM), review in 00_review/ via Finder, approve by moving into 00_review/approved/, intake copies approved originals into 01_inbox/. Follows the written 6-task plan. Strict TDD, independent verify, SEQUENTIAL mutation proofs, real-render integration.',
  whenToUse: 'Implements docs/superpowers/plans/2026-06-04-content-discovery-review.md end to end. Resume on failure via {scriptPath, resumeFromRunId}.',
  phases: [
    { title: 'Preflight', detail: 'base state + reused primitives present' },
    { title: 'Implement', detail: 'STRICT TDD in one worktree, plan order T1->T6, commit per task' },
    { title: 'Verify', detail: 'independent re-run + cost-guardrail check (no transcribe/LLM in discover path)' },
    { title: 'Adversarial', detail: 'SEQUENTIAL skeptics on T3/T4 (dedup, approved-only, idempotent), mutation proofs' },
    { title: 'Integrate', detail: 'full suite + ruff + REAL discover->review->intake render proof' },
    { title: 'Close', detail: 'docs, push, PR, CI watch' },
  ],
}

const ROOT = '/Users/molhamhomsi/Moh Flow Fanops'
const WT = '/Users/molhamhomsi/Moh Flow Fanops-discover'
const BRANCH = 'feat-content-discovery'
const VENVRUN = `cd "${WT}" && source .venv/bin/activate &&`
const PLAN = 'docs/superpowers/plans/2026-06-04-content-discovery-review.md'

const PREFLIGHT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['base_ok', 'head', 'baseline_count', 'ruff_clean', 'no_open_prs', 'primitives_present', 'cli_structure_notes', 'notes', 'stop'],
  properties: {
    base_ok: { type: 'boolean' }, head: { type: 'string' }, baseline_count: { type: 'string' },
    ruff_clean: { type: 'boolean' }, no_open_prs: { type: 'boolean' },
    primitives_present: { type: 'boolean', description: 'scan_local, probe_dimensions, sha256_of, ledger.already_seen, Config _STAGE loop all exist' },
    cli_structure_notes: { type: 'string' }, notes: { type: 'string' }, stop: { type: 'boolean' },
  },
}
const IMPL_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['task_id', 'red', 'red_evidence', 'green', 'green_evidence', 'suite_count', 'files', 'commit_sha', 'commit_subject'],
  properties: {
    task_id: { type: 'string' }, red: { type: 'boolean' }, red_evidence: { type: 'string' },
    green: { type: 'boolean' }, green_evidence: { type: 'string' }, suite_count: { type: 'string' },
    files: { type: 'array', items: { type: 'string' } }, commit_sha: { type: 'string' }, commit_subject: { type: 'string' },
  },
}
const VERIFY_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['task_id', 'verified', 'count', 'notes'],
  properties: { task_id: { type: 'string' }, verified: { type: 'boolean' }, count: { type: 'string' }, notes: { type: 'string' } },
}
const COST_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['discover_is_cheap', 'evidence'],
  properties: {
    discover_is_cheap: { type: 'boolean', description: 'the discover() path calls NO transcribe/detect_signals/claude/claude_json — only stat+probe_dimensions+make_thumbnail' },
    evidence: { type: 'string', description: 'grep of src/fanops/discover.py imports + body proving no transcribe/signals/llm' },
  },
}
const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['task_id', 'real', 'mutation_proven', 'mutation_evidence', 'any_bypass', 'notes'],
  properties: {
    task_id: { type: 'string' }, real: { type: 'boolean' }, mutation_proven: { type: 'boolean' },
    mutation_evidence: { type: 'string' }, any_bypass: { type: 'string' }, notes: { type: 'string' },
  },
}
const INTEGRATE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['suite_count', 'ruff_clean', 'real_render_ok', 'render_evidence', 'approved_only', 'regressions', 'blocked', 'evidence'],
  properties: {
    suite_count: { type: 'string' }, ruff_clean: { type: 'boolean' },
    real_render_ok: { type: 'boolean', description: 'real ffmpeg: discover wrote viewable thumbnails for the 2 media (PII excluded)' },
    render_evidence: { type: 'string' },
    approved_only: { type: 'boolean', description: 'after approving 1, intake copied ONLY that original into 01_inbox/' },
    regressions: { type: 'string' }, blocked: { type: 'boolean' }, evidence: { type: 'string' },
  },
}
const CLOSE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['docs_updated', 'pushed', 'pr_url', 'ci_status', 'ci_run_url', 'notes'],
  properties: {
    docs_updated: { type: 'array', items: { type: 'string' } }, pushed: { type: 'boolean' },
    pr_url: { type: 'string' }, ci_status: { type: 'string' }, ci_run_url: { type: 'string' }, notes: { type: 'string' },
  },
}

// ════════════════════════════════════════════════════════════════════════
phase('Preflight')
const pf = await agent(
  `Preflight for FanOps content-discovery. Read-only in "${ROOT}" (do NOT create the worktree yet). READ the plan ${PLAN} + its spec docs/superpowers/specs/2026-06-04-content-discovery-review-design.md IN FULL.
1. base_ok/head: \`cd "${ROOT}" && git rev-parse HEAD\` (expect 079cbc9... on main), clean tree (a few untracked .claude/workflows/*.js fine).
2. baseline_count: \`cd "${ROOT}" && source .venv/bin/activate && python -m pytest -q 2>&1 | tail -3\` (expect "351 passed, 1 skipped").
3. ruff_clean: \`cd "${ROOT}" && source .venv/bin/activate && ruff check src/ 2>&1 | tail -1\`.
4. no_open_prs: \`gh pr list --repo Fleezyflo/fanops --state open\` (empty).
5. primitives_present: confirm these EXIST (the plan reuses them): ingest.scan_local(roots), ingest.probe_dimensions(path), ingest.sha256_of(path), ingest.MEDIA_EXT, ingest.is_excluded(name), ledger.already_seen(*, sha256=), and Config sets stage dirs via the _STAGE loop in __init__ (so adding "review" to _STAGE creates cfg.review).
6. cli_structure_notes: where main() builds subparsers + where _dispatch's if-chain is (for the discover/intake verbs).
stop=true only if base wrong or a primitive missing. Return ONLY structured JSON.`,
  { schema: PREFLIGHT_SCHEMA, phase: 'Preflight', label: 'preflight' }
)
log(`Preflight: base_ok=${pf.base_ok} baseline=${pf.baseline_count} ruff=${pf.ruff_clean} primitives=${pf.primitives_present} stop=${pf.stop}`)
if (pf.stop || !pf.base_ok || !pf.primitives_present) {
  return { blocked: true, phase: 'Preflight', preflight: pf }
}

// ════════════════════════════════════════════════════════════════════════
phase('Implement')
const SETUP = `STEP 0 — WORKTREE + VENV (ONCE, first implementer):
- \`cd "${ROOT}" && git worktree add "${WT}" -b ${BRANCH} main\` (resume: \`cd "${WT}" && git status\`).
- \`cd "${WT}" && python3.12 -m venv .venv && source .venv/bin/activate && pip install -q -e ".[dev]"\`. Verify pytest-timeout+ruff: \`pip show pytest-timeout ruff | head -4\`.
- Baseline: \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` -> "351 passed, 1 skipped".
`
const PLAN_REF = `Follow ${PLAN} for this task EXACTLY — it has the literal failing test + complete impl code + commit message. STRICT TDD: write the literal failing test, run it and CONFIRM RED (capture red_evidence), write the minimal impl from the plan, run it and CONFIRM GREEN (green_evidence), run the FULL suite and quote suite_count, commit with the plan's exact message + Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>. CRITICAL COST RULE: discover.py must NOT import or call transcribe / detect_signals / claude / claude_json — discovery is CHEAP (stat + probe_dimensions + make_thumbnail only). Return ONLY structured JSON.`

const TASKS = [
  { id: 'T1', extra: 'Task 1: add "review" to Config._STAGE so cfg.review = base/00_review.' },
  { id: 'T2', extra: 'Task 2: create src/fanops/discover.py with candidate_meta (cheap, fail-soft) + make_thumbnail (1 ffmpeg frame, fail-open). NO transcribe/signals/LLM imports.' },
  { id: 'T3', extra: 'Task 3: discover() orchestrator — scan_local -> thumbnail + manifest.json into cfg.review, dedup vs ledger.already_seen(sha256) AND prior manifest. Returns {found,new,skipped}.' },
  { id: 'T4', extra: 'Task 4: intake() — sweep cfg.review/approved/*.jpg, resolve each via manifest to its source_path, COPY the approved ORIGINAL into cfg.inbox; idempotent (intaken.json), missing-safe. Returns {approved,intaken,missing}.' },
  { id: 'T5', extra: 'Task 5: CLI verbs `fanops discover <folder>` (unknown folder -> stderr + exit 2) and `fanops intake`, wired in main() subparsers + _dispatch.' },
  { id: 'T6', extra: 'Task 6: tests/integration/test_discover_real.py (real ffmpeg: 2 videos + a PII-named -> 2 viewable thumbnails; approve 1 -> intake copies only that original) + docs (RUNTIME discover/review/intake section, README verbs). The discover/intake code already exists from T2-T5.' },
]
const impls = {}
for (let i = 0; i < TASKS.length; i++) {
  const t = TASKS[i]
  const r = await agent(
    `${t.id} implementer for FanOps content-discovery. ${i === 0 ? SETUP : `Worktree "${WT}" exists with venv; prior tasks committed (\`cd "${WT}" && git log --oneline -${i + 1}\`).`}
${t.extra}
${PLAN_REF}`,
    { schema: IMPL_SCHEMA, phase: 'Implement', label: `impl:${t.id}` }
  )
  impls[t.id] = r
  log(`${t.id}: red=${r?.red} green=${r?.green} suite=${r?.suite_count} sha=${(r?.commit_sha || '').slice(0, 8)}`)
  if (!r || !r.green || !r.commit_sha) {
    log(`IMPLEMENT BLOCKED at ${t.id}.`)
    return { blocked: true, phase: 'Implement', failed_task: t.id, impls }
  }
}

// ════════════════════════════════════════════════════════════════════════
phase('Verify')
const vtasks = [
  { id: 'T1', test: 'tests/test_config.py -k review' },
  { id: 'T2', test: 'tests/test_discover.py -k "candidate_meta or thumbnail"' },
  { id: 'T3', test: 'tests/test_discover.py -k discover_' },
  { id: 'T4', test: 'tests/test_discover.py -k intake' },
  { id: 'T5', test: 'tests/test_cli.py -k "discover or intake"' },
]
const verifies = await parallel(vtasks.map(t => () =>
  agent(
    `INDEPENDENT Verify agent for FanOps discovery task ${t.id} (you did NOT implement it). Worktree "${WT}", venv .venv.
1. \`${VENVRUN} python -m pytest ${t.test} -v 2>&1 | tail -15\` — task tests PASS.
2. FULL suite: \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` — QUOTE count.
3. Non-vacuous: grep the implemented symbol exists + is wired (T1: review in config.py _STAGE; T2/T3/T4: candidate_meta/make_thumbnail/discover/intake in discover.py; T5: discover+intake in cli.py _dispatch).
RECURRING FALSE ALARM: "pytest-mock not installed / N errors" = forgot the venv -> re-run with \`${VENVRUN} python -m pytest ...\`. verified=true ONLY if task tests pass AND wiring is real. Return ONLY structured JSON.`,
    { schema: VERIFY_SCHEMA, phase: 'Verify', label: `verify:${t.id}` }
  )
))
for (const v of verifies.filter(Boolean)) log(`Verify ${v.task_id}: verified=${v.verified} count="${v.count}"`)

// the cost-guardrail check (the spec's load-bearing invariant)
const cost = await agent(
  `Cost-guardrail agent for FanOps discovery. Worktree "${WT}". The spec's #1 rule: \`discover()\` must be CHEAP — NO transcription, NO LLM, NO signal detection in the discovery path (that expensive work happens only AFTER intake, on approved items). Prove it:
- \`grep -nE "import|transcribe|detect_signals|claude|claude_json|signals" "${WT}/src/fanops/discover.py"\` — confirm discover.py imports ONLY cheap things (config, ledger, ingest.{scan_local,probe_dimensions,sha256_of}, os/json/shutil/subprocess/pathlib) and NEVER references transcribe/detect_signals/claude/claude_json/signals.
- Confirm make_thumbnail runs ONE ffmpeg frame-grab and candidate_meta runs ONE probe_dimensions — no per-candidate LLM/transcription.
Set discover_is_cheap accordingly + quote the grep. Return ONLY structured JSON.`,
  { schema: COST_SCHEMA, phase: 'Verify', label: 'cost-guardrail' }
)
log(`Cost guardrail: discover_is_cheap=${cost.discover_is_cheap}`)

// ════════════════════════════════════════════════════════════════════════
// ADVERSARIAL — SEQUENTIAL (no parallel-shared-worktree). T3 (dedup) + T4 (approved-only/idempotent).
phase('Adversarial')
const advSpecs = [
  { id: 'T3', prompt: `Prove discover() DEDUPS: a candidate whose sha256 is already a ledger Source is SKIPPED (not re-thumbnailed/re-manifested), and re-running discover() on the same folder adds nothing new. MUTATION: remove the \`led.already_seen(sha256=digest)\` (or the \`eid in manifest\`) check, confirm test_discover_dedupes_already_seen_content FAILS, then \`git checkout src/fanops/discover.py\`. Confirm git status clean.` },
  { id: 'T4', prompt: `THE KEY ONE — prove intake() copies ONLY approved originals + is idempotent + missing-safe. (a) Only the entry MOVED into approved/ has its original copied to 01_inbox/; a non-approved candidate is NOT copied. MUTATION: make intake copy ALL manifest entries (ignore the approved/ filter), confirm test_intake_copies_only_approved_originals FAILS, restore. (b) Idempotent: a second intake() does not re-copy (intaken.json). (c) A stale approved entry with no/ missing original is reported \`missing\`, never a crash. Verify by reading + running the tests.` },
]
const verdicts = []
for (const t of advSpecs) {
  for (let k = 0; k < 2; k++) {
    const v = await agent(
      `INDEPENDENT adversarial skeptic #${k + 1} for FanOps discovery task ${t.id} (you did NOT implement/verify it). Default real=false unless you positively confirm. Worktree "${WT}", venv .venv. Running SEQUENTIALLY. After ANY mutation \`cd "${WT}" && git checkout <file>\` and confirm \`git status --porcelain\` CLEAN before returning. NEVER commit a mutation.

${t.prompt}

real=true ONLY if it genuinely satisfies the contract. mutation_proven=true ONLY if you reverted/injected, watched the test FAIL (capture the line), and restored clean. any_bypass = how it misbehaves (empty if none). Return ONLY structured JSON.`,
      { schema: VERDICT_SCHEMA, phase: 'Adversarial', label: `adv:${t.id}#${k + 1}` }
    )
    verdicts.push(v)
    if (v) log(`Adversarial ${v.task_id}#${k + 1}: real=${v.real} mutation_proven=${v.mutation_proven} bypass=${v.any_bypass || 'none'}`)
  }
}
const advByTask = {}
for (const v of verdicts.filter(Boolean)) (advByTask[v.task_id] ||= []).push(v)
const advConfirmed = {}
for (const id of ['T3', 'T4']) {
  const vs = advByTask[id] || []
  const realN = vs.filter(v => v.real).length
  const mutN = vs.filter(v => v.mutation_proven).length
  advConfirmed[id] = { confirmed: vs.length > 0 && realN >= Math.ceil(vs.length / 2) && mutN >= 1, realN, total: vs.length, mutN }
  log(`Adversarial ${id}: confirmed=${advConfirmed[id].confirmed} real=${realN}/${vs.length} mut=${mutN}`)
}

// ════════════════════════════════════════════════════════════════════════
phase('Integrate')
const allVerifyOk = verifies.filter(Boolean).every(v => v.verified)
const allAdvOk = ['T3', 'T4'].every(id => advConfirmed[id].confirmed)
if (!allVerifyOk || !cost.discover_is_cheap || !allAdvOk) {
  log(`PRE-INTEGRATE BLOCK: verifyOk=${allVerifyOk} cheap=${cost.discover_is_cheap} advOk=${allAdvOk}`)
  return { blocked: true, phase: 'pre-Integrate', verifies, cost, advConfirmed }
}
const integ = await agent(
  `Integrate agent for FanOps content-discovery — MUST include a REAL discover->review->intake run (the proof). Worktree "${WT}", venv .venv. All 6 tasks committed on ${BRANCH}.
1. FULL suite: \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` -> suite_count (expect 351 + new). regressions: any 351-baseline test now red? (expect none).
2. ruff: \`${VENVRUN} ruff check src/ 2>&1 | tail -1\` -> ruff_clean.
3. REAL RUN (unmocked ffmpeg): make a scratch bank dir with TWO real videos (\`ffmpeg -f lavfi -i color=c=navy:s=720x1280:d=3 keep.mp4\`, color=darkgreen skip.mp4) + a PII-named file ("tax return.mp4", any bytes). From a scratch cwd, run the REAL discover() (or \`python -m fanops.cli discover <bank>\`): assert 2 viewable thumbnails appear in 00_review/ (2 .jpg, size>0) and the PII file is excluded; manifest.json has 2 entries. Set real_render_ok + render_evidence (thumbnail count/sizes, manifest entries).
4. APPROVED-ONLY: move keep.mp4's thumbnail into 00_review/approved/, run intake (or \`fanops intake\`): assert ONLY keep.mp4 landed in 01_inbox/ (skip.mp4 did NOT). Set approved_only. BONUS: run \`fanops advance\` (dryrun) and confirm the intaken keep.mp4 enters the pipeline (a source appears).
5. blocked=true if suite regresses, ruff fails, thumbnails aren't real, or intake copied a non-approved file. Return ONLY structured JSON.`,
  { schema: INTEGRATE_SCHEMA, phase: 'Integrate', label: 'integrate' }
)
log(`Integrate: suite=${integ.suite_count} ruff=${integ.ruff_clean} real_render=${integ.real_render_ok} approved_only=${integ.approved_only} blocked=${integ.blocked}`)
log(`  render evidence: ${integ.render_evidence}`)
if (integ.blocked || !integ.real_render_ok || !integ.approved_only) {
  return { blocked: true, phase: 'Integrate', integ, impls, verifies, cost, advConfirmed }
}

// ════════════════════════════════════════════════════════════════════════
phase('Close')
const close = await agent(
  `Close agent for FanOps content-discovery (superpowers:finishing-a-development-branch posture). All 6 tasks committed on ${BRANCH} in "${WT}"; suite green (${integ.suite_count}); ruff clean; real discover->intake proven. Work in the worktree.
DOCS (sync-docs — read then edit; if T6 already did them, verify+supplement): RUNTIME.md — a "Content discovery + review intake" section (fanops discover <folder> -> 00_review/ thumbnails+metadata, cheap/no-LLM; browse in Finder + move keepers into 00_review/approved/; fanops intake -> approved originals into 01_inbox/; dedups vs ledger). README — add discover/intake to the command list. docs_updated = files edited. COMMIT docs if not already (skip if T6 committed all + tree clean).
PUSH + PR + CI:
- \`cd "${WT}" && git push -u origin ${BRANCH} 2>&1\`. pushed.
- \`gh pr create --repo Fleezyflo/fanops --base main --head ${BRANCH} --title "Content discovery + folder-review intake (fanops discover/intake)" --body "<short: scan a folder for media + cheap metadata (ffprobe + 1 thumbnail, NO transcription/LLM = least cost) -> 00_review/; approve by moving thumbnails into 00_review/approved/; fanops intake copies approved originals into 01_inbox/ for the existing pipeline. Rejects never pipelined. Dedups vs ledger; fail-soft/open; idempotent intake. Real discover->intake proven. Suite ${integ.suite_count}.>"\` -> pr_url.
- WATCH CI: \`gh run list --repo Fleezyflo/fanops --limit 3\` -> find the ${BRANCH} run id -> \`gh run watch <id> --repo Fleezyflo/fanops --exit-status 2>&1 | tail -20\`. Re-query on a dropped watch. ci_status + ci_run_url. Both jobs green.
deviations + handoff are the ORCHESTRATOR's job — do NOT touch ~/.claude memory. Return ONLY structured JSON.`,
  { schema: CLOSE_SCHEMA, phase: 'Close', label: 'close' }
)
log(`Close: docs=${JSON.stringify(close.docs_updated)} pushed=${close.pushed} ci=${close.ci_status}`)

return {
  blocked: false,
  preflight: pf,
  impls: Object.fromEntries(Object.entries(impls).map(([k, v]) => [k, { red: v.red, green: v.green, suite_count: v.suite_count, commit_sha: v.commit_sha }])),
  verifies: verifies.filter(Boolean),
  cost,
  adversarial: advConfirmed,
  adversarial_verdicts: verdicts.filter(Boolean),
  integrate: integ,
  close,
  worktree: WT,
  branch: BRANCH,
}
