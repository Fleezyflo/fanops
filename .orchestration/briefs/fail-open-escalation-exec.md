# Agent execution contract — Fail-open escalation (step 2)

**Role of THIS file:** parent-agent playbook. It tells an orchestrator how to execute
[fail-open-escalation-policy.md](fail-open-escalation-policy.md) with **sub-agents**, under a hard ban
on bloat, drive-by edits, lazy wiring, and “looks done” theatre.

**Not this file’s job:** restate the full inventory. The policy brief is authoritative for *what*.
This brief is authoritative for *how an agent wave must land it*.

**Depends on:** [config-truth-plane.md](config-truth-plane.md) must be **merged to `main` (or the
same PR must land honesty first, then this)**. If honesty is open → STOP IF §0.

**Governing repo rules:** [AGENTS.md](../../AGENTS.md), [.agents/_worker-protocol.md](../../.agents/_worker-protocol.md).
Cite symbols, not rotting line numbers (STD-DOC-01).

---

## Gate facts (pre-write)

1. **Callers:** Operator / orchestrator opens this path. No `src/` import.
2. **Duplicate check:** `fail-open-escalation-exec.md` is the sole execution wrapper for the policy brief.
3. **Data I/O:** none — markdown only.
4. **User ask (verbatim):** brief an agent to execute the fail-open brief using sub-agents; forbid bloat, unnecessary code/changes, lazy work, less-than-ideal wiring/config/engineering.

---

## Three lenses (execution)

| Lens | Binding answer |
|------|----------------|
| **Best practice** | One mechanism (`fanops.escalation`), three call-sites on the unattended spine, tests that go red on the messy symptom. Sub-agents own **disjoint** file sets; parent never edits product code. |
| **Root cause** | Missing shared posture + exit contract — not “too few try/excepts.” Fixing root ≠ spraying `fail_open()` everywhere. |
| **Leanest** | ~1 new module + thin retargets + exit flips + few CI tests. If a diff grows past the allowlist in §4, it is wrong. |

---

## 0. Parent agent = orchestrator only

You are **delegation-only** for product code.

| Parent MAY | Parent MUST NOT |
|------------|-----------------|
| Read policy + this contract; verify dependency | Edit `src/`, `tests/` (except spawning workers that do) |
| Spawn sub-agents with the Task tool; serialise by file ownership | Implement “just this one line” yourself |
| Run `rg` / `./scripts/check.sh` / `gh` for gates | Run local `pytest` / `check-full.sh` |
| Open/land PR per AGENTS.md (or spawn land worker) | Push/`checkout -B` on `main`; force-push; wipe ledger |
| STOP and report when a worker drifts | Expand scope to steps 3–4 (derived signals / machine-health) |

**Sub-agent types (Cursor):** prefer `generalPurpose` or lane-appropriate workers; each brief below is
self-contained. Do **not** spawn explore agents to “rediscover” the inventory — the policy brief
already did. Do **not** spawn parallel workers that touch the same hot file.

**Protocol:** every implementer reads `.agents/_worker-protocol.md`. One unit → one worktree → own
venv when the wave requires it. Branch must embed the ticket id once minted (`mol-<id>`).

---

## 1. STOP IF (hard)

Abort the wave and report; do not half-land.

1. **Config honesty not on `main`** and this wave is not sequenced *after* honesty in the *same*
   ordered PR stack. Binding exit/attempt behaviour to disputed env is forbidden.
2. **Operator has not answered** (or brief defaults not confirmed) on:
   - stuck-progress exit = `1`
   - `LlmContextLimitError` = deterministic attempt burn (moments TERMINATE / enrichment DEGRADE-clean)
   - learn `AuthError` on run = doctor-unhealthy, run exit may stay `0`
3. Any worker wants to edit a file **outside its allowlist** → STOP, re-plan; never steal.
4. Any worker proposes mass-converting framing/clip/overlay/compose excepts → STOP (P2 BEST_EFFORT).
5. Any worker proposes changing scrape `try_cap` / day budget / cooldown ladder → STOP (budget ≠ ceiling).
6. Local `pytest` requested or started → STOP (CI-only).
7. New abstraction layers (“FailureBus”, plugin registry, YAML policy files, per-subsystem adapters)
   → STOP. One module, four postures, reuse `agentstep` attempts.

---

