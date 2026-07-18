---
id: CC-2026-07-18-change-contract-compiler
traits: [governance]
authorized_actions: [design, implement]
incidental_allowlist: []
blast_radius: []
invariants: [LAW-SOT-01, LAW-SOT-02, LAW-DOC-01, C2.1, C18.1]
stop_conditions: ["T6: the operator required a contract for this phase"]
supersedes: []
---

# CC-2026-07-18-change-contract-compiler

The first Change Contract written under ADR-0105, governing the change that builds the compiler and
verifier which will check every contract after it — including, before merge, this one.

**`authorized_actions` is `{design, implement}` and stops there.** Merge and acceptance are separate
grants obtained at their own gates. ADR-0105 §10 makes that binding: *"partial authorization is not
full authorization — design ≠ implement ≠ merge ≠ apply-live ≠ accept."* A contract that granted
itself merge would be the first thing this system was built to prevent.

**Self-validation is necessary and not sufficient.** A compiler that accepts its own contract has
proven internal consistency and nothing more. Sufficiency needs all three: this contract passing,
the two independent fixtures under `tests/fixtures/contracts/` passing, and every deliberately
corrupted variant failing with its OWN named rule. `NC-C26` is the third.

**This is not Phase 4.** Phase 4 requires a fresh agent, with no conversational context, driving
three cases unaided. This contract was written by the agent that designed the system; it proves the
artifact is writable and checkable, not that it is discoverable cold.

**Implementation found the model defective, and amending it is part of this change (§4.1a).** The
exact-head gate admitted only a non-author GitHub review; this repository has exactly one account
with push access and it authors every PR, so no evidence of any kind could ever be produced and the
gate was **unsatisfiable, not merely strict**. The same self-reference — a record cannot name the
commit whose hash is computed over it — made `head_proposed`, and therefore the `implemented` state,
unreachable in *every* repository. **The compiler discovering that its own governing model contains
an impossible invariant is the system working, not failing:** it was found by executing the model
rather than by reading it. The correction is parent-binding plus a platform-gated second evidence
route, and it moves the ADR body, so **this contract's `D` moves with it and requires renewed
approval.** Both are recorded below and neither is silent.

### objective

Implement ADR-0105 as an executable compiler and verifier: `tools/contract/`, a read-only command
surface, a firing negative control for every rule, and the two `tools/arch` corrections ADR-0105 §9
records as gap **G4**. Add no CI job, no workflow, no hook and no gate — ADR-0105 §9 leaves
enforcement to Phase 6.

### success_condition

All five hold, each independently checkable by someone who did not write this:

1. `python -m tools.contract selftest` exits 0 with every control DETECTED.
2. `python -m tools.contract verify docs/contracts/CC-2026-07-18-change-contract-compiler.md`
   reaches `continue` at rule `OK` at its own head.
3. `python -m tools.arch ci` exits 0.
4. `.venv/bin/python -m tools.ci static` exits 0.
5. `git diff --name-only <base>...<head>` equals `expected_surfaces` exactly — the anti-scope-
   expansion set of ADR-0105 §5.3 is empty.

### rollback

`git revert` of the single squash commit removes the package, the tests, the fixtures, both
`tools/arch` edits, the ADR line and the roadmap edit together.

Cost: **near zero, and that is a property of Phase 3 shipping no gate.** No workflow references the
package, no required context depends on it, no runtime imports it, and nothing in `src/fanops/`
changes. Reverting the ADR line restores the previously approved body AND its previously approved
digest, so no dangling approval is left behind.

This contract is RETAINED after any terminal event, per ADR-0105 §11.2 — deletion would destroy the
audit trail the artifact exists to create.

**This rollback story stops holding the moment Phase 6 adopts the contract as a gate's input, and
must be rewritten then.**

### authority

