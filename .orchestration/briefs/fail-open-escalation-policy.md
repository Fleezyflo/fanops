# Brief — Fail-open escalation policy + inventory remediation

**Status:** draft for agent execution (await operator `APPROVE`)  
**Execute via:** [fail-open-escalation-exec.md](fail-open-escalation-exec.md) (sub-agent wave + anti-bloat contract)  
**Depends on:** [config-truth-plane.md](config-truth-plane.md) (remediation step 1) — **do not land policy binding until Config and Settings mean the same thing for every `FANOPS_*` the policy reads**  
**Symptom this closes:** messy / unpredictable unattended behaviour — exit 0 while stuck, infinite pending gates, doctor green on WARN, sibling failures escalate while peers pend forever  
**Prior root diagnosis:** remediation-order step 2 from the unattended-honesty root-cause session (fail-open without an escalation model)

---

## 0. Three lenses (locked)

| Lens | Answer |
|------|--------|
| **Best practice** | One shared failure taxonomy + one attempt ledger + one exit-code contract. Every fail-open site names its posture (`degrade` / `refuse` / `terminate` / `nonzero`) in code, not only in a comment. Breadcrumb ≠ escalation. |
| **Root cause** | House default is "log + continue." Escalation ceilings are invented per subsystem (or absent). Runtime Config fail-opens typos to defaults; Settings is strict only on the doctor path — policy that binds to Config can bind to the wrong values. |
| **Leanest fix** | (1) Finish config honesty. (2) Add one `fanops.escalation` module (posture enum + shared attempt counter + exit helpers). (3) Retarget the unattended spine first (`responder` → `cli.cmd_run` → `doctor`). (4) Leave media BEST_EFFORT leaf detectors alone unless they lie about success. Do not mass-rewrite ~325 `except Exception` sites. |

---

## 1. Evidence snapshot (measured 2026-08-13)

| Signal | Count / fact | Source |
|--------|--------------|--------|
| Comment/mention `fail-open` / `fail_open` in `src/fanops` | ~340 | ripgrep |
| `with fail_open(...)` sites | **40** | AST |
| Broad `except Exception` / `BaseException` | **325** | AST |
| Of those: logged continue | 272 | AST outcome classify |
| Silent swallow ratchet baseline | **empty `{}`** | `tests/test_swallow_ratchet.py` `_baseline_silent_swallows` |
| Shared escalation module | **absent** | only `errors.fail_open` (log + swallow) |
| Per-subsystem ceilings | ≥5 | see §3 |

`errors.fail_open` (`fanops.errors.fail_open`): logs every failure with `exc_info`, never swallows `KeyboardInterrupt`/`SystemExit`, **never escalates, never stamps ledger state, never changes exit code**.

Swallow ratchet: forbids *silent* broad handlers. Blind spot (already noted in `docs/CODEMAPS/anomalies.md`): **logged ≠ surfaced ≠ escalated**. A gate can log forever and still look "healthy" to `doctor` exit and `cmd_run` exit 0.

### 1.1 Explicit `with fail_open(...)` sites (complete list)

| Module | Site string |
|--------|-------------|
| `accounts._hydrate_from_personas` | `accounts._hydrate_from_personas` |
| `agentstep.bump_attempts` | `agentstep.bump_attempts` |
| `artifacts.manifest_stage_times` | `artifacts.manifest_stage_times` |
| `audit.write_audit` / `audit.read_audit_tail` | same |
| `cutover._load_state` | `cutover._load_state` |
| `daemon` | `ensure.kickstart_stale_code`, `daemon._studio_get_fingerprint`, `version_signal.git`, `daemon._redeploy_studio.plist_generation`, `kickstart_studio_if_present` |
| `fanops_hashtags` / `persona_research` | dynamic rederive/corpus sites |
| `health_model.daemon_progress` | `daemon_progress` |
| `pipeline_run.note_stage` | `note_stage` |
| `pipeline_status._gate_attempt` | `pipeline_status._gate_attempt` |
| `post/zernio` | `zernio.create.parse`, `zernio.409.parse` |
| `studio.actions*` / `studio.app` / `studio.personas` | publish_now, warm aspect, randomize schedule, metrics digest, approve sched_detail, month_arg, preview_compose |
| `studio.views` (15) | led_for_request, publish_queue, errored_sources, corpora marker, personas discovery, post_live_today, resolve_account_handle, review_handoff, zero_post_clips, metrics_stale_hint, review_nav_params, account_work_counts, daemon_health (+ pending_gates ×2) |

