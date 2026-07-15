---
status: accepted
date: 2026-07-15
accepted_in_principle: 2026-07-15
supersedes: []
references: [0089, 0096, 0097, 0099, 0100]
deciders: [operator]
---

# ADR-0101 — Required Checks and Merge-Gate Policy

> **Accepted in principle 2026-07-15**, operator amendments folded. Declares the merge-gate *policy*;
> it mutates no live branch protection. Every implied change is deferred to Phase E, applied one at a
> time with a captured pre-image and an explicit gate — **no live mutation until the `tools/ci`
> validator and the repository-remediation PRs are green** (`docs/ci/CI_BRANCH_PROTECTION_MUTATIONS.md`).
>
> **Reclassification (2026-07-16).** The engineering implementation of this ADR is **complete and
> merged** — the required-context set, the `tools/ci` validator, and its wiring. The remaining
> "Phase E" is reclassified as **Operational Governance Deployment (OGD)**: the deployment of
> repository **security policy** to the live branch-protection surface. It is a **governance-operations**
> activity, **not remaining engineering work**; wherever this ADR says "Phase E," read "OGD." The two
> concepts are kept distinct — engineering produced the policy; OGD deploys it.

## Status

**Accepted** (in principle, 2026-07-15). Formalizes catalogue **0089 `GOV-TWO-REQUIRED-GATES`** and
**0097 `GOV-CI-ONLY-APPROVAL`**; depends on ADR-0100. Explicitly does **not** revive **0096
`GOV-ENFORCEMENT-GATE-DISABLED`** or the `(Unit:<slug>)` land-gate.

## Context

Live branch protection (re-probed 2026-07-15): strict=true; **2** required contexts;
`enforce_admins=false`; 0 required reviews; force-push/deletions off; conversation-resolution off;
linear-history off; repo merge methods = squash+merge-commit+rebase, delete-branch-on-merge off.

