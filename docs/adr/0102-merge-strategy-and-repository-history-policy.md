---
status: accepted
date: 2026-07-15
accepted_in_principle: 2026-07-15
supersedes: []
references: [0088, 0095, 0096, 0101]
deciders: [operator]
---

# ADR-0102 — Merge Strategy and Repository History Policy

> **Accepted in principle 2026-07-15**, operator amendments folded. The one mechanical enforcement
> (`required_linear_history` + squash-only) is a live branch-protection / repo-setting change, deferred
> to Phase E with an approval gate and an explicit break-glass. It deliberately does **not** revive the
> dormant commit-message land-gate, and **the policy does not depend on any `Unit:` tag folklore**.
>
> **Reclassification (2026-07-16).** The engineering that produced this policy is complete; the one
> live change (`required_linear_history` + squash-only + auto-delete) is part of **Operational
> Governance Deployment (OGD)** — deployment of repository security policy, a governance-operations
> activity, not engineering. Wherever this ADR says "Phase E," read "OGD."

## Status

**Accepted** (in principle, 2026-07-15). No existing catalogue slug — the archaeology confirms **no
merge-strategy decision was ever recorded**, which is the gap this ADR closes.

## Context

`main`'s history is **genuinely mixed** (Phase-A freeze evidence, `git log origin/main`):

