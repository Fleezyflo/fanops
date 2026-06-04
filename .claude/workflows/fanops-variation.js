export const meta = {
  name: 'fanops-variation',
  description: 'Per-account creative variation (backlog j): per-account caption + burned-in hook (shared base clip + cheap per-account overlay pass), deterministic variant_key, observe-only lift-by-variant in the digest. Follows the written 7-task plan. Strict TDD, independent verify, SEQUENTIAL adversarial mutation proofs, real two-account render proof. Touches NONE of the amplify/cascade machinery.',
  whenToUse: 'Implements docs/superpowers/plans/2026-06-04-per-account-creative-variation.md end to end. Resume on failure via {scriptPath, resumeFromRunId}.',
  phases: [
    { title: 'Preflight', detail: 'base state + ffmpeg + resolve poster-seam & response-helper unknowns' },
    { title: 'Frame', detail: 'per-task structured impl plan against real code' },
    { title: 'Implement', detail: 'STRICT TDD in one worktree, plan order T1->T7, commit per task' },
    { title: 'Verify', detail: 'independent re-run + 4 special checks (off-path unchanged, determinism, fail-open, amplify untouched)' },
    { title: 'Adversarial', detail: '>=2 SEQUENTIAL skeptics on T2/T5/T6, mutation proofs' },
    { title: 'Integrate', detail: 'full suite + ruff + REAL two-account render proof' },
    { title: 'Close', detail: 'sync-docs, push, PR, CI watch' },
  ],
}

const ROOT = '/Users/molhamhomsi/Moh Flow Fanops'
const WT = '/Users/molhamhomsi/Moh Flow Fanops-variation'
const BRANCH = 'feat-creative-variation'
const VENVRUN = `cd "${WT}" && source .venv/bin/activate &&`
const PLAN = 'docs/superpowers/plans/2026-06-04-per-account-creative-variation.md'

