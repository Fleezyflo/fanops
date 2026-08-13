# Brief — Retarget policy gates to honesty (observability contract, not call-name shape)

**Root issue #6 in remediation order — LAST.**  
Prerequisites: config truth (#1) → fail-open escalation (#2) → derived-signal primitives (#3) → machine-health / observability contract (#4) → control vs data plane untangle (#5).  
**Do not start product work here until #2 and #4 especially have landed.** Otherwise you “fix” the wrong proxy again: teach the AST more names, empty the swallow baseline louder, and declare honesty while the machine still fails open to green.

**Principle:** CI ratchets that claim to enforce **honesty**, **silence**, **prints**, **governance**, or **boundaries** must check **semantic properties of the foundation contracts** — severity present, UNKNOWN never mapped to healthy, degrade paths call the contract breadcrumb / escalation APIs, no new ad-hoc health channels — **not** “a function named `log` / `warning` / `debug` appears somewhere in the except-handler AST.”

**Grounded on:** current tree inventory (symbols primary, STD-DOC-01). Measured 2026-08-13. Not an implementation ticket.

---

## Three lenses

| Lens | Answer |
|---|---|
| Best practice | Policy gates prove *properties of the contracts they claim to protect*. Call-name / shape scanners are scaffolding during migration only; they never equal honesty. Negative controls plant *semantic* lies (log+continue without `decide` / without health breadcrumb), not missing identifier strings |
| Root cause | Degradation-honesty waves taught `_handler_non_silent` more call names (`log`, `debug`, `warning`, house helpers) until `_baseline_silent_swallows = {}`. Empty baseline + folklore = “no silent failures.” Reality: **322** broad `except Exception`/`BaseException` sites; **0** silent by the ratchet’s definition; **~78** credited *only* by weak names (`warning` ×77, `log` ×1). Breadcrumb ≠ escalate ≠ health. `test_escalation_spine_ratchet` already knows this on a denylist — the swallow ratchet still does not |
| Leanest | Do **not** invent a new AST framework. Retarget swallow (+ arch mirror) to require **named semantic APIs** from #2/#4 once they exist; keep print / PostState / lane / side-effect / arch budgets as what they already are (routing, ownership, blast radius — **not** honesty). Migrate with shrink-only / dual-gate so CI does not false-red the whole tree overnight |

---

## KEEP — prerequisites (must land first)

This brief **depends on roots #2 and #4 especially**. #1, #3, #5 are required so the *symbols this ratchet names* are not lies.

| Prerequisite | Brief | Must deliver for *this* contract’s ratchets to be meaningful |
|---|---|---|
| **#1 Config truth plane** | [config-truth-plane.md](config-truth-plane.md) | One registration + parsers + Config façade. Gates that assert “misconfig → NONZERO / FAIL” must read the same plane doctor validates — else ratchet greens while runtime substitutes |
| **#2 Fail-open escalation** | [fail-open-escalation-policy.md](fail-open-escalation-policy.md) / [fail-open-escalation-exec.md](fail-open-escalation-exec.md) | `fanops.escalation` with `EscalationPosture`, `decide`, shared attempt ceiling, spine wiring. **`errors.fail_open` remains log+swallow** — breadcrumb ≠ escalate. Spine ratchet already requires `decide` / honest `fail_open` / escaping `raise` on a denylist; this brief **generalizes that law** off call-name theater |
| **#3 Derived-signal primitives** | [derived-signal-primitives.md](derived-signal-primitives.md) (+ [derived-signal-close-1-2.md](derived-signal-close-1-2.md)) | TTL / logical-clock age / no fail-open-to-LIVE. Close-out must finish before #4/#6 treat UNKNOWN as law |
| **#4 Machine-health contract** | [machine-health-contract.md](machine-health-contract.md) | Severity enum; `build_health_report` sole constructor; UNKNOWN→FAIL; channel allowlist. **This brief’s new honesty gates attach to those symbols** |
| **#5 Control vs data plane** | [control-data-plane-untangle.md](control-data-plane-untangle.md) | Observe paths stop write-on-read. Prevents honesty ratchets from blessing CP dual-write “health refresh” as contract compliance |

Do **not** expand this brief into implementing 1–5. If a WP needs a missing API, mark **BLOCKED** and stop.

Do **not** start here:

- Teaching `_handler_non_silent` more names (`trace`, `logger`, …) as a “fix”
- Lowering / emptying baselines without semantic proof
- Mass-rewriting ~322 broad excepts to call a new helper just to satisfy AST
- New monitoring SaaS / second policy engine
- Editing `.agents/lanes.json` to steal hot files

---

## What “honesty” means (post-#2 + #4)

Honesty is **not** “the except body mentions a logging identifier.”

Once escalation (#2) and machine-health (#4) exist, a failure site is **honest** iff all of the following that apply are true:

| Property | Semantic requirement (illustrative symbols — lock in WP0 against landed APIs) |
|---|---|
| **Posture named** | Progress-critical / control-plane failure calls `escalation.decide` (or a thin wrapper that itself calls `decide`, proven like `_on_deterministic_fail`) — not merely `logging.warning` |
| **Breadcrumb contracted** | Observability / optional enrichment uses `errors.fail_open` *or* the health-contract breadcrumb API (e.g. `health_model.record_degrade` / check contribution with **severity**) — never a bare continue with a debug print |
| **Severity present** | Any contribution to machine health carries severity from #4’s enum; **no new** `ok=True` + optional `warn` without severity |
| **UNKNOWN never green** | Probe/snapshot/ledger miss maps to `UNKNOWN` → overall unhealthy for required signals; never strip zeros / LIVE / `"live"` label |
| **Channel allowlist** | Operator health surfaces come only from `build_health_report` / named projectors; new ad-hoc health printers fail CI |
| **Exit tracks severity** | CLI verbs that claim readiness (`cmd_doctor`, `cmd_health`, `cmd_init`, `cmd_autopilot` per #4) exit non-zero when report unhealthy |
| **Surfacing channel** | Structured operator trail uses house `get_logger` → `run.log` (or the contract’s designated sink) — stdlib `logging.getLogger(...).debug` alone is **not** honesty |

**Explicit non-equivalence (already named in-tree, still not enforced by swallow ratchet):**

- `docs/CODEMAPS/anomalies.md`: **`logging ≠ surfacing`**
- [fail-open-escalation-policy.md](fail-open-escalation-policy.md): **breadcrumb ≠ escalation**; empty swallow baseline listed as a measured signal that *does not* mean escalated
- `docs/ENGINEERING_STANDARDS.md` STD-ERR residual: ratchet accepts stdlib `logging`; surfacing remains review judgment — **that residual is the defect this brief closes**

---

## Current wiring (the theater, named)

```
                    broad except Exception / BaseException  (~322 sites)
                                    |
              +---------------------+---------------------+
              |                                           |
   _handler_non_silent (call-name)              real outcomes (unmeasured by swallow)
   credits if AST sees ANY of:                  escalate? decide? severity? UNKNOWN?
     fail_open, get_logger, getLogger,            exit flip? health channel?
     warning|debug|info|error|…|log,
     _quarantine, _capture_poll_exc, raise
              |
              v
   silent count == 0  →  _baseline_silent_swallows = {}
              |
              v
   folklore: "no silent failures" / Wave 2c "baseline empty"
              |
              +---- tools/arch/extract._handler_non_silent  (byte-same theater)
              +---- derived/ratchets.json measured.silent_broad_except_by_file = {}
              +---- IMPL-007 copies print budgets; swallow "unsupported" when baseline unparsed
```

**Parallel gate that already refuses call-name theater (denylist only):**  
`tests/test_escalation_spine_ratchet.py` — `_handler_escalates` rejects log-only; rejects `with fail_open: raise` then continue; requires `decide` / `_on_deterministic_fail` / escaping `raise` / honest sole-body `fail_open`. **Files:** `responder.py`, `signals.py`, `cli.py`, `doctor.py`, `pipeline_status.py`, `escalation.py`.

---

## Exhaustive ratchet catalog

Classification key for **Disposition under honesty contract**:

| Tag | Meaning |
|---|---|
| **RETARGET** | Claims honesty / silence / fail-open discipline; must move to semantic properties |
| **KEEP** | Correct proxy for its real job; do not dress it as honesty |
| **NARROW** | Keep mechanism; shrink scope / strengthen negative controls |
| **EXTEND** | Keep; grow denylist or attach to #4 channel list *after* APIs exist |
| **DELETE** | Pure theater or superseded — none of today’s gates earn DELETE without replacement |
| **OWNERSHIP** | Lane / PR collision — not honesty |
| **FOLKLORE** | Docs that over-claim what a gate proves |

### A. Honesty / silence / fail-open claims (core of this brief)

| Gate | Symbols / artifact | Claims to prove | Actually measures (proxy) | Disposition |
|---|---|---|---|---|
| Swallow AST ratchet | `tests/test_swallow_ratchet.py` — `_handler_non_silent`, `_is_broad_except`, `_scan_silent_swallows`, `_baseline_silent_swallows`, `test_silent_swallow_count_does_not_exceed_baseline` | No new *silent* broad `except Exception` / `BaseException`; Wave history claims spine “breadcrumbed to 0” | Presence of **any** credited call name / `Raise` / `with fail_open` anywhere in handler AST (walk includes nested calls). Baseline today: **`{}`** | **RETARGET** → semantic allowlist (escalate / health breadcrumb / typed BEST_EFFORT registry) |
| Arch swallow mirror | `tools/arch/extract.py` — `_call_name`, `_handler_non_silent`, `_is_broad_except`; `ModuleFacts.silent_broad_excepts` | Same measurement for derived census | **Exact mirror** of swallow call-name set (comment: ratchet tests are canonical) | **RETARGET** in lockstep with the test — never diverge |
| Arch declared/measured ratchets | `tools/arch/generate.py` — `_declared_ratchets`, emit `ratchets`; `.reports/architecture/derived/ratchets.json` | Third-party check of CI budgets | Parses `_CLI_PRINT_COUNT` / zero-print modules; swallow baseline often **`unsupported` / unparsed** while `measured.silent_broad_except_by_file: {}` | **RETARGET** measured fields to semantic census; keep “test is canonical” rule |
| IMPL-007 | `tools/arch/policy.py` `_ratchet_drift`; RULE `IMPL-007`; `test_arch_governance` | Contract copy of ratchet budgets matches tests | Print equality + prose scrape of `_CLI_PRINT_COUNT`; swallow half weak | **KEEP** mechanism; **RETARGET** what budgets *mean* when honesty budgets replace call-name |
| Escalation spine ratchet | `tests/test_escalation_spine_ratchet.py` — `_handler_escalates`, `_SPINE`, `_ESCALATE_CALLS`, theatre negative controls | Spine broad-except must escalate; log-only ≠ OK; fail_open-raise-then-continue ≠ OK | Call/shape of `decide` / `_on_deterministic_fail` / `fail_open` / `Raise` on **denylist files only** | **KEEP** + **EXTEND** after #2 fully lands; model for whole-tree honesty (not delete swallow without replacement) |
| ENGINEERING residual | `docs/ENGINEERING_STANDARDS.md` §6 “No new silent broad except” | Craft rule backed by swallow ratchet | Admits residual: accepts stdlib logging | **FOLKLORE → RETARGET** prose when gate retargets |
| Anomalies / CODEMAPS | `docs/CODEMAPS/anomalies.md`, C2–C6 traces | “No unlogged swallow”, “logs before continuing” | Human census; often equates log with fixed | **FOLKLORE** — purge overclaims; point to #2/#4/#6 |
| Fail-open brief evidence row | `fail-open-escalation-policy.md` §1 empty baseline | Lists empty baseline as measured fact | Correct as *measurement*; dangerous if read as *honesty achieved* | **FOLKLORE** annotate: empty = call-name theater closed, not honesty |

### B. Routing / I/O shape (not honesty — keep)

| Gate | Symbols | Claims | Proxy | Disposition |
|---|---|---|---|---|
| Internal prints routed | `tests/test_internal_prints_routed.py` — `_INTERNAL_MODULES`, `_print_call_nodes`, `_CLI_PRINT_COUNT`, `test_internal_modules_have_no_print_calls`, `test_cli_print_count_unchanged` | Internal modules never `print()`; CLI print count exact equality (GB-6) | Bare `print(...)` Call nodes with `Name id == "print"`; equality on `cli.py` | **KEEP** — observability *routing*, not honesty. Note: `sys.stdout.write` / f-string logging bypasses. Do **not** retarget into “honesty” |
| Print measurement in arch | `extract` print_calls; `derived/ratchets.json` `print_calls_by_module`; impact bumps | Mirror of print ratchet | Same | **KEEP** |

### C. Single-owner / census honesty (adjacent — keep; do not confuse)

| Gate | Symbols | Claims | Proxy | Disposition |
|---|---|---|---|---|
| PostState census ratchet | `tests/test_post_state_census_ratchet.py` — `_call_is_hand_rolled_post_state_census`, `_EXEMPT=ledger.py`, negative plant | Zero hand-rolled `Counter(… .state …)` over posts outside `Ledger.state_histogram` | AST shape of Counter + posts iterable + `.state` | **KEEP** — prevents multi-channel *state census* disagreement (cousin of #4 channel disease). Not exception honesty |
| Public URL guard | `tests/test_public_url_guard.py` + `text.safe_public_url` | No non-https garbage persisted as live URL | Runtime predicate tests | **KEEP** — data honesty; not AST call-name |
| No ghosts | `tests/test_no_ghosts.py` | Deleted symbols stay deleted | String/AST presence of ghost names | **KEEP** — deletion honesty |
| Skill drift | `tests/test_skill_drift.py` | SKILL.md DRIFT-GUARD matches code | Regex vs `_ARABIC` / `_hook_spec` | **KEEP** — doc↔code |

### D. Architecture / blast-radius governance (keep; not exception honesty)

| Gate | Symbols | Claims | Proxy | Disposition |
|---|---|---|---|---|
| Arch governance suite | `tests/test_arch_governance.py` + `tools/arch/{policy,drift,selftest,registries,impact}` | Derived byte-identical; RULES reachable; unknowns ceiling; deep gate selection | Full ARCH-001…010, IMPL-001…010 | **KEEP**; add **new** honesty rules only after #4 (channel allowlist already sketched in machine-health WP4) — do not overload ARCH-008 |
| ARCH-008 side-effect ratchet | `governance/side_effect_ratchet.json`; `_side_effect_ratchet` | Side-effect site ceilings + module allowlists | AST census of subprocess/network/ledger txn/… | **KEEP** — blast radius, not silence |
| ARCH-003 env surface | policy env checks | Declared env = code reads | getenv census vs KB/docs | **KEEP** — feeds #1; not swallow |
| ARCH-005 unknowns ceiling | `registries.unknown_growth` | UNKNOWNs may not grow quietly | Count vs ceiling | **KEEP** — meta-honesty of architecture understanding |
| Governance tombstone | `tests/test_governance_tombstone.py` | Prose governance namespaces stay dead; `ENFORCEMENT.md` exists | Path existence | **KEEP** — anti-theatre for *docs*; ironic cousin of this brief |
| Arch merge regen | `tests/test_arch_merge_regen.py` | Merge regenerates derived | Hook/merge contract | **KEEP** |

### E. CI lane / workflow hygiene (keep)

| Gate | Symbols | Claims | Proxy | Disposition |
|---|---|---|---|---|
| Integration marker guard | `tests/test_integration_marker_guard.py` | Every `tests/integration/test_*.py` carries `integration` marker | AST of pytestmark / decorators / aliases | **KEEP** — prevents false unit-lane passes; **not** product honesty |
| CI log producer guards | `tests/test_ci_log_producer_guards.py` | Log consumers gate on producer allowlist; e2e lock pins | YAML + lockfile parse | **KEEP** — CI surface honesty |

### F. Ownership (classify correctly — NOT honesty)

| Gate | Symbols | Claims | Proxy | Disposition |
|---|---|---|---|---|
| Lane guard | `scripts/lane_guard.py`; `tests/test_lane_guard.py`; `.agents/lanes.json` | Disjoint hot-file ownership | Branch→lane→hot_files stray detection | **OWNERSHIP / KEEP** — never retarget as honesty |
| PR collision guard | `scripts/pr_collision_guard.py`; `tests/test_pr_collision_guard.py` | Two PRs do not share hot files | GitHub PR file intersection | **OWNERSHIP / KEEP** |

### G. Related behavioral tests (not AST ratchets — inventory for boundary)

These prove *behaviors* and must remain; they are **not** substitutes for retargeting swallow:

| Area | Examples | Role vs this brief |
|---|---|---|
| Escalation / responder | `tests/test_responder.py`, spine negative controls inside escalation ratchet | Prove #2 semantics; honesty ratchet should *call the same APIs* |
| Health / doctor | `tests/test_health*.py`, `tests/test_doctor.py` | Prove #4; channel/exit honesty gates live there + new AST allowlist |
| Variant amplify fail-safe | `tests/test_variant_amplify.py` (log on swallowed pass) | Behavioral; do not weaken to call-name |
| Foundation-honesty waves | `tests/test_postiz_trust_boundary.py`, personas malformed row, etc. | Historical naming; not a global ratchet |

### Catalog size (this inventory)

| Bucket | Count of distinct enforcement surfaces listed above |
|---|---|
| Core honesty/silence (A) | **8** (swallow test, extract mirror, generate/derived, IMPL-007, spine ratchet, + 3 folklore anchors) |
| Routing / census / data (B–C) | **6** |
| Arch / blast / tombstone (D) | **7** |
| CI hygiene (E) | **2** |
| Ownership (F) | **2** |
| **Total catalog rows** | **≥25** enforcement surfaces + related behavioral families |

---

## Proxy failure modes — swallow ratchet alone (≥5)

Measured context: **322** broad handlers; **0** “silent”; **78** weak-only credits (**77** `warning`, **1** `log`). Top weak-only files include `framing.py` (9), `health_model.py` (8), `digest.py` (7), `daemon.py` (6), `studio/views_results.py` (5).

### FM1 — Weak-name credit = “non-silent”

**Proxy:** `_handler_non_silent` returns True if call attr/id ∈ `{warning, debug, info, log, …}`.  
**Lie:** A progress-blocking or health-relevant failure that only `_log.warning(...)` then continues / returns green sentinel is **ratchet-green** and **operator-blind** at exit.  
**Evidence:** ~78 weak-only sites; `health_model` alone has eight `warning`-only broad excepts — the typed health owner’s own degrade paths are call-name “honest” while #4 still documents soft-green elsewhere.  
**Game:** Prefer `warning` over `error`; never call `decide`; never stamp severity.

### FM2 — Logger *construction* credits the handler

**Proxy:** AST walk credits `getLogger` / `get_logger` as Call names anywhere in the handler body.  
**Lie:** `logging.getLogger("fanops.x").debug(...)` credits **both** `getLogger` (in the non-silent set) **and** `debug`. Even constructing a logger without a structured escalate/health event can credit the handler.  
**Evidence:** `discover.candidate_meta` uses `logging.getLogger(...).debug(...)` — documented in anomalies as debug-level fail-soft; still non-silent to the ratchet. `doctor` mixes `decide` + `getLogger(...).debug` — spine OK where denylisted; swallow would green on debug alone outside spine.  
**Game:** Construct a logger; skip escalate; skip health breadcrumb.

### FM3 — `with fail_open` / bare `fail_open` Call = done

**Proxy:** Any `With` whose context Call is named `fail_open`, or any Call named `fail_open`, marks non-silent.  
**Lie:** `errors.fail_open` **logs and swallows** — never escalates, never stamps ledger, never flips exit ([fail-open-escalation-policy.md](fail-open-escalation-policy.md)). Spine ratchet already rejects theatre `with fail_open: raise` then continue; **swallow ratchet does not**.  
**Game:** Wrap a no-op / re-raise-inside-fail_open and continue; baseline stays empty.

### FM4 — Empty baseline `{}` read as “no silent failures”

**Proxy:** `test_silent_swallow_count_does_not_exceed_baseline` with `_baseline_silent_swallows() -> {}` asserts no file has a positive silent count.  
**Lie:** Vacuous success of a **definition** of silence, not absence of fail-open-forever / soft-green / infinite-pending. Wave 2c docstring in `_baseline_silent_swallows` narrates “breadcrumbed the remainder to 0” and “taught” helpers — teaching the **scanner**, not the **machine**.  
**Evidence:** Folklore in ENGINEERING_STANDARDS (“No new silent broad except”), CODEMAPS “no unlogged swallow,” fail-open brief row “Silent swallow ratchet baseline: empty `{}`.”  
**Game:** Add `warning` to any new broad except; never raise baseline; claim honesty in the PR.

### FM5 — Teaching the allowlist is the “fix”

**Proxy:** Historical remediation path: add `_quarantine`, `log`, `_capture_poll_exc` to `_handler_non_silent` so counts drop without changing control-plane outcomes.  
**Lie:** The ratchet’s green path becomes “spell a blessed name,” documented in MOL-909 discovery notes (`log = get_logger(cfg); log(...)` once unrecognized — fix was name shape).  
**Game:** Introduce `_breadcrumb(exc)` that only prints; teach AST; ship.  
**Anti-pattern for this brief:** any WP that only extends the name frozenset.

### FM6 — House helpers without semantic proof

**Proxy:** `_quarantine`, `_capture_poll_exc` are globally non-silent. Spine separately asserts `_on_deterministic_fail` calls `decide`.  
**Lie:** A new helper can be taught to swallow without a negative control that the helper still escalates / records health. Without spine-style wrapper proof, teaching is soft-open.  
**Game:** Add `_note_exc` that logs at debug; add to frozenset; baseline empty forever.

### FM7 — Scope holes (not Exception / nested / pass)

**Proxy:** Only `ExceptHandler` with type `Exception`/`BaseException` (or tuple containing them). Bare `except:` excluded. Narrow types ignored. Nested function bodies skipped when walking.  
**Lie:** `except OSError: pass` / nested silent handlers remain invisible; silent OSError on wipe/chmod paths historically debated in anomalies.  
**Game:** Narrow the type name while still swallowing the real failure class; or nest the handler.

### FM8 — Arch mirror doubles confidence without doubling truth

**Proxy:** `extract._handler_non_silent` duplicates the test; `derived/ratchets.json` shows `silent_broad_except_by_file: {}` and often `unsupported` for unparsed baseline.  
**Lie:** Two green artifacts (`test` + `derived`) look like independent confirmation. They are **one proxy copied**. IMPL-007 cannot fully cross-check empty dict when baseline parse is unsupported.  
**Game:** None needed — theater is automatic.

*(FM1–FM5 are the mandatory ≥5; FM6–FM8 are structural cousins that land in the same retarget.)*

---

## What good enforcement looks like

Design against **future** #2/#4 symbols. Do not implement those modules in this wave — only consume them once landed. Names below are **targets for WP0 lock**; rename to whatever #2/#4 actually ship, then freeze.

### Semantic allowlist (replace call-name frozenset)

A broad except is **honesty-OK** only if the handler body satisfies **one** classified path:

| Class | Required AST/semantic proof | Typical use |
|---|---|---|
| **ESCALATE** | Call to `decide` **or** wrapper proven to call `decide` (pattern: `test_on_deterministic_fail_calls_decide`) **or** escaping `Raise` not solely inside swallowing `fail_open` | Spine / control plane |
| **NAMED_DEGRADE** | `with fail_open(...)` as sole escalate-grade body **or** call to `health_model.record_degrade` / `HealthReport` contribution API with severity ∈ {WARN, DEGRADED, … per #4} | Observability / optional enrichment |
| **HEALTH_CONTRIB** | Calls into `build_health_report` sensors that set severity FAIL/UNKNOWN — not `ok=True, warn=True` | Doctor / health assembly |
| **BEST_EFFORT_REGISTRY** | Site listed in a **declared** registry file (e.g. `governance/best_effort_excepts.json`) with rationale + expiry — shrink-only; negative control plants unlisted site → red | Media leaf detectors, certifi env decorate, etc. |
| **FORBIDDEN** | Log-only / weak-name-only / getLogger-construction-only / theatre fail_open+continue | Must fail CI outside registry |

### Proposed APIs the ratchet would require (consume, don’t invent here)

```
# from #2 (already sketched)
fanops.escalation.decide(failure_class, attempts) -> EscalationPosture
fanops.escalation.record_attempt / clear_attempts

# from #4 (illustrative — lock to landed names)
fanops.health_model.record_degrade(scope, reason, *, severity, observed_at=…)
fanops.health_model.build_health_report(...)   # sole constructor
# projectors only — ban new assembly

# breadcrumb (existing — insufficient alone for ESCALATE)
fanops.errors.fail_open(...)
fanops.log.get_logger(cfg)  # structured run.log — necessary for NAMED_DEGRADE surfacing, not sufficient for ESCALATE
```

### Channel / soft-lie gates (owned jointly with #4 WP4)

| Gate | Mechanism |
|---|---|
| Health channel allowlist | Registry or AST: Studio/CLI health printers may only call `build_health_report` / named projectors |
| Severity shape | Forbid new check dicts without `severity`; baseline shrink-only for legacy `warn` |
| UNKNOWN presentation | Tests (behavioral) + optional AST ban on strip fail-open-to-zeros patterns once #3/#4 land |
| Exit honesty | Behavioral tests on `cmd_doctor` / `cmd_health` / `cmd_autopilot` — not print-count |

### Negative controls (mandatory — copy spine’s discipline)

Planted sources that **must** fail the new honesty finder:

1. `except Exception: warning(...); return None` — weak-only  
2. `except Exception: get_logger(cfg); return None` — construction without escalate  
3. `except Exception:\n with fail_open(...): raise\n continue` — theatre (already in spine)  
4. New doctor check `{ok: True, warn: True}` without severity — soft-lie  
5. New function assembling health outside allowlist  

Planted sources that **must** pass:

1. `decide(...); return` / `raise` after decide  
2. Sole-body honest `fail_open` for observability class  
3. `record_degrade(..., severity=...)` then continue  
4. BEST_EFFORT registry-listed site with narrow rationale  

---

## Migration path (no big-bang false-red)

**Goal:** move from call-name allowlist → semantic allowlist without turning 322 sites red overnight and teaching panic-fixes.

### Phase M0 — Freeze & dual measure (read-only + tests written, CI)

1. Keep today’s swallow test **green** (call-name) as `test_silent_swallow_count_does_not_exceed_baseline` until M3.  
2. Add **shadow** census (non-blocking or warning job): count weak-only / log-only / no-decide spine-adjacent. Publish numbers in PR body / derived optional field — **do not** claim honesty.  
3. Lock symbol names against landed #2/#4 APIs in WP0 inventory.

### Phase M1 — Spine is law (depends #2)

1. Keep/extend `test_escalation_spine_ratchet` as the **authoritative** escalate proof for control-plane files.  
2. Expand `_SPINE` only when #2 retargets those modules — not speculative framing/clip.  
3. Swallow ratchet **gains** a second test: `test_spine_files_match_escalation_ratchet` (no divergence of definitions).

### Phase M2 — Semantic credit for new code only

1. Introduce `_handler_honest(body, *, path) -> bool` implementing semantic classes.  
2. Ratchet rule: **new** files / **new** broad-except growth must be honest (count of non-honest ≤ `baseline_non_honest`).  
3. Seed `baseline_non_honest` from shadow census — **shrink-only**. Never teach new weak names.

### Phase M3 — Retire call-name as authority

1. Flip: semantic test becomes blocking; call-name test deleted or demoted to “legacy residual inside baseline_non_honest.”  
2. Empty `{}` **forbidden** as a claim of honesty in docs — replace with “N non-honest sites remaining (shrink-only).”  
3. Retarget `extract._handler_non_silent` → `_handler_honest` mirror; regen derived; update IMPL-007 budgets to semantic fields.  
4. Folklore purge: ENGINEERING_STANDARDS, anomalies intro, CODEMAPS overclaims, fail-open evidence wording.

### Phase M4 — Health-channel honesty (#4)

1. Land channel allowlist + severity shape ratchet (machine-health WP4) — **this brief’s acceptance includes wiring those gates so they cannot regress to call-name**.  
2. Ban new soft-lie shapes; UNKNOWN behavioral tests.

**Hard rule during migration:** lowering `baseline_non_honest` requires either (a) site now calls semantic API, or (b) site moved to BEST_EFFORT registry with review — never “added warning()”.

---

## Empty `_baseline_silent_swallows = {}` — the loudest lie

| Reading | Verdict |
|---|---|
| “CI proves zero silent broad excepts under the call-name definition” | **True** (tautology of the scanner) |
| “FanOps has no silent failures / degradation is honest” | **False** |
| “Logged continue is fine forever” | **False** — infinite-pending gates, soft-green doctor, strip zeros (owned by #2/#4/#3) |
| “Teaching `_quarantine` / `log` fixed honesty” | **False** — fixed the **proxy** |
| Correct operator sentence | “Call-name silence is closed; honesty ratchets are remediation #6 after escalation + health contract.” |

Any PR that celebrates an empty swallow baseline as honesty **fails this brief’s folklore acceptance**.

---

## Defect classes this brief owns

### D1 — Call-name theater as policy  
Blessed identifiers substitute for escalate / severity / UNKNOWN law.

### D2 — Vacuous empty baseline  
`{}` manufactures confidence; arch derived mirrors it.

### D3 — Dual scanners, one proxy  
Test + `extract` + `ratchets.json` look like triangulation; they are copies.

### D4 — Folklore overclaim  
ENGINEERING_STANDARDS / CODEMAPS / anomalies / briefs cite the ratchet as honesty.

### D5 — Fix-by-teaching  
Allowlist growth as remediation path.

### D6 — Category errors  
Treating lane_guard / print equality / integration markers as honesty work (or retargeting them).

---

## Explicit non-goals

| Anti-goal | Why |
|---|---|
| Implement #1–#5 here | Wrong order |
| Mass rewrite ~322 excepts | Owned by #2 lean spine-first; this brief retargets **gates** |
| Add more names to `_handler_non_silent` | FM5 |
| Delete swallow ratchet with no semantic replacement | Opens real silence |
| Retarget lane_guard / print GB-6 into “honesty” | Category error |
| New policy microservice / OTel | Bloat |
| Local `pytest` | AGENTS.md |
| Edit `lanes.json` | Stop and report |

---

## Work packages (ordered inside this brief)

### WP0 — Inventory freeze + prereq gate (read-only)

- Confirm #1–#5 status; **BLOCKED** list if #2/#4 APIs missing.  
- Freeze catalog table (this brief) + re-measure weak-only / silent counts.  
- Lock semantic symbol names to **landed** APIs only.  
- No product code.

### WP1 — Negative controls + shadow census

- Add planted semantic-lie tests (may start as `xfail` / non-blocking marker — document).  
- Shadow count of non-honest broad excepts; seed shrink-only baseline file.  
- Do **not** flip swallow yet.

### WP2 — Align spine + swallow definitions

- Single shared module for escalate-OK detection used by spine ratchet (extract from `test_escalation_spine_ratchet` into e.g. `tools/arch/honesty.py` or `tests/_honesty_ast.py` — lean).  
- Swallow continues call-name until WP3; spine remains blocking.

### WP3 — Semantic ratchet for new sites (depends #2)

- `_handler_honest`; new-site / growth ratchet blocking.  
- BEST_EFFORT registry (declared, shrink-friendly).  
- Forbid allowlist-name teaching in review (adversary ban).

### WP4 — Retire call-name authority + arch mirror (depends #2; prefers #4)

- Flip blocking semantic test; delete or demote call-name.  
- Retarget `extract` / `generate` / `derived/ratchets.json` / IMPL-007 fields.  
- Folklore purge (ENGINEERING_STANDARDS residual, anomalies overclaim, brief wording).

### WP5 — Health-channel / severity shape gates (depends #4)

- Implement or finish machine-health WP4 channel allowlist + soft-lie ban **as honesty contract enforcement** (coordinate — do not fork a second allowlist).  
- Behavioral UNKNOWN/exit tests if not already landed.

### WP6 — Gate

- `./scripts/check.sh`; `python -m tools.arch regen` if scanned lines / derived change; PR on appropriate lane; no local pytest.

---

## Acceptance checklist

- [ ] Prerequisites #2 and #4 delivered (or every dependent WP marked BLOCKED with symbol evidence); #1/#3/#5 status recorded  
- [ ] Catalog dispositions applied: swallow **RETARGET**ed; lane/print/PostState/integration **KEEP** with correct classification  
- [ ] ≥5 swallow proxy failure modes documented in the landing PR (cite this brief)  
- [ ] Semantic honesty definition locked to landed `decide` / health breadcrumb / severity APIs  
- [ ] Negative controls plant weak-only / theatre fail_open / soft-lie check shapes  
- [ ] Migration: no big-bang false-red; shrink-only non-honest baseline; **no** new call-name teachings  
- [ ] Empty `{}` no longer narrated as “no silent failures” in standards/docs touched by folklore purge  
- [ ] `extract` mirror matches new definition; derived regen committed  
- [ ] Health channel allowlist + severity shape enforced (via #4 or WP5)  
- [ ] Adversary PASS: no FM5 teaching, no ownership-as-honesty, no implementation of #1–#5 scope  
- [ ] No product behavior change beyond what gates require (prefer gate-only PRs)

---

## Lane / blast radius

| Path | Note |
|---|---|
| `tests/test_swallow_ratchet.py`, `tests/test_escalation_spine_ratchet.py` | Primary |
| `tools/arch/extract.py`, `generate.py`, `policy.py` (IMPL-007) | Mirror + budgets |
| `.reports/architecture/derived/ratchets.json` | Regen only |
| `governance/best_effort_excepts.json` (new, if used) | DECLARED |
| `docs/ENGINEERING_STANDARDS.md`, `docs/CODEMAPS/anomalies.md` | Folklore purge |
| `src/fanops/**` | **Avoid** in gate-only waves; if teaching sites to call `decide`/`record_degrade`, that is #2/#4 work — stop and reassign |
| Lane ownership | Prefer `rfd/`; tests/tools generally unrestricted — still respect hot files if a WP touches product |

---

## Risks

| Risk | Mitigation |
|---|---|
| Flip semantic before #2/#4 APIs exist | WP0 BLOCKED; dual-gate migration |
| False-red 78+ weak-only sites | Shrink-only baseline; BEST_EFFORT registry for true leaves |
| Adversary accepts “add warning” PR | Hard ban in EXEC; negative control |
| Divergent spine vs swallow definitions | Shared helper module (WP2) |
| Scope creep into mass except rewrite | Gate-only; product fixes belong in #2/#4 PRs |
| Celebrating empty baseline again | Folklore acceptance line |

---

## Anti-patterns (instant reject)

1. Teaching AST more names (`log`, `debug`, `trace`, …) as the fix  
2. Lowering / emptying baselines without semantic proof  
3. New AST gates that only measure call-name / print-shape again and call it “honesty”  
4. Mass “add a log everywhere”  
5. Claiming empty `_baseline_silent_swallows` proves degradation honesty  
6. Retargeting `lane_guard` / integration markers into this program  
7. Implementing config / escalation / health / plane untangle “while we’re here”

---

## One-sentence objective

**Retarget honesty/silence CI gates from call-name AST theater to semantic properties of the escalation + machine-health contracts — and stop reading an empty swallow baseline as proof that FanOps cannot fail silently.**
