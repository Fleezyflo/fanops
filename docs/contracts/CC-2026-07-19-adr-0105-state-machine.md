---
id: CC-2026-07-19-adr-0105-state-machine
traits: [governance]
authorized_actions: [design]
incidental_allowlist: []
blast_radius: []
invariants: [LAW-SOT-01, LAW-DOC-01, C2.1, C18.1]
stop_conditions: ["T6: the operator required a contract for this phase"]
supersedes: []
---

# CC-2026-07-19-adr-0105-state-machine

Amends ADR-0105 §4.1a, §4.2 and §4.3 so a lifecycle row can no longer promote a record to a state
that asserts an irreversible act was authorized. It amends the MODEL and implements NOTHING.

**This contract is self-contained by construction.** Every state, predicate, precedence rule and
negative control the amendment must produce is written out below. A reviewer holding only this
repository and this file can determine the exact required ADR text without any other source.

**This contract cannot be text-only, and that is a finding rather than a convenience.** `NC-C27`
recomputes the ADR body digest and compares it to the pin in `tools/contract/classify.py`. Editing
the body moves the digest, so the pin must move in the same change or CI reddens. The pin is ONE
line; any other edit to that file is a scope violation, not a judgement call.

**`authorized_actions` is `[design]` and stops there.** Implementation, merge and acceptance are
separate grants obtained at their own gates.

### objective

Amend ADR-0105 §4.3 from the eleven states `tools/contract/lifecycle.py::state()` returns today to
the fourteen enumerated below; amend §4.1a to state that both authorization routes bind the FINAL
pre-merge PR head and that post-merge rederivation verifies a route rather than originating one;
amend §4.2 to carry the values those states consume. Add no rule to any tool. Change no behaviour.
Implement no control.

**The count is fourteen, and no current state is removed.** Today's ladder returns eleven:
`refused`, `superseded`, `abandoned`, `accepted`, `merged`, `approved_for_merge`, `implemented`,
`in_implementation`, `approved`, `in_review`, `draft`. This amendment adds exactly three —
`acceptance_claimed`, `merged_unverified`, `merged_unauthorized` — so 11 + 3 = 14. `merged` and
`accepted` are NARROWED by predicate, not split into replacements: each keeps its name and its rank
and gains a stricter predicate, with the weaker cases falling to the new states beneath it. Nothing
is consolidated and nothing is retired, because every current state still describes a reachable
condition that no other state describes.

### the target state ladder

Ordered, **first match wins**, evaluated top to bottom. Rank is precedence: a lower rank is reached
only when every rank above it fails. Fourteen states, matching the count stated above.

| # | state | complete predicate |
|---|---|---|
| 1 | refused | a `refused` event is present |
| 2 | superseded | a `superseded` event is present |
| 3 | abandoned | an `abandoned` event is present |
| 4 | accepted | an `accepted` event is present AND the governed merge commit exists on `main` AND `accepted.merge_sha` equals that actual governed merge commit AND `merge_authorization == verified` AND `acceptance_verified` |
| 5 | acceptance_claimed | an `accepted` event is present AND rank 4 does not hold — the merge is not on `main`, or `accepted.merge_sha` names a commit that is not the governed merge, or `merge_authorization != verified`, or NOT `acceptance_verified` |
| 6 | merged | the merge commit is on `main` AND `merge_authorization == verified` |
| 7 | merged_unverified | the merge commit is on `main` AND `merge_authorization` is one of `claimed_stale`, `claimed_unknown`, `claimed_inadmissible`, `fidelity_failed`, `unavailable` |
| 8 | merged_unauthorized | the merge commit is on `main` AND `merge_authorization == absent` |
| 9 | approved_for_merge | `merge_authorization == verified` at the current head |
| 10 | implemented | a `head_proposed` event binds the current head AND CI is green |
| 11 | in_implementation | an `approved` event names the current `D` AND an `implementation_started` event is present |
| 12 | approved | an `approved` event names the current `D` |
| 13 | in_review | a PR is open AND all mandatory fields for the declared traits are present |
| 14 | draft | otherwise |