## 2. Quality bar — FORBIDDEN (bloat / lazy / bad wiring)

Treat each row as a **merge blocker**. Verifier must fail the unit if any appear.

### 2.1 Unnecessary code

| Forbidden | Why | Required instead |
|-----------|-----|------------------|
| Second attempt counter / new JSON under control | Drift with `agentstep` | Wrap `bump_attempts` / `clear_attempts` |
| Duplicate `ATTEMPT_CEILING` constants | Exactly the bug we are killing | One symbol in `fanops.escalation`; others import |
| New env knobs for the ceiling “just in case” | Config sprawl; honesty dependency | Hard-code `3` matching today’s behaviour unless operator asked |
| Enum + stringly twin APIs / facade wrappers | Bloat | One `EscalationPosture` (+ maybe string `.value` only if a log needs it) |
| Copy-paste of `_on_deterministic_fail` into signals/cli/doctor | Lazy fork | Call `decide` / shared helpers |
| “Compat shim” that preserves infinite-pending | Leaves the mess | Delete the bad path |
| Comments longer than the change; new markdown under `docs/` unless CLAUDE one-liner | Theatre | PR body = decision record |
| Reformatting / import churn / rename sprees | Noise | Touch only load-bearing lines |
| Expanding swallow ratchet “while we are here” to all studio views | Scope creep | Optional follow-up ticket only |

### 2.2 Lazy / less-than-ideal engineering (reject on sight)

| Smell | Reject because | Correct shape |
|-------|----------------|---------------|
| `except Exception: log; return 0` still on gates_blocked | Symptom unfixed | Nonzero exit mapped from `_run_once` |
| Doctor still `ok=True, warn=True` for progress class | Fake green | Progress-blocking → unhealthy |
| Context-limit still “degraded_reason only” | Infinite pending | Same attempt path as schema/toolchain |
| `pipeline_status` keeps its own `_GATE_DETERMINISTIC_MAX = 3` | Ceiling fork | Import sole home |
| String-matching `"deterministic ceiling"` / `"attempt 3/3"` as the *only* control plane | Fragile | Structured reason from escalation module; leave legacy string checks only if a consumer already depends — do not add new ones |
| Broad `except Exception` around the new module that swallows TERMINATE | Nullifies policy | Never catch `SystemExit`/`KeyboardInterrupt`; never demote TERMINATE to log |
| Wiring `escalation` through Studio views “for consistency” | Unnecessary | Spine only |
| Feature flag to keep old pending-forever behaviour | Dual maintenance | One behaviour |
| `Config` importing `Settings` / pydantic to “read the ceiling” | Inverts honesty brief | Ceiling is not an env key |

### 2.3 Wiring / configuration / setup (must be ideal)

| Requirement | Detail |
|-------------|--------|
| **Single import home** | `from fanops.escalation import ATTEMPT_CEILING, decide, …` — no relative cycles |
| **Attempts store** | Existing agentstep paths only; same on-disk shape |
| **Exit mapping** | One place in `cli` (`cmd_run` / `_run_once` return → process exit). Do not invent a parallel `sys.exit` deep in doctor *and* cli with different meanings |
| **Doctor health** | Smallest change: progress-blocking warn class flips unhealthy. Do not rewrite all sensors |
| **Tests** | Hermetic unit tests written with the change; assert the *messy symptom* (pending forever / exit 0 / healthy-on-warn). No integration marks. No local run |
| **House style** | Compact one-liners; no black/ruff format of untouched regions |
| **Lane guard** | Before edit: check `.agents/lanes.json`. Collision → STOP and report |

---

## 3. Sub-agent wave plan (ordered)

Parent sequences these. **Do not parallelise A∥B** (same symbols). **C and D may run after B** in parallel only if file sets stay disjoint.

### Wave 0 — Dependency auditor (read-only, mandatory)

**Sub-agent goal:** Prove config honesty state.

**Allowlist:** read-only `rg` / `git` / brief files.

**DoD:**
- [ ] Report: honesty merged on `main`? PR#? Or blocked?
- [ ] If blocked → parent STOPs entire exec wave.

**Forbidden:** any file write.

---

### Wave A — Mechanism (implementer)

**Unit name:** `escalation-core`  
**Objective:** Add `fanops.escalation` and make it the sole `ATTEMPT_CEILING` home; retarget responder + signals; delete duplicate ceiling in `pipeline_status`.

