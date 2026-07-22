<!-- HAND-MAINTAINED (status corrected 2026-07-18). This file is NOT generated: no generator exists.
     `tools/ci/common.py` defines GEN_VIEW pointing here, but nothing imports it, no `tools/ci` verb
     writes it (verbs: static | deployed | reconcile | selftest), no workflow produces it, and no
     byte-compare covers it. It is also outside DC-4's PROSE_DOCS, so no validator checks its claims.

     DATA AUTHORITY: .github/ci-control-registry.yml (ADR-0100) — that file, never this one, is what
     `tools/ci` reads. Edit the registry first, then hand-update this view to match.
     MAINTENANCE: hand-held, by whoever changes the registry.
     GENERATOR: a deferred slice (docs/ci/CI_REMEDIATION_SLICE_PLAN.md); until it lands, treat every
     statement here as prose that can rot, and prefer the registry when the two disagree.

     ORIGINAL BANNER, preserved: "GENERATED VIEW (provisional). Source of truth:
     .github/ci-control-registry.yml (ADR-0100). Hand-rendered for review; once the Phase-C tools/ci
     generator lands it is produced byte-for-byte from the registry and DC-5 forbids hand-editing."

     Do not transcribe mutable counts (e.g. the negative-control count) here — they live in
     tools/arch/selftest.py::CONTROLS. -->

# FanOps — CI Control Inventory (current state)

**Derived from:** `.github/ci-control-registry.yml` reconciled against the live tree and branch
protection in `docs/ci/freeze/2026-07-15/PHASE-A-SNAPSHOT.md`. **Revalidated 2026-07-22** against live
branch protection (required = the two contexts below, `strict` on, `enforce_admins` false).

**This view is HAND-MAINTAINED, not generated.** `generated_view:` in the registry and `GEN_VIEW` in
`tools/ci/common.py` name this file, but `GEN_VIEW` has no reader: there is no generator and no
byte-compare behind it. Treat it as prose that must be edited alongside the registry, and trust the
registry when the two disagree.

## Summary

- **4 workflows · 11 jobs.** **Live required today: 2** (`unit …`, `real-tooling E2E …`), strict on.
- **Intended required (ADR-0101, amended 2026-07-22): 2** — identical to the live set. The staged
  expansion to five (OGD) is **CANCELLED**, so `intended == current ==` live and `tools.ci deployed`
  reports no findings.
- **Controls inventoried:** 2 required top-level jobs (+ required sub-gates that block transitively),
  5 advisory (`ARCH-GATE`, `CI-BASEINSTALL`, `LANE-GUARD`, `ARCH-IMPACT`, `CI-TIMING`), 3
  scheduled/advisory (`ARCH-RECONCILE`, `NIGHTLY-ASR`, `NIGHTLY-PIPAUDIT`), 3 local. Every control maps
  to a real workflow job, scheduled process, or local hook — no orphans.
- **4 duplicate groups**; `arch-drift-policy` is retained by design — with `ARCH-GATE` advisory, the
  unit lane is permanently the merge-blocking line for arch drift/policy/registries.

