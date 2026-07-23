# FanOps — Operational Governance Deployment (repository security policy) — **CANCELLED**

> # ⛔ CANCELLED — DO NOT EXECUTE ANY MUTATION BELOW
>
> **Operator decision, 2026-07-22 (CI simplification).** OGD will not happen. **M1–M6 are cancelled,
> not deferred**, and nothing in this file is a pending action. The document is retained unedited
> below the line as the historical record of a plan that was designed, reviewed, and then dropped —
> deleting it would erase why the repository looks the way it does.
>
> **What is true instead:**
>
> - The required-context set is **FINAL at two**: `unit (fast, no toolchain)` and
>   `real-tooling E2E (must run, not skip)`. `intended_required_contexts` in
>   `.github/ci-control-registry.yml` now equals `current_required_contexts` equals live GitHub, so
>   `python -m tools.ci deployed` reports **no findings** where it previously reported three contexts
>   "pending Operational Governance Deployment".
> - `unit` is the sole **routine** PR blocker for logic. `real-tooling E2E` stays required as a
>   CONTEXT, but its suite is **on-demand** (`scripts/ci_e2e_trigger.py`): manual dispatch, the 04:00
>   UTC nightly schedule, or an explicit `force-e2e` request. On an ordinary push or pull request the
>   context reports in seconds with a message saying the suite did not run.
> - The three contexts M1–M3 would have promoted — the architecture gate, base-install and the lane +
>   cross-PR collision guard — are **advisory**: they still run on every PR and their verdicts are
>   read; they no longer block a merge. Impact was already advisory and is unchanged.
> - M4 (`required_conversation_resolution`), M5 (`required_linear_history` + squash-only) and M6
>   (`enforce_admins`) are cancelled with the rest. `enforce_admins` remains **false**.
>
> **Live branch protection is untouched by this change.** It still requires both contexts, and that is
> deliberate — requirement 4 of the simplification was that the E2E context must not disappear. Should
> anyone later want `real-tooling E2E` **removed** from live branch protection, that is a **repository
> settings mutation requiring separate, explicit operator approval**. No pull request performs it, and
> this one did not: it changed policy, classification and job behaviour in the repository only.
>
> Rationale of record: `docs/adr/0101-required-checks-and-merge-gate-policy.md` (amended 2026-07-22).

---

<sub>Everything below this line is the CANCELLED plan, preserved verbatim as history. It is not a
runbook and has no pending steps.</sub>

> **This is NOT engineering work.** The CI-governance **engineering implementation is complete and
> merged** — the ADRs (0100–0102), the control registry, the `tools/ci` validator (DC-1..DC-6), every
> repository-remediation slice, and the validator's wiring into the required unit lane. What follows is
> **Operational Governance Deployment (OGD)**: the deployment of repository **security policy** to the
> live GitHub branch-protection surface. It is a **governance-operations** activity — distinct in kind
> from the engineering that produced it — and it changes repository access-control settings, not code.
> *(Historically these steps were tracked as "Phase E / Step 6"; that label is retained only as an
> alias for continuity with the merged commit history.)*

> **LIFECYCLE STATUS** (authoritative: `docs/ci/CI_PROGRAM_LIFECYCLE.md`). Phases **1–4**
> (Investigation → Architecture → Governance → Implementation) are **COMPLETE**; this runbook is
> **Phase 5 — Operational Governance Deployment**. The Phase-4 exit criterion — the `tools/ci` validator
> merged, all repository-remediation PRs merged, and all five proposed required jobs green on one final
> SHA (`26bca12`, the tree now on `main`) — is satisfied and independently re-provable (`python -m tools.ci
> reconcile`). **DEPLOYMENT GATE: operator.** Nothing below has been executed. Because each mutation
> changes live repository security settings, every step is applied **only on explicit operator action**,
> **one at a time**, with the **Phase-A pre-image** captured (`freeze/2026-07-15/branch-protection.json`)
> and a **read-only re-probe after every mutation**. Order is the operator directive (2026-07-15).
> Commands are shown for review — the operator applies them.

**Repo:** `Fleezyflo/fanops` · **Branch:** `main` · **Pre-image:** Phase-A freeze (re-verified
2026-07-15: live required = `["unit (fast, no toolchain)","real-tooling E2E (must run, not skip)"]`;
`enforce_admins=false`; conv-resolution=false; linear-history=false; repo squash+merge+rebase all on,
delete-branch-on-merge off).

**Pre-flight (before ANY mutation):** confirm live still equals the pre-image; if it drifted, stop and re-baseline.
```bash
gh api repos/Fleezyflo/fanops/branches/main/protection > /tmp/live-now.json
diff <(jq -S . docs/ci/freeze/2026-07-15/branch-protection.json) <(jq -S . /tmp/live-now.json) && echo "MATCHES pre-image"
```

