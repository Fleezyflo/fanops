export const meta = {
  name: 'fanops-subtitles',
  description: 'Burned-in subtitles + hook overlay for FanOps clips (deferred-from-plan feature). Builds an .ass from the transcript, chains it into clip.py ffmpeg -vf, fail-open if ffmpeg lacks text filters. Strict TDD, independent verify, adversarial mutation proofs. Ends with a REAL rendered before/after clip.',
  whenToUse: 'After ffmpeg-full is linked (drawtext/subtitles/ass present). The highest-value deferred feature: clips currently carry no on-screen text though the caption guidance assumes a hook.',
  phases: [
    { title: 'Preflight', detail: 'confirm base state + ffmpeg text filters present + targets' },
    { title: 'Implement', detail: 'TDD in one worktree: S1 overlay.ass+capability, S2 Moment.hook, S4 cfg.burn_subs, S3 wire into clip.py, S5 docs; commit per task' },
    { title: 'Verify', detail: 'independent re-run of each task + full suite' },
    { title: 'Adversarial', detail: '>=1 skeptic per task, mutation proofs (own /tmp copy or sequential — NOT shared-worktree parallel)' },
    { title: 'Integrate', detail: 'full suite + REAL end-to-end render (burned text, incl Arabic) + fail-open proof' },
    { title: 'Close', detail: 'sync-docs, push, PR, CI watch' },
  ],
}

const ROOT = '/Users/molhamhomsi/Moh Flow Fanops'
const WT = '/Users/molhamhomsi/Moh Flow Fanops-subs'
const BRANCH = 'feat-burned-in-subtitles'
const VENVRUN = `cd "${WT}" && source .venv/bin/activate &&`

