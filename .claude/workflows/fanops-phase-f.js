export const meta = {
  name: 'fanops-phase-f',
  description: 'Phase F (operator-recovery CLI verbs F1-F3 + PII fix F4) + finale G1-G3; closes all 19 audit gaps. Deterministic TDD, independent verify, adversarial mutation proofs.',
  whenToUse: 'The LAST phase of the FanOps live-autonomous plan. Run once; resume on failure via {scriptPath, resumeFromRunId}.',
  phases: [
    { title: 'Preflight', detail: 'confirm base state, deps, cli structure, F4 target' },
    { title: 'Frame', detail: 'per-task structured impl plan against real cli.py/ingest.py' },
    { title: 'Implement', detail: 'STRICT TDD in a worktree: F1->F2->F3 sequential, then F4; commit per task' },
    { title: 'Verify', detail: 'independent re-run of each task + special checks (4 verbs in --help, unknown-id exit2, F4 meta intact)' },
    { title: 'Adversarial', detail: '>=2 skeptics per task, majority vote, mutation proofs' },
    { title: 'Integrate', detail: 'full suite + compose check + the 19-gap completeness map' },
    { title: 'Close', detail: 'G1 real-CLI gate, G2 sync-docs, G3 operator runbook, push + CI watch' },
  ],
}

// ── Shared constants ──────────────────────────────────────────────────────
const ROOT = '/Users/molhamhomsi/Moh Flow Fanops'
const WT = '/Users/molhamhomsi/Moh Flow Fanops-phase-f'        // sibling worktree path
const BRANCH = 'phase-f'
// venv activation prefix every Bash agent uses inside the worktree
const VENVRUN = `cd "${WT}" && source .venv/bin/activate &&`

// Pre-flight finding baked in for every agent: F4 ALSO must update the two
// existing assertions in tests/test_ingest.py (lines ~82, ~96) that read
// meta["original_name"] == "perf.mp4". The plan's F4 snippet omits this; if
// left untouched the suite goes RED. They should change to assert the filename
// is GONE (e.g. assert "original_name" not in meta / assert meta["bytes"]).
const F4_NOTE = `CRITICAL F4 CAVEAT (verified in preflight, NOT in the plan snippet): tests/test_ingest.py has TWO EXISTING assertions that read meta["original_name"] == "perf.mp4" (in test_skips_audio_only_drop ~line 82 and test_skips_pii ~line 96). Dropping original_name from ingest.py makes BOTH go RED. F4 MUST also update those two assertions so they no longer require original_name — change each to assert the filename is gone and/or assert on the surviving meta (e.g. meta["bytes"] / "original_name" not in meta). The new test from the plan still gets added. Keep meta={"bytes": f.stat().st_size} so downstream readers (meta["transcribed"] set by transcribe.py, meta["amplify_count"] set by adjust.py) are unaffected — those keys are added later by other modules, not by ingest.`

// ── JSON schemas (force structured outputs) ───────────────────────────────
const PREFLIGHT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['base_ok', 'head', 'baseline_count', 'no_open_prs', 'deps_present', 'missing_deps', 'f4_target_present', 'cli_structure_notes', 'meta_readers', 'stop'],
  properties: {
    base_ok: { type: 'boolean' },
    head: { type: 'string' },
    baseline_count: { type: 'string', description: 'e.g. "291 passed, 1 skipped"' },
    no_open_prs: { type: 'boolean' },
    deps_present: { type: 'boolean' },
    missing_deps: { type: 'array', items: { type: 'string' } },
    f4_target_present: { type: 'boolean', description: 'original_name still written at ingest.py' },
    cli_structure_notes: { type: 'string', description: 'where the subparser block and _dispatch chain live + how a verb is added to both' },
    meta_readers: { type: 'array', items: { type: 'string' }, description: 'downstream readers of src.meta and which key' },
    stop: { type: 'boolean', description: 'true if a plan-required dep is missing -> orchestrator halts' },
  },
}

const FRAME_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['tasks', 'invariants', 'f4_meta_safe'],
  properties: {
    tasks: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['id', 'files', 'failing_test', 'impl', 'ordering_dep', 'commit_msg'],
        properties: {
          id: { type: 'string' },
          files: { type: 'array', items: { type: 'string' } },
          failing_test: { type: 'string', description: 'the literal test function name + file' },
          impl: { type: 'string', description: 'concrete implementation against the real cli.py/ingest.py body' },
          ordering_dep: { type: 'string' },
          commit_msg: { type: 'string' },
        },
      },
    },
    invariants: { type: 'array', items: { type: 'string' } },
    f4_meta_safe: { type: 'string', description: 'confirmation that dropping original_name does not break meta["transcribed"]/meta["amplify_count"]' },
  },
}

const IMPL_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['task_id', 'red', 'red_evidence', 'green', 'green_evidence', 'suite_count', 'files', 'commit_sha', 'commit_subject'],
  properties: {
    task_id: { type: 'string' },
    red: { type: 'boolean', description: 'the new test was watched FAILING before impl' },
    red_evidence: { type: 'string', description: 'the failing pytest line / assertion error' },
    green: { type: 'boolean' },
    green_evidence: { type: 'string', description: 'the passing pytest summary line' },
    suite_count: { type: 'string', description: 'full-suite count after this task, e.g. "293 passed, 1 skipped"' },
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
    count: { type: 'string', description: 'quoted full-suite count this verifier observed' },
    cli_help_lists_all_four: { type: 'boolean', description: 'F-suite only: --help lists resolve/unhold/retry-source/retry-metrics' },
    unknown_id_exit2: { type: 'boolean', description: 'this task verb on an unknown id exits 2 (not a traceback)' },
    f4_meta_intact: { type: 'boolean', description: 'F4 only: filename nowhere in serialized source AND meta["transcribed"]/amplify_count still work' },
    notes: { type: 'string' },
  },
}

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['task_id', 'skeptic', 'real', 'mutation_proven', 'mutation_evidence', 'any_bypass', 'notes'],
  properties: {
    task_id: { type: 'string' },
    skeptic: { type: 'string', description: 'which lens this skeptic took' },
    real: { type: 'boolean', description: 'true = the implementation genuinely satisfies the contract (NOT refuted)' },
    mutation_proven: { type: 'boolean', description: 'reverted the guard/injected a mutation -> the new test FAILED -> restored' },
    mutation_evidence: { type: 'string', description: 'the failing line under mutation + confirmation of restore (git diff clean)' },
    any_bypass: { type: 'string', description: 'any way the verb misbehaves (empty string if none)' },
    notes: { type: 'string' },
  },
}

