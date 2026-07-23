---
id: CC-2026-07-22-fixture-declaration-only
traits: [live]
authorized_actions: [design, implement, apply-live]
incidental_allowlist: [docs/CONFIG.md]
blast_radius: []
invariants: [LAW-SOT-01]
stop_conditions: ["T6: the operator required a contract for this task"]
supersedes: []
approved_digest: sha256:1591758d45347c99ef55815b969a4274a6773dcfacce706f62acac3f8877578d
approval_token: APPROVE THE DECLARATION-ONLY FIXTURE
execution_gate: RUN THE DECLARATION-ONLY FIXTURE
---

# CC-2026-07-22-fixture-declaration-only

A FIXED, COMMITTED, VALID contract in the ADR-0106 shape: **no `## Lifecycle` section at all.**

It is the counterpart to `valid_full.md`, which pins the lifecycle-bearing shape. Between them they
populate every slot in `model.ALL_FIELDS`; neither can populate all of them alone, because the two
approval routes are mutually exclusive by `APPROVAL-DUAL-ROUTE` and that exclusivity is the point.

It carries the `live` trait and therefore an `execution_gate`, so it also pins the one thing most
easily lost in the move off the lifecycle: a `live` change still needs a SECOND operator act, and
`RF-1` still refuses without one.

The three approval lines sit OUTSIDE `D` (`parse.digest_range`). That is what lets an operator
approve a digest computed before the approval was written, and it is asserted directly by
`test_recording_the_approval_leaves_the_digest_unchanged`.

### objective

Pin the declaration-only contract shape as a committed artifact, independently of any fixture the
compiler builds for itself.

### success_condition

`python -m tools.contract digest` on this file prints the value in its own `approved_digest`, and
parsing it yields zero MALFORMED, UNSUPPORTED or UNKNOWN diagnostics.

### rollback

Delete the file. Nothing imports it; no gate reads it; no workflow references it.

### authority

| id | source_file | blob_sha |
|---|---|---|
| ADR-0106 | docs/adr/0106-declaration-only-change-contracts.md | fixture-not-resolved |

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
| the approval fields | `parse.digest_range` | a field elided from `D` in one place and not the other would put the token back inside the digest it records |

### reusable_evidence

| claim | proven_by | proven_at | binding |
|---|---|---|---|

### verification

| obligation_id | control_or_requirement | distinct_boundary |
|---|---|---|
| OB-ARCH-CI | python -m tools.arch ci | regeneration byte-compare |