const PREFLIGHT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['base_ok', 'head', 'baseline_count', 'no_open_prs', 'ffmpeg_text_ok', 'targets_present', 'notes', 'stop'],
  properties: {
    base_ok: { type: 'boolean' }, head: { type: 'string' }, baseline_count: { type: 'string' },
    no_open_prs: { type: 'boolean' },
    ffmpeg_text_ok: { type: 'boolean', description: 'bare ffmpeg has drawtext+subtitles+ass and renders' },
    targets_present: { type: 'boolean' }, notes: { type: 'string' }, stop: { type: 'boolean' },
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
  required: ['suite_count', 'real_render_ok', 'render_evidence', 'failopen_ok', 'regressions', 'blocked', 'evidence'],
  properties: {
    suite_count: { type: 'string' },
    real_render_ok: { type: 'boolean', description: 'a REAL clip rendered with burned-in text (unmocked ffmpeg)' },
    render_evidence: { type: 'string', description: 'the clip path + how text presence was confirmed (ass file / ocr / frame extract)' },
    failopen_ok: { type: 'boolean', description: 'with text filter forced-absent, a clip still renders w/o text + logs warning' },
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
  `Preflight for the FanOps burned-in-subtitles feature. Read-only in "${ROOT}" (do NOT create the worktree yet).
1. base_ok/head: \`cd "${ROOT}" && git rev-parse HEAD\` (expect ade52e9... on main), \`git status --porcelain\` (a few untracked .claude/workflows/*.js are fine).
2. baseline_count: \`cd "${ROOT}" && source .venv/bin/activate && python -m pytest -q 2>&1 | tail -3\` (expect "321 passed, 1 skipped").
3. no_open_prs: \`gh pr list --repo Fleezyflo/fanops --state open\` (expect empty).
4. ffmpeg_text_ok: confirm the LINKED ffmpeg has text filters AND renders. Run: \`for f in drawtext subtitles ass; do ffmpeg -hide_banner -h filter=$f 2>&1 | head -1; done\` (NONE should say "Unknown filter"), then a live render: \`ffmpeg -hide_banner -f lavfi -i color=c=black:s=320x240:d=1 -vf "drawtext=text=OK:fontcolor=white:fontsize=40:x=10:y=10" -frames:v 1 /tmp/_pf.png -y\` -> exit 0 + file exists. Set ffmpeg_text_ok accordingly.
5. targets_present (Read/grep): clip.py render_moment (src = led.sources[m.parent_id] at ~L42; ffmpeg_clip_cmd builds -vf via reframe_filter at ~L35; subprocess.run at ~L49); models.py Moment (id/parent_id/state/start/end/reason/transcript_excerpt/signal_score, ~L66-78 — NO hook field yet, that's S2's add); Source.transcript = list[{start,end,text}] + Source.language (models.py); config.py property pattern (blotato_api_key ~L31, tuning ~L48) for the new burn_subs; tests/test_clip.py fixture style (mocker.patch("fanops.clip.subprocess.run") + fake_run writing bytes).
6. Confirm an Arabic-capable font path exists for the .ass (e.g. /System/Library/Fonts/SFArabic.ttf or /Library/Fonts/Arial Unicode.ttf) — note which.
stop=true only if base is wrong, ffmpeg_text_ok is FALSE (then the feature can't be proven — STOP and report), or a target is missing. Return ONLY structured JSON.`,
  { schema: PREFLIGHT_SCHEMA, phase: 'Preflight', label: 'preflight' }
)
log(`Preflight: base_ok=${pf.base_ok} baseline=${pf.baseline_count} ffmpeg_text_ok=${pf.ffmpeg_text_ok} targets=${pf.targets_present} stop=${pf.stop}`)
if (pf.stop || !pf.base_ok || !pf.ffmpeg_text_ok || !pf.targets_present) {
  return { blocked: true, phase: 'Preflight', preflight: pf }
}

// ════════════════════════════════════════════════════════════════════════
phase('Implement')
const SETUP = `STEP 0 — WORKTREE + VENV (ONCE, first implementer):
- \`cd "${ROOT}" && git worktree add "${WT}" -b ${BRANCH} main\` (resume: \`cd "${WT}" && git status\` instead).
- \`cd "${WT}" && python3.12 -m venv .venv && source .venv/bin/activate && pip install -q -e ".[dev]"\`. Verify: \`pip show pytest-timeout ruff | head -4\`.
- Baseline: \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` -> "321 passed, 1 skipped".
`

// ── S1: overlay.py (the .ass builder + capability probe) — pure, standalone ──
const s1 = await agent(
  `S1 implementer for the FanOps burned-in-subtitles feature. ${SETUP}

TASK S1 — NEW MODULE src/fanops/overlay.py (pure functions, no clip.py dependency, independently testable). It produces an ASS subtitle file (libass; the project's ffmpeg has the 'subtitles'/'ass' filters) for a clip, plus a cheap cached capability probe. Functions:
1. \`build_ass(segments, *, hook=None, clip_start, clip_end, width=1080, height=1920, font="Arial Unicode MS") -> str\` — returns ASS file text. \`segments\` = list[{start,end,text}] in SOURCE time (the source transcript). REBASE each segment to clip time: ev_start = max(0, seg["start"] - clip_start); ev_end = min(clip_end, seg["end"]) - clip_start; DROP segments that don't overlap [clip_start, clip_end] (ev_end <= 0 or seg["start"] >= clip_end). Emit a [Script Info] (PlayResX/PlayResY = width/height), a [V4+ Styles] with a bold centered SUBTITLE style (bottom third: Alignment=2, a generous MarginV ~ height*0.12, white text + black outline/shadow for legibility) and a separate HOOK style (top third: Alignment=8, larger fontsize, a punchy color), and [Events] Dialogue lines. Format ASS timestamps as H:MM:SS.cc (centiseconds). If \`hook\` is a non-empty string, add ONE Dialogue on the HOOK style spanning the clip's first min(2.5, clip_len) seconds. Escape ASS special chars in text (newlines -> \\N, strip stray braces {}). Return the full .ass text.
2. \`write_ass(text, path) -> path\` — write the .ass to disk (tiny helper).
3. \`subtitles_vf(ass_path) -> str\` — return the ffmpeg -vf fragment that burns the ass: \`subtitles=<escaped path>\` (escape ':' and '\\' and ',' per ffmpeg filter-arg rules; the standard is to wrap the filename and backslash-escape ':'). Return JUST the filter token (the caller chains it after the reframe with a comma).
4. \`ffmpeg_has_textfilter() -> bool\` — run \`ffmpeg -hide_banner -filters\` once, return True iff 'subtitles' (or 'drawtext') appears; CACHE the result in a module global so repeated clip renders don't re-probe. Must NOT raise if ffmpeg is absent (return False).

STRICT TDD (new file tests/test_overlay.py):
1. RED — write tests FIRST:
   - test_build_ass_rebases_segment_times: segments=[{start:10.0,end:12.0,text:"hi"}], clip_start=8.0, clip_end=14.0 -> the ass text contains a Dialogue starting at 0:00:02.00 (10-8) ending 0:00:04.00, text "hi".
   - test_build_ass_drops_nonoverlapping_segments: a segment entirely before clip_start or after clip_end is NOT in the output.
   - test_build_ass_includes_hook_when_present: hook="WATCH THIS" -> a Dialogue on the HOOK style with that text, starting at 0; absent hook -> no hook Dialogue.
   - test_build_ass_escapes_and_handles_arabic: text with a newline -> \\N in output; an Arabic string round-trips (present in output, not mangled).
   - test_subtitles_vf_escapes_path: subtitles_vf("/a/b c.ass") returns a string containing "subtitles=" and the path properly escaped.
   - test_ffmpeg_has_textfilter_is_cached: monkeypatch overlay.subprocess.run to a counter; call ffmpeg_has_textfilter() twice -> subprocess invoked at most ONCE (cached). (Reset the cache global in the test.)
   Run \`${VENVRUN} python -m pytest tests/test_overlay.py -v 2>&1 | tail -20\` -> CONFIRM FAIL (module/functions don't exist). red_evidence.
2. IMPL — write src/fanops/overlay.py.
3. GREEN — \`${VENVRUN} python -m pytest tests/test_overlay.py -q 2>&1 | tail -4\`; FULL suite \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` (expect 321 + new).
4. COMMIT — \`cd "${WT}" && git add -A && git commit -m "feat (subtitles S1): overlay.py — transcript->ASS builder (rebased, styled, Arabic-safe) + cached ffmpeg text-filter probe

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`. sha + subject.
Return ONLY structured JSON.`,
  { schema: IMPL_SCHEMA, phase: 'Implement', label: 'impl:S1-overlay' }
)
log(`S1 overlay: red=${s1.red} green=${s1.green} suite=${s1.suite_count} sha=${(s1.commit_sha||'').slice(0,8)}`)

// ── S2: Moment.hook field + deterministic hook extraction ──
const s2 = await agent(
  `S2 implementer. Worktree "${WT}" exists with venv; S1 committed. \`cd "${WT}" && git log --oneline -2\`.

TASK S2 — add an optional \`hook\` to a Moment + a deterministic extractor (so a clip gets a punchy top-third line even with NO LLM). Two parts:
1. models.py: add \`hook: Optional[str] = None\` to the Moment model (after transcript_excerpt). Backward-compatible (optional, defaults None; an old ledger loads fine).
2. A pure helper — put it in overlay.py (alongside the ASS builder, since it feeds the hook) OR a tiny function in moments.py; recommend overlay.py: \`derive_hook(transcript_excerpt, *, max_words=7) -> str | None\`. Logic: take the moment's transcript_excerpt (the spoken text), grab the FIRST sentence/clause (split on . ! ? or newline), trim to <= max_words words, Title-case-ish or leave as-is (keep it simple — return the trimmed first clause, stripped). Return None for empty/whitespace input. This is the deterministic default; a future LLM can overwrite Moment.hook directly.
   ALSO wire it at moment creation: in moments.py where a Moment is built (\`keep[mid] = Moment(... transcript_excerpt=pick.transcript_excerpt ...)\` ~L61-64), set \`hook=derive_hook(pick.transcript_excerpt)\` so new moments carry a hook. (Import derive_hook from overlay.)

STRICT TDD:
1. RED — tests:
   - tests/test_overlay.py ADD test_derive_hook_takes_punchy_first_clause: derive_hook("They slept on me. Not anymore, watch this whole thing.") -> "They slept on me" (first clause, <=7 words); derive_hook("") -> None; a >7-word first clause is trimmed to 7 words.
   - tests/test_moments.py ADD test_moment_gets_derived_hook: build a MomentDecision pick with transcript_excerpt="This changed everything for me." through reconcile_moments (match existing test fixtures in test_moments.py), assert the resulting Moment.hook is non-empty and is the derived first clause.
   Run the two -> CONFIRM FAIL (no hook field / no derive_hook). red_evidence.
2. IMPL — models.py hook field; overlay.derive_hook; wire into moments.py.
3. GREEN — \`${VENVRUN} python -m pytest tests/test_overlay.py tests/test_moments.py -q 2>&1 | tail -4\`; FULL suite. No regression (existing moments tests must still pass — Moment.hook is optional).
4. COMMIT — \`cd "${WT}" && git add -A && git commit -m "feat (subtitles S2): Moment.hook field + deterministic first-clause hook extraction (LLM can override later)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`. sha + subject.
Return ONLY structured JSON.`,
  { schema: IMPL_SCHEMA, phase: 'Implement', label: 'impl:S2-hook' }
)
log(`S2 hook: red=${s2.red} green=${s2.green} suite=${s2.suite_count} sha=${(s2.commit_sha||'').slice(0,8)}`)

// ── S4: cfg.burn_subs config property ──
const s4 = await agent(
  `S4 implementer. Worktree "${WT}" exists; S1,S2 committed.

TASK S4 — add a Config property \`burn_subs\` (the on/off toggle for the feature). In src/fanops/config.py, mirror the existing env-property pattern (e.g. poster_backend/responder_mode): \`burn_subs -> bool\` reading env FANOPS_BURN_SUBS, DEFAULT TRUE (on). Treat "0"/"false"/"no"/"off" (case-insensitive) as False, everything else (incl unset) as True. Also add \`subtitle_font -> str\` reading FANOPS_SUBTITLE_FONT defaulting to "Arial Unicode MS" (the Arabic-capable font for the .ass), so the operator can change it.

STRICT TDD (tests/test_config.py — read it first to match style; if it doesn't exist, add a minimal one):
1. RED — test_burn_subs_defaults_on_and_respects_env: Config().burn_subs is True by default; with FANOPS_BURN_SUBS=0 (monkeypatch.setenv) -> False; FANOPS_BURN_SUBS=false -> False; =1 -> True. test_subtitle_font_default_and_override: default "Arial Unicode MS"; FANOPS_SUBTITLE_FONT="X" -> "X".
   Run -> CONFIRM FAIL. red_evidence.
2. IMPL — the two properties.
3. GREEN — the new tests pass; FULL suite. No regression.
4. COMMIT — \`cd "${WT}" && git add -A && git commit -m "feat (subtitles S4): FANOPS_BURN_SUBS toggle (default on) + FANOPS_SUBTITLE_FONT config

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`. sha + subject.
Return ONLY structured JSON.`,
  { schema: IMPL_SCHEMA, phase: 'Implement', label: 'impl:S4-config' }
)
log(`S4 config: red=${s4.red} green=${s4.green} suite=${s4.suite_count} sha=${(s4.commit_sha||'').slice(0,8)}`)

// ── S3: wire into clip.py (depends on S1+S2+S4) ──
const s3 = await agent(
  `S3 implementer. Worktree "${WT}" exists; S1 (overlay.py), S2 (Moment.hook + derive_hook), S4 (cfg.burn_subs) all committed. \`cd "${WT}" && git log --oneline -4\`. This is the WIRING task — it edits src/fanops/clip.py.

TASK S3 — burn the subtitles+hook into the rendered clip. Read the CURRENT clip.py (render_moment ~L39-76, ffmpeg_clip_cmd ~L30-37, reframe_filter). The clip is cut+reframed in ONE ffmpeg pass via a single -vf (reframe_filter). Add the subtitle/hook burn as an ADDITIONAL -vf filter chained AFTER the reframe (comma-separated), gated + fail-open:
- In render_moment, BEFORE building the cmd: if \`cfg.burn_subs\` AND \`overlay.ffmpeg_has_textfilter()\` AND the source has a non-empty transcript, build an .ass: \`overlay.build_ass(src.transcript, hook=m.hook, clip_start=m.start, clip_end=m.end, width=<target w>, height=<target h>, font=cfg.subtitle_font)\`, write it to a temp/clips-adjacent path (e.g. cfg.clips / f"{cid}.ass"), and produce the subtitles -vf fragment. Pass it into ffmpeg_clip_cmd so the final -vf is \`<reframe>,<subtitles=...>\`. Get target w/h from reframe_filter's target table for the aspect (9:16->1080x1920 etc).
- FAIL-OPEN (critical): if cfg.burn_subs is False, OR ffmpeg lacks the text filter (ffmpeg_has_textfilter() False), OR transcript is empty/None, OR build_ass returns empty -> render the clip with the reframe ONLY (current behavior), no subtitle filter. When the text filter is ABSENT but burn_subs is on, log ONE warning via get_logger(cfg) (e.g. "subtitles requested but ffmpeg lacks the text filter — rendering without"). NEVER raise, NEVER block a clip on subtitles.
- ffmpeg_clip_cmd: extend its signature to accept an optional \`extra_vf: str | None = None\` and append it to the -vf (comma-joined) when present, keeping the existing callers/tests working (default None = old behavior).

STRICT TDD (tests/test_clip.py — match the existing fixture style with mocker.patch("fanops.clip.subprocess.run")):
1. RED — tests:
   - test_render_burns_subtitles_when_enabled: source WITH a transcript ([{start,end,text}]) + a moment; cfg.burn_subs True; monkeypatch overlay.ffmpeg_has_textfilter -> True; capture the ffmpeg cmd via the fake_run and assert the -vf arg CONTAINS "subtitles=" AND an .ass file was written. (Patch subprocess.run to capture cmd, like the existing fixture.)
   - test_render_failopen_when_no_textfilter: same setup but monkeypatch overlay.ffmpeg_has_textfilter -> False; assert the -vf does NOT contain "subtitles=" and the clip still renders (state rendered), and a warning was logged (capture via the logger or assert no raise + plain reframe).
   - test_render_failopen_when_no_transcript: source transcript None -> no "subtitles=" in -vf, clip renders.
   - test_ffmpeg_clip_cmd_appends_extra_vf: ffmpeg_clip_cmd(..., extra_vf="subtitles=x.ass") -> the -vf value ends with ",subtitles=x.ass" (chained after reframe).
   Run -> CONFIRM FAIL. red_evidence.
2. IMPL — wire it.
3. GREEN — \`${VENVRUN} python -m pytest tests/test_clip.py -q 2>&1 | tail -4\`; FULL suite. CRITICAL: the EXISTING clip tests (render_moment_creates_clip, error paths, reframe) must ALL still pass — extra_vf defaults None so they're unaffected; VERIFY.
4. COMMIT — \`cd "${WT}" && git add -A && git commit -m "feat (subtitles S3): burn subtitles+hook into the clip render (-vf chained after reframe; gated by FANOPS_BURN_SUBS; FAIL-OPEN if ffmpeg lacks text filters or transcript is empty)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`. sha + subject.
Return ONLY structured JSON.`,
  { schema: IMPL_SCHEMA, phase: 'Implement', label: 'impl:S3-wire' }
)
log(`S3 wire: red=${s3.red} green=${s3.green} suite=${s3.suite_count} sha=${(s3.commit_sha||'').slice(0,8)}`)

const impls = { S1: s1, S2: s2, S4: s4, S3: s3 }
const allGreen = Object.values(impls).every(r => r && r.green && r.commit_sha)
if (!allGreen) {
  log(`IMPLEMENT BLOCKED: ${JSON.stringify(Object.fromEntries(Object.entries(impls).map(([k,v])=>[k,{red:v?.red,green:v?.green,sha:(v?.commit_sha||'').slice(0,8)}])))}`)
  return { blocked: true, phase: 'Implement', impls }
}

// ════════════════════════════════════════════════════════════════════════
phase('Verify')
const vtasks = [
  { id: 'S1', test: 'tests/test_overlay.py' },
  { id: 'S2', test: 'tests/test_overlay.py tests/test_moments.py -k "hook"' },
  { id: 'S4', test: 'tests/test_config.py -k "burn_subs or subtitle_font"' },
  { id: 'S3', test: 'tests/test_clip.py' },
]
const verifies = await parallel(vtasks.map(t => () =>
  agent(
    `INDEPENDENT Verify agent for FanOps subtitles task ${t.id} (you did NOT implement it). Worktree "${WT}", venv .venv.
1. \`${VENVRUN} python -m pytest ${t.test} -v 2>&1 | tail -15\` — task tests PASS.
2. FULL suite: \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` — QUOTE the count.
3. Non-vacuous check: grep the implemented symbol actually exists and is wired (e.g. S1: overlay.build_ass + ffmpeg_has_textfilter in src/fanops/overlay.py; S2: \`hook\` in models.py Moment + derive_hook call in moments.py; S4: burn_subs in config.py; S3: "subtitles=" / extra_vf / ffmpeg_has_textfilter in clip.py). Confirm the change took effect, not a hollow pass.
verified=true ONLY if task tests pass AND the symbol/wiring is real. Return ONLY structured JSON.`,
    { schema: VERIFY_SCHEMA, phase: 'Verify', label: `verify:${t.id}` }
  )
))
for (const v of verifies.filter(Boolean)) log(`Verify ${v.task_id}: verified=${v.verified} count="${v.count}"`)