const PREFLIGHT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['base_ok', 'head', 'baseline_count', 'ruff_clean', 'no_open_prs', 'ffmpeg_text_ok', 'sites_present', 'poster_seam', 'response_helper', 'notes', 'stop'],
  properties: {
    base_ok: { type: 'boolean' }, head: { type: 'string' }, baseline_count: { type: 'string' },
    ruff_clean: { type: 'boolean' }, no_open_prs: { type: 'boolean' }, ffmpeg_text_ok: { type: 'boolean' },
    sites_present: { type: 'boolean' },
    poster_seam: { type: 'string', description: 'HOW the clip file reaches the poster — does publish_due/poster read Post.media_urls, or clip.media_url via ensure_clip_media? quote the resolved mechanism + file:line' },
    response_helper: { type: 'string', description: 'the real agentstep helper to write a caption response file (e.g. response_path) + how tests/test_caption.py writes responses' },
    notes: { type: 'string' }, stop: { type: 'boolean' },
  },
}
const FRAME_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['tasks', 'invariants', 'order_ok'],
  properties: {
    tasks: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['id', 'files', 'failing_test', 'impl', 'commit_msg'],
      properties: { id: { type: 'string' }, files: { type: 'array', items: { type: 'string' } },
        failing_test: { type: 'string' }, impl: { type: 'string' }, commit_msg: { type: 'string' } } } },
    invariants: { type: 'array', items: { type: 'string' } },
    order_ok: { type: 'boolean', description: 'T1->T2->T3->T4->T5(needs T1-4)->T6(needs T1)->T7 confirmed' },
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
const SPECIAL_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['off_path_unchanged', 'variant_key_deterministic', 'failopen_holds', 'amplify_untouched', 'evidence'],
  properties: {
    off_path_unchanged: { type: 'boolean', description: 'FANOPS_CREATIVE_VARIATION unset -> crosspost path identical to today (no variant_key, burn_hook_only NOT called)' },
    variant_key_deterministic: { type: 'boolean', description: 'same (account,platform,clip) -> same variant_key across processes' },
    failopen_holds: { type: 'boolean', description: 'no text filter -> plain clip, no raise; no hook -> no variant' },
    amplify_untouched: { type: 'boolean', description: 'git diff main -- adjust.py ledger.py shows NO behavioral change to amplify/classify_outcomes/_delete_moment_cascade' },
    evidence: { type: 'string' },
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
  required: ['suite_count', 'ruff_clean', 'real_render_ok', 'render_evidence', 'two_account_distinct', 'regressions', 'blocked', 'evidence'],
  properties: {
    suite_count: { type: 'string' }, ruff_clean: { type: 'boolean' },
    real_render_ok: { type: 'boolean', description: 'unmocked ffmpeg: two hooks -> two files that DIFFER from each other + base; OCR if tesseract present' },
    render_evidence: { type: 'string' },
    two_account_distinct: { type: 'boolean', description: 'a 2-account crosspost (variation ON) -> 2 Posts, different variant_key/variant_hook, different files' },
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
  `Preflight for FanOps per-account-creative-variation (backlog j). Read-only in "${ROOT}" (do NOT create the worktree yet). READ the plan ${PLAN} and its spec docs/superpowers/specs/2026-06-04-per-account-creative-variation-design.md IN FULL first.
1. base_ok/head: \`cd "${ROOT}" && git rev-parse HEAD\` (expect cfb79ca... on main), clean tree.
2. baseline_count: \`cd "${ROOT}" && source .venv/bin/activate && python -m pytest -q 2>&1 | tail -3\` (expect "338 passed, 1 skipped").
3. ruff_clean: \`cd "${ROOT}" && source .venv/bin/activate && ruff check src/ 2>&1 | tail -1\` ("All checks passed!").
4. no_open_prs: \`gh pr list --repo Fleezyflo/fanops --state open\` (empty).
5. ffmpeg_text_ok: \`ffmpeg -hide_banner -h filter=drawtext 2>&1 | head -1\` (NOT "Unknown filter").
6. sites_present: confirm the plan's edit sites exist — models.py CaptionItem (~L171) + Post (~L93-108); overlay.py build_ass/write_ass/subtitles_vf/ffmpeg_has_textfilter; caption.py ingest_captions meta_captions store (~L129); prompts.py caption_prompt (~L32); crosspost.py crosspost_clips surface loop (~L69-98) using ids.surface_key/_hash; config.py property pattern; digest.py render_digest.
7. **RESOLVE poster_seam (the plan's known integration risk):** grep src/fanops/post/ for media_urls, ensure_clip_media, media_url, and read how publish_due / the posters resolve the file to upload. Report EXACTLY how a clip file reaches the poster today (Post.media_urls? clip.media_url via ensure_clip_media?) with file:line — Task 5 must route the per-account variant file through THAT seam.
8. **RESOLVE response_helper:** read src/fanops/agentstep.py for the real helper to write a caption RESPONSE file; check how tests/test_caption.py already writes a response. Report the real function name(s).
stop=true only if base is wrong, ffmpeg_text_ok false, or a site is genuinely missing. Return ONLY structured JSON.`,
  { schema: PREFLIGHT_SCHEMA, phase: 'Preflight', label: 'preflight' }
)
log(`Preflight: base_ok=${pf.base_ok} baseline=${pf.baseline_count} ruff=${pf.ruff_clean} ffmpeg=${pf.ffmpeg_text_ok} sites=${pf.sites_present} stop=${pf.stop}`)
log(`  poster_seam: ${pf.poster_seam}`)
log(`  response_helper: ${pf.response_helper}`)
if (pf.stop || !pf.base_ok || !pf.ffmpeg_text_ok || !pf.sites_present) {
  return { blocked: true, phase: 'Preflight', preflight: pf }
}

// ════════════════════════════════════════════════════════════════════════
phase('Frame')
const frame = await agent(
  `Frame agent for FanOps creative variation. Read-only "${ROOT}". Read the plan ${PLAN} + the CURRENT body of models.py, overlay.py, caption.py, prompts.py, crosspost.py, config.py, digest.py, and post/ (for the upload seam). The Preflight resolved two integration unknowns — USE them: poster_seam = "${pf.poster_seam}"; response_helper = "${pf.response_helper}".
Produce a per-task structured plan for the plan's 7 tasks (T1 model fields, T2 overlay.burn_hook_only, T3 caption per-surface hook, T4 FANOPS_CREATIVE_VARIATION config, T5 crosspost variant wiring, T6 digest lift-by-variant, T7 integration+docs). For each: id; files; failing_test (the literal test from the plan); impl (concrete code against the REAL current body — for T5 specify the exact field the variant file flows through, per poster_seam; for T3 specify the real response helper); commit_msg (from the plan).
invariants (list): variant_key is content-addressed (ids.surface_key/_hash), NEVER random/hash(); fail-open everywhere (variation off / no hook / no libass -> today's shared-clip behavior, never a blocked post); v1 touches NONE of amplify/classify_outcomes/_delete_moment_cascade; FANOPS_CREATIVE_VARIATION defaults OFF; optional model fields (old ledgers load).
order_ok: confirm T1->T2->T3->T4->T5->T6->T7 (T5 needs T1-T4, T6 needs T1) — they run SEQUENTIALLY in one worktree (shared files). Return ONLY structured JSON.`,
  { schema: FRAME_SCHEMA, phase: 'Frame', label: 'frame' }
)
log(`Frame: ${frame.tasks.length} tasks; order_ok=${frame.order_ok}; invariants=${frame.invariants.length}`)

// ════════════════════════════════════════════════════════════════════════
phase('Implement')
const SETUP = `STEP 0 — WORKTREE + VENV (ONCE, first implementer):
- \`cd "${ROOT}" && git worktree add "${WT}" -b ${BRANCH} main\` (resume: \`cd "${WT}" && git status\` instead).
- \`cd "${WT}" && python3.12 -m venv .venv && source .venv/bin/activate && pip install -q -e ".[dev]"\`. Verify: \`pip show pytest-timeout ruff | head -4\`.
- Baseline: \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` -> "338 passed, 1 skipped"; \`${VENVRUN} ruff check src/ 2>&1 | tail -1\` clean.
`
const PLAN_REF = `Follow ${PLAN} for this task EXACTLY — it has the literal failing test + complete impl code + commit message. Use the Preflight-resolved facts: poster_seam = "${pf.poster_seam}"; response_helper = "${pf.response_helper}". STRICT TDD: write the literal failing test, run it and CONFIRM IT FAILS (capture red_evidence), write the minimal impl from the plan, run it and CONFIRM GREEN (green_evidence), run the FULL suite (\`${VENVRUN} python -m pytest -q 2>&1 | tail -3\`) and quote suite_count, then commit with the plan's exact message + Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>. If the plan's test references an API that doesn't match the real code (e.g. the response helper), ADJUST the test to the REAL API (per response_helper) before running — do NOT invent APIs. Return ONLY structured JSON.`

const TASKS = [
  { id: 'T1', extra: 'Task 1: CaptionItem.hook + Post.variant_key/variant_hook (optional fields, models.py).' },
  { id: 'T2', extra: 'Task 2: overlay.burn_hook_only — cheap per-account hook overlay on a shared base clip; FAIL-OPEN (no textfilter/empty hook -> copy base, return False, no ffmpeg). Reuses build_ass/write_ass/subtitles_vf/ffmpeg_has_textfilter.' },
  { id: 'T3', extra: 'Task 3: caption_prompt asks for a per-surface hook; ingest_captions stores meta_captions[surface]["hook"]. Use the REAL response helper from preflight in the test.' },
  { id: 'T4', extra: 'Task 4: FANOPS_CREATIVE_VARIATION config property (default OFF, opt-in).' },
  { id: 'T5', extra: 'Task 5: crosspost wires the per-account variant — when cfg.creative_variation AND cap.get("hook"): burn a per-account file via overlay.burn_hook_only from the shared base clip, route it through the poster_seam from preflight, stamp Post.variant_key (= surface_key, DETERMINISTIC) + variant_hook. Variation OFF -> identical to today. Read the CURRENT crosspost add_post block first.' },
  { id: 'T6', extra: 'Task 6: digest "Lift by variant" section — group analyzed posts with a variant_key by lift_score; absent when no variants.' },
  { id: 'T7', extra: 'Task 7: integration test tests/integration/test_variation_render.py (real two-account burn, marked integration, skips cleanly w/o libass) + docs (RUNTIME §Backlog mark (j) v1 DONE + FANOPS_CREATIVE_VARIATION env table; README). The render code already exists from T2.' },
]
const impls = {}
let prevSha = null
for (let i = 0; i < TASKS.length; i++) {
  const t = TASKS[i]
  const isFirst = i === 0
  const r = await agent(
    `${t.id} implementer for FanOps creative variation. ${isFirst ? SETUP : `Worktree "${WT}" exists with venv; prior tasks committed (\`cd "${WT}" && git log --oneline -${i + 1}\`).`}
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
  prevSha = r.commit_sha
}

// ════════════════════════════════════════════════════════════════════════
phase('Verify')
const vtasks = [
  { id: 'T1', test: 'tests/test_models.py -k "hook or variant"' },
  { id: 'T2', test: 'tests/test_overlay.py -k burn_hook_only' },
  { id: 'T3', test: 'tests/test_prompts.py -k hook tests/test_caption.py -k per_surface_hook' },
  { id: 'T4', test: 'tests/test_config.py -k creative_variation' },
  { id: 'T5', test: 'tests/test_crosspost.py -k variant' },
  { id: 'T6', test: 'tests/test_digest.py -k variant' },
]
const verifies = await parallel(vtasks.map(t => () =>
  agent(
    `INDEPENDENT Verify agent for FanOps variation task ${t.id} (you did NOT implement it). Worktree "${WT}", venv .venv.
1. \`${VENVRUN} python -m pytest ${t.test} -v 2>&1 | tail -15\` — task tests PASS.
2. FULL suite: \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` — QUOTE count.
3. Non-vacuous: grep the implemented symbol exists + is wired (T1: hook/variant_key/variant_hook in models.py; T2: overlay.burn_hook_only; T3: hook in caption_prompt + meta_captions store; T4: creative_variation in config.py; T5: burn_hook_only call + variant_key in crosspost.py; T6: "Lift by variant" in digest.py).
RECURRING FALSE ALARM: if you see "pytest-mock not installed / N errors", you forgot the venv — re-run with \`${VENVRUN} python -m pytest ...\`. verified=true ONLY if task tests pass AND wiring is real. Return ONLY structured JSON.`,
    { schema: VERIFY_SCHEMA, phase: 'Verify', label: `verify:${t.id}` }
  )
))
for (const v of verifies.filter(Boolean)) log(`Verify ${v.task_id}: verified=${v.verified} count="${v.count}"`)

// the 4 special checks (one dedicated agent)
const special = await agent(
  `Special-checks agent for FanOps creative variation. Worktree "${WT}", venv .venv. Prove the 4 load-bearing invariants on the committed branch:
1. off_path_unchanged: with FANOPS_CREATIVE_VARIATION UNSET, a crosspost produces Posts with variant_key=None and overlay.burn_hook_only is NOT called (the existing tests/test_crosspost.py path). Run the existing crosspost tests + the new "no_variant_when_disabled" test.
2. variant_key_deterministic: in two separate python processes, compute the variant_key for the same (account,platform,clip) and confirm IDENTICAL (it must be SHA via surface_key, not random/hash()). Run e.g. \`${VENVRUN} python -c "from fanops.ids import surface_key; print(surface_key('@a','instagram'))"\` twice in fresh processes -> same output; and confirm crosspost uses surface_key (grep).
3. failopen_holds: with FANOPS_CREATIVE_VARIATION=1 but overlay.ffmpeg_has_textfilter monkeypatched False, a crosspost still produces a post (burn_hook_only copies base, returns False) — no raise; and a surface with no hook -> no variant. (Reason from the tests + code.)
4. amplify_untouched: \`cd "${WT}" && git diff main -- src/fanops/adjust.py src/fanops/ledger.py\` shows NO behavioral change to amplify/classify_outcomes/_delete_moment_cascade (ideally an EMPTY diff for adjust.py). Quote the diff stat.
Return ONLY structured JSON.`,
  { schema: SPECIAL_SCHEMA, phase: 'Verify', label: 'special-checks' }
)
log(`Special: off_unchanged=${special.off_path_unchanged} determ=${special.variant_key_deterministic} failopen=${special.failopen_holds} amplify_untouched=${special.amplify_untouched}`)

// ════════════════════════════════════════════════════════════════════════
// ADVERSARIAL — SEQUENTIAL (lesson: parallel-shared-worktree skeptics contaminate). 2 per high-risk task.
phase('Adversarial')
const advSpecs = [
  { id: 'T2', prompt: `Prove overlay.burn_hook_only BURNS the hook when able (textfilter present + non-empty hook -> ffmpeg cmd with subtitles= and a distinct output file) AND FAILS OPEN otherwise (no textfilter OR empty hook -> output is a byte-copy of the base, returns False, ffmpeg NOT invoked). MUTATION: make the fail-open path RAISE instead of copy, confirm test_burn_hook_only_failopen_when_no_textfilter FAILS, then \`git checkout src/fanops/overlay.py\`. Confirm git status clean after.` },
  { id: 'T5', prompt: `THE KEY ONE. (a) With FANOPS_CREATIVE_VARIATION=1 + per-surface hooks, two accounts get DIFFERENT variant_key AND different variant_hook AND burn_hook_only called per account. MUTATION: make variant_key a constant (or drop it), confirm test_crosspost_creates_per_account_variant FAILS, restore. (b) DETERMINISM: variant_key = surface_key(account,platform) (content-addressed), NOT random/hash() — verify by reading the code + that it's stable across processes. (c) With variation OFF, behavior is IDENTICAL to today (no variant_key, burn NOT called) — MUTATION: make the off-path still call burn, confirm test_crosspost_no_variant_when_disabled FAILS, restore.` },
  { id: 'T6', prompt: `Prove the digest "Lift by variant" section ranks analyzed variant-posts by lift_score (highest first) and is ABSENT when there are no variant posts. MUTATION: change the section to NOT filter on variant_key (include all posts) or to sort ascending, confirm test_digest_shows_lift_by_variant FAILS (wrong order / wrong content), restore.` },
]
const verdicts = []
for (const t of advSpecs) {
  for (let k = 0; k < 2; k++) {
    const v = await agent(
      `INDEPENDENT adversarial skeptic #${k + 1} for FanOps variation task ${t.id} (you did NOT implement/verify it). Default real=false unless you positively confirm. Worktree "${WT}", venv .venv. You run SEQUENTIALLY (no other skeptic active) — but still, after ANY mutation \`cd "${WT}" && git checkout <file>\` and confirm \`git status --porcelain\` CLEAN before returning. NEVER commit a mutation.

${t.prompt}

real=true ONLY if it genuinely satisfies the contract. mutation_proven=true ONLY if you reverted/injected, watched the test FAIL (capture the line), and restored clean. any_bypass = how it misbehaves (empty if none). Return ONLY structured JSON.`,
      { schema: VERDICT_SCHEMA, phase: 'Adversarial', label: `adv:${t.id}#${k + 1}` }
    )
    verdicts.push(v)
    if (v) log(`Adversarial ${v.task_id}: real=${v.real} mutation_proven=${v.mutation_proven} bypass=${v.any_bypass || 'none'}`)
  }
}
const advByTask = {}
for (const v of verdicts.filter(Boolean)) (advByTask[v.task_id] ||= []).push(v)
const advConfirmed = {}
for (const id of ['T2', 'T5', 'T6']) {
  const vs = advByTask[id] || []
  const realN = vs.filter(v => v.real).length
  const mutN = vs.filter(v => v.mutation_proven).length
  advConfirmed[id] = { confirmed: vs.length > 0 && realN >= Math.ceil(vs.length / 2) && mutN >= 1, realN, total: vs.length, mutN }
  log(`Adversarial ${id}: confirmed=${advConfirmed[id].confirmed} real=${realN}/${vs.length} mut=${mutN}`)
}

// ════════════════════════════════════════════════════════════════════════
phase('Integrate')
const allVerifyOk = verifies.filter(Boolean).every(v => v.verified)
const specialOk = special.off_path_unchanged && special.variant_key_deterministic && special.failopen_holds && special.amplify_untouched
const allAdvOk = ['T2', 'T5', 'T6'].every(id => advConfirmed[id].confirmed)
if (!allVerifyOk || !specialOk || !allAdvOk) {
  log(`PRE-INTEGRATE BLOCK: verifyOk=${allVerifyOk} specialOk=${specialOk} advOk=${allAdvOk}`)
  return { blocked: true, phase: 'pre-Integrate', verifies, special, advConfirmed }
}
const integ = await agent(
  `Integrate agent for FanOps creative variation — MUST include a REAL two-account render (the proof). Worktree "${WT}", venv .venv. All 7 tasks committed on ${BRANCH}.
1. FULL suite: \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` -> suite_count (expect 338 + new). regressions: any 338-baseline test now red? (expect none).
2. ruff: \`${VENVRUN} ruff check src/ 2>&1 | tail -1\` -> ruff_clean.
3. REAL RENDER (unmocked ffmpeg-full): synthesize a base clip \`ffmpeg -f lavfi -i color=c=navy:s=720x1280:d=4 -f lavfi -i sine=frequency=300:d=4 -shortest /tmp/var_base.mp4 -y\`; then in python call overlay.burn_hook_only twice (hooks "HOOK ALPHA" and "HOOK BETA", 720x1280) -> /tmp/var_a.mp4, /tmp/var_b.mp4. CONFIRM both exist, both DIFFER from each other AND from the base (different sizes/MD5). If tesseract is available, extract a frame from var_a at t=1s and OCR -> assert "ALPHA" present. Set real_render_ok + render_evidence (sizes/MD5/OCR).
4. TWO-ACCOUNT crosspost (variation ON): build a scratch ledger (Source+Moment+captioned Clip with per-surface hooks for 2 accounts), FANOPS_CREATIVE_VARIATION=1, run crosspost_clips with the REAL burn -> assert 2 Posts with DIFFERENT variant_key + variant_hook pointing at DIFFERENT files. Set two_account_distinct.
5. blocked=true if suite regresses, ruff fails, the real render fails, or the two accounts aren't distinct. Return ONLY structured JSON.`,
  { schema: INTEGRATE_SCHEMA, phase: 'Integrate', label: 'integrate' }
)
log(`Integrate: suite=${integ.suite_count} ruff=${integ.ruff_clean} real_render=${integ.real_render_ok} two_acct=${integ.two_account_distinct} blocked=${integ.blocked}`)
log(`  render evidence: ${integ.render_evidence}`)
if (integ.blocked || !integ.real_render_ok || !integ.two_account_distinct) {
  return { blocked: true, phase: 'Integrate', integ, impls, verifies, special, advConfirmed }
}

// ════════════════════════════════════════════════════════════════════════
phase('Close')
const close = await agent(
  `Close agent for FanOps creative variation (superpowers:finishing-a-development-branch posture). All 7 tasks committed on ${BRANCH} in "${WT}"; suite green (${integ.suite_count}); ruff clean; real two-account render proven. Work in the worktree.
DOCS (sync-docs — read then edit): RUNTIME.md §Backlog — mark (j) v1 DONE (per-account caption+hook variation, observe-only, FANOPS_CREATIVE_VARIATION default OFF, fail-open, touches none of amplify) + add FANOPS_CREATIVE_VARIATION to the env table. README.md — note the toggle + lift-by-variant. (If T7 already did the docs, verify + supplement.) docs_updated = files edited. COMMIT docs: \`cd "${WT}" && git add -A && git commit -m "docs (variation): mark backlog (j) v1 DONE; document FANOPS_CREATIVE_VARIATION + lift-by-variant\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\` (skip if T7 committed all docs + tree clean).
PUSH + PR + CI:
- \`cd "${WT}" && git push -u origin ${BRANCH} 2>&1\`. pushed.
- \`gh pr create --repo Fleezyflo/fanops --base main --head ${BRANCH} --title "Per-account creative variation (j) — observe-only v1" --body "<short: per-account caption + burned-in hook (shared base + cheap per-account overlay pass); deterministic variant_key; lift-by-variant in the digest; FANOPS_CREATIVE_VARIATION default OFF; fail-open; touches none of amplify/cascade. Real two-account render proven. Suite ${integ.suite_count}.>"\` -> pr_url.
- WATCH CI: \`gh run list --repo Fleezyflo/fanops --limit 3\` -> find the ${BRANCH} run id -> \`gh run watch <id> --repo Fleezyflo/fanops --exit-status 2>&1 | tail -20\`. Re-query on a dropped watch. ci_status = "completed success"/etc; ci_run_url = the URL. Both jobs green.
deviations + handoff are the ORCHESTRATOR's job — do NOT touch ~/.claude memory. Return ONLY structured JSON.`,
  { schema: CLOSE_SCHEMA, phase: 'Close', label: 'close' }
)
log(`Close: docs=${JSON.stringify(close.docs_updated)} pushed=${close.pushed} ci=${close.ci_status}`)

return {
  blocked: false,
  preflight: pf,
  frame: { tasks: frame.tasks.length, invariants: frame.invariants },
  impls: Object.fromEntries(Object.entries(impls).map(([k, v]) => [k, { red: v.red, green: v.green, suite_count: v.suite_count, commit_sha: v.commit_sha, commit_subject: v.commit_subject }])),
  verifies: verifies.filter(Boolean),
  special,
  adversarial: advConfirmed,
  adversarial_verdicts: verdicts.filter(Boolean),
  integrate: integ,
  close,
  worktree: WT,
  branch: BRANCH,
}
