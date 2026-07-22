---
id: CC-2026-07-22-declaration-only-contracts
traits: [governance]
authorized_actions: [design, implement]
incidental_allowlist: []
blast_radius: []
invariants:
  - "every contract already on `main` derives the same state after this change as before it"
  - "a lifecycle-bearing contract's `D` is computed by ADR-0105 §3's reference implementation, byte-for-byte"
  - "recording an approval never changes the digest that approval names"
  - "an unapproved contract still stops at `ST-3`, and a `live` one without a separate gate still stops at `RF-1`"
  - "the lifecycle reader is retained, wired and covered — a historical record never becomes unreadable"
stop_conditions:
  - "any edit to `docs/adr/0105-reusable-change-contract-architecture.md`, which would move the blob six landed contracts cite"
  - "any edit to a contract under `docs/contracts/` dated before 2026-07-22"
  - "any change that alters the digest a landed contract's `approved` event names"
supersedes: []
---

# CC-2026-07-22-declaration-only-contracts

### objective

Remove the development ceremony the contract system accumulated, leaving the parts that decide
whether a change is authorized. A new contract becomes **declaration-only** — no `## Lifecycle`
section, no event chain, no post-merge publication PR, no acceptance ceremony, and no GitHub run
ids, timestamps, merge SHAs or other platform facts copied into a tracked file. The lifecycle
tooling is retained and applies only to the six contracts written under ADR-0105.

The governing decision is `docs/adr/0106-declaration-only-change-contracts.md`. This contract is
itself written in the shape that ADR authorizes, so the PR that introduces the shape is also the
first thing verified in it.

Preserved verbatim, and each is exercised by this change: the `T1`–`T6` trigger model, path-only
preflight, the declaration and its closed field set, declaration digest approval, the
expected-surface-versus-diff check, the separate operator gate for a live action, the unit lane, and
relevance-gated E2E.

### success_condition

Five observable facts, each re-run on the final head:

1. `python -m tools.contract state <path>` returns a **byte-identical verdict for all seven
   contracts on `main`** before and after this change — `accepted`, `accepted`, `merged`,
   `acceptance_claimed`, `acceptance_claimed`, `merged_unauthorized`, `merged_unauthorized`.
2. `python -m tools.contract selftest` reports every control detected, including `NC-D1`–`NC-D9`,
   and `NC-D8` recomputes each landed contract's digest against the approval it records.
3. `python -m tools.contract template` emits a skeleton with **no** `## Lifecycle` section.
4. `python -m tools.contract verify docs/contracts/CC-2026-07-22-declaration-only-contracts.md
   --phase pre` reaches `continue` at rule `OK` with zero diagnostics, and `scope` reports no
   unauthorized surface.
5. `python -m tools.arch ci` and `python -m tools.ci static` stay clean, and the `unit (fast, no
   toolchain)` context concludes success on the exact head.

### rollback

Revert the merge commit. Every change is repository-side — one new ADR, one new fixture, prose, and
the verifier's own source. The lifecycle code is untouched, so reverting restores the previous model
with no migration; a contract written in the declaration-only shape in the interim would need a
`## Lifecycle` section added, which is an append and voids nothing.

### authority

| id | source_file | blob_sha |
|---|---|---|
| ADR-0105 | docs/adr/0105-reusable-change-contract-architecture.md | bce8525d462e9df8e070191972cc7a757c6da377 |
| ADR-0106 | docs/adr/0106-declaration-only-change-contracts.md | 6176ee772b08065d2517a9b0a4f0241663e798b6 |

### owners

| subsystem_id | why_touched |
|---|---|
| S01_foundation | no `src/fanops/` module changes; ownership is DECLARED for the governance tooling and documentation paths, never inferred (ADR-0105 §7) |

### allowed_scope