const INTEGRATE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['suite_count', 'four_verbs_compose', 'real_cli_status_exit0', 'real_cli_help_four', 'gap_map', 'all_gaps_closed', 'regressions', 'blocked', 'evidence'],
  properties: {
    suite_count: { type: 'string' },
    four_verbs_compose: { type: 'boolean' },
    real_cli_status_exit0: { type: 'boolean' },
    real_cli_help_four: { type: 'boolean' },
    gap_map: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['gap', 'task', 'landed_on_main_or_branch', 'evidence'],
        properties: {
          gap: { type: 'string' },
          task: { type: 'string' },
          landed_on_main_or_branch: { type: 'boolean' },
          evidence: { type: 'string' },
        },
      },
    },
    all_gaps_closed: { type: 'boolean' },
    regressions: { type: 'string' },
    blocked: { type: 'boolean' },
    evidence: { type: 'string' },
  },
}

const CLOSE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['g1_done', 'g2_done', 'g3_done', 'pushed', 'ci_status', 'ci_run_url', 'docs_updated', 'deviations_recorded', 'handoff_written', 'notes'],
  properties: {
    g1_done: { type: 'boolean', description: 'full suite green + real-CLI status exit0 + --help lists four verbs' },
    g2_done: { type: 'boolean', description: 'README + RUNTIME synced, backlog items removed' },
    g3_done: { type: 'boolean', description: 'operator runbook written in RUNTIME.md' },
    pushed: { type: 'boolean' },
    ci_status: { type: 'string', description: 'completed success | failure | other' },
    ci_run_url: { type: 'string' },
    docs_updated: { type: 'array', items: { type: 'string' } },
    deviations_recorded: { type: 'boolean' },
    handoff_written: { type: 'boolean' },
    notes: { type: 'string' },
  },
}

// ════════════════════════════════════════════════════════════════════════
// PHASE 1 — PREFLIGHT
// ════════════════════════════════════════════════════════════════════════
phase('Preflight')
const pf = await agent(
  `You are the Preflight agent for FanOps Phase F. Work in the MAIN repo at "${ROOT}" (read-only — do NOT create the worktree yet, do NOT edit anything).

Confirm and report (structured JSON):
1. base_ok / head: \`cd "${ROOT}" && git rev-parse HEAD\` — expect 0bf4c287b95f8f39ccdf00568e8114934f41ca5d on branch main, clean tree (\`git status --porcelain\` empty).
2. baseline_count: \`cd "${ROOT}" && source .venv/bin/activate && python -m pytest -q 2>&1 | tail -3\` — expect "291 passed, 1 skipped".
3. no_open_prs: \`gh pr list --repo Fleezyflo/fanops --state open\` — expect empty.
4. deps_present / missing_deps: Read src/fanops/models.py and confirm these enum members + fields EXIST: PostState.{published,failed,needs_reconcile}; ClipState.{held,captions_requested}; SourceState.{error,catalogued}; Clip.held, Clip.held_reason; Source.error_reason, Source.meta; Post.public_url; and Ledger.transaction exists in src/fanops/ledger.py. List any that are MISSING.
5. f4_target_present: \`grep -n original_name "${ROOT}/src/fanops/ingest.py"\` — confirm meta={"original_name": f.name, ...} is still written (expect line ~92).
6. cli_structure_notes: Read src/fanops/cli.py. Describe EXACTLY where the subparser block is (the \`sub.add_parser(...)\` lines, ~114-122) and where the _dispatch if-chain is (~184-253), and the pattern for adding a verb to BOTH (subparser with add_argument + an \`if args.cmd == "...":\` branch using \`with Ledger.transaction(cfg) as led:\`).
7. meta_readers: report which modules read src.meta and which key: confirm transcribe.py sets meta["transcribed"], adjust.py reads/sets meta["amplify_count"]. (These keys are added by OTHER modules, not ingest — so dropping original_name from ingest's add_source does NOT remove them.)

${F4_NOTE}

stop: set true ONLY if a plan-required dep from item 4 is genuinely MISSING (then the orchestrator halts). Otherwise false.

Return ONLY the structured JSON.`,
  { schema: PREFLIGHT_SCHEMA, phase: 'Preflight', label: 'preflight' }
)

log(`Preflight: base_ok=${pf.base_ok} baseline=${pf.baseline_count} no_open_prs=${pf.no_open_prs} deps_present=${pf.deps_present} f4_target=${pf.f4_target_present} stop=${pf.stop}`)
if (pf.stop || !pf.base_ok || !pf.deps_present || !pf.f4_target_present) {
  log(`PREFLIGHT BLOCKED: missing_deps=${JSON.stringify(pf.missing_deps)} — halting.`)
  return { blocked: true, phase: 'Preflight', preflight: pf }
}

// ════════════════════════════════════════════════════════════════════════
// PHASE 2 — FRAME
// ════════════════════════════════════════════════════════════════════════
phase('Frame')
const frame = await agent(
  `You are the Frame agent for FanOps Phase F. Read (read-only, MAIN repo "${ROOT}"): the plan section docs/superpowers/plans/2026-06-01-fanops-live-autonomous.md lines 1748-2007 (Phase F tasks F1/F2/F3/F4 — each has a LITERAL test + minimal impl), the CURRENT body of src/fanops/cli.py and src/fanops/ingest.py, src/fanops/transcribe.py (the meta["transcribed"] reader), src/fanops/adjust.py (the meta["amplify_count"] reader).

Produce a per-task structured plan for F1, F2, F3, F4. For each task give: id; files (exact paths); failing_test (the literal test function name + file from the plan); impl (the concrete minimal code to make it pass, written against the REAL current cli.py/ingest.py body you just read — quote the exact subparser line and the exact _dispatch branch); ordering_dep (F1 first, F2 after F1, F3 after F2 — all three edit cli.py + tests/test_cli.py; F4 is disjoint, edits only ingest.py + tests/test_ingest.py); commit_msg (the EXACT message from the plan).

invariants (list): every verb wraps its mutation in \`with Ledger.transaction(cfg) as led:\` (Phase B lock-safe, consistent with the B-followup transaction-wrapped write commands); an unknown id prints to stderr and returns 2 (never a crash/traceback); resolve's --url only sets public_url; F1 resolve does EXACTLY what the human asks (no auto-requeue of a maybe-live post); F2 unhold resets BOTH held=False AND state=captions_requested; F3 retry-source sets meta["transcribed"]=False to force a REAL re-transcribe; F3 retry-metrics exits 0 for published / exit 2 otherwise.

f4_meta_safe: confirm in prose that dropping original_name and keeping meta={"bytes": ...} does NOT break meta["transcribed"] (set by transcribe.py AFTER ingest) or meta["amplify_count"] (set by adjust.py AFTER ingest) — those keys are written by downstream modules, not by ingest's add_source.

${F4_NOTE}

Return ONLY the structured JSON.`,
  { schema: FRAME_SCHEMA, phase: 'Frame', label: 'frame' }
)
log(`Frame: ${frame.tasks.length} tasks planned; invariants=${frame.invariants.length}; f4_meta_safe noted.`)

