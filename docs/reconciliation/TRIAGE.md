# TRIAGE — disposition ledger for the 76 non-REAL governance census rows

Materialized verbatim from the approved cleanup plan (audited + reverified against HEAD 05bc960,
2026-07-24). Scope: census surfaces A/B/C (101 rows = 25 REAL + 76 below). Every row is terminal —
no WATCH state, no open judgment. Executed by: PR-1 (group A), PR-2 (B, C), PR-3 (F documentation),
operator gate 2 (G first exercise). Groups D/E require no action (mechanisms verified real).
This file is tracked by PR-3's archive commit and deleted with docs/reconciliation/ in PR-4.

| # | id | census class | disposition | evidence |
|---|----|----|----|----|
| 1 | C/contract.decide | INERT | DEAD (PR-1) | engine invoked by nothing required; zero code consumers |
| 2 | C/contract.parse | INERT | DEAD (PR-1) | same |
| 3 | C/contract.model | INERT | DEAD (PR-1) | same |
| 4 | C/contract.lifecycle | INERT | DEAD (PR-1) | same |
| 5 | C/contract.validate | INERT | DEAD (PR-1) | same |
| 6 | C/contract.classify | INERT | DEAD (PR-1) | same |
| 7 | C/contract.derive | INERT | DEAD (PR-1) | same |
| 8 | C/contract.adapters | INERT | DEAD (PR-1) | same |
| 9 | C/contract.report | INERT | DEAD (PR-1) | same |
| 10 | C/contract.main | INERT | DEAD (PR-1) | same |
| 11 | C/contract.selftest | SELF-REF | DEAD (PR-1) | self-injected defects gating no PR; sole executor of the engine |
| 12 | A/test_post_ | UNVERIFIED | DEAD (PR-1) | mechanism tests/test_contract_compiler.py:611 is a contract-engine invariant; dies with the engine |
| 13 | B/e2e | UNFALSIFIABLE | RESOLVED (PR-2) | auto-green removed; job gated to workflow_dispatch+schedule |
| 14 | B/e2e.gate | UNFALSIFIABLE | RESOLVED (PR-2) | gate step deleted with the trigger script |
| 15 | C/scripts.ci_e2e_trigger | UNFALSIFIABLE | RESOLVED (PR-2) | scripts/ci_e2e_trigger.py deleted |
| 16 | B/e2e.integration | INERT | RESOLVED (PR-2) | becomes honestly scheduled-only (no fake PR gate) |
| 17 | B/e2e.slow | INERT | RESOLVED (PR-2) | same |
| 18 | B/unit.envprobe | UNFALSIFIABLE | DEMOTE-DIAGNOSTIC (PR-2) | scripts/ci_env_probe.py has zero failing exit paths; step + registry reworded non-gating |
| 19 | C/scripts.ci_env_probe | UNFALSIFIABLE | DEMOTE-DIAGNOSTIC (PR-2) | same |
| 20 | A/test_actions_ | UNVERIFIED | ALREADY-REAL | tests/test_studio_actions.py:239 (unit lane) |
| 21 | A/test_every_rule_is_reachable | UNVERIFIED | ALREADY-REAL | tests/test_arch_governance.py:107 (unit lane) |
| 22 | A/test_field_authority_declares_all_six_attributes | UNVERIFIED | ALREADY-REAL | tests/test_arch_governance.py:132 (unit lane) |
| 23 | A/test_generated_artifacts_are_a_pure_function_of_the_source_tree | UNVERIFIED | ALREADY-REAL | tests/test_arch_governance.py:59 (unit lane) |
| 24 | A/test_reframe | UNVERIFIED | ALREADY-REAL | tests/test_reframe_s2_d1a.py:169 (unit lane) |
| 25 | A/test_studio_ | UNVERIFIED | ALREADY-REAL | tests/test_postiz_trust_boundary.py:144 (unit lane) |
| 26 | B/unit | UNVERIFIED | ALREADY-REAL | ci.yml:67 — required lane, -m "not integration and not slow" |
| 27 | B/unit.hookverify | UNVERIFIED | ALREADY-REAL | ci.yml:87–93 (skip→fail hook proof step) |
| 28 | B/unit.lint | UNVERIFIED | ALREADY-REAL | ci.yml:59–60 (ruff check .) |
| 29 | B/unit.secretscan | UNVERIFIED | ALREADY-REAL | scripts/scan-secrets.sh:60 (exit 1 path) |
| 30 | B/unit.lockdrift | UNVERIFIED | ALREADY-REAL | scripts/check-locks.sh:1 |
| 31 | B/unit.slo | UNVERIFIED | ALREADY-REAL | scripts/ci_slo_gate.py:15, blocking at ci.yml:75–79 |
| 32 | C/scripts.ci_slo_gate | UNVERIFIED | ALREADY-REAL | scripts/ci_slo_gate.py:15 |
| 33 | C/scripts.scan-secrets | UNVERIFIED | ALREADY-REAL | scripts/scan-secrets.sh:60 |
| 34 | C/scripts.check-locks | UNVERIFIED | ALREADY-REAL | scripts/check-locks.sh:1 |
| 35 | A/ARCH-02 | DECORATIVE | ALREADY-REAL | tools/arch/policy.py:112 (ARCH-004, import cycles) |
| 36 | A/ARCH-03 | DECORATIVE | ALREADY-REAL | tools/arch/policy.py:136 (ARCH-007, lazy-import hoist) |
| 37 | A/ARCH-04 | DECORATIVE | ALREADY-REAL | tools/arch/policy.py:105 (ARCH-003, env-var declaration) |
| 38 | A/ARCH-05 | DECORATIVE | ALREADY-REAL | tools/arch/policy.py:148 (ARCH-008, WARNING severity by documented design) |
| 39 | A/ARCH-06 | DECORATIVE | ALREADY-REAL | tools/arch/impact.py + architecture.yml:99 (advisory --strict impact) |
| 40 | A/test_env_perms | DECORATIVE | ALREADY-REAL | tests/test_env_perms.py (unit lane, census miscredit) |
| 41 | A/test_internal_prints_routed | DECORATIVE | ALREADY-REAL | tests/test_internal_prints_routed.py (unit lane) |
| 42 | A/test_pipeline_concurrent | DECORATIVE | ALREADY-REAL | tests/test_pipeline_concurrent.py (unit lane) |
| 43 | A/test_publish_lockfree | DECORATIVE | ALREADY-REAL | tests/test_publish_lockfree.py (unit lane) |
| 44 | A/test_reconcile_lockfree | DECORATIVE | ALREADY-REAL | tests/test_reconcile_lockfree.py (unit lane) |
| 45 | A/test_hashtag_attribution_severance | DECORATIVE | ALREADY-REAL | tests/test_hashtag_attribution_severance.py (unit lane) |
| 46 | A/test_ledger_sqlite_store | DECORATIVE | ALREADY-REAL | tests/test_ledger_sqlite_store.py (unit lane) |
| 47 | A/test_no_ghosts | DECORATIVE | ALREADY-REAL | tests/test_no_ghosts.py (unit lane) |
| 48 | A/test_secret_provider | DECORATIVE | ALREADY-REAL | tests/test_secret_provider.py (unit lane) |
| 49 | A/test_secret_write_routing | DECORATIVE | ALREADY-REAL | tests/test_secret_write_routing.py (unit lane) |
| 50 | A/test_swallow_ratchet | DECORATIVE | ALREADY-REAL | tests/test_swallow_ratchet.py (unit lane, 49-file baseline ratchet) |
| 51 | A/test_config_doc_drift | DECORATIVE | ALREADY-REAL | tests/test_config_doc_drift.py (unit lane) |
| 52 | A/test_account_first_e2e | DECORATIVE | ALREADY-REAL | tests/test_account_first_e2e.py (slow-marked → e2e lane ci.yml:229) |
| 53 | A/test_per_persona_e2e | DECORATIVE | ALREADY-REAL | tests/test_per_persona_e2e.py (slow-marked → e2e lane) |
| 54 | A/test_ci_require_e2e | DECORATIVE | ALREADY-REAL | tests/test_ci_require_e2e.py (path-run in unit job ci.yml:87–93) |
| 55 | A/test_variation_render | DECORATIVE | ALREADY-REAL | tests/integration/test_variation_render.py:5 (integration-marked → e2e lane; census root-dir miscredit; lane pinned by tests/test_integration_marker_guard.py) |
| 56 | B/ci-timing | DECORATIVE | ALREADY-REAL | ci.yml:244 job (advisory artifact builder; census anchor was off ~170 lines) |
| 57 | B/nightly.pipaudit | DECORATIVE | ALREADY-REAL | nightly.yml:37–38 (continue-on-error: true — advisory) |
| 58 | C/scripts.check_scope | DECORATIVE | ALREADY-REAL | scripts/check_scope.py:132, invoked by scripts/check.sh:74,95 |
| 59 | A/test_version_consistency | DECORATIVE | ALREADY-REAL-STRUCTURAL | src/fanops/__init__.py derives __version__ via importlib.metadata — no second literal to drift; the named test never existed and would assert a tautology; STD-VER-01 trailer stripped in PR-3 |
| 60 | B/arch.gate | INERT | ADVISORY-DOCUMENTED (PR-3) | architecture.yml gate leg — real advisory automation, no gate claim |
| 61 | B/arch.impact | INERT | ADVISORY-DOCUMENTED (PR-3) | architecture.yml impact leg |
| 62 | B/arch.controls | INERT | ADVISORY-DOCUMENTED (PR-3) | architecture.yml controls leg |
| 63 | B/lane-guard | INERT | ADVISORY-DOCUMENTED (PR-3) | lane-guard.yml |
| 64 | C/scripts.lane_guard | INERT | ADVISORY-DOCUMENTED (PR-3) | scripts/lane_guard.py |
| 65 | C/scripts.pr_collision_guard | INERT | ADVISORY-DOCUMENTED (PR-3) | scripts/pr_collision_guard.py |
| 66 | B/base-install | INERT | ADVISORY-DOCUMENTED (PR-3) | real non-required PR smoke job |
| 67 | C/scripts.base_install_smoke | INERT | ADVISORY-DOCUMENTED (PR-3) | scripts/base_install_smoke.py |
| 68 | B/nightly.asr | INERT | SCHEDULED-DOCUMENTED (PR-3) | nightly.yml — independent, never touches the e2e job |
| 69 | C/arch.selftest | INERT | MACHINERY-DOCUMENTED (PR-3) | tooling entrypoint, not a gate |
| 70 | C/arch.impact | INERT | MACHINERY-DOCUMENTED (PR-3) | tooling entrypoint |
| 71 | C/arch.main | INERT | MACHINERY-DOCUMENTED (PR-3) | tooling entrypoint |
| 72 | C/ci.main | INERT | MACHINERY-DOCUMENTED (PR-3) | tooling entrypoint |
| 73 | C/scripts.check_sh | INERT | MACHINERY-DOCUMENTED (PR-3) | local dev entrypoint (lint-only runner) |
| 74 | C/scripts.repo_sweep | INERT | MACHINERY-DOCUMENTED (PR-3) | read-only sweep; consumed by scripts/orchestrate.py:64,67,77 |
| 75 | A/DC-3 | INERT | ON-DEMAND-DOCUMENTED (PR-3; first exercise gate 2) | python -m tools.ci deployed --require-live (tools/ci/cli.py:40); invoked by no workflow; defined trigger = after any branch-protection change |
| 76 | C/ci.dc3 | INERT | ON-DEMAND-DOCUMENTED (PR-3; first exercise gate 2) | same mechanism, duplicate surface |

Sum check: 12 DEAD + 5 RESOLVED + 2 DEMOTE + 39 ALREADY-REAL + 1 STRUCTURAL + 15 DOCUMENTED + 2 ON-DEMAND = 76
(= 29 INERT + 25 DECORATIVE + 16 UNVERIFIED + 5 UNFALSIFIABLE + 1 SELF-REF). COMPILE rows: ZERO.
