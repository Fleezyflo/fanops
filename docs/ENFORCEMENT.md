# ENFORCEMENT — the mechanisms that actually bite

**The rule of this file:** a rule exists here only if a mechanism enforces it. A new rule ships with
its enforcer or it does not ship. Prose that claims authority without a mechanism is not governance —
it is decoration, and it lives nowhere.

Every entry below was proven by the theatre census (2026-07-24; executed and archived into main
history by the cleanup route that produced this file): each arch/ci rule injected a violation into a
fixture and the gate caught it (`selftest`), or it is a live product gate whose flip the census
captured.

## Merge gates — the only things that block a PR to `main`
- **Required status context `unit (fast, no toolchain)`** — `.github/workflows/ci.yml`. The sole
  blocking check on push/PR (live branch protection; reconciled by the DC-3 probe below).
- **`merge_approved` single-operator ruleset** — the operator's merge *is* the authorization; the only
  route to protected `main`.

## Unit-lane validators — run inside the required `unit` job; each has a firing negative control
- **`tools/arch`** — architecture governance. Proof: `python -m tools.arch selftest` (every control
  fires; the count is printed by the run, never pinned in prose). Wired via
  `tests/test_arch_governance.py` in the unit lane.
  - `tools/arch/policy.py`: ARCH-001, ARCH-002, ARCH-003, ARCH-004, ARCH-006, ARCH-007, ARCH-008,
    ARCH-009, ARCH-010; IMPL-006, IMPL-007, IMPL-009, IMPL-010.
  - `tools/arch/drift.py` (artifacts under `derived/` byte-identical to regeneration) ·
    `tools/arch/registries.py` (registry validity / unknown-growth).
- **`tools/ci`** — CI-registry ↔ workflow reconciliation. Proof: `python -m tools.ci selftest`. Wired
  via `tests/test_ci_registry_validator.py` in the unit lane.
  - `tools/ci/checks.py`: DC-1, DC-2, DC-4, DC-5, DC-6, measured against
    `.github/ci-control-registry.yml`.
- **`ruff check .`** (F+E), **`scripts/scan-secrets.sh`** (PR-diff secret scan),
  **`scripts/check-locks.sh`** (lockfile drift), **`scripts/ci_slo_gate.py`** (unit-suite SLO,
  blocking) — all steps of the `unit` job.
- **`tests/test_governance_tombstone.py`** — pins the governance-prose deletion (see Tombstone below)
  and this file's existence, forever in the required lane.

## On-demand admin probe — operator-run, no workflow invokes it
- **DC-3: `python -m tools.ci deployed --require-live`** — reconciles LIVE branch protection against
  the registry's `current`/`intended` context lists. Needs an admin-scoped token, so it is not
  automated. Defined trigger: the operator runs it after ANY branch-protection change. First
  exercised at cleanup gate 2 (2026-07-25): PASS.

## Advisory automation — runs and reports, does NOT block a PR (keep; real work, just not a gate)
- `.github/workflows/architecture.yml` — `tools.arch ci`/`impact` + the `derived/` AUTO-REGEN
  reconcile leg (regenerates the machine artifacts and FAILS on drift with a reviewable diff;
  deleting it silently rots `derived/`).
- `.github/workflows/lane-guard.yml` — `scripts/lane_guard.py` (file-ownership) +
  `scripts/pr_collision_guard.py` (cross-PR collision).
- ci.yml `base-install` job — clean-venv packaging smoke (`scripts/base_install_smoke.py`): base
  install must import fanops + CLI and REFUSE smart-framing loudly without cv2.
- `.github/workflows/nightly.yml` — pip-audit (`continue-on-error: true`) + ASR smoke. Scheduled,
  independent; never touches the e2e job.

## Scheduled lane — cron/dispatch only; nothing of it runs on a PR
- ci.yml `e2e` job — the real-tooling suite (real ffmpeg/whisper/espeak; `FANOPS_REQUIRE_E2E=1`
  turns integration skips into failures) + the `@slow` cross-face proofs. Runs on
  `workflow_dispatch` or the 04:00 UTC nightly ONLY (job-level `if`, since 2026-07-24). A render or
  publish regression can merge green and be caught by the nightly — the accepted, disclosed cost of
  not holding PR iteration for ~7 minutes.

## Tooling entrypoints — machinery, not gates
- `python -m tools.arch [selftest|ci|impact|regen]` and `python -m tools.ci
  [static|deployed|reconcile|selftest]` — CLI surfaces of the validators above. Operating note: run
  them from the repo venv (`.venv/bin/python`); bare `python` lacks the YAML dependency.
- `./scripts/check.sh` — the local lint-only runner (ruff + scoped checks; never pytest —
  the test suite is CI-only in this repo).
- `scripts/repo_sweep.py` — read-only inventory sweep, consumed by `scripts/orchestrate.py`.

## Runtime product gates — enforce in `src/fanops`, proven live by the census
- **Publish gate:** a crossposted post is born `PostState.awaiting_approval`; `publish_due`/`publish_now`
  iterate only `queued`, so nothing publishes until the operator approves — even on a live backend.
- **Learning gate:** `validation_gate.learning_validated` (closed by default) + `p4_unlocked`; freezes
  the learning actuators until a real, non-degraded metric proves the field-shape.
- **Weak-hook gate:** `hookcheck.is_weak_hook` — rejects empty / feed-wide-duplicate / template-cluster
  hooks at the crosspost mint (`src/fanops/moments.py` call site).
- **Doctor:** `src/fanops/doctor.py` — fail-closed operator setup checks (`fanops doctor`/`status`).
- The live `FANOPS_*` toggles (`src/fanops/settings.py`) — reference: `docs/CONFIG.md`, `docs/FLAGS.md`.

## Tombstone — deleted 2026-07; do not restore as authority
The prose legal system — a constitution, architectural laws, ADRs, change contracts, governance
roadmaps, and CI prose inventories claiming enforcement — is deleted: the theatre census proved the
overwhelming majority of its stated gates were backed by no mechanism. Gone with it:
- **`tools/contract/`** — census SELF-REF: zero references in `.github/` or `.githooks/`; its only
  executor was its own selftest, computing decisions against no real PR. Dead as a gate.
- **`real-tooling E2E (must run, not skip)` as a REQUIRED context** — it auto-greened on every
  push/PR without running the suite (UNFALSIFIABLE). Dropped from branch protection at cleanup
  gate 1; the suite survives as the scheduled lane above.
- **`docs/{adr,constitution,contracts,governance,ci,superpowers}/`** and the standalone
  constitution/laws/philosophy documents — archived in main history at the cleanup route's merges.

`tests/test_governance_tombstone.py` pins the deletion. A rule ships as an enforcer (a unit-lane
test, an arch rule, a dc check) or it does not ship — this file is the index, and the whole of the
law.
