export const meta = {
  name: 'fanops-phase-d-confirm',
  description: 'Phase D re-confirmation: re-run D3 adversarial skeptics on the committed fix (2472c24), compose-check + full suite (Integrate), and produce Close drafts. D5 live read calls were done by the orchestrator (auth-blocked -> verified against loaded live tool schemas).',
  phases: [
    { title: 'D3-reconfirm' },
    { title: 'Integrate' },
    { title: 'Close' },
  ],
}

const WT = '/Users/molhamhomsi/fanops-phase-d'
const VENV = `cd "${WT}" && source .venv/bin/activate`

const PRIME_DIRECTIVE = `
PRIME DIRECTIVE: NO ambiguous/maybe-live post may EVER be marked \`failed\` (=> re-queue => double-post
to a REAL fan account). 2xx-no-id and non-auth MCP error -> needs_reconcile, never failed. run.py
re-drives ONLY queued; needs_reconcile is parked + surfaced by the digest + never auto-requeued.`

const ENV_RULES = `
ENVIRONMENT: work ONLY in ${WT} on branch phase-d-blotato. ALWAYS \`${VENV} && python -m pytest ...\`
(bare pytest mis-reports the \`mocker\` fixture). Your final message IS your return value (JSON when a
schema is given).`

// D5 findings established by the orchestrator via the loaded LIVE Blotato MCP tool schemas + source
// analysis (a data-returning live call is auth-blocked: blotato_get_user/list_accounts both returned
// "Invalid API key or auth session" — recorded honestly, not faked). These are FACTS for the agents.
const D5_FINDINGS = `
D5 LIVE-CONTRACT FINDINGS (orchestrator-established; agents must NOT re-call the MCP — it is
auth-blocked this session; treat these as the verified reality):
- The loaded live tool SCHEMAS confirm: blotato_get_post_status takes postSubmissionId and returns
  publicUrl(published)/scheduledTime(scheduled)/errorMessage(failed); enum in-progress->published|
  scheduled|failed. blotato_list_posts items carry state.type in {scheduled,published,failed} and a
  published item "includes postUrl" (NOT publicUrl). create_presigned_upload_url returns presignedUrl
  + publicUrl. list_accounts returns numeric id + per-platform required fields (FB pageId, TikTok
  privacyLevel+flags, Pinterest boardId, YouTube title+privacyStatus).
- SOURCE-SIDE (grep-verified): the ONLY post-URL reader is reconcile.py:64 info.get("publicUrl"), fed by
  BlotatoStatusClient.get_status -> GET /v2/posts/{id} (metrics.py:43) = the get_post_status endpoint,
  where the key IS publicUrl -> MATCH. metrics.list_posts hits GET /v2/posts (the list endpoint, where
  the URL is postUrl) but track.py/pull_metrics reads ONLY postSubmissionId+metrics from those rows,
  NEVER a URL -> NO postUrl/publicUrl mismatch. media.py:40 reads publicUrl from the PRESIGN response
  (create_presigned_upload_url) where the key IS publicUrl -> MATCH.
- CONCLUSION: the postUrl/publicUrl divergence is REAL in the API but FanOps reads the post URL only
  from get_post_status (publicUrl) and never from a list row -> NO live bug, NO code fix needed. (A
  future feature that reads a URL from a list_posts row WOULD need postUrl — documentation/guard note.)
- postSubmissionId field MATCH; status enum MATCH; presign keys MATCH; per-platform fields MATCH for
  every platform FanOps targets (Pinterest is not in FanOps's Platform enum, so its boardId is N/A).
- A data-returning live MCP call AND any live test post are DEFERRED pending valid Blotato auth + a
  human-approved throwaway test account. blotato_create_post posts to a REAL account (no dry-run) and
  is NEVER fired autonomously.`

const SKEPTIC_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['task_id', 'lens', 'refuted', 'confidence', 'reasoning', 'prime_directive_safe'],
  properties: {
    task_id: { type: 'string' },
    lens: { type: 'string' },
    refuted: { type: 'boolean' },
    confidence: { type: 'string', enum: ['low', 'medium', 'high'] },
    prime_directive_safe: { type: 'boolean' },
    reasoning: { type: 'string' },
  },
}

const INTEGRATE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['full_unit_count', 'integration_count', 'compose_ok', 'compose_evidence', 'regressed', 'blocked'],
  properties: {
    full_unit_count: { type: 'string' },
    integration_count: { type: 'string' },
    compose_ok: { type: 'boolean' },
    compose_evidence: { type: 'string' },
    regressed: { type: 'boolean' },
    blocked: { type: 'boolean' },
  },
}

const CLOSE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['cli_smoke_ok', 'cli_smoke_evidence', 'token_present', 'no_regression', 'readme_drift', 'runtime_drift', 'commits'],
  properties: {
    cli_smoke_ok: { type: 'boolean' },
    cli_smoke_evidence: { type: 'string' },
    token_present: { type: 'boolean' },
    no_regression: { type: 'boolean' },
    readme_drift: { type: 'string' },
    runtime_drift: { type: 'string' },
    commits: { type: 'array', items: { type: 'string' } },
  },
}