// ════════════════════════════════════════════════════════════════════════
// ADVERSARIAL — sequential (NOT parallel-shared-worktree; lesson from prior phases).
// Each skeptic mutates then restores; running them one-at-a-time avoids the
// cross-contamination that recurred 3x when skeptics shared one worktree.
// ════════════════════════════════════════════════════════════════════════
phase('Adversarial')
const advSpecs = [
  { id: 'S1', prompt: `Prove overlay.build_ass genuinely REBASES segment times to clip time (a seg at source-time 10s in a clip starting at 8s appears at 2s, NOT 10s) and DROPS non-overlapping segments. MUTATION: change the rebase to use raw seg["start"] (drop the "- clip_start"), confirm test_build_ass_rebases_segment_times FAILS, then \`git checkout src/fanops/overlay.py\`. Also confirm ffmpeg_has_textfilter() is truly cached (one subprocess call for N invocations) and never raises when ffmpeg is absent.` },
  { id: 'S2', prompt: `Prove Moment.hook is set at moment creation from the transcript (derive_hook wired into moments.py), and derive_hook takes the punchy FIRST clause trimmed to max_words (not the whole excerpt). MUTATION: make moments.py pass hook=None (drop the derive_hook call), confirm test_moment_gets_derived_hook FAILS, restore. Confirm Moment.hook is OPTIONAL (an old Moment built without it still validates).` },
  { id: 'S4', prompt: `Prove FANOPS_BURN_SUBS defaults to TRUE (on) and "0"/"false"/"off" turn it off. MUTATION: flip the default to False, confirm test_burn_subs_defaults_on FAILS, restore. Confirm the parsing isn't trivially broken (e.g. "true" stays True, unset stays True).` },
  { id: 'S3', prompt: `THE KEY ONE — prove FAIL-OPEN is real and the gate works. (a) With cfg.burn_subs True + ffmpeg_has_textfilter True + a transcript, the -vf contains "subtitles=" (subtitles ARE burned). MUTATION: remove the subtitles-append in clip.py, confirm test_render_burns_subtitles FAILS, restore. (b) With ffmpeg_has_textfilter False, the clip STILL renders (no "subtitles=" in -vf, state=rendered, no raise) — MUTATION: make the no-textfilter path raise instead of fail-open, confirm test_render_failopen_when_no_textfilter FAILS, restore. (c) Confirm a None/empty transcript also fails open. The fail-open property is load-bearing: a clip must NEVER be blocked or errored just because subtitles couldn't be added.` },
]
const verdicts = []
for (const t of advSpecs) {
  const v = await agent(
    `INDEPENDENT adversarial skeptic for FanOps subtitles task ${t.id} (you did NOT implement/verify it). Default real=false unless you positively confirm. Worktree "${WT}", venv .venv. You are running SEQUENTIALLY (no other skeptic is active), but still: after ANY mutation, \`cd "${WT}" && git checkout <file>\` and confirm \`git status --porcelain\` CLEAN before returning — NEVER commit a mutation.

${t.prompt}

real=true ONLY if it genuinely satisfies the contract. mutation_proven=true ONLY if you reverted/injected, watched the test FAIL (capture the line), and restored clean. any_bypass = how it misbehaves (empty if none). Return ONLY structured JSON.`,
    { schema: VERDICT_SCHEMA, phase: 'Adversarial', label: `adv:${t.id}` }
  )
  verdicts.push(v)
  if (v) log(`Adversarial ${v.task_id}: real=${v.real} mutation_proven=${v.mutation_proven} bypass=${v.any_bypass || 'none'}`)
}
const advOk = verdicts.filter(Boolean).every(v => v.real && v.mutation_proven)

