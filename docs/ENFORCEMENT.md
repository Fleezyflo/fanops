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
  via `tests/test_ci_registry_validator.py` in the unit lane. Every DC is a pure function, so the
  unit lane proves all of them DISCRIMINATE — each negative control injects one defect and asserts
  the named DC fires on evidence absent before — including the deployed-state DCs below, whose
  VERDICT needs the network and is therefore scheduled. What the unit lane does NOT carry for
  DC-3/8/9 is any claim about live GitHub; that distinction is the point, not a gap.
  - `tools/ci/checks.py`: DC-1, DC-2, DC-4, DC-6, DC-7, measured against
    `.github/ci-control-registry.yml`. DC-6 also rejects unknown GitHub Actions permission keys
    before GitHub rejects the workflow. **DC-7** is the one that catches the failure below: an
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
- **`tests/test_post_state_census_ratchet.py`** (MOL-815) — binary AST gate: zero
  `Counter(… .state …)` over `led.posts` / `posts.values()` outside `ledger.py`. Negative control
  plants that Counter and asserts the finder fires. Removal condition: permanent by policy.
- **`tests/test_machine_health_channel_ratchet.py`** (MOL-965 WP4) — closed-world CALLER AST +
  soft-lie ban: every `FunctionDef` that Calls `build_health_report` / `doctor_report` must be in
  `_ALLOWED_HEALTH_CONSTRUCTOR_CALLERS` (file allowlist is secondary, not a Band-Aid);
  `cmd_status` / `cmd_up` / `/healthz` must not call the constructor; Studio may not import
  `doctor_report`; `ok`+`warn` check dicts without `severity` cannot grow. Operator contract:
  [docs/MACHINE_HEALTH.md](MACHINE_HEALTH.md).

## Deployed-state probes — the plane no static check can reach
`python -m tools.ci deployed [--require-live]`, in the `reconcile` job (weekly + `workflow_dispatch`).
Read-only GETs; nothing here mutates, and nothing that mutates may be added — a reconciler able to
change what it measures is not a reconciler. These measure state that exists ONLY in GitHub
settings, so the tree can look perfect while the control is switched off. Each probe carries its
OWN error: one unreadable plane must never report another as clean, which is how DC-3's standing
403 would otherwise have gone on hiding DC-8 and DC-9.

- **DC-8: a declared workflow held disabled in GitHub** (MOL-722). Every `workflow:` in the registry
  is a commitment that the file runs, and DC-2 already makes that list exhaustive — so this needed
  no new declaration. Live `state` not `active` is BLOCKING and names the state, so
  `disabled_manually` (an operator decision) reads differently from `disabled_inactivity` (GitHub
  retiring a cron in a repo that went quiet). A declared workflow GitHub has NO record of is INFO,
  not a finding: a workflow added on a branch and not yet on `main` looks exactly like that, and
  conflating the two would redden every PR that adds one. Forensic basis — `nightly.yml`, declaring
  a daily `dependency-audit` and `asr-smoke`, was `disabled_manually` from 2026-07-14 and
  deployed-state reconciliation reported clean for the whole of it. Needs `actions: read`, which IS
  grantable, so this one is authoritative in CI. A live workflow the registry does not declare is
  deliberately NOT reported: GitHub keeps a record of every workflow file ever pushed, and this repo
  carries three such ghosts from deleted files.
- **DC-9: a declared repository security setting disabled** (MOL-722) — `required_security_settings`
  vs the repo object's `security_and_analysis`. **Stated cost:** that field is admin-only, admin is
  not a grantable `GITHUB_TOKEN` scope, so in CI this reports an explicit `[SKIP]` — never a pass —
  until the operator supplies the same fine-grained PAT DC-3 waits on. It is authoritative today on
  an operator terminal. `--require-live` deliberately does NOT escalate its probe failure:
  escalating a probe that structurally cannot succeed manufactures a red nobody can clear, which
  this repo has shipped once already (`impact --strict`, unclearable on deletions). A divergence it
  CAN see still blocks.
- **DC-3: live branch protection vs the registry context list** — same job; `--require-live` turns
  an unreadable branch-protection probe into a failure.
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
  AND the sole execution of the **deployed-state probes** (`tools.ci deployed --require-live`:
  DC-3, DC-8, DC-9 above). Those are the `tools/ci` checks the required unit lane cannot carry —
  they need the network and an authenticated settings read — so until 2026-07-26 DC-3 ran nowhere
  at all, and live protection silently carried three contexts the registry never declared.
  Schedule-or-dispatch only: a red there is a signal to look, and no merge is waiting on it. That
  containment is WHERE the job runs, not a softened verdict — a real divergence still renders
  `[FAIL]` and exits non-zero.
- `.github/workflows/nightly.yml` — pip-audit (`continue-on-error: true`) + ASR smoke. Scheduled,
  independent; never touches the e2e job. Whether it is actually ENABLED in GitHub is DC-8's
  question — from 2026-07-14 it was not, and nothing here could tell.

## Scheduled lane — cron/dispatch only; nothing of it runs on a PR
- `ci-e2e.yml` — the real-tooling suite (real ffmpeg/whisper/espeak; `FANOPS_REQUIRE_E2E=1`
  turns integration skips into failures) + the `@slow` cross-face proofs. Runs on the 04:00 UTC
  cron schedule ONLY. `ci.yml` has no E2E job and no `workflow_dispatch`. A render or publish
  regression can merge green and be caught by the nightly — the accepted, disclosed cost of not
  holding PR iteration for ~7 minutes.