**Endpoint note.** `required_status_checks` (M1–M3) and `enforce_admins` (M6) have dedicated
sub-endpoints (safe, additive). `required_conversation_resolution` (M4) and `required_linear_history`
(M5) have **no sub-endpoint** — they are only settable via the **full** `PUT …/protection` object,
where a missing field **resets** protection. For M4/M5, build the payload from the live pre-image with
exactly one field flipped (script below), or use a **repository ruleset** (additive; see end).

---

## M1 · Add `gate (drift + policy + registries)` to required contexts  *(FIRST)*

- **Before:** `["unit (fast, no toolchain)","real-tooling E2E (must run, not skip)"]`; strict=true.
- **After:** the two **plus** `"gate (drift + policy + registries)"`; strict unchanged.
- **Reason:** Model A — `gate` is the authoritative merge-gate for architecture governance (ADR-0101).
- **Affected ADR:** 0100/0101. **Registry:** `ARCH-GATE.classification=required` already set; DC-3 stays green.
- **Risk:** *(estimate)* low — `gate` already runs on every PR, stdlib-only, fast.
- **Sequencing — M1 is UNBLOCKED; nothing is de-duplicated first.** The authority is
  `.github/ci-control-registry.yml` (`duplicate_groups.arch-drift-policy`) and ADR-0101 §2. The order is:
  1. **RETAIN** the existing overlap. Until this mutation lands, the unit lane is the **only** required
     line enforcing arch drift/policy/registries.
  2. **M1** — make `gate` a live required context (this step).
  3. **PROVE** it: observe it green and actually required over a real observation window, the same bar
     M6 sets below ("observed green/stable"). A context that is required but flaky is not a gate.
  4. **ONLY THEN** design and execute de-duplication, scoping the unit lane to the invariants `gate`
     does not run — enumerated **from `tests/test_arch_governance.py` at execution time**, never from a
     copied list. (A prose list of "four" already circulates and is believed to omit the path-selection
     tests, which `.github/workflows/architecture.yml` cites by name. A copied count rots; the test file
     does not.)

  > **Corrected 2026-07-18.** This bullet previously read: *"`SLICE-ARCH-MODEL` must land first so `gate`
  > (not the unit arch tests) is the sole required arch owner."* That was **circular** — `gate` can only
  > become the sole required arch owner *via M1 itself* — and executing it literally would have removed
  > the only required arch enforcement **before** its replacement was required, i.e. the enforcement gap
  > the registry explicitly forbids. `SLICE-ARCH-MODEL` was in fact satisfied through the plan's own
  > declared-residual branch: the overlap was **recorded as retained**, not scoped away. The runbook's own
  > closeout section already said so (see §6a residuals and the deferred-items list).
- **Command (DO NOT RUN — approval required):**
```bash
gh api -X PATCH repos/Fleezyflo/fanops/branches/main/protection/required_status_checks \
  -f strict=true \
  -f 'contexts[]=unit (fast, no toolchain)' \
  -f 'contexts[]=real-tooling E2E (must run, not skip)' \
  -f 'contexts[]=gate (drift + policy + registries)'
```
- **Rollback:** re-PATCH with the original two contexts. **Post:** re-probe → 3 contexts, **and open the
  stability observation window from step 3 above.** De-duplication (step 4) may not be designed until that
  window closes green. Without this post-condition M1 has no stability proof, while ADR-0101 §2 makes
  "proven-required, stable" the trigger for de-duplication.

## M2 · Add `base install (no extras) refuses smart-framing`  *(SECOND)*

- **Before:** the 3 from M1. **After:** + `"base install (no extras) refuses smart-framing"`.
- **Reason:** unique packaging + cv2 fail-closed invariant, no blocking backup (ADR-0101).
- **Risk:** *(estimate)* low — runs every PR, green today.
- **Command (DO NOT RUN):**
```bash
gh api -X PATCH repos/Fleezyflo/fanops/branches/main/protection/required_status_checks \
  -f strict=true \
  -f 'contexts[]=unit (fast, no toolchain)' \
  -f 'contexts[]=real-tooling E2E (must run, not skip)' \
  -f 'contexts[]=gate (drift + policy + registries)' \
  -f 'contexts[]=base install (no extras) refuses smart-framing'
```
- **Rollback:** re-PATCH with the 3 from M1. **Post:** re-probe → 4 contexts.

## M3 · Add `lane file-ownership + cross-PR collision`  *(THIRD)*

