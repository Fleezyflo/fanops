# Theatre census - FanOps @ main

Machine-derived; every row's class is recomputed by `verify_theatre.py` from anchors + captured
executions. `python verify_theatre.py` exits 0 iff this file is honest against the tree. The
.claude agent harness (hooks / hookify / GateGuard) is EXCLUDED as orchestration above this repo,
not FanOps. The two strata are kept separate below.

**SYSTEM — 60 of 62 toggles LIVE, 2 of 15 gates REAL. The product enforces.**
  src/fanops product code — the clip + cross-post engine itself
  NOT-A-TOGGLE 2 · UNVERIFIED 13 · REAL 2 · LIVE 60

**GOVERNANCE — 25 of 101 gates injection-proven; 60 enforce nothing on a PR.**
  the governance/orchestration the project imposes on its own changes (docs, CI, contract engines)
  DECORATIVE 25 · INERT 29 · UNFALSIFIABLE 5 · SELF-REF 1 · UNVERIFIED 16 · REAL 25

| class | n | meaning |
|---|---|---|
| DECORATIVE | 25 | claims enforcement; no executable mechanism acts on a violation |
| INERT | 29 | mechanism exists but off the required path (advisory / orphan / unwired) |
| UNFALSIFIABLE | 5 | wired + required, but the captured control shows it cannot fail |
| SELF-REF | 1 | effect is solely emitting the artifact that asserts its own pass |
| NOT-A-TOGGLE | 2 | false-positive: matched the grep but is not an os.environ switch |
| UNVERIFIED | 29 | wired, but the blocking control needs CI-pytest / live service / ledger / self-trip |
| REAL | 27 | captured control injected a violation and it was caught / switch live |
| LIVE | 60 | toggle flip produced a real captured behavior delta |

# SYSTEM — src/fanops product code — the clip + cross-post engine itself

