<!-- GENERATED VIEW (provisional). Source of truth: .github/ci-control-registry.yml (ADR-0100).
     Hand-rendered for review; once the Phase-C tools/ci generator lands it is produced byte-for-byte
     from the registry and DC-5 forbids hand-editing. Do not transcribe mutable counts (e.g. the
     negative-control count) here — they live in tools/arch/selftest.py::CONTROLS. -->

# FanOps — CI Control Inventory (current state)

**Derived from:** `.github/ci-control-registry.yml` reconciled against the live tree and branch
protection in `docs/ci/freeze/2026-07-15/PHASE-A-SNAPSHOT.md`. **Revalidated 2026-07-15** (HEAD
`0a3b503`; live required contexts still the two below; the three new required contexts' exact `name:`
strings verified char-for-char). **Provisional generated view** — the Phase-C generator replaces it.

## Summary

- **4 workflows · 11 jobs.** **Live required today: 2** (`unit …`, `real-tooling E2E …`), strict on.
- **Intended required (ADR-0101): 5** — `unit`, `real-tooling E2E`, `base install (no extras) refuses
  smart-framing`, `gate (drift + policy + registries)`, `lane file-ownership + cross-PR collision`.
  The three new ones are added **one at a time during Operational Governance Deployment (OGD)** (order: gate → base-install → lane-guard),
  gated on the `tools/ci` validator + remediation PRs being green.
- **Controls inventoried:** 5 required top-level jobs (+ required sub-gates that block transitively),
  2 advisory (`ARCH-IMPACT`, `CI-TIMING`), 3 scheduled/advisory (`ARCH-RECONCILE`, `NIGHTLY-ASR`,
  `NIGHTLY-PIPAUDIT` — the last stays advisory until its failure policy is separately approved),
  3 local. Every control maps to a real workflow job, scheduled process, or local hook — no orphans.
- **4 duplicate groups**; `arch-drift-policy` is **resolved to Model A** (gate authoritative).

**Req? legend:** ✅ = live-required now · ⬦ = intended-required, added during OGD · ↳ = blocks
transitively through its parent required job (never its own context).

## Ownership matrix

| Control ID | Name / context | Class | Owner | Workflow · job | Req? | Dup-group | ADR | Status |
|---|---|---|---|---|---|---|---|---|
| `CI-UNIT` | unit (fast, no toolchain) | **required** | ci-lane | ci · unit | ✅ | — | 0101 | active |
| ↳ `CI-UNIT-SECRETSCAN` | Secret scan (PR diff) | required* | ci-lane | ci · unit | ↳ | secret-scan | 0101 | active |
| ↳ `CI-UNIT-LOCKDRIFT` | Lockfile drift guard | required* | ci-lane | ci · unit | ↳ | — | 0101 | active |
| ↳ `CI-UNIT-ENVPROBE` | CI env probe | required* | ci-lane | ci · unit | ↳ | — | 0101 | active |
| ↳ `CI-UNIT-LINT` | Lint (ruff F+E) | required* | ci-lane | ci · unit | ↳ | ruff-scopes | 0101 | active |
| ↳ `CI-UNIT-PYTEST` | Unit tests | required* | ci-lane | ci · unit | ↳ | — | 0101 | active |
| ↳ `CI-UNIT-SLO` | Unit pytest SLO gate | required* | ci-lane | ci · unit | ↳ | — | 0101 | active |
| ↳ `CI-UNIT-HOOKVERIFY` | skip→fail hook verify | required* | ci-lane | ci · unit | ↳ | — | 0101 | active |
| ↳ `CI-UNIT-ARCHGOV` | arch tests (distinct invariants) | required* | arch-engine | ci · unit | ↳ | **arch-drift-policy** | 0100/0101 | active |
| `CI-BASEINSTALL` | base install (no extras) refuses smart-framing | **required** | ci-lane | ci · base-install | ⬦ (E-2nd) | — | 0101 | active |
| `CI-E2E` | real-tooling E2E (must run, not skip) | **required** | ci-lane | ci · e2e | ✅ | — | 0101 | active |
| ↳ `CI-E2E-TOOLCHAIN` | Verify toolchain on PATH | required* | ci-lane | ci · e2e | ↳ | — | 0101 | active |
| ↳ `CI-E2E-INTEGRATION` | Integration suite (must run) | required* | ci-lane | ci · e2e | ↳ | — | 0101 | active |
| ↳ `CI-E2E-SLOW` | Slow cross-face proofs | required* | ci-lane | ci · e2e | ↳ | — | 0101 | active |
| ↳ `CI-E2E-NEGCONTROLS` | negative controls (validator effectiveness) | required* | arch-engine | ci · e2e | ↳ | **negative-controls** | 0100/0101 | active |
| `CI-TIMING` | ci-timing artifact (main only) | advisory | ci-lane | ci · ci-timing | ❌ | — | 0101 | active (obs.) |
| `ARCH-GATE` | gate (drift + policy + registries) | **required** | arch-engine | architecture · gate | ⬦ (E-1st) | **arch-drift-policy** | 0100/0101 | active |
| `ARCH-IMPACT` | impact report | advisory | arch-engine | architecture · impact | ❌ | — | 0101 | active |
| `ARCH-CONTROLS` | negative controls (validator effectiveness) | advisory | arch-engine | architecture · controls | ❌ | **negative-controls** | 0100 | **transitional** |
| `ARCH-RECONCILE` | scheduled reconciliation | scheduled | arch-engine | architecture · reconcile | ❌ | — | 0100 | active |
| `LANE-GUARD` | lane file-ownership + cross-PR collision | **required** | ci-lane | lane-guard · lane-guard | ⬦ (E-3rd) | — | 0101 | **transitional · harden-first** |
| `NIGHTLY-PIPAUDIT` | dependency audit (pip-audit) | scheduled | ci-lane | nightly · dependency-audit | ❌ | — | 0101 | active (advisory until failure policy approved) |
| `NIGHTLY-ASR` | [asr] toolchain smoke | scheduled | ci-lane | nightly · asr-smoke | ❌ | — | 0101 | active |
| `LOCAL-RUFF-PRECOMMIT` | ruff (staged) | local | ci-lane | .githooks/pre-commit | — | ruff-scopes | 0100 | active |
| `LOCAL-CHECK-SH` | scripts/check.sh (scoped) | local | ci-lane | scripts/check.sh | — | ruff-scopes | 0100 | active |
| `LOCAL-SECRETSCAN` | scan-secrets.sh (staged) | local | ci-lane | .githooks/pre-commit | — | secret-scan | 0101 | active |

