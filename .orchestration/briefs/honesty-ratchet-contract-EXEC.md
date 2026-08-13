# EXEC — Honesty ratchet contract (orchestrator + subagents)

**You are the orchestrator.** You do not implement. You spawn subagents, verify diffs against this brief + the product brief, refuse bloat / call-name theater, and land only when acceptance is met.

**Product brief (source of truth for WHAT):** [`.orchestration/briefs/honesty-ratchet-contract.md`](honesty-ratchet-contract.md)  
**This brief (source of truth for HOW):** execute that brief with subagents under the constraints below.

**Repo law:** [`AGENTS.md`](../../AGENTS.md) — never push `main`; no local `pytest`; `./scripts/check.sh` before commit; cite symbols not rotting lines; never wipe ledger; drift → merge not reset.

**Remediation order:** this is root **#6 — LAST**. Roots **#1–#5** are prerequisites. Especially **#2 (escalation)** and **#4 (machine-health)**. Do **not** implement those inside this wave. If APIs are missing, mark **BLOCKED** and stop. Do **not** “fix” honesty by teaching `_handler_non_silent` more names.

---

## Mission (one sentence)

Retarget honesty/silence CI gates from **call-name AST theater** to **semantic properties** of the escalation + machine-health contracts — without big-bang false-red, without mass except rewrites, without celebrating empty `_baseline_silent_swallows`.

---

## Hard bans (instant reject / re-spawn)

| Ban | Why |
|---|---|
| Implement root #1–#5 “while we’re here” (config truth, escalation module, TTL/mtime, health severity model, plane untangle) | Wrong brief / wrong order |
| Teach `_handler_non_silent` / `extract._handler_non_silent` **new call names** (`trace`, `logger`, `breadcrumb`, …) as the fix | FM5 — proxy theater |
| Lower / empty swallow or non-honest baselines without semantic proof (site calls `decide` / health breadcrumb / BEST_EFFORT registry) | Vacuous green |
| Delete swallow ratchet with no semantic replacement | Re-opens real silence |
| Mass rewrite ~300 broad excepts / “add a log everywhere” | Lazy; belongs to #2 lean spine |
| New AST gate that only measures identifier shape and labels it honesty | Recreates the disease |
| Retarget `lane_guard` / PR collision / integration marker / print GB-6 into “honesty” | Category error (OWNERSHIP / routing) |
| Celebrate empty `_baseline_silent_swallows = {}` as “no silent failures” in docs/PR | Loudest lie |
| New policy framework / OTel / second arch engine | Bloat |
| Edit `.agents/lanes.json` to take hot files | Stop and report |
| `git add -A`, secrets, force-push, push to `main` | Safety |
| Local `pytest` / `check-full.sh` | Machine-killer; CI-only tests |
| Parallel implementers on the same ratchet/helper files | Collision |
| Fork a second health-channel allowlist divergent from #4 WP4 | Dual ownership |

**Default stance:** if a change is not required for a checked acceptance line in the product brief, **do not make it**. Prefer **gate-only** PRs; product call-site fixes belong in #2/#4 waves.

---

## Quality bar (every WP)

Before accepting a subagent’s work, answer **yes** to all:

1. **Root, not proxy** — does this prove escalate / severity / UNKNOWN / channel law, or only a blessed identifier?  
2. **Prereq honesty** — if #2/#4 symbols missing, is the WP **BLOCKED** rather than faked with call-name?  
3. **Minimal diff** — smallest gate/docs change that meets WP acceptance.  
4. **No false-red flood** — shrink-only baselines; dual-gate until flip.  
5. **Category correct** — lane/print/PostState/integration left as KEEP, not “honesty.”  
6. **Negative control** — planted semantic lie fails the new finder (or documented `xfail` only in WP1 shadow).  

If any answer is no → reject and re-spawn with a focused delta brief.

---

## Subagent model

| Role | Spawns | Does |
|---|---|---|
| **You (orchestrator)** | all below | brief, sequence, reject bans, merge order, PR |
| **inventory** | WP0 only | read-only census; confirm #1–#5; lock landed API symbols; BLOCKED list |
| **implementer** | one WP at a time | tests/tools/docs only unless orchestrator explicitly assigns a product file; `check.sh`; no local pytest |
| **adversary / review** | after each implementer | read-only: hunt hard bans, FM5 teaching, folklore lies, category errors; PASS/FAIL with symbol evidence |
| **land** | after all WPs + adversary PASS | branch, commit named files only, PR |

**Rules:**

- **One WP per implementer.** Do not parallelize WP2–WP4 on `test_swallow_ratchet.py` / shared honesty helper / `extract.py`.  
- WP0 first; BLOCKED list binds later WPs.  
- Adversary never edits; FAIL → fix implementer with FAIL list only.  
- Inherit parent model unless human says otherwise.  
- ECC write hooks: `ECC_GATEGUARD=off` + `ECC_DISABLED_HOOKS=…` authorized as prior waves — not ban evasion.

---

## Sequence (mandatory)

