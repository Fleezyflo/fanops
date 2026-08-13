# EXEC — Machine-health / observability contract (orchestrator + subagents)

**You are the orchestrator.** You do not implement. You spawn subagents, verify diffs against this brief + the product brief, refuse bloat, and land only when acceptance is met.

**Product brief (source of truth for WHAT):** [`.orchestration/briefs/machine-health-contract.md`](machine-health-contract.md)  
**This brief (source of truth for HOW):** execute that brief with subagents under the constraints below.

**Repo law:** [`AGENTS.md`](../../AGENTS.md) — never push `main`; no local `pytest`; `./scripts/check.sh` before commit; cite symbols not rotting lines; never wipe ledger; drift → merge not reset.

**Remediation order:** this is root **#4**. Roots **#1–#3** are prerequisites. Do **not** implement config truth, escalation policy, or derived-signal primitives inside this wave unless a WP is explicitly marked to consume an already-landed stub — otherwise mark **BLOCKED** and stop.

---

## Mission (one sentence)

Lock **one** machine-state → severity → primary channel (`fanops doctor` / `build_health_report`) with projections + CI ratchets so multi-channel soft-green cannot return — nothing else.

---

## Hard bans (instant reject / re-spawn)

| Ban | Why |
|---|---|
| Implement root #1 (config truth), #2 (escalation module/postures), or #3 (mtime age / strip TTL / fail-open-to-LIVE primitives) “while we’re here” | Wrong brief / wrong order; encodes lies or duplicates work |
| Symptom fix: another banner, another WARN, another snapshot file, another htmx health partial | Recreates D1 |
| New observability framework / OTel / second health package / microservice | Bloat |
| Keep shipping `ok=True, warn=True` as the public contract without severity | Soft-lie disease |
| Fail-open missing/stale snapshot to zero counts or hidden danger | D3 |
| Map UNKNOWN → healthy / LIVE / `"live"` publish label | Forbidden by product brief |
| Merge `/healthz` into doctor or claim process-up = machine-healthy | Category error |
| Mass fail_open rewrite / swallow-ratchet allowlist retarget | Roots #2 / #6 |
| Drive-by Studio visual redesign, card layouts, unrelated refactors | Scope creep |
| Edit `.agents/lanes.json` to take hot files | Stop and report |
| `git add -A`, secrets, force-push, push to `main` | Safety |
| Local `pytest` / `check-full.sh` | Machine-killer; CI-only tests |
| Lazy “log everywhere” / AST theater without a failing acceptance line | Lazy |
| Parallel implementers on the same hot files | Collision |

**Default stance:** if a change is not required for a checked acceptance line in the product brief, **do not make it**.

---

## Quality bar (every WP)

Before accepting a subagent’s work, answer **yes** to all:

1. **Root, not symptom** — removes multi-channel ownership / soft-lie / UNKNOWN→green, or only papers over it?
2. **Single wiring** — can you draw `fact → build_health_report → severity → primary channel` with projections as pure functions?
3. **Minimal diff** — smallest edit that meets WP acceptance.
4. **Prereq honesty** — if TTL/mtime/escalation posture missing, is the WP **BLOCKED** rather than faked?
5. **Readable** — stranger finds the one health owner without spelunking strip + doctor + up.

If any answer is no → reject and re-spawn with a focused delta brief.

---

## Subagent model

| Role | Spawns | Does |
|---|---|---|
| **You (orchestrator)** | all below | brief, sequence, reject bloat, merge order, PR |
| **inventory** | WP0 only | read-only census; confirm #1–#3 landed; BLOCKED list |
| **implementer** | one WP at a time | code + tests written, not run locally; `check.sh` |
| **adversary / review** | after each implementer | read-only: hunt bans, dual channels, soft-lies; PASS/FAIL with evidence |
| **land** | after all WPs + adversary PASS | branch, commit named files only, PR |

**Rules:**

- **One WP per implementer.** Do not parallelize WP1–WP4 on `health_model.py` / `doctor.py` / `views.py`.
- WP0 first; its BLOCKED list binds later WPs.
- Adversary never edits; FAIL → fix implementer with FAIL list only.
- Inherit parent model unless human says otherwise.
- ECC write hooks: `ECC_GATEGUARD=off` + `ECC_DISABLED_HOOKS=…` authorized as prior waves — not ban evasion.

---

## Sequence (mandatory)

```
WP0 inventory + prereq gate (read-only)
    ↓
WP1 severity + healthy predicate + exit honesty
    ↓ adversary
WP2 single constructor + projectors (strip / Go-Live / daemon / metrics)
    ↓ adversary
WP3 freshness wiring  ——— if #3 missing → BLOCKED (skip code; document)
    ↓ adversary (or BLOCKED record)
WP4 channel ratchet + folklore purge
    ↓ adversary
WP5 gate (check.sh, arch regen, PR)
```

Do not skip adversary between implementers. Do not start WP2 until WP1 PASS. Do not fake WP3.

---

## Per-WP spawn prompts (copy-adapt)

### WP0 — inventory + prereq gate

```
Read-only. Product brief: .orchestration/briefs/machine-health-contract.md
Confirm status of roots #1–#3 (PRs/briefs landed?). Emit: channel×symbol×severity table
delta vs brief; BLOCKED list for WP3+. No code changes. No commits.
```

### WP1 — severity + exits

```
Implement WP1 only from machine-health-contract.md
Severity on checks/deps; retarget report_is_healthy; cmd_doctor/cmd_health/cmd_init/cmd_autopilot
exit honesty. Do not add strip TTL (WP3). Do not build escalation.py (#2).
check.sh. No pytest. Bans: machine-health-contract-EXEC.md Hard bans.
```

### WP2 — projectors

```
Implement WP2 only. Strip/Go-Live/daemon partial/metrics must project from build_health_report
(or health_model projectors). One half-live function. No new health channels.
No snapshot TTL invention if #3 not landed — leave reads as-is but do not add fail-open zeros.
```

### WP3 — freshness

```
Implement WP3 only IF derived-signal (#3) primitives exist. Wire TTL/UNKNOWN into strip/dep/daemon
reads. Missing/stale → UNKNOWN/FAIL presentation. If #3 absent: return BLOCKED with evidence; no code.
```

### WP4 — ratchet

```
Implement WP4 only. CI AST/registry: allowlisted health surfaces; ban new ok+warn without severity;
docs folklore purge. Minimal tests written for CI.
```

### Adversary (after each WP1–4)

```
Read-only review of current diff vs machine-health-contract.md + EXEC hard bans.
FAIL on: new channel, soft-lie, UNKNOWN→green, implementing #1–#3, bloat, unrelated files.
PASS with symbol evidence. No edits.
```

### Land

```
Branch: prefer rfd/machine-health-contract (or publish/ if views_common/config touched).
Stage only named program files + tests + this brief if needed.
HEREDOC commit. check.sh. Push. gh pr create. Never force-push main.
Exclude junk (*.crt, unrelated briefs, secrets).
```

---

## Orchestrator refusal phrases (use verbatim when needed)

- “Rejected: invents a parallel health channel.”
- “Rejected: soft-lie `ok`+`warn` without severity.”
- “Rejected: scope is root #1/#2/#3 — mark BLOCKED, do not implement here.”
- “Rejected: fail-open to green/LIVE/zero counts.”
- “Rejected: bloat / drive-by / local pytest.”

---

## Done means

Product brief acceptance checklist is checked; adversary PASS on final diff; PR open against `main` (not pushed to `main`); prerequisites either satisfied or explicitly BLOCKED in the PR body with no silent partials.
