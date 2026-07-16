<!-- Evidence dossier — hand-authored analysis, grounded in repository behavior. FROZEN + SUPERSEDED.
     Generated 2026-07-15 against local HEAD 0a3b503 (#652) and a live branch-protection probe.
     Method: 6 parallel evidence sweeps + first-hand reads of tools/arch, pyproject.toml,
     docs/CI_ARCHITECTURE_REVIEW.md, commit bodies, and gh api branch protection.
     This is the material that WAS used to write the Repository Constitution — it is NOT the
     constitution, and it is no longer current. Superseded by docs/REPOSITORY_CONSTITUTION.md +
     docs/governance/EVIDENCE_RECONCILIATION.md (register E1). Tracked 2026-07-16 as historical
     evidence so the layer's E1 citation resolves in a fresh clone (SLICE-DOSSIER-RETIRE).
     Sibling to docs/CI_ARCHITECTURE_REVIEW.md (same hand-authored-audit convention). -->

# FanOps — Engineering Constitution: Evidence Dossier

> 🧊 **FROZEN + SUPERSEDED — historical evidence, not current truth. Do not cite as authority.**
> A **2026-07-15 snapshot** taken against `0a3b503` (#652). It is register **E1** in
> [`docs/governance/EVIDENCE_RECONCILIATION.md`](governance/EVIDENCE_RECONCILIATION.md), which
> adjudicated it as *"partly stale"* and superseded it. **Current authority:**
> [`docs/REPOSITORY_CONSTITUTION.md`](REPOSITORY_CONSTITUTION.md) (rules + honest enforcement status),
> [`docs/ARCHITECTURAL_LAWS.md`](ARCHITECTURAL_LAWS.md) (the enforceable subset),
> [`docs/ENGINEERING_PHILOSOPHY.md`](ENGINEERING_PHILOSOPHY.md) (the instincts).
> Its 30 principles were absorbed there; nothing is lost by treating this file as archaeology.
>
> **Why it is tracked rather than deleted.** `EVIDENCE_RECONCILIATION.md` cites it by path as E1. An
> untracked citation target resolves on exactly one machine and dangles in every fresh clone — the
> `arch-kb-was-never-in-git` meta-defect this repo already paid for once (R7). Tracking the artifact
> the layer was *derived from* preserves provenance and makes the derivation auditable.
> `docs/CI_ARCHITECTURE_REVIEW.md` was tracked for the same reason (#674).
>
> **Known-false claims, retained deliberately (recorded, not rewritten):**
> - **RC-4/RC-5 / `AR-02` restore race** — asserted here as a **live CRITICAL** data-loss defect.
>   **FIXED** by #653 (restore serializes on the ledger lock) + #654 (`fanops restore` exposed).
>   `LAW-PERSIST-02`: *"Residual: none (defect discharged)."*
> - **`architecture.yml` "21" injected defects** — the rotting count was **deleted** by #665.
> - **`CONFIG.md` "65"/"63" env vars vs a measured 73** — the number-in-prose is **gone**; `ARCH-003`
>   now mechanically checks `CONFIG.md`'s *name-set* instead of a count (#656). The defect class was
>   fixed at the root, exactly as P2 prescribes.
> - **"`CONFIG.md` cites a non-existent `system-lens-map.md`"** — **false when written**, not merely
>   rotted: `docs/CODEMAPS/system-lens-map.md` existed at this dossier's own base `0a3b503`.
> - **P30 / "the ADR convention sits dormant; `docs/adr/` is empty"** — **superseded**: the ADR system
>   is live (0100–0104) atop a tracked 1,723-line catalogue.
>
> These are left **in place, uncorrected**, per the repo's *"correct the record, don't quietly patch"*
> rule — a superseded register is evidence of what was believed and when, and silently fixing it would
> destroy that. The corrections above are the record. **Read this file only as history.**
>
> **Self-demonstrating note.** Four of this dossier's headline findings went stale within ~24h of
> authoring, and one was wrong on arrival. That is not an indictment of the method — it is P1/P2
> proving themselves on their own author: prose not mechanically regenerated from code rots, including
> *this* prose. The constitutional layer was written from the **live tree**, not from this snapshot,
> for precisely that reason (`ENGINEERING_PHILOSOPHY.md` §7).

**What this is.** The material needed to *write* a Repository Constitution with confidence — not the constitution itself. Every principle is reverse-engineered from how the repo behaves and cited to `file:line`, `#PR`, or a rule id. Where implementation and documentation disagree, the implementation is treated as authoritative (which is the repo's own first rule).

**The thesis every investigation converged on, independently:**

> **The code is the only source of truth. Prose not mechanically regenerated from the code is presumed rotting — and "the doc names a mechanism that does not exist while the property survives via a different one" is this repository's self-diagnosed signature defect, found in all five audit cycles** (`kb/invariants.json:8`, `ARCHITECTURE_GOVERNANCE.md:209`). The entire governance apparatus (Cycle 7, `tools/arch`) exists to mechanize that one sentence.

Context: a 2026-06-01 clean-slate rebuild, 1,462 commits in 45 days, near-fully autonomous (0 required human reviews on `main`) — which is *why* the constitution is enforced by machine rather than by culture.

---

## §1 — Candidate Constitutional Principles

Each: **claim** · *evidence* · ⟂ *counterexample / limit*.

### Cluster I — Source of Truth & Provenance

**P1 · Implementation wins over prose; the code is authoritative.**
*`ARCH-009` (BLOCKING): "Implementation wins over prose" (`ARCHITECTURE_GOVERNANCE.md:209`); generated docs are "a view… no hand-written facts" (`:11`); `field_authority.json`: "There is no case in which a document overrides a measurement."*
⟂ The rule indicts its own docs: `kb/invariants.json:110-152` enumerates **7 invariants FALSE-as-written**; `INV-20` finds nested-`CLAUDE.md` citations "10 of 10 STALE."

**P2 · A number copied into prose is a defect ("number rot").**
*`ARCH-007`/`ARCH-009`/`IMPL-007`; "a number in prose is a number that drifts" (`ARCH_RUNBOOK.md:30`); the `_CLI_PRINT_COUNT` saga — "NINE places… FOUR DIFFERENT VALUES… went stale in ONE COMMIT" (`policy.py:618`); #641 deletes the number, keeps the argument. "The judgments survived; the facts did not."*
⟂ Live violations persist: `architecture.yml` says "21" injected defects (actual 24 local / 25 `origin/main`); `CONFIG.md` says "65"/"63" env vars, engine measures **73**.

**P3 · A generated artifact is a pure function of the source tree — not the clock, machine, user, or git commit.**
*`ARCH-006`; `test_generated_artifacts_are_a_pure_function_of_the_source_tree` regenerates from a copy of `src/` placed outside any git repo and demands byte-identity (`test_arch_governance.py:59`); `reconcile` "NEVER SILENTLY REWRITES A CANONICAL ARTIFACT" (`architecture.yml:161`).*
⟂ Born from a real bug: artifacts once stamped `repository_commit: HEAD`, self-invalidating — "could never have been green on any commit, including the one that introduced it."

**P4 · A fingerprint/version bumps iff observable output changes; render-neutral change is quarantined from render-moving change.**
*`_REFRAME_GEOM_V` bumps only "after a geometry-math change" that alters rendered bytes (`clip.py:699`); centered clips omit `geom` from the hash and never re-render (`clip.py:723`); `SCHEMA_VERSION`, `_DETECT_V`/`_SIDECAR_V` bump only on a shape change. #640 (fingerprint-neutral) and #647 (fingerprint-moving) were deliberately split into separate PRs.*
⟂ None found — among the most consistently honored rules in the tree.

**P5 · Truth is classified per field: DERIVED (from code) vs DECLARED (human judgment), with a machine-readable owner map.**
*`common.py:13`: DECLARED = "canonical, validated, never overwritten"; DERIVED = "GENERATED… never hand-edited." `field_authority.json` assigns every field one of six classes and is gate-checked (`test_field_authority_declares_all_six_attributes`). The SPLIT pattern: for subsystems, "the MODULE SET is derived and wins; the GROUPING is declared and stands."*
⟂ DECLARED artifacts still cache derived numbers; those fields are tagged "MIGRATION TARGET — one of them will rot," fix pending.

### Cluster II — Correctness Doctrine

**P6 · Fix the root cause, not the symptom; every slice traces to an approved root cause.**
*`RC-n` naming throughout git; #646 "Root correction — ONE shared capability, not duplicated conditionals"; `IMPL-004` "No orphaned root cause," `IMPL-008` "a slice must trace to an approved root cause, or it is a hidden scope expansion." "A deferral is not a discharge" (`CYCLE6_CORRECTIONS.md:46`).*
⟂ "make-visible / harden / sweep" symptom-verbs are named as the anti-pattern (memory `fix-root-not-symptom`).

**P7 · One invariant, one owner; one implementation, one mechanism.**
*`selftest.detect` is "THE ONLY IMPLEMENTATION" (`selftest.py:285`); `health_model.py:210` "the ONE owner both daemon.status and doctor call"; `casting.py:1` "the SOLE crosspost-gate input"; #646 proves producer==consumer across all **96 states**; `test_hashtag_attribution_severance` pins attribution to exactly hook/clip/account.*
⟂ The rule exists because duplication drifted: two copies of the control-counter once reported "23/23 while pytest failed NC-23, on the same commit."

**P8 · Classify an invariant by its enforcement mechanism; never strengthen it beyond what is enforced.**
*Prime directive `kb/invariants.json:8`: "An invariant that holds by four manual call-site guards is NOT 'bound at the type level', however the docstring reads." Taxonomy: MECHANICAL_TYPE/_GUARD/_TEST/_ASSERT/STRUCTURAL/CONVENTION/DOCUMENTATION_ONLY. The hard invariants are the "looks-like-a-cleanup" boundaries GB-1…GB-7.*
⟂ The "uncomfortable ratio" (`:172`): of the top safety properties, exactly one is enforced by a CI test and one by an import-time assert — the rest by manual guards whose docstrings overclaim.

**P9 · Fail direction follows consequence — a real three-branch rule, not "always fail open."**
*Verdict-producer that can't decide → fail toward MORE checking (`select.deep_required(None)→True`; `UNKNOWN_IMPACT` "NEVER TREATED AS SAFE"). Degradable side-feature → safe default plus a logged breadcrumb (`errors.fail_open`, `exc_info=True`, never swallows `KeyboardInterrupt`). Correctness prerequisite → fail CLOSED/LOUD (`require_cv2` raises `ToolchainMissingError`, exit 2, rather than ship a blind centre-crop, #633; unknown tz "fails CLOSED to UTC").*
⟂ **The sharpest counterexample (I5):** the destructive ledger-wipe path got the weakest fail handling — bare `except Exception`, stdlib `logging.getLogger` (un-surfaced), `warning` not `error`. "logging ≠ surfacing"; fix-quality was inversely correlated with risk.

**P10 · Reachability and cost are established by measuring the LIVE TREE, never by reading code — and re-verified at merge.**
*`CYCLE6_CORRECTIONS.md:16`: Cycle 4 named five read-only merge gates "and ran none of them"; Cycle 6 ran all five and three collapsed a "blocking" risk (0 malformed backends, 0 stranded posts, 0 retired moments). "A collapsed risk not re-verified at merge is a risk merely not looked at" → all three re-armed as gates.*
⟂ Measurement itself has a shelf-life — which is why the collapse is re-armed rather than closed.

### Cluster III — Verification & Enforcement

**P11 · Every rule is a machine-evaluated predicate; a boundary that cannot be a predicate is flagged, not trusted.**
*"Every rule is a predicate a machine evaluates" (`ARCHITECTURE_GOVERNANCE.md:101`); the 21-rule engine; `IMPL-002` flags "2 slice boundaries [that] are PROSE, not machine-checkable" RED; `verifymap.py:123`: "CI cannot decide whether a particular test discharges a verification class — that is a semantic judgement."*
⟂ The `verify`/REQUIREMENTS table is advisory: "This line used to read 'CI fails if…'. IT DOES NOT" (`verifymap.py:115`).

**P12 · A rule must be able to fail and be proven to fire (negative controls); a decorative rule is worse than none.**
*"A rule that cannot fail is not a rule; it is decoration that makes a dashboard green" (`policy.py:9`). 24–25 negative controls each inject a defect and demand new evidence; `test_every_rule_is_reachable` fails if any rule lacks a control. "A MISSED control means the rule is DECORATIVE… it manufactures confidence."*
⟂ Self-caught: `IMPL-007`'s first parser "extracted NOTHING… silently no-opped," found only by NC-15 — the governance system committing its own signature defect.

**P13 · If a check doesn't RUN in the gate it does not exist; a gate that passes because its inputs are missing is the worst decoration.**
*`GOV-001` runs first and short-circuits (`policy.py:282`); origin story: "`.reports/architecture/` was in .gitignore — the ENTIRE KB of Cycles 1-6 was NOT IN THE REPOSITORY [and CI] would have gone GREEN" (memory `arch-kb-was-never-in-git`); NC-22 injects a deleted artifact and asserts red.*
⟂ `drift.report()` existed but "nobody called it — adding a check to the uncalled function is indistinguishable from not adding it."

**P14 · Tests are CI-only; the local machine does lint + structural checks, mechanically denied from running the suite.**
*`.claude/settings.json:14-19` hard-`deny`s `pytest`, `python -m pytest`, `black`, `ruff format`, `check-full.sh`; `scripts/check.sh:86` exits before pytest unless operator override. Two rationales: crash ("parallel local suites during a wave take the machine down," #605) and deadlock (P15). CI is the single authority; local tiers are fail-fast accelerators.*
⟂ Not pure lint: `check.sh:72` also fails closed if a changed `src` module has no scoped test — a structural gate, undocumented as policy.

**P15 · A hanging test is the bug — surface it; never raise the timeout to make it pass.**
*Global `timeout=60` is a deadlock guardrail set "well above any real test (~10-20s)" so the only thing it catches is a ledger-SQLite self-deadlock (`pyproject.toml:84`); `tests/CLAUDE.md`: "A hanging test IS the bug. NEVER raise the timeout."*
⟂ None.

**P16 · Test-first (RED→GREEN); required verification cannot vanish; a regression is locked by a test — but a lock can pin the wrong thing.**
*RED/GREEN commit pairs (e.g. `ledger schema_version (RED)` → `(GREEN)`, 2026-06-13); `IMPL-006` "every INVARIANT test named by the matrix still exists once its slice is merged"; S11 regression-lock (#657).*
⟂ **The danger, named:** `RC-5` — a green CI test "asserts the data-loss outcome as the expected behaviour… a REGRESSION LOCK ON THE DEFECT. Any correct fix MUST break this test."

### Cluster IV — Change Management

**P17 · Change is atomic slices on an acyclic DAG — each with file-ownership, a rollback class, a verification set, and a root cause.**
*`IMPL-001…008`; 12 slices / 19 files, "Ordering DAG acyclic: ✅ PROVEN"; rollback ∈ {CODE_REVERSIBLE, DATA_IRREVERSIBLE, WORLD_IRREVERSIBLE} (`IMPL-005`); commit grammar `type(scope): subject (Unit: <slug>) (#PR)` (169 `fix` ≫ 82 `feat`), mandatory `Co-Authored-By` trailer; plans carry a coverage proof ("46 rows placed exactly once").*
⟂ A design brief is a lighter authorization unit (Objective/Scope/Acceptance, no DAG/rollback) for single-UI changes — the ceremony scales to blast radius.

**P18 · A residual is acceptable only when zero-reachability, cheaply contained + regression-locked, and documented; containment ≠ endorsement.**
*`RC-9` accepted: "mutation-time enforcement DEFERRED — runtime cost… outweigh a zero-reachability risk"; `S11` is a GUARD that "does NOT fix RC-9, it PINS RC-9's unreachability in CI." "Baselined because it EXISTS, not because it is endorsed" (`cli.py:141`).*
⟂ Acceptance rests on two measured numbers (0 offending rows, ~6 affected fixtures), never on assertion.

**P19 · "Dead / zero-caller" is a LEAD, never a verdict, until a whole-tree AST + alias-import sweep; deletions are revalidated at execution.**
*`GB-2`; "the name-based call graph… wrongly labeled 5 LIVE functions dead" (`anomalies.md:145`); Cycle 8 cancelled 4 planned deletions on premise-invalidation ("`Ledger.add_render` — ≈25 test callers, a live helper").*
⟂ Genuinely-dead code is confirmed and removed (`set_channel_routing`, `is_exempt`) — the rule slows deletion, it doesn't forbid it.

**P20 · Exceptions are time-boxed, owned, justified; there is no `# noqa` for an architecture rule, and an expired exception suppresses nothing.**
*`exceptions.json:6`: "AN EXPIRED EXCEPTION SUPPRESSES NOTHING… A finding is either fixed, or it is here, with an owner and a removal plan." Only 2 live (EXC-001/002, expiry 2027-01-01). Accepted-risk exemplar F-C/AR-13: 108 mutating unauthenticated Studio routes, severity literally `"ACCEPTED"` — "GROUND TRUTH, not a recommendation."*
⟂ None — the registry is small and disciplined.

**P21 · A migration is justified only by a real on-disk shape change; additive, idempotent, copy-on-write, never wipes; break a shape only after all consumers die.**
*`ledger.py:218`: "Additive + idempotent + never-raising… NEVER wiped — every migration is copy-on-write"; `SCHEMA_VERSION` v2→v11; v10→v11 drops maps only as "the teardown's on-disk half" of the completed P11 casting removal. Migration-vs-shim is decided by whether a mechanism exists: the ledger has a hop-chain (may drop), the registry has none (must stay lenient).*
⟂ Compat is broken deliberately when the thing is a feature not a data-shape (`DEAD-001..005` flags in tests only, code gone).

**P22 · Backwards-compat by default (byte-identical when unchanged/flag-off); forward-compat is load-bearing; break only with enumerated, tested divergence.**
*`models.py:171` relies on pydantic `extra="ignore"` so "an older binary must parse a newer ledger, never crash"; `extra="forbid"` is BLOCKING-banned (`IMPL-010`/GB-3); `_NewerSchema` refuses an unknown-newer ledger; "byte-identical when unchanged" appears 289×; E3 divergences are "enumerated as intentional… we do not relabel history," frozen fixtures kept byte-identical.*
⟂ None — near-universally honored.

### Cluster V — The Human Boundary & Autonomy

**P23 · Product / cost / go-live decisions are the operator's; the machine never guesses, speculates, or recommends.**
*"cost guardrails are a product call, deliberately not imposed" (`FLAGS.md:36`); `PD-1…PD-5` "Not recoverable from code. Not guessed"; `unknowns.json` "DO NOT SPECULATE… a plausible answer recorded as fact is worse than an admitted gap," ceiling of 8 enforced by `ARCH-005`; `IMPL-008` surfaces a risk-only slice "as a product decision."*
⟂ The operator exercises this live: #645 "disable the enforcement gate (operator decision)" — "the gate cost more in blocked work than it protected. Operator called it."

**P24 · Nothing reaches the world without an explicit operator gate.**
*No-auto-publish: every Post born `awaiting_approval`, publish iterates `queued` only (`src/fanops/CLAUDE.md`, INV-08). `go_live` is the SOLE setter of `FANOPS_LIVE=1`, gated accounts-valid → live-ready channel → backlog → explicit `confirmed=True`. Wipe requires the typed word `REMOVE` + snapshot. Discovery proposes; the operator accepts ("Discovery must never auto-write a caption tag — curation gate").*
⟂ None — the most structurally reinforced boundary in the product.

**P25 · Autonomy = delegation + verification priced to risk (demanded only where wrongness is expensive); small non-hot changes land on green CI alone.**
*`.orchestration/SPEC.md:37`: "an independent verification record is demanded ONLY where wrongness is expensive: hot file, >5 files, or unverifiable… CI cannot catch an implementer grading their own homework"; no self-verification, record must match head SHA, only `{fanops-worker, fanops-lander}` may spawn.*
⟂ **Now dormant** — the enforcing hook was disabled 2026-07-15 (#645); the principle survives as convention, not mechanism (see §5).

**P26 · Default-ON for the system's purpose; every default-ON flag keeps a firewalled OFF path + firewall test; learning actuators are validation-frozen and amplify-only.**
*`FLAGS.md:1`: differentiation + closed-loop hashtags "ship ON by default — they are the system's purpose, not opt-ins," each with a legacy OFF path "pinned by a firewall test"; `variant_amplify`/`variant_transfer` stay inert until `learning_validated` "even with the kill switch ON"; actuators never retire/publish. New flag must land with property + OFF early-return + firewall test + registry row.*
⟂ None.

**P27 · A file that steers runtime is a validated contract that fails visibly — or an inert doc that is drift-tested. No third option.**
*`CONTROL-FILES.md:5`: load-bearing config "is validated at its read boundary and fails visibly… an inert doc is mirror-tested so it cannot drift… No third option (an unvalidated file silently steering output) is allowed." Every env read must be declared (`ARCH-003`, extended to check `docs/CONFIG.md`'s name-set, #656).*
⟂ None.

### Cluster VI — Meta / Anti-Rot

**P28 · State blind spots out loud, every run; be over-inclusive on what you cannot verify.**
*`IMPL-009` prints its dynamic-door blind spot every run ("understating a blind spot is the exact failure this system exists to prevent"); baselines say "THIS IS EMPTY TODAY"; `_verification_persists` says "HONEST STATUS: ARMED ON ZERO TESTS." Every SCC claim "BOUNDS blast radius; it does not ESTABLISH it."*
⟂ None — honesty-about-limits is itself a consistent discipline.

**P29 · Keep cheap graceful-degradation scaffolding; the fix for a rotting copy is deletion; carry near-zero real debt.**
*`SCAFFOLDING-VERDICT.md`: KEEP the migration framework / dual-ASR / `publish_lead_minutes` — "graceful degradation, not speculation… so the call isn't re-litigated"; "Deletion is the fix" for rotting number-copies. grep finds 0 `FIXME`/`HACK`/`XXX`/`kludge`/`stopgap` in all of `src/`; "throwaway" is a design pattern (ledger snapshots, cutover probes), not debt.*
⟂ Speculative insurance is kept when cheap; speculative complexity is cut (over-engineered `PostState.ready` "rejected here," `dryrun-boundary.plan.md:14`).

**P30 · An ADR convention is *declared but dormant*; in practice decisions live in commit bodies, plan "locked-decision" blocks, governance registries, memory, and two ad-hoc decision-records.**
*`.agents/skills/domain-modeling/ADR-FORMAT.md` declares a full convention — Nygard template, `docs/adr/NNNN-slug.md` numbering, and a three-part "when to write one" test (hard-to-reverse ∧ surprising-without-context ∧ real-tradeoff). But it is dormant: the format doc is itself **untracked**, **nothing in the tracked tree references it**, and `docs/adr/` sat empty until 2026-07-15. So rationale actually rides in squash-commit bodies (the richest venue — #641, #646), plan "locked-decision" blocks, the `INVENTORY.md` SHIM registry, `unknowns.json`, auto-memory, and two ad-hoc decision-records (`cv2-decision-record-v4.md`, `phaseB-toctou-decision.md`, which add options + reject-reasons + a binding Test contract + a Retractions section).*
⟂ Same declared-but-dormant shape as the disabled land-gate (§5.1) and the once-gitignored arch KB — a recurring meta-defect (§3, I-b).

---

## §2 — Evolution Timeline (how the philosophy hardened)

| Date | Event | Philosophy shift |
|---|---|---|
| **2026-06-01** | Clean-slate `fanops v2` scaffold | Fresh start; no legacy constitution |
| **2026-06-13** | `SCHEMA_VERSION` + copy-on-write migration + newer-than-code guard (RED→GREEN) | Durability discipline & forward-compat born early |
| **2026-06-16** | `SCAFFOLDING-VERDICT.md`, `CONTROL-FILES.md` | First "don't re-litigate" verdict; "no third option" control-file contract |
| **2026-06-19→06-26** | Ledger v2→v9 migrations (day-anchor, metrics, SelectionFact, AccountSelection) | Migration-as-routine; each hop carries inline rationale |
| **2026-07-03** | First full-codebase trace: `anomalies.md` + `full-trace-index.md` (10 traces), issue-register (MOL-65..87) | Descriptive-audit era — hand-authored maps, safety verdicts |
| **2026-07-07→07-11** | CI hardening wave (skip→fail, SHA-pins, hash-locked deps, SLO gate, secret scan); `fail_open` primitive + swallow ratchet; codemaps FROZEN | Enforcement moves from prose to AST ratchets + CI gates; fail-loud formalized |
| **2026-07-12** | Tests made CI-only, mechanically denied locally (#605, "waves crashed the machine"); "price verification to risk" | Hard operational law; risk-priced autonomy |
| **2026-07-14** | `cv2` required → render refuses not silent-centre (#633); Cycle 6 froze the implementation contract; 3 "blocking" risks collapsed by live-tree measurement | Fail-loud pushed into the product; "measure the live tree" |
| **2026-07-15** | Cycle 7 governance engine (`tools/arch`, 21 predicates, negative controls, #636); number-rot cleanup (#641); operator disables the orchestration gate (#645); Cycle 8 slice wave RC-1…S11 lands; independent `CI_ARCHITECTURE_REVIEW.md` authored | Executable-governance era — machine-generated docs that "cannot lie"; single-threaded gate retired by operator |

**Arc:** careful prose → prose with coverage-proofs → machine-checked prose that cannot rot. The orchestration land-gate had the shortest life of any control — born 2026-07-11 (#568), dead 2026-07-15 (#645), ~4 days.

---

## §3 — Conflicting Principles & Multiple Sources of Truth

- **I-a · Two truth-registers, opposite headline verdicts, never reconciled.** The Sonnet trace (`anomalies.md`/`full-trace-index.md`) declares "None CRITICAL… 10 invariants all HOLD"; the adversarial architecture cycle finds two CRITICAL data-loss defects (`RC-4`/`RC-5`) in a module the trace declared safe — `RC-4` reached "by following the DOCUMENTED wipe-rollback procedure." Same code, two registers, no cross-link. **The single most important structural inconsistency.**
- **I-b · A declared ADR system sits dormant while decisions scatter across six venues.** `.agents/skills/domain-modeling/ADR-FORMAT.md` (untracked, referenced by nothing tracked, `docs/adr/` empty until 2026-07-15) specifies exactly where decisions *should* live; in practice they're in commit bodies / plans / registries / memory (P30), with no index. This is one instance of a **recurring meta-defect: load-bearing governance artifacts keep landing untracked or on one machine** — the Cycle-1–6 arch KB was gitignored (`arch-kb-was-never-in-git`), `insights-culmination-MASTER.plan.md` was never committed (I-g / §5.7), and both `ADR-FORMAT.md` and this dossier's sibling `docs/adr/README.md` catalogue are untracked. The repo's own P13 ("a gate that passes on missing inputs is the worst decoration") is the antidote it has not yet applied to its own documents.
- **I-c · The number-rot rule is itself violated inside governance.** `architecture.yml` prose "21" vs actual 24/25 controls; `CONFIG.md` "65/63" vs measured 73 env vars.
- **I-d · Three merge-policy planes disagree.** Workflow YAML exposes ~11 checks; governance prose tags 18 rules BLOCKING; live branch protection requires exactly 2 (`unit`, `e2e`, strict, 0 reviews, `enforce_admins:false` — re-probed 2026-07-15). "BLOCKING" means policy-engine exit code, not merge-block; arch invariants reach the gate only via a second, required pytest path. Thesis: "the repository does not declare its intended merge policy in a machine-verifiable form."
- **I-e · Fix-quality inversely correlated with risk (contradicts P9).** The destructive-wipe path got the sloppiest fail-open; cosmetic paths got the cleanest fixes.
- **I-f · The taxonomy had a blind spot ("logging ≠ surfacing").** The 10-invariant scale "had no axis for fail-silently-and-forever"; `errors.fail_open` itself defaults to the unsurfaced stdlib logger.
- **I-g · Stale contracts read as live.** The `.reports/architecture/` Cycle-6 contract is future-tense ("S01–S12 READY/SHIP FIRST") while those slices already merged; its `_CLI_PRINT_COUNT==147` is wrong (live 165). `anomalies.md` self-declares C2–C10 "likely carry similar rot." `lane-guard.yml` violates the repo's own SHA-pin policy.
- **I-h · Two dangling authority references.** `CONFIG.md` cites `docs/CODEMAPS/system-lens-map.md` as its authority; that file does not exist.

---

## §4 — Missing Principles (enforced in implementation, undocumented as policy)

1. **The two AST ratchets ARE the no-regression law** — `test_swallow_ratchet.py` (per-file broad-`except` baseline over 49 files) and `test_internal_prints_routed.py` (`_CLI_PRINT_COUNT` exact-equality) — enforcement lives only in test files.
2. **Harness-level mechanical denies** (`settings.json`) make the local-test ban and no-reformat house-style physically unbreakable by the agent — a real rule stated only in a settings file.
3. **Behavioral working-style hooks are wired and live** (`anti-divert-contract.py`, `block-hedge-on-stop.py`, `decide_dont_ask.py`) — they enforce a working constitution mechanically, documented nowhere as policy.
4. **Autouse hermeticity fixtures** (`_no_real_publish_sleep`, `_hermetic_publish_env` stripping `_LEAKY_ENV`) enforce no-network/no-stall by fixture, not rule.
5. **"Quarantine fingerprint-neutral from fingerprint-moving into separate PRs"** — a real, repeated practice (#640 vs #647), never written down.
6. **The commit body IS the ADR** — nothing requires the squash-commit body to carry root-cause + rejected-alternatives + test contract, yet it invariably does. Load-bearing and unwritten.
7. **The `(Unit:<slug>)` tag is followed on every PR** by convention even though its enforcing gate is now off.
8. **Schedule monotonicity** is the codebase's only import-time assert (`crosspost.py:30`) — the single strongest-enforced invariant, absent from every policy doc.
9. **`check.sh`'s "changed src ⇒ scoped test must exist" gate** — a no-false-confidence rule stated only in a script comment.

---

## §5 — Principles That Should Be Retired (documented but no longer reflected)

1. **The orchestration enforcement gate / `OPS-001` single-threaded constraint** — operator-disabled 2026-07-15 (#645); `.cursor/hooks.json` now empty. `ORCHESTRATION.md` was updated to admit dormancy; **`AGENTS.md` was not** and still presents the hook land-gate as the merge authority (`:156`,`:180`). Retire the "enforced" framing; keep as convention.
2. **The `(Unit:<slug>)` land-gate as an enforced control** — code intact, enforcement off (G12). Demote to documented convention.
3. **`anomalies.md`'s "all invariants HOLD / none CRITICAL" headline** — falsified by `RC-4`/`RC-5`. Reconcile with the architecture register or retract.
4. **The `.reports/architecture/` Cycle-6 pre-implementation contract's future-tense S01–S12 and `_CLI_PRINT_COUNT==147`** — superseded by the landed slices and `cycle8-closure`; supersede on disk.
5. **Dead doc claims:** the Go-Live `FANOPS_POSTER=postiz` write (`go_live` never writes it); `hookcheck.py:11`'s "always-on strict critic" ("a documentation lie"); `docs/FLAGS.md` still listing removed `creative_variation`; orphan flags `account_first`/`batch_studio`/`moments_wait_cycles`.
6. **Stale line-anchors** across nested `CLAUDE.md` (10/10) and `tests/CLAUDE.md` — regenerate or delete the anchors.
7. **The phantom `insights-culmination-MASTER.plan.md`** — cited as "the current build plan" in three tracked files but never committed.

---

## §6 — Proposed Constitution Outline (sections only — NOT the constitution)

0. **Preamble & Precedence** — scope; the supremacy clause (implementation is authoritative; unregenerated prose is presumed rotting).
1. **Definitions** — invariant (by enforcement mechanism), architecture (measured structure + declared partition), implementation (the change contract), DERIVED vs DECLARED, fingerprint, slice, root cause, residual, exception, unknown, control-file.
2. **Source of Truth & Provenance** — code-authoritative; generated-as-view; the number-rot ban; per-field authority classes; byte-reproducibility from source alone.
3. **Correctness Doctrine** — root-cause-not-symptom; one-invariant-one-owner; never-strengthen-beyond-enforced; the three-branch fail-direction rule; measure-the-live-tree.
4. **Verification & Enforcement** — predicates over prose; negative controls; run-in-the-gate-or-it-doesn't-exist; tests-are-CI-only; the AST ratchets; RED→GREEN and regression-locks; the merge-control plane (and its current under-declaration).
5. **Change Management** — atomic slices / acyclic DAG / rollback class / verification set; the commit-and-PR grammar; landing discipline; migration law; backward/forward-compat and the rules for a deliberate break; fingerprint-bump policy.
6. **Residuals, Exceptions & Unknowns** — residual-acceptance criteria; containment ≠ endorsement; time-boxed owned exceptions; the unknowns ceiling; dead-code-is-a-lead.
7. **The Human Boundary** — product vs engineering separation; operator-only decisions; the no-auto-publish / confirm-gate lifecycle; risk-priced autonomous merge.
8. **Configuration as Contract** — env declaration; control-file validation; secrets; default-ON-with-firewalled-OFF; validation-frozen actuators.
9. **Anti-Rot & Document Hygiene** — single-register reconciliation; staleness review-dates; the retirement process; a single decision-record venue (closing the six-venue scatter).
10. **Amendment & Governance-of-Governance** — how a rule is added/changed; the exceptions registry; cycle closure & freezing; how the constitution itself avoids becoming rotting prose.

---

**Confidence note.** The `tools/arch` engine, `pyproject.toml`, CI review, branch protection, `ADR-FORMAT.md`, and git/commit evidence are first-hand. The `.reports/architecture/**` and `anomalies.md` citations come from the investigation sweeps with exact `file:line`, internally corroborated across six independent passes (all six converged on P1/P2). The two live-vs-doc contradictions most worth acting on before writing anything: **I-a** (reconcile the two truth-registers — one calls a CRITICAL data-loss path "safe") and **§5.1** (`AGENTS.md` still advertises a gate that is off).

**Correction (2026-07-15, post-delivery).** An earlier draft of P30 / I-b asserted "there is no formal ADR system." A wider sweep of `.agents/skills/` (which the plans investigation did not cover) found `.agents/skills/domain-modeling/ADR-FORMAT.md` — a real, declared ADR convention that is merely dormant. The claim is corrected above. That miss is itself an instance of the repo's signature defect — a confident negative claim a broader search falsifies — and is logged here per the repo's own "correct the record" ethos rather than quietly patched. A **sibling** archaeology deliverable was also found at `docs/adr/README.md` (an untracked 99-entry ADR *catalogue*, produced by a parallel run 2026-07-15). This dossier (constitutional *principles*) and that catalogue (per-decision *ADR records*) are complementary views of the same evidence; they should cross-reference, not compete — otherwise they become exactly the unreconciled registers **I-a** warns about.