// ════════════════════════════════════════════════════════════════════════
phase('Integrate')
const allVerifyOk = verifies.filter(Boolean).every(v => v.verified)
if (!allVerifyOk || !advOk) {
  log(`PRE-INTEGRATE BLOCK: verifyOk=${allVerifyOk} advOk=${advOk}`)
  return { blocked: true, phase: 'pre-Integrate', verifies, verdicts }
}
const integ = await agent(
  `Integrate agent for the FanOps subtitles feature — this MUST include a REAL render (the whole point: prove burned-in text actually appears, not just that mocked tests pass). Worktree "${WT}", venv .venv. All tasks committed on ${BRANCH}.
1. FULL suite: \`${VENVRUN} python -m pytest -q 2>&1 | tail -3\` -> suite_count. regressions: any 321-baseline test now red? (expect none; delta = new tests).
2. REAL END-TO-END RENDER (unmocked ffmpeg — the linked full build has drawtext/subtitles/ass):
   - Make a scratch dir. Synthesize a tiny test video WITH the real ffmpeg: \`ffmpeg -f lavfi -i color=c=blue:s=720x1280:d=5 -f lavfi -i sine=frequency=440:d=5 -shortest /tmp/subs_src.mp4 -y\`.
   - Build an .ass via overlay.build_ass for a transcript like [{"start":0.5,"end":2.5,"text":"They slept on me"},{"start":2.5,"end":4.5,"text":"not anymore مرحبا"}], hook="They slept on me", clip_start=0.0, clip_end=5.0, 720x1280, font from cfg.subtitle_font. Write it, then render: \`ffmpeg -y -i /tmp/subs_src.mp4 -vf "<reframe-or-scale 720x1280>,subtitles=<ass>" -frames:v 1 -ss 1.0 /tmp/subs_frame.png\` (extract a frame at t=1s where the hook+first subtitle show).
   - CONFIRM the burned text is actually present: the cleanest proof without OCR deps is (a) the render exits 0 and the PNG is non-trivially sized, AND (b) re-render the SAME frame WITHOUT the subtitles filter and assert the two PNGs DIFFER in byte size / pixel content (text changed the frame). If an OCR tool (tesseract) happens to be available, use it to read "slept"; otherwise the differ-with-vs-without proof is sufficient. Set real_render_ok + render_evidence (paths, sizes, with-vs-without delta).
   - BONUS: drive it through the actual code path — build a Source with that transcript + a Moment in a scratch ledger, set FANOPS_BURN_SUBS=1, call render_moment with the REAL subprocess (no mock), confirm the output clip exists and its ffmpeg cmd included subtitles=. (Config resolves from cwd; use a scratch root.)
3. FAIL-OPEN PROOF: temporarily force ffmpeg_has_textfilter to return False (monkeypatch in a one-off python -c, OR set PATH to a dir with a fake ffmpeg lacking filters) and confirm render_moment still produces a clip (reframe only) + logs the warning. Set failopen_ok.
4. blocked=true if suite regresses, the real render fails, or fail-open doesn't hold. Return ONLY structured JSON.`,
  { schema: INTEGRATE_SCHEMA, phase: 'Integrate', label: 'integrate' }
)
log(`Integrate: suite=${integ.suite_count} real_render=${integ.real_render_ok} failopen=${integ.failopen_ok} blocked=${integ.blocked}`)
log(`  render evidence: ${integ.render_evidence}`)
if (integ.blocked || !integ.real_render_ok) {
  return { blocked: true, phase: 'Integrate', integ, impls, verifies, verdicts }
}

