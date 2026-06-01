export const meta = {
  name: 'fanops-deep-audit',
  description: 'Deep multi-lens audit of the FanOps codebase with adversarial verification',
  phases: [
    { title: 'Review', detail: 'six independent expert lenses read the code in parallel' },
    { title: 'Verify', detail: 'each finding faces three independent skeptics who try to refute it' },
    { title: 'Synthesize', detail: 'merge surviving findings into one prioritized report' },
  ],
}

const ROOT = "/Users/molhamhomsi/Moh Flow Fanops"

const FINDINGS_SCHEMA = {
  type: "object",
  properties: {
    findings: {
      type: "array",
      items: {
        type: "object",
        properties: {
          title: { type: "string", description: "one-line summary of the issue" },
          severity: { type: "string", enum: ["critical", "high", "medium", "low"] },
          file: { type: "string", description: "path:line where it lives" },
          evidence: { type: "string", description: "the specific code/behavior proving it — quote it" },
          impact: { type: "string", description: "what goes wrong in production, concretely" },
          fix: { type: "string", description: "the minimal change that addresses the root cause" },
        },
        required: ["title", "severity", "file", "evidence", "impact", "fix"],
      },
    },
    notes: { type: "string", description: "anything notable that is NOT a finding (good patterns, areas that look solid)" },
  },
  required: ["findings", "notes"],
}

const VERDICT_SCHEMA = {
  type: "object",
  properties: {
    real: { type: "boolean", description: "true if the finding is a genuine, exploitable/triggerable issue; false if refuted, theoretical, or already mitigated" },
    reasoning: { type: "string", description: "why it survives or is refuted — cite specific code" },
    severity_adjusted: { type: "string", enum: ["critical", "high", "medium", "low", "non-issue"], description: "your independent severity call after investigation" },
  },
  required: ["real", "reasoning", "severity_adjusted"],
}

