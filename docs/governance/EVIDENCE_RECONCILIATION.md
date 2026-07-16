<!-- Reconciliation matrix — hand-authored, grounded in the CURRENT tracked tree.
     Base: origin/main @ 04c4092 (#664), fetched + revalidated 2026-07-16.
     Purpose: reconcile the parallel evidence registers into one authoritative reading BEFORE the
     Repository Constitution is written. This document does not change code, CI, or branch protection.
     It is Phase 1 of the constitutional layer (docs/REPOSITORY_CONSTITUTION.md et al.). -->

# Evidence Reconciliation Matrix

**Base state.** `origin/main` @ `04c4092` (#664), re-fetched and revalidated **2026-07-16**. Every
"current-source evidence" cell is read from that tree, not from a prior snapshot.

**Why this exists.** Five registers describe the same repository from different vantage points and
different moments. Several disagree; several were **overtaken by events** while the archaeology ran —
`origin/main` advanced #652 → #664 in hours, landing fixes that falsify earlier "open defect" claims.
This is the repository's own thesis in miniature: *a claim not revalidated against the current tree is
presumed stale.* This matrix is the single reconciled reading the constitutional layer is built on.

## Registers reconciled

| Reg | Register | Location | Nature | Freshness |
|---|---|---|---|---|
| E1 | Constitution evidence dossier | `docs/CONSTITUTION-EVIDENCE-DOSSIER.md` (**tracked** 2026-07-16; frozen + superseded — R7·resolution) | principles + contradictions | snapshot @ #652 — **partly stale**; five known-false claims named in its banner |
| E2 | ADR decision catalogue | `docs/adr/README.md` (tracked, 1722 lines) | 99 back-filled decision records | current |
| E3 | CI governance program | ADR-0100/0101/0102 + `.github/ci-control-registry.yml` (+ schema) + `tools/ci` (#661) | control-plane authority + registry | current, **accepted-in-principle / rollout transitioning** |
| E4 | Architecture governance | `docs/ARCHITECTURE_GOVERNANCE.md` (generated) + `tools/arch` + `.reports/architecture/**` | 21 executable rules, DERIVED/DECLARED | current |
| E5 | Anomaly / invariant audits | `docs/CODEMAPS/anomalies.md`, `.reports/architecture/INVARIANT_AUDIT.md`, `docs/CI_ARCHITECTURE_REVIEW.md` | trace verdicts, adversarial audit | **frozen 2026-07-11 / review superseded by its own ADRs** |

## Status vocabulary (used throughout the constitutional layer)

- **enforced** — a machine mechanism blocks or fails on violation *today* (CI required check, ratchet, import-time assert, gate).
- **partially-enforced** — enforced on part of its scope, or declared+built but not yet wired to a blocking gate.
- **documented-only** — a written convention with no mechanism.
- **dormant** — a mechanism that exists on disk but is deliberately switched off.
- **proposed** — declared intent not yet accepted or not yet built.
- **accepted-residual** — a known gap deliberately kept (zero/low reachability, contained, documented).
- **historical** — a true record of a past state, superseded by current reality; retained as provenance, not as current law.

## Reconciliation entries

Each: **Claim A** vs **Claim B** · current-tree evidence · **authoritative conclusion** · **action** (Correct / Supersede / Retain-as-historical / Record-as-proposed).

### R1 — The ADR system's actual state
- **A** (E1 dossier P30/I-b, and an intra-session draft): *"there is no formal ADR system; `docs/adr/` is empty / declared-but-dormant."*
- **B** (E2 + ADR-FORMAT): *the ADR system is declared and active.*
- **Current evidence:** `origin/main` tracks `docs/adr/0100`, `0101`, `0102`, `0103`, and `README.md` (a 1722-line catalogue of 99 decisions). The convention is declared in `.agents/skills/domain-modeling/ADR-FORMAT.md` (Nygard template, `docs/adr/NNNN-slug.md`, a 3-part "when to write one" test). Four ADRs are accepted-in-principle (0100–0102) or proposed (0103).
- **Authoritative conclusion:** the ADR system is **active** (four ADRs + a tracked catalogue). The only residual is that the *convention doc itself* (`ADR-FORMAT.md`) is **untracked** — a declared governance artifact living outside the tree.
- **Action:** **Correct** E1 (P30/I-b superseded by this row). **Record-as-remediation:** track `ADR-FORMAT.md`.

### R2 — The disabled land / enforcement gate
- **A** (E1 §5.1; `AGENTS.md`): the `.cursor/hooks.json` land-gate / `(Unit:<slug>)` tag is the merge authority.
- **B** (#645; ADR-0101; ADR-0102 §2): the gate was **disabled by operator decision**; it is explicitly **not revived**.
- **Current evidence:** catalogue slug **0096 `GOV-ENFORCEMENT-GATE-DISABLED`** (status Superseded); ADR-0101 Status "does **not** revive 0096 or the land-gate"; ADR-0102 §2 "the `(Unit:<slug>)` marker belongs to the dormant land-gate (0096) and is **not** part of this policy … enforced by nothing."
- **Authoritative conclusion:** the land-gate is **dormant** by decision. The `(Unit:)` tag and commit-message grammar are **documented-only** conventions (the #637+ history shows they still hold in practice). Reviving a message gate requires a separate ADR.
- **Action:** **Retain** (dormant, confirmed). **Record-as-remediation:** `AGENTS.md` still presents the hook land-gate as live authority (E1 §5.1) — a documentation correction, out of scope for this docs-only PR, logged in the implementation roadmap.

### R3 — Competing truth registers around critical persistence / restore
- **A** (E5 `anomalies.md`): *"None … CRITICAL; the wipe/restore invariants HOLD."*
- **B** (E4/E5 adversarial cycle; E1 I-a): **`RC-4` / `RC-5` — CRITICAL data-loss** on the documented wipe-rollback path.
- **Current evidence:** **#653** `fix(ledger): RC-4/RC-5 — restore_snapshot serializes on the ledger lock`; **#654** `fix(cli): S01c — expose fanops restore, the reversible half of fanops wipe`; **#655** `fix(accounts): S02 — normalize + skip-and-flag a bad backend value at the load boundary`. All merged to `origin/main`.
- **Authoritative conclusion:** the adversarial register was **correct** (the defect was real); the "all HOLD" register was **wrong on this point**. **The defect is now FIXED.** Both registers are therefore **historical** on RC-4/RC-5.
- **Action:** **Supersede** (defect discharged; E1 I-a no longer a live contradiction). **Retain** both registers as historical provenance of the reconciliation. **Record-as-remediation:** `anomalies.md`'s "all HOLD / none CRITICAL" headline is stale and must carry a superseded-by pointer.

### R4 — Merge policy machine-verifiability (the CI control plane)
- **A** (E5 `CI_ARCHITECTURE_REVIEW.md` thesis; E2 slug 0099; E1 I-d): *"the repository does not declare its intended merge policy in a machine-verifiable form; no version-controlled control plane."*
- **B** (E3 ADR-0100 + registry + #661): the control plane exists — a registry of intent + a validator that fails on divergence.
- **Current evidence:** `.github/ci-control-registry.yml` (+ `.schema.json`) declares intent for every control; **#661** `feat(tools/ci): CI Control Registry validator — DC-1..DC-6, three modes, negative controls` built the validator; ADR-0100 declares the precedence order and the six divergence checks; ADR-0100 is **accepted in principle**, registry rollout `phase: transitioning`.
- **Authoritative conclusion:** the gap is **closed in mechanism** (registry + schema + validator exist) and **partially deployed** — the validator is not yet a required blocking gate, and `intended_required_contexts` (5) exceeds `current_required_contexts` (2, live). Status: **partially-enforced**, with deployment sequenced to Phase D/E.
- **Action:** **Supersede** the "not machine-verifiable" claim. **Record-as-proposed:** the remaining deployment (validator → required; 5-context set live; `enforce_admins`) is Phase-E future policy owned by ADR-0100/0101.

### R5 — lane-guard supply-chain / hygiene
- **A** (E1 G9; E5 review): `lane-guard.yml` uses floating `@v7`/`@v6` and has no `timeout-minutes`/`concurrency` — violates the SHA-pin policy.
- **B** (#663): hardened.
- **Current evidence:** **#663** `ci(lane-guard): pin actions to SHAs + add timeout and concurrency (Unit: lane-guard-harden)`.
- **Authoritative conclusion:** **FIXED.** Registry control `LANE-GUARD` still notes the hardening as its precondition (`SLICE-LANEGUARD-HARDEN`), now satisfied.
- **Action:** **Supersede** (E1 G9 stale).

### R6 — Required-context set and admin enforcement
- **A** (live probe / E1 / E5): **2** required contexts (`unit`, `e2e`), `enforce_admins=false`, 0 reviews — recorded partly as accepted-residual (admin bypass).
- **B** (E3 ADR-0101 + registry): **5** intended required contexts; `enforce_admins` **enabled last**; `required_conversation_resolution` enabled; governed break-glass replaces the standing bypass.
- **Current evidence:** registry `current_required_contexts` = the 2 live; `intended_required_contexts` = 5 (`unit`, `e2e`, `base install…`, `gate…`, `lane…`); `rollout.phase: transitioning`. ADR-0101 §4 (admins enabled last, break-glass), §6 (conversation-resolution).
- **Authoritative conclusion:** **current enforced** = 2 required, `enforce_admins=false`. **Declared intent** = 5 required + admin-enforced + conv-resolution, deployed one-at-a-time in Phase E, gated on the `tools/ci` validator being green. The old "accepted-residual admin bypass" (E2 slug 0097) is **superseded by a decision to change** (ADR-0101), not a standing residual.
- **Action:** **Retain** the 2-context live state as *current enforced*; **Record-as-proposed** the 5-context + admin-enforcement intent (Phase E).

### R7 — Tracked vs untracked governance artifacts
- **Claim:** load-bearing governance artifacts have a recurring habit of living untracked / on one machine (E1 I-b meta-defect; `arch-kb-was-never-in-git`).
- **Current evidence:** the Cycle 1–6 arch KB is **now tracked** (`.reports/architecture/governance/*.json`, `IMPLEMENTATION_GOVERNANCE.md` all in `origin/main`) — the historical GOV-001 gitignore hole is **closed**. Still untracked: `.agents/skills/domain-modeling/ADR-FORMAT.md` (the ADR convention), `docs/CONSTITUTION-EVIDENCE-DOSSIER.md` (E1, shared checkout), `docs/constitution/` (a parallel-agent artifact, shared checkout), and — until this PR — the constitutional layer itself.
- **Authoritative conclusion:** the meta-defect is **mostly remediated** (arch KB tracked); the residual is a short, named list of untracked governance docs.
- **Action:** **Record-as-remediation:** track `ADR-FORMAT.md`; decide the fate of `docs/CONSTITUTION-EVIDENCE-DOSSIER.md` (superseded by this layer — update or retire) and the parallel `docs/constitution/` (owned by another agent — do **not** absorb; leave for its owner). This PR persists the authoritative layer into the tree, closing the largest instance.

#### R7 · resolution (2026-07-16) — the deferred fates, decided by the owner

R7 deferred two decisions to the artifacts' owner rather than absorbing them unilaterally (correctly —
`ENGINEERING_PHILOSOPHY.md` §12). The owner has now ruled. Both are hereby closed.

- **`docs/CONSTITUTION-EVIDENCE-DOSSIER.md` (E1) → RETIRED AS TRACKED HISTORICAL EVIDENCE.** Executes
  `SLICE-DOSSIER-RETIRE` (option 1: banner + retain). It is **now tracked**, carrying a
  frozen/superseded banner that names its five known-false claims and their discharging PRs
  (RC-4/RC-5 → #653/#654; the "21" count → #665; the env-var count → #656; the
  `system-lens-map.md` claim → **false when written**; P30 → superseded by the live ADR system).
  Tracked rather than deleted because **this file is E1** — an untracked citation target resolves on
  one machine and dangles in every fresh clone, which is the very `arch-kb-was-never-in-git` defect R7
  names. Its errors are left uncorrected in place: a superseded register is evidence of what was
  believed and when.

- **`docs/constitution/` (the parallel 11-doc layer) → SUPERSEDED; NOT LANDED; NOT ABSORBED.** Adjudicated
  2026-07-16 against this layer, the catalogue, and the live tree. Verdict: **wholly superseded, zero
  genuinely-missing knowledge.** It is a re-projection of registers this repo already tracks (its own
  section headers say so — its dependency graphs are redrawn from catalogue §7/§5/§6, its per-ADR
  registry from catalogue §2/§3), and landing it would have created the second constitution
  `ENGINEERING_PHILOSOPHY.md` §12 exists to prevent. Three findings settled it:
  1. **It would import falsified claims.** It asserts the RC-4/RC-5 restore race as a *live* CRITICAL
     defect in three places; #653/#654 discharged it (`LAW-PERSIST-02`: "Residual: none").
  2. **One of its laws is actively dangerous.** Its §4.2 mandates *"a transition MUST replace, not
     mutate (`model_copy(update=…)`)"* and cites **GB-5** as support. GB-5 says the **opposite**: *"No
     slice may convert a `setattr` on a `Moment` to `model_copy`"* — `Moment` is the only model with
     `validate_assignment=True`, which `model_copy` **bypasses**, and `cast_add`/`cast_remove` are
     correct *only* because of that setattr (`COUP-07`). An engineer following it would silently break
     the per-persona ownership gate. `LAW-STATE-03` states the rule correctly and narrowly.
  3. **Its traceability layer is a second registry**, restating CI-control rows, validator ids and
     owners that `ARCHITECTURAL_LAWS.md` already carries inline — banned by `ARCHITECTURAL_LAWS.md:6`
     ("the registry remains the single owner of control rows"), by `STANDARDS_ENFORCEMENT_MATRIX.md:14`
     ("No second registry"), and by its own LAW-1.1. Its hand-drawn subsystem graph embeds **DERIVED**
     module counts (127) that had already rotted against the measured 130 before it was ever committed.

  **Disposition:** the files were never tracked and are **not** landed; the working copy is retained
  locally by its owner, carrying a local superseded marker. Nothing is deleted from the tree, no
  history is rewritten, and this row is the permanent record of why. Any future re-proposal must
  re-derive against the **live tree**, not that snapshot.

- **`.agents/skills/domain-modeling/ADR-FORMAT.md` → still open** (`SLICE-ADRFORMAT-TRACK`). R7's
  residual is now **one** artifact, not three.

### R8 — The anomaly ledger's method and freshness
- **A** (E5 `anomalies.md`): a frozen (2026-07-11) trace with an "all invariants HOLD" summary; self-declares C2–C10 "likely carry similar rot."
- **B** (E4 governance + landed fixes): invariant state is authoritatively carried by `tools/arch` policy + `INVARIANT_AUDIT.md` + the tests, and by the RC-fix wave.
- **Authoritative conclusion:** `anomalies.md` is a **historical** frozen snapshot. It is not the current authority on invariant state; the arch governance engine and the landed fixes are.
- **Action:** **Retain-as-historical.** **Record-as-remediation:** add a superseded-by/"frozen snapshot" banner (a documentation correction; not this PR's scope — roadmap item).

### R9 — The CI Architecture Review's status
- **Current evidence:** `docs/CI_ARCHITECTURE_REVIEW.md` is the read-only audit whose thesis seeded ADR-0100/0101/0102 (each ADR cites it). Its concrete recommendations (control registry, 5-required set, `enforce_admins`, lane-guard hardening) are now **adopted** as ADR policy and partly **landed** (#661, #663).
- **Authoritative conclusion:** **historical** — superseded by the ADRs it produced. Its sketch filename `.github/ci-ownership.yml` was superseded by `.github/ci-control-registry.yml` (ADR-0100 §Superseded).
- **Action:** **Retain-as-historical** (provenance for 0100–0102).

### R10 — The reframe framing decision
- **A** (implicit legacy design): content-blind centre fallback + face-count-only treatment routing.
- **B** (E-reframe: `RCDR-centered-multi-untracked.md` + ADR-0103): subject-aware + layout-aware framing; PIP ≠ live two-shot; zoom restraint; speaker *selection* deferred behind diarization.
- **Current evidence:** ADR-0103 status **proposed** (`accepted_in_principle: pending`); the corrective spec (`docs/design/reframe/framing-spec.md`) and roadmap exist; the current code still ships the content-blind fallback.
- **Authoritative conclusion:** **proposed** future policy with an **accepted-residual** in the interim (`FANOPS_SMART_FRAMING=0` is the standing rollback; under the first remediation track a non-speaking host may be shown).
- **Action:** **Record-as-proposed** (ADR-0103) with its named accepted-residual.

### R11 — Self-correction of the evidence dossier (E1)
The dossier (E1) was authored against #652 and is superseded on five headline points by rows above:
| Dossier claim | Row | Disposition |
|---|---|---|
| P30 / I-b "no ADR system / dormant" | R1 | superseded — ADR system active |
| I-a "RC-4/RC-5 CRITICAL open" | R3 | superseded — fixed (#653–655) |
| I-d "merge policy not machine-verifiable" | R4 | superseded — registry + validator exist |
| G9 "lane-guard violates SHA-pin" | R5 | superseded — fixed (#663) |
| "2 required contexts (residual admin bypass)" | R6 | current-enforced=2; 5-context intent + admin-enforce now decided (ADR-0101) |
- **Action:** the dossier is **historical evidence**, not current law. This matrix + `docs/REPOSITORY_CONSTITUTION.md` are authoritative. The dossier is not committed in this PR; its remaining stale rows are superseded here rather than silently edited (provenance preserved).

## Contradictions still genuinely open (carried into the constitution as such)

1. **`AGENTS.md` advertises the dormant land-gate** as merge authority (R2) — documentation correction pending.
2. **`anomalies.md` "all HOLD" headline** is stale (R3/R8) — needs a superseded-by banner.
3. **`ADR-FORMAT.md` (the ADR convention) is untracked** (R1/R7) — declared governance living outside the tree.
4. **The CI control plane is declared but not yet the enforced merge gate** (R4/R6) — 2 live vs 5 intended required; `enforce_admins=false`; validator not yet blocking. This is a *sequenced deployment*, not an unreconciled contradiction, but it is a real current-vs-intended gap.
5. **ADR-0103 (reframe) is proposed, not accepted**; the content-blind fallback still ships (R10).
6. **`docs/ARCHITECTURE_GOVERNANCE.md`-adjacent number-rot** (e.g. an `architecture.yml` comment count vs `selftest.CONTROLS`) — historically real; going forward it is exactly what arch `IMPL-007` and CI `DC-4`/`DC-5` are designed to catch. Treated as a governed, monitored residual.

These six are the inputs to `docs/ARCHITECTURAL_LAWS.md` (enforcement status) and `docs/governance/CONSTITUTION_IMPLEMENTATION_ROADMAP.md` (slices).

## Handling rules honored here

- **No history rewritten.** Superseded claims are recorded with their disposition, not deleted.
- **No other agent's work absorbed.** The parallel `docs/constitution/` and the tracked `docs/adr/README.md` catalogue are cited as inputs; neither is edited or moved by this layer.
- **Reality wins.** Where a register disagreed with the current tree, the tree (executable source, live config, accepted ADRs — in that precedence, ADR-0100 §Precedence) is authoritative.
