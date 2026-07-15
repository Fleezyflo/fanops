<!-- The Repository Constitution — authoritative explanation of how FanOps is intended to be engineered.
     Base: origin/main @ 04c4092 (#664), revalidated 2026-07-16. Reconciliation: docs/governance/EVIDENCE_RECONCILIATION.md.
     This document states INTENT + PHILOSOPHY. It is SUBORDINATE to executable source, live config, and
     accepted ADRs (the precedence in §2). It never overrides a measurement. Enforcement status per rule is
     HONEST — a rule marked `proposed`/`dormant`/`documented-only` is NOT presented as current reality. -->

# FanOps — Repository Constitution

> The permanent, authoritative account of how this system is *intended* to be engineered, reconciled
> against the current tracked tree (`origin/main` @ `04c4092`). Companion documents:
> `docs/ENGINEERING_PHILOSOPHY.md` (the *why* / instincts), `docs/ARCHITECTURAL_LAWS.md` (the enforceable
> laws with IDs), `docs/adr/` (the per-decision record), `.github/ci-control-registry.yml` (the CI control
> plane), `docs/ARCHITECTURE_GOVERNANCE.md` (the generated architecture view).

**How to read a rule.** Every rule carries six fields:
**Rule** · **Why** · **Evidence** (file:line / ADR / commit) · **Enforcement** (`enforced` · `partially-enforced` · `documented-only` · `dormant` · `proposed` · `accepted-residual`) · **Owner** (subsystem / governance plane) · **Violation** (consequence).

Enforcement is stated as it *is today*, not as intended. Where intent exceeds reality, the rule says so and points to `docs/governance/CONSTITUTION_IMPLEMENTATION_ROADMAP.md`.

---

## §1 — Purpose and Authority

**C1.1 — This constitution states intent and philosophy; it is subordinate to reality.**
Why: the repository's founding lesson is that prose which claims to be authority rots; only code and live config are authority. · Evidence: `ARCH-009` "Implementation wins over prose" (`docs/ARCHITECTURE_GOVERNANCE.md`); ADR-0100 precedence order. · Enforcement: **documented-only** (this document is prose by nature). · Owner: operator + governance planes. · Violation: if this document ever contradicts the code/ADRs, the code/ADRs win and this document is corrected (§18).

**C1.2 — Every rule declares its true enforcement status.**
Why: an aspirational rule presented as enforced "manufactures confidence" — the exact defect the governance engine exists to catch. · Evidence: `policy.py:9` "a rule that cannot fail is decoration that makes a dashboard green"; negative-control discipline. · Enforcement: **enforced** (by authorship discipline + the maintenance checks in `docs/governance/CONSTITUTION_MAINTENANCE.md`). · Owner: constitution maintainer. · Violation: a mislabeled rule is a documentation defect corrected on sight.

**C1.3 — Amendment is only via §18.**
Why: the constitutional layer must not itself become unreconciled prose. · Evidence: §18; ADR lifecycle (`ADR-FORMAT.md`). · Enforcement: **documented-only** (→ proposed automation in `CONSTITUTION_MAINTENANCE.md`). · Owner: operator. · Violation: an out-of-band edit is drift.

---

## §2 — Source-of-Truth Hierarchy

**C2.1 — Precedence order (binding).** When planes disagree, authority runs: **(1) executable source & tests → (2) live GitHub configuration → (3) accepted ADRs & registries → (4) generated docs → (5) historical prose.**
Why: three planes provably diverged (workflow YAML vs governance prose vs live branch protection); a single precedence resolves every conflict deterministically. · Evidence: ADR-0100 §Precedence; `.github/ci-control-registry.yml` header. · Enforcement: **partially-enforced** — precedence is adopted as CI law; the `tools/ci` validator (DC-1..DC-6, #661) mechanizes it, not yet a required gate. · Owner: CI governance (`tools/ci`) + ADR-0100. · Violation: a decision made on a lower plane against a higher one is reverted to the higher.

**C2.2 — Implementation wins over prose.**
Why: the signature defect across all audit cycles is "the doc names a mechanism that does not exist while the property survives via another." · Evidence: `ARCH-009` (BLOCKING); `kb/invariants.json:8`. · Enforcement: **enforced** (ARCH-009 blocks via `ARCH-GATE` + unit-lane arch tests). · Owner: architecture governance (`tools/arch`). · Violation: a DECLARED numeric claim that disagrees with the derived fact reddens the gate.

**C2.3 — A number copied into prose is a defect.**
Why: a count in prose rots (the `_CLI_PRINT_COUNT` saga: 9 copies, 4 values). · Evidence: `ARCH-007`/`IMPL-007`; #641. · Enforcement: **enforced** for the scanned surfaces (`.reports/`, `tools/arch/`, `docs/` per IMPL-007); **partially-enforced** for CI prose (DC-4 proposed). · Owner: `tools/arch` (IMPL-007) + `tools/ci` (DC-4, proposed). · Violation: a stale live-assignment fails the arch gate.

**C2.4 — Generated artifacts are pure functions of source, byte-verified.**
Why: a generated view that can drift silently is worse than no view. · Evidence: `ARCH-006`; `test_generated_artifacts_are_a_pure_function_of_the_source_tree`; ADR-0102 §4 (regenerate, never hand-merge). · Enforcement: **enforced** (`ARCH-GATE` byte-compare + drift gate). · Owner: `tools/arch`. · Violation: a hand-edited derived artifact fails the byte-compare.

**C2.5 — Truth is classified per field: DERIVED (from code) vs DECLARED (human judgment).**
Why: some facts are measured and win; some groupings are declared and stand — conflating them causes rot. · Evidence: `field_authority.json`; `common.py:13`. · Enforcement: **enforced** (`test_field_authority_declares_all_six_attributes`). · Owner: `tools/arch`. · Violation: an artifact without a declared authority class fails the governance test.

---

## §3 — Architecture Philosophy

**C3.1 — Architecture is measured, not asserted; the module set is a fact, the partition is a model.**
Why: an unmeasured architecture claim is unfalsifiable. · Evidence: `ARCH-001`/`ARCH-002` (partition totality: 130/130 modules, 0 ghosts). · Enforcement: **enforced**. · Owner: `tools/arch`. · Violation: an unassigned module or a ghost fails the gate.

**C3.2 — No new compile-time import cycle; a lazy import deferring a cycle may not be hoisted.**
Why: the 11-level layering exists only because certain imports are deferred; hoisting one "looks like a cleanup" and bricks process start. · Evidence: `ARCH-004`; `ARCH-007`/GB-1 (must-stay-lazy ratchet). · Enforcement: **enforced**. · Owner: `tools/arch`. · Violation: a hoisted must-stay-lazy edge reddens the gate.

**C3.3 — Every environment variable read is declared.**
Why: the env surface is a trust boundary; an undeclared read is an undocumented input to a live system. · Evidence: `ARCH-003` (extended to check `docs/CONFIG.md` name-set, #656). · Enforcement: **enforced**. · Owner: `tools/arch`. · Violation: an undeclared env read fails the gate.

**C3.4 — Side effects are censused; the blast radius is known.**
Why: env-writes, ledger txns, locks, subprocess, network, rmtree are the danger surface and must be counted, not guessed. · Evidence: `ARCH-008` (side-effect census, WARNING). · Enforcement: **partially-enforced** (WARNING severity). · Owner: `tools/arch`. · Violation: an unregistered side-effect site warns (not blocks) — a deliberate WARNING-tier residual.

---

## §4 — Domain and Ownership Philosophy

**C4.1 — One invariant, one owner; one implementation, one mechanism.**
Why: two copies of one truth drift (the 23-vs-24 control count divergence). · Evidence: `selftest.detect` "THE ONLY IMPLEMENTATION"; `health_model.py:210` "the ONE owner"; #646 producer==consumer across 96 states. · Enforcement: **partially-enforced** (per-invariant tests + `duplicate_group` justification in the CI registry; not a single global mechanism). · Owner: the owning subsystem per invariant. · Violation: unjustified duplicate ownership is a CI `DC-6` failure (proposed) / a review finding.

**C4.2 — Each persona owns its moment end-to-end; no per-account maps on a shared object.**
Why: per-account state hung on a shared moment is "the ghost" — decided-vs-clipped races and silent no-ops. · Evidence: `casting.affinity_admits` (single-owner gate, `affinities` len==1); memory `per-account-on-shared-object-is-the-ghost`. · Enforcement: **enforced** (tests: `test_per_persona_e2e.py`, `test_no_ghosts.py`). · Owner: picking / crosspost. · Violation: a re-introduced per-account fork fails the no-ghosts proof.

**C4.3 — Attribution has exactly one set of owners; severed inputs stay severed.**
Why: a hashtag's worth is live reach, never a post that used it — attribution belongs to hook/clip/account. · Evidence: `test_hashtag_attribution_severance` (`lift_score` invariant under added hashtags). · Enforcement: **enforced** (test). · Owner: learning / tracking. · Violation: a learning module reading `.hashtags` fails the severance test.

---

## §5 — State-Machine and Lifecycle Philosophy

**C5.1 — State is explicit per unit; setters are immutable; reserved states are inert until guarded.**
Why: hidden inference over entity state produces unprovable lifecycles; a reserved state with a live writer is an unguarded door. · Evidence: independent per-unit enums; `RenderState` reserved surface (INVENTORY SHIM). · Enforcement: **enforced** (type + tests). · Owner: models / ledger. · Violation: a `model_copy` mutation of a `Moment` (GB-5) or a new terminal writer breaks the guard.

**C5.2 — Nothing publishes without an explicit operator gate (no auto-publish).**
Why: a fan-ops engine that auto-publishes is a liability; the operator is the release authority. · Evidence: every Post born `awaiting_approval`; `publish_due`/`publish_now` iterate `queued` only (INV-08). · Enforcement: **enforced** (test goes red if violated). · Owner: post / studio approval lane. · Violation: any new path that publishes an unapproved post fails the approval-lifecycle proof.

**C5.3 — A terminal Post state has no unguarded door; `published` ⇒ `public_url`.**
Why: a post marked published without a URL is a data-integrity lie the learning loop trusts. · Evidence: `IMPL-009`/GB-4 (approved terminal-writer set); the R1 published-URL invariant (#657 regression-lock). · Enforcement: **enforced** (`IMPL-009` baseline + regression tests). · Owner: `tools/arch` + post. · Violation: a new write to `published`/`analyzed` outside the approved guarded set reddens the gate.

**C5.4 — Cascade preserves the live/worklist; retire is irreversible and product-gated.**
Why: destroying worklist state on a cascade is unrecoverable; whether to retire is a product decision, not an engineering default. · Evidence: cascade-preserve tests; `PD-3` "THE OPERATOR. No recommendation offered." · Enforcement: **enforced** (cascade tests) + **accepted-residual** (retire behavior blocked on `PD-3`). · Owner: pipeline + operator. · Violation: a cascade that drops a live post fails the preservation proof.

---

## §6 — Persistence and Data-Integrity Philosophy

**C6.1 — One SQLite/WAL ledger; every save is a full-document replace inside one write transaction; atomicity is per-transaction, never per-field.**
Why: one atomic, crash-durable store, safely writable by concurrent daemon/Studio/CLI. · Evidence: `ledger_sqlite.py` (WAL, `synchronous=FULL`, `0600`); catalogue `PERSIST-SINGLE-LEDGER-SUBSTRATE` (#475). · Enforcement: **enforced** (store-interface tests). · Owner: ledger. · Violation: a per-field write or a second substrate breaks the model.

**C6.2 — No network call and no heavy subprocess ever runs inside the ledger lock (the cardinal rule).**
Why: holding the single lock across a slow POST or a 30–60 min render blocks every writer to the lock timeout. · Evidence: `ARCHITECTURE_MANIFEST.md` §1; `pipeline.py:162` adopt-or-defer; #89. · Enforcement: **enforced** (lock-free tests; the 60 s pytest timeout is the deadlock guardrail). · Owner: ledger + pipeline + post. · Violation: I/O under the lock self-deadlocks and fails fast in CI.

**C6.3 — The ledger is never wiped implicitly; wipe is snapshot + typed-confirm gated; restore serializes on the ledger lock.**
Why: a wipe/restore data-loss race on the documented rollback path is a CRITICAL defect (RC-4/RC-5). · Evidence: **RC-4/RC-5 FIXED — #653** (`restore_snapshot` serializes on the lock), **#654** (`fanops restore` exposed), **#655** (S02 backend normalize); typed `REMOVE` + server-verified preview token (MOL-71). · Enforcement: **enforced** (post-fix; `test_ledger_sqlite_store` recovery + the RC regression locks). · Owner: ledger / studio. · Violation: an unserialized restore or an implicit wipe reintroduces RC-4/RC-5.

**C6.4 — Forward-compatibility is load-bearing: `extra="ignore"`; `extra="forbid"` is banned on a ledger model.**
Why: an older binary must parse a newer ledger and drop unknown keys, never crash. · Evidence: `models.py:171`; `IMPL-010`/GB-3 (BLOCKING). · Enforcement: **enforced**. · Owner: `tools/arch` + models. · Violation: `extra="forbid"` on a ledger model reddens the gate and would brick every reader.

---

## §7 — Failure and Ambiguity Philosophy

**C7.1 — Fail direction follows consequence (a three-branch rule, not "always fail open").**
Why: the safe direction differs by role — a verdict-producer, a degradable feature, and a correctness prerequisite fail in opposite directions. · Evidence: `select.deep_required(None)→True` (verdict → more checking); `errors.fail_open` (feature → safe default + logged breadcrumb); `require_cv2` raises (prerequisite → closed/loud). · Enforcement: **partially-enforced** (swallow ratchet + specific tests; the *direction choice* is a review judgment). · Owner: `errors` + each subsystem. · Violation: a prerequisite that fails open (a silent centre-crop) is the ADR-0034 defect.

**C7.2 — Fail-open always logs a surfaced breadcrumb; a new silent broad `except` is CI-red.**
Why: "logging ≠ surfacing" — a swallow routed to an unsurfaced channel hides real failure. · Evidence: `errors.fail_open` (`exc_info=True`); `test_swallow_ratchet.py` (49-file baseline). · Enforcement: **enforced** (AST ratchet) with a known limit: the ratchet accepts stdlib `logging`, so *surfaced-vs-unsurfaced* is still a review judgment (accepted-residual). · Owner: `errors` + review. · Violation: a new unlisted silent handler reddens the unit lane.

**C7.3 — A correctness prerequisite refuses loudly rather than degrade silently.**
Why: shipping a blind-centred clip when subject-aware framing was required is a silent quality regression. · Evidence: `require_cv2` → `ToolchainMissingError` (exit 2), ADR-0034; `CI-BASEINSTALL` proves the refusal. · Enforcement: **enforced** (`base_install_smoke.py`, a required-intent context). · Owner: framing + ci-lane. · Violation: a silent fallback where a refusal is required.

**C7.4 — Ambiguity is never resolved as success; UNKNOWN is never treated as safe.**
Why: an ambiguous network send recorded as "done" strands a post forever. · Evidence: `needs_reconcile` on ambiguous publish; `impact.py:11` "UNKNOWN_IMPACT NEVER TREATED AS SAFE." · Enforcement: **enforced** (reconcile path + `impact --strict`, advisory). · Owner: post / reconcile / `tools/arch`. · Violation: an ambiguous outcome collapsed to success.

---

## §8 — Provider and External-System Philosophy

**C8.1 — Publishing goes through a provider (Postiz / Zernio); the Meta Graph is read-only (metrics/trends).**
Why: one publish seam, one measurement seam — never conflated. · Evidence: catalogue `PROVIDER-PUBLISH-VS-MEASURE`; memory `fanops-account-connection-truth`. · Enforcement: **enforced** (routing tests). · Owner: providers. · Violation: a publish attempt via the Graph, or a metric read that mutates.

**C8.2 — A provider registry is the single home for who-publishes-a-channel; per-(handle×platform) routing is truth.**
Why: a handle's IG and TikTok are different integrations; a single `account_id` is only a legacy fallback. · Evidence: `providers.py` registry; `set_account_backend`; catalogue `PROVIDER-PER-CHANNEL-ROUTING`. · Enforcement: **enforced** (routing tests) + **accepted-residual** (`FANOPS_POSTER` legacy bridge). · Owner: providers / accounts. · Violation: channel routing decided outside the registry.

**C8.3 — `go_live` is the sole setter of `FANOPS_LIVE=1`, behind a four-step confirm gate.**
Why: flipping to live publishing is the highest-consequence operator action and must be single-sourced and gated. · Evidence: `golive.go_live` (accounts-valid → ≥1 live-ready channel → past-due-backlog → explicit confirm); INV-18. · Enforcement: **enforced** (go-live tests). · Owner: studio go-live. · Violation: any other setter of `FANOPS_LIVE=1`.

**C8.4 — An unknown poster / live value resolves to dryrun (never a false LIVE).**
Why: a mis-set flag must fail toward *not publishing*, never toward publishing. · Evidence: catalogue `PROVIDER-FAIL-SAFE-LIVE`. · Enforcement: **enforced**. · Owner: providers / config. · Violation: an unknown value surfacing a LIVE banner.

---

## §9 — Reconciliation and Recovery Philosophy

**C9.1 — Publish is a three-phase handshake: claim (committed pre-network) → network → finalize; reconcile owns the ambiguous.**
Why: the network call sits between two short transactions so a crash never loses the claim or double-publishes. · Evidence: catalogue `PUBLISH-CLAIM-NETWORK-FINALIZE` (#89); `PUBLISH-DOUBLE-PUBLISH-DEFENCE` (RC-1/RC-3b). · Enforcement: **enforced** (lock-free + double-publish tests). · Owner: post / reconcile. · Violation: a claim after the network, or a publish that can double-fire.

**C9.2 — The terminal ladder is a pure function of `(state, age)`.**
Why: a recovery decision that depends on hidden context is unprovable and non-deterministic. · Evidence: **RC-2** `fix(reconcile): the terminal ladder is a pure function of (state, age)` (#639). · Enforcement: **enforced** (RC-2 tests). · Owner: reconcile. · Violation: a ladder branch reading anything but `(state, age)`.

**C9.3 — Producer and consumer of a state share ONE capability, proven exhaustively.**
Why: a producer and consumer gating on *different* predicates strand posts (a live-but-credless backend mints a `submitting` post reconcile never resolves). · Evidence: **RC-3b** — `channel_provider_if_ready`, `producer_claim == is_live_backend` across all 96 states (#646). · Enforcement: **enforced** (exhaustive parity test). · Owner: providers / post / reconcile. · Violation: a duplicated conditional diverging producer from consumer.

**C9.4 — Recovery is second, orthogonal, and never a silent rewrite.**
Why: liveness needs a second verdict (a pump that logs every tick but never finishes a pass is dead); reconciliation reports drift, a human lands the fix. · Evidence: **RC-6** last-successful-pass verdict (#648); `ARCH-RECONCILE` "never a silent rewrite." · Enforcement: **enforced** (daemon tests) + **enforced** (reconcile job). · Owner: daemon / `tools/arch`. · Violation: a bot auto-committing the record, or a single liveness signal masking a stalled pass.

---

## §10 — Migration and Reversibility Philosophy

**C10.1 — A migration is justified only by a real on-disk shape change; it is additive, idempotent, copy-on-write, and never wipes.**
Why: a migration that can raise or drop data is a corruption vector; the ladder must be a safe hop-chain. · Evidence: `ledger.py:218` "Additive + idempotent + never-raising… copy-on-write"; `SCHEMA_VERSION` v0→v11. · Enforcement: **enforced** (migration round-trip tests). · Owner: ledger. · Violation: a raising or destructive migration step.

**C10.2 — A shape is broken only after all consumers are gone; the migration is the on-disk half of a teardown.**
Why: dropping a map while a reader exists bricks the load. · Evidence: `_migrate_v10_drop_selections` drops `account_selections` only after the P11 casting teardown removed every reader. · Enforcement: **enforced** (schema tests). · Owner: ledger. · Violation: a drop preceding consumer removal.

**C10.3 — A ledger newer than the running code is refused loudly, never loaded-and-field-dropped.**
Why: silently loading a forward ledger drops future fields on the next save. · Evidence: `ledger._NewerSchema`. · Enforcement: **enforced**. · Owner: ledger. · Violation: a silent downgrade.

**C10.4 — A new feature is byte-identical for legacy callers / when its flag is off.**
Why: default-on features are the system's purpose, but every one keeps a firewalled off-path proven identical. · Evidence: `FLAGS.md` firewall tests; "byte-identical when unchanged" (289 sites). · Enforcement: **enforced** (firewall tests). · Owner: each feature owner. · Violation: an off-path that diverges from legacy behavior.

---

## §11 — Testing and Evidence Philosophy

**C11.1 — Tests run in CI only; local suite execution is mechanically denied.**
Why: parallel local suites during a multi-agent wave crash the host; CI is the single test authority. · Evidence: `.claude/settings.json` denies `pytest`; ADR-0088 (catalogue `GOV-TESTS-CI-ONLY`); #605. · Enforcement: **enforced** (harness deny + `check.sh` guard). · Owner: ci-lane. · Violation: a local suite run risks the host and is blocked.

**C11.2 — A hanging test is the bug; the 60 s timeout is a deadlock guardrail, never raised to pass.**
Why: the timeout is a *detector* set far above real runtime; raising it re-hides a deadlock. · Evidence: `pyproject.toml:84`; `tests/CLAUDE.md`. · Enforcement: **enforced**. · Owner: ci-lane. · Violation: raising the timeout to green a hang.

**C11.3 — Test-first (RED→GREEN); required verification cannot vanish; a lock must break to fix, never pin the defect.**
Why: a green test asserting the wrong outcome is a regression-lock on the defect (RC-5). · Evidence: RED/GREEN commit pairs; `IMPL-006`; RC-5 lesson. · Enforcement: **enforced** (`IMPL-006`) + **documented-only** (the "lock the fix not the defect" discipline is a review judgment). · Owner: `tools/arch` + authors. · Violation: deleting a named invariant test, or locking a defect.

**C11.4 — A validator must be proven to fire; a rule without a negative control is decoration.**
Why: a silent no-op validator manufactures confidence (IMPL-007 caught exactly that in itself). · Evidence: 24–25 negative controls; `test_every_rule_is_reachable`; `CI-E2E-NEGCONTROLS` (required). · Enforcement: **enforced**. · Owner: `tools/arch` + `tools/ci`. · Violation: a policy rule with no firing control.

**C11.5 — Reachability and cost are measured against the live tree, not read from code — and re-verified at merge.**
Why: Cycle 4 named five merge gates and ran none; measuring the live tree collapsed three "blocking" risks. · Evidence: `CYCLE6_CORRECTIONS.md`; the three collapses (0 malformed backends / 0 stranded posts / 0 retired moments), re-armed as gates. · Enforcement: **partially-enforced** (the specific re-armed gates exist; the *practice* is a review discipline). · Owner: reviewers + `tools/arch`. · Violation: a cost/reachability claim asserted from reading, not measuring.

---

## §12 — CI and Governance Philosophy

> This section states the *philosophy*. The authoritative control inventory is `.github/ci-control-registry.yml`; the merge-gate policy is ADR-0101; the history policy is ADR-0102. This constitution cross-references them and never restates a control row.

**C12.1 — CI is the sole merge-quality authority; the control registry is the declared-intent plane.**
Why: with 0 required human reviews, CI *is* the gate — so the required set must be declared, not live only in GitHub's UI. · Evidence: ADR-0100 (registry = intent); ADR-0101 (`GOV-CI-ONLY-APPROVAL`); registry `intended_required_contexts`. · Enforcement: **partially-enforced** — registry + validator exist (#661); not yet a required blocking gate (rollout `transitioning`). · Owner: `tools/ci` + ADR-0100/0101. · Violation: a required context that exists only in the UI, unreconciled with the registry.

**C12.2 — Five intended required contexts, each a distinct merge-blocking invariant; today two are live.**
Why: `unit`, `e2e`, `base-install`, `gate`, `lane-guard` each own an invariant with no other blocking owner; duplicates are forbidden unless justified. · Evidence: ADR-0101 §1; registry `intended_required_contexts` (5) vs `current_required_contexts` (2). · Enforcement: **partially-enforced / proposed** — 2 live (`unit`,`e2e`); the other 3 + `enforce_admins` are Phase-E, one at a time. · Owner: ADR-0101 / operator. · Violation: promoting/removing a required check outside the registry+Phase-E path.

**C12.3 — No bot silently rewrites the governance-of-record; reconciliation reports drift, a human lands the fix.**
Why: a bot editing branch protection or a derived artifact is the opposite of governance. · Evidence: ADR-0100 §Rejected (auto-committing reconciliation); `ARCH-RECONCILE` (reviewable diff, never a silent rewrite). · Enforcement: **enforced** (reconcile jobs never auto-commit). · Owner: `tools/arch` + `tools/ci`. · Violation: an auto-mutation of BP or a canonical artifact.

**C12.4 — Architecture governance (`tools/arch`) and CI governance (`tools/ci`) share method, not ownership.**
Why: fusing them into one subsystem was rejected by operator amendment; they reference each other, stay distinct. · Evidence: ADR-0100 §Validator-host. · Enforcement: **enforced** (separate modules/owners). · Owner: both planes. · Violation: hosting one plane's validator inside the other.

**C12.5 — Squash-only, linear history; generated-artifact conflicts are regenerated, never hand-merged.**
Why: squash gives one revertable commit per PR and kills the stacked-integration-merge conflict class; a hand-merged derived file is drift by definition. · Evidence: ADR-0102 §1/§4; `required_linear_history` (Phase E). · Enforcement: **partially-enforced / proposed** — the #637+ convention holds; `required_linear_history` is Phase-E; the derived-file rule is already enforced by the arch drift gate. · Owner: ADR-0102 / operator. · Violation: a merge commit on `main` (post-Phase-E) or a hand-merged derived artifact.

---

## §13 — Operational and Daemon Philosophy

**C13.1 — The pipeline is lazy and daemon-driven; ingest only catalogues; the launchd daemon is the hands-off driver.**
Why: a pull-only converge loop is restartable and idempotent; nothing happens until a tick pulls it. · Evidence: catalogue `OPS-LAZY-DAEMON-DRIVEN`; memory `fanops-pipeline-lazy-daemon-driven`. · Enforcement: **enforced** (pipeline design + tests). · Owner: cli / daemon. · Violation: eager side-effects at ingest time.

**C13.2 — One driver owns the converge loop (flock lease); liveness has a single owner plus an orthogonal pass verdict.**
Why: two drivers race; a single "alive" signal masks a pump that logs but never finishes a pass. · Evidence: `OPS-RUN-LEASE`; `daemon_progress` single owner; **RC-6** orthogonal `pass_verdict` (#648). · Enforcement: **enforced** (daemon tests). · Owner: daemon. · Violation: a second converge driver, or a liveness verdict from one signal only.

**C13.3 — A dead-man's switch guards autonomy; the heartbeat is the mutation signal itself.**
Why: an autonomous daemon must prove it is alive by doing work, not by asserting it. · Evidence: `OPS-DEAD-MANS-SWITCH`; the loop-origin heartbeat lands only after a pass completes. · Enforcement: **enforced**. · Owner: daemon. · Violation: a heartbeat decoupled from real progress.

**C13.4 — Runtime config reaches the resident daemon (per-tick `.env` reload); the daemon follows the code tree, not a stale baked path.**
Why: a live-flip that never reaches the running daemon is a silent no-op; a stale plist PATH pins the wrong binary. · Evidence: `OPS-ENV-RELOAD` (#… per-tick reload); `keeper-adopts-pump` (#628); memory `daemon-only-failures-check-plist-path`. · Enforcement: **enforced** (reload tests). · Owner: daemon / config. · Violation: a config change invisible to the resident process.

---

## §14 — Security and Secrets Philosophy

**C14.1 — Secrets are keyring-first; reads fail open, writes fail closed (round-trip verified).**
Why: a missing secret must degrade, but a *write* that silently fails would let the caller scrub the plaintext fallback believing it stored. · Evidence: `secret_provider.set_secret` (read-back-or-raise); catalogue `FOUND-SECRETS-ASYMMETRIC`. · Enforcement: **enforced** (`test_secret_provider`, `test_secret_write_routing`). · Owner: secrets. · Violation: a write that does not verify the round-trip.

**C14.2 — API keys are write-only; never rendered back to any surface.**
Why: an echoed key leaks through logs/UI. · Evidence: `go_live` (key never echoed); studio go-live contract. · Enforcement: **enforced**. · Owner: secrets / studio. · Violation: any surface rendering a stored key.

**C14.3 — No secret enters a PR diff; the scan has no bypass.**
Why: CI must never honor a local skip of the secret scan. · Evidence: `CI-UNIT-SECRETSCAN` (required); `scan-secrets.sh` "deliberately NO skip/bypass". · Enforcement: **enforced** (required sub-gate). · Owner: ci-lane. · Violation: a committed secret in a PR diff (blocked) — or adding a bypass env.

**C14.4 — The Studio is localhost, no-auth by design.**
Why: a single-operator localhost cockpit does not warrant an auth layer; this is a recorded, accepted decision, not an oversight. · Evidence: INV-21 `STUDIO-LAZY-FLASK-NOAUTH`; `kb/risks.json` AR-13 severity `ACCEPTED`. · Enforcement: **accepted-residual** (documented; re-evaluate if ever exposed beyond localhost). · Owner: studio / operator. · Violation: exposing the Studio beyond localhost without revisiting this residual.

---

## §15 — Evolution, Deletion, and Simplification Philosophy

**C15.1 — "Dead / zero-caller" is a LEAD, never a verdict; deletion requires a whole-tree AST + alias sweep and is revalidated at execution.**
Why: the name-based call graph mislabels aliased/lazy backends as dead (5 live functions once flagged); Cycle 8 cancelled 4 planned deletions on invalid premises. · Evidence: `GB-2`; `anomalies.md:145`; `cycle8-closure`. · Enforcement: **documented-only / partially-enforced** (GB-2 is a review discipline; the arch impact gate detects some breakage). · Owner: reviewers + `tools/arch`. · Violation: a deletion on a grep, not an AST census.

**C15.2 — Prefer the smallest correct solution; reject over-engineering on the record.**
Why: the cheapest fix that needs no migration beats an 8-file threading of a new state. · Evidence: `dryrun-boundary.plan.md:14` (rejected `PostState.ready`); YAGNI invocations. · Enforcement: **documented-only** (design judgment, recorded in plans/ADRs). · Owner: authors + operator. · Violation: added complexity with no demonstrated need.

**C15.3 — Keep cheap graceful-degradation scaffolding; the fix for a rotting copy is deletion.**
Why: speculative *insurance* (a migration framework shipping one no-op) is kept when cheap; speculative *complexity* is cut; a rotting number-copy is deleted, not re-explained. · Evidence: `SCAFFOLDING-VERDICT.md` (KEEP-3); "Deletion is the fix"; 0 `FIXME`/`HACK` in `src/`. · Enforcement: **documented-only**. · Owner: operator + authors. · Violation: hoarding speculative complexity, or re-prosing a stale copy instead of deleting it.

**C15.4 — A reversal is recorded and learned from, not quietly undone.**
Why: the per-frame reframe chase was tried and reverted; a decision record carries a Retractions section. · Evidence: `dynamic-reframer-built` (#228 reverted); `cv2-decision-record-v4.md` Retractions. · Enforcement: **documented-only** (ADR/commit-body discipline). · Owner: authors. · Violation: a silent reversal with no recorded rationale.

---

## §16 — Documentation and ADR Philosophy

**C16.1 — ADRs live in `docs/adr/NNNN-slug.md`; write one when a decision is hard-to-reverse ∧ surprising-without-context ∧ the result of a real trade-off.**
Why: the three-part test keeps the catalogue to decisions worth recording, not obvious ones. · Evidence: `.agents/skills/domain-modeling/ADR-FORMAT.md`; ADRs 0100–0103. · Enforcement: **partially-enforced** — the convention is active (four ADRs) but the format doc itself is **untracked** (remediation queued, §17.7); no gate checks ADR presence. · Owner: authors + operator. · Violation: a hard-to-reverse decision landed with no ADR.

**C16.2 — The catalogue (`docs/adr/README.md`) is the historical decision register; formalization into standalone ADRs is prioritized, not bulk-generated.**
Why: 99 back-filled decisions do not each need a file — only the Tier-1 safety/authority/irreversible ones do first. · Evidence: `docs/adr/README.md` §8; `docs/adr/FORMALIZATION_ROADMAP.md` (this layer). · Enforcement: **documented-only** (roadmap-driven). · Owner: operator. · Violation: auto-cutting 99 ADRs, or leaving a Tier-1 decision unformalized.

**C16.3 — Generated docs are views; hand-editing one is drift.**
Why: a generated doc that can be hand-edited stops being a faithful view. · Evidence: `ARCH-006`; `docs/ARCHITECTURE_GOVERNANCE.md` header. · Enforcement: **enforced** (byte-compare). · Owner: `tools/arch`. · Violation: a hand-edited generated artifact.

**C16.4 — Every governing document carries provenance and freshness; unregenerated prose is presumed rotting.**
Why: a doc without a base-SHA/date cannot be trusted against the current tree. · Evidence: the `<!-- provenance -->` header convention (this layer; `CI_ARCHITECTURE_REVIEW.md`); INV-20 (line anchors rot). · Enforcement: **documented-only** (→ proposed staleness detection in `CONSTITUTION_MAINTENANCE.md`). · Owner: constitution maintainer. · Violation: an authority doc with no provenance header.

---

## §17 — Accepted Residuals

A residual is acceptable only when it is **zero/low reachability, contained (ideally regression-locked), and documented here with an owner**. "A deferral is not a discharge."

| ID | Residual | Why accepted | Containment | Owner | Review |
|---|---|---|---|---|---|
| AR-1 | `RC-9` mutation-time invariant enforcement deferred | zero current reachability; runtime + fixture cost disproportionate | `S11` GUARD pins unreachability in CI (#657) | `tools/arch` / models | on any new mutation path |
| AR-2 | Studio localhost no-auth | single-operator localhost; recorded decision (INV-21 / AR-13) | not exposed beyond localhost | studio / operator | if ever exposed |
| AR-3 | `enforce_admins = false` (admin bypass) | historically a residual — **now a decision to change** | ADR-0101 §4: enable last + governed break-glass | operator / ADR-0101 | Phase E |
| AR-4 | Commit-message grammar unenforced | reviving a message gate = the dormant land-gate (0096), out of scope | #637+ convention holds in practice | ADR-0102 / operator | separate ADR only |
| AR-5 | ADR-0103 Track-A may show a non-speaking host | composition/routing provable from visual evidence; speaker *selection* needs diarization | `FANOPS_SMART_FRAMING=0` global rollback | reframe / operator | Track B (attribution) |
| AR-6 | Swallow ratchet accepts stdlib `logging` (not only surfaced channels) | "logging ≠ surfacing" is a review judgment the AST cannot make | ratchet blocks *new* silent swallows; surfacing is reviewed | reviewers | as ratchet evolves |
| AR-7 | `ADR-FORMAT.md` untracked; `FANOPS_POSTER` legacy bridge; `architecture.yml` prose counts | low blast radius; monitored | IMPL-007 / DC-4 catch number-rot going forward; roadmap tracks the untracked doc | governance planes | roadmap slices |
| AR-8 | Side-effect census is WARNING, not BLOCKING (`ARCH-008`) | a new registered site should warn, not deadlock a merge | census still runs every gate | `tools/arch` | if abused |

Residuals not listed here are **not** accepted — they are either fixed or tracked in `docs/governance/CONSTITUTION_IMPLEMENTATION_ROADMAP.md`.

---

## §18 — Amendment Process

**C18.1 — A constitutional change is proposed as an ADR.** A change to any rule here that alters a decision records an ADR (`docs/adr/NNNN-slug.md`), which owns the *why*; this document is then updated to reference it. · Enforcement: **documented-only**. · Owner: operator.

**C18.2 — A change to an *enforced* mechanism goes through its governance plane.** Architecture rules change via `tools/arch` (regenerate + gate); CI rules change via the control registry + `tools/ci` (a registry row edit in the same PR, ADR-0100/0101 lifecycle); a required-check change follows ADR-0101 §8 (six promotion criteria) + Phase-E (one at a time, pre-image captured). · Enforcement: **partially-enforced** (arch: enforced; CI: proposed validators). · Owner: the relevant plane.

**C18.3 — Provenance is preserved; history is never silently rewritten.** A superseded rule is marked superseded with a pointer, not deleted; the reconciliation record (`docs/governance/EVIDENCE_RECONCILIATION.md`) is appended, not edited away. · Enforcement: **documented-only** (→ proposed supersession-link validation, `CONSTITUTION_MAINTENANCE.md`). · Owner: constitution maintainer.

**C18.4 — Enforcement status is re-attested on amendment.** Any rule touched by an amendment must have its enforcement field re-verified against the current tree at that time (the same revalidation discipline that produced this document). · Enforcement: **documented-only** (→ proposed staleness/contradiction detection). · Owner: constitution maintainer.

---

### Appendix — Rule-to-Law-to-Control cross-reference

The enforceable subset of these rules is carried, with IDs and mechanisms, in `docs/ARCHITECTURAL_LAWS.md`; where a law is owned by CI it cross-references a `.github/ci-control-registry.yml` control id (never duplicated). Decisions are recorded in `docs/adr/` (catalogue + standalone ADRs); the formalization order is `docs/adr/FORMALIZATION_ROADMAP.md`. Gaps between a rule's intent and its enforcement are slices in `docs/governance/CONSTITUTION_IMPLEMENTATION_ROADMAP.md`.