**Allowlist (ONLY):**
- `src/fanops/escalation.py` (**NEW**)
- `src/fanops/responder.py`
- `src/fanops/pipeline_status.py`
- `src/fanops/signals.py`
- `src/fanops/CLAUDE.md` (fail-open bullet only — one short paragraph)
- `tests/test_escalation.py` (**NEW**, minimal)
- `tests/test_responder.py` (add cases only; no drive-by refactors)

**Must implement (policy §2 / §4 Phase B):**
1. Postures: `degrade | refuse | terminate | nonzero` (names stable).
2. `ATTEMPT_CEILING = 3`.
3. `decide(failure_class, attempts) -> posture`.
4. Thin wrappers over `agentstep.bump_attempts` / `clear_attempts`.
5. `LlmResponder._answer_one`: `LlmContextLimitError` and repeated generic failures burn attempts; at ceiling use existing terminal matrix (moments TERMINATE / hooks+captions DEGRADE-clean).
6. Remove `pipeline_status._GATE_DETERMINISTIC_MAX` literal; import sole home.
7. `signals` producer uses shared ceiling/decide — no private copy.

**Explicit non-goals for Wave A:** cli exits, doctor health, scrape, framing, studio views, reconcile rename theatre.

**Worker self-check before handoff:**
```
rg -n '_GATE_DETERMINISTIC_MAX' src/fanops
# expect: definitions only under escalation.py (re-exports OK if single assignment)
./scripts/check.sh
```

**Report shape:** files touched, symbol list, greps, branch name — no essay.

---

### Wave B — Unattended exits (implementer)

**Unit name:** `escalation-exits`  
**Depends on:** Wave A merged to the working branch (same PR stack OK).

**Allowlist (ONLY):**
- `src/fanops/cli.py` (`_run_once` / `cmd_run` exit mapping only)
- `src/fanops/doctor.py` (progress-blocking warn → unhealthy only)
- Tests: extend existing cli/doctor tests or add `tests/test_escalation_exits.py` — **minimal**

**Must implement:**
1. After converge, if any gate still awaiting → process exit **`1`** (pause still **`0`**).
2. Progress-blocking doctor WARN → `report_is_healthy` false / `ok=False` for that class (smallest patch).
3. Learn `AuthError`: do **not** invent a second publish halt; ensure doctor surfaces learning/auth freeze if not already (prefer doctor over changing run exit — per policy gap default).

**Forbidden:** rewriting `_run_once` learning blocks; touching hashtag refresh try/excepts “for consistency.”

---

### Wave C — Verifier (separate sub-agent, not the implementer)

**Unit name:** `escalation-verify`  
**Role:** verify only (worker-protocol verify).

**Checks (all required):**
1. Diff ⊆ union of Wave A+B allowlists (+ intentional test files).
2. Every FORBIDDEN row in §2 absent.
3. Pre-PR greps in §5 pass.
4. CI unit job green on the PR (cite run; do not re-run locally).
5. Policy symptoms fixed:
   - context-limit reaches ceiling (test evidence)
   - gates_blocked → nonzero (test evidence)
   - doctor progress warn → unhealthy (test evidence)
6. Scrape budgets untouched (`rg` on try_cap/cooldown defs = unchanged intent).

**On fail:** do not write a passing verified record; parent spawns a **fix** worker with a *delta* brief (still allowlist-bound).

---

### Wave D — Optional ratchet follow-up (SEPARATE ticket — do not bundle)

Only if parent explicitly schedules it after C passes: spine denylist that handlers must call
`escalation.decide` / `fail_open` / `raise`. **Default: skip.** Bundling this is bloat.

---

## 4. File allowlist (hard ceiling on blast radius)

| Path | Wave | Notes |
|------|------|-------|
| `src/fanops/escalation.py` | A | NEW — keep **small** (< ~120 lines target; if larger, you over-designed) |
| `src/fanops/responder.py` | A | Retarget only |
| `src/fanops/pipeline_status.py` | A | Delete duplicate constant |
| `src/fanops/signals.py` | A | Import shared ceiling |
| `src/fanops/CLAUDE.md` | A | One bullet update |
| `src/fanops/cli.py` | B | Exit mapping only |
| `src/fanops/doctor.py` | B | Progress health only |
| `tests/test_escalation.py` | A | NEW |
| `tests/test_responder.py` | A | Additive |
| `tests/test_escalation_exits.py` or existing cli/doctor tests | B | Additive |