// ════════════════════════════════════════════════════════════════════════
phase('Close')
const close = await agent(
  `Close agent for the FanOps subtitles feature (superpowers:finishing-a-development-branch posture). All tasks committed on ${BRANCH} in "${WT}"; suite green (${integ.suite_count}); a real clip rendered with burned-in text. Work in the worktree.

DOCS (sync-docs — read then edit, code-verified):
- MohFlow-FanOps/00_control/RUNTIME.md: §Backlog — mark "Burned-in subtitle / hook overlay rendering" DONE (now: overlay.py builds an ASS from the transcript, clip.py burns it via the subtitles filter, gated by FANOPS_BURN_SUBS, fail-open if ffmpeg lacks text filters). Add FANOPS_BURN_SUBS + FANOPS_SUBTITLE_FONT to the env table. Add a short note: requires a text-capable ffmpeg (libass) — the project's ffmpeg-full build has it; a stripped ffmpeg falls open (no text, logged).
- README.md: note clips now carry burned-in subtitles + a hook (toggle FANOPS_BURN_SUBS).
- docs_updated = files edited.
COMMIT docs: \`cd "${WT}" && git add -A && git commit -m "docs (subtitles): mark burned-in-subtitles backlog DONE; document FANOPS_BURN_SUBS/SUBTITLE_FONT + the libass requirement

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"\`.

PUSH + PR + CI:
- \`cd "${WT}" && git push -u origin ${BRANCH} 2>&1\`. pushed.
- \`gh pr create --repo Fleezyflo/fanops --base main --head ${BRANCH} --title "Burned-in subtitles + hook overlay" --body "<short: overlay.py transcript->ASS builder; Moment.hook + deterministic extraction; clip.py burns subtitles+hook via the ffmpeg subtitles filter chained after reframe; gated by FANOPS_BURN_SUBS (default on); FAIL-OPEN if ffmpeg lacks libass text filters or transcript is empty. Real render proven incl Arabic. Suite ${integ.suite_count}.>"\` -> pr_url.
- WATCH CI: \`gh run list --repo Fleezyflo/fanops --limit 3\` to find the ${BRANCH} run id, then \`gh run watch <id> --repo Fleezyflo/fanops --exit-status 2>&1 | tail -20\`. The CI Linux runner installs full ffmpeg (apt ffmpeg has libass) so the E2E renders real text there. If the watch drops, re-query + re-watch. ci_status = "completed success"/etc; ci_run_url = the URL. Both jobs green.

deviations + handoff are the ORCHESTRATOR's job — do NOT touch ~/.claude memory. Return ONLY structured JSON.`,
  { schema: CLOSE_SCHEMA, phase: 'Close', label: 'close' }
)
log(`Close: docs=${JSON.stringify(close.docs_updated)} pushed=${close.pushed} ci=${close.ci_status}`)

return {
  blocked: false,
  preflight: pf,
  impls: Object.fromEntries(Object.entries(impls).map(([k, v]) => [k, { red: v.red, green: v.green, suite_count: v.suite_count, commit_sha: v.commit_sha, commit_subject: v.commit_subject, files: v.files }])),
  verifies: verifies.filter(Boolean),
  adversarial_verdicts: verdicts.filter(Boolean),
  integrate: integ,
  close,
  worktree: WT,
  branch: BRANCH,
}