// ════════════════════════════════════════════════════════════════════════
// PHASE 3 — IMPLEMENT (one worktree off main; F1->F2->F3 sequential, then F4)
// NOTE: the plan calls F4 "parallel" because it is DISJOINT from F1-F3 (different
// files, no dependency). But all four implementers write to the SAME git worktree;
// concurrent git/pytest in one tree would race. So we honor F4's INDEPENDENCE
// (it doesn't depend on F1-F3's result) while keeping git-safe SEQUENCING:
// F1->F2->F3->F4 as sequential awaits. F4 could equally run first; order is free.
// Each task is STRICT TDD: literal RED, minimal impl, literal GREEN, full suite, commit.
// ════════════════════════════════════════════════════════════════════════
phase('Implement')

// One-time worktree + venv setup, done by the F1 implementer as step 0.
const SETUP = `STEP 0 — WORKTREE + VENV (do this ONCE, you are the first implementer):
- From the main repo: \`cd "${ROOT}" && git worktree add "${WT}" -b ${BRANCH} main\` (if the path already exists from a resume, \`cd "${WT}" && git status\` instead and skip creation).
- Create the worktree's OWN editable venv with python3.12 (NOT 3.14): \`cd "${WT}" && python3.12 -m venv .venv && source .venv/bin/activate && pip install -q -e ".[dev]"\`. The [dev] extra MUST pull pytest-timeout (the B-followup added it; without it pytest warns "Unknown config option: timeout"). Verify: \`pip show pytest-timeout | head -2\`.
- Confirm baseline in the worktree: \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` -> expect "291 passed, 1 skipped".
`

const f1 = await agent(
  `You are the F1 implementer for FanOps Phase F, working in a git worktree. ${SETUP}

TASK F1 — \`fanops resolve <post_id> <published|failed> [--url URL]\` (audit H1's missing human-reconcile path).
Plan reference: docs/superpowers/plans/2026-06-01-fanops-live-autonomous.md F1 (~lines 1752-1815).
Files: src/fanops/cli.py (subparser + _dispatch branch), tests/test_cli.py (add test).

STRICT TDD:
1. RED — add this EXACT test to tests/test_cli.py:
\`\`\`python
def test_resolve_promotes_a_needs_reconcile_post(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Post, PostState, Platform
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.needs_reconcile, submission_id="fanops_t"))
    from fanops.cli import main
    assert main(["resolve", "p1", "published", "--url", "https://x/p"]) == 0
    led = Ledger.load(cfg)
    assert led.posts["p1"].state is PostState.published and led.posts["p1"].public_url == "https://x/p"
\`\`\`
   Run \`${VENVRUN} python -m pytest tests/test_cli.py::test_resolve_promotes_a_needs_reconcile_post -v 2>&1 | tail -15\` and CONFIRM it FAILS (no resolve subcommand -> argparse SystemExit / error). Capture the failing line as red_evidence.

2. IMPL — add the subparser to the sub.add_parser block in main() (near the other parsers, ~line 121):
\`\`\`python
    p_res = sub.add_parser("resolve"); p_res.add_argument("post_id")
    p_res.add_argument("status", choices=["published", "failed"]); p_res.add_argument("--url", default=None)
\`\`\`
   and add to _dispatch (with the other \`if args.cmd ==\` branches):
\`\`\`python
    if args.cmd == "resolve":
        from fanops.models import PostState
        with Ledger.transaction(cfg) as led:
            if args.post_id not in led.posts:
                print(f"no such post: {args.post_id}", file=sys.stderr); return 2
            p = led.posts[args.post_id]
            p.state = PostState.published if args.status == "published" else PostState.failed
            if args.url: p.public_url = args.url
        print(f"resolved {args.post_id} -> {args.status}"); return 0
\`\`\`

3. GREEN — \`${VENVRUN} python -m pytest tests/test_cli.py::test_resolve_promotes_a_needs_reconcile_post -v 2>&1 | tail -10\` PASSES (green_evidence). Then FULL suite \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` -> expect "292 passed, 1 skipped" (suite_count).

4. COMMIT — \`cd "${WT}" && git add src/fanops/cli.py tests/test_cli.py && git commit -m "feat (audit H1): fanops resolve <post_id> — the documented human-reconcile path now exists

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`. Capture commit_sha (\`git rev-parse HEAD\`) + commit_subject.

Do NOT touch ingest.py or test_ingest.py. Return ONLY the structured JSON.`,
  { schema: IMPL_SCHEMA, phase: 'Implement', label: 'impl:F1' }
)
log(`F1: red=${f1.red} green=${f1.green} suite=${f1.suite_count} sha=${(f1.commit_sha||'').slice(0,8)}`)

