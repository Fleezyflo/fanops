---
status: accepted
date: 2026-07-22
supersedes: []
references: [0105]
deciders: [operator]
---

# ADR-0106 — Declaration-Only Change Contracts

> **Scope.** This ADR narrows ADR-0105. It changes **what a change contract is** — the declaration
> alone — and retires the lifecycle append chain, the post-merge publication PR and the acceptance
> ceremony for new work. It changes nothing about **when** a contract is required: the `T1`–`T6`
> trigger model, path-only preflight, the expected-surface-versus-diff check and the requirement that
> a live action carry a separate operator gate all survive verbatim.

## Status

**Accepted** 2026-07-22, by operator directive ("IMPLEMENT PR #715 REMAINING REMEDIATION").

**ADR-0105 is not edited by this ADR, deliberately.** Six landed contracts cite it by blob SHA, and
`derive.authority_state` compares that recorded SHA against the file's blob at the current head. Any
edit — including one that merely added a "superseded by" note — would move the blob and put every one
of those contracts into `AUTH-BLOB-MOVED` → `ST-2`, which is a governance stop on six historical
records that did nothing wrong. A superseding decision is recorded in a new file precisely so it can
be recorded at all.

**What of ADR-0105 remains in force.** Everything except the sections listed below. §1 (triggers),
§1a (the classification path set), §2 (authority), §3.1–§3.2 (the declaration fields), §5
(classification), §6 (identity), §7 (scope and ownership), §8 (evidence reuse), §9 (verification
selection), §10 (stop/refusal/escalation) and §11 (storage) are untouched and still govern.

**What this ADR supersedes, and only for contracts created on or after 2026-07-22:**

| ADR-0105 | superseded by |
|---|---|
| §3.3 — the lifecycle section | §2 below: there is no lifecycle section |
| §3.6 — the append/rewrite asymmetry | §4 below: a landed contract is immutable outright |
| §4.1, §4.1a — three gates, parent-binding | §3 below: two gates, neither commit-bound |
| §4.2 — the eleven event kinds | §2 below: no events |
| §4.3 — the derived state ladder | §3 below: `draft` / `approved`, derived as before |
| §4.3a — acceptance verified against the platform | §5 below: there is no acceptance step |
| §4.4 — the invalidation table | §4 below |

**Contracts created before that date keep ADR-0105 in full.** Their lifecycle sections are still
parsed, their gates still derived, their acceptance still verified against the platform, and the code
that does it is unchanged. `python -m tools.contract state <path>` produces the identical verdict for
each of them before and after this change.

## Context

The contract system was built to answer one question — *was this change authorized, and did it stay
inside what was authorized?* By the time Phase 3 closed it was also answering four questions nobody
had asked it to: what happened to the change, when, on which commit, and with which platform run ids.

The cost of the second set was not theoretical. A single ordinary governance change had come to
require:

1. a contract with a `created` row naming a base SHA that is stale the moment `main` advances;
2. an `approved` row, a `binding` row, an `implementation_started` row and a `head_proposed` row;
3. a `merge_approved` row naming the exact parent commit — which, because a lifecycle append moves
   the head, had to be re-issued whenever anything else was appended;
4. a **second pull request after the merge**, whose entire content was appending `merged` and
   `accepted` rows carrying the squash SHA, the platform `mergedAt`, and the numeric ids of the
   check runs that had already passed;
5. and a verifier that re-read all of it against GitHub to decide whether the ceremony had been
   performed correctly.

Every one of those artifacts is a **copy of a fact the platform already holds authoritatively**. The
merge SHA is in the git history. The merge timestamp is in the PR. The check runs are in the checks
API. A copy in a tracked file is strictly worse than the original: it can drift, it cannot be
re-derived, and it must itself be verified — which is what §4.3a's several hundred lines of
platform-verification exist to do. The system was spending most of its complexity verifying its own
bookkeeping.

The failure mode this produced is on the record. `CC-2026-07-21-preflight-classification` derives
`acceptance_claimed` today, not because anything about that change was wrong, but because its
`merge_approved` row names PR #712 while the publication PR that carried its acceptance was #713 —
and a second `merge_approved` could not be added, because the implementation reads only the last one
and a second would have REPLACED the first. Three of the four Phase 3 contracts carry a lifecycle gap
of some kind (`PHASE3_LIFECYCLE_DISCLOSURE.md`), all three disposed unrepaired. A record-keeping
discipline that its own authors could not keep correct four times running is not a discipline; it is
a tax.