The intended required set is undeclared, so the live set is unprovable from the tree, and required
contexts match by mutable job `name:` strings (a rename **deadlocks** the queue — fails closed, not a
bypass). Two constraints bound this ADR: never require code-owner review (it would block the
orchestrator's autonomous merge — 0097); do not revive the dormant enforcement gate / land-gate (0096).

## Decision

**1 · Five required contexts** — each owning a **distinct** merge-blocking invariant (exact `name:`
strings, verified 2026-07-15):

| Required context | Control | Distinct invariant it owns |
|---|---|---|
| `unit (fast, no toolchain)` | CI-UNIT | hermetic logic suite + lint + SLO + secret-scan + lock-drift + the skip→fail hook |
| `real-tooling E2E (must run, not skip)` | CI-E2E | the real ffmpeg/whisper pipeline runs (not mocks) + cross-face proofs + **validator-effectiveness** (negative controls) |
| `base install (no extras) refuses smart-framing` | CI-BASEINSTALL | clean no-extras packaging + cv2 fail-closed (loud refuse, never a silent centre-crop) |
| `gate (drift + policy + registries)` | ARCH-GATE | **architecture governance**: derived artifacts byte-match source + no BLOCKING policy finding + registries valid |
| `lane file-ownership + cross-PR collision` | LANE-GUARD | no cross-lane / cross-open-PR hot-file collision |

**Advisory** (run, do not block): `ARCH-IMPACT` (impact report), `CI-TIMING` (timing/reporting).
**Scheduled** (off the PR path): `ARCH-RECONCILE`, `NIGHTLY-ASR`, and **`NIGHTLY-PIPAUDIT` (dependency
audit) — stays advisory until its failure policy is separately approved** (a distinct risk decision, not
promoted here). **Local-only:** `LOCAL-RUFF-PRECOMMIT`, `LOCAL-CHECK-SH`, `LOCAL-SECRETSCAN`.

**2 · No duplicate sub-gate is required merely because it runs separately.** Sub-gates block
*transitively* through their parent required job and are never their own GitHub context. Under Model A,
**architecture-governance enforcement is owned by `gate`** — the authoritative merge-blocking path —
**once `gate` is a required context (Phase E, mutation M1)**. Until then, the unit-collected
`test_arch_governance.py` is the *only* required line enforcing arch drift/policy/registries, so its
overlap with `gate` is deliberately **retained** — recorded as the `arch-drift-policy` `duplicate_group`
with a stated distinct boundary, not silent duplication. `test_arch_governance.py` also carries the
invariants `gate` does **not** run (regeneration determinism, generated-artifacts-are-a-pure-function-of-source,
rule reachability, field-authority), which are **distinct**. `SLICE-ARCH-MODEL` (Phase D) records this
truthfully; **de-duplication** — scoping the unit lane down to only those distinct invariants — is a
**post-M1 follow-up**, because removing the overlap before `gate` is a proven-required, stable context
would leave arch drift/policy/registries with **no required enforcement** (an enforcement gap). The
negative controls (validator-effectiveness) remain a distinct invariant carried by `CI-E2E`; the advisory
`ARCH-CONTROLS` reduces to a reachability assertion (`SLICE-NEGCTRL-DEDUP`). Net at Phase-E completion:
five required contexts, five distinct invariants, no required duplicate — the transitional overlap is a
tracked, accepted residual until the post-M1 de-dup.

**3 · Reconciliation** (`intended_required_contexts` == live) is proven by **DC-3** (authenticated,
scheduled). **Anti-detach** by **DC-1** (static, per-PR): a rename not mirrored in branch protection +
registry in the same PR fails DC-1 before it can deadlock the queue.

**4 · Administrators — `enforce_admins` ENABLED, last, after proof of stability.** No standing,
undocumented admin bypass. Enable `enforce_admins=true` **only after all five required checks are
proven stable green on the remediation PR** (Phase E, final mutation). **Break-glass** (the one
sanctioned bypass) is **explicit, auditable, temporary, and restored**: to land an emergency fix, an
admin (a) records the reason, (b) `DELETE …/protection/enforce_admins` (logged in the GitHub audit
log), (c) merges the fix, (d) immediately `POST …/protection/enforce_admins` to **restore** protection,
(e) files a follow-up. There is no other bypass.

**5 · No required reviews, no CODEOWNERS** — settled (would block the orchestrator's autonomous merge).

**6 · Enable `required_conversation_resolution=true`** — unresolved review threads block a merge (cheap
integrity; operator amendment).

**7 · Enable auto-delete of merged branches** (repo setting `delete_branch_on_merge=true`) — no
long-lived merged branches (operator amendment; ties to ADR-0102).

**8 · Change-control for FUTURE required checks** (the promotion process; ADR-0100 lifecycle). A control
moves advisory → required only when **all six** hold, else it stays advisory: (1) unique invariant;
(2) false-positive rate characterized over an observation period; (3) runtime acceptable; (4) actionable
failure messages; (5) rollback exists (flip the registry row + revert the mutation); (6) named owner.
Promotion executes as: registry row `required: true` → Phase-E mutation (one at a time, pre-image
captured) → DC-3 green. Removal deletes the job **and** its registry row together.

## Alternatives considered

- **Keep only 2 required** (the draft). Superseded by the operator amendment: `base-install`, `gate`,
  `lane-guard` each own a real merge-critical invariant with no other blocking owner.
- **Promote by renaming the three jobs to short contexts** (`base-install`, `gate`, `lane-guard`).
  Deferred — the required contexts use the current exact `name:` strings (zero rename risk); a rename to
  cleaner display names is a *separate* controlled migration guarded by DC-1, not bundled with promotion.
- **Keep `enforce_admins=false`** (the draft recommendation). Rejected by amendment — an undocumented
  admin bypass is not preserved; a governed break-glass replaces it.

## Rejected alternatives (non-obvious)

- **Requiring the unit-lane arch tests AND `gate`** for the same invariant. Rejected — that is the
  "duplicate required merely because it runs separately" the amendment forbids; Model A gives `gate`
  sole ownership.
- **Promoting `dependency audit` to required now.** Rejected — its failure policy (what a CVE finding
  *does* to a merge) is a separate risk decision requiring its own approval.
- **Reviving the disabled enforcement gate / land-gate** to "make CI stricter." Out of scope (0096).

## Consequences

- Five declared, reconciled required contexts, each a distinct invariant; a rename can no longer
  silently detach one (DC-1); admin bypass becomes a governed, auditable break-glass, not a standing
  hole; unresolved threads and stale merged branches are closed off.
- Every future required-check change has one auditable path (registry → Phase-E mutation → DC-3).

## Risks

- **Promoting three checks at once raises the merge-availability surface** — a flaky `gate`/`lane-guard`
  could block PRs. *Mitigated:* Phase-E adds them one at a time, each proven stable first; DC-3 +
  rollback per step. *(estimate.)*
- **`lane-guard` depends on best-effort `LINEAR_API_KEY`** — a token hiccup could red a PR once required.
  *Mitigated:* promote only after an observation window characterizes that failure mode (criterion 2);
  it is intentionally the third promotion. *(estimate.)*
- **`enforce_admins=true` removes casual break-glass.** *Mitigated:* the explicit temporary-disable →
  restore procedure. *(proven capability; governed.)*
- **DC-3 needs a scoped token** — fails loudly on auth error rather than reporting "in sync." *(estimate.)*

## Migration plan

Policy only here. Realization: Phase C lands DC-1/DC-3 in `tools/ci`; `SLICE-ARCH-MODEL` +
`SLICE-NEGCTRL-DEDUP` land in Phase D; Phase E applies mutations **in the operator's order** — add
`gate` → add `base-install` → add `lane-guard` → enable conversation-resolution → require linear
history + squash-only (ADR-0102) → **enable `enforce_admins` last**.

## Rollback plan

No live change from this ADR. Each Phase-E mutation carries its exact `gh api` rollback restoring the
Phase-A pre-image. Reverting the policy = a superseding ADR; registry classifications revert with it.

## Enforcement mechanism

`DC-1` (anti-detach), `DC-3` (intent==live + reports admin/reviews/conv-res/linear), `DC-4`
(prose==classification) — all in `tools/ci`, each with a negative control. Branch protection remains the
live enforcer; the registry declares intent; the DCs prove agreement.

## Verification contract

- DC-1: every required context ∈ workflow job names.
- DC-3: `intended_required_contexts` (the five) == live required contexts; reports `enforce_admins`,
  review count, conversation-resolution, linear-history.
- After Phase E: live shows five required contexts, `enforce_admins=true`,
  `required_conversation_resolution=true`.

## Superseded decisions or documents

- Formalizes **0089** + **0097** (makes their tacit facts machine-verifiable; supersedes the
  accepted-residual admin-bypass of 0097 with a governed break-glass). Does **not** revive **0096**.

## Affected workflows and controls

- **Required:** CI-UNIT, CI-E2E, CI-BASEINSTALL, ARCH-GATE, LANE-GUARD.
- **Advisory:** ARCH-IMPACT, CI-TIMING. **Scheduled/advisory:** ARCH-RECONCILE, NIGHTLY-ASR,
  NIGHTLY-PIPAUDIT (until failure policy approved).
- BP/repo settings referenced: `required_status_checks.contexts`, `enforce_admins`,
  `required_conversation_resolution`, `delete_branch_on_merge`, `required_linear_history` (ADR-0102).
- No workflow modified by this ADR (Phase D does that).

## Operator decisions — RESOLVED (2026-07-15)

1. Accept ADR-0101 → **Yes, in principle**.
2. `enforce_admins` → **enable, last, after stability proof**; governed break-glass replaces the bypass.
3. `base-install` → required (**yes**), plus `gate` and `lane-guard`.
4. `required_conversation_resolution` → **enable**; `delete_branch_on_merge` → **enable**;
   `required_linear_history` → **enable** (ADR-0102).