Plus ~300 documented fail-open *comments* / logged broad excepts (clip/framing/compose/overlay/reconcile/cli/doctor densest). Treat comment-only media leaf detectors as BEST_EFFORT_OK unless they claim success falsely.

---

## 2. Shared escalation policy (THE deliverable)

### 2.1 Postures (exactly four — no fifth "soft fail")

| Posture | Meaning | Unit / process effect | Operator visibility (required) |
|---------|---------|----------------------|--------------------------------|
| **DEGRADE** | Continue with a documented safe default. Work product remains usable but incomplete. | Unit progresses | `run.log` event **and** (when a Source/Post exists) `degraded_reason` / equivalent field |
| **REFUSE** | Do not perform this unit/op. Do not invent success. Retry later is allowed. | Skip / leave pending / return sentinel that callers treat as "not done" | `run.log` + status/digest surface |
| **TERMINATE** | Permanent for this unit until operator resume. Stop burning attempts. | `SourceState.error` / post park / gate discard as appropriate | `error_reason` + digest/status |
| **NONZERO** | Process-level hard refuse. Unattended monitors must see failure. | CLI / doctor exit `1` or `2` | stderr one-liner + exit code |

**Forbidden:** "fail-open" that only logs and leaves the machine looking green. That is the messy behaviour.

### 2.2 Classification rules (deterministic)

| Failure class | Examples | Default posture | Notes |
|---------------|----------|-----------------|-------|
| **Optional enrichment** | framing miss → centered crop; hook-burn fail → base copy; digest section skip; audit write | **DEGRADE** | Forever OK. Must still breadcrumb every failure (not once/process). |
| **Transient I/O / network** | 5xx, timeout once, flock busy, transport hiccup on verify | **REFUSE** (retry next tick) | Do not TERMINATE on first hit. Count toward shared attempt budget when the *same key* repeats. |
| **Deterministic producer failure** | schema/validation, toolchain missing for this unit, double timeout in one pass, corrupt gate payload, context-limit | burn shared attempt; at ceiling → **TERMINATE** (or DEGRADE-clean for enrichment gates — see §2.4) | Today only some of these hit `_on_deterministic_fail`. |
| **Operator / config honesty** | unknown `FANOPS_*` enum, missing required binary for the *run*, `FANOPS_RESPONDER` typo | **NONZERO** at preflight/doctor; runtime must not silently substitute | Config honesty precondition. |
| **Fatal auth on write path** | Postiz/Zernio 401 on publish | halt queue (**REFUSE** remaining) + **NONZERO** on publish verbs; learn path may skip with stderr but must not look like success forever | Keep existing AuthError type halt on publish. |
| **Observability-only** | audit trail, version fingerprint, Studio read helpers | **DEGRADE** | Never block product path. |

### 2.3 Shared attempt model — **no more per-subsystem ceilings**

One module owns:

```
fanops.escalation
  EscalationPosture = degrade | refuse | terminate | nonzero
  ATTEMPT_CEILING = 3          # THE only strike count for deterministic retries
  record_attempt(cfg, scope, key) -> int
  clear_attempts(cfg, scope, key)
  decide(failure_class, attempts) -> EscalationPosture
```

Reuse `agentstep.bump_attempts` / `clear_attempts` — do **not** invent a second counter file. Extend `scope` so signals / future producers share the same store; stop mirroring `_GATE_DETERMINISTIC_MAX` in `pipeline_status.py`.

**Not escalation ceilings (keep as budgets):** scrape `try_cap` / day budget / cooldown ladder; band/pick ceilings; upload ceilings.

**Ceilings that MUST collapse into the shared model or into the later health contract:**

| Today | File | Problem |
|-------|------|---------|
| `_GATE_DETERMINISTIC_MAX = 3` | `responder.py` | Only some exception classes escalate |
| duplicate `_GATE_DETERMINISTIC_MAX = 3` | `pipeline_status.py` | Drift risk |
| import of responder constant | `signals.py` | Partial reuse, not shared policy |
| `_GATE_STALE_TICKS = 3` | `doctor.py` | WARN-only; mtime reset defeats it |
| `_DAEMON_STALE_TICKS = 3` | `doctor.py` | Separate "dead pump" notion |
| `_STAGE_HANG_CEILING_S = 3600` | `health_model.py` | Time-based hang — belongs in health contract (step 4), not a third escalation dialect |

### 2.4 Gate-kind terminal matrix