## Decision

### 1 · A change contract is its declaration, and nothing else

A contract created on or after 2026-07-22 is a single file under `docs/contracts/` containing the
declaration fields of ADR-0105 §3.1 and **no `## Lifecycle` section**. It records what was authorized.
It does not record what happened.

The rule for what goes in the file is now stateable in one line: **a contract holds facts that were
true when it was written and stay true; everything else is looked up where it lives.**

Consequently a new contract contains **no** GitHub run ids, no check-run ids, no timestamps, no merge
SHAs, no base SHAs, no branch names and no PR numbers. Those are platform state. `git log`, `gh pr
view` and the checks API are their sources of truth, they are always current there, and they are
never current in a tracked file.

### 2 · The two operator acts are front-matter fields

| field | records | required |
|---|---|---|
| `approved_digest` | the declaration digest `D` the operator approved | to pass `ST-3` |
| `approval_token` | the operator's approval words, verbatim | with `approved_digest`, always |
| `execution_gate` | the operator's separate authorization to perform a live action | when `live` is in `traits` |

Both approval fields are written **once**, by the agent, after the operator has answered — exactly as
the `approved` event they replace was ("`approved` | operator, recorded by agent", §4.2). The audit is
unchanged: the token appears in the diff, and `approved_digest` names a digest the operator computed
independently.

`execution_gate` is a separate field, not a value inside `approval_token`, because ADR-0105 §1 `T4`
requires a live action to carry a gate **separate** from the approval of the change. One field
carrying both would make approving a change approve running it.

### 3 · Two gates, and neither binds to a commit

| gate | binds to | rule that reads it |
|---|---|---|
| **Content approval** | the declaration digest `D` | `ST-3` |
| **Execution gate** | the declared live action | `RF-1` |

**Merge authorization is gone as a gate**, and with it the whole parent-binding apparatus. ADR-0105
§4.1a exists because a record written into a tree cannot name the commit that contains it; the
correction — bind to the parent and prove the delta is lifecycle-only — is sound, and it is now
unnecessary, because there is no in-tree record that needs to name a commit. What authorized the
merge is what always actually authorized it: an approved declaration, a diff that stayed inside it,
and green required CI on the head being merged. The first two are `ST-3` and `ST-1`; the third is
branch protection.

The derived state ladder for a declaration-only contract is therefore short, and still **derived,
never declared**: `refused` if a terminal condition is declared, `approved` if `approved_digest` names
the current `D`, `draft` otherwise.

### 4 · Invalidation, and the immutability of a landed contract

| event | content approval |
|---|---|
| any declaration byte changes | **VOID.** `D` moves. Re-approve. |
| an approval field is written or rewritten | **survives** — the three approval lines are outside `D` by construction |
| the head moves | survives — nothing binds to a commit |
| the base moves | survives |
| a cited authority's blob changes | **FLAG — re-confirm** (unchanged from §4.4) |

ADR-0105 §3.6 drew an asymmetry between appending to a contract (routine) and rewriting one
(governance-sensitive). With nothing to append, the asymmetry collapses into a simpler rule:
**a contract that has landed on `main` is immutable.** Any byte that moves afterwards is a
declaration edit, `DECL-DIVERGED` reports it, and the remedy is a new contract with `supersedes:`.

### 5 · There is no acceptance step, and no publication PR

A merged change is a merged change. Nothing is appended afterwards, no second pull request is opened,
and no `accepted` row is written — so nothing needs to be verified against the platform to decide
whether that row was earned.

**This deletes a real check, and the deletion is the point.** §4.3a's acceptance verification is
careful, correct work: it proves the merge SHA, the `mergedAt` chronology, the check-run provenance
down to the producing App identity, and the workflow-blob stability between base and head. What it
proves is that *the acceptance row was written honestly*. It is a verification of the bookkeeping,
not of the change — and with no row to write, the honest answer is that the check has nothing left to
check. Whether the change was correct is what CI, review and the success condition answer, before the
merge, where an answer can still change the outcome.

**A `live` change is not affected.** `RF-1` refuses without `execution_gate`, before the action, which
is where a gate on an irreversible act belongs. Acceptance never gated a live action; it recorded one
after the fact.