**Precedence rules the amendment must state explicitly.**

Ranks 1–3 are unconditional and are evaluated before any gate is consulted. A terminal event
outranks every other condition, including a verified acceptance, because terminal events are
self-limiting: they only ever reduce what a contract permits, so honouring a written one can never
grant anything.

Ranks 4–5 precede 6–8 because an `accepted` event is a claim about the whole change and must be
reported as such even when the merge beneath it is unverified. Collapsing rank 5 into rank 7 would
erase the fact that acceptance was asserted.

Ranks 6–8 partition the single condition "the merge commit is on `main`" by authorization status.
That condition is derived from the platform and from `main` ancestry, never from a lifecycle row, so
a contract that never records a `merged` event still reaches the correct rank.

### the authorization value set, closed and total

`merge_authorization` takes **exactly one of seven** values. The seventh, `unavailable`, exists
because an incomplete read is not a readable negative and must never be hidden inside one.

| value | exact predicate | required reads | all reads completed | pre-merge state | post-merge state | rule | outcome |
|---|---|---|---|---|---|---|---|
| verified | a qualifying route binds the FINAL pre-merge PR head, and post-merge the fidelity conjuncts hold | PR head ref + API head; reviews; `merge_approved` blob at PR head; admissibility census; trees | yes | approved_for_merge | merged | OK | continue |
| claimed_stale | a claim exists but binds a head other than the final pre-merge head, or `parent_binds` fails there | as above | yes | lower ladder | merged_unverified | ST-10 | stop |
| claimed_unknown | a claim binds correctly, but no record establishes the effective-writer census AT the authorization instant. A permanent property of the fact, not a read failure | as above, all succeeding | yes | lower ladder | merged_unverified | ST-10 | stop |
| claimed_inadmissible | reads prove two or more effective ref writers existed at the authorization instant | as above, all succeeding | yes | lower ladder | merged_unverified | ST-10 | stop |
| fidelity_failed | PR-head tree does not equal the merged-commit tree | trees at both commits | yes | n/a | merged_unverified | ST-10 | stop |
| absent | every required read completed and no qualifying claim of either route exists | as above, all succeeding | yes | lower ladder | merged_unauthorized | ST-9 | stop |
| unavailable | at least one required read did NOT complete | whichever read failed | **no** | lower ladder | merged_unverified | ST-7 | stop |

**Exhaustiveness proof, which the amendment must carry.** The seven values partition into three
disjoint groups: `{verified}` → rank 6; `{claimed_stale, claimed_unknown, claimed_inadmissible,
fidelity_failed, unavailable}` → rank 7; `{absent}` → rank 8. The groups are disjoint and their union
is the whole set, so once "the governed merge commit is on `main`" holds, exactly one of ranks 6, 7
and 8 matches. **No value can fall through to `approved_for_merge`, `implemented`, or any lower
rank after the merge is known to exist**, because rank 8's predicate is the negation, within the
closed set, of ranks 6 and 7 taken together. `NC-SM-14` and `NC-SM-17` hold this at implementation.

**Readable negatives and unavailable evidence stay distinct.** `absent` means the reads succeeded
and proved nothing qualifying exists — a finding, reported through `ST-9`, and `ST-7` must NOT fire.
`unavailable` means a read did not complete — reported through `ST-7`, and it must never be recorded
as `absent`, which would convert an outage into a proven governance failure.

**State derivation and the decision are separate, and the amendment must say so in those words.**
Under `unavailable`, derivation still runs and still yields `merged_unverified`; it can never yield
`merged` or `accepted`, since neither is reachable without `verified`. The decision independently
stops at `ST-7`. The report carries both the derived state and the unavailable classification, so a
state reached under an incomplete read is never mistaken for a readable negative.

### `acceptance_verified`, defined mechanically

`acceptance_verified` is TRUE if and only if **all** of A–F hold. Nothing here consults free-form
prose, and no row can satisfy this gate by existing.

