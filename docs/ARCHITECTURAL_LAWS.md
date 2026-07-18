<!-- Architectural Laws — the ENFORCEABLE subset of the Constitution, with stable IDs and mechanisms.
     Base: origin/main @ 04c4092 (#664), 2026-07-16.
     A "law" here is a rule with (or intended to have) a mechanical enforcer. Rules that are purely
     philosophical live in the Constitution/Philosophy, not here.
     This document does NOT duplicate the CI control registry (.github/ci-control-registry.yml). Where a
     law is enforced by CI, it cross-references the control `id`; the registry remains the single owner of
     control rows. IDs here (LAW-*) are stable; line anchors in Evidence are hints (INV-20 — re-grep). -->

# FanOps — Architectural Laws

**Field key (per law).** *Scope* (what it governs) · *Owner* (subsystem / governance plane) · *ADR* (dependencies) · *Evidence* (source/test/commit) · *Enforcement* (`enforced` · `partially-enforced` · `documented-only` · `dormant` · `proposed`) + mechanism · *CI/validator* (registry control id — never restated) · *Bypass* (known holes) · *Residual* (accepted gap) · *Remediation* (if enforcement is missing/partial → roadmap slice).

**Enforcement tally:** 24 enforced · 8 partially-enforced · 3 proposed · 1 dormant (recorded, not law). Full status per law below; the gaps are the input to `docs/governance/CONSTITUTION_IMPLEMENTATION_ROADMAP.md`.

---

## A · Source-of-truth & architecture governance

### LAW-SOT-01 — Implementation wins over prose; a DECLARED numeric claim must equal the derived fact
- Scope: every governed artifact. · Owner: `tools/arch`. · ADR: 0100 (precedence).
- Evidence: `ARCH-009` (BLOCKING), `docs/ARCHITECTURE_GOVERNANCE.md` §6; `kb/invariants.json:8`.
- Enforcement: **enforced** — `python -m tools.arch ci` policy check. · CI/validator: `ARCH-GATE` (required-intent), `CI-UNIT-ARCHGOV` (scoped distinct invariants).
- Bypass: none for scanned artifacts. · Residual: prose *outside* the DECLARED set is unscanned (LAW-SOT-03). · Remediation: n/a.

### LAW-SOT-02 — Generated artifacts are a pure function of the source tree, byte-verified
- Scope: `.reports/architecture/derived/**`, generated docs, generated CI views. · Owner: `tools/arch` (+ `tools/ci` for `docs/ci/CI_CONTROL_INVENTORY.md`). · ADR: 0102 §4 (regenerate, never hand-merge).
- Evidence: `ARCH-006`; `test_generated_artifacts_are_a_pure_function_of_the_source_tree`; the `repository_commit` self-invalidation lesson.
- Enforcement: **enforced** — byte-compare + drift gate. · CI/validator: `ARCH-GATE`, `ARCH-RECONCILE` (untracked-aware, scheduled); CI registry view via `DC-5` (proposed).
- Bypass: none. · Residual: none. · Remediation: n/a.

### LAW-SOT-03 — No stale number lives as a live assignment in prose/code
- Scope: `.reports/`, `tools/arch/**`, `docs/**` (arch); CI prose (registry/workflows). · Owner: `tools/arch` (arch), `tools/ci` (CI). · ADR: 0100.
- Evidence: `IMPL-007` (widened scan, #641); the `_CLI_PRINT_COUNT` 9-copies/4-values history.
- Enforcement: **partially-enforced** — `IMPL-007` covers arch surfaces; CI-prose ("21" vs control count) is not yet gated. · CI/validator: `CI-UNIT-ARCHGOV`; `DC-4` (prose↔classification) **proposed**.
- Bypass: a number in a workflow comment not scanned by IMPL-007. · Residual: AR-7. · Remediation: land `DC-4` (roadmap SLICE-DC-CORE).

### LAW-SOT-04 — Every governed artifact declares its source-of-truth (DERIVED vs DECLARED)
- Scope: `field_authority.json` domain. · Owner: `tools/arch`. · ADR: —.
- Evidence: `test_field_authority_declares_all_six_attributes`; `common.py:13`.
- Enforcement: **enforced**. · CI/validator: `CI-UNIT-ARCHGOV`.
- Bypass: none. · Residual: DECLARED artifacts still cache derived numbers (migration target). · Remediation: delete cached copies (roadmap, low priority).

### LAW-SOT-05 — The three CI planes agree (intent = implementation = deployed)
- Scope: registry ↔ workflow jobs ↔ live branch protection. · Owner: `tools/ci`. · ADR: 0100, 0101.
- Evidence: `.github/ci-control-registry.yml`; ADR-0100 divergence checks DC-1..DC-6; `tools/ci` validator (#661).
- Enforcement: **partially-enforced** — validator built (#661), not yet a required blocking gate; rollout `transitioning`. · CI/validator: (self) `tools/ci` DC-1..DC-6.
- Bypass: none deployed yet (validator advisory). · Residual: AR-3 (2 live vs 5 intended). · Remediation: SLICE-CI-VALIDATOR-REQUIRED (Phase D/E, ADR-0100/0101).

## B · Architecture structure

### LAW-ARCH-01 — The subsystem partition is total (every module owned, no ghosts)
- Scope: `kb/subsystems.json` vs derived module set. · Owner: `tools/arch`. · ADR: —.
- Evidence: `ARCH-001`/`ARCH-002` (130/130, 0 ghosts).
- Enforcement: **enforced**. · CI/validator: `ARCH-GATE`, `CI-UNIT-ARCHGOV`.
- Bypass: none. · Residual: the grouping is a DECLARED model (not enforced by code — by design). · Remediation: n/a.

### LAW-ARCH-02 — No new compile-time import cycle
- Scope: G1 import graph. · Owner: `tools/arch`. · ADR: —.
- Evidence: `ARCH-004` (non-trivial SCC set == approved set; 1 baselined).
- Enforcement: **enforced**. · CI/validator: `ARCH-GATE`.
- Bypass: none. · Residual: 1 baselined cycle (contained, `UNK-C5-1`). · Remediation: n/a (baseline = containment, not endorsement).

### LAW-ARCH-03 — A must-stay-lazy import may not be hoisted to module level (GB-1)
- Scope: the pinned lazy-edge set. · Owner: `tools/arch`. · ADR: —.
- Evidence: `ARCH-007`/GB-1 ("hoisting looks like a cleanup, breaks process start").
- Enforcement: **enforced**. · CI/validator: `ARCH-GATE`.
- Bypass: none. · Residual: none. · Remediation: n/a.

### LAW-ARCH-04 — Every environment variable read is declared
- Scope: derived env-read set ⊆ declared env set (+ `docs/CONFIG.md` name-set). · Owner: `tools/arch`. · ADR: —.
- Evidence: `ARCH-003` (extended #656).
- Enforcement: **enforced**. · CI/validator: `ARCH-GATE`, `CI-UNIT-ARCHGOV`.
- Bypass: none. · Residual: none. · Remediation: n/a.

### LAW-ARCH-05 — Side effects are censused
- Scope: env-write/ledger-txn/lock/subprocess/network/rmtree/mkdtemp sites. · Owner: `tools/arch`. · ADR: —.
- Evidence: `ARCH-008` (WARNING).
- Enforcement: **partially-enforced** — WARNING severity (a new registered site warns, does not block). · CI/validator: `ARCH-GATE` (warns).
- Bypass: a warning can be ignored. · Residual: AR-8 (deliberate — a census miss should not deadlock a merge). · Remediation: promote to BLOCKING only if abused (roadmap, deferred).

### LAW-ARCH-06 — A PR's architectural blast radius is computable and not BREAKING/UNKNOWN
- Scope: PR diff vs base. · Owner: `tools/arch`. · ADR: 0101 (advisory today).
- Evidence: `impact.py:11` ("UNKNOWN never safe"); `python -m tools.arch impact --strict`.
- Enforcement: **partially-enforced** — runs every PR, **advisory** (not required). · CI/validator: `ARCH-IMPACT` (advisory).
- Bypass: advisory → does not block. · Residual: unique control with no blocking backup. · Remediation: promote after false-positive rate characterized (ADR-0101 criterion 2; roadmap SLICE-IMPACT-PROMOTE).

## C · Domain & ownership

### LAW-OWN-01 — One invariant, one owner; duplicate ownership must be justified
- Scope: all controls/invariants. · Owner: each subsystem + `tools/ci`. · ADR: 0101 (Model A).
- Evidence: registry `duplicate_groups` (arch-drift-policy, negative-controls, ruff-scopes, secret-scan); `selftest.detect` "THE ONLY IMPLEMENTATION".
- Enforcement: **partially-enforced** — per-invariant tests + registry `duplicate_group` declarations; the anti-silent-duplicate gate is `DC-6` (proposed). · CI/validator: `DC-5`/`DC-6` (proposed).
- Bypass: an undeclared duplicate today (review-caught only). · Residual: two arch harnesses run per PR (declared `duplicate_group`). · Remediation: land `DC-6`; execute SLICE-ARCH-MODEL / SLICE-NEGCTRL-DEDUP (Phase D).

### LAW-OWN-02 — Each persona owns its moment end-to-end (affinities len==1); no per-account map on a shared object
- Scope: picking → crosspost. · Owner: picking/crosspost. · ADR: —.
- Evidence: `casting.affinity_admits`; memory `per-account-on-shared-object-is-the-ghost`.
- Enforcement: **enforced** — `tests/test_per_persona_e2e.py`, `tests/test_no_ghosts.py`, `tests/test_account_first_e2e.py`.
- Bypass: none. · Residual: none. · Remediation: n/a.

### LAW-OWN-03 — Metric attribution is severed from hashtags
- Scope: learning/tracking read paths. · Owner: learning. · ADR: —.
- Evidence: `tests/test_hashtag_attribution_severance.py` (`lift_score` invariant; no learner reads `.hashtags`).
- Enforcement: **enforced**. · CI/validator: unit lane.
- Bypass: none. · Residual: none. · Remediation: n/a.

## D · State machine & lifecycle

### LAW-STATE-01 — No auto-publish: a Post is born `awaiting_approval`; publish iterates `queued` only
- Scope: post lifecycle. · Owner: post/studio. · ADR: —.
- Evidence: INV-08; `publish_due`/`publish_now` gate; approval-lifecycle tests (#56–#59).
- Enforcement: **enforced** — "a test goes red if you do." · CI/validator: unit lane (+ `CI-E2E-SLOW` review-lane proofs).
- Bypass: none. · Residual: none. · Remediation: n/a.

### LAW-STATE-02 — No new unguarded door to a terminal Post state; `published` ⇒ `public_url` (GB-4 / R1)
- Scope: writes to `PostState.published`/`analyzed`. · Owner: `tools/arch` + post. · ADR: —.
- Evidence: `IMPL-009`/GB-4 (approved terminal-writer set); R1 regression-lock (#657).
- Enforcement: **enforced** — the write-site set is a pinned baseline; a new door reddens the gate. · CI/validator: `ARCH-GATE` (IMPL-009), unit lane (R1 tests).
- Bypass: none. · Residual: RC-9 mutation-time enforcement deferred (AR-1, pinned by S11). · Remediation: mutation-time enforcement only if RC-9 becomes reachable.

### LAW-STATE-03 — A `Moment` is mutated by setattr, never `model_copy` (GB-5)
- Scope: models. · Owner: `tools/arch` + models. · ADR: —.
- Evidence: this law is the current owner of the boundary. It was first stated as GB-5 in the Cycle-6
  implementation contract (`.reports/architecture/IMPLEMENTATION_CONTRACT.md`), which is a **historical,
  program-specific record** — cite it as corroborating provenance, never as the live authority.
- Enforcement: **partially-enforced** — a Global Boundary (review + the arch impact/policy surface); not a dedicated blocking predicate. · CI/validator: `ARCH-GATE` (policy), review.
- Bypass: a `model_copy` not caught by an existing rule. · Residual: recorded GB. · Remediation: add a dedicated predicate if a violation is attempted (roadmap, deferred).

### LAW-STATE-04 — No ledger model sets `extra="forbid"` (GB-3)
- Scope: `models.py` ConfigDict. · Owner: `tools/arch`. · ADR: —.
- Evidence: `IMPL-010`/GB-3 (BLOCKING).
- Enforcement: **enforced**. · CI/validator: `ARCH-GATE`.
- Bypass: none. · Residual: none. · Remediation: n/a.

## E · Persistence & data integrity

### LAW-PERSIST-01 — No network call or heavy subprocess ever runs inside the ledger lock (the cardinal rule)
- Scope: every `Ledger.transaction`. · Owner: ledger + pipeline + post. · ADR: —.
- Evidence: `ARCHITECTURE_MANIFEST.md` §1; `pipeline.py:162` adopt-or-defer; #89.
- Enforcement: **enforced** — `test_publish_lockfree`, `test_reconcile_lockfree`, `test_pipeline_concurrent`; the 60 s deadlock timeout is the guardrail. · CI/validator: unit lane; `CI-UNIT-SLO` proximate.
- Bypass: none. · Residual: none. · Remediation: n/a.

### LAW-PERSIST-02 — The ledger is never wiped implicitly; wipe is snapshot + typed-confirm; restore serializes on the lock
- Scope: wipe/restore. · Owner: ledger/studio. · ADR: —.
- Evidence: **RC-4/RC-5 fixed** (#653/#654/#655); MOL-71 server-verified preview token; typed `REMOVE`.
- Enforcement: **enforced** — `test_ledger_sqlite_store` recovery + RC regression locks. · CI/validator: unit lane.
- Bypass: admin/manual DB surgery outside the app (out of scope). · Residual: none (defect discharged). · Remediation: n/a.

### LAW-PERSIST-03 — Migrations are additive/idempotent/copy-on-write; a newer-than-code ledger is refused loudly
- Scope: `SCHEMA_VERSION` ladder. · Owner: ledger. · ADR: —.
- Evidence: `ledger.py:218`; `_NewerSchema`; v0→v11; the byte-identical import bridge test.
- Enforcement: **enforced** — migration round-trip + newer-schema-refused tests. · CI/validator: unit lane.
- Bypass: none. · Residual: none. · Remediation: n/a.

### LAW-PERSIST-04 — Forward-compat by `extra="ignore"`; a shape is dropped only after every consumer is gone
- Scope: ledger models + schema drops. · Owner: ledger. · ADR: —.
- Evidence: `models.py:171`; `_migrate_v10_drop_selections` (post-teardown).
- Enforcement: **enforced** (paired with LAW-STATE-04). · CI/validator: unit lane + `ARCH-GATE`.
- Bypass: none. · Residual: none. · Remediation: n/a.

## F · Failure semantics

### LAW-FAIL-01 — No new silent broad `except`; a fail-open handler logs a breadcrumb
- Scope: all `src/` exception handlers. · Owner: `errors` + reviewers. · ADR: —.
- Evidence: `tests/test_swallow_ratchet.py` (49-file baseline); `errors.fail_open` (`exc_info=True`).
- Enforcement: **enforced** — AST ratchet (CI-red on a new silent broad except in an unlisted file). · CI/validator: `CI-UNIT-PYTEST` (collects the ratchet).
- Bypass: the ratchet accepts stdlib `logging` (unsurfaced), so *surfacing* is a review judgment. · Residual: AR-6. · Remediation: strengthen the ratchet toward surfaced channels only if abused (roadmap, deferred).

### LAW-FAIL-02 — Internal modules route output through the logger, never `print()` (exact-equality budget)
- Scope: `_INTERNAL_MODULES` + the `cli.py` print budget. · Owner: `errors`/cli. · ADR: —.
- Evidence: `tests/test_internal_prints_routed.py` (`_CLI_PRINT_COUNT` exact-equality, MOL-358).
- Enforcement: **enforced** — AST ratchet. · CI/validator: `CI-UNIT-PYTEST`.
- Bypass: none. · Residual: the budget number is a copy IMPL-007 also scans (LAW-SOT-03). · Remediation: n/a.

### LAW-FAIL-03 — A correctness prerequisite refuses loudly (cv2 required; no silent centre-crop)
- Scope: smart-framing render path (no-extras install). · Owner: framing + ci-lane. · ADR: 0034 (catalogue); 0103 (reframe principle).
- Evidence: `require_cv2` → `ToolchainMissingError` (exit 2); `scripts/base_install_smoke.py`.
- Enforcement: **enforced** — the base-install smoke asserts the loud refusal. · CI/validator: `CI-BASEINSTALL` (required-intent; currently advisory, promotion 2nd in Phase E).
- Bypass: `CI-BASEINSTALL` not yet a *live* required context (advisory today). · Residual: AR-3 (Phase-E promotion). · Remediation: SLICE-BASEINSTALL-REQUIRED (Phase E).

### LAW-FAIL-04 — Schedule monotonicity is asserted at import time
- Scope: `crosspost` scheduling. · Owner: crosspost. · ADR: —.
- Evidence: `crosspost.py:30` import-time assert (INV-15, MOL-69).
- Enforcement: **enforced** — import-time assert (fails process/test on violation). · CI/validator: any import in the unit lane.
- Bypass: none. · Residual: none. · Remediation: n/a.

## G · Reconciliation & recovery

### LAW-RECON-01 — Publish is claim(pre-network) → network → finalize; a post can never double-publish
- Scope: publish path. · Owner: post/reconcile. · ADR: —.
- Evidence: catalogue `PUBLISH-CLAIM-NETWORK-FINALIZE` (#89); RC-1 (#637), RC-3b (#646).
- Enforcement: **enforced** — lock-free + double-publish-defence tests. · CI/validator: unit lane.
- Bypass: none. · Residual: none. · Remediation: n/a.

### LAW-RECON-02 — The terminal reconcile ladder is a pure function of `(state, age)`
- Scope: reconcile. · Owner: reconcile. · ADR: —.
- Evidence: **RC-2** (#639).
- Enforcement: **enforced** — RC-2 tests. · CI/validator: unit lane.
- Bypass: none. · Residual: none. · Remediation: n/a.

### LAW-RECON-03 — A state's producer and consumer share ONE capability (proven exhaustively)
- Scope: `submitting` producer/consumer. · Owner: providers/post/reconcile. · ADR: —.
- Evidence: **RC-3b** `channel_provider_if_ready`; `producer_claim == is_live_backend` across 96 states (#646).
- Enforcement: **enforced** — exhaustive parity test. · CI/validator: unit lane.
- Bypass: none. · Residual: none. · Remediation: n/a.

## H · Providers, go-live, security

### LAW-PROV-01 — `go_live` is the sole setter of `FANOPS_LIVE=1`, behind a four-step confirm gate
- Scope: go-live. · Owner: studio go-live. · ADR: —.
- Evidence: INV-18; `golive.go_live`.
- Enforcement: **enforced** — go-live tests. · CI/validator: unit lane.
- Bypass: manual `.env` edit + resident-daemon reload (operator action; not an app path). · Residual: none. · Remediation: n/a.

### LAW-PROV-02 — An unknown poster/live value resolves to dryrun (no false LIVE)
- Scope: config/providers. · Owner: providers/config. · ADR: —.
- Evidence: catalogue `PROVIDER-FAIL-SAFE-LIVE`.
- Enforcement: **enforced**. · CI/validator: unit lane.
- Bypass: none. · Residual: none. · Remediation: n/a.

### LAW-SEC-01 — Secrets: reads fail-open, writes fail-closed (round-trip verified); keys are write-only
- Scope: `secret_provider`. · Owner: secrets. · ADR: —.
- Evidence: `set_secret` read-back-or-raise; `tests/test_secret_provider.py`, `test_secret_write_routing.py`, `test_env_perms.py`.
- Enforcement: **enforced**. · CI/validator: unit lane.
- Bypass: none. · Residual: a broken keyring backend warns-once (MOL-359) — degraded, not silent. · Remediation: n/a.

### LAW-SEC-02 — No secret enters a PR diff; the scan has no bypass
- Scope: PR diff + staged diff. · Owner: ci-lane. · ADR: 0101.
- Evidence: `scripts/scan-secrets.sh` (no skip env).
- Enforcement: **enforced** — required sub-gate. · CI/validator: `CI-UNIT-SECRETSCAN` (required), `LOCAL-SECRETSCAN` (pre-commit).
- Bypass: none (deliberately no skip). · Residual: none. · Remediation: n/a.

## I · CI, testing & history

### LAW-CI-01 — Tests run in CI only; local suite execution is mechanically denied
- Scope: test execution. · Owner: ci-lane. · ADR: 0088 (catalogue).
- Evidence: `.claude/settings.json` deny (`pytest`, `python -m pytest`, `check-full.sh`); `scripts/check.sh:86` guard.
- Enforcement: **enforced** — harness deny + script guard. · CI/validator: `CI-UNIT-PYTEST`, `CI-E2E-INTEGRATION` (the sole authorities).
- Bypass: operator-only `FANOPS_LOCAL_TESTS=1` from a human terminal (documented). · Residual: none. · Remediation: n/a.

### LAW-CI-02 — A hanging test is the bug; the 60 s timeout is a deadlock guardrail, never raised to pass
- Scope: pytest. · Owner: ci-lane. · ADR: 0093 (catalogue).
- Evidence: `pyproject.toml:88` (`timeout = 60`); `tests/CLAUDE.md`.
- Enforcement: **enforced** — pytest-timeout. · CI/validator: `CI-UNIT-PYTEST`, `CI-E2E-*`.
- Bypass: raising the timeout (a documentation/PR-review violation). · Residual: none. · Remediation: n/a.

### LAW-CI-03 — Every arch policy rule has a negative control that fires on an injected defect
- Scope: arch validators. · Owner: `tools/arch` + `tools/ci`. · ADR: 0100.
- Evidence: 24–25 controls; `test_every_rule_is_reachable`; `selftest.detect`.
- Enforcement: **enforced** — the merge-blocking neg-control path. · CI/validator: `CI-E2E-NEGCONTROLS` (required), `ARCH-CONTROLS` (advisory selftest).
- Bypass: none for the blocking path. · Residual: the two harnesses both run ~170 s (declared `duplicate_group`). · Remediation: SLICE-NEGCTRL-DEDUP (Phase D) — keep one authoritative full run.

### LAW-CI-04 — Five intended required contexts, each a distinct merge-blocking invariant
- Scope: branch protection. · Owner: `tools/ci` + operator. · ADR: 0101.
- Evidence: registry `intended_required_contexts` (5) vs `current_required_contexts` (2, live).
- Enforcement: **partially-enforced / proposed** — 2 live (`CI-UNIT`, `CI-E2E`); `ARCH-GATE`, `CI-BASEINSTALL`, `LANE-GUARD` are Phase-E, one at a time. · CI/validator: `DC-1` (anti-detach), `DC-3` (intent==live) **proposed**.
- Bypass: `enforce_admins=false` today (governed break-glass to replace it). · Residual: AR-3. · Remediation: SLICE-CI-VALIDATOR-REQUIRED + Phase-E mutations (ADR-0101 §Migration).

### LAW-CI-05 — Lock-drift, SLO budget, and skip→fail-hook are enforced sub-gates of the required unit/e2e lanes
- Scope: dependency locks, unit runtime, e2e must-run. · Owner: ci-lane. · ADR: 0101.
- Evidence: `check-locks.sh`; `ci_slo_gate.py`; the `FANOPS_REQUIRE_E2E` conftest hook + its guard-on-the-guard.
- Enforcement: **enforced** — sub-gates block transitively through the required parent job. · CI/validator: `CI-UNIT-LOCKDRIFT`, `CI-UNIT-SLO`, `CI-UNIT-HOOKVERIFY`, `CI-E2E-INTEGRATION`.
- Bypass: none. · Residual: none. · Remediation: n/a.

### LAW-CI-06 — Squash-only + linear history; a generated-artifact conflict is regenerated, never hand-merged
- Scope: `main` merge strategy. · Owner: operator + `tools/arch`. · ADR: 0102.
- Evidence: ADR-0102 §1/§4; the #637+ squash convention; the arch drift gate rejects a hand-merged derived file.
- Enforcement: **partially-enforced / proposed** — the derived-file half is **enforced** (`ARCH-GATE` byte-compare); `required_linear_history` + squash-only-button are **Phase-E** (proposed). Commit-message grammar is **documented-only** (reviving a gate = the dormant land-gate). · CI/validator: `ARCH-GATE`; branch-protection `required_linear_history` (Phase E).
- Bypass: merge-commit is legal until Phase E; message grammar unenforced (AR-4). · Residual: AR-4. · Remediation: SLICE-LINEAR-HISTORY (Phase E).

### LAW-CI-07 — Workflow hygiene: every job SHA-pins its actions and declares a timeout + concurrency
- Scope: `.github/workflows/**`. · Owner: ci-lane. · ADR: 0100 (DC-6).
- Evidence: SHA-pin policy (`dependabot.yml:3`); lane-guard hardened (#663).
- Enforcement: **partially-enforced / proposed** — the policy holds in practice (all workflows pinned post-#663); the mechanical check is `DC-6` (**proposed**). · CI/validator: `DC-6` (proposed).
- Bypass: a new floating tag would pass until DC-6 lands. · Residual: none active (lane-guard fixed). · Remediation: land `DC-6` (SLICE-DC-CORE).

### LAW-CI-08 — No bot silently rewrites the governance-of-record; reconciliation reports drift, a human lands the fix
- Scope: arch reconcile, CI reconcile, branch protection. · Owner: `tools/arch` + `tools/ci`. · ADR: 0100.
- Evidence: `ARCH-RECONCILE` (reviewable diff, never a silent rewrite); ADR-0100 §Rejected (auto-committing reconciliation).
- Enforcement: **enforced** — the reconcile jobs never auto-commit; DC-3 is authenticated + scheduled, never per-PR auto-mutation. · CI/validator: `ARCH-RECONCILE`; `DC-3` (proposed, report-only).
- Bypass: none. · Residual: none. · Remediation: n/a.

### LAW-CI-09 — The harness enforcement layer is declared; a wired hook is governance, and an unwired one is dormant
- Scope: `.claude/settings.json` (`permissions.deny` + `hooks`), `.claude/hooks/**`. · Owner: operator + governance planes. · ADR: — (0088 catalogue covers the deny-list half).
- Evidence: `.claude/settings.json` `hooks` wires **4** entries — `block-hedge-on-stop.py` (`Stop`, **blocks** turn-end on hedging/half-measure tells), `decide_dont_ask.py` (`PreToolUse:AskUserQuestion`, **blocks** a question already settled by the turn's context), `anti-divert-contract.py` (`UserPromptSubmit`, advisory — silent unless the turn shows a divert signal, then injects one line), `hookify-run.py` (`PreToolUse:Bash|Edit|Write|MultiEdit|AskUserQuestion`, evaluates `.claude/hookify.*.local.md`, **fails open** by design). `LAW-CI-01` already cites this same file for `permissions.deny` (`pytest`, `black`, `ruff format`, `git push --force`, `rm -rf`, `sudo`).
- Enforcement: **enforced** (the hooks execute — two of them block) / **documented-only** for the *declaration* itself (this row is the declaration; no validator asserts the census). · CI/validator: none — `CM-4` (designed) is the natural owner, widened per the note below.
- Bypass: a hook edited or unwired without a governance change; `hookify-run.py`'s fail-open means a runner bug silently disables the rule set (deliberate — a runner bug must never block a tool call). · Residual: the census below is hand-maintained (`LAW-SOT-03` hazard) until `CM-4` measures it. · Remediation: `SLICE-CM-CONTRADICTION` / `CM-4` — see the widening note.

> **Why this law exists — the signature defect, inverted.** This repo's named defect is *"the doc names a
> mechanism that does not exist"* (`C2.2`). Here the inverse was live and unrecorded: **a mechanism that
> executes — and blocks — that no governance document named.** `CM-4` is specified to detect
> *declared-but-unexecuted*; nothing detects *executed-but-undeclared*. An enforcing layer nobody declared
> cannot be reviewed, reasoned about, or safely removed. Recording it is the fix; widening `CM-4` to run
> the census in both directions is the remediation.
>
> **Honest census (2026-07-16, hand-measured; CORRECTED 2026-07-18 — 7 tracked files in
> `.claude/hooks/`):** **4 wired** (above) · **1 library, correctly unwired** — `completion_evidence.py`
> (imported by `block-hedge-on-stop.py`/`decide_dont_ask.py`; not a hook) · **2 DORMANT**:
> `orchestration_gate_claude.py` — **tracked, spec'd (`.orchestration/SPEC.md`), tested
> (`tests/test_orchestration_gate_claude.py`), and wired NOWHERE**: no `settings.json` entry and no
> `.cursor/hooks.json` entry (that file is `{"version":1,"hooks":{}}`); and `stop-completion-gate.py` —
> **tracked, referenced by nothing, wired nowhere** (no `settings.json` entry, no spec, no test, no
> import). Both are `CM-4`'s exact class — a declared mechanism nothing executes.
>
> *Correction note (2026-07-18):* the 2026-07-16 census recorded `orchestration_gate_claude.py` as
> "wired outside `settings.json`". That was wrong when written — the operator had disabled the gate
> wiring on 2026-07-15. **Being spec'd and covered by a test is not being wired** — precisely the
> confusion this row exists to prevent. The original count is preserved here as the error it was, not
> silently replaced.
> Its fate (wire it or delete it, per `C15.3` "deletion is the fix") is a follow-up; **this row does not
> resolve it, it names it.**

## J · Evolution & documentation

### LAW-EVO-01 — "Dead / zero-caller" is a lead, never a verdict; a deletion ships a whole-tree AST census and is revalidated at execution (GB-2)
- Scope: any symbol deletion. · Owner: reviewers + `tools/arch`. · ADR: —.
- Evidence: GB-2; the 5-live-mislabeled-dead finding; the 4 cancelled Cycle-8 deletions.
- Enforcement: **partially-enforced** — `ARCH-IMPACT` detects some breakage; the AST-census-before-delete is a review discipline. · CI/validator: `ARCH-IMPACT` (advisory).
- Bypass: a grep-based deletion that the impact gate does not flag. · Residual: enforcement is mostly review. · Remediation: none planned (the discipline + impact gate are judged sufficient).

### LAW-DOC-01 — Generated docs are views; hand-editing one is drift
- Scope: `docs/ARCHITECTURE_GOVERNANCE.md`, generated CI inventory, any generated table. · Owner: `tools/arch` + `tools/ci`. · ADR: 0102 §4.
- Evidence: `ARCH-006`; generated-doc headers ("DO NOT EDIT").
- Enforcement: **enforced** (arch) / **proposed** (CI inventory via `DC-5`). · CI/validator: `ARCH-GATE`; `DC-5` (proposed).
- Bypass: the CI inventory is not yet byte-gated. · Residual: AR-7. · Remediation: `DC-5` (SLICE-DC-CORE).

### LAW-DOC-02 — A governing document carries a provenance header; ADRs follow `ADR-FORMAT`
- Scope: this constitutional layer, ADRs. · Owner: constitution maintainer. · ADR: — (`ADR-FORMAT.md`).
- Evidence: the `<!-- provenance -->` headers here; `.agents/skills/domain-modeling/ADR-FORMAT.md`.
- Enforcement: **documented-only / proposed** — convention followed; no gate. · CI/validator: none yet (see `CONSTITUTION_MAINTENANCE.md`).
- Bypass: a doc with no header, or `ADR-FORMAT.md` itself being untracked. · Residual: AR-7 (untracked format doc). · Remediation: SLICE-ADRFORMAT-TRACK + proposed staleness/provenance checks.

---

### Cross-references
- CI-owned laws point to a `.github/ci-control-registry.yml` control `id` — that registry is the single owner of control rows; this document never restates one.
- The `dormant` land-gate (catalogue 0096, `(Unit:<slug>)`) is **not** a law here — it is recorded in the Constitution §17 / Reconciliation R2 as dormant-by-decision, enforced by nothing.
- Gaps (`partially-enforced` / `proposed`) are enumerated as ordered slices in `docs/governance/CONSTITUTION_IMPLEMENTATION_ROADMAP.md`.