- **Before:** the 4 from M2. **After:** + `"lane file-ownership + cross-PR collision"`.
- **Prereq:** `SLICE-LANEGUARD-PIN` + `SLICE-LANEGUARD-TIMEOUT-CONCURRENCY` landed, and an observation
  window characterizing the best-effort `LINEAR_API_KEY` failure mode (ADR-0101 criterion 2).
- **Risk:** *(estimate)* medium — the Linear lookup is best-effort; a token hiccup could red a PR. Third
  on purpose.
- **Command (DO NOT RUN):**
```bash
gh api -X PATCH repos/Fleezyflo/fanops/branches/main/protection/required_status_checks \
  -f strict=true \
  -f 'contexts[]=unit (fast, no toolchain)' \
  -f 'contexts[]=real-tooling E2E (must run, not skip)' \
  -f 'contexts[]=gate (drift + policy + registries)' \
  -f 'contexts[]=base install (no extras) refuses smart-framing' \
  -f 'contexts[]=lane file-ownership + cross-PR collision'
```
- **Rollback:** re-PATCH with the 4 from M2. **Post:** re-probe → 5 contexts (== `intended_required_contexts`).

## M4 · Enable `required_conversation_resolution`  *(FOURTH — full-PUT)*

- **Before:** `false`. **After:** `true` (unresolved review threads block a merge).
- **Mechanism:** no sub-endpoint — full `PUT …/protection` with only this field flipped, built from the
  **current** live object (which by now has the 5 required contexts from M1–M3).
- **Command (DO NOT RUN — build payload from live, flip one field):**
```bash
gh api repos/Fleezyflo/fanops/branches/main/protection > /tmp/prot.json
# edit /tmp/prot.json into the PUT shape, setting required_conversation_resolution=true,
# preserving required_status_checks(5 contexts,strict), enforce_admins, reviews, restrictions,
# allow_force_pushes=false, allow_deletions=false, required_linear_history(current).
gh api -X PUT repos/Fleezyflo/fanops/branches/main/protection --input /tmp/prot-put.json
```
- **Rollback:** re-PUT with `required_conversation_resolution=false`. **Post:** re-probe → true.

## M5 · `required_linear_history=true` + squash-only + auto-delete-branch  *(FIFTH — ADR-0102)*

- **Before:** linear=false; repo squash+merge+rebase on; delete-branch off. **After:** linear=true; repo
  squash-only; delete-branch on.
- **Mechanism:** branch protection full-PUT (linear-history) + repo-settings PATCH.
- **Commands (DO NOT RUN):**
```bash
# (a) branch protection — full PUT with required_linear_history=true (preserve all else, incl. 5 contexts + conv-res=true)
gh api -X PUT repos/Fleezyflo/fanops/branches/main/protection --input /tmp/prot-linear.json
# (b) repo settings — squash-only + auto-delete branch
gh api -X PATCH repos/Fleezyflo/fanops \
  -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false \
  -F delete_branch_on_merge=true
```
- **Rollback:**
```bash
gh api -X PUT repos/Fleezyflo/fanops/branches/main/protection --input docs/ci/freeze/2026-07-15/branch-protection.json  # linear=false (NB: also resets contexts — re-apply M1-M3 after)
gh api -X PATCH repos/Fleezyflo/fanops -F allow_merge_commit=true -F allow_rebase_merge=true -F delete_branch_on_merge=false
```
- **Note:** the pre-image PUT rollback resets required contexts too — after a linear-history rollback,
  re-apply M1–M3. Prefer flipping only linear-history back via a fresh full-PUT built from live.
- **Break-glass (ADR-0102 §9):** to land an emergency non-linear merge, temporarily `PUT` linear=false
  (audit-logged), merge, then **immediately restore** linear=true from live. No standing exception.

## M6 · Enable `enforce_admins`  *(LAST — only after all 5 required checks proven stable)*

- **Before:** `false` (admins bypass — the accepted-residual being closed). **After:** `true` (gates bind
  admins too).
- **Prereq:** M1–M5 applied AND all five required checks observed green/stable on the remediation PR.
- **Reason:** ADR-0101 §4 — no standing undocumented bypass; a governed break-glass replaces it.
- **Command (DO NOT RUN):**
```bash
gh api -X POST repos/Fleezyflo/fanops/branches/main/protection/enforce_admins    # enable (dedicated sub-endpoint, safe)
```
- **Break-glass (ADR-0101 §4):** admin records reason → `DELETE …/protection/enforce_admins`
  (audit-logged) → merge emergency fix → **immediately** `POST …/protection/enforce_admins` to restore →
  file follow-up. The only sanctioned bypass; explicit, auditable, temporary, restored.
- **Rollback:** `gh api -X DELETE repos/Fleezyflo/fanops/branches/main/protection/enforce_admins`.