| id | source_file | blob_sha |
|---|---|---|
| ADR-0105 | docs/adr/0105-reusable-change-contract-architecture.md | f9fa602b501f80418d8a66eb9c6389a99ae64c8a |
| C2.1 | docs/REPOSITORY_CONSTITUTION.md | 1f42a8ea298af39fffd56e3ce5c3542cef512df2 |
| C18.1 | docs/REPOSITORY_CONSTITUTION.md | 1f42a8ea298af39fffd56e3ce5c3542cef512df2 |
| LAW-SOT-01 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |
| LAW-SOT-02 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |
| LAW-DOC-01 | docs/ARCHITECTURAL_LAWS.md | 91ce5627ddc08b5f90189114bbef18c268b484a0 |

The ADR blob recorded here is the **amended** body — the one this change produces and the operator
re-approves. Citing the pre-amendment blob would be citing an authority that no longer exists.

### owners

| subsystem_id | why_touched |
|---|---|
| S01_foundation | no `src/fanops/` module changes; ownership is DECLARED for the tooling and documentation paths, never inferred (ADR-0105 §7) |

### allowed_scope

| glob | why | basis |
|---|---|---|
| tools/contract/** | the package this change exists to add | declared |
| tests/test_contract_compiler.py | its proof | declared |
| tests/fixtures/contracts/** | the independent fixtures D-6 requires | declared |
| docs/contracts/CC-2026-07-18-change-contract-compiler.md | this contract | declared |
| tools/arch/impact.py | the `changed_enums` dimension (ADR-0105 G4) | declared |
| tools/arch/verifymap.py | retire two dead requirements, add one reachable (G4) | declared |
| docs/adr/0105-reusable-change-contract-architecture.md | the T3 amendment the ADR's own rule compels, and the §4.1a amendment implementation proved necessary | declared |
| docs/governance/AGENT_CHANGE_SYSTEM_ROADMAP.md | the phase transition | declared |

### prohibited_scope

| glob | why |
|---|---|
| .github/workflows/** | Phase 3 adds NO CI job — ADR-0105 §9 |
| .github/ci-control-registry.yml | no control added, edited or reclassified |
| .cursor/** | no hook change |
| .claude/** | no hook change |
| .githooks/** | no hook change |
| .orchestration/** | the dormant gate is Phase 6's decision (roadmap D4) |
| src/fanops/** | no runtime change; nothing in the application moves |
| requirements/** | no dependency added — stdlib only |
| pyproject.toml | no dependency added; no marker added |
| .reports/architecture/** | no derived artifact added or hand-edited |
| tools/ci/** | the sibling is READ, never modified |
| tools/arch/select.py | the near miss: it gates only the `tools.arch selftest` job, and these controls ride the `unit` job's collection, so it is explicitly NOT needed |

### expected_surfaces

| path | kind | why |
|---|---|---|
| tools/contract/__init__.py | NEW | records the one-way dependency on both siblings |
| tools/contract/__main__.py | NEW | CLI dispatch and the composition root |
| tools/contract/model.py | NEW | types and the closed field set; imports nothing |
| tools/contract/parse.py | NEW | the declaration/lifecycle split, `D`, and the closed grammar |
| tools/contract/classify.py | NEW | triggers T1–T6, traits, per-file labels |
| tools/contract/derive.py | NEW | ownership, blast radius, obligations, authority, evidence |
| tools/contract/lifecycle.py | NEW | the three gates, nine states, invalidation |
| tools/contract/validate.py | NEW | the seven validation families |
| tools/contract/decide.py | NEW | the pure decision table |
| tools/contract/adapters.py | NEW | the five ports and the only I/O |
| tools/contract/report.py | NEW | rendering and the exit-class mapping |
| tools/contract/selftest.py | NEW | the negative controls |
| tests/test_contract_compiler.py | NEW | the CI face of all of it |
| tests/fixtures/contracts/valid_minimal.md | NEW | the independent minimal fixture |
| tests/fixtures/contracts/valid_full.md | NEW | the independent full-coverage fixture |
| docs/contracts/CC-2026-07-18-change-contract-compiler.md | NEW | this contract |
| tools/arch/impact.py | MODIFIED | add `changed_enums`, ceilinged at COMPATIBLE_CHANGE |
| tools/arch/verifymap.py | MODIFIED | retire two dead requirements, add `changed_enums` |
| docs/adr/0105-reusable-change-contract-architecture.md | MODIFIED | add `tools/contract/**` to §1 T3; recompute the digest |
| docs/governance/AGENT_CHANGE_SYSTEM_ROADMAP.md | MODIFIED | Phase 2 ACCEPTED, Phase 3 IN IMPLEMENTATION, next gate |

### coupling

| what | must_move_with | why |
|---|---|---|
| the ADR-0105 §1 T3 path list | tools/contract/classify.py `T3_PATTERNS` and `ADR_0105_DIGEST` | the code transcribes the ADR's list; a copy that can drift from its authority is a second authority. `NC-C27` fails if either moves without the other |
| retiring `changed_state_machines` | adding `changed_enums` to impact.py AND verifymap.py | a requirement whose dimension nothing writes is dead, which is the defect being fixed, not a new way to commit it |

### reusable_evidence

| claim | proven_by | proven_at | binding |
|---|---|---|---|
| `impact.report` returns a dict in-process, so ADR-0105 gap G1 needs no `tools/arch` change | read of `tools/arch/impact.py:81` during Phase 3A | 1634f1f2daa04fab2c334c2021be2c1ea34c3378 | tool:phase-3a-design |
| `subsystem_of` is total (134 modules, `partition_is_total: true`), so the G2 transform is checkable against its own output | read of `.reports/architecture/derived/modules.json` during Phase 3A | fixture-not-resolved | tool:phase-3a-design |

### verification

| obligation_id | control_or_requirement | distinct_boundary |
|---|---|---|
| OB-ARCH-CI | python -m tools.arch ci | regeneration byte-compare plus the policy rule set — proves the ARTIFACTS match the source |
| OB-CI-STATIC | python -m tools.ci static | registry-versus-workflow reconciliation — proves the DECLARED controls match the wired ones |
| OB-NEG-CONTROL | tools/contract/selftest.py, every control DETECTED | proves each rule FIRES on an injected defect, which neither of the above can show |
| OB-C18 | the ADR-0105 amendment disclosed, its digest recomputed, and renewed operator approval obtained | proves the AUTHORITY changed with consent, not silently |
| OB-REVERIFY | the two independent fixtures and NC-C26 | proves the compiler is not merely self-consistent — the one thing self-validation cannot show |

## Lifecycle

| timestamp | event | values |
|---|---|---|
| 2026-07-18T14:00:00Z | created | id=CC-2026-07-18-change-contract-compiler; base_sha=ce132f61c8637f5adfaed2e3de999c6254031792 |
| 2026-07-18T17:00:00Z | binding | branch=feat/change-contract-compiler; pr=703 |
| 2026-07-18T17:05:00Z | approved | digest=sha256:d6e268108f63ccfaaa300e430a087a32cd6298b0793f0f608ff366da982cf607; token=APPROVE PHASE 3B CHANGE CONTRACT COMPILER IMPLEMENTATION; adr_0105_renewed=sha256:dd125cb18b7daf2174242a1ffad2f57fd4d19df8cdd6df9d9317db3d496955ac; operator=operator |
| 2026-07-18T17:10:00Z | implementation_started | note=the implementation preceded this record; the event is written where the lifecycle requires it |
| 2026-07-19T13:00:00Z | approved | digest=sha256:cbb8ed59c0db34161e78968ff0172697275ce20fd33ef655189197bfb8607542; token=APPROVE GOVERNANCE AMENDMENT; adr_0105_renewed=sha256:e757fb6e01d3e6f143f6d6af9f45bce780331562adb07149b55857baefc5875a; operator=operator |