**A — required lifecycle values present and well formed.** An `accepted` event carrying
`merge_sha` (40 hex), `decision`, `date`, `operator`; and a `merged` event carrying `merge_sha`.
A row violating this grammar is rejected during **lifecycle validation** as `ACCEPT-INCOMPLETE`,
reaching rule `A5` — it never reaches state derivation at all. A row that parses but whose values
disagree with reality is NOT a validation error; it is represented as an unverified claim
(`acceptance_claimed`). Grammar failures are parse-time; semantic mismatches are state-time.

**B — the actual governed merge SHA is established from the platform, never from a row.** It is
`pullRequest.mergeCommit.oid`, confirmed an ancestor of `main`. Immutable identifier: the commit SHA.

**C — SHA agreement.** `accepted.merge_sha` and `merged.merge_sha` both equal the value from B.

**D — merge chronology.** The `merged` row timestamp equals `pullRequest.mergedAt` exactly. Equality,
not an upper bound. Immutable identifier: the server-stamped instant.

**E — success-condition evidence, machine-verified.** For every context in
`intended_required_contexts` read from `.github/ci-control-registry.yml` **at `created.base_sha`**,
plus every context named by a `verification` obligation: a qualifying check-run exists at
`head_sha == <B>` with `conclusion == success`. A run qualifies only when its pinned identity tuple
matches — app id, workflow id, workflow file path, job name — and only the greatest check-run id per
name is considered. Immutable identifiers: check-run id, workflow id, and the workflow-file blob SHA
at `created.base_sha`. `failure`, `skipped`, `cancelled` and absent all fail E as **readable**
negatives.

**F — the evidence set is pinned.** The `accepted` row records the check-run **id set** it was
verified against. After acceptance, only those ids are verified; a later rerun cannot revise an
earlier verdict, and recognising one requires appending a new `accepted` row.

**The `evidence=` free-text field is descriptive only and carries no verification weight.** The
amendment must state that in those words. A–F are the whole gate.

**Readable failures produce `acceptance_claimed`:** any of C, D, E or F failing on completed reads.
**Unavailable reads additionally fire `ST-7`:** the PR record, the check-runs list, or the registry
blob at `created.base_sha` failing to read.

### the witnessed route, stated exactly

The amendment must state all four of the following, in the ADR text:

1. The verifier's qualifying predicate is `state == APPROVED` AND
   `commit_id == final_pre_merge_head`. There is no third conjunct.
2. Reviewer identity is **not an input available to the verifier**. `ReviewPort.approvals` returns
   `(commit_id, state)` pairs and never reads a login, so no identity or permission predicate can be
   evaluated even in principle.
3. **This amendment adds no reviewer-identity and no permission predicate.** It does not raise the
   bar for a witnessed review and does not require the reviewer to hold any repository permission.
4. Any non-author property of the actual GitHub review is **platform-produced evidence**, not a
   verifier rule. GitHub refuses to let a PR author approve their own pull request; that refusal is
   where author-distinctness comes from. Describing it as a verifier check would misstate what the
   code does, and the ADR must not.

### success_condition

Each is independently checkable using only this repository and this contract:

1. `python -m tools.contract selftest` exits 0 with every control DETECTED, `NC-C27` included,
   proving the recomputed ADR body digest equals both the new pin and the new front-matter value.
2. The amended ADR-0105 §4.3 contains a first-match-wins ladder of **exactly fourteen states**, whose
   names, order and predicates are byte-equivalent in meaning to the table in "the target state
   ladder" above, and whose stated count is the literal number fourteen.
3. The amended §4.3 states the three precedence rules given above: terminal events unconditional and
   first; `accepted`/`acceptance_claimed` above the merged family; the merged family partitioned by
   authorization status over a platform-derived "on main" condition.
4. The amended §4.3 states that state derivation and the decision are separate, that an unavailable
   read still yields a merged-family state, and that `ST-7` fires independently.
5. The amended §4.1a contains all four witnessed-route statements from "the witnessed route, stated
   exactly" above, including the explicit statement that no identity or permission predicate is added.
6. The amended §4.1a states that both routes bind the final pre-merge PR head, and that post-merge
   rederivation verifies an existing authorization and never originates one.
