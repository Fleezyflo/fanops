<!-- Constitution Implementation Roadmap — ordered minimal slices to close the gap between each rule's
     intent and its enforcement. Base: origin/main @ 04c4092 (#664), 2026-07-16.
     NOTHING here is implemented yet — this is the plan. Each slice is classified; runtime slices owned by
     the CI governance program are CROSS-REFERENCED, not re-owned. This layer's own PR is docs-only. -->

# Constitution — Implementation Roadmap

Every `partially-enforced` / `proposed` / `documented-only` rule in the Constitution and the Laws is a
gap. This roadmap converts each into the **smallest effective** slice, ordered by dependency, and
classifies it. It implements none of them — it is the plan presented for review.

**Classes:** `doc-correction` · `adr-formalization` · `governance-automation` · `runtime-remediation`
(owned by the CI program — cross-ref) · `accepted-residual` (no action) · `cancelled-after-premise-invalidation` · `blocked-on-product-decision`.

**Slice fields:** *Invariant · Evidence · Owner · Gap · Fix (smallest) · Deps · Blast · Proof (fail→pass) · Rollback · CI/gov integration · Product decision?*

---

## 0 · Cancelled after premise invalidation

The evidence dossier (written @ #652) named these as open gaps; revalidation against `origin/main` #664
shows each is **already closed**. Recording them so they are not re-attempted (`docs/governance/EVIDENCE_RECONCILIATION.md` R3–R6).

| Cancelled slice | Why cancelled (current evidence) |
|---|---|
| ~~Fix "merge policy not machine-verifiable"~~ | ADR-0100 + `.github/ci-control-registry.yml` + `tools/ci` validator (#661) already exist (R4). Remaining work is *deployment*, tracked below as runtime cross-refs — not a "build the plane" slice. |
| ~~Harden lane-guard (SHA-pin + timeout)~~ | Landed #663 (R5). |
| ~~Fix RC-4/RC-5 wipe/restore data-loss~~ | Landed #653/#654/#655 (R3). |
| ~~Reconcile the `docs/adr/README.md` collision / "build an ADR system"~~ | The ADR system is active; the catalogue is the tracked README; no collision (R1). |

## 1 · Documentation corrections (cheapest, highest-legibility; land first)

### SLICE-DOC-AGENTS — `doc-correction`
- Invariant: no doc advertises a dormant mechanism as live authority (C16.4, R2).
- Evidence: `AGENTS.md:156,180` present the `.cursor` hook land-gate as the merge authority; it is disabled (#645, catalogue 0096); ADR-0101/0102 do not revive it.
- Owner: operator / docs. · Gap: stale "live gate" prose. · Fix: edit `AGENTS.md` to state the gate is dormant-by-decision; the merge rails are branch protection + required checks (ADR-0101) + lint-only `check.sh`; `(Unit:<slug>)` is a documented convention, not a gate.
- Deps: none. · Blast: docs-only (one file). · Proof: before — `AGENTS.md` claims an enforcing gate; after — it matches ORCHESTRATION.md's dormancy banner + ADR-0102 §2. · Rollback: revert the edit. · CI/gov: future CM-4 (dormant-governance) would flag it. · Product decision: **No**.
- *Note:* `AGENTS.md` is a repo doc; this edit is **out of scope for the constitutional-layer PR** (which is docs-only *for the new layer*). Sequence as a **follow-up doc PR** to avoid touching an unrelated tracked file in the constitution PR.

### SLICE-DOC-ANOMALIES — `doc-correction` — ✅ **DONE** (2026-07-16)
- Invariant: a frozen snapshot is labeled as such, not as current truth (C16.4, R3/R8).
- Evidence: `docs/CODEMAPS/anomalies.md` "none CRITICAL / all HOLD" is stale (RC-4/RC-5 were real, now fixed).
- Owner: docs / `tools/arch` codemaps. · Gap: no superseded/frozen banner. · Fix: add a header banner "frozen 2026-07-11 snapshot; superseded on RC-4/RC-5 by #653–655; current invariant state is `tools/arch` + `INVARIANT_AUDIT.md`."
- Deps: none. · Blast: docs-only. · Proof: before — reads as current; after — reads as historical with a pointer. · Rollback: revert. · CI/gov: CM-4/CM-8 report. · Product decision: **No**. · *Follow-up doc PR.*
- **Landed:** the frozen/superseded banner + an in-place `⚠ CORRECTION` on the false "all HOLD"
  paragraph and the §Summary row (recorded, not rewritten). Cites #653/#654 and hands the invariant
  verdict to `tools/arch` + `INVARIANT_AUDIT.md` + `LAW-PERSIST-02`. Scope note: the slice said
  "#653–655"; only **#653/#654** are the RC-4/RC-5 restore fixes (#655 is RC-3 accounts-backend
  normalization, a different defect) — the banner cites the two that are actually this defect.
  **Also landed in the same PR** (same file, same authority): the **C1 re-verification** against live
  source — 4 of 6 C1 entries were stale (`RenderState` is live at `views_results.py:112`, not dead;
  the `ledger.py` docstring is fixed; `ledger_wipe` moved `:188`→`:218` and is only *partially*
  fixed), plus a cross-cluster fix-quality spot-check. C2–C10 remain un-re-verified.

### SLICE-ADRFORMAT-TRACK — `doc-correction`
- Invariant: a declared governance artifact lives in the tree, not on one machine (C16.1, R1/R7, AR-7).
- Evidence: `.agents/skills/domain-modeling/ADR-FORMAT.md` exists but is **untracked**.
- Owner: operator. · Gap: the ADR convention is untracked. · Fix: `git add` the file (verify content first; it is a real Nygard convention). · Deps: none. · Blast: adds one tracked file. · Proof: before — `git ls-files` empty; after — tracked. · Rollback: `git rm --cached`. · CI/gov: CM-4 (dormant/untracked governance) would flag it. · Product decision: **No**.

### SLICE-DOSSIER-RETIRE — `doc-correction`
- Invariant: superseded evidence is not left as a live-looking authority (C18.3, R11).
- Evidence: `docs/CONSTITUTION-EVIDENCE-DOSSIER.md` (untracked, shared checkout, authored @ #652) has five superseded rows.
- Owner: this layer's author. · Gap: a partly-stale dossier sits untracked. · Fix: either add a "superseded by `docs/governance/EVIDENCE_RECONCILIATION.md`" banner and leave it as historical evidence, or remove it (it is mine — no rule-3 concern). · Deps: none. · Blast: one untracked file. · Proof: before — reads as current; after — clearly historical or gone. · Rollback: restore from git history if it was ever tracked (it was not) / re-create. · CI/gov: none. · Product decision: **No**.

## 2 · ADR formalization

### SLICE-ADR-NUMBERING — `adr-formalization` (prerequisite for all other ADR cuts)
- Invariant: ADR numbering is unambiguous (C16.1).
- Evidence: `FORMALIZATION_ROADMAP.md` §Prerequisite — ADR-FORMAT "increment from highest" (→0104) collides with the catalogue's reserved 0001–0099.
- Owner: operator. · Gap: two numbering rules. · Fix: cut `docs/adr/0104-adr-backfill-numbering.md` adopting "0001–0099 = reserved back-fill numbers; 0100+ = net-new." · Deps: none. · Blast: one ADR + one README row. · Proof: before — ambiguous; after — a ratified policy the roadmap keys to. · Rollback: supersede with a new ADR. · CI/gov: CM-2 (index integrity). · Product decision: **No** (an engineering convention).

### SLICE-ADR-FORMALIZE-T1 — `adr-formalization`
- Invariant: the hardest-to-reverse decisions have standalone ADRs (C16.2).
- Evidence: `FORMALIZATION_ROADMAP.md` Tier 1 (10 slugs: STATE-NO-AUTO-PUBLISH, PUBLISH-CLAIM-NETWORK-FINALIZE, …).
- Owner: operator / authors. · Gap: 10 Tier-1 decisions live only in the catalogue. · Fix: cut 10 `docs/adr/00NN-slug.md` files from the catalogue entries + roadmap; each ≤ the ADR-FORMAT template; add README index rows; preserve supersession links. · Deps: SLICE-ADR-NUMBERING. · Blast: 10 ADR files + index rows (docs-only). · Proof: before — CM-3 reports 10 missing Tier-1 ADRs; after — 0. · Rollback: delete the files + rows. · CI/gov: CM-2/CM-3/CM-5. · Product decision: **No** (records existing decisions; changes none).

## 3 · Governance automation (design in `CONSTITUTION_MAINTENANCE.md`; gated on the CI program's DC-*)

### SLICE-CONSTLINT — `governance-automation`
- Invariant: the constitutional layer cannot silently rot (C1.2, CM-1/2/5/6).
- Evidence: `CONSTITUTION_MAINTENANCE.md` CM-1/2/5/6.
- Owner: `constitution-lint` (thin, delegates to `tools/arch`/`tools/ci`; ADR-0100 "share method not ownership"). · Gap: no mechanical check of schema/index/supersession/citations. · Fix: build the four blocking checks + a negative control each; collect in the fast unit lane like `test_arch_governance.py`. · Deps: none hard (reuses `tools/arch` symbol table). · Blast: new pure-Python module + tests (runtime — **not** in this docs-only PR). · Proof: before — a fieldless rule / dangling citation passes; after — CI red, proven by the negative controls. · Rollback: one-line collection revert. · CI/gov: advisory → promotable via ADR-0101 §8. · Product decision: **No**.

### SLICE-CM-CONTRADICTION — `governance-automation`
- Invariant: a rule may not claim an enforcement it does not have (C1.2, CM-8).
- Evidence: `CONSTITUTION_MAINTENANCE.md` CM-8.
- Owner: a reconciler delegating to `DC-3` + arch policy + registry. · Gap: "enforced" claims are hand-verified today. · Fix: build the report-only cross-plane detector. · Deps: **the CI program's `DC-3`** must land first (runtime cross-ref below). · Blast: new report job. · Proof: before — a mislabeled law passes; after — it appears in the drift report. · Rollback: remove the job. · CI/gov: report-only (never auto-fixes). · Product decision: **No**.

## 4 · Runtime remediation — OWNED BY THE CI GOVERNANCE PROGRAM (cross-reference only)

These close `partially-enforced`/`proposed` **laws**, but they are **not this layer's to implement** —
they belong to ADR-0100/0101/0102 Phases C–E and the registry's named slices. Listed so the gap is
visible and attributed; the constitution's enforcement fields flip to `enforced` when these land.

| Cross-ref slice (owner: `tools/ci` / operator) | Closes | Gate |
|---|---|---|
| `DC-1…DC-6` land as the `tools/ci` validator, then required | LAW-SOT-05, LAW-SOT-03 (DC-4), LAW-DOC-01 (DC-5), LAW-CI-07 (DC-6) | ADR-0100 Phase C |
| `SLICE-ARCH-MODEL` (scope unit-lane arch tests to distinct invariants) | LAW-OWN-01 duplicate justification | ADR-0101 Phase D |
| `SLICE-NEGCTRL-DEDUP` (one authoritative full neg-control run) | LAW-CI-03 residual | Phase D |
| `SLICE-BASEINSTALL-REQUIRED` (promote `CI-BASEINSTALL`) | LAW-FAIL-03 (cv2 refusal becomes a live required gate) | Phase E (2nd) |
| Phase-E branch-protection mutations (add `gate`→`base-install`→`lane-guard`; `enforce_admins`; `required_linear_history`) | LAW-CI-04, LAW-CI-06 | ADR-0101/0102 Phase E |

**Not duplicated here.** This roadmap does not re-plan the CI program; it points at it. Editing a CI
workflow, the registry, or branch protection is explicitly **out of scope** for the constitutional-layer
PR (docs-only, no CI-setting/branch-protection/runtime change).

## 5 · Blocked on product decision

| Slice | Decision the operator must make | Owner |
|---|---|---|
| `BLOCK-ADR0103` | accept ADR-0103 (subject/layout-aware reframe) + the reframe remediation roadmap → unblocks the framing fix (currently the content-blind fallback ships; AR-5) | operator |
| `BLOCK-ENFORCE-ADMINS` | enable `enforce_admins` + promote the 3 remaining required contexts (Phase E order) — a merge-availability/risk call | operator (ADR-0101) |
| `BLOCK-PIPAUDIT` | promote `NIGHTLY-PIPAUDIT` to a gate, or keep advisory — a CVE-response risk/product call | operator (ADR-0101) |
| `BLOCK-CONV-RESOLUTION` | enable `required_conversation_resolution` (Phase E) | operator (ADR-0101 §6) |

## 6 · Accepted residuals (no action; recorded in Constitution §17)

`AR-1` RC-9 mutation-time deferral (pinned by S11) · `AR-2` Studio localhost no-auth · `AR-4`
commit-message grammar unenforced (reviving = the dormant land-gate) · `AR-6` swallow ratchet accepts
stdlib `logging` (surfacing is a review judgment) · `AR-8` side-effect census is WARNING not BLOCKING.
Each is zero/low-reachability, contained, and owned; none is a slice.

## Ordered execution (dependency DAG)

```
SLICE-ADR-NUMBERING ─► SLICE-ADR-FORMALIZE-T1
SLICE-ADRFORMAT-TRACK ─┐
SLICE-DOSSIER-RETIRE   ├─ (independent doc-corrections, any order)
SLICE-DOC-AGENTS*      │   (*follow-up doc PRs — not the constitution PR)
SLICE-DOC-ANOMALIES*   ┘
CI-PROGRAM DC-* ─► SLICE-CM-CONTRADICTION
SLICE-CONSTLINT (independent; needs no DC-*)
```

**First wave (safe, docs-only, high-value):** SLICE-ADRFORMAT-TRACK, SLICE-DOSSIER-RETIRE,
SLICE-ADR-NUMBERING → SLICE-ADR-FORMALIZE-T1.
**Second wave (follow-up doc PRs):** SLICE-DOC-AGENTS, SLICE-DOC-ANOMALIES.
**Third wave (automation, after CI program Phase C):** SLICE-CONSTLINT, SLICE-CM-CONTRADICTION.
**Operator track (parallel):** the §5 product decisions + the §4 CI-program Phases D–E.