## D/toggle  (62: LIVE 60, NOT-A-TOGGLE 1, UNVERIFIED 1)
NOT-A-TOGGLE  D/FANOPS_CFG  def:src/fanops/studio/app.py:155  reads:3  gate:∅  ctl:n/a (not an os.environ toggle)->n/a/NO-OP
UNVERIFIED(needs-launchd-plist runtime)  D/FANOPS_DAEMON_INTERVAL  def:src/fanops/daemon.py:134  reads:4  gate:∅  ctl:UNVERIFIED(needs-launchd-plist ...->UNVERIFIED/needs-launchd-plist runtime
LIVE  D/FANOPS_ACCOUNT_CASTING  def:src/fanops/settings.py:26  reads:9  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_ADJUST_PER_SURFACE  def:src/fanops/settings.py:28  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_ARTIST_NAME  def:src/fanops/settings.py:33  reads:4  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_ASR_LANGUAGE  def:src/fanops/settings.py:34  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_ASR_MODEL  def:src/fanops/settings.py:34  reads:8  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_AUTO_ADOPT  def:src/fanops/daemon.py:368  reads:1  gate:∅  ctl:python -c '<Config/daemon reads...->0/FANOPS_AUTO_ADOPT consumed at import/...
LIVE  D/FANOPS_AWARE_REFRAME  def:src/fanops/settings.py:25  reads:4  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_BURN_SUBS  def:src/fanops/settings.py:25  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_CLIP_PROFILE  def:src/fanops/settings.py:34  reads:14  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_CONCURRENT_SOURCES  def:src/fanops/settings.py:29  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_CONCURRENT_WORKERS  def:src/fanops/settings.py:212  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/4->11
LIVE  D/FANOPS_CORPUS_AUTO  def:src/fanops/settings.py:23  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_CORPUS_TARGET  def:src/fanops/settings.py:163  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/12->19
LIVE  D/FANOPS_GC_KEEP_DAYS  def:src/fanops/settings.py:203  reads:4  gate:∅  ctl:python -c '<Settings flip FANOP...->0/30->37
LIVE  D/FANOPS_HASHTAG_TRENDS  def:src/fanops/settings.py:23  reads:4  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_HOOK_ROUTER  def:src/fanops/settings.py:26  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_IG_RETENTION_PROOF  def:src/fanops/settings.py:28  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_IMPACT_CUT  def:src/fanops/settings.py:26  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_INTRO_TEASE  def:src/fanops/settings.py:26  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_ISOLATE_VOCALS  def:src/fanops/settings.py:25  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_LIVE  def:src/fanops/settings.py:23  reads:33  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_LLM_MODEL  def:src/fanops/settings.py:33  reads:7  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_LLM_TRANSPORT  def:src/fanops/settings.py:33  reads:18  gate:∅  ctl:python -c '<Settings flip FANOP...->0/read+validated (invalid value rejected)
LIVE  D/FANOPS_MEDIA_PUBLIC_BASE  def:src/fanops/settings.py:151  reads:8  gate:∅  ctl:python -c '<Settings flip FANOP...->0/None->'1'
LIVE  D/FANOPS_MOMENT_HOOK_LEARNING  def:src/fanops/settings.py:29  reads:4  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_OPERATOR_TZ  def:src/fanops/settings.py:35  reads:5  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_P4_DIM_BIAS  def:src/fanops/settings.py:28  reads:6  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_P4_MIN_REACH_GAP  def:src/fanops/settings.py:202  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/0.0->1.5
LIVE  D/FANOPS_POSTER  def:src/fanops/settings.py:33  reads:70  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_POSTIZ_AUTOSTART  def:src/fanops/settings.py:30  reads:4  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_POSTIZ_COMPOSE_DIR  def:src/fanops/settings.py:214  reads:4  gate:∅  ctl:python -c '<Settings flip FANOP...->0/None->'1'
LIVE  D/FANOPS_POSTIZ_ONDEMAND  def:src/fanops/daemon.py:726  reads:3  gate:∅  ctl:python -c '<Config/daemon reads...->0/FANOPS_POSTIZ_ONDEMAND consumed at im...
LIVE  D/FANOPS_POSTIZ_PUBLISH_PER_MIN  def:src/fanops/settings.py:210  reads:4  gate:∅  ctl:python -c '<Settings flip FANOP...->0/4->11
LIVE  D/FANOPS_PUBLISH_LEAD_MINUTES  def:src/fanops/settings.py:208  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/0->7
LIVE  D/FANOPS_QUEUE_GATE  def:src/fanops/settings.py:24  reads:4  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_REALISTIC_CADENCE  def:src/fanops/settings.py:29  reads:5  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_REQUIRE_FULL_OBJECTIVE  def:src/fanops/settings.py:23  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_RESPONDER  def:src/fanops/settings.py:33  reads:35  gate:∅  ctl:python -c '<Settings flip FANOP...->0/read+validated (invalid value rejected)
LIVE  D/FANOPS_ROOT  def:src/fanops/config.py:145  reads:13  gate:∅  ctl:python -c '<Config/daemon reads...->0/FANOPS_ROOT consumed at import/daemon...
LIVE  D/FANOPS_SHOW_EXTRAS  def:src/fanops/settings.py:24  reads:5  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_SMART_FRAMING  def:src/fanops/settings.py:23  reads:9  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_SOURCE_SHARD_MIN  def:src/fanops/settings.py:205  reads:4  gate:∅  ctl:python -c '<Settings flip FANOP...->0/45->52
LIVE  D/FANOPS_SUBTITLE_FONT  def:src/fanops/settings.py:35  reads:4  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_TIMING_BIAS  def:src/fanops/settings.py:28  reads:5  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_UPLOAD_MAX_MB  def:src/fanops/settings.py:204  reads:7  gate:∅  ctl:python -c '<Settings flip FANOP...->0/2048->2055
LIVE  D/FANOPS_VARIANT_AMPLIFY  def:src/fanops/settings.py:27  reads:21  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_VARIANT_AMPLIFY_MIN_GAP  def:src/fanops/settings.py:190  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/25.0->26.5
LIVE  D/FANOPS_VARIANT_AMPLIFY_MIN_POSTS  def:src/fanops/settings.py:189  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/8->15
LIVE  D/FANOPS_VARIANT_AMPLIFY_MIN_STREAK  def:src/fanops/settings.py:191  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/3->10
LIVE  D/FANOPS_VARIANT_LEARNING  def:src/fanops/settings.py:27  reads:12  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_VARIANT_MIN_GAP  def:src/fanops/settings.py:187  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/10.0->11.5
LIVE  D/FANOPS_VARIANT_MIN_POSTS  def:src/fanops/settings.py:186  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/3->10
LIVE  D/FANOPS_VARIANT_TRANSFER  def:src/fanops/settings.py:27  reads:15  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_VARIANT_TRANSFER_MAX_HOOKS  def:src/fanops/settings.py:196  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/2->9
LIVE  D/FANOPS_VARIANT_TRANSFER_MIN_DONORS  def:src/fanops/settings.py:195  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/2->9
LIVE  D/FANOPS_VARIANT_UCB  def:src/fanops/settings.py:27  reads:13  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_VARIANT_UCB_C  def:src/fanops/settings.py:193  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/1.4142135623730951->2.914213562373095
LIVE  D/FANOPS_VISUAL_START  def:src/fanops/settings.py:25  reads:3  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_WHISPER_MODEL  def:src/fanops/settings.py:34  reads:5  gate:∅  ctl:python -c '<Settings flip FANOP...->0/''->'1'
LIVE  D/FANOPS_ZERNIO_MAX_UPLOAD_MB  def:src/fanops/settings.py:209  reads:4  gate:∅  ctl:python -c '<Settings flip FANOP...->0/4->11

## D/toggle-extra  (7: UNVERIFIED 6, NOT-A-TOGGLE 1)
NOT-A-TOGGLE  D/FANOPS_X  def:tools/arch/policy.py:345  reads:1  gate:∅  ctl:n/a (not an os.environ toggle)->n/a/NO-OP
UNVERIFIED(not-a-Settings-field)  D/FANOPS_CHECK_ALLOW_NO_TESTS  def:scripts/check.sh:76  reads:3  gate:∅  ctl:python -c '<Settings flip>'->UNVERIFIED/not-a-Settings-field
UNVERIFIED(not-a-Settings-field)  D/FANOPS_FIXTURE_ROOT  def:scripts/gen_framing_vectors.py:178  reads:2  gate:∅  ctl:python -c '<Settings flip>'->UNVERIFIED/not-a-Settings-field
UNVERIFIED(not-a-Settings-field)  D/FANOPS_LOCAL_TESTS  def:scripts/check.sh:8  reads:3  gate:∅  ctl:python -c '<Settings flip>'->UNVERIFIED/not-a-Settings-field
UNVERIFIED(not-a-Settings-field)  D/FANOPS_NC_STALE_DOC  def:tools/arch/selftest.py:294  reads:1  gate:∅  ctl:python -c '<Settings flip>'->UNVERIFIED/not-a-Settings-field
UNVERIFIED(not-a-Settings-field)  D/FANOPS_NC_UNDECLARED  def:tools/arch/selftest.py:158  reads:1  gate:∅  ctl:python -c '<Settings flip>'->UNVERIFIED/not-a-Settings-field
UNVERIFIED(not-a-Settings-field)  D/FANOPS_REQUIRE_STUDIO  def:scripts/check-full.sh:5  reads:2  gate:∅  ctl:python -c '<Settings flip>'->UNVERIFIED/not-a-Settings-field

## E/cold-start  (8: UNVERIFIED 6, REAL 2)
UNVERIFIED(needs-ledger+live-metrics harness)  E/dim_collecting_progress  def:src/fanops/validation_gate.py:42  reads:0  gate:src/fanops/validation_gate.py:42  ctl:UNVERIFIED(needs-ledger+live-me...->UNVERIFIED/needs-ledger+live-metrics harness
UNVERIFIED(needs-live-Postiz/Meta analytics)  E/doctor.setup_state  def:src/fanops/doctor.py:15  reads:0  gate:src/fanops/doctor.py:15  ctl:UNVERIFIED(needs-live-Postiz/Me...->UNVERIFIED/needs-live-Postiz/Meta analytics
UNVERIFIED(needs-ledger+live-metrics harness)  E/enough_attributed_signal  def:src/fanops/validation_gate.py:32  reads:0  gate:src/fanops/validation_gate.py:32  ctl:UNVERIFIED(needs-ledger+live-me...->UNVERIFIED/needs-ledger+live-metrics harness
UNVERIFIED(needs-ledger+live-metrics harness)  E/gate_source_id  def:src/fanops/gate_keys.py:6  reads:0  gate:src/fanops/gate_keys.py:6  ctl:UNVERIFIED(needs-ledger+live-me...->UNVERIFIED/needs-ledger+live-metrics harness
UNVERIFIED(needs-live-Postiz/Meta analytics)  E/learn_doctor  def:src/fanops/learn_doctor.py:28  reads:0  gate:src/fanops/learn_doctor.py:28  ctl:UNVERIFIED(needs-live-Postiz/Me...->UNVERIFIED/needs-live-Postiz/Meta analytics
UNVERIFIED(needs-ledger+live-metrics harness)  E/p4_unlocked  def:src/fanops/validation_gate.py:52  reads:0  gate:src/fanops/validation_gate.py:52  ctl:UNVERIFIED(needs-ledger+live-me...->UNVERIFIED/needs-ledger+live-metrics harness
REAL  E/is_weak_hook  def:src/fanops/hookcheck.py:30  reads:0  gate:src/fanops/hookcheck.py:30  ctl:python -c '<is_weak_hook empty ...->0/True False
REAL  E/learning_validated  def:src/fanops/validation_gate.py:22  reads:0  gate:src/fanops/validation_gate.py:3  ctl:python -c '<learning_validated ...->0/closed

# GOVERNANCE — the governance/orchestration the project imposes on its own changes (docs, CI, contract engines)

## A/enforcer-index  (48: DECORATIVE 22, REAL 18, UNVERIFIED 7, INERT 1)
DECORATIVE  A/ARCH-02  def:docs/ARCHITECTURAL_LAWS.md:57  reads:6  gate:∅  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
DECORATIVE  A/ARCH-03  def:docs/ARCHITECTURAL_LAWS.md:63  reads:4  gate:∅  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
DECORATIVE  A/ARCH-04  def:docs/ARCHITECTURAL_LAWS.md:69  reads:4  gate:∅  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
DECORATIVE  A/ARCH-05  def:docs/ARCHITECTURAL_LAWS.md:75  reads:1  gate:∅  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
DECORATIVE  A/ARCH-06  def:docs/ARCHITECTURAL_LAWS.md:81  reads:1  gate:∅  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
DECORATIVE  A/test_account_first_e2e  def:docs/ARCHITECTURAL_LAWS.md:95  reads:2  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
DECORATIVE  A/test_ci_require_e2e  def:docs/ENGINEERING_STANDARDS.md:210  reads:9  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
DECORATIVE  A/test_config_doc_drift  def:docs/ENGINEERING_STANDARDS.md:365  reads:5  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
DECORATIVE  A/test_env_perms  def:docs/ARCHITECTURAL_LAWS.md:221  reads:3  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
DECORATIVE  A/test_hashtag_attribution_severance  def:docs/REPOSITORY_CONSTITUTION.md:78  reads:12  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
DECORATIVE  A/test_internal_prints_routed  def:docs/ARCHITECTURAL_LAWS.md:169  reads:10  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
DECORATIVE  A/test_ledger_sqlite_store  def:docs/REPOSITORY_CONSTITUTION.md:107  reads:6  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
DECORATIVE  A/test_no_ghosts  def:docs/REPOSITORY_CONSTITUTION.md:75  reads:10  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
DECORATIVE  A/test_per_persona_e2e  def:docs/REPOSITORY_CONSTITUTION.md:75  reads:6  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
DECORATIVE  A/test_pipeline_concurrent  def:docs/ARCHITECTURAL_LAWS.md:137  reads:2  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
DECORATIVE  A/test_publish_lockfree  def:docs/ARCHITECTURAL_LAWS.md:137  reads:6  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
DECORATIVE  A/test_reconcile_lockfree  def:docs/ARCHITECTURAL_LAWS.md:137  reads:4  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
DECORATIVE  A/test_secret_provider  def:docs/REPOSITORY_CONSTITUTION.md:237  reads:3  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
DECORATIVE  A/test_secret_write_routing  def:docs/REPOSITORY_CONSTITUTION.md:237  reads:5  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
DECORATIVE  A/test_swallow_ratchet  def:docs/REPOSITORY_CONSTITUTION.md:120  reads:16  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
DECORATIVE  A/test_variation_render  def:docs/ENGINEERING_STANDARDS.md:210  reads:19  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
DECORATIVE  A/test_version_consistency  def:docs/ENGINEERING_STANDARDS.md:126  reads:1  gate:∅  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
INERT  A/DC-3  def:tools/ci/checks.py:63  reads:51  gate:tools/ci/checks.py:63  ctl:UNVERIFIED(needs-live-Postiz/Me...->UNVERIFIED/needs-live-Postiz/Meta analytics
UNVERIFIED(needs-CI-runner (pytest denied locally))  A/test_actions_  def:tests/test_studio_actions.py:239  reads:4  gate:tests/test_studio_actions.py:239  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
UNVERIFIED(needs-CI-runner (pytest denied locally))  A/test_every_rule_is_reachable  def:tests/test_arch_governance.py:107  reads:10  gate:tests/test_arch_governance.py:107  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
UNVERIFIED(needs-CI-runner (pytest denied locally))  A/test_field_authority_declares_all_six_attributes  def:tests/test_arch_governance.py:132  reads:3  gate:tests/test_arch_governance.py:132  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
UNVERIFIED(needs-CI-runner (pytest denied locally))  A/test_generated_artifacts_are_a_pure_function_of_the_source_tree  def:tests/test_arch_governance.py:59  reads:5  gate:tests/test_arch_governance.py:59  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
UNVERIFIED(needs-CI-runner (pytest denied locally))  A/test_post_  def:tests/test_contract_compiler.py:611  reads:18  gate:tests/test_contract_compiler.py:611  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
UNVERIFIED(needs-CI-runner (pytest denied locally))  A/test_reframe  def:tests/test_reframe_s2_d1a.py:169  reads:3  gate:tests/test_reframe_s2_d1a.py:169  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
UNVERIFIED(needs-CI-runner (pytest denied locally))  A/test_studio_  def:tests/test_postiz_trust_boundary.py:144  reads:67  gate:tests/test_postiz_trust_boundary.py:144  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
REAL  A/ARCH-001  def:tools/arch/policy.py:90  reads:6  gate:tools/arch/policy.py:90  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
REAL  A/ARCH-002  def:tools/arch/policy.py:98  reads:4  gate:tools/arch/policy.py:98  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
REAL  A/ARCH-003  def:tools/arch/policy.py:105  reads:16  gate:tools/arch/policy.py:105  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
REAL  A/ARCH-004  def:tools/arch/policy.py:112  reads:6  gate:tools/arch/policy.py:112  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
REAL  A/ARCH-006  def:tools/arch/policy.py:126  reads:14  gate:tools/arch/policy.py:126  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
REAL  A/ARCH-007  def:tools/arch/policy.py:19  reads:11  gate:tools/arch/policy.py:19  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
REAL  A/ARCH-008  def:tools/arch/policy.py:148  reads:7  gate:tools/arch/policy.py:148  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
REAL  A/ARCH-009  def:tools/arch/policy.py:155  reads:11  gate:tools/arch/policy.py:155  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
REAL  A/ARCH-01  def:tools/arch/policy.py:163  reads:5  gate:tools/arch/policy.py:163  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
REAL  A/DC-1  def:tools/ci/checks.py:21  reads:66  gate:tools/ci/checks.py:21  ctl:python -m tools.ci selftest->0/NC-DC1
REAL  A/DC-2  def:tools/ci/checks.py:39  reads:17  gate:tools/ci/checks.py:39  ctl:python -m tools.ci selftest->0/NC-DC1
REAL  A/DC-4  def:tools/ci/checks.py:92  reads:20  gate:tools/ci/checks.py:92  ctl:python -m tools.ci selftest->0/NC-DC1
REAL  A/DC-5  def:tools/ci/checks.py:115  reads:29  gate:tools/ci/checks.py:115  ctl:python -m tools.ci selftest->0/NC-DC1
REAL  A/DC-6  def:tools/ci/checks.py:145  reads:41  gate:tools/ci/checks.py:145  ctl:python -m tools.ci selftest->0/NC-DC1
REAL  A/IMPL-006  def:tools/arch/policy.py:210  reads:5  gate:tools/arch/policy.py:210  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
REAL  A/IMPL-007  def:tools/arch/policy.py:216  reads:24  gate:tools/arch/policy.py:216  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
REAL  A/IMPL-009  def:tools/arch/policy.py:239  reads:9  gate:tools/arch/policy.py:239  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
REAL  A/IMPL-010  def:tools/arch/policy.py:247  reads:6  gate:tools/arch/policy.py:247  ctl:python -m tools.arch selftest->0/25/25 injected defects detected

## B/ci-gate  (21: INERT 8, UNVERIFIED 6, UNFALSIFIABLE 3, DECORATIVE 2, REAL 2)
DECORATIVE  B/ci-timing  def:.github/workflows/ci.yml:74  reads:0  gate:∅  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
DECORATIVE  B/nightly.pipaudit  def:.github/workflows/nightly.yml:22  reads:0  gate:∅  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  B/arch.controls  def:.github/workflows/architecture.yml:13  reads:0  gate:tools/arch/selftest.py:45  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
INERT  B/arch.gate  def:.github/workflows/architecture.yml:55  reads:0  gate:tools/arch/policy.py:286  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
INERT  B/arch.impact  def:.github/workflows/architecture.yml:8  reads:0  gate:tools/arch/impact.py:35  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  B/base-install  def:.github/workflows/ci.yml:95  reads:0  gate:scripts/base_install_smoke.py:4  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  B/e2e.integration  def:.github/workflows/ci.yml:205  reads:0  gate:.github/workflows/ci.yml:205  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
INERT  B/e2e.slow  def:.github/workflows/ci.yml:222  reads:0  gate:.github/workflows/ci.yml:222  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
INERT  B/lane-guard  def:.github/workflows/lane-guard.yml:46  reads:0  gate:scripts/lane_guard.py:148  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  B/nightly.asr  def:.github/workflows/nightly.yml:3  reads:0  gate:.github/workflows/nightly.yml:3  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
UNFALSIFIABLE  B/e2e  def:.github/workflows/ci.yml:3  reads:0  gate:.github/workflows/ci.yml:153  ctl:python scripts/ci_e2e_trigger.p...->0/run=false (context reports green; sui...
UNFALSIFIABLE  B/e2e.gate  def:.github/workflows/ci.yml:142  reads:0  gate:scripts/ci_e2e_trigger.py:51  ctl:python scripts/ci_e2e_trigger.p...->0/run=false (context reports green; sui...
UNFALSIFIABLE  B/unit.envprobe  def:.github/workflows/ci.yml:57  reads:0  gate:scripts/ci_env_probe.py:16  ctl:grep -rnI --exclude-dir=reconci...->0/no failing exit path (matches=0)
UNVERIFIED(needs-CI-runner (pytest denied locally))  B/unit  def:.github/workflows/ci.yml:34  reads:0  gate:.github/workflows/ci.yml:6  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
UNVERIFIED(needs-CI-runner (pytest denied locally))  B/unit.hookverify  def:.github/workflows/ci.yml:6  reads:0  gate:.github/workflows/ci.yml:6  ctl:UNVERIFIED(needs-CI-runner (pyt...->UNVERIFIED/needs-CI-runner (pytest denied locally)
UNVERIFIED(needs-CI-runner (a PR diff to scan))  B/unit.lint  def:.github/workflows/ci.yml:59  reads:0  gate:.github/workflows/ci.yml:59  ctl:UNVERIFIED(needs-CI-runner (a P...->UNVERIFIED/needs-CI-runner (a PR diff to scan)
UNVERIFIED(needs-CI-runner (a PR diff to scan))  B/unit.lockdrift  def:.github/workflows/ci.yml:49  reads:0  gate:scripts/check-locks.sh:1  ctl:UNVERIFIED(needs-CI-runner (a P...->UNVERIFIED/needs-CI-runner (a PR diff to scan)
UNVERIFIED(needs-CI-runner (a PR diff to scan))  B/unit.secretscan  def:.github/workflows/ci.yml:41  reads:0  gate:scripts/scan-secrets.sh:60  ctl:UNVERIFIED(needs-CI-runner (a P...->UNVERIFIED/needs-CI-runner (a PR diff to scan)
UNVERIFIED(needs-CI-runner (a timing log to exceed budget))  B/unit.slo  def:.github/workflows/ci.yml:75  reads:0  gate:scripts/ci_slo_gate.py:15  ctl:UNVERIFIED(needs-CI-runner (a t...->UNVERIFIED/needs-CI-runner (a timing log to exce...
REAL  B/unit.archgov  def:.github/workflows/ci.yml:67  reads:0  gate:tools/arch/policy.py:90  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
REAL  B/unit.civalidator  def:.github/workflows/ci.yml:67  reads:0  gate:tools/ci/checks.py:21  ctl:python -m tools.ci selftest->0/NC-DC1

## C/engine  (32: INERT 20, REAL 5, UNVERIFIED 3, UNFALSIFIABLE 2, DECORATIVE 1, SELF-REF 1)
DECORATIVE  C/scripts.check_scope  def:scripts/check_scope.py:132  reads:0  gate:∅  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/arch.impact  def:tools/arch/impact.py:35  reads:0  gate:tools/arch/impact.py:35  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/arch.main  def:tools/arch/cli.py:32  reads:0  gate:tools/arch/cli.py:32  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/arch.selftest  def:tools/arch/selftest.py:45  reads:0  gate:tools/arch/selftest.py:45  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
INERT  C/ci.dc3  def:tools/ci/checks.py:63  reads:0  gate:tools/ci/checks.py:63  ctl:UNVERIFIED(needs-live-Postiz/Me...->UNVERIFIED/needs-live-Postiz/Meta analytics
INERT  C/ci.main  def:tools/ci/cli.py:8  reads:0  gate:tools/ci/cli.py:8  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/contract.adapters  def:tools/contract/adapters.py:3  reads:0  gate:tools/contract/adapters.py:3  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/contract.classify  def:tools/contract/classify.py:6  reads:0  gate:tools/contract/classify.py:6  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/contract.decide  def:tools/contract/decide.py:225  reads:0  gate:tools/contract/decide.py:225  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/contract.derive  def:tools/contract/derive.py:5  reads:0  gate:tools/contract/derive.py:5  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/contract.lifecycle  def:tools/contract/lifecycle.py:187  reads:0  gate:tools/contract/lifecycle.py:187  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/contract.main  def:tools/contract/__main__.py:25  reads:0  gate:tools/contract/__main__.py:25  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/contract.model  def:tools/contract/model.py:46  reads:0  gate:tools/contract/model.py:46  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/contract.parse  def:tools/contract/parse.py:58  reads:0  gate:tools/contract/parse.py:58  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/contract.report  def:tools/contract/report.py:25  reads:0  gate:tools/contract/report.py:25  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/contract.validate  def:tools/contract/validate.py:33  reads:0  gate:tools/contract/validate.py:33  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/scripts.base_install_smoke  def:scripts/base_install_smoke.py:4  reads:0  gate:scripts/base_install_smoke.py:4  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/scripts.check_sh  def:scripts/check.sh:1  reads:0  gate:scripts/check.sh:1  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/scripts.lane_guard  def:scripts/lane_guard.py:148  reads:0  gate:scripts/lane_guard.py:148  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/scripts.pr_collision_guard  def:scripts/pr_collision_guard.py:49  reads:0  gate:scripts/pr_collision_guard.py:49  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
INERT  C/scripts.repo_sweep  def:scripts/repo_sweep.py:20  reads:0  gate:scripts/repo_sweep.py:20  ctl:n/a (no mechanism to exercise)->n/a/NO-OP
UNFALSIFIABLE  C/scripts.ci_e2e_trigger  def:scripts/ci_e2e_trigger.py:51  reads:0  gate:scripts/ci_e2e_trigger.py:61  ctl:python scripts/ci_e2e_trigger.p...->0/run=false (context reports green; sui...
UNFALSIFIABLE  C/scripts.ci_env_probe  def:scripts/ci_env_probe.py:16  reads:0  gate:scripts/ci_env_probe.py:16  ctl:grep -rnI --exclude-dir=reconci...->0/no failing exit path (matches=0)
SELF-REF  C/contract.selftest  def:tools/contract/selftest.py:73  reads:0  gate:tools/contract/selftest.py:73  ctl:python -m tools.contract selftest->0/155/155 injected defects detected
UNVERIFIED(needs-CI-runner (a PR diff to scan))  C/scripts.check-locks  def:scripts/check-locks.sh:1  reads:0  gate:scripts/check-locks.sh:1  ctl:UNVERIFIED(needs-CI-runner (a P...->UNVERIFIED/needs-CI-runner (a PR diff to scan)
UNVERIFIED(needs-CI-runner (a timing log to exceed budget))  C/scripts.ci_slo_gate  def:scripts/ci_slo_gate.py:15  reads:0  gate:scripts/ci_slo_gate.py:15  ctl:UNVERIFIED(needs-CI-runner (a t...->UNVERIFIED/needs-CI-runner (a timing log to exce...
UNVERIFIED(needs-CI-runner (a PR diff to scan))  C/scripts.scan-secrets  def:scripts/scan-secrets.sh:60  reads:0  gate:scripts/scan-secrets.sh:60  ctl:UNVERIFIED(needs-CI-runner (a P...->UNVERIFIED/needs-CI-runner (a PR diff to scan)
REAL  C/arch.drift  def:tools/arch/drift.py:31  reads:0  gate:tools/arch/drift.py:31  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
REAL  C/arch.policy  def:tools/arch/policy.py:90  reads:0  gate:tools/arch/policy.py:90  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
REAL  C/arch.registries  def:tools/arch/registries.py:19  reads:0  gate:tools/arch/registries.py:19  ctl:python -m tools.arch selftest->0/25/25 injected defects detected
REAL  C/ci.checks  def:tools/ci/checks.py:21  reads:0  gate:tools/ci/checks.py:21  ctl:python -m tools.ci selftest->0/NC-DC1
REAL  C/ci.selftest  def:tools/ci/selftest.py:25  reads:0  gate:tools/ci/selftest.py:25  ctl:python -m tools.ci selftest->0/NC-DC1