| Gate kind | Terminal action | Rationale |
|-----------|-----------------|-----------|
| `moments` | `SourceState.error` + `discard_gate` | No windows → no clips; fail-closed |
| `moment_hooks` | write clean `hook=None` response + `degraded_reason` | Enrichment; DEGRADE-clean so ingest proceeds |
| `captions` | write clean `items=[]` + `degraded_reason` | Enrichment; same |
| `signals` producer | same TERMINATE path as moments owner | Must call shared `decide` |

### 2.5 Exit-code contract (unattended)

| Situation | Exit | Today (broken) |
|-----------|------|----------------|
| Clean pass / intentional pause | `0` | OK |
| Config typo / missing toolchain for run | `2` | Partial |
| Auth fatal on **publish** write path | non-zero | OK on publish; learn skips with `0` |
| Gates still awaiting after converge loop | **non-zero (`1`)** | `cmd_run` prints note, **exit stays 0** |
| Doctor: progress-blocking WARN | **non-zero (`1`)** | sensors `ok=True, warn=True`; `report_is_healthy` ignores warn |
| Doctor: hard misconfig | `1`/`2` | OK path exists via Settings strict |

**Rule:** unattended monitors that key on exit code must never see green while the pump is stuck.

### 2.6 When `errors.fail_open` is allowed

| Allowed | Forbidden |
|---------|-----------|
| Observability / optional enrichment / Studio read helpers with a safe UI default | Anything on the unattended progress spine that can strand a Source/Post/gate |
| Inside a caller that **already** applied REFUSE/TERMINATE and is only protecting a secondary write | As the *only* handler for deterministic LLM/toolchain failures |
| With an explicit site string that names the posture (`… degrade:`) | Naked `except Exception` that logs and returns without naming posture |

### 2.7 Config honesty precondition (binding rule)

**Do not implement §2 attempt/exit binding until config truth-plane unification is done.**

Today: runtime `Config` / `env_bool` / `parse_scrape_*` keep defaults on malformed env; `Settings.strict_validate` fails loud on doctor only. Policy constants must read the unified plane.

---

## 3. Inventory by priority

### P0 — MISCLASSIFIED (messy behaviour)

| Cluster | Symbols | Current | Required |
|---------|---------|---------|----------|
| Responder context-limit / generic | `LlmResponder._answer_one` | log + leave pending (no attempt burn) | burn attempts → TERMINATE / DEGRADE-clean per §2.4 |
| Stale reseed vs doctor age | stale branch + request rewrite mtime | WARN defeated | attempts on logical key; do not age solely on resettable mtime |
| `cmd_run` gates blocked | `_run_once` | stderr + **exit 0** | **NONZERO** |
| Doctor progress WARNs | operational warn sensors | `ok=True, warn=True` | progress-blocking → unhealthy |
| Learn AuthError on run | `_run_once` learn | stderr + exit 0 | doctor-unhealthy at minimum |
| Config typo fail-open | `env_bool`, poster validate-or-default | warn + default | unified plane first |

### P1 — spine (correct enough; align vocabulary)

Pipeline `_quarantine` unit TERMINATE; publish `AuthError` halt; reconcile `_GATE_FAILOPEN`/`_GATE_PARK` (REFUSE vs TERMINATE); reconcile submitting-age unstrand; hashtag/vocab/corpora ticks as DEGRADE optional plane.

### P2 — BEST_EFFORT_OK (do not "fix")

`framing.*` miss → centered (cv2 absent + smart framing ON already REFUSE via `ToolchainMissingError`); clip/overlay/compose fail-open copies; caption scorer hints; digest/audit/daemon fingerprint; Studio `fail_open` view helpers. Optional: rename log sites to `… degrade:`.

### P3 — deferred to later remediation steps

TTL-less Home snapshots / fail-open-to-LIVE / `daemon_progress` unknown≠alive — derived-signal + machine-health contracts (steps 3–4). Do not encode new lies in this land.

---

## 4. Agent change plan

### Phase A — Preconditions
Confirm config-honesty PR status. Freeze this brief as the PR decision record.

### Phase B — Mechanism (lean)
1. Add `src/fanops/escalation.py`.
2. Retarget `responder` + `signals`; delete `pipeline_status` duplicate ceiling.
3. Route `LlmContextLimitError` + repeated generic failures through attempt burn.
4. `cli._run_once` / `cmd_run`: awaiting after converge → exit `1`; pause stays `0`.
5. Doctor: progress-blocking WARN → unhealthy.
6. Update `src/fanops/CLAUDE.md` fail-open bullet.
7. Write CI tests (no local pytest). `./scripts/check.sh`. Arch regen if scanned lines shift.