const LENSES = [
  {
    key: "correctness-concurrency",
    prompt: `You are a senior Python correctness & concurrency reviewer auditing the FanOps codebase at ${ROOT}.
Read these modules COMPLETELY: src/fanops/ledger.py, pipeline.py, agentstep.py, ids.py, models.py, crosspost.py, adjust.py, and their tests (tests/test_ledger.py, test_pipeline.py, test_agentstep.py, test_ids.py, test_adjust.py, test_crosspost.py).
The system claims: atomic temp+os.replace ledger writes under a file lock; content-addressed SHA ids (never builtin hash()); per-unit error quarantine; request_id correlation so stale agent responses can't be applied; conservative retirement that preserves live published lineage.
Hunt for: race conditions, non-atomic writes, lock gaps, id collisions or instability, state-machine transitions that lose data or double-process, off-by-one in scheduling/staggering, request/response correlation holes, places where an exception escapes the quarantine and wedges a whole pass. Verify the claimed invariants actually hold in code — don't take comments at face value. Read the actual implementation.`,
  },
  {
    key: "security-secrets",
    prompt: `You are a security engineer auditing the FanOps codebase at ${ROOT}.
Read COMPLETELY: src/fanops/config.py, post/blotato_rest.py, post/blotato_mcp.py, post/media.py, post/payload.py, post/run.py, accounts.py, track.py, log.py, and their tests.
This system holds a BLOTATO_API_KEY, uploads media to public URLs, posts to multiple social accounts, and pulls metrics. Hunt for: secret leakage (into logs, error messages, ledger JSON, payload files written to disk, exception text), API keys in committed files, SSRF / unvalidated URLs in media upload, injection into captions/payloads, missing TLS/cert validation, overly-broad file permissions on written artifacts, PII in the ledger, anything that would leak the operator's identity or credentials. Quote the exact line that leaks.`,
  },
  {
    key: "blotato-integration",
    prompt: `You are an API-integration reviewer auditing the live Blotato seams in FanOps at ${ROOT}.
Read COMPLETELY: src/fanops/post/blotato_rest.py, post/blotato_mcp.py, post/media.py, post/metrics.py, post/payload.py, post/run.py, and tests/test_blotato_rest.py, test_blotato_mcp.py, test_media.py, test_metrics.py, tests/integration/test_blotato_smoke.py.
The handoff flags FOUR unverified INTEGRATION CHECKPOINTs: (1) the media /uploads contract, (2) the postSubmissionId response key, (3) the metrics endpoint, (4) the MCP tool name/args. These have NEVER run against live Blotato. Hunt for: brittle assumptions about response shape, missing error handling for non-200s / malformed JSON, retry/backoff bugs (no jitter, retrying non-idempotent posts → double-post risk), timeout handling that lands a post in the wrong state, the submitting-stranded-post gap, places where a live API quirk would silently corrupt the ledger or double-publish. Be concrete about what breaks on first contact with the real API.`,
  },
  {
    key: "opsec-brand-risk",
    prompt: `You are an opsec & brand-safety reviewer auditing FanOps at ${ROOT}.
This is an autonomous MULTI-ACCOUNT fan-engine that cross-posts the same artist's clips to many fan accounts, staggered "for opsec" with a "subtle non-synchronized artist @mention". Read COMPLETELY: src/fanops/crosspost.py, tagging.py, caption.py, accounts.py, signals.py, moments.py, digest.py, and MohFlow-FanOps/00_control/RISK.md, RUNTIME.md, context.md, accounts.json. Also read their tests.
Hunt for: patterns that would let platforms trivially correlate the fan accounts (identical timing, identical captions, identical @mention placement, identical media hashes, predictable stagger), brand-risk gating that can be bypassed, the 'held' brand-risk state being re-rendered or escaping, captions that could post something off-brand or harmful without a gate, the @mention being TOO synchronized, any place the "subtle" opsec is actually a fingerprint. Evaluate whether the stated opsec measures actually achieve non-correlation or just look like they do.`,
  },
  {
    key: "test-honesty",
    prompt: `You are a test-suite skeptic auditing whether FanOps's green tests actually prove anything, at ${ROOT}.
Read a representative sample of tests COMPLETELY (tests/test_pipeline.py, test_crosspost.py, test_adjust.py, test_post_run.py, test_blotato_rest.py, test_track.py, test_digest.py, tests/integration/test_e2e_real.py, test_blotato_smoke.py) and the modules they cover.
The handoff brags the E2E "is proven NOT to be just mocks". Hunt for: tests that assert on mock behavior instead of real behavior, tautological assertions, over-mocking that hides integration bugs, missing edge-case coverage on the money/reputation paths (publish, track, adjust), skipped tests masquerading as coverage, assertions weak enough to pass through a real bug, the 1-skipped test's true coverage gap. Tell me which green tests would NOT catch a real regression, and which critical paths have no real test at all.`,
  },
  {
    key: "operability-failure",
    prompt: `You are an SRE auditing FanOps for operability and failure-mode handling at ${ROOT}.
Read COMPLETELY: src/fanops/cli.py, pipeline.py, ingest.py, transcribe.py, clip.py, digest.py, log.py, config.py, and tests/test_cli.py, test_ingest.py, test_transcribe.py, test_clip.py, test_digest.py.
This runs unattended via 'fanops run'. Hunt for: failure modes that crash the loop vs degrade cleanly, the two "accepted auto-unrecoverable stuck states" and whether they're truly surfaced (not silently lost), unbounded growth (disk: clips never GC'd, ledger growth), the whisper model-download failure path (the recently-fixed E2E gap — is the SAME fragility present in the real 'fanops advance' runtime, not just the test?), ffmpeg failures, missing-file handling, config validation gaps, exit codes, observability holes. Focus on what bites an operator at 3am.`,
  },
]

phase('Review')
log(`Auditing FanOps across ${LENSES.length} expert lenses, then adversarially verifying each finding.`)

