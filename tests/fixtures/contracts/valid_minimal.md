---
id: CC-2026-07-18-fixture-minimal
traits: []
authorized_actions: [design]
incidental_allowlist: []
blast_radius: []
invariants: [LAW-SOT-01]
stop_conditions: []
supersedes: []
---

# CC-2026-07-18-fixture-minimal

A FIXED, COMMITTED, VALID contract with the empty trait set — the `contained` case.

It exists so that the compiler accepting its own bootstrap contract is not the only evidence it
works. A compiler that accepts only the document it was written alongside has proven internal
consistency and nothing else; an independent fixture is what turns that into a claim about the
format. This file is deliberately NOT derived from the bootstrap contract.

It also pins the one shape most easily broken by accident: `traits: []`. ADR-0105 §5.1 says a
contract may exist with an empty trait set, so a validator that read `[]` as "missing" would make
the contained case — the case the ADR most wants to stay cheap — impossible to express. That defect
was real during Phase 3B implementation, and this fixture is what keeps it fixed.

### objective

Pin the minimal valid contract shape, independently of the bootstrap contract.

### success_condition

`python -m tools.contract verify` on this file reaches `continue` at rule `OK`, exit class 0.

### rollback

Delete the file. Nothing imports it; no gate reads it; no workflow references it.

### authority

| id | source_file | blob_sha |
|---|---|---|
| ADR-0105 | docs/adr/0105-reusable-change-contract-architecture.md | fixture-not-resolved |

### owners

| subsystem_id | why_touched |
|---|---|
| S01_foundation | the fixture names a module in this subsystem |

### allowed_scope

| glob | why | basis |
|---|---|---|
| src/fanops/example.py | the single declared surface | declared |

### prohibited_scope

| glob | why |
|---|---|
| .github/workflows/** | a fixture never changes CI |

### expected_surfaces

| path | kind | why |
|---|---|---|
| src/fanops/example.py | MODIFIED | the single declared surface |

### coupling

| what | must_move_with | why |
|---|---|---|

### reusable_evidence

| claim | proven_by | proven_at | binding |
|---|---|---|---|

### verification

| obligation_id | control_or_requirement | distinct_boundary |
|---|---|---|
| OB-ARCH-CI | python -m tools.arch ci | regeneration byte-compare |

## Lifecycle

| timestamp | event | values |
|---|---|---|
| 2026-07-18T09:00:00Z | created | id=CC-2026-07-18-fixture-minimal; base_sha=ce132f6 |