7. The amended text names every control identifier in "implementation controls" below — NC-SM-01
   through NC-SM-19 — so the implementation contract inherits a fixed inventory, not a description.
8. The amended §4.2 defines `merge_authorization` as the CLOSED seven-value set of "the authorization
   value set, closed and total" above, including `unavailable` as a distinct value, and carries the
   exhaustiveness proof that every merge on `main` reaches exactly one of ranks 6, 7 and 8.
9. The amended §4.3 defines `acceptance_verified` as conditions A through F of "`acceptance_verified`,
   defined mechanically" above, and states in those words that the `evidence=` free-text field is
   descriptive only and carries no verification weight.
10. The amended text names the three new decision rules the implementation must introduce — `ST-9`,
    `ST-10`, `ST-11` — and authorizes no other new rule identifier.
11. `python -m tools.arch ci` and `python -m tools.ci static` exit 0.
12. `git diff --name-only <base>...<head>` equals `expected_surfaces` exactly.

### implementation controls

The later implementation contract must build every control below. **This contract builds none of
them.** Every expected rule is named by exact identifier: `A5`, `OK`, `RF-3` and `ST-7` exist today;
`ST-9`, `ST-10` and `ST-11` are the three the implementation must INTRODUCE, and no other new rule id
is authorized. `ST-8` is the highest rule presently defined, so the new ids extend the sequence
without colliding.

**Nineteen controls.** The previous inventory of thirteen was written before the authorization set
was closed and before `accepted` required the merge to exist; six controls were added to cover those
corrections. The count is nineteen because the list below has nineteen rows, not because thirteen or
any other number was carried forward.

| id | injected defect or missing evidence | expected derived state | expected rule | outcome | distinct boundary proven |
|---|---|---|---|---|---|
| NC-SM-01 | an `accepted` event with `merge_authorization != verified` | acceptance_claimed | ST-11 | stop | a written row cannot promote to `accepted` |
| NC-SM-02 | merge on `main`, a verified route, and no `accepted` event | merged | OK | continue | verification alone does not manufacture acceptance |
| NC-SM-03 | every read completes and proves no qualifying claim exists | merged_unauthorized | ST-9 | stop | a readable negative reaches ST-9 and `ST-7` must NOT fire |
| NC-SM-04 | a required authorization read raises | merged_unverified | ST-7 | stop | `unavailable` is its own value and reaches the fail-closed rule |
| NC-SM-05 | a Route U claim binding a head other than the final pre-merge head | merged_unverified | ST-10 | stop | binding is to the FINAL head, not any head |
| NC-SM-06 | a post-merge `merge_approved` append onto a `merged_unauthorized` record | merged_unauthorized, unchanged | ST-9 | stop | an append verifies nothing and originates nothing |
| NC-SM-07 | a witnessed review whose `commit_id` is not the final pre-merge head | merged_unverified | ST-10 | stop | the witnessed route binds the final head |
| NC-SM-08 | PR-head tree not equal to the merged-commit tree | merged_unverified | ST-10 | stop | `fidelity_failed` is a readable negative, never `ST-7` |
| NC-SM-09 | a `refused` event alongside a fully verified acceptance | refused | RF-3 | refuse | terminal precedence is unconditional and outranks rank 4 |
| NC-SM-10 | inputs satisfying two ladder ranks at once | the higher rank only | that rank's rule | that rank's outcome | first-match-wins is ordering, not a search for the best fit |
| NC-SM-11 | the implemented state set enumerated and compared to the declared count fourteen | not applicable | fail the control | fail | the declared number and the implemented set cannot drift |
| NC-SM-12 | a required check-run at the merge SHA is `failure`, `skipped`, `cancelled` or absent | acceptance_claimed | ST-11 | stop | acceptance verification is separate from merge authorization |
| NC-SM-13 | a required read does not complete while the governed merge is on `main` | merged_unverified | ST-7 | stop | derivation and decision are separate and both are reported |
| NC-SM-14 | each of the seven authorization values driven in turn with the merge on `main` | exactly one of merged, merged_unverified, merged_unauthorized per value | OK, ST-10, ST-10, ST-10, ST-10, ST-9, ST-7 respectively | continue for the first, stop for the rest | the value set is total and the post-merge partition is exhaustive and mutually exclusive |
| NC-SM-15 | an `accepted` event while the governed merge is NOT on `main` | acceptance_claimed | ST-11 | stop | acceptance cannot precede the merge it accepts |
| NC-SM-16 | `accepted.merge_sha` naming a commit that is not the governed merge | acceptance_claimed | ST-11 | stop | the row is checked against the platform, never trusted |
| NC-SM-17 | the governed merge on `main` under every authorization value | never a rank below 8 | never a rank-9-or-lower rule | stop or continue per NC-SM-14 | no fall-through to `approved_for_merge`, `implemented` or lower |
| NC-SM-18 | reads all complete but no record establishes the census at the authorization instant | merged_unverified | ST-10 | stop | `claimed_unknown` is a readable finding and is NOT `unavailable` |
| NC-SM-19 | an `accepted` row whose `evidence=` text asserts success while the pinned check-run set does not | acceptance_claimed | ST-11 | stop | free-form evidence text cannot verify itself |