| glob | why | basis |
|---|---|---|
| tools/contract/**.py | the verifier: the digest range, the field set, the two gates, the template, the controls | declared |
| tests/test_contract_compiler.py | the focused tests for the changed tooling | declared |
| tests/fixtures/contracts/valid_declaration_only.md | a committed artifact in the new shape, so self-validation is not the only evidence | declared |
| docs/adr/0106-declaration-only-change-contracts.md | the governing decision | declared |
| docs/governance/AGENT_CHANGE_SYSTEM_ROADMAP.md | Phases 4–8 marked cancelled; the sequencing decisions they carried voided | declared |
| AGENTS.md | the agent-facing route: the four-step contract flow, and normal work no longer routed through dormant orchestration | declared |
| docs/contracts/CC-2026-07-22-declaration-only-contracts.md | this declaration | declared |

### prohibited_scope

| glob | why |
|---|---|
| docs/adr/0105-reusable-change-contract-architecture.md | six landed contracts cite its blob SHA; any edit puts every one of them into `AUTH-BLOB-MOVED` → `ST-2` for doing nothing wrong |
| docs/contracts/CC-2026-07-1*.md | historical contracts are immutable records, never rewritten |
| docs/contracts/CC-2026-07-2[01]-*.md | same |
| src/** | no runtime behaviour changes in this unit |
| .github/** | no workflow, registry or branch-protection change |
| .orchestration/**, scripts/orchestrate.py, .cursor/** | the orchestration machinery is de-routed in prose, never deleted or rewired |

### expected_surfaces

| path | kind | why |
|---|---|---|
| tools/contract/parse.py | MODIFIED | `digest_range` — the single definition of what `D` covers, selecting on the boundary count; the declaration-only shape stops being a parse failure |
| tools/contract/model.py | MODIFIED | the three approval fields, in the closed front-matter set |
| tools/contract/lifecycle.py | MODIFIED | content approval read from `approved_digest` when there is no lifecycle; `DECL-DIVERGED` for a landed declaration-only contract |
| tools/contract/validate.py | MODIFIED | `APPROVAL-DUAL-ROUTE` and `APPROVAL-INCOMPLETE` |
| tools/contract/decide.py | MODIFIED | `NO-BOUNDARY` out of the parse-failure set; the two new codes into the lifecycle family; the execution gate reads front matter |
| tools/contract/report.py | MODIFIED | the template emits the declaration-only skeleton |
| tools/contract/__main__.py | MODIFIED | `digest` uses the one definition; the base note stops reading a missing `base_sha` as a defect |
| tools/contract/selftest.py | MODIFIED | `build_decl_only`; `NC-D1`–`NC-D9`; `NC-C32` removed with the rule it named |
| tests/test_contract_compiler.py | MODIFIED | digest stability, the field-set count, the fixture union |
| tests/fixtures/contracts/valid_declaration_only.md | NEW | the committed declaration-only artifact |
| docs/adr/0106-declaration-only-change-contracts.md | NEW | the governing decision |
| docs/governance/AGENT_CHANGE_SYSTEM_ROADMAP.md | MODIFIED | Phases 4–8 CANCELLED; D2/D4/B1/B2/P5-1/P5-2 voided |
| AGENTS.md | MODIFIED | the four-step flow; orchestration de-routed |
| docs/contracts/CC-2026-07-22-declaration-only-contracts.md | NEW | this declaration |

### coupling

| what | must_move_with | why |
|---|---|---|
| `model.APPROVAL_FIELDS` | `parse._APPROVAL_LINES` | a field listed in one and not the other lands INSIDE `D`, so recording it would void the approval in the act of writing it |
| `parse.digest_range` | every landed contract's `approved` digest | the digest rule decides whether six existing approvals still name their own declarations; `NC-D8` is the check |
| a decision rule reading a new code | a negative control producing it | `NC-C31` reads `decide.py`'s AST and goes red on an uncovered code |
| `model.ALL_FIELDS` | the committed fixtures | `test_the_committed_fixtures_between_them_exercise_every_slot_and_state` requires their union to cover the set |

### reusable_evidence

| claim | proven_by | proven_at | binding |
|---|---|---|---|

### verification

| obligation_id | control_or_requirement | distinct_boundary |
|---|---|---|
| OB-CONTRACT-SELFTEST | python -m tools.contract selftest | every rule still carries a firing control after the rule set changed |
| OB-LEGACY-STATE | python -m tools.contract state, on all seven landed contracts | the migration guarantee: identical verdicts before and after, read off the repository |
| OB-ARCH-CI | python -m tools.arch ci | regeneration byte-compare plus the policy rule set |
| OB-CI-STATIC | python -m tools.ci static | the registry-versus-workflow reconciliation is undisturbed by the prose edits |
| OB-UNIT-CI | the required context `unit (fast, no toolchain)` concluding success on the exact head | the only evidence the pytest suite, including the new fixture and tests, passes |
| OB-SELF | python -m tools.contract verify on this contract | the new shape is verified by the tool that defines it, in the PR that introduces it |
| OB-C18 | this contract and ADR-0106 | Constitution C18.1 / the ADR process |
| OB-REVERIFY | every claim above re-run on the final head, not reused from an earlier run | no evidence reuse |