// ═════════════════════════════════════════════════════════════════════════════
// PHASE 1 — D3 RE-CONFIRM (the fix is committed at 2472c24; re-run the 2 skeptics that refuted)
// ═════════════════════════════════════════════════════════════════════════════
phase('D3-reconfirm')
const d3lens1 = 'A reworded auth error with NO matched substring (e.g. "credentials rejected", "Authentication failed", "Token expired") — after the fix, does a TYPED BlotatoAuthError raised by the caller now PROPAGATE (run.py halts by type), AND does an UNTYPED RuntimeError with such a message still (acceptably) park as needs_reconcile? Confirm the fix added an "except BlotatoAuthError: raise" clause BEFORE the substring net, and that the substring net is unchanged for untyped transports. Also confirm: a non-auth 500 -> needs_reconcile (not raw-raise, not failed).'
const d3lens2 = 'Does a caller-raised BlotatoAuthError now propagate UNCHANGED through publish_due so run.py _is_fatal_auth_error sees it and HALTS the queue (F52/H8)? Trace it end-to-end (the per-post except in publish_due re-raises a fatal auth error). Confirm blotato_mcp still imports the D2 helper (no copy). Confirm the new regression test test_mcp_typed_auth_error_propagates_even_with_nonmatching_message asserts real behaviour and was mutation-proven.'

function d3Prompt(lens) {
  return 'You are an INDEPENDENT ADVERSARIAL SKEPTIC re-confirming FanOps Phase D task D3 AFTER a fix (commit 2472c24). Your job is to REFUTE the claim that the D3 hole is now closed. Default to refuted=true if ANY hole remains. READ-ONLY — no git mutations.\n'
    + ENV_RULES + '\n' + PRIME_DIRECTIVE + '\n\n'
    + 'THE FIX (commit 2472c24): blotato_mcp.py publish() now has an "except BlotatoAuthError: raise" clause as the FIRST except clause (re-raise the typed auth error unchanged), BEFORE the broad "except Exception" that does the six-substring auth match. So a caller-raised typed BlotatoAuthError propagates regardless of message; an untyped auth RuntimeError is still best-effort substring-matched; a non-auth error still parks as needs_reconcile.\n\n'
    + 'CLAIM: The D3 hole both prior skeptics found is now CLOSED — a typed BlotatoAuthError from the caller propagates so run.py halts by type; the substring net remains for untyped transports; non-auth errors still park (never failed); the no-double-post invariant holds.\n'
    + 'YOUR LENS: ' + lens + '\n\n'
    + 'Read blotato_mcp.py + run.py + the tests in the worktree; run read-only experiments via the worktree venv (cd in, activate, python -c, or pytest a specific test). Report refuted, confidence, prime_directive_safe, concrete reasoning.'
}

const d3skeptics = (await parallel([
  () => agent(d3Prompt(d3lens1), { phase: 'D3-reconfirm', schema: SKEPTIC_SCHEMA, label: 'd3-reconfirm.1' }),
  () => agent(d3Prompt(d3lens2), { phase: 'D3-reconfirm', schema: SKEPTIC_SCHEMA, label: 'd3-reconfirm.2' }),
])).filter(Boolean)

const d3refuted = d3skeptics.filter(s => s.refuted).length
const d3confirmed = d3refuted === 0 && d3skeptics.every(s => s.prime_directive_safe !== false)
log(`D3 re-confirm: ${d3skeptics.length} skeptics, ${d3refuted} refuted, confirmed=${d3confirmed}`)
if (!d3confirmed) {
  return { status: 'blocked', phase: 'D3-reconfirm', d3skeptics }
}

