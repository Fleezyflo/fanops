<!-- Phase A freeze evidence — a dated, immutable pre-image. Do NOT edit after capture.
     Every Phase D/E change and every rollback in this program references THIS snapshot. -->

# CI Governance — Phase A Freeze Snapshot

**Captured:** 2026-07-15 · **By:** CI reconciliation program, Phase A (freeze the current state).
**Purpose:** an immutable pre-image of the CI system before any remediation. All later slices cite
this file for their "before" state and rollback target. **Nothing here is a proposal** — it is the
mechanically-probed current truth, per the authority order (executable source/tests → live GitHub →
ADRs/registry → generated docs → prose).

## 1. Git state

| Fact | Value |
|---|---|
| Working-tree `HEAD` | `0a3b5033d1048b723bc96bd54a591dba0af85064` (#652) |
| `origin/main` (tip) | `0c452fee4009eb26a325cda5abbba5b3ccdc3255` (#657) |
| Branch | `main` |
| CI-machinery drift `HEAD..origin/main` | **only** `tools/arch/policy.py`, `tools/arch/selftest.py` (verified `git diff --name-only`). The four workflow files, `pyproject.toml`, `src/fanops/__init__.py` are byte-identical at both SHAs. |

The working tree is 5 commits behind `origin/main`; the only CI-relevant delta is inside the arch
engine (the negative-control set grew). **Every workflow fact in this snapshot is valid at both SHAs.**
Raw: [`state.txt`](state.txt).

## 2. Live branch protection (`main`) — re-probed 2026-07-15

Source: `gh api repos/Fleezyflo/fanops/branches/main/protection`. Raw: [`branch-protection.json`](branch-protection.json).

| Setting | Live value |
|---|---|
| Required status checks (`strict` = up-to-date-before-merge) | **`true`** |
| Required contexts | **`unit (fast, no toolchain)`**, **`real-tooling E2E (must run, not skip)`** (2 only; both GitHub-Actions app `15368`) |
| `enforce_admins` | **`false`** (administrators bypass required checks) |
| `required_approving_review_count` | **`0`** |
| `require_code_owner_reviews` | `false` |
| `require_last_push_approval` | `false` |
| `dismiss_stale_reviews` | `false` |
| `allow_force_pushes` | `false` |
| `allow_deletions` | `false` |
| `required_conversation_resolution` | **`false`** |
| `required_linear_history` | **`false`** |

## 3. Workflow / job inventory (11 jobs across 4 workflows)

Raw name/timeout/concurrency/cron grep: [`workflow-manifest.txt`](workflow-manifest.txt).

| Workflow | Job (`id`) | Status-check `name:` | Triggers | Timeout | Concurrency | Required (BP) |
|---|---|---|---|---|---|---|
| `ci.yml` | `unit` | `unit (fast, no toolchain)` | push+PR→main | 15 | `ci-…{ref}` cancel-in-progress | **✅ required** |
| `ci.yml` | `base-install` | `base install (no extras) refuses smart-framing` | push+PR→main | 10 | (workflow-level) | ❌ |
| `ci.yml` | `e2e` | `real-tooling E2E (must run, not skip)` | push+PR→main | 25 | (workflow-level) | **✅ required** |
| `ci.yml` | `ci-timing` | `ci-timing artifact (main only)` | push→main only, `needs:[unit,e2e]` | (none) | (workflow-level) | ❌ (never a PR check) |
| `architecture.yml` | `gate` | `gate (drift + policy + registries)` | push+PR→main (`if != schedule`) | 10 | `arch-…{ref}` cancel-in-progress | ❌ |
| `architecture.yml` | `impact` | `impact report` | PR→main (`if == pull_request`) | 10 | (workflow-level) | ❌ |
| `architecture.yml` | `controls` | `negative controls (validator effectiveness)` | push+PR→main (`if != schedule`, path-selected, fails-open) | 15 | (workflow-level) | ❌ |
| `architecture.yml` | `reconcile` | `scheduled reconciliation` | `schedule` cron `17 5 * * 1` | 20 | (workflow-level) | ❌ (never a PR check) |
| `lane-guard.yml` | `lane-guard` | `lane file-ownership + cross-PR collision` | PR→main | **(none)** | **(none)** | ❌ |
| `nightly.yml` | `dependency-audit` | `dependency audit (pip-audit)` | `schedule` cron `0 3 * * *` + dispatch | 15 | `nightly-…{ref}` cancel-in-progress | ❌ (advisory) |
| `nightly.yml` | `asr-smoke` | `[asr] toolchain smoke (nightly)` | `schedule` + dispatch | 45 | (workflow-level) | ❌ (nightly) |

**Action pinning (Phase A observation):** every `uses:` in `ci.yml`, `architecture.yml`, `nightly.yml`
is SHA-pinned. **`lane-guard.yml` alone floats** — `actions/checkout@v7` (:26), `actions/setup-python@v6`
(:29). No `timeout-minutes` and no `concurrency:` on `lane-guard.yml`.

## 4. Required PR-path pytest executions (marker-derived)

| Workflow·job·step | Invocation | Marker expression |
|---|---|---|
| `ci·unit·unit-tests` | `pytest -q -n auto -m "not integration and not slow"` | not integration and not slow |
| `ci·unit·hook-verify` | `pytest tests/test_ci_require_e2e.py` (asserts exit 1) | file-scoped |
| `ci·e2e·integration` | `pytest -q -m "integration and not ci_hook_regression and not asr" -rs` | integration… |
| `ci·e2e·slow` | `pytest -q -m slow` | slow (serial — no `-n auto`) |
| `architecture·controls·selftest` | `python -m tools.arch selftest` (path-selected) | n/a (CLI) |
| `ci·unit` (collected) | `tests/test_arch_governance.py` runs inside the unit suite | not integration and not slow |
| `ci·e2e·slow` (collected) | `test_negative_control_is_detected` (`@slow`) runs inside e2e-slow | slow |

SLO budget (blocking, `ci_slo_gate.py`): `CI_UNIT_PYTEST_BUDGET_S` = **135** (PR) / **140** (main).
Local pytest is **denied to the agent** (`.claude/settings.json`) and CI-only by policy
([0088 `GOV-TESTS-CI-ONLY`](../../adr/README.md)); CI is the test authority.

## 5. Timings

No fresh `ci-timing.json` artifact is retrievable from this checkout (it is produced on `push→main`
only). The authoritative source is the latest `ci-timing` artifact. The last measured figure carried
into this program (from `docs/CI_ARCHITECTURE_REVIEW.md`, Phase 4) is the dedicated negative-control
job runtime of **170.51 s** (≈5.7 duplicated runner-min/PR versus the pytest-lane execution of the
same `selftest.detect` implementation). Treat as an *estimate to re-measure* in Phase C.

## 6. Open work & baseline

- **Open PRs to `main`:** **none** (`gh pr list --state open` → `[]`). Clean slate — no in-flight CI
  work to reconcile against.
- **Passing baseline:** `origin/main` @ `0c452fe` (#657) is the last landed tip; by policy the two
  required contexts pass on `main`. This snapshot does not (and cannot) re-run them — CI is the
  authority and no local pytest is permitted.

## 7. Immutability

This directory is the **pre-image**. Do not edit it after capture. Phase E branch-protection
mutations and every slice rollback in
[`../CI_BRANCH_PROTECTION_MUTATIONS.md`](../CI_BRANCH_PROTECTION_MUTATIONS.md) and
[`../CI_REMEDIATION_SLICE_PLAN.md`](../CI_REMEDIATION_SLICE_PLAN.md) reference the values here as their
"before" state.