```
WP0 inventory + prereq gate (read-only)
    ↓
WP1 negative controls + shadow non-honest census (call-name swallow STAYS blocking)
    ↓ adversary
WP2 shared escalate-OK helper; spine ↔ swallow definition align
    ↓ adversary
WP3 semantic growth ratchet + BEST_EFFORT registry  ——— if #2 missing → BLOCKED
    ↓ adversary (or BLOCKED record)
WP4 retire call-name authority; retarget extract/derived/IMPL-007; folklore purge
         ——— prefers #2+#4; if missing → BLOCKED flip, keep dual-gate
    ↓ adversary
WP5 health-channel / severity shape honesty gates ——— if #4 missing → BLOCKED
    ↓ adversary
WP6 gate (check.sh, arch regen, PR)
```

Do not skip adversary between implementers. Do not fake WP3–WP5. Do not expand `_SPINE` into framing/clip “for completeness.”

---

## Per-WP spawn prompts (copy-adapt)

### WP0 — inventory + prereq gate

```
Read-only. Product brief: .orchestration/briefs/honesty-ratchet-contract.md
Confirm status of roots #1–#5 (PRs/briefs landed? which symbols exist?).
Re-measure: broad except count, silent-by-call-name, weak-only credits.
Emit: BLOCKED list; locked semantic API symbol table from LANDED code only.
No code changes. No commits.
```

### WP1 — shadow + negative controls

```
Implement WP1 only from honesty-ratchet-contract.md
Plant semantic-lie negative controls (weak-only, getLogger-construction,
fail_open theatre, soft-lie ok+warn). Shadow/shrink-only baseline_non_honest OK.
Do NOT flip swallow call-name test. Do NOT teach new call names.
check.sh. No pytest. Bans: honesty-ratchet-contract-EXEC.md Hard bans.
```

### WP2 — shared helper

```
Implement WP2 only. Extract escalate-OK detection shared by
test_escalation_spine_ratchet (and future honesty finder) into one lean module
(tests/_honesty_ast.py or tools/arch/honesty.py). Spine stays blocking.
Swallow call-name unchanged. No product src/fanops behavior changes.
```

### WP3 — semantic growth ratchet

```
Implement WP3 only IF #2 decide/escalation APIs exist.
_handler_honest + blocking growth/shrink-only non-honest baseline +
BEST_EFFORT declared registry. If #2 absent: return BLOCKED with evidence; no code.
Forbidden: extending warning/debug/log name frozenset.
```

### WP4 — retire call-name + arch mirror + folklore

```
Implement WP4 only IF semantic ratchet from WP3 is green in CI intent.
Flip call-name out of authority; retarget extract._handler_non_silent mirror;
regen derived/ratchets.json; update IMPL-007 fields; purge ENGINEERING_STANDARDS
residual + anomalies/CODEMAPS overclaim that empty baseline = honesty.
If flip unsafe: keep dual-gate, document; do not false-red 78 weak-only sites.
```

### WP5 — health channel honesty

```
Implement WP5 only IF #4 health contract APIs/channel list exist.
Wire channel allowlist + severity-shape ban (coordinate with machine-health WP4;
do not fork). If #4 absent: BLOCKED; no code.
```

### Adversary (after each WP1–5)

```
Read-only review of current diff vs honesty-ratchet-contract.md + EXEC hard bans.
FAIL on: new call-name teachings, empty-baseline folklore, implementing #1–#5,
retargeting lane/print/integration as honesty, mass except rewrite, bloat,
unrelated files, missing negative controls.
PASS with symbol evidence. No edits.
```

### Land

```
Branch: prefer rfd/honesty-ratchet-contract.
Stage only named program files (tests/tools/docs/governance/derived) + briefs if needed.
HEREDOC commit. check.sh. Push. gh pr create. Never force-push main.
Exclude junk (*.crt, unrelated briefs, secrets).
```

---

## Adversary hunt list (concrete)

Fail the WP if any appear:

1. Diff adds strings to the swallow non-silent name frozenset without deleting the frozenset’s authority  
2. PR body or docs say “silent swallows = 0 ⇒ honest” / celebrate `{}`  
3. New helper taught to AST without `test_*_calls_decide` / severity proof  
4. `scripts/lane_guard.py` or print `_CLI_PRINT_COUNT` “retargeted”  
5. Product mass-touch of `framing.py` / `digest.py` / `health_model.py` excepts “to satisfy ratchet” without #2/#4 posture  
6. Second `build_health_report` fork or parallel health allowlist file  
7. Local pytest in the agent transcript  

---

## Orchestrator refusal phrases (use verbatim when needed)

- “Rejected: teaches the AST more call names (FM5).”  
- “Rejected: empty swallow baseline is not honesty.”  
- “Rejected: scope is root #1–#5 — mark BLOCKED, do not implement here.”  
- “Rejected: category error — lane/print/integration are not honesty gates.”  
- “Rejected: mass except rewrite / log-everywhere theater.”  
- “Rejected: deletes swallow ratchet without semantic replacement.”  
- “Rejected: bloat / drive-by / local pytest.”  

---

## Done means

Product brief acceptance checklist is checked; adversary PASS on final diff; PR open against `main` (not pushed to `main`); prerequisites either satisfied or explicitly BLOCKED in the PR body with **no silent partials** and **no call-name fake-green**.

---

## Human paste one-liner

```
APPROVE honesty-ratchet #6 EXEC: retarget swallow/arch from call-name theater to semantic decide/health-breadcrumb/severity gates after #2+#4; shrink-only migration; ban teaching AST names; empty {} ≠ honesty; gate-only PRs — spawn WP0 inventory first.
```
