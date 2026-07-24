# CLEANUP ROUTE — governance de-theatre (executor copy of the approved plan)

This file mirrors the operator-approved execution plan (audited + reverified against HEAD `05bc960`,
2026-07-24). It is cleanup scaffolding: tracked by PR-3's archive commit, deleted by PR-4. If an
executing session's own re-check contradicts a recorded fact below, that step HALTS for
re-derivation — no improvisation. The disposition ledger is `docs/reconciliation/TRIAGE.md`
(76 rows, all terminal; PR-3's precondition is a diff against it).

## Rules of engagement (every session)

- One PR at a time, strictly sequential; the operator merges each via merge_approved before the
  next starts (stacked PRs break on squash-merge here).
- Start every session: `git fetch origin && git checkout main && git pull --ff-only`.
- NEVER run pytest locally (CI-only). Allowed locally: `./scripts/check.sh`,
  `python -m tools.arch selftest|ci`, `python -m tools.ci selftest`, `ruff check .`.
- No mass reformat — if ECC's formatter hook fires on a `.py`, revert the hunk, add the hook to
  `ECC_DISABLED_HOOKS` for the session, note it in the PR body.
- No new docs beyond `docs/ENFORCEMENT.md`. No new gates beyond the tombstone test.
- PR titles carry `(Unit: <slug>)`.
- Verify-before-delete, always: `grep -rn "<path-or-symbol>" src tools scripts .github tests` must
  be empty (or only inside files deleted in the same PR) before a `git rm`. READ every file before
  deleting it.
- Never `git reset --hard`, never `git clean` (untracked `docs/reconciliation/` files are the
  working ledger until PR-3 tracks them). Live `~/FanOps` tree untouched — nothing here changes
  runtime behavior.
- GateGuard fact-forcing blocks (first Bash/Write per session): answer the facts, retry. Friction,
  not failure.
- Exit-code baselines (recorded 2026-07-24; only NEW failures block anywhere on this route):
  `python -m tools.arch ci` exits 1 with EXACTLY one BLOCKING finding — IMPL-007 on
  `docs/reconciliation/02_REPOSITORY_REALITY_AND_INTEGRITY.md` (its stale `_CLI_PRINT_COUNT` copy: 165, vs test/measured 168) —
  until PR-3's archive commit fixes that line. `docs/reconciliation/verify_theatre.py` exits 1 with
  EXACTLY 19 read_sites(+1) drift rows (cause: the untracked post-freeze `docs/ENFORCEMENT.md`
  draft; the verifier greps `docs/` excluding only `reconciliation/`). Any OTHER failure or drift
  row = halt.

## Census ground truth (re-aggregated 2026-07-24)

`docs/reconciliation/theatre.jsonl`: 178 rows = A/enforcer-index 48 + B/ci-gate 21 + C/engine 32 +
D/toggle 69 + E/cold-start 8. Governance scope (A/B/C): **101 rows = REAL 25 + non-REAL 76**
(INERT 29 · DECORATIVE 25 · UNVERIFIED 16 · UNFALSIFIABLE 5 · SELF-REF 1). D/E (77 rows) are
product-runtime — out of scope, untouched. Dispositions: `TRIAGE.md` (zero COMPILE rows — the only
new test on this route is the tombstone; no `cleanup/2b` PR exists).

## PR-1 `cleanup/1-contract-engine`

- Delete `tools/contract/` (whole package), `tests/test_contract_compiler.py`,
  `tests/fixtures/contracts/` (exactly 3 fixture files).
- `tests/test_ci_e2e_trigger.py`: **NO edit** (its only `tools/contract` hit is docstring prose at
  line 44; no import; the file dies in PR-2). The trigger script must NOT be touched in this PR —
  it is what keeps the still-required E2E context reporting on PR-1's own run.
- `AGENTS.md`: excise routing rows **12–14** + block **26–54** only. **KEEP rows 15–21 and the
  precedence sentence at 23–24** (they name docs that exist until PR-3, which repoints them).
  dc4-safe: dc4 scans only registry context strings vs classification words, prose-doc set =
  `[AGENTS.md]` (`tools/ci/common.py:15`); no contract/preflight reference exists outside 12–54.
- Gate: consumer grep empty after deletions; `./scripts/check.sh`; `python -m tools.ci selftest`;
  `python -m tools.arch selftest`. Open via `/ecc:prp-pr`, title `(Unit: cleanup-contract-engine)`.
  **STOP — operator merges.**

## Operator gate 1 (after PR-1 merges)

Drop the auto-green E2E required context, **keeping the app_id pin** (live protection uses
`checks[]` with `app_id: 15368`; the bare `contexts[]` param is deprecated and drops the pin):

```bash
gh api -X PATCH repos/Fleezyflo/fanops/branches/main/protection/required_status_checks --input - <<'JSON'
{"checks":[{"context":"unit (fast, no toolchain)","app_id":15368}]}
JSON
```

Verify: `gh api repos/Fleezyflo/fanops/branches/main/protection/required_status_checks --jq '.contexts'`
→ `["unit (fast, no toolchain)"]`.

## PR-2 `cleanup/2-ci-truth` (anchors from the 2026-07-24 tree; ci.yml 266 lines, registry 710)

Precondition: contexts query = unit-only, else halt.

- **ci.yml e2e job (122–242):** add job-level
  `if: github.event_name == 'workflow_dispatch' || github.event_name == 'schedule'`; delete the
  gate step (142–151) and auto-green step (152–159); FULL-strip the gate `if:` lines at
  163/169/179/184/190/198/202/207/224; at **217 and 231 remove ONLY the gate clause, PRESERVING**
  `always() && steps.<e2e-integration|e2e-slow>.outcome != 'cancelled'`; delete the dead upload
  step (236–242) and stale comments (123–136 header rewrite, 160–162 delete). `ci-timing` job
  stays (skipped e2e ≠ cancelled; merges the unit partial only — recorded consequence). The
  `force-e2e` label/title path is REMOVED entirely.
- **Registry lockstep (MANDATORY — dc1–dc6 never parse `on:` or these prose fields; CI will not
  catch a stale registry):** drop E2E from `current_required_contexts` (:57) and
  `intended_required_contexts` (:63); CI-E2E `trigger:` → `[workflow_dispatch, schedule]` (:350),
  `classification:` → `scheduled` (:351), delete `trigger_gate` (:365–395), keep
  `branch_protection_context` (:361) as bare string, rewrite `invariant`/`justification`/
  `failure_evidence`/`artifacts`/`consumers` (:346/:357/:362/:355/:356); rewrite header narrative
  (:34–46) and negative-controls prose (:91–93). Sub-controls (:397–475) already scheduled —
  untouched.
- Delete `scripts/ci_e2e_trigger.py` AND `tests/test_ci_e2e_trigger.py` (same PR — the test
  importlib-loads the script, unmarked → unit lane goes red otherwise).
- **Env-probe demotion:** rename ci.yml:57 step to state diagnostic/non-gating; registry
  CI-UNIT-ENVPROBE lockstep — `step:` (:184), `invariant` (:180), `classification:` → `advisory`
  (:187; dc-safe: `parent: CI-UNIT`, no branch_protection_context), `justification` (:189),
  `failure_evidence` (:193 — its "exits non-zero on mismatch" claim is FALSE; the script has zero
  failing exit paths). Script untouched.
- Left stale deliberately: `verify_theatre.py:136–141,331,369` (census scaffolding, dies PR-4).
  Untouched: `architecture.yml`, `lane-guard.yml`, `nightly.yml`, `scripts/ci_env_probe.py`.
- Checks: `tools.ci selftest`; `./scripts/check.sh`; `/ecc:code-review` (fix CRITICAL/HIGH or
  halt). Open via prp-pr `(Unit: cleanup-ci-truth)`. **STOP — operator merges, then runs**
  `python -m tools.ci deployed --require-live` → expect PASS (Disposition G's defined trigger).

## PR-3 `cleanup/3-governance-prose`

Precondition: `TRIAGE.md` equals the approved Disposition table (diff, not judgment).

1. **Adopt + harden `docs/ENFORCEMENT.md`** (the untracked 57-line draft): rewrite :53–55 (E2E
   resolved by PR-2), :27–29 (DC-3 → on-demand probe + trigger), strip per-rule line anchors
   (:20–22, :27 — paths + ids only), fix ":18 25/25" to count-free wording, rewrite the :7–9
   preamble (census historical; drop "verify exits 0"), fix :35–36 (architecture.yml regenerates
   `derived/` only after step 3). Add Disposition-F entries + a tombstone line. TRACK the file.
2. **Add `tests/test_governance_tombstone.py`** (~20 lines, path-absence + ENFORCEMENT.md-exists,
   unmarked → required unit lane).
3. **Generated-doc surgery (complete verified set):** `tools/arch/render.py` — remove the doc from
   `expected()` (:201) + delete `_governance_doc` and its renderers as dead code (pointers at
   :118–119 die with them); `tools/arch/drift.py` — delete `stale_docs()` (def :74) + its call-site
   term at :204; `tools/arch/selftest.py` — remove NC-23 (Control :68, inject branch :253ff,
   narrow the :320 `("NC-08","NC-23")` condition to NC-08, tidy comments :290/:308–309);
   `tools/arch/policy.py` — rewrite ARCH-006 text (:126–134) to `derived/**`-only, drop the doc
   mention at :678–680. Run `tools.arch regen` in-PR. Acceptance: `tools.arch docs` no-ops;
   selftest green at 24 controls; `ruff check .` clean.
4. **Delete:** `docs/adr/` `docs/contracts/` `docs/governance/` `docs/ci/` `docs/superpowers/`
   `docs/REPOSITORY_CONSTITUTION.md` `docs/ARCHITECTURAL_LAWS.md` `docs/ARCHITECTURE_GOVERNANCE.md`
   (via step 3) `docs/ARCH_RUNBOOK.md` `docs/CI_ARCHITECTURE_REVIEW.md` `docs/CI_SLO.md`
   `docs/CONSTITUTION-EVIDENCE-DOSSIER.md` `docs/SCAFFOLDING-VERDICT.md`
   `docs/ENGINEERING_PHILOSOPHY.md` `docs/handoff.md`. `docs/constitution/` is fully UNTRACKED —
   plain `rm -rf`, loss accepted and recorded (successors are tracked). `docs/HANDOFF-LEARNING-SEAM.md`
   = MOVE → `docs/design/learning-seam.md`. Registry: edit the four dangling prose fields (locate
   by content grep — `docs/adr` ×2, `generated_view: docs/ci/`, `docs/CI_SLO.md` — line numbers
   shifted by PR-2). One ENFORCEMENT.md line each survives for tools.arch operation and the SLO
   gate.
5. **Edits:** `docs/ENGINEERING_STANDARDS.md` — strip the 29 `**Enforcement owner:** …` trailer
   lines (keep Rule/Rationale/Evidence). `CLAUDE.md` — repoint the sole deleted-doc reference
   (:50) → `docs/ENFORCEMENT.md`. `AGENTS.md` — repoint rows 15/16/18/20/21 + rewrite the
   precedence sentence (23–24). KEEP UNTOUCHED: `docs/CODEMAPS/`, all product docs (CONFIG, FLAGS,
   CONTROL-FILES, LEVERS, LEVER-THRESHOLDS, GOLIVE, INSTAGRAM_CONNECT, META_CREDS_OPS, POSTIZ_OPS,
   POSTIZ_SETUP, YOUTUBE_CONNECT, RUNBOOK), `docs/runbooks/`, `docs/design/`.
6. **Final commit — archive:** `git add` the 9 untracked reconciliation files + TRIAGE.md
   (07–11 already tracked). **Fix doc-02's stale `_CLI_PRINT_COUNT` copy (165 → 168) in the same commit**
   (tracking it otherwise promotes the stale copy into the required lane); re-run `tools.arch ci` —
   the pre-existing failure must be GONE.
- Sanity loop BEFORE and AFTER: `tools.arch ci` + both selftests (only NEW failures block).
  `/ecc:code-review` over the full diff. Open `(Unit: cleanup-governance-prose)`. **STOP —
  operator merges.**

## PR-4 `cleanup/4-close` (self-consume)

`verify_theatre.py build` once against the cleaned tree (fail-soft over the deleted trigger
script) — the printed distribution is the closing record for the PR body. Extend the tombstone test
with `docs/reconciliation`. Delete `docs/reconciliation/` entirely (recoverable from the PR-3 merge
in main history). Local checks. Open `(Unit: cleanup-close)`. **STOP — operator merges.**

## Session 5 — closeout, then development resumes

1. Create EXACTLY ONE hookify relapse rule at `.claude/hookify.governance-tombstone.local.md`:

```markdown
---
name: governance-tombstone
enabled: true
event: file
action: block
pattern: "(tools/contract/|docs/(adr|constitution|contracts|governance|ci|superpowers|reconciliation)/)"
---
The governance prose layer was deleted 2026-07 (docs/ENFORCEMENT.md is the index of real
enforcers). Do not recreate files under these namespaces. A rule ships as an enforcer
(unit-lane test, arch rule, dc check) or it does not ship.
```

Verify with `/ecc:hookify-list`.
2. Stratum 2: one `config-gc` run over `~/.claude` (≤20 candidates, operator confirms each,
   soft-delete + log).
3. Resumption gate: ECC `production-audit` over the product (local evidence only) → ship/block
   list; fix blockers.
4. Resume `docs/design/v0.1-ship-route.md` (rev 2, binding).

## Exit criteria (mechanical)

1. `grep -rn "tools/contract\|tools\.contract\|REPOSITORY_CONSTITUTION\|ARCHITECTURAL_LAWS\|ARCHITECTURE_GOVERNANCE" src tools scripts .github tests` → empty.
2. Required contexts = `["unit (fast, no toolchain)"]`.
3. Unit lane green on main; local `tools.arch selftest` (24) + `tools.ci selftest` +
   `tools.arch ci` + `ruff check .` pass.
4. `docs/` = product docs + CODEMAPS + ENFORCEMENT.md + ENGINEERING_STANDARDS.md (craft-only) +
   design/ (incl. learning-seam.md) + runbooks/ — none of adr/constitution/contracts/governance/
   ci/superpowers/reconciliation.
5. Tombstone test in the required unit lane.
6. All 76 TRIAGE dispositions terminal; no row exited by silent deletion; zero COMPILE /
   COMPILE-DEFER rows.
7. production-audit run; blockers fixed; ship-route resumed.
