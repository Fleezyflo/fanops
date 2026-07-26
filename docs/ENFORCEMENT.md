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
- **`tools/arch`** — architecture governance. The RULES run here: `test_no_blocking_policy_findings`,
  the `derived/` drift byte-compare, and `test_every_rule_is_reachable` are all in the unit lane.
  **The proof that those rules FIRE is not** — `test_negative_control_is_detected` carries
  `@pytest.mark.slow` and the unit lane runs `-m "not integration and not slow"`, so no
  merge-blocking run of the negative controls exists. This paragraph used to claim otherwise. That
  matters because the blocking check is "no blocking findings", which goes GREENER when a rule
  silently stops firing — the IMPL-007 failure this repo already had. The `negative controls` job
  below is the only PR-visible run and it is ADVISORY: it reports, it does not gate. That gap is
  disclosed here, not closed — say advisory and mean it.
  - `tools/arch/policy.py`: ARCH-001, ARCH-002, ARCH-003, ARCH-004, ARCH-006, ARCH-007, ARCH-008,
    ARCH-009, ARCH-010; IMPL-006, IMPL-007, IMPL-009, IMPL-010.
  - `tools/arch/drift.py` (artifacts under `derived/` byte-identical to regeneration) ·
    `tools/arch/registries.py` (registry validity / unknown-growth).
- **`tools/ci`** — CI-registry ↔ workflow reconciliation. Proof: `python -m tools.ci selftest`. Wired
  via `tests/test_ci_registry_validator.py` in the unit lane.
  - `tools/ci/checks.py`: DC-1, DC-2, DC-4, DC-6, DC-7, measured against
    `.github/ci-control-registry.yml`. **DC-7** is the one that catches the failure below: an
    advisory job that can nonetheless FAIL the workflow. Its siblings all compare a declaration to
    another declaration; none asked what a job DOES when it fails, which is why six jobs sat in that
    state unnoticed. **DC-5 was deleted 2026-07-26** with the `duplicate_groups` block it policed:
    every group paired a real job either with a `LOCAL-*` git-hook row that existed for no other
    purpose, or with a sub-row describing a STEP of another job. The registry manufactured the
    duplication the check then found.
- **`ruff check .`** (F+E), **`scripts/scan-secrets.sh`** (PR-diff secret scan),
  **`scripts/check-locks.sh`** (lockfile drift), **`scripts/ci_slo_gate.py`** (unit-suite SLO,
  blocking) — all steps of the `unit` job.
- **`tests/test_governance_tombstone.py`** — pins the governance-prose deletion (see Tombstone below)
  and this file's existence, forever in the required lane.

## Admin probe — now automated; was operator-run, and the trigger was not honoured
- **DC-3: `python -m tools.ci deployed --require-live`** — reconciles LIVE branch protection against
  the registry's `current`/`intended` context lists. Runs in the `reconcile` job (weekly + on
  `workflow_dispatch`), which holds the `administration: read` grant the branch-protection GET needs.
  Until 2026-07-26 this entry read "operator-run, no workflow invokes it… Defined trigger: the
  operator runs it after ANY branch-protection change." That was a deliberate decision, not an
  oversight — and it failed the way a human-memory trigger fails. Protection was changed on
  2026-07-24; the probe was not run; live carried FOUR required contexts against a registry
  declaring ONE for two days, three of them `continue-on-error` (required checks that could never
  fail). A control whose trigger is "somebody remembers" is the weakest kind this file admits, so it
  is now on a schedule. The operator run remains useful immediately after a protection change —
  it is just no longer the only thing standing between a drift and nobody noticing.

## Advisory automation — runs and REPORTS; carries `continue-on-error: true` so it cannot paint red
Advisory now means advisory. Until 2026-07-26 these jobs hard-failed while nothing could block on
them — red that had to be merged past, which is decoration by this file's own first rule and trains
merge-past-red on the whole board. **DC-7 keeps it that way** — an advisory job without
`continue-on-error` is now a blocking validator finding. The required set stays FINAL at the single
`unit` context, so every job here takes the same shape: report, do not gate. The first three are the
ONLY signal for what they check; that cost is stated on each line rather than hidden.

**And each of them now WRITES that report.** `continue-on-error` makes a failed job report success,
so on its own it is a mute button, not a report — until 2026-07-26 `controls` and `base-install`
produced no artefact whatsoever when they failed, and only `impact` was legible, by the accident of
teeing its verdict to the run summary. All three now emit a job summary. `controls` additionally
prints its SELECTION VERDICT, because its suite is selection-gated and this repo has already shipped
a green `negative controls` context whose step never ran: a summary that cannot distinguish "skipped"
from "passed silently" would rebuild the blind spot it exists to close.
- `architecture.yml` `impact` job — `tools.arch impact --strict`, the only breaking-change detection
  in the repo. It exits non-zero and prints each UNDECLARED reason verbatim; `9c5f71e` made those
  reasons clearable by pasting the printed line into `approved_breaking_changes` in
  `governance/baselines.json`. Nothing gates on the verdict — read the log.
- `architecture.yml` `controls` job — `tools.arch selftest`, the only PR-visible proof the arch
  validators fire on an injected defect. The pytest twin is `@pytest.mark.slow` and the unit lane
  deselects `slow`. Nothing gates on it, while the unit lane's own blocking check is "no blocking
  findings" — a check that goes GREENER when a rule silently stops firing.
- ci.yml `base-install` job — `scripts/base_install_smoke.py`, the only exercise of a clean
  no-extras install and the loud cv2 refusal; the unit lane installs `[framing]`, so it is
  structurally incapable of covering this.
- `.github/workflows/lane-guard.yml` — `scripts/lane_guard.py` (file-ownership) +
  `scripts/pr_collision_guard.py` (cross-PR collision). Coordination advice resting on a best-effort
  Linear lookup, not a claim about the diff.
- ci.yml `ci-timing` job — post-hoc timing telemetry on main; nothing reads its result.
- `architecture.yml` `reconcile` job — the scheduled `derived/` AUTO-REGEN leg (regenerates the
  machine artifacts and FAILS on drift with a reviewable diff; deleting it silently rots `derived/`)
  AND the sole execution of **DC-3** (`tools.ci deployed --require-live`: registry
  `required_contexts` vs LIVE branch protection). DC-3 is the one `tools/ci` check the
  required unit lane cannot carry — it needs the network and an authenticated settings read — so
  until 2026-07-26 it ran nowhere at all, and live protection silently carried three contexts the
  registry never declared. Schedule-or-dispatch only: a red there is a signal to look, and no merge
  is waiting on it.
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
