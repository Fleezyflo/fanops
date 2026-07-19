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
| 4 | accepted | an `accepted` event is present AND `merge_authorization == verified` AND `acceptance_verified` |
| 5 | acceptance_claimed | an `accepted` event is present AND (`merge_authorization != verified` OR NOT `acceptance_verified`) |
| 6 | merged | the merge commit is on `main` AND `merge_authorization == verified` |
| 7 | merged_unverified | the merge commit is on `main` AND `merge_authorization` is one of `claimed_stale`, `claimed_unknown`, `claimed_inadmissible`, `fidelity_failed` |
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

### the post-merge states, distinguished

`merge_authorization` takes exactly one of six values: `verified`, `claimed_stale`,
`claimed_unknown`, `claimed_inadmissible`, `fidelity_failed`, `absent`.

| condition | `merge_authorization` | derived state |
|---|---|---|
| an authorization route is successfully rederived after merge | `verified` | `merged`, or `accepted` if an `accepted` event is present and acceptance verifies |
| repository or review evidence is unavailable (the read did not complete) | not computable; never `verified` | `merged_unverified`, marked unverifiable — and the DECISION independently stops at `ST-7` |
| evidence is readable and proves no qualifying authorization ever existed | `absent` | `merged_unauthorized` |
| evidence is readable and a claim exists but does not qualify | `claimed_stale`, `claimed_unknown`, `claimed_inadmissible` or `fidelity_failed` | `merged_unverified` |
| an `accepted` event exists without verified merge authorization | any value except `verified` | `acceptance_claimed` |
| acceptance evidence is missing, malformed or stale but readable | `acceptance_verified` is false | `acceptance_claimed` |
| acceptance evidence is unverifiable because a read did not complete | `acceptance_verified` is false | `acceptance_claimed`, and the DECISION stops at `ST-7` |

**State derivation and the decision are separate, and the amendment must say so in those words.**
When a required input is unavailable, state derivation still runs and still yields a merged-family
state — it simply can never yield `merged` or `accepted`, because neither is reachable without a
`verified` authorization. The decision independently stops at `ST-7`. The report must therefore carry
both the derived state and the unverifiable marker, so that a state reached under an incomplete read
is never mistaken for a readable negative. A state is a description; `ST-7` is a refusal to proceed;
neither substitutes for the other.

**The `ST-7` rule is preserved unchanged: any required input whose read did not complete reaches
`ST-7`.** A readable negative — a proven-absent authorization, a tree mismatch, a failing or absent
required check — is a finding and must never be routed to `ST-7`.

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
7. The amended text names every control identifier in "implementation controls" below, so the
   implementation contract inherits a fixed inventory rather than a description.
8. `python -m tools.arch ci` and `python -m tools.ci static` exit 0.
9. `git diff --name-only <base>...<head>` equals `expected_surfaces` exactly.

### implementation controls

The later implementation contract must build every control below. **This contract builds none of
them**, and the amendment's only obligation is to name their identifiers so the inventory is fixed
before implementation begins.

| id | injected defect or missing evidence | expected derived state | expected rule and outcome | distinct boundary proven |
|---|---|---|---|---|
| NC-SM-01 | an `accepted` event with no qualifying authorization of either route | acceptance_claimed | stop, the acceptance rule | a written row cannot promote to `accepted` |
| NC-SM-02 | merge with a verified route present and no `accepted` event | merged | continue at `OK` | verification alone does not manufacture acceptance |
| NC-SM-03 | a readable record proving no qualifying authorization ever existed | merged_unauthorized | stop, the unauthorized-merge rule | a readable negative is a finding, never `ST-7` |
| NC-SM-04 | every authorization read raises, so no read completes | merged_unverified, marked unverifiable | stop at `ST-7` | unavailability reaches the fail-closed rule and does not masquerade as a finding |
| NC-SM-05 | an authorization bound to a head that is not the final pre-merge head | merged_unverified | stop, the stale-authorization rule | binding is to the final head, not to any head |
| NC-SM-06 | a post-merge lifecycle row appended in an attempt to create authorization | unchanged from before the append | stop, unchanged | rederivation and appends verify; neither originates |
| NC-SM-07 | a witnessed review whose `commit_id` is not the final pre-merge head | merged_unverified | stop, the stale-authorization rule | the witnessed route binds the final head |
| NC-SM-08 | an unwitnessed claim bound to anything other than the final pre-merge head | merged_unverified | stop, the stale-authorization rule | the unwitnessed route binds the final head |
| NC-SM-09 | a terminal event present alongside a fully verified acceptance | the terminal event | stop, the terminal rule | terminal precedence is unconditional and outranks rank 4 |
| NC-SM-10 | inputs satisfying two ladder ranks at once | the higher rank only | the higher rank's outcome | first-match-wins is ordering, not a search for the best fit |
| NC-SM-11 | the implemented state set enumerated and compared to the declared count | not applicable | fail if the counts differ | the declared number and the implemented set cannot drift |
| NC-SM-12 | acceptance evidence readable but missing, malformed or stale | acceptance_claimed | stop, the acceptance rule | acceptance verification is separate from merge authorization |
| NC-SM-13 | a merged-family state derived while a required read did not complete | merged_unverified, marked unverifiable | stop at `ST-7` | derivation and decision are separate and both are reported |

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
| tests/** | NC-SM-01 through NC-SM-13 are implemented under a later contract, never here |
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
| the control identifiers NC-SM-01..NC-SM-13 | the amended §4.3 text | an inventory that lives only in a contract body is an inventory the implementation can silently shorten |

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
