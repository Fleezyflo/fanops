# Phase 3 — Lifecycle Disclosure

> **This document discloses. It does not repair.** Every gap below is recorded as it stands on
> `main`. No lifecycle row is added to any contract named here, no authorization is asserted after
> the fact, and no state is edited into existence. A gap that is disclosed is still a gap.
>
> **The gaps are DISPOSED, not open** — see *Disposition* below. Each has a final recorded outcome,
> and in all three cases that outcome is that it stands unrepaired. This is a decision that has been
> made, not a decision that is pending.

## Why this exists

Phase 3 built the change-contract compiler and verifier. Four contracts were written under
ADR-0105 while building it. **Only one of the four carries a complete, platform-verified
lifecycle.** The other three landed with rows missing, and in two cases with the change merged
ahead of the authorization that should have permitted it.

Those gaps were not visible while they were being made. They are visible now for two reasons: the
verifier that exposes them is the thing Phase 3 delivered, and the default-base defect that masked
every landed contract behind a `CL-2` was fixed in the same change that publishes this document.
Before that fix, every landed contract answered `CL-2` regardless of its actual state, so the
states below could not be read without knowing to pass `--base` by hand.

## The four contracts, as derived

Machine-derived by `python -m tools.contract state <path>` on `main`. Not transcribed from intent.

| contract | derived state | content approval | merge authorization | acceptance |
|---|---|---|---|---|
| `CC-2026-07-18-change-contract-compiler` | `acceptance_claimed` | satisfied | **stale** | **claimed** |
| `CC-2026-07-19-cli-lifecycle-integrity` | **`merged_unauthorized`** | **not_sought** | **not_sought** | not_sought |
| `CC-2026-07-19-single-operator-authorization` | `merged` | satisfied | satisfied | not_sought |
| `CC-2026-07-20-acceptance-rederivation` | **`accepted`** | satisfied | satisfied | satisfied |

## G1 — `CC-2026-07-19-cli-lifecycle-integrity` merged with no authorization recorded