**Req? legend:** ✅ = live-required now · ○ = advisory (runs and is read; does not block) · ↳ = blocks
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
| `CI-BASEINSTALL` | base install (no extras) refuses smart-framing | advisory | ci-lane | ci · base-install | ○ | — | 0101 | active |
| `CI-E2E` | real-tooling E2E (must run, not skip) | **required** | ci-lane | ci · e2e | ✅ | — | 0101 | active |
| ↳ `CI-E2E-TOOLCHAIN` | Verify toolchain on PATH | required* | ci-lane | ci · e2e | ↳ | — | 0101 | active |
| ↳ `CI-E2E-INTEGRATION` | Integration suite (must run) | required* | ci-lane | ci · e2e | ↳ | — | 0101 | active |
| ↳ `CI-E2E-SLOW` | Slow cross-face proofs | required* | ci-lane | ci · e2e | ↳ | — | 0101 | active |
| ↳ `CI-E2E-NEGCONTROLS` | negative controls (validator effectiveness) | required* | arch-engine | ci · e2e | ↳ | **negative-controls** | 0100/0101 | active |
| `CI-TIMING` | ci-timing artifact (main only) | advisory | ci-lane | ci · ci-timing | ❌ | — | 0101 | active (obs.) |
| `ARCH-GATE` | gate (drift + policy + registries) | advisory | arch-engine | architecture · gate | ○ | **arch-drift-policy** | 0100/0101 | active |
| `ARCH-IMPACT` | impact report | advisory | arch-engine | architecture · impact | ❌ | — | 0101 | active |
| `ARCH-CONTROLS` | negative controls (validator effectiveness) | advisory | arch-engine | architecture · controls | ❌ | **negative-controls** | 0100 | **transitional** |
| `ARCH-RECONCILE` | scheduled reconciliation | scheduled | arch-engine | architecture · reconcile | ❌ | — | 0100 | active |
| `LANE-GUARD` | lane file-ownership + cross-PR collision | advisory | ci-lane | lane-guard · lane-guard | ○ | — | 0101 | active |
| `NIGHTLY-PIPAUDIT` | dependency audit (pip-audit) | scheduled | ci-lane | nightly · dependency-audit | ❌ | — | 0101 | active (advisory until failure policy approved) |
| `NIGHTLY-ASR` | [asr] toolchain smoke | scheduled | ci-lane | nightly · asr-smoke | ❌ | — | 0101 | active |
| `LOCAL-RUFF-PRECOMMIT` | ruff (staged) | local | ci-lane | .githooks/pre-commit | — | ruff-scopes | 0100 | active |
| `LOCAL-CHECK-SH` | scripts/check.sh (scoped) | local | ci-lane | scripts/check.sh | — | ruff-scopes | 0100 | active |
| `LOCAL-SECRETSCAN` | scan-secrets.sh (staged) | local | ci-lane | .githooks/pre-commit | — | secret-scan | 0101 | active |

`required*` = a sub-gate that blocks **transitively** through its parent required job; never its own
GitHub context (a required control's identity is the stable `id`, not the display name). `local`
evidence is `to-verify-phase-C` (re-read when the `tools/ci` validators are built — INV-20).

## Two merge-blocking contexts, and what everything else is for

| Context | Distinct invariant | When it does its work |
|---|---|---|
| `unit (fast, no toolchain)` | hermetic logic + lint + SLO + secret-scan + lock-drift + skip→fail hook + the arch and CI-registry validators | every PR — the sole ROUTINE blocker |
| `real-tooling E2E (must run, not skip)` | real ffmpeg/whisper pipeline + cross-face proofs + validator-effectiveness | **the context reports on every PR in seconds; the suite is ON-DEMAND** — manual dispatch, the 04:00 UTC nightly schedule, or an explicit `force-e2e` request (`scripts/ci_e2e_trigger.py`) |

Everything else runs and is read without blocking: the architecture gate, impact report, base-install
smoke, and the lane + cross-open-PR collision guard. Declassifying them is **not** deleting them — the
jobs still execute on every PR and a red one is still a red one; it just does not hold the merge.

The remaining overlap is deliberate. `CI-UNIT-ARCHGOV` (a unit sub-gate) carries the invariants `gate`
does **not** run (determinism, pure-function-of-source, reachability, field-authority) **and** the
drift/policy/registries checks — and with `gate` now advisory, the unit lane is **permanently** the only
merge-blocking line enforcing them. That closes the de-duplication question rather than deferring it:
scoping the unit lane down would leave those checks blocking nothing at all.

## Registered intentional redundancy

- **`arch-drift-policy` — RETAINED BY DESIGN.** `ARCH-GATE` is advisory (2026-07-22); the
  merge-blocking line for arch drift/policy/registries is permanently `CI-UNIT-ARCHGOV`. The advisory
  gate keeps a fast, standalone, readable verdict and a job summary. De-duplication is closed, not
  deferred. *(History: this was "DECIDED (Model A), NOT YET RESOLVED" while OGD M1 was still expected
  to promote `gate`; before 2026-07-18 it wrongly claimed the scoping was already done.)*
- **`negative-controls`** — `CI-E2E-NEGCONTROLS` is the full validator-effectiveness run, collected in
  the `e2e` slow step. Since 2026-07-22 that step is ON-DEMAND, and the controls are
  `@pytest.mark.slow` while the unit lane deselects `slow`, so **they do not execute on any pull
  request**. `ARCH-CONTROLS` (advisory) is the only negative-control run a PR sees — meaning no
  negative-control run blocks a merge. Stated, not implied.
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