- Recent (#637…#657): **squash** merges — one commit per PR, conventional-commit subject +
  `(Unit: <slug>)` + `(#NNN)` (e.g. `fix(ledger): RC-4/RC-5 … (Unit: rc5-restore-serialize) (#653)`).
- Earlier (#595…#645): **merge commits** — `Merge pull request #NNN from Fleezyflo/…`, and even
  `Merge branch 'main' into <feature>` (stacked-PR integration merges).

Live branch protection has `required_linear_history = false` — both strategies are currently legal.
There is **no declared, machine-verifiable policy**. The mixed history has already caused real pain:
stacked children breaking after a parent squash-merge (conflict + no CI), documented in project memory.

## Decision

**1 · Squash-merge is the sole strategy for `main`.** Merge commits and rebase-merges are retired. One
PR → one commit on `main`. Rationale: it matches the *current* (#637+) convention, produces linear
history by construction, makes each PR a single revertable unit, and eliminates the
`Merge branch 'main' into …` stacked-integration commits that caused the conflict class.

**2 · Squash commit-message contract** (preserves root-cause + ADR context; **the merge POLICY does not
depend on any of these — they are documented conventions, enforced by nothing**):

- Conventional prefix: `type(scope): summary` (e.g. `fix(publish): …`, `feat(arch): …`, `chore(ci): …`).
- **Root-cause handle preserved:** the RC-/MOL-/S-id the PR closes, in the subject or body.
- The PR number **`(#NNN)`** (GitHub appends it automatically on a squash merge).
- When a PR implements an ADR, the body cites it (`ADR-01NN`), so the decision trail survives the squash.
- **No `Unit:` tag dependency.** The `(Unit:<slug>)` marker belongs to the dormant land-gate (0096) and
  is **not** part of this policy; the squash-only + linear-history guarantee stands entirely on
  `required_linear_history`, independent of any commit-message content.

**3 · Branch naming:** `<lane>/<slug>` for wave work (the lane-guard resolves ownership from the prefix
— `publish/`, `picking/`, `rfd/`, `ci/`; ADR 0095), or `<type>/<short-slug>` / `fix/mol-<id>` for
one-off work. The branch is disposable; the squash subject is the durable record.

**4 · Generated-artifact conflicts are NEVER hand-merged.** A conflict in a derived artifact (the
`.reports/architecture/` KB, `ci-timing.json`, any generated doc/table, the ownership view) is resolved
by **regenerating from the final merged source state** (`python -m tools.arch regen && … docs`, or the
registry generator), never by manually reconciling the diff. Derived files are pure functions of source
(the arch engine's founding invariant); a hand-merged derived file is drift by definition and the arch
drift gate will (correctly) reject it.

**5 · Stale-branch deletion:** the head branch is deleted on merge (GitHub "automatically delete head
branches" repo setting — Phase E). No long-lived feature branches on the remote.

**6 · Replacement-PR rule:** when a stacked child breaks after its parent squash-merges (conflict + no
CI re-run), **do not** force-push the old PR into shape — rebase the intended change onto fresh `main`
in a **new** branch, open a **replacement** PR, and note the superseded PR number in its body. (This is
the documented recovery for the exact stacked-PR failure in project memory.)

**7 · `force-with-lease` policy:** `git push --force` / `-f` is **denied** (`.claude/settings.json`).
`--force-with-lease` is permitted **only** on a private feature branch you solely own, to update your
own open PR — **never** on `main`, and **never** over another agent's commits (the wave gate blocks
force-push during multi-agent work; parallel orchestrators colliding is a recorded failure). When in
doubt, open a replacement PR (rule 6) instead of force-pushing.

**8 · Mechanical enforcement — scoped honestly:**

- **`required_linear_history = true`** (Phase E, operator-approved) mechanically forbids merge commits
  on `main` — the single, robust enforcement of squash-only.
- The **generated-artifact rule** is already enforced by the arch drift gate (`CI-UNIT-ARCHGOV` /
  `ARCH-GATE`): a hand-merged derived file fails the byte-compare.
- The **commit-message / branch-naming conventions remain documented conventions, not a gate.**
  Mechanically enforcing the message format is precisely the dormant `(Unit:<slug>)` land-gate
  (catalogue 0096, disabled by operator decision); this ADR does **not** revive it. Re-enabling a
  commit-message gate is a *separate* ADR + explicit operator approval.

**9 · Emergency merge (break-glass) — explicit, auditable, temporary, restored.** Squash-only +
`required_linear_history` must not become a trap. The single sanctioned way to land an emergency change
that cannot satisfy the linear-history constraint: an admin (a) records the reason; (b) temporarily
disables the blocking setting — `gh api -X PUT …/protection --input <pre-image-with-linear-false>` (and,
only if a merge commit is genuinely unavoidable, `PATCH …repo -F allow_merge_commit=true`), logged in
the GitHub audit log; (c) lands the fix; (d) **immediately restores** — re-enable
`required_linear_history=true` and squash-only from the Phase-A/registry pre-image; (e) files a
follow-up. This is the same break-glass discipline as ADR-0101 §4 (admin enforcement): no standing,
undocumented exception — every bypass is a logged, reverted event.

## Alternatives considered

- **Keep the status quo (mixed).** Rejected — it is the defect: unprovable policy + the stacked-PR
  conflict class.
- **Merge-commit-only** (preserve every PR's internal commits). Rejected — noisier history, no
  single-commit revert, and it is what produced the `Merge branch 'main' into …` integration commits.
- **Rebase-merge.** Rejected — rewrites author commits onto `main` without a squash boundary; loses the
  one-PR-one-commit revert unit and complicates the `(#NNN)` provenance.

## Rejected alternatives (non-obvious)

- **Mechanically enforcing the squash message via a revived land-gate.** Rejected — it directly
  contradicts the operator's dormancy decision (0096). The convention is documented; enforcement waits
  for a deliberate separate ADR.
- **Allowing merge commits for stacked PRs "when convenient".** Rejected — a per-case exception is how
  the mixed history arose; the replacement-PR rule (6) handles stacking without merge commits.

## Consequences

- `main` becomes linear and one-commit-per-PR; `git revert <sha>` cleanly undoes a landed PR.
- The stacked-PR conflict class is addressed by the replacement-PR rule instead of integration merges.
- Derived-file conflicts have one resolution (regenerate), removing a whole class of hand-merge errors.
- History provenance (`type(scope)`, `(Unit:)`, `(#NNN)`, ADR refs) is preserved in the squash subject.

## Risks

- **`required_linear_history` blocks a legitimate emergency merge-commit workflow** if one is ever
  needed. *Mitigated:* `enforce_admins=false` (ADR-0101) leaves a break-glass path. *(estimate.)*
- **Convention-only message rules will drift** without a gate. *Accepted residual* — the alternative
  (reviving the land-gate) is out of scope by operator decision; the #637+ history shows the convention
  already holds in practice. *(proven trend; unenforced.)*

## Migration plan

1. Adopt squash-only as convention immediately (already the #637+ practice).
2. Phase E: enable `required_linear_history = true`; set repo merge button to **squash-only** (disable
   merge-commit + rebase); enable auto-delete-head-branch. Each is a separate operator-approved change
   with a pre-image.
3. Document the message/branch conventions in `AGENTS.md` (docs-only; no gate).

## Rollback plan

No live change from this ADR itself. The Phase-E mutations each carry a rollback (`required_linear_history`
→ `false`; re-enable merge-commit/rebase buttons; disable auto-delete). Reverting the policy = a new
superseding ADR.

## Enforcement mechanism

`required_linear_history` (Phase E) + the existing arch drift gate for derived artifacts. No new
commit-message gate (dormant land-gate not revived).

## Verification contract

- After Phase E: `gh api …/protection` shows `required_linear_history.enabled = true`; the repo's
  allowed merge methods = squash only.
- The arch drift gate stays green (derived-file rule already enforced).
- No CI job is added or modified.

## Superseded decisions or documents

- None superseded. Fills the *absent* merge-strategy decision. Explicitly does **not** revive **0096**.

## Affected workflows and controls

- **Workflows/controls:** none modified. This ADR touches repository *settings* (merge methods, linear
  history, branch auto-delete), all in Phase E.
- Cross-refs ADR-0101 (`required_linear_history` is a shared branch-protection field).

## Operator decisions required

1. **Accept ADR-0102?** (Y/N) — squash-only + the history rules.
2. **Enable `required_linear_history = true`** (Phase E)? *Recommendation:* yes — it is the one robust
   mechanical guarantee of squash-only.
3. **Repo merge-method setting:** restrict to squash-only + enable auto-delete-head-branch (Phase E)?
4. **Commit-message enforcement:** keep convention-only (recommended, respects 0096), or open a
   separate ADR to revive a message gate?