**Anything else → STOP IF.** Especially forbidden without a new ticket: `framing.py`, `clip.py`,
`compose.py`, `overlay.py`, `fanops_hashtags.py`, `studio/views*.py`, `reconcile.py`, `health_model.py`
(stage hang), `settings.py` / config honesty files (wrong step).

---

## 5. Pre-PR acceptance greps (mechanical)

Parent or verifier runs; paste output into PR body.

```bash
# Sole ceiling home (adjust if re-export pattern used — must be ONE assignment)
rg -n '_GATE_DETERMINISTIC_MAX\s*=' src/fanops
rg -n 'ATTEMPT_CEILING\s*=' src/fanops

# No second attempts store
rg -n 'attempt' src/fanops/escalation.py

# Spine still not mass-"fixed"
rg -n 'fail_open\(' src/fanops/framing.py src/fanops/clip.py | wc -l   # must not explode vs main

# Scrape budgets untouched (expect same defs as main)
rg -n '_SCRAPE_TRY_CAP|_SCRAPE_DAY_BUDGET|_COOLDOWN_DELAYS_S' src/fanops/fanops_hashtags.py src/fanops/ig_hashtag_scrape.py

./scripts/check.sh
```

**Definition of done (checkboxes for PR):**
- [ ] Wave 0: config honesty unblocked
- [ ] `fanops.escalation` exists; sole attempt ceiling
- [ ] context-limit / deterministic path burns attempts → terminal matrix
- [ ] `pipeline_status` has no private ceiling literal
- [ ] `cmd_run` stuck gates → exit 1; pause → 0
- [ ] doctor progress-blocking warn → unhealthy
- [ ] Tests written for the three symptoms; CI green
- [ ] Diff ⊆ §4 allowlist
- [ ] No item from §2 FORBIDDEN present
- [ ] PR description cites policy brief + this exec brief; no new governance docs

---

## 6. Parent spawn templates (copy into Task prompts)

### 6.1 Wave A prompt skeleton

```
ROLE: implementer for unit escalation-core. Read:
  .orchestration/briefs/fail-open-escalation-exec.md
  .orchestration/briefs/fail-open-escalation-policy.md (§2 + Phase B only)
  .agents/_worker-protocol.md
  AGENTS.md
OBEY allowlist Wave A exactly. FORBIDDEN §2 is merge-blocking.
Do NOT run pytest. Do NOT touch scrape/framing/studio/cli/doctor.
Deliver: branch with mol-<id>, check.sh green, compact report.
```

### 6.2 Wave B prompt skeleton

```
ROLE: implementer for unit escalation-exits. Same briefs. Allowlist Wave B only.
Wire exit 1 on gates_blocked; doctor progress warn → unhealthy.
Do NOT re-open Wave A design. No pytest local. Compact report.
```

### 6.3 Wave C prompt skeleton

```
ROLE: verifier for escalation-verify. You did NOT implement.
Diff ⊆ allowlist? §2 FORBIDDEN absent? §5 greps? CI green cited?
Symptom tests present? If any no → FAIL with exact gaps. No code edits unless brief says fix-worker.
```

---

## 7. Anti-patterns seen in prior waves (do not repeat)

- “Breadcrumb everything” without exit/attempt change → **logged ≠ fixed**.
- Mirroring a constant into a second file “so we do not import” → **ceiling fork**.
- Expanding the change into Studio read helpers → **wrong plane**.
- Shipping a policy markdown update without mechanism → **theatre** (this exec brief already exists; do not add a third).
- Strangler PR that leaves infinite-pending behind a flag → **forbidden**.

---

## 8. Handoff / land

1. Implementers push + open PR tagged with ticket; wait for CI.
2. Verifier writes `.orchestration/state/verified/<UNIT>.json` only on pass (`head_sha` = PR head).
3. Parent lands per orchestration rules when records + green CI demand it.
4. Never force-push `main`.

---

## 9. Gaps still owned by the operator (not agents)

If unanswered, Wave 0 / parent uses **policy brief defaults** (§6 of policy) and states them in the PR:
- exit `1` stuck / `2` config
- context-limit = deterministic burn
- learn auth = doctor-unhealthy, run exit 0

Override only on explicit operator message.