**Derived state: `merged_unauthorized`.** The contract carries exactly one row — `created`. Its
change landed on `main` as `35cbf7fcebdd9e2b5f657a971af6c31140879123` (PR #705), and the contract
file was **added to `main` by that same commit**.

Absent: `approved`, `implementation_started`, `head_proposed`, `merge_approved`, `merged`,
`accepted`. All three gates read `not_sought` — not *failed*, but *never asked*.

This is the most serious of the three. A change to the governance tooling itself merged without a
recorded content approval or merge authorization, under a contract that existed only to say the
change had begun.

## G2 — `CC-2026-07-18-change-contract-compiler` claims an acceptance it cannot support

**Derived state: `acceptance_claimed`, not `accepted`.** Three distinct defects:

1. **The merge authorization is incomplete.** Its `merge_approved` row omits `digest` and `pr`. Of
   the five `MERGE_AUTH_VALUES`, three are present. The verifier's words: *"an authorization
   missing any of parent_sha, digest, pr, operator, token is not specific enough to authorize a
   merge."* Post-merge rederivation at PR head `00128edeed0d` fails for the same reason.

2. **An `accepted` row rests on that unauthorized merge.** The row is present and the state is
   `acceptance_claimed`, because *"acceptance cannot rest on an unauthorized merge."* The
   distinction between an `accepted` row and an `accepted` state is the whole of §4.3a, and this
   contract is the case that distinction was written for.

3. **A record naming its own commit.** The `merged` and `accepted` rows both name
   `merge_sha=2bcb0641e0b51631ccd7916ccb79dc5283ca774f` — and the contract file was added to `main`
   **by `2bcb064` itself**. Both rows were therefore written inside the commit they claim to
   describe, before the merge they record had occurred.

Every timestamp in this contract is a round clock value (`14:00:00Z`, `17:00:00Z`, `17:05:00Z`,
`13:30:00Z`, …) and no row carries a `timestamp_source`. They were authored, not observed. Later
contracts pin each row to a GitHub API `Date` response header; this one predates that practice.

It also carries **two** `approved` rows naming different digests and different tokens, which the
schema permits but which makes "what was approved" a question with two answers.

## G3 — `CC-2026-07-19-single-operator-authorization` was authorized but never accepted

**Derived state: `merged`.** This one is materially different from G1 and G2 and should not be
read alongside them as equivalent.

Content approval is **satisfied**, and merge authorization is **satisfied** — the verifier
rederives it correctly across the squash: *"OPERATOR merge authorization accepted: the head is
50e1aaa79965 plus lifecycle appends to this contract and nothing else … merge 8311bc94b83f is on
`main` and its tree 36e7a058782b equals the authorized PR-head tree."* All five rows carry
`timestamp_source=GitHub API Date response header`.

What is missing is only the **post-merge append**: no `merged` row, no `accepted` row. Its change
landed as `8311bc94b83fc0ba1b2ec0f1e1e163caee75e362` (PR #707). Acceptance was never claimed —
which is correct behaviour, not a false claim. The gap is an omission, not a misstatement.

## What is deliberately NOT done — here or later

Under the disposition above these are permanent, not scoped to this change.

- **No backfill.** No row is appended to G1, G2 or G3.
- **No retroactive authorization.** Nothing in this document authorizes a merge that has already
  occurred, and it must not be cited as though it did.
- **No state repair.** `merged_unauthorized` and `acceptance_claimed` remain the derived states.
- **No edit to any landed declaration.** Each is frozen under its own `D`; editing the body would
  move `D` and void the approval that names it.

Backfilling would be the worst available option: it would produce a clean-looking history whose
cleanliness was manufactured after the fact by the same agent whose omissions created the gaps.
The record is more useful wrong-and-labelled than right-and-fabricated.

## Disposition — final, recorded 2026-07-20

**R9 is DISPOSED, not open.** The operator's decision is that all three gaps stand exactly as they
are, permanently, and that none of them is repaired. Each has been considered and each has an
outcome. What follows is the disposition, not a plan.

| gap | disposition |
|---|---|
| **G1** | An **unratified, disclosed unauthorized merge.** Not ratified. Not re-derived. Not backfilled. It stands on the record as a merge that occurred without recorded authorization. |
| **G2** | An **unratified `acceptance_claimed` historical violation.** Not ratified. The `accepted` row stays exactly as written, and the verifier continues to decline to honour it. |
| **G3** | A **disclosed post-merge omission.** The `merged` + `accepted` append is not performed, now or later. The contract stays at `merged`. |

**Disposed does not mean fixed, and it does not mean forgiven.** It means the question of what to
do about each gap has been answered, and the answer is *nothing*. These are not pending items, not
follow-up work, and not a backlog. Any future proposal to append a row to G1, G2 or G3, or to edit
their bodies, contradicts this disposition and requires reversing it explicitly first.

**Why leaving them is the correct outcome and not merely the cheap one.** Ratifying G1 after the
fact would grant an authorization that was never sought, at a moment chosen for convenience, for a
change whose merge is long past — which is indistinguishable from the failure the authorization
requirement exists to prevent. Repairing G2 would remove the single clearest live demonstration the
program has that an `accepted` row does not produce an `accepted` state. Completing G3 would be
harmless in isolation, but it would leave one of three gaps closed and two open, implying the
remaining two were judged less real rather than judged and kept.

The verifier already reports each of these correctly and will continue to, at every future run.
That is the enforcement. The record is not improved by making it look better than the history was.

## Correction to a previously published claim

Two PR descriptions (#708, #709) state that the `success_condition` 8 erratum in
`CC-2026-07-20-acceptance-rederivation` landed silently because *"`NC-AC-31` guards `RepoPort`'s
surface but not `MergeFactsPort`'s."*

**That claim is wrong.** `NC-AC-10` does guard `MergeFactsPort`'s surface, and asserts it exactly:
it fails if the public surface is anything other than `["check_runs", "jobs", "pull",
"workflow_runs"]`. The surface was never unguarded.

What was actually missing is narrower and worth stating precisely: **nothing reads the
declaration's prose against the code it describes.** `NC-AC-10` guarded the port and let its own
success message go on saying "two closed reads" while printing four; the contract's
`success_condition` 8 said "exactly `pull` and `check_runs`" while §*expected_surfaces* in the same
file said four. Both sentences were wrong in the same direction, and both were invisible for the
same reason — no control consumed either of them.

The two prose defects are corrected in this change. The frozen declaration is not, and the erratum
remains disclosed in that contract's own `accepted` row.

## Evidence

- `python -m tools.contract state docs/contracts/<id>.md` — the derived states above
- `docs/adr/0105-reusable-change-contract-architecture.md` §4.3a — acceptance is verified, never
  asserted; the `merged` / `accepted` distinction
- `docs/governance/AGENT_CHANGE_SYSTEM_ROADMAP.md` — Phase 3 status and residual **R9**