---

## Repository-ruleset alternative (operator note)

M4/M5's full-object PUT is error-prone (a missing field resets protection). The same guarantees
(`required_linear_history`, required checks, conversation-resolution, squash-only) can be expressed as
an **additive GitHub repository ruleset** — diffable, no whole-object resend. Choosing rulesets vs
classic protection is itself a governance decision, surfaced here, not chosen.

## Guarantees

- Nothing executed here. The engineering gate is **met** (validator + remediation + wiring merged, 5/5
  green on the final SHA); each mutation now waits only for explicit, per-step **operator** action.
- Every mutation has a captured pre-image and a tested rollback.
- DC-1 is live (it runs in the required `unit` lane via `tools/ci`), so a promoted context cannot silently
  detach through a job rename. **DC-3 is NOT live** — no workflow invokes it and `run_static` excludes it;
  it needs an operator-provisioned admin token that does not yet exist. Until then, live-vs-declared
  reconciliation is the **manual** read-only re-probe prescribed after every mutation, not an automated
  check. *(Corrected 2026-07-18; this line previously asserted "DC-1 + DC-3 live before M1–M3".)*
- After each applied mutation: re-probe, confirm the intended delta and nothing else, update the
  registry in the same PR. Final state == `intended_required_contexts` (5) + `enforce_admins=true` +
  `required_conversation_resolution=true` + `required_linear_history=true`.

## Program closeout — Phase 6 (produced only after OGD, before the freeze)

When OGD is complete — all six mutations applied, the live surface re-probed, and the registry's
`current_required_contexts` reconciled to `intended_required_contexts` (5) — the program's last act is to
produce **two permanent, immutable records**, then freeze. Neither is written before OGD completes,
because both document the *deployed* state as historical fact.

### 6a · `docs/ci/CI_PROGRAM_CLOSEOUT.md` — the historical closeout
The single immutable record of the entire CI-governance program. Required sections (operator directive,
2026-07-16):

1. **Final architecture** — the reconciled three-plane model (registry = intent, workflows =
   implementation, live branch protection = deployed) as actually realized.
2. **Final control registry** — the frozen `ci-control-registry.yml` state, with `current` == `intended`.
3. **Deployed branch protection** — the post-OGD live surface (the M1–M6 end state), captured verbatim.
4. **ADRs** — 0100/0101/0102 (and any superseding notes), as the decision record.
5. **Validators** — `tools/ci` (DC-1..DC-6), its three modes, and where each is enforced.
6. **Implementation summary** — the PR ledger (#658, #661–#668, #670, the OGD-reclassification PR, and
   the OGD mutation PRs) mapped to what each delivered.
7. **Accepted residuals** — knowingly-retained items (e.g. the unit-lane arch overlap retained until
   `gate` is required; convention-only commit-message rules).
8. **Deferred items** — work consciously postponed (e.g. `SLICE-NEGCTRL-DEDUP`, `SLICE-DOC-INTEGRITY`,
   the post-M1 arch de-duplication, the scheduled DC-3 job + its admin token).
9. **Cancelled work** — anything proposed and dropped, with the reason.
10. **Future amendment process** — how a change is made *after* the freeze.

### 6b · `docs/ci/CI_GOVERNANCE_DNA.md` — the principles of record
A permanent, immutable statement of what this program *established* — so a future engineer inherits the
reasoning, not just the artifacts. Required content (operator directive, 2026-07-16):

- **Principles** — the governing ideas this program proved (three-plane reconciliation; a control has one
  invariant / owner / classification / reason / deletion test; intent must be machine-verified against
  implementation and deployment; a skip is never a pass; every blocking check has a negative control).
- **Governance mechanisms introduced** — the control registry + schema, the `tools/ci` validator
  (DC-1..DC-6), the rollout model, the duplicate-group model, the classification/lifecycle model.
- **Non-negotiable architectural rules** — the invariants no future change may violate without a new
  program (e.g. `tools/ci` ≠ `tools/arch`; required contexts mirror workflow job names; derived artifacts
  are pure functions of source; no required duplicate without a declared distinct boundary).
- **Amendment process** — exactly how a future CI-governance change is proposed, decided (ADR), and
  deployed, and the rule that it starts a **new** program rather than extending this frozen one.

### Freeze semantics
Once **both** `CI_PROGRAM_CLOSEOUT.md` **and** `CI_GOVERNANCE_DNA.md` exist, **this CI-governance program
is frozen.** Both records are immutable. Any subsequent change to CI governance — including to the
`tools/ci` validator itself — begins as a **new** governance program (new ADR + new registry revision
under the DNA document's amendment process), **never** as an extension of this one. Extending a frozen
program is itself a governance violation.