## Tooling entrypoints — machinery, not gates
- `python -m tools.arch [selftest|ci|impact|regen]` and `python -m tools.ci
  [static|deployed|reconcile|selftest]` — CLI surfaces of the validators above. Operating note: run
  them from the repo venv (`.venv/bin/python`); bare `python` lacks the YAML dependency.
- `./scripts/check.sh` — the local lint-only runner (ruff + scoped checks; never pytest —
  the test suite is CI-only in this repo).
- `scripts/repo_sweep.py` — read-only inventory sweep, consumed by `scripts/orchestrate.py`.
- **Architecture derived merge hygiene (MOL-833):** `.gitattributes` sets `merge=ours` on
  `.reports/architecture/derived/**` so concurrent PRs that each regen those tracked artifacts do
  not conflict. `./scripts/setup-hooks.sh` arms both `core.hooksPath=.githooks` and
  `merge.ours.driver=true` (the attribute is inert without the driver). `.githooks/post-merge` then
  runs `python -m tools.arch regen` on the merged source and leaves the working tree refreshed —
  not auto-committed. Proof: `tests/test_arch_merge_regen.py` (clean dual-branch merge + byte-identical
  regen; negative control that a real source conflict still fails and is not regenerated away).
  Drift/CI still catch a forgotten follow-up commit (loud/late).

## Runtime product gates — enforce in `src/fanops`, proven live by the census
- **Publish gate:** a crossposted post is born `PostState.awaiting_approval`; `publish_due`/`publish_now`
  iterate only `queued`, so nothing publishes until the operator approves — even on a live backend.
- **Learning gate — data quality, NOT operator consent:** `validation_gate.learning_validated` reads
  one key, `00_control/cutover.json` `metrics_confirmed`. It is **auto-stamped open** by
  `track._auto_validate_metrics_shape` on the first real, non-degraded analyzed metric from a live
  backend, and it **never re-closes** — nothing in the tree writes `metrics_confirmed` False
  (`cutover_postiz` and the auto-stamp write only `True`). So it proves the metric FIELD-SHAPE and
  says nothing about intent. What it actually freezes: `variant_amplify`, `variant_transfer` (at
  `caption._transferred_hooks` plus the digest "borrowing" label), and — through `p4_unlocked`, which
  adds an attributed-signal floor on top of it — `p4_dim_bias` and `timing_bias`. Consent is a
  separate mechanism: each actuator's own default-OFF flag (`docs/FLAGS.md`).
- **Unattended-actuator gate:** the learn pass (`cli._learn_pass`) reaches the two actuators that
  change state on their own — `adjust.amplify` re-opens a moment request on a metric winner's source
  (minting moments → clips → posts), `adjust.retire` suppresses a loser's clip, its moment when no
  live sibling remains, and every unshipped post of that lineage. Each is gated on its OWN default-OFF
  intent flag and nothing else: `cfg.learn_amplify` (`FANOPS_LEARN_AMPLIFY`) and `cfg.learn_retire`
  (`FANOPS_LEARN_RETIRE`); both paths leave a breadcrumb (`amplified`/`amplify_skipped`,
  `retired`/`retire_skipped`). `learning_validated` is deliberately **not** in either chain — with no
  `False` writer it can never re-bind once stamped, so it would be theatre rather than a gate. Both
  actuators previously ran on `cfg.is_live_backend` alone: going live to PUBLISH also switched on an
  autonomous generator and an autonomous destroyer.
- **Operator brake:** `pipeline_run.paused` — the `00_control/paused` marker written by `fanops pause`
  — is checked at the top of `cli._cmd_run_pass`, BEFORE the run lease is taken. A paused pump logs
  `run/paused`, still emits its heartbeat (silence would look like a dead pump), and does no pipeline
  work; operator verbs such as `fanops advance` stay unblocked.
- **Weak-hook gate:** `hookcheck.is_weak_hook` — rejects empty / feed-wide-duplicate / template-cluster
  hooks at the crosspost mint (`src/fanops/moments.py` call site).
- **Cascade-delete peers (MOL-809):** `Ledger.retire_source` (whole-source empty-keep-set; CLI gates
  MOL-842) and `fanops purge` / `ledger_wipe.execute_purge` (day+origin scoped; MOL-758) are both
  sanctioned — neither delegates to the other (transaction ownership + granularity).
- **Origin-backfill deletion (MOL-808):** the one-shot `machine_inferred` reconstruction engine, CLI verb, and Studio provenance panel are gone after the amplify-descended purge left them with zero addressable rows. `display_origin` / `UNLABELLED_DISPLAY` remain in `models.py` for Review cards; Jul-13 `unknown` rows are operator corpus, not this migration.
- **Doctor / machine health:** `health_model.build_health_report` is the sole constructor;
  primary channel `fanops doctor` (`cli.cmd_doctor`); Studio strip/Go-Live/metrics project from it.
  `/healthz` is process-only; `fanops status` is backlog, not a third healthy.
  See [docs/MACHINE_HEALTH.md](MACHINE_HEALTH.md); CI: `tests/test_machine_health_channel_ratchet.py`.
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