`required*` = a sub-gate that blocks **transitively** through its parent required job; never its own
GitHub context (a required control's identity is the stable `id`, not the display name). `local`
evidence is `to-verify-phase-C` (re-read when the `tools/ci` validators are built — INV-20).

## Five required contexts — five distinct merge-blocking invariants

| Context | Distinct invariant |
|---|---|
| `unit (fast, no toolchain)` | hermetic logic + lint + SLO + secret-scan + lock-drift + skip→fail hook |
| `real-tooling E2E (must run, not skip)` | real ffmpeg/whisper pipeline + cross-face proofs + validator-effectiveness |
| `base install (no extras) refuses smart-framing` | clean no-extras packaging + cv2 fail-closed |
| `gate (drift + policy + registries)` | architecture governance (drift + policy + registries) — **Model A authoritative** |
| `lane file-ownership + cross-PR collision` | no cross-lane / cross-open-PR hot-file collision |

No required context duplicates another's invariant. `CI-UNIT-ARCHGOV` (a unit sub-gate) is scoped by
`SLICE-ARCH-MODEL` to the invariants `gate` does **not** run (determinism, pure-function-of-source,
reachability, field-authority), so `unit` and `gate` stay distinct.

## Registered intentional redundancy

- **`arch-drift-policy` — RESOLVED (Model A).** `ARCH-GATE` (required) is the authoritative merge-gate
  for arch drift/policy/registries; `CI-UNIT-ARCHGOV` scoped to distinct invariants (`SLICE-ARCH-MODEL`).
- **`negative-controls`** — `CI-E2E-NEGCONTROLS` (required, in e2e) is the full validator-effectiveness
  run; `ARCH-CONTROLS` (advisory) reduces to a reachability assertion (`SLICE-NEGCTRL-DEDUP`).
- **`ruff-scopes`**, **`secret-scan`** — deliberate scope/moment tiering; keep all, remove none.

## Current-state defects → remediation slices

| # | Defect (proven this session) | Evidence | Slice |
|---|---|---|---|
| 1 | Version drift: `pyproject` **0.4.0** vs `__init__` **0.3.0**; consumed by `cli.py:1104`, `daemon.py:773` | grep 2026-07-15 | `SLICE-VERSION-AUTHORITY` |
| 2 | `.markdownlint.json` exists but nothing references it (dormant) | grep — no hit | `SLICE-MARKDOWNLINT` |
| 3 | `lane-guard.yml`: floating `checkout@v7`, `setup-python@v6`; no timeout; no concurrency | read 2026-07-15 | `SLICE-LANEGUARD-HARDEN` (before its promotion) |
| 4 | `architecture.yml:140` stale "21 injected defects" comment | read 2026-07-15 | `SLICE-STALE-COUNT` |
| 5 | Negative controls run twice on an arch-relevant PR | workflow analysis | `SLICE-NEGCTRL-DEDUP` |
| 6 | `test_variation_render.py` marks via `@REQUIRE` alias, not module `pytestmark` — a *future* unmarked test would run in unit | read 2026-07-15 (line 5,7) | `SLICE-MARKER-GUARD` |
| 7 | arch enforcement had two blocking paths; Model A gives `gate` sole ownership | ADR-0101 | `SLICE-ARCH-MODEL` |
| 8 | Control-file / doc integrity (stale counts, anchors, dormant labels) | review + 2026-07-15 | `SLICE-DOC-INTEGRITY` |

**Corrected premise (code beats prose):** the review's claim that `test_variation_render.py` "runs in
the unit lane" is **stale** — it *is* integration-marked (`REQUIRE = pytest.mark.integration` +
`@REQUIRE`, lines 5/7), so it runs in the e2e lane. The real, smaller finding is the fragile
*mechanism*; `SLICE-MARKER-GUARD` adds a collect-time guard, it does not re-mark the test.

## Non-defects (deliberate — do not "fix")

No coverage floor (`ci.yml:62`, MOL-199); no OS/Python matrix; `pip-audit` advisory (until its failure
policy is separately approved); no CODEOWNERS / required reviews; compact one-liner house style. See
`CI_REMEDIATION_SLICE_PLAN.md` §"Decided — no change".