### 6 · The lifecycle tooling is retained, and is now historical-only

`tools/contract/lifecycle.py`, the eleven event kinds, `parent_binds`, `_rederive_post_merge` and the
acceptance verifier are **kept, wired and covered**. They are what reads the six contracts written
under ADR-0105, and those contracts are permanent records. Deleting the reader would make them
unreadable, which is the one thing a governance record must never become.

They apply to a contract that carries a `## Lifecycle` section, and to nothing else. The selector is
`parse.split`'s boundary count — a fact about the bytes, not a mode, a flag or a date comparison. A
contract cannot be read both ways, and `APPROVAL-DUAL-ROUTE` refuses one that tries to record its
approval in both places at once.

### 7 · What `D` covers, exactly

`parse.digest_range` is the single definition, and it selects on the same boundary count:

```python
def digest_range(raw: bytes) -> bytes:
    decl, _, n = split(raw)
    return decl if n else _APPROVAL_LINES.sub(b"", raw)
```

**Lifecycle-bearing** — ADR-0105 §3's reference implementation, byte-for-byte. This is what keeps
every landed approval naming its own declaration.

**Declaration-only** — the whole file, with the three approval lines elided. The elision is what lets
the approval live inside the artifact it approves: recording it leaves `D` **byte-identical**, so the
digest the operator named is still the digest the file computes afterwards. ADR-0105 §Status already
relies on exactly this property for an ADR's own `approved_digest`; it gets it by excluding the whole
front matter, which is not available here because a contract's front matter carries load-bearing
declaration fields (`traits`, `authorized_actions`, `blast_radius`) that an approval must cover.

Eliding all three fields rather than only `approved_digest` is not a hole. They are the **record** of
the two operator acts, never part of what was authorized; everything an approval is about is inside
`D`. Leaving the token inside would make writing it change `D` and void the approval in the act of
recording it — the unsatisfiable-gate shape ADR-0105 §4.1a already had to correct once.

## Consequences

- A governed change is one pull request. It was two.
- A contract stops rotting. It contains nothing that a later commit can falsify.
- **Acceptance verification against the platform no longer runs for new work.** Stated plainly
  because it is the largest thing given up. §5 above is the argument; the code remains for the
  contracts it was written for.
- The declaration field set grows from 19 slots to 22. It is still closed, and the three additions are
  the approval record rather than new authority.
- `python -m tools.contract template` emits the declaration-only skeleton, so the default an agent
  copies is the current shape rather than the retired one.

## Risks

- **An agent writes `approved_digest` before the operator has answered.** Unchanged in kind from the
  `approved` event, which an agent also wrote. The mitigation is the same: the value is a digest the
  operator computes independently, and the token is words only the operator supplies. *Not* mitigated
  by the file's structure, and never was.
- **A landed contract is edited and nobody notices.** `DECL-DIVERGED` fires on any byte, and it is
  strictly stronger here than under the lifecycle model, where only the pre-boundary range was
  compared.
- **The two shapes drift.** Both are exercised by committed fixtures
  (`tests/fixtures/contracts/valid_full.md`, `valid_declaration_only.md`), and
  `NC-D8` recomputes every landed contract's digest against the approval it records.

## Verification contract

- `python -m tools.contract selftest` — every rule carries a firing negative control, `NC-D1`–`NC-D9`
  covering the declaration-only shape.
- `NC-D8` — every landed contract still computes the digest its `approved` event names.
- `python -m tools.contract state <path>` — byte-identical verdicts for all seven contracts on
  `main`, before and after.
- `tests/test_contract_compiler.py` — the digest-stability property, the field-set count, and the two
  committed fixtures.

## Enforcement mechanism

`tools/contract` (the verifier) and the `unit (fast, no toolchain)` required context that runs its
tests. No new workflow, no new required check, no branch-protection change.

## Rollback plan

Revert the merge commit. The lifecycle code is untouched by this change, so reverting restores the
previous model without a migration; contracts written in the declaration-only shape in the interim
would then need a `## Lifecycle` section added, which is an append and voids nothing.

## Affected workflows and controls

None. `tools/contract` is not wired into any workflow as a gate; it is run by hand and by
`tests/test_contract_compiler.py` in the unit lane.