const f2 = await agent(
  `You are the F2 implementer for FanOps Phase F. The worktree "${WT}" already exists with its venv (F1 set it up and committed). Pull latest state: \`cd "${WT}" && git log --oneline -3\` should show F1's resolve commit on top of main.

TASK F2 — \`fanops unhold <clip_id>\` (RUNTIME backlog (f)): clear a brand-risk hold WITHOUT editing ledger.json.
Plan reference: F2 (~lines 1817-1878). Files: src/fanops/cli.py, tests/test_cli.py.

STRICT TDD:
1. RED — add this EXACT test to tests/test_cli.py:
\`\`\`python
def test_unhold_resets_a_held_clip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Clip, ClipState
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="c1", parent_id="m1", path="/c.mp4", state=ClipState.held, held=True,
                          held_reason="brand risk"))
    from fanops.cli import main
    assert main(["unhold", "c1"]) == 0
    c = Ledger.load(cfg).clips["c1"]
    assert c.state is ClipState.captions_requested and c.held is False
\`\`\`
   \`${VENVRUN} python -m pytest tests/test_cli.py::test_unhold_resets_a_held_clip -v 2>&1 | tail -15\` -> CONFIRM FAILS (no unhold). red_evidence.

2. IMPL — subparser (in the add_parser block):
\`\`\`python
    p_unh = sub.add_parser("unhold"); p_unh.add_argument("clip_id")
\`\`\`
   _dispatch branch:
\`\`\`python
    if args.cmd == "unhold":
        from fanops.models import ClipState
        with Ledger.transaction(cfg) as led:
            if args.clip_id not in led.clips:
                print(f"no such clip: {args.clip_id}", file=sys.stderr); return 2
            c = led.clips[args.clip_id]; c.held = False; c.held_reason = None
            c.state = ClipState.captions_requested      # re-enter the caption gate
        print(f"unheld {args.clip_id}"); return 0
\`\`\`

3. GREEN — the new test passes, then FULL suite \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` -> expect "293 passed, 1 skipped". CRITICAL: confirm F1's test_resolve... STILL passes (the resolve subparser/dispatch must survive — run \`${VENVRUN} python -m pytest tests/test_cli.py -q 2>&1 | tail -3\` to see both).

4. COMMIT — \`cd "${WT}" && git add src/fanops/cli.py tests/test_cli.py && git commit -m "feat (audit): fanops unhold <clip_id> — clear a brand-risk hold without editing ledger.json

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`. Capture sha + subject.

Do NOT touch ingest.py/test_ingest.py. Return ONLY the structured JSON.`,
  { schema: IMPL_SCHEMA, phase: 'Implement', label: 'impl:F2' }
)
log(`F2: red=${f2.red} green=${f2.green} suite=${f2.suite_count} sha=${(f2.commit_sha||'').slice(0,8)}`)

const f3 = await agent(
  `You are the F3 implementer for FanOps Phase F. The worktree "${WT}" exists with venv; \`cd "${WT}" && git log --oneline -4\` should show F2(unhold) and F1(resolve) on top of main.

TASK F3 — \`fanops retry-source <source_id>\` + \`fanops retry-metrics <post_id>\`.
Plan reference: F3 (~lines 1880-1955). Files: src/fanops/cli.py, tests/test_cli.py.

STRICT TDD:
1. RED — add this EXACT test to tests/test_cli.py:
\`\`\`python
def test_retry_source_resets_error_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.error,
                              error_reason="toolchain missing: ffmpeg"))
    from fanops.cli import main
    assert main(["retry-source", "s1"]) == 0
    s = Ledger.load(cfg).sources["s1"]
    assert s.state is SourceState.catalogued and s.error_reason is None
\`\`\`
   \`${VENVRUN} python -m pytest tests/test_cli.py::test_retry_source_resets_error_source -v 2>&1 | tail -15\` -> CONFIRM FAILS. red_evidence.

2. IMPL — subparsers (in the add_parser block):
\`\`\`python
    p_rs = sub.add_parser("retry-source"); p_rs.add_argument("source_id")
    p_rm = sub.add_parser("retry-metrics"); p_rm.add_argument("post_id")
\`\`\`
   _dispatch branches:
\`\`\`python
    if args.cmd == "retry-source":
        from fanops.models import SourceState
        with Ledger.transaction(cfg) as led:
            if args.source_id not in led.sources:
                print(f"no such source: {args.source_id}", file=sys.stderr); return 2
            s = led.sources[args.source_id]
            s.state = SourceState.catalogued      # re-enter from the top (transcribe retries)
            s.error_reason = None
            s.meta["transcribed"] = False         # force a real re-transcribe
        print(f"retry-source {args.source_id}"); return 0
    if args.cmd == "retry-metrics":
        from fanops.models import PostState
        with Ledger.transaction(cfg) as led:
            if args.post_id not in led.posts:
                print(f"no such post: {args.post_id}", file=sys.stderr); return 2
            p = led.posts[args.post_id]
            if p.state is PostState.published:    # leave it published so the next track pass re-pulls
                print(f"retry-metrics {args.post_id}: will re-pull on next track"); return 0
            print(f"retry-metrics {args.post_id}: not published (state={p.state.value})", file=sys.stderr); return 2
\`\`\`

3. GREEN — the new test passes, then FULL suite \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` -> expect "294 passed, 1 skipped". CRITICAL: confirm BOTH F1 and F2 tests still pass and \`${VENVRUN} python -m fanops.cli --help 2>&1\` now lists resolve, unhold, retry-source, retry-metrics (no clobber). Quote that.

4. COMMIT — \`cd "${WT}" && git add src/fanops/cli.py tests/test_cli.py && git commit -m "feat (audit): fanops retry-source / retry-metrics recovery verbs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`. sha + subject.

Do NOT touch ingest.py/test_ingest.py. Return ONLY the structured JSON.`,
  { schema: IMPL_SCHEMA, phase: 'Implement', label: 'impl:F3' }
)
log(`F3: red=${f3.red} green=${f3.green} suite=${f3.suite_count} sha=${(f3.commit_sha||'').slice(0,8)}`)

// F4 — disjoint from F1-F3 (only ingest.py + test_ingest.py). Runs after F3 to keep
// git-safe single-tree sequencing; its result does not depend on F1-F3.
const f4 = await agent(
  `You are the F4 implementer for FanOps Phase F. The worktree "${WT}" exists with venv; F1/F2/F3 are committed. You touch ONLY src/fanops/ingest.py and tests/test_ingest.py (disjoint from the cli verbs).

TASK F4 — stop persisting meta.original_name (PII residue, C3 sibling). The SHA id IS identity (per ingest's docstring); the operator's private filename must not be written to the ledger.
Plan reference: F4 (~lines 1957-2007). Files: src/fanops/ingest.py (~line 92), tests/test_ingest.py.

${F4_NOTE}

STRICT TDD:
1. RED — add this EXACT new test to tests/test_ingest.py (note it needs \`import json\` at the top of the file if not present, and uses the existing _put + Config + Ledger imports):
\`\`\`python
def test_ingest_does_not_persist_original_filename(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put(cfg.inbox / "MY-PRIVATE-NAME.mp4", b"V")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1920, 1080, 12.0))
    led = ingest_drops(Ledger.load(cfg), cfg)
    s = next(iter(led.sources.values()))
    assert "original_name" not in s.meta
    assert "MY-PRIVATE-NAME" not in json.dumps(s.model_dump())   # the filename is nowhere in the unit
\`\`\`
   Add \`import json\` near the top of tests/test_ingest.py if absent. Run \`${VENVRUN} python -m pytest tests/test_ingest.py::test_ingest_does_not_persist_original_filename -v 2>&1 | tail -15\` -> CONFIRM FAILS (original_name IS in meta today). red_evidence.

   ALSO: run \`${VENVRUN} python -m pytest tests/test_ingest.py -q 2>&1 | tail -6\` and observe that test_skips_audio_only_drop and test_skips_pii currently PASS (they assert meta["original_name"]=="perf.mp4") — these are the two you must update in step 2.

2. IMPL — in src/fanops/ingest.py, change the add_source meta to drop original_name (keep only non-identifying meta):
\`\`\`python
            led.add_source(Source(id=sid, state=SourceState.catalogued, source_path=str(dest),
                                  source_origin=origin, sha256=digest, width=w, height=h,
                                  duration=dur or None,
                                  meta={"bytes": f.stat().st_size}))   # AUDIT: no original_name (PII)
\`\`\`
   THEN update the two existing assertions that now break:
   - test_skips_audio_only_drop (~line 82): replace \`assert next(iter(led.sources.values())).meta["original_name"] == "perf.mp4"\` with an assertion that does NOT require original_name. The test's real intent is "the .wav was skipped and the .mp4 was catalogued" — so assert on the surviving identity, e.g. \`assert "original_name" not in next(iter(led.sources.values())).meta\` (the count==1 assertion above it already proves the right file was kept). Keep the count check.
   - test_skips_pii (~line 96): same change — replace the \`meta["original_name"] == "perf.mp4"\` assertion with \`assert "original_name" not in next(iter(led.sources.values())).meta\`.
   (Both tests already assert len(led.sources)==1, which is the load-bearing "PII/audio file was excluded, the real one catalogued" check; the original_name assertion was incidental and is exactly the PII we're removing.)

3. GREEN — \`${VENVRUN} python -m pytest tests/test_ingest.py -q 2>&1 | tail -4\` ALL green (incl. the new test + the two updated ones). Then FULL suite \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` -> expect "295 passed, 1 skipped" (the new ingest test is +1 over F3's 294). Confirm meta["transcribed"]/meta["amplify_count"] paths are untouched (grep that ingest.py no longer sets original_name but downstream modules still set those keys: \`grep -rn 'transcribed\\|amplify_count\\|original_name' src/fanops/\`).

4. COMMIT — \`cd "${WT}" && git add src/fanops/ingest.py tests/test_ingest.py && git commit -m "fix (audit C3-sibling): stop persisting original filename in the ledger (PII; sha is identity)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`. sha + subject.

Return ONLY the structured JSON.`,
  { schema: IMPL_SCHEMA, phase: 'Implement', label: 'impl:F4' }
)
log(`F4: red=${f4.red} green=${f4.green} suite=${f4.suite_count} sha=${(f4.commit_sha||'').slice(0,8)}`)

const impls = { F1: f1, F2: f2, F3: f3, F4: f4 }
const allImplGreen = [f1, f2, f3, f4].every(r => r && r.red && r.green && r.commit_sha)
if (!allImplGreen) {
  log(`IMPLEMENT BLOCKED: not all tasks RED->GREEN->committed. ${JSON.stringify(Object.fromEntries(Object.entries(impls).map(([k,v])=>[k,{red:v?.red,green:v?.green,sha:(v?.commit_sha||'').slice(0,8)}])))}`)
  return { blocked: true, phase: 'Implement', impls }
}

// ════════════════════════════════════════════════════════════════════════
// PHASE 4 — VERIFY (a DIFFERENT agent than each implementer, independent re-run)
// ════════════════════════════════════════════════════════════════════════
phase('Verify')
const FVERBS = ['F1', 'F2', 'F3']
const verifyTasks = [
  { id: 'F1', test: 'tests/test_cli.py::test_resolve_promotes_a_needs_reconcile_post', verb: 'resolve', unknownArgs: 'resolve NOPE published' },
  { id: 'F2', test: 'tests/test_cli.py::test_unhold_resets_a_held_clip', verb: 'unhold', unknownArgs: 'unhold NOPE' },
  { id: 'F3', test: 'tests/test_cli.py::test_retry_source_resets_error_source', verb: 'retry-source', unknownArgs: 'retry-source NOPE' },
  { id: 'F4', test: 'tests/test_ingest.py::test_ingest_does_not_persist_original_filename', verb: null, unknownArgs: null },
]
const verifies = await parallel(verifyTasks.map(t => () =>
  agent(
    `You are an INDEPENDENT Verify agent for FanOps Phase F task ${t.id} (you did NOT implement it). Worktree "${WT}", venv at .venv. Re-run from scratch and report structured JSON.

1. \`${VENVRUN} python -m pytest ${t.test} -v 2>&1 | tail -10\` — must PASS.
2. FULL suite: \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` — QUOTE the exact count into \`count\` (expect "295 passed, 1 skipped" once all four tasks are committed).
${t.id !== 'F4' ? `3. SPECIAL — all four verbs in --help: \`${VENVRUN} python -m fanops.cli --help 2>&1\` — set cli_help_lists_all_four=true ONLY if ALL of resolve, unhold, retry-source, retry-metrics appear (proves the subparsers didn't clobber each other across F1/F2/F3).
4. SPECIAL — unknown-id exit 2: run, from a SCRATCH temp dir (Config resolves from cwd, so use a fresh empty dir to avoid touching a real ledger — e.g. \`cd "${WT}" && source .venv/bin/activate && cd $(mktemp -d) && python -m fanops.cli ${t.unknownArgs}; echo "EXIT=$?"\`). set unknown_id_exit2=true ONLY if it printed a clean "no such ..." line to stderr and EXIT=2 (NOT a traceback, NOT exit 1). Quote the output.` : `3. SPECIAL F4 — filename gone: confirm \`grep -rn original_name "${WT}/src/fanops/ingest.py"\` returns NOTHING (the meta no longer writes it). Confirm \`grep -rn original_name "${WT}/tests/test_ingest.py"\` shows the assertions were updated (no \`meta["original_name"] == "perf.mp4"\` remains, only "not in" forms or the new test).
4. SPECIAL F4 — meta intact: confirm meta["transcribed"] is still set by transcribe.py and meta["amplify_count"] by adjust.py (\`grep -rn 'transcribed\\|amplify_count' "${WT}/src/fanops/transcribe.py" "${WT}/src/fanops/adjust.py"\`). Run the transcribe + adjust tests to prove F4 didn't break them: \`${VENVRUN} python -m pytest tests/test_transcribe.py tests/test_adjust.py -q 2>&1 | tail -4\`. set f4_meta_intact=true ONLY if those pass AND the filename is nowhere in a serialized source (the new test already asserts \`"MY-PRIVATE-NAME" not in json.dumps(s.model_dump())\` — confirm it passes).`}

Set verified=true ONLY if the task test passes AND its special checks hold. Put any anomaly in notes. Return ONLY the structured JSON.`,
    { schema: VERIFY_SCHEMA, phase: 'Verify', label: `verify:${t.id}` }
  )
))
for (const v of verifies.filter(Boolean)) {
  log(`Verify ${v.task_id}: verified=${v.verified} count="${v.count}" help4=${v.cli_help_lists_all_four ?? 'n/a'} unkExit2=${v.unknown_id_exit2 ?? 'n/a'} f4meta=${v.f4_meta_intact ?? 'n/a'}`)
}

// ════════════════════════════════════════════════════════════════════════
// PHASE 5 — ADVERSARIAL (>=2 independent skeptics/task, majority vote, mutation proof)
// ════════════════════════════════════════════════════════════════════════
phase('Adversarial')
const advSpecs = {
  F1: [
    `Skeptic A (transaction + url): Prove resolve writes INSIDE \`with Ledger.transaction(cfg)\` (lock-safe). Prove --url ONLY sets public_url and ONLY the human-chosen status is applied (published->published, failed->failed) — the verb does EXACTLY what's asked, never auto-requeues a maybe-live post. MUTATION PROOF: temporarily move the mutation OUTSIDE the transaction (or break the choices) in cli.py, confirm test_resolve... FAILS or the state isn't persisted, then \`git checkout src/fanops/cli.py\` to restore (throwaway, NEVER commit; confirm git diff clean after).`,
    `Skeptic B (unknown id): Prove an unknown post_id prints "no such post" to stderr and returns 2, NOT a traceback/exit 1. MUTATION PROOF: temporarily delete the \`if args.post_id not in led.posts: ... return 2\` guard, run \`fanops resolve NOPE published\` from a scratch dir and confirm it now CRASHES (KeyError traceback) instead of clean exit 2, then \`git checkout\` to restore. Capture both states.`,
  ],
  F2: [
    `Skeptic A (full reset): Prove unhold resets BOTH held=False AND state=ClipState.captions_requested (a half-reset would wedge the clip — held cleared but stuck out of the caption gate, or in-gate but still flagged held). MUTATION PROOF: temporarily drop the \`c.state = ClipState.captions_requested\` line (leave only held=False), confirm test_unhold... FAILS on the state assertion, then \`git checkout\` to restore.`,
    `Skeptic B (unknown id + transaction): Prove unknown clip_id -> stderr + exit 2 (no crash), and the mutation is inside Ledger.transaction. MUTATION PROOF: delete the not-in-clips guard, run \`fanops unhold NOPE\` from a scratch dir, confirm KeyError crash instead of exit 2, then restore.`,
  ],
  F3: [
    `Skeptic A (real re-transcribe): Prove retry-source forces a REAL re-transcribe by setting meta["transcribed"]=False (NOT just flipping state — transcribe.py:82 skips if meta.get("transcribed") is True, so a state-only reset would re-enter but SKIP transcribe, leaving a stale/empty transcript). MUTATION PROOF: temporarily drop the \`s.meta["transcribed"] = False\` line, and add a quick assertion to a throwaway run (or reason from transcribe.py:82) that without it a previously-transcribed source would skip — minimally, confirm test still passes WITHOUT it (showing the plan test doesn't pin it) then ADD a stronger throwaway check: set meta["transcribed"]=True before retry-source and assert it's False after; confirm THAT fails when the line is dropped. Restore via \`git checkout\` (throwaway never committed).`,
    `Skeptic B (retry-metrics published vs not + unknown id): Prove retry-metrics returns 0 for a PUBLISHED post (leaves it published so next track re-pulls) and exit 2 with the state for a not-published post; unknown post_id -> exit 2. MUTATION PROOF: temporarily invert the \`if p.state is PostState.published\` condition, confirm a published post now wrongly exits 2 (or a non-published exits 0), then \`git checkout\` to restore. Test with scratch-dir CLI runs creating a published vs queued post.`,
  ],
  F4: [
    `Skeptic A (filename truly gone everywhere): Prove the operator's filename is NOWHERE in the serialized source unit — not just absent from meta.original_name, but the new test's \`"MY-PRIVATE-NAME" not in json.dumps(s.model_dump())\` genuinely holds. Check source_path too: source_path is \`str(dest)\` = the SHA-named copy (\`{sid}{suffix}\`), NOT the original name — confirm by reading ingest.py that dest uses make_id/sid, so the original basename never lands in source_path. MUTATION PROOF: temporarily re-add original_name to the meta dict in ingest.py, confirm test_ingest_does_not_persist_original_filename FAILS, then \`git checkout\` to restore.`,
    `Skeptic B (downstream meta readers intact): Prove dropping original_name did NOT break any reader of src.meta. Confirm meta["transcribed"] (transcribe.py) and meta["amplify_count"] (adjust.py) are set by THOSE modules after ingest, so they're independent of ingest's meta dict. Run \`${VENVRUN} python -m pytest tests/test_transcribe.py tests/test_adjust.py tests/test_ingest.py -q 2>&1 | tail -4\` and confirm green. MUTATION PROOF: this is a negative — confirm that with original_name dropped, the transcribe+adjust suites STILL pass (they would pass regardless, proving independence); note in mutation_evidence that no reader depends on original_name (grep proof: \`grep -rn original_name "${WT}/src/fanops/"\` returns nothing).`,
  ],
}
const advTasks = []
for (const [id, prompts] of Object.entries(advSpecs)) {
  for (let i = 0; i < prompts.length; i++) advTasks.push({ id, idx: i, prompt: prompts[i] })
}
const verdicts = await parallel(advTasks.map(t => () =>
  agent(
    `You are an INDEPENDENT adversarial skeptic for FanOps Phase F task ${t.id} (you did NOT implement or verify it). Your job is to REFUTE — default to real=false if you cannot positively confirm. Worktree "${WT}", venv at .venv. After ANY mutation you MUST \`cd "${WT}" && git checkout <file>\` to restore and confirm \`git diff\` (and \`git status --porcelain\`) is CLEAN before returning — a mutation must NEVER be committed.

${t.prompt}

Set real=true ONLY if the implementation genuinely satisfies the contract. Set mutation_proven=true ONLY if you actually reverted the guard / injected the mutation, watched the relevant test FAIL (capture the failing line in mutation_evidence), and then restored to a clean tree. any_bypass = any way the verb misbehaves (empty string if none). Return ONLY the structured JSON.`,
    { schema: VERDICT_SCHEMA, phase: 'Adversarial', label: `adv:${t.id}#${t.idx}` }
  )
))
// majority vote per task
const advByTask = {}
for (const v of verdicts.filter(Boolean)) {
  (advByTask[v.task_id] ||= []).push(v)
}
const advConfirmed = {}
for (const id of ['F1', 'F2', 'F3', 'F4']) {
  const vs = advByTask[id] || []
  const realCount = vs.filter(v => v.real).length
  const mutCount = vs.filter(v => v.mutation_proven).length
  const confirmed = vs.length > 0 && realCount >= Math.ceil(vs.length / 2) && mutCount >= 1
  advConfirmed[id] = { confirmed, realCount, total: vs.length, mutCount, bypasses: vs.map(v => v.any_bypass).filter(Boolean) }
  log(`Adversarial ${id}: confirmed=${confirmed} real=${realCount}/${vs.length} mutation_proven=${mutCount} bypasses=${JSON.stringify(advConfirmed[id].bypasses)}`)
}

// ════════════════════════════════════════════════════════════════════════
// PHASE 6 — INTEGRATE + COMPLETENESS (true barrier; one agent)
// ════════════════════════════════════════════════════════════════════════
phase('Integrate')
const allAdvOk = ['F1', 'F2', 'F3', 'F4'].every(id => advConfirmed[id].confirmed)
const allVerifyOk = verifies.filter(Boolean).every(v => v.verified)
if (!allAdvOk || !allVerifyOk) {
  log(`PRE-INTEGRATE BLOCK: verifyOk=${allVerifyOk} advOk=${allAdvOk}. Halting before Integrate.`)
  return { blocked: true, phase: 'pre-Integrate', verifies, advConfirmed }
}
const integ = await agent(
  `You are the Integrate + completeness agent for FanOps Phase F — this is the LAST phase of the entire live-autonomous plan, so you must PROVE the plan is DONE. Worktree "${WT}", venv at .venv. All four tasks (F1 resolve, F2 unhold, F3 retry-source/retry-metrics, F4 PII drop) are committed on branch ${BRANCH}.

1. FULL suite (unit + integration markers): \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` -> suite_count (expect "295 passed, 1 skipped" — baseline 291 + 4 new tests: F1,F2,F3 each +1 in test_cli.py, F4 +1 in test_ingest.py). regressions: note ANY test that was green at baseline and is now red (expect none).
2. Compose check: \`${VENVRUN} python -m fanops.cli --help 2>&1\` lists ALL of resolve, unhold, retry-source, retry-metrics -> four_verbs_compose. Confirm no dispatch collision (each verb actually dispatches — spot-check by reading _dispatch has all four branches).
3. Real-CLI smoke (Config resolves from cwd — use a scratch dir to avoid a real ledger): \`cd "${WT}" && source .venv/bin/activate && cd $(mktemp -d) && python -m fanops.cli status; echo "EXIT=$?"\` -> real_cli_status_exit0 (EXIT=0). And \`python -m fanops.cli --help\` from there lists the four verbs -> real_cli_help_four.

4. THE 19-GAP COMPLETENESS MAP (the plan's Self-Review at lines ~2030-2037 is the authoritative mapping). For EACH gap, confirm the closing task's commit is on main OR on this ${BRANCH} branch (which will merge to main). Use \`cd "${WT}" && git log --oneline main\` for already-merged phases and \`git log --oneline ${BRANCH} -8\` for this phase's F1-F4. Build gap_map with one entry per gap:
   - Tier 0: #1->A1-A3 (claude -p responder), #2->E1 (learning loop in run), #3/B2->D2 (robust submission-id extraction), #4/B3->D3 (MCP auth), #5/B4->B1-B2 (ledger transaction), #6/B5->E2 (heartbeat/dead-man's-switch)
   - Tier 1: H2->A3, H1->D1+F1 (client token + resolve verb), H4->C1 (NaN/Inf validate), H5->C2 (caption language), H6->C3 (caption surface), N1->A3
   - Tier 2: resolve->F1, unhold->F2, retry-metrics->F3, retry-source->F3
   - Tier 3: reconcile-logging->E4, responder-failure-digest->E3, jitter->D4, live-checkpoints->D5
   - Plus: C3-sibling PII (original_name)->F4, M1->B3, M2->subsumed by B1
   For Tiers 0/1/3 and M1/M2, these landed in earlier merged phases (A-E) — confirm by finding a representative commit on main (e.g. grep \`git log --oneline main\` for the phase's feature commits; you do NOT need to re-verify their tests, just that the commit exists on main). For Tier 2 + C3-sibling (F1-F4), confirm the four NEW commits exist on ${BRANCH}. Set landed_on_main_or_branch + a one-line evidence (the commit sha/subject) for each.
   Set all_gaps_closed=true ONLY if every gap maps to a real landed commit. Report any gap you canNOT map in evidence.

5. blocked=true if the suite regresses, any task is unconfirmed, or any gap is unmapped. Otherwise false.

Return ONLY the structured JSON.`,
  { schema: INTEGRATE_SCHEMA, phase: 'Integrate', label: 'integrate' }
)
log(`Integrate: suite=${integ.suite_count} four_verbs=${integ.four_verbs_compose} status_exit0=${integ.real_cli_status_exit0} all_gaps_closed=${integ.all_gaps_closed} blocked=${integ.blocked}`)
log(`19-gap map: ${integ.gap_map.length} gaps, ${integ.gap_map.filter(g => g.landed_on_main_or_branch).length} landed.`)
if (integ.blocked || !integ.all_gaps_closed) {
  log(`INTEGRATE BLOCKED: regressions=${integ.regressions} unmapped=${JSON.stringify(integ.gap_map.filter(g => !g.landed_on_main_or_branch))}`)
  return { blocked: true, phase: 'Integrate', integ, impls, verifies, advConfirmed }
}

// ════════════════════════════════════════════════════════════════════════
// PHASE 7 — CLOSE (G1 + G2 + G3; push; CI watch; handoff)
// ════════════════════════════════════════════════════════════════════════
phase('Close')
const close = await agent(
  `You are the Close agent for FanOps Phase F — the plan's FINALE (G1+G2+G3). All four F tasks are committed on branch ${BRANCH} in worktree "${WT}"; the full suite is green (${integ.suite_count}); the 19-gap completeness check PASSED (all gaps mapped to landed commits). Adopt the \`superpowers:finishing-a-development-branch\` posture. Work in the worktree.

G1 — full verification + real-CLI gate:
- \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` green.
- Real-CLI (scratch cwd): \`cd "${WT}" && source .venv/bin/activate && cd $(mktemp -d) && python -m fanops.cli status; echo EXIT=$?\` -> exit 0; \`python -m fanops.cli --help\` lists resolve, unhold, retry-source, retry-metrics. Set g1_done.

G2 — sync-docs (do it MANUALLY here — read then edit):
- Read README.md and MohFlow-FanOps/00_control/RUNTIME.md.
- README.md: add the four new recovery commands (resolve/unhold/retry-source/retry-metrics) to the command list with one-line descriptions, and ensure the live-cutover gate is mentioned. If README already lists the LLM responder via \`claude -p\` and the live gate, leave those.
- RUNTIME.md: document the recovery verbs; the dead-man's-switch monitor hook (the heartbeat's published_in_run==0 over N runs); that \`run\` now closes the learning loop (E1). REMOVE the now-DONE backlog items: \`unhold\` (backlog (f)), jitter (backlog (c)), and the reconcile/operability gaps that F1-F3 close. Keep genuinely-open backlog (e.g. per-account creative variation C2, cover-art-audio edge (h), audio-only-skip-silent (i), externalize brand-risk lists, hardcoded artist name).
- Add docs_updated = the list of files you edited. Set g2_done.

G3 — the operator runbook (the deliverable that makes the live cutover executable). In RUNTIME.md, add a clearly-titled section "Operator runbook: fresh checkout -> first real post" with the EXACT 7-step sequence:
  (1) create the real fan accounts (human-only);
  (2) connect each account in Blotato (human-only);
  (3) paste each numeric account_id into accounts.json and set status: active;
  (4) set BLOTATO_API_KEY (sandbox first) in .env;
  (5) ensure \`claude\` is authed on the host — ANTHROPIC_API_KEY exported (NOTE: \`--bare\` ignores OAuth/keychain, so the env var is REQUIRED for the autonomous responder);
  (6) run the [GATED] D5 smoke test (the Blotato sandbox live contract check);
  (7) cron entry \`cd <root> && fanops run\` on the chosen interval, WITH a monitor alerting when the heartbeat's published_in_run == 0 over N consecutive runs (the dead-man's-switch).
  Make it concrete and copy-pasteable. Set g3_done.

COMMIT the docs: \`cd "${WT}" && git add -A && git commit -m "docs (Phase F + finale): four recovery verbs, operator runbook (fresh checkout -> first real post), dead-man's-switch monitor, learning-loop-in-run; remove done backlog (unhold/jitter/reconcile)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`.

PUSH + CI:
- \`cd "${WT}" && git push -u origin ${BRANCH} 2>&1\`. Set pushed.
- Open a PR (the human owns the merge — do NOT merge): \`gh pr create --repo Fleezyflo/fanops --base main --head ${BRANCH} --title "Phase F + finale: operator-recovery CLI verbs + PII fix + runbook (closes all 19 audit gaps)" --body "<short body: F1 resolve, F2 unhold, F3 retry-source/retry-metrics, F4 drop original_name PII; G1 verification, G2 docs, G3 operator runbook; all 19 audit gaps now closed; full suite ${integ.suite_count}; remaining work is operator-gated (ANTHROPIC_API_KEY + Blotato auth + 3 human-only account steps).>"\`. Capture the PR url into ci_run_url if useful.
- WATCH CI to completion: \`gh run list --repo Fleezyflo/fanops --limit 3\` to get the run id for branch ${BRANCH}, then \`gh run watch <id> --repo Fleezyflo/fanops --exit-status 2>&1 | tail -20\`. If the watch drops on a network blip, RE-QUERY \`gh run list\` and re-watch — do NOT conclude failure from a dropped watch. Set ci_status to "completed success" / "failure" / other, and ci_run_url to the run's URL. Both jobs (unit + e2e) must be green.

Set deviations_recorded=false and handoff_written=false (the ORCHESTRATOR will do the memory deviation memo + handoff after you return — do NOT write to ~/.claude memory yourself). Put anything notable in notes.

Return ONLY the structured JSON.`,
  { schema: CLOSE_SCHEMA, phase: 'Close', label: 'close' }
)
log(`Close: g1=${close.g1_done} g2=${close.g2_done} g3=${close.g3_done} pushed=${close.pushed} ci=${close.ci_status}`)

return {
  blocked: false,
  preflight: pf,
  frame: { tasks: frame.tasks.length, invariants: frame.invariants, f4_meta_safe: frame.f4_meta_safe },
  impls: Object.fromEntries(Object.entries(impls).map(([k, v]) => [k, { red: v.red, green: v.green, suite_count: v.suite_count, commit_sha: v.commit_sha, commit_subject: v.commit_subject }])),
  verifies: verifies.filter(Boolean),
  adversarial: advConfirmed,
  adversarial_verdicts: verdicts.filter(Boolean),
  integrate: integ,
  close,
  worktree: WT,
  branch: BRANCH,
}