### Phase C — Non-goals
No mass except rewrite; no scrape budget edits; no full machine-health rewrite beyond doctor exit honesty.

### Phase D — Acceptance
- `rg -n '_GATE_DETERMINISTIC_MAX' src/fanops` → single home
- context-limit hits ceiling; gates_blocked → nonzero; doctor progress warn → unhealthy
- scrape try_cap / cooldown untouched

---

## 5. CLI Agent Execution Prompt (run only after operator APPROVE)

```
OBJECTIVE:
Land shared fail-open escalation policy (degrade/refuse/terminate/nonzero) on the unattended spine; eliminate per-subsystem attempt ceilings; do not bind behaviour to env until config honesty is merged.

CONTEXT SNAPSHOT (evidence only):
- fanops.errors.fail_open — log+swallow only; no escalation
- tests/test_swallow_ratchet.py — silent baseline empty; logged≠escalated
- responder.LlmResponder._answer_one — context_limit + bare Exception leave pending without attempt burn; _GATE_DETERMINISTIC_MAX=3 local
- pipeline_status._GATE_DETERMINISTIC_MAX — duplicate mirror
- signals producer imports responder ceiling
- cli._run_once — gates_blocked note with exit 0; learn/auth swallowed exit 0
- doctor operational warn sensors — ok=True,warn=True; report_is_healthy ignores warn
- config.env_bool / parse_scrape_* — runtime fail-open defaults vs Settings.strict_validate
- AST: 40 with fail_open; 325 broad except (272 logged_continue)
- Brief: .orchestration/briefs/fail-open-escalation-policy.md

CHANGE PLAN:
1) Verify config-honesty dependency; if not merged, do not flip cmd_run/doctor exits keyed on disputed env (module+tests OK).
2) Add fanops.escalation (posture + ATTEMPT_CEILING + decide + attempt wrappers over agentstep).
3) Retarget responder deterministic path + context_limit + signals; remove pipeline_status duplicate.
4) cmd_run: awaiting after converge → exit 1; pause stays 0.
5) doctor: progress-blocking warn → unhealthy.
6) Update CLAUDE.md fail-open bullet; write CI tests; check.sh; arch regen if needed.
7) Do not touch scrape budgets, framing leaf detectors, or mass except rewrites.

COMMANDS:
rg -n '_GATE_DETERMINISTIC_MAX|fail_open\(|gates_blocked|report_is_healthy' src/fanops
./scripts/check.sh
# NO local pytest

FILE EDITS (anchors by symbol):
- NEW src/fanops/escalation.py
- src/fanops/responder.py — LlmResponder._answer_one / _on_deterministic_fail / _GATE_DETERMINISTIC_MAX
- src/fanops/pipeline_status.py — remove duplicate ceiling
- src/fanops/signals.py — import escalation
- src/fanops/cli.py — _run_once / cmd_run exit mapping
- src/fanops/doctor.py — progress warn → unhealthy
- src/fanops/CLAUDE.md — fail-open house norm
- tests/test_responder.py (+ test_escalation / doctor exit tests as needed)

ROLLBACK:
git checkout -- <touched files>; rm src/fanops/escalation.py if added

ACCEPTANCE:
- Shared ATTEMPT_CEILING sole definition
- context_limit escalates by attempt N
- gates_blocked → nonzero
- doctor progress warn → nonzero
- check.sh pass; CI unit green on PR
- scrape try_cap / cooldown untouched

TELEMETRY: exit codes, check.sh output, rg counts for _GATE_DETERMINISTIC_MAX
RISKS: lane hot-files — stop if another lane owns a file; config honesty not merged → wrong binding; never force-push main
```

---

## 6. Gaps (operator confirmation)

1. **Config honesty status** — merged / in-flight / open? Behaviour flips wait.
2. **Exit codes** — draft uses `1` stuck-progress, `2` config/toolchain. Confirm launchd/cron assumptions.
3. **`LlmContextLimitError`** — draft = deterministic attempt burn (moments TERMINATE / enrichment DEGRADE-clean). Prefer shrink-and-REFUSE instead?
4. **Learn AuthError on run** — draft: keep run exit 0, force doctor-unhealthy. Or also nonzero run?

---

## 7. Resolved vs outstanding

| Item | State |
|------|-------|
| Inventory of `fail_open` + broad except density | Done (§1) |
| Shared posture definitions | Draft (§2) — awaiting APPROVE |
| Config honesty dependency | Outstanding |
| Code land Phase B | Blocked on APPROVE (+ config honesty) |
| Derived-signal / machine-health steps 3–4 | Out of scope here |