// ═════════════════════════════════════════════════════════════════════════════
// PHASE 2 — INTEGRATE (compose check + full suite; D5 findings supplied by orchestrator)
// ═════════════════════════════════════════════════════════════════════════════
phase('Integrate')
const integrate = await agent(`You are the INTEGRATE agent for FanOps Phase D. All four tasks (D1 21378b0, D2 074d76e, D3 ec73e08 + remediation 2472c24, D4 6c7d145) are implemented, verified, and adversarially confirmed. Prove they COMPOSE and the suite is green.
${ENV_RULES}
${PRIME_DIRECTIVE}
${D5_FINDINGS}

Do:
1. Full unit suite: \`${VENV} && python -m pytest -q -m "not integration" 2>&1 | tail -3\`. Quote it (expect ~261 passed / some deselected — the baseline-unit count + Phase D's new unit tests).
2. Integration marker: \`${VENV} && python -m pytest -q -m integration 2>&1 | tail -3\`. Quote it (the 1 creds-gated Blotato smoke skips cleanly).
3. Also run the WHOLE suite unmarked: \`${VENV} && python -m pytest -q 2>&1 | tail -3\`. Expect 268 passed, 1 skipped.
4. COMPOSE CHECK — reason from code (cite file:line) and/or a small throwaway \`python -c\` (do NOT commit) that:
   (a) D1 token + D2 extraction interlock: a crossposted Post starts with a fanops_ token (crosspost.py); a REST 2xx-no-id leaves it needs_reconcile WITH the token preserved (blotato_rest.py); a real id (incl alias/nested) overwrites the token.
   (b) D3 MCP interlock: a non-auth MCP error parks needs_reconcile (not failed); an untyped auth error maps to BlotatoAuthError; a TYPED BlotatoAuthError propagates unchanged (the 2472c24 fix).
   (c) NO path across D1/D2/D3 marks a maybe-live post failed. Cite each.
   Set compose_ok + compose_evidence.
5. Confirm the D5 findings above are consistent with the current code (the publicUrl reader is reconcile.py:64 fed by get_status=GET /v2/posts/{id}; track reads no URL from list rows; media reads publicUrl from the presign). You do NOT need to re-call the MCP (auth-blocked). Just confirm the source matches the findings.

If the suite regressed below 268 or compose fails, set blocked=true. Return INTEGRATE_SCHEMA JSON.`, { phase: 'Integrate', schema: INTEGRATE_SCHEMA, label: 'integrate' })

if (integrate.blocked || integrate.regressed || !integrate.compose_ok) {
  return { status: 'blocked', phase: 'Integrate', integrate, d3skeptics }
}
log(`Integrate OK — unit ${integrate.full_unit_count}, integration ${integrate.integration_count}, compose_ok=${integrate.compose_ok}`)

// ═════════════════════════════════════════════════════════════════════════════
// PHASE 3 — CLOSE (scratch-cwd CLI smoke + doc drift drafts; orchestrator applies docs/push/CI/handoff)
// ═════════════════════════════════════════════════════════════════════════════
phase('Close')
const close = await agent(`You are the CLOSE agent for FanOps Phase D. Do the real-CLI smoke on a SCRATCH ROOT and draft the doc drift. Do NOT push/PR/invoke skills — the orchestrator does those.
${ENV_RULES}

PART A — REAL-CLI SMOKE on a SCRATCH ROOT (Config resolves from cwd; running elsewhere is a false-green — verify-the-verification). The cleanest proof of D1's token: write a small python script using the worktree's fanops that builds Config(root=<a fresh mktemp -d>), seeds one Source + Moment + a captioned Clip + an accounts.json (mirror tests/test_crosspost.py setup), calls crosspost_clips, and asserts EVERY resulting Post.submission_id startswith "fanops_" AND equals f"fanops_{_hash('idemp', post.id)}". Run it with \`${VENV} && python /tmp/<script>.py\` (cwd in the scratch dir). Capture literal output. Then confirm the repo ledgers under /Users/molhamhomsi/Moh Flow Fanops and ${WT} are UNTOUCHED (the scratch root holds any ledger). Set cli_smoke_ok + cli_smoke_evidence + token_present + no_regression.

PART B — DOC DRIFT DRAFTS (report exact lines; do NOT edit):
1. README at /Users/molhamhomsi/Moh Flow Fanops/README.md (and worktree copy): find any line about "2xx with no submission id -> failed" or MCP auth or submission-id handling that is now stale (D2/D3 changed 2xx-no-id and MCP-no-id to needs_reconcile; D3 added typed-auth re-raise). Quote exact lines into readme_drift (or "none").
2. RUNTIME.md at /Users/molhamhomsi/Moh Flow Fanops/MohFlow-FanOps/00_control/RUNTIME.md: find (a) the INTEGRATION CHECKPOINT / "unverified contract" comments that D5 confirmed against the loaded live MCP schemas (postSubmissionId field, status enum, publicUrl-on-get_status vs postUrl-on-list_posts split, presign keys) — these should be marked "verified against live MCP tool schemas 2026-06-02"; (b) any submission-id / client-token / needs_reconcile / MCP-auth prose to add or correct. Quote exact lines into runtime_drift (or "none").

PART C: \`cd "${WT}" && git log --oneline a2a5d4e..HEAD\` — list all Phase D commit shas + first lines into commits.

Return CLOSE_SCHEMA JSON.`, { phase: 'Close', schema: CLOSE_SCHEMA, label: 'close' })

log(`Close done — cli_smoke_ok=${close.cli_smoke_ok}, token_present=${close.token_present}`)

return {
  status: (d3confirmed && !integrate.blocked && close.cli_smoke_ok && close.token_present) ? 'complete' : 'needs_attention',
  worktree: WT,
  branch: 'phase-d-blotato',
  d3_reconfirm: { skeptics: d3skeptics, confirmed: d3confirmed },
  integrate,
  close,
}