### rollback

`git revert` of the single squash commit restores the previous body, the previous pin and the
previous front-matter digest together, leaving no dangling approval. No tool behaviour changes in
either direction, because this contract changes no logic, so the cost of reverting is the cost of
re-reading one document.

This contract is RETAINED after any terminal event, per ADR-0105 §11.2.

### authority

| id | source_file | blob_sha |
|---|---|---|
| ADR-0105 | docs/adr/0105-reusable-change-contract-architecture.md | f9fa602b501f80418d8a66eb9c6389a99ae64c8a |
| C2.1 | docs/REPOSITORY_CONSTITUTION.md | 1f42a8ea298af39fffd56e3ce5c3542cef512df2 |
| C18.1 | docs/REPOSITORY_CONSTITUTION.md | 1f42a8ea298af39fffd56e3ce5c3542cef512df2 |
| LAW-SOT-01 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |
| LAW-DOC-01 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |

The ADR blob named here is the PRE-amendment body, which is the authority this change acts upon. The
POST-amendment digest is recorded in the lifecycle `approved` row as `adr_0105_renewed`.

### owners

| subsystem_id | why_touched |
|---|---|
| S01_foundation | no `src/fanops/` module changes; ownership is DECLARED for the documentation and pin paths, never inferred (ADR-0105 §7) |

### allowed_scope

| glob | why | basis |
|---|---|---|
| docs/adr/0105-reusable-change-contract-architecture.md | the amendment and its own front-matter `approved_digest` | declared |
| tools/contract/classify.py | the `ADR_0105_DIGEST` pin ONLY, which `NC-C27` compares against | declared |
| docs/contracts/CC-2026-07-19-adr-0105-state-machine.md | this contract | declared |

### prohibited_scope