const results = await pipeline(
  LENSES,
  (lens) => agent(lens.prompt, {
    label: `review:${lens.key}`,
    phase: 'Review',
    schema: FINDINGS_SCHEMA,
  }).then(r => ({ lens: lens.key, ...r })),
  (review) => {
    const findings = (review && review.findings) || []
    if (!findings.length) return { lens: review.lens, verified: [], notes: review.notes }
    return parallel(findings.map(f => () =>
      // three independent skeptics per finding, each told to default to refuted if uncertain
      parallel([1, 2, 3].map(n => () =>
        agent(
          `You are skeptic #${n} adversarially verifying a code-audit finding in the FanOps repo at ${ROOT}.
Open the cited file and READ THE ACTUAL CODE around ${f.file}. Do not trust the finding's summary — verify it independently.

FINDING: ${f.title}
SEVERITY CLAIMED: ${f.severity}
FILE: ${f.file}
EVIDENCE CLAIMED: ${f.evidence}
IMPACT CLAIMED: ${f.impact}

Your job is to REFUTE this if you can. Mark real=false if: the code doesn't actually do what the finding claims, the issue is already mitigated elsewhere, it's purely theoretical and can't be triggered in this system's real usage, or the severity is materially inflated. Default to real=false when genuinely uncertain — the bar for a confirmed finding is high. Only mark real=true if you independently confirmed the issue by reading the code.`,
          { label: `verify:${review.lens}:${n}`, phase: 'Verify', schema: VERDICT_SCHEMA }
        )
      )).then(votes => {
        const v = votes.filter(Boolean)
        const realCount = v.filter(x => x.real).length
        const survives = realCount >= 2  // majority of 3 must independently confirm
        return { finding: f, survives, realCount, verdicts: v }
      })
    )).then(checked => ({ lens: review.lens, verified: checked, notes: review.notes }))
  }
)

phase('Synthesize')
const flat = results.filter(Boolean)
const confirmed = []
const refuted = []
for (const r of flat) {
  for (const c of (r.verified || [])) {
    const rec = {
      lens: r.lens,
      title: c.finding.title,
      claimedSeverity: c.finding.severity,
      file: c.finding.file,
      impact: c.finding.impact,
      fix: c.finding.fix,
      votes: `${c.realCount}/3`,
      adjustedSeverities: (c.verdicts || []).map(v => v.severity_adjusted),
    }
    if (c.survives) confirmed.push(rec)
    else refuted.push(rec)
  }
}

const lensNotes = flat.map(r => ({ lens: r.lens, notes: r.notes }))

const synthesis = await agent(
  `You are the lead auditor synthesizing a deep code audit of the FanOps codebase (an autonomous multi-account social-posting engine) at ${ROOT}.

CONFIRMED FINDINGS (survived adversarial verification — at least 2 of 3 independent skeptics confirmed by reading the code):
${JSON.stringify(confirmed, null, 2)}

REFUTED / DOWNGRADED (did not survive — for your awareness, do not re-raise unless you disagree with strong evidence):
${JSON.stringify(refuted.map(r => ({ title: r.title, file: r.file, votes: r.votes })), null, 2)}

PER-LENS NOTES (good patterns / areas that looked solid):
${JSON.stringify(lensNotes, null, 2)}

Produce a tight, prioritized engineering report in markdown:
1. **Verdict** — one paragraph: overall health of the codebase and whether it's safe to operate (dry-run) and what's blocking a safe LIVE run.
2. **Confirmed findings, prioritized** — group by severity (Critical → Low). For each: the issue, file:line, concrete production impact, and the minimal fix. Merge duplicates that multiple lenses found. Be specific and honest; do not pad.
3. **What's solid** — the genuinely good patterns worth preserving (from the notes), so the report isn't just negative.
4. **Recommended next actions** — a short ordered list, distinguishing "before LIVE" from "nice to have".
Keep it skimmable. Cite file:line. No filler.`,
  { label: 'synthesize', phase: 'Synthesize' }
)

return {
  summary: {
    lenses: LENSES.length,
    confirmedCount: confirmed.length,
    refutedCount: refuted.length,
  },
  confirmed,
  refuted: refuted.map(r => ({ title: r.title, file: r.file, votes: r.votes })),
  report: synthesis,
}
