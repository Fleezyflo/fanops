---
id: CC-2026-07-18-fixture-full
traits: [cross-system, governance, live]
authorized_actions: [design, implement, merge, apply-live, accept]
incidental_allowlist: [docs/CONFIG.md]
blast_radius: [fanops.crosspost, fanops.reconcile]
invariants: [LAW-SOT-01, LAW-SOT-02, C2.1]
stop_conditions: ["T6: the operator required a contract for this task"]
supersedes: [CC-2026-07-17-fixture-full-predecessor]
---

# CC-2026-07-18-fixture-full

Every one of the nineteen declaration slots populated, all three traits set, and a lifecycle
carried the whole way through to `accepted`.

The minimal fixture cannot exercise this: it has no traits, so it reaches none of the
trait-conditional fields, none of the union obligations, and none of the derived states past
`draft`. This one walks every grammar branch — inline lists, quoted scalars carrying a colon,
block lists, all eight tables, every event kind that a single contract can legally carry — and
every derived state the ladder can reach.

It carries all three traits at once ON PURPOSE. ADR-0105 §5.2(c) records that a
`{governance, live}` change is exactly where a worst-wins model silently drops every governance
obligation, and §5.1 answers with the union rule. A fixture that never set two traits together
would leave that answer untested.

### objective

Exercise every grammar branch, every trait, and every derived lifecycle state in one document.

### success_condition

`python -m tools.contract verify` parses all 19 slots with zero diagnostics of kind `MALFORMED`,
`UNSUPPORTED` or `UNKNOWN`, and derives state `accepted`.

### rollback

Delete the file. Nothing imports it; no gate reads it. Cost: the grammar loses its widest fixture,
so restore it before changing the parser.

### authority

| id | source_file | blob_sha |
|---|---|---|
| ADR-0105 | docs/adr/0105-reusable-change-contract-architecture.md | fixture-not-resolved |
| C2.1 | docs/REPOSITORY_CONSTITUTION.md | fixture-not-resolved |
| LAW-SOT-01 | docs/ARCHITECTURAL_LAWS.md | fixture-not-resolved |

### owners

| subsystem_id | why_touched |
|---|---|
| S01_foundation | the change originates here |
| S04_registry | a second owner, so `cross-system` is real rather than declared |

### allowed_scope

| glob | why | basis |
|---|---|---|
| src/fanops/crosspost.py | the declared change | declared |
| docs/governance/** | the governance surface this change edits | declared |
| .github/ci-control-registry.yml | the live control row this change mutates | inferred |

### prohibited_scope

| glob | why |
|---|---|
| src/fanops/ledger.py | the near miss: adjacent, tempting, and out of scope |
| .github/workflows/** | no workflow change |

### expected_surfaces

| path | kind | why |
|---|---|---|
| src/fanops/crosspost.py | MODIFIED | the declared change |
| docs/governance/EXAMPLE.md | NEW | the governance record this change adds |
| src/fanops/dead_example.py | DELETED | a tracked deletion — NOT `live` (ADR-0105 §1 T4) |

### coupling

| what | must_move_with | why |
|---|---|---|
| the surface-time contract | fanops.reconcile | a schedule change that reconcile does not learn about strands the post |

### reusable_evidence

| claim | proven_by | proven_at | binding |
|---|---|---|---|
| the presign contract is the supported upload path | PR #694, proven live | fixture-not-resolved | blob:src/fanops/post/zernio.py |

### verification

| obligation_id | control_or_requirement | distinct_boundary |
|---|---|---|
| OB-ARCH-CI | python -m tools.arch ci | regeneration byte-compare |
| OB-CI-STATIC | python -m tools.ci static | registry-vs-workflow reconciliation |
| OB-VM-changed_enums | transition tests | illegal SOURCE states are refused, not that the legal one works |
| OB-ROLLBACK-REHEARSAL | rollback rehearsed against a pre-image | the undo path, not the do path |

## Lifecycle

| timestamp | event | values |
|---|---|---|
| 2026-07-18T09:00:00Z | created | id=CC-2026-07-18-fixture-full; base_sha=ce132f6 |
| 2026-07-18T09:05:00Z | approved | digest=sha256:fixture; token=APPROVE; execution_gate=granted |
| 2026-07-18T09:10:00Z | binding | branch=feat/fixture; pr=999 |
| 2026-07-18T09:15:00Z | implementation_started | — |
| 2026-07-18T10:00:00Z | head_proposed | head_sha=deadbeef; ci=green; verifier=continue |
| 2026-07-18T11:00:00Z | merged | merge_sha=cafebabe |
| 2026-07-18T12:00:00Z | accepted | merge_sha=cafebabe; decision=ACCEPT; evidence=the success condition held; date=2026-07-18; operator=operator |