| glob | why |
|---|---|
| tools/contract/lifecycle.py | the state model is AMENDED here and IMPLEMENTED under a later contract |
| tools/contract/decide.py | no rule added, reordered or reworded |
| tools/contract/model.py | no type change; the fourteen states are text until implemented |
| tools/contract/adapters.py | no port added; the census and check-run joins are later contracts |
| tools/contract/__main__.py | the dependency fail-closed repair belongs to the implementation contract |
| tools/contract/selftest.py | no control added; this contract NAMES the inventory and does not build it |
| tests/** | NC-SM-01 through NC-SM-19 are implemented under a later contract, never here |
| .github/ci-control-registry.yml | the registry amendment is a SEPARATE contract |
| .github/workflows/** | no workflow change; enforcement remains Phase 6 |
| src/fanops/** | no runtime change |
| docs/contracts/CC-2026-07-18-change-contract-compiler.md | an accepted contract is not a mutable work log |
| docs/contracts/CC-2026-07-19-cli-lifecycle-integrity.md | a landed contract is not a mutable work log |
| docs/governance/** | the roadmap is not advanced, reordered or amended by this contract |

### expected_surfaces

| path | kind | why |
|---|---|---|
| docs/adr/0105-reusable-change-contract-architecture.md | MODIFIED | §4.1a, §4.2 and §4.3 amended; front-matter `approved_digest` recomputed |
| tools/contract/classify.py | MODIFIED | one line, the `ADR_0105_DIGEST` pin |
| docs/contracts/CC-2026-07-19-adr-0105-state-machine.md | NEW | this contract |

### coupling

| what | must_move_with | why |
|---|---|---|
| the ADR-0105 body | the `ADR_0105_DIGEST` pin AND the ADR front-matter `approved_digest` | `NC-C27` recomputes the body digest and compares it to both. A body edit without both is red CI; a pin edit without the body is a lie about what was approved |
| the stated state count of fourteen | the enumerated ladder in the amended §4.3 | a number in prose that no reader can recount against a list is the defect this revision exists to remove. `NC-SM-11` is the control that keeps them equal |
| the control identifiers NC-SM-01..NC-SM-19 | the amended §4.3 text | an inventory that lives only in a contract body is an inventory the implementation can silently shorten |

### reusable_evidence

| claim | proven_by | proven_at | binding |
|---|---|---|---|
| the witnessed route qualifies on `state == APPROVED` and `commit_id == head_sha` alone, with no permission or identity predicate | read of the exact-head branch in `gates` | f558f08d817b66bfc5afe046ff133edd0d5b9dc7 | tool:route-w-feasibility |
| `ReviewPort.approvals` returns `(commit_id, state)` pairs only and never reads a login, so the verifier cannot evaluate reviewer identity | read of `ReviewPort.approvals` | 69e1630ac4dccd0a9e2e8d4b594ed599c5582fa5 | tool:route-w-feasibility |
| `Derived` is constructed before the review and principal reads, so their failures cannot reach the field the fail-closed rule consults | read of the `run` pipeline stage order | edc5da272dda94a3b84025e5a6bdf566df5ce53e | tool:route-w-feasibility |
| `ST-7` predicates on `derived.unverifiable` alone, so a dependency diagnostic appended later decides nothing | read of the `ST-7` rule | 0a9574b0b6792d9103485f96b564642fa7bcd483 | tool:route-w-feasibility |
| `state()` returns exactly eleven states today, so adding three yields fourteen | read of the ladder and of `TERMINAL_EVENTS` | f558f08d817b66bfc5afe046ff133edd0d5b9dc7 | tool:route-w-feasibility |

Each row binds to the blob of the file its claim was read from, at the base commit of this change.
Citing the ADR blob for a claim about code would bind evidence to a document that cannot prove it.

### verification

| obligation_id | control_or_requirement | distinct_boundary |
|---|---|---|
| OB-NC-C27 | python -m tools.contract selftest, NC-C27 DETECTED | proves the pin, the front matter and the live body agree, which is the ONE control this contract moves |
| OB-ARCH-CI | python -m tools.arch ci | regeneration byte-compare plus the policy rule set |
| OB-CI-STATIC | python -m tools.ci static | registry-versus-workflow reconciliation |
| OB-C18 | the amendment disclosed, its digest recomputed, and renewed operator approval obtained | proves the AUTHORITY changed with consent rather than silently |
| OB-LADDER | the amended §4.3 enumerates fourteen states and states the number fourteen | proves the declared count and the enumerated list agree at approval time, which NC-SM-11 then holds at implementation time |
| OB-ROUTE-W | an APPROVED review whose commit_id equals the FINAL pre-merge head, on a PR the reviewer did not author | the only merge evidence available while the unwitnessed route is frozen. The verifier checks state and commit_id ONLY; the non-author property is produced by GitHub refusing author self-approval, and is recorded here as platform evidence rather than as a verifier predicate |

## Lifecycle

| timestamp | event | values |
|---|---|---|
| 2026-07-19T14:34:05Z | created | id=CC-2026-07-19-adr-0105-state-machine; base_sha=35cbf7fcebdd9e2b5f657a971af6c31140879123 |
