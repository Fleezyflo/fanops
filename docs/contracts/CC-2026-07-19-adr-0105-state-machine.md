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

**This contract cannot be text-only, and that is a finding rather than a convenience.** `NC-C27`
recomputes the ADR body digest and compares it to the pin in `tools/contract/classify.py`. Editing
the body moves the digest, so the pin must move in the same change or CI reddens. The pin is ONE
line; any other edit to that file is a scope violation, not a judgement call.

**`authorized_actions` is `[design]` and stops there.** Implementation, merge and acceptance are
separate grants obtained at their own gates.

### objective

Amend ADR-0105 so that `accepted` requires a verified merge authorization AND a separate
acceptance-verification gate; the merge family splits into `merged`, `merged_unverified` and
`merged_unauthorized`; both authorization routes bind the FINAL pre-merge PR head; post-merge
rederivation verifies a route and never originates one; and every unavailable read reaches `ST-7`
while every readable negative is a state rather than a stop. Add no rule to any tool. Change no
behaviour.

### success_condition

Each is independently checkable by someone who did not write this:

1. `python -m tools.contract selftest` exits 0 with every control DETECTED, `NC-C27` included,
   proving the recomputed body digest equals both the new pin and the new front-matter value.
2. The amended §4.3 ladder carries the twelve states of the consolidated design, with terminal-event
   precedence and first-match-wins preserved.
3. The amended §4.1a states that BOTH routes bind the final pre-merge PR head, and that a witnessed
   review qualifies on `state == APPROVED` and `commit_id == head` alone.
4. The amended text names every negative control the IMPLEMENTATION contract must build. This
   contract builds none of them.
5. `python -m tools.arch ci` and `python -m tools.ci static` exit 0.
6. `git diff --name-only <base>...<head>` equals `expected_surfaces` exactly.

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
| tools/contract/model.py | no type change; the new states are text until implemented |
| tools/contract/adapters.py | no port added; the census and check-run joins are later contracts |
| tools/contract/__main__.py | the dependency fail-closed repair belongs to the implementation contract |
| tools/contract/selftest.py | no control added; this contract NAMES them and does not build them |
| .github/ci-control-registry.yml | the registry amendment is a SEPARATE contract |
| .github/workflows/** | no workflow change; enforcement remains Phase 6 |
| src/fanops/** | no runtime change |
| docs/contracts/CC-2026-07-18-change-contract-compiler.md | an accepted contract is not a mutable work log |
| docs/contracts/CC-2026-07-19-cli-lifecycle-integrity.md | a landed contract is not a mutable work log |
| docs/governance/** | the roadmap transition belongs to the Phase 3 closure contract |

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
| the amended §4.3 ladder | the negative-control names written into the amendment text | a state with no named control is a state nothing will ever test, which is the defect class this amendment exists to remove |

### reusable_evidence

| claim | proven_by | proven_at | binding |
|---|---|---|---|
| the witnessed route qualifies on `state == APPROVED` and `commit_id == head_sha` alone, with no permission or identity predicate | read of the exact-head branch in `gates` | f558f08d817b66bfc5afe046ff133edd0d5b9dc7 | tool:route-w-feasibility |
| `ReviewPort.approvals` returns `(commit_id, state)` pairs only and never reads a login, so the verifier cannot evaluate reviewer identity | read of `ReviewPort.approvals` | 69e1630ac4dccd0a9e2e8d4b594ed599c5582fa5 | tool:route-w-feasibility |
| `Derived` is constructed before the review and principal reads, so their failures cannot reach the field the fail-closed rule consults | read of the `run` pipeline stage order | edc5da272dda94a3b84025e5a6bdf566df5ce53e | tool:route-w-feasibility |
| `ST-7` predicates on `derived.unverifiable` alone, so a dependency diagnostic appended later decides nothing | read of the `ST-7` rule | 0a9574b0b6792d9103485f96b564642fa7bcd483 | tool:route-w-feasibility |

Each row binds to the blob of the file the claim was read from, at the base commit of this change.
Citing the ADR blob for a claim about code would bind evidence to a document that cannot prove it.

### verification

| obligation_id | control_or_requirement | distinct_boundary |
|---|---|---|
| OB-NC-C27 | python -m tools.contract selftest, NC-C27 DETECTED | proves the pin, the front matter and the live body agree, which is the ONE control this contract moves |
| OB-ARCH-CI | python -m tools.arch ci | regeneration byte-compare plus the policy rule set |
| OB-CI-STATIC | python -m tools.ci static | registry-versus-workflow reconciliation |
| OB-C18 | the amendment disclosed, its digest recomputed, and renewed operator approval obtained | proves the AUTHORITY changed with consent rather than silently |
| OB-ROUTE-W | a non-author APPROVED review whose commit_id equals the FINAL PR head | the only merge evidence available while the unwitnessed route is frozen |

## Lifecycle

| timestamp | event | values |
|---|---|---|
| 2026-07-19T14:34:05Z | created | id=CC-2026-07-19-adr-0105-state-machine; base_sha=35cbf7fcebdd9e2b5f657a971af6c31140879123 |
